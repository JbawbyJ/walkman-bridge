"""Check final 0.4.0 resources against their inventory and reviewed checkout."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

rows = []
for product in ('bridge', 'player'):
    resources = ROOT / 'dist_electron' / product / 'win-unpacked/resources'
    manifest = json.loads((resources / 'runtime-manifest.json').read_text(encoding='utf-8'))
    assert manifest['version'] == '0.4.0' and manifest['product'] == product
    for relative, digest in manifest['files'].items():
        assert sha(resources / relative) == digest, (product, relative)
    checked = []
    for source in (ROOT / 'backend').glob('*.py'):
        if product == 'player' and source.name in ('device.py', 'jsymphonic.py'):
            assert not (resources / 'backend' / source.name).exists()
            continue
        assert sha(source) == sha(resources / 'backend' / source.name), source
        checked.append('backend/' + source.name)
    for source in (ROOT / 'frontend/dist').rglob('*'):
        if source.is_file():
            relative = source.relative_to(ROOT / 'frontend/dist')
            assert sha(source) == sha(resources / 'frontend/dist' / relative)
            checked.append('frontend/dist/' + relative.as_posix())
    assert sha(ROOT / 'scanner-helper/artifacts/win-x64/nightops-scanner-helper.exe') == sha(resources / 'scanner-helper/NightOps.ScanHelper.exe')
    if product == 'player':
        assert not (resources / 'jre').exists() and not list(resources.rglob('*.jar'))
    else:
        assert sha(resources / 'backend/vendor/jsymphonic.jar') == 'cbab7deae5baed02b799f43a454095e5b8b8d00a26151cc0c8649c2cccbcfc72'
        lock = manifest['runtime_lock']['jsymphonic']
        source_zip = ROOT / 'packaging' / lock['source_archive']
        assert sha(source_zip) == lock['sha256']
        with zipfile.ZipFile(source_zip) as expected, zipfile.ZipFile(resources / 'sources/jsymphonic.zip') as bundled:
            assert 'NATIVE-PLAYLISTS.md' in expected.namelist()
            assert set(expected.namelist()) == set(bundled.namelist())
            assert all(expected.read(name) == bundled.read(name) for name in expected.namelist())
    rows.append({'product': product, 'passed': True, 'inventory_files': len(manifest['files']), 'source_parity': checked})
old = {'player/Red-Lotus-Player-Setup-0.3.0-x64.exe':'50e6c5a4cd02999582d6ff4e916b25053607d68f2676bc2dc0ce069b4e683892',
       'bridge/Walkman-Bridge-Night-Ops-Setup-0.3.0-x64.exe':'64af2f5bc5ee2c9047ceb2532ecdc3b41b845c114d16a69b65ec69663959e470'}
assert all(sha(ROOT / 'dist_electron' / name) == digest for name, digest in old.items())
artifacts = []
for product, filename in [('player', 'Red-Lotus-Player-Setup-0.4.0-x64.exe'), ('bridge', 'Walkman-Bridge-Night-Ops-Setup-0.4.0-x64.exe')]:
    file = ROOT / 'dist_electron' / product / filename
    artifacts.append({'product': product, 'filename': filename, 'size_bytes': file.stat().st_size, 'sha256': sha(file)})
out = ROOT / 'packaging/build/management-upgrade/artifact-proof.json'
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps({'passed': True, 'old_installers_preserved': True, 'products': rows, 'artifacts': artifacts}, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'passed': True, 'products': [{'product': row['product'], 'inventory_files': row['inventory_files']} for row in rows], 'artifacts': artifacts}))
