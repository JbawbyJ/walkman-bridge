"""Read-only release verification against disposable engine/source bundles."""
import importlib.util
import io
from pathlib import Path
import shutil
import stat
import zipfile

import pytest


spec = importlib.util.spec_from_file_location('verify_release', Path(__file__).with_name('verify-release.py'))
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)

NATIVE = 'src/main/java/org/naurd/media/jsymphonic/device/sony/nw/NativePlaylistDatabase.java'
SOURCE = {
    'LICENSE': b'fixture GPL license',
    'pom.xml': b'<project/>',
    'NATIVE-PLAYLISTS.md': b'Playlist contract',
    NATIVE: b'class NativePlaylistDatabase {}',
    '.github/workflows/ci.yml': b'name: engine CI',
    'src/test/java/RecoveryTest.java': b'class RecoveryTest {}',
}


def write_source(path, files, directories=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w') as archive:
        if directories:
            for directory in ('.github/', '.github/workflows/', 'src/'):
                archive.writestr(directory, b'')
        for name, data in files.items():
            archive.writestr(name, data)


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    root, resources = tmp_path / 'checkout', tmp_path / 'resources'
    monkeypatch.setattr(verify, 'ROOT', root)
    jar = root / 'backend/vendor/jsymphonic.jar'
    jar.parent.mkdir(parents=True)
    with zipfile.ZipFile(jar, 'w') as binary:
        binary.writestr('org/naurd/media/jsymphonic/headless/HeadlessCli.class', b'fixture')
        binary.writestr(NATIVE.replace('src/main/java/', '').replace('.java', '.class'), b'fixture')
    monkeypatch.setattr(verify, 'REVIEWED_ENGINE_SHA256', verify.sha(jar))
    (resources / 'backend/vendor').mkdir(parents=True)
    shutil.copyfile(jar, resources / 'backend/vendor/jsymphonic.jar')
    for name, data in {
        'jre/bin/java.exe': b'fixture, never executed',
        'jre/release': b'JAVA_VERSION="21.0.12"',
        'jre/legal/java.base/LICENSE': b'fixture Java license',
        'notices/java/NOTICE': b'fixture notice',
        'notices/jsymphonic/LICENSE': SOURCE['LICENSE'],
    }.items():
        target = resources / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    locked = root / 'packaging/sources/engine-src.zip'
    bundled = resources / 'sources/engine-src.zip'
    write_source(locked, SOURCE)
    bundled.parent.mkdir(parents=True)
    shutil.copyfile(locked, bundled)
    lock = {'jdk': {'version': '21.0.12'},
            'jsymphonic': {'source_archive': 'sources/engine-src.zip', 'sha256': verify.sha(locked)}}
    return resources, lock, locked, bundled


@pytest.mark.parametrize('directories', [True, False])
def test_pinned_complete_archive_verifies_without_sibling_checkout(bundle, directories):
    resources, lock, locked, bundled = bundle
    write_source(locked, SOURCE, directories=directories)
    lock['jsymphonic']['sha256'] = verify.sha(locked)
    shutil.copyfile(locked, bundled)
    result = verify.verify_bridge(resources, lock)
    assert result['source_files'] == len(SOURCE)
    assert result['source_archive_sha256'] == lock['jsymphonic']['sha256']
    assert result['license_parity'] is True


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'C:/absolute', 'src\\escape',
                                  'src/../escape', 'src//bad', './bad', 'src/../bad/'])
def test_source_archive_rejects_unsafe_paths(name):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr(name, b'')
    # Windows ZipInfo normalizes backslashes when writing. Preserve the raw
    # malicious filename in both ZIP headers so verification sees that input.
    if '\\' in name:
        data = io.BytesIO(data.getvalue().replace(name.replace('\\', '/').encode(), name.encode()))
    with zipfile.ZipFile(data) as archive, pytest.raises(ValueError, match='Unsafe'):
        verify.source_archive_files(archive)


@pytest.mark.parametrize('names', [('LICENSE', 'LICENSE'), ('LICENSE', 'license'),
                                   ('src/', 'src'), ('src/file', 'src')])
@pytest.mark.filterwarnings('ignore:Duplicate name:UserWarning')
def test_source_archive_rejects_duplicates_and_conflicting_paths(names):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        for name in names:
            archive.writestr(name, b'')
    with zipfile.ZipFile(data) as archive, pytest.raises(ValueError):
        verify.source_archive_files(archive)


def test_source_archive_rejects_symlink_entries():
    data = io.BytesIO()
    entry = zipfile.ZipInfo('linked-source')
    entry.create_system = 3
    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr(entry, '../outside')
    with zipfile.ZipFile(data) as archive, pytest.raises(ValueError, match='regular'):
        verify.source_archive_files(archive)


def test_locked_archive_tampering_fails_its_pin(bundle):
    resources, lock, locked, bundled = bundle
    write_source(locked, {**SOURCE, 'extra': b'tampered'})
    shutil.copyfile(locked, bundled)
    with pytest.raises(ValueError, match='Locked Java source archive hash mismatch'):
        verify.verify_bridge(resources, lock)


@pytest.mark.parametrize('change', ['changed', 'missing', 'extra', 'repacked'])
def test_bundled_source_requires_exact_pinned_archive_and_all_payloads(bundle, change):
    resources, lock, _, bundled = bundle
    files = dict(SOURCE)
    if change == 'changed':
        files[NATIVE] = b'changed source'
    elif change == 'missing':
        del files['.github/workflows/ci.yml']
    elif change == 'extra':
        files['extra'] = b'additional source'
    write_source(bundled, files, directories=change != 'repacked')
    with pytest.raises(ValueError, match='Bundled Java source'):
        verify.verify_bridge(resources, lock)


def test_required_source_file_cannot_be_omitted_even_with_updated_archive_pin(bundle):
    resources, lock, locked, bundled = bundle
    files = dict(SOURCE)
    del files[NATIVE]
    write_source(locked, files)
    lock['jsymphonic']['sha256'] = verify.sha(locked)
    shutil.copyfile(locked, bundled)
    with pytest.raises(ValueError, match='Incomplete native Java source snapshot'):
        verify.verify_bridge(resources, lock)


def test_bundled_license_must_match_pinned_source(bundle):
    resources, lock, _, _ = bundle
    (resources / 'notices/jsymphonic/LICENSE').write_bytes(b'other license')
    with pytest.raises(ValueError, match='bundled license differ'):
        verify.verify_bridge(resources, lock)


def test_source_pin_does_not_replace_independent_binary_review(bundle):
    resources, lock, _, _ = bundle
    verify.REVIEWED_ENGINE_SHA256 = '0' * 64
    with pytest.raises(ValueError, match='pinned independent review'):
        verify.verify_bridge(resources, lock)
