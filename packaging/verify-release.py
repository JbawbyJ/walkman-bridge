"""Verify the current frozen release without rebuilding or executing products.

Run with Python 3.11+ and Node.js available on PATH (or --node PATH). Product
identity comes from package.json/products.cjs; 0.4.0 remains a pinned historical
preservation check. The only write is the explicit/default JSON proof report.
This establishes artifact/source parity, not clean-guest runtime acceptance.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / 'packaging/build/branding-041/artifact-proof.json'
HISTORICAL_INSTALLERS = {
    'bridge/Walkman-Bridge-Night-Ops-Setup-0.4.0-x64.exe':
        'd24f785e5b75be81458f27e1580a4846e88fce51664d6ee682e0b6c36e0f4076',
    'player/Red-Lotus-Player-Setup-0.4.0-x64.exe':
        '393dad9bfb124d4e34760ead3ebf610f5eb37d4ebadbd863d7cd4101443f1b47',
}
# Independently reviewed native-playlist engine; update only with new engine
# review evidence, independently of the desktop product's version/branding.
REVIEWED_ENGINE_SHA256 = 'cbab7deae5baed02b799f43a454095e5b8b8d00a26151cc0c8649c2cccbcfc72'
DEVICE_MODULES = {'device.py', 'jsymphonic.py'}
BUILDER_RESOURCE_FILES = {'runtime-manifest.json', 'app.asar', 'elevate.exe'}

NODE_INSPECTION = r'''
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const root = process.argv[1];
const asar = require(path.join(root, 'node_modules/@electron/asar'));
const {products} = require(path.join(root, 'packaging/products.cjs'));
const pkg = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));
function check(value, message) { if (!value) throw new Error(message); }
const digest = value => crypto.createHash('sha256').update(value).digest('hex');
const modules = ['boundary.cjs', 'main.cjs', 'preload.cjs', 'scanner-broker.cjs'];
const expected = ['package.json', ...modules.map(name => `electron/${name}`)].sort();
check(JSON.stringify(Object.keys(products).sort()) === JSON.stringify(['bridge', 'player']), 'Unexpected product mapping');
const rows = {};
for (const [key, product] of Object.entries(products)) {
  const archive = path.join(root, 'dist_electron', key, 'win-unpacked/resources/app.asar');
  const names = asar.listPackage(archive).map(name => name.replaceAll('\\', '/').replace(/^\//, ''))
    .filter(name => !asar.statFile(archive, name).files).sort();
  check(JSON.stringify(names) === JSON.stringify(expected), `${key}: unexpected Electron archive entries`);
  const hashes = {};
  for (const name of modules) {
    const relative = `electron/${name}`;
    hashes[relative] = digest(asar.extractFile(archive, relative));
    check(hashes[relative] === digest(fs.readFileSync(path.join(root, relative))), `${key}: stale ${relative}`);
  }
  const installedPackage = JSON.parse(asar.extractFile(archive, 'package.json').toString('utf8'));
  for (const [field, value] of Object.entries({version:pkg.version, name:product.packageName,
      productName:product.productName, main:pkg.main})) {
    check(installedPackage[field] === value, `${key}: packaged ${field} differs from current product identity`);
  }
  rows[key] = {definition:product, electron:{files:names, source_sha256:hashes, package:installedPackage}};
}
process.stdout.write(JSON.stringify({version:pkg.version, products:rows}));
'''


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def load_json(path: Path) -> dict:
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f'Duplicate JSON key {key!r}: {path}')
            result[key] = value
        return result
    return json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=unique_pairs)


def relative_file(root: Path, name: str) -> Path:
    require(isinstance(name, str) and bool(name), 'Empty or non-string resource path')
    relative = PurePosixPath(name)
    require(not relative.is_absolute() and '\\' not in name and ':' not in name
            and '..' not in relative.parts and relative.as_posix() == name,
            f'Unsafe or noncanonical relative path: {name!r}')
    target = root.joinpath(*relative.parts)
    require(target.resolve().is_relative_to(root.resolve()), f'Resource escapes its directory: {name}')
    require(target.is_file() and not target.is_symlink(), f'Resource is not a regular file: {target}')
    return target


def files_under(root: Path) -> dict[str, Path]:
    require(root.is_dir(), f'Missing directory: {root}')
    result = {}
    for path in root.rglob('*'):
        require(not path.is_symlink() and not (hasattr(path, 'is_junction') and path.is_junction()),
                f'Link/reparse directory is not a release artifact: {path}')
        if path.is_file():
            result[path.relative_to(root).as_posix()] = path
    return result


def parity(source: Path, packaged: Path, label: str) -> str:
    expected = sha(source)
    require(expected == sha(packaged), f'Source parity failed: {label}')
    return expected


def checksums(path: Path) -> dict[str, str]:
    result = {}
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r'([0-9a-fA-F]{64})[ \t]+\*?(.+)', line)
        require(match is not None, f'Malformed SHA256SUMS line: {path}')
        digest, name = match.groups()
        require(name == Path(name).name and '/' not in name and '\\' not in name
                and ':' not in name and name not in result, f'Ambiguous checksum filename: {name}')
        result[name] = digest.lower()
    return result


def source_archive_files(archive: zipfile.ZipFile) -> dict[str, bytes]:
    names = archive.namelist()
    require(len(names) == len(set(names)), 'Duplicate source archive entry')
    require(all(not name.endswith('/') for name in names), 'Unexpected directory entry in source snapshot')
    return {name: archive.read(name) for name in names}


def verify_bridge(resources: Path, runtime_lock: dict) -> dict:
    jar = resources / 'backend/vendor/jsymphonic.jar'
    digest = parity(ROOT / 'backend/vendor/jsymphonic.jar', jar, 'Bridge reviewed Java engine')
    require(digest == REVIEWED_ENGINE_SHA256, 'Bridge Java engine lacks the pinned independent review')
    require((resources / 'jre/bin/java.exe').is_file(), 'Bridge Java runtime missing')
    require(f'JAVA_VERSION="{runtime_lock["jdk"]["version"]}"' in
            (resources / 'jre/release').read_text(encoding='utf-8'), 'Bridge Java version differs from runtime lock')
    with zipfile.ZipFile(jar) as binary:
        require({'org/naurd/media/jsymphonic/headless/HeadlessCli.class',
                 'org/naurd/media/jsymphonic/device/sony/nw/NativePlaylistDatabase.class'} <= set(binary.namelist()),
                'Bridge JAR is missing native playlist entry points')
    locked_source = relative_file(ROOT / 'packaging', runtime_lock['jsymphonic']['source_archive'])
    require(sha(locked_source) == runtime_lock['jsymphonic']['sha256'], 'Locked Java source archive hash mismatch')
    with zipfile.ZipFile(locked_source) as original, zipfile.ZipFile(resources / 'sources/jsymphonic.zip') as bundled:
        expected, actual = source_archive_files(original), source_archive_files(bundled)
    require(expected == actual, 'Bundled Java source differs from the locked corresponding source')
    require({'NATIVE-PLAYLISTS.md', 'LICENSE', 'pom.xml',
             'src/main/java/org/naurd/media/jsymphonic/device/sony/nw/NativePlaylistDatabase.java'} <= set(expected),
            'Incomplete native Java source snapshot')
    checkout = ROOT.parent / 'jsymphonic'
    allowed_root_files = {'pom.xml', 'LICENSE', 'README.md', 'INSTALLING-FFMPEG.md', 'NATIVE-PLAYLISTS.md'}
    current = {name: checkout / name for name in allowed_root_files if (checkout / name).is_file()}
    for directory in ('src', 'docs'):
        if (checkout / directory).exists():
            current.update({directory + '/' + name: path for name, path in files_under(checkout / directory).items()})
    require(set(current) == set(expected), 'Locked Java source file set differs from the current engine checkout')
    for name, path in current.items():
        require(path.read_bytes() == expected[name], f'Java source snapshot is stale: {name}')
    require(expected['LICENSE'] == (resources / 'notices/jsymphonic/LICENSE').read_bytes(),
            'Bridge Java source and bundled license differ')
    require((resources / 'notices/java/NOTICE').is_file() and bool(list((resources / 'jre/legal').rglob('LICENSE'))),
            'Bridge Java runtime license/notice is missing')
    return {'jar_sha256': digest, 'source_archive': runtime_lock['jsymphonic']['source_archive'],
            'source_archive_sha256': sha(locked_source), 'source_files': len(expected), 'license_parity': True}


def verify_product(product: str, metadata: dict, version: str, runtime_lock: dict) -> dict:
    definition = metadata['definition']
    product_root = ROOT / 'dist_electron' / product
    resources = product_root / 'win-unpacked/resources'
    manifest = load_json(resources / 'runtime-manifest.json')
    require(manifest.get('version') == version and manifest.get('product') == product, f'{product}: stale manifest identity')
    require(manifest.get('runtime_lock') == runtime_lock, f'{product}: stale runtime lock')
    require(load_json(resources / 'product.json') == {'product': product}, f'{product}: incorrect product marker')
    inventory = manifest.get('files')
    require(isinstance(inventory, dict) and bool(inventory), f'{product}: missing manifest file inventory')
    actual = files_under(resources)
    require(set(actual) == set(inventory) | BUILDER_RESOURCE_FILES,
            f'{product}: resource inventory mismatch; extra={sorted(set(actual) - set(inventory) - BUILDER_RESOURCE_FILES)} '
            f'missing={sorted((set(inventory) | BUILDER_RESOURCE_FILES) - set(actual))}')
    for name, digest in inventory.items():
        require(isinstance(digest, str) and re.fullmatch(r'[0-9a-f]{64}', digest) is not None, f'{product}: invalid digest for {name}')
        require(sha(relative_file(resources, name)) == digest, f'{product}: manifest hash mismatch for {name}')
    checked = {}
    backend_expected = {path.name: path for path in (ROOT / 'backend').glob('*.py')
                        if product == 'bridge' or path.name not in DEVICE_MODULES}
    backend_actual = {path.name for path in (resources / 'backend').glob('*.py')}
    require(set(backend_expected) == backend_actual, f'{product}: unexpected/missing backend modules')
    for name, source in backend_expected.items():
        checked['backend/' + name] = parity(source, resources / 'backend' / name, product + ': ' + name)
    frontend = files_under(ROOT / 'frontend/dist')
    require(set(frontend) == set(files_under(resources / 'frontend/dist')), f'{product}: unexpected/missing frontend files')
    for name, source in frontend.items():
        checked['frontend/dist/' + name] = parity(source, resources / 'frontend/dist' / name, product + ': ' + name)
    helper = 'scanner-helper/RedLotus.ScanHelper.exe'
    checked[helper] = parity(ROOT / 'scanner-helper/artifacts/win-x64/RedLotus.ScanHelper.exe', resources / helper, product + ': scanner helper')
    require(set(files_under(resources / 'scanner-helper')) == {'RedLotus.ScanHelper.exe'}, f'{product}: stale/additional scanner helpers')
    for source, name in [(ROOT / 'packaging/NOTICES.md', 'THIRD-PARTY-NOTICES.md'),
                         (ROOT / 'LICENSE', 'LICENSE-APPLICATION.txt'),
                         (ROOT / 'packaging/python-wheels.lock.json', 'python-wheels.lock.json')]:
        checked[name] = parity(source, resources / name, product + ': ' + name)
    for name, source in files_under(ROOT / 'packaging/notices').items():
        checked['notices/' + name] = parity(source, resources / 'notices' / name, product + ': notice ' + name)
    engine = None
    if product == 'player':
        require(not any(name.lower().endswith('.jar') for name in actual) and not (resources / 'jre').exists(), 'Player contains Java')
        require(not any((resources / 'backend' / name).exists() for name in DEVICE_MODULES), 'Player contains device modules')
        require(not (resources / 'sources/jsymphonic.zip').exists(), 'Player contains a device-engine source archive')
    else:
        engine = verify_bridge(resources, runtime_lock)
    filename = definition['artifactName'].replace('${version}', version).replace('${arch}', 'x64').replace('${ext}', 'exe')
    require('${' not in filename and filename == Path(filename).name, f'{product}: unsupported artifact template')
    sums = checksums(product_root / 'SHA256SUMS.txt')
    artifacts = []
    for name in (filename, filename + '.blockmap'):
        path = relative_file(product_root, name)
        digest = sha(path)
        require(name in sums and digest == sums[name], f'{product}: current installer/blockmap checksum mismatch: {name}')
        artifacts.append({'filename': name, 'size_bytes': path.stat().st_size, 'sha256': digest})
    executable = relative_file(product_root / 'win-unpacked', definition['executableName'] + '.exe')
    with executable.open('rb') as stream:
        require(stream.read(2) == b'MZ', f'{product}: unpacked executable is not a Windows binary')
    return {'product': product, 'passed': True, 'inventory_files': len(inventory),
            'manifest_sha256': sha(resources / 'runtime-manifest.json'), 'source_parity': checked,
            'electron': metadata['electron'], 'engine': engine, 'player_exclusions_verified': product == 'player',
            'executable': {'filename': executable.name, 'sha256': sha(executable)}, 'artifacts': artifacts}


def verify(node: str, report: dict) -> None:
    inspection = subprocess.run([node, '-e', NODE_INSPECTION, str(ROOT)], cwd=ROOT, capture_output=True,
                                text=True, encoding='utf-8', timeout=60,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    require(inspection.returncode == 0, 'Product/ASAR inspection failed: ' + (inspection.stderr or inspection.stdout).strip())
    metadata = json.loads(inspection.stdout)
    version = metadata['version']
    require(re.fullmatch(r'\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?', version) is not None, 'Invalid current product version')
    report['version'] = version
    report['historical_verifier_sha256'] = sha(ROOT / 'packaging/verify-management-release.py')
    historical = []
    for name, digest in HISTORICAL_INSTALLERS.items():
        file = relative_file(ROOT / 'dist_electron', name)
        require(sha(file) == digest, f'Historical 0.4.0 installer changed: {name}')
        historical.append({'filename': name, 'sha256': digest, 'preserved': True})
    report['historical_installers'] = historical
    runtime_lock = load_json(ROOT / 'packaging/runtime-lock.json')
    for product in ('bridge', 'player'):
        report['products'].append(verify_product(product, metadata['products'][product], version, runtime_lock))
    report['old_installers_preserved'] = True
    report['artifacts'] = [dict(artifact, product=row['product']) for row in report['products'] for artifact in row['artifacts']]
    report['passed'] = True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node', default=shutil.which('node'), help='Node executable; defaults to PATH')
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = args.output.resolve()
    require(output.is_relative_to((ROOT / 'packaging/build').resolve()), 'Proof output must remain under packaging/build')
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'passed': False,
              'scope': 'Frozen artifact, inventory, identity and source parity; no guest-runtime claim', 'products': []}
    try:
        require(bool(args.node), 'Node.js is required to read product definitions and Electron ASARs; provide --node')
        verify(args.node, report)
    except Exception as error:
        report['error'] = f'{type(error).__name__}: {error}'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'version': report.get('version'), 'proof': str(output),
                      'products': [{'product': row['product'], 'inventory_files': row['inventory_files']} for row in report['products']],
                      'error': report.get('error')}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
