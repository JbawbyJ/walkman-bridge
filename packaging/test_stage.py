import base64
import importlib.util
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import zipfile

spec = importlib.util.spec_from_file_location("nightops_stage", Path(__file__).with_name("stage.py"))
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)


class StagingSafetyTests(unittest.TestCase):
    def test_optimized_python_still_rejects_stale_engine(self):
        code = f'''
import importlib.util
from pathlib import Path
from types import SimpleNamespace
spec = importlib.util.spec_from_file_location('stage', {str(stage.PACKAGING / 'stage.py')!r})
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)
stage.subprocess.run = lambda *a, **k: SimpleNamespace(returncode=0, stdout='{{"event":"playlistJournal","state":"none","files":[]}}\\n{{"event":"done"}}\\n')
try:
    stage.verify_engine_contract(Path('java.exe'), Path('old.jar'))
except ValueError:
    raise SystemExit(0)
raise SystemExit('Stale engine accepted under optimization')
'''
        result = subprocess.run([sys.executable, '-O', '-c', code], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_stale_jar_cannot_be_staged_even_with_a_done_event(self):
        old = SimpleNamespace(returncode=0, stdout='{"event":"playlistJournal","state":"none","files":[]}\n{"event":"done"}\n')
        with patch.object(stage.subprocess, 'run', return_value=old):
            with self.assertRaisesRegex(ValueError, 'recovery contract'):
                stage.verify_engine_contract(Path('java.exe'), Path('old.jar'))

    def test_recovery_contract_probe_is_confined_to_disposable_empty_device(self):
        observed = []
        def run(command, **kwargs):
            if '--device-base64' in command:
                mount = Path(base64.b64decode(command[command.index('--device-base64') + 1]).decode('utf-8'))
            else:
                mount = Path(command[command.index('--device') + 1])
            self.assertTrue((mount / 'OMGAUDIO').is_dir())
            self.assertEqual(list((mount / 'OMGAUDIO').iterdir()), [])
            observed.append(mount)
            if 'playlist-repair' in command:
                events, code = [{'event': 'fatal', 'code': 'PLAYLIST_LIBRARY_NOT_LOADED'}], 1
            elif 'info' in command:
                events, code = [{'event': 'device', 'ok': True}], 0
            else:
                events, code = [{'event': 'playlistJournal', 'state': 'none', 'files': [],
                                 'outcome': 'none', 'library_fingerprint': 'a' * 64}, {'event': 'done'}], 0
            return SimpleNamespace(returncode=code, stdout='\n'.join(json.dumps(e) for e in events))
        with patch.object(stage.subprocess, 'run', side_effect=run):
            stage.verify_engine_contract(Path('java.exe'), Path('candidate.jar'))
        self.assertEqual(len(observed), 4)
        self.assertEqual(len(set(observed)), 1)
        self.assertFalse(observed[0].exists())

    def test_engine_without_path_transport_cannot_be_staged(self):
        def run(command, **kwargs):
            if '--device-base64' in command:
                return SimpleNamespace(returncode=2, stdout='')
            if 'playlist-repair' in command:
                events, code = [{'event': 'fatal', 'code': 'PLAYLIST_LIBRARY_NOT_LOADED'}], 1
            else:
                events, code = [{'event': 'playlistJournal', 'state': 'none', 'files': [],
                                 'outcome': 'none', 'library_fingerprint': 'a' * 64}, {'event': 'done'}], 0
            return SimpleNamespace(returncode=code, stdout='\n'.join(json.dumps(e) for e in events))
        with patch.object(stage.subprocess, 'run', side_effect=run):
            with self.assertRaisesRegex(ValueError, 'UTF-8 path transport'):
                stage.verify_engine_contract(Path('java.exe'), Path('old.jar'))

    def test_vendored_deno_notice_bytes_match_recorded_provenance(self):
        directory = stage.PACKAGING / 'notices/deno'
        for entry in json.loads((directory / 'PROVENANCE.json').read_text(encoding='utf-8')):
            data = (directory / entry['file']).read_bytes()
            self.assertEqual(len(data), entry['bytes'])
            self.assertEqual(hashlib.sha256(data).hexdigest(), entry['sha256'])

    def test_player_requires_deno_notices_and_excludes_device_reconciliation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('product.json', 'python/python.exe', 'python/python313.dll', 'python/python313.zip',
                         'python/Lib/site-packages/fastapi/__init__.py', 'ffmpeg/ffmpeg.exe',
                         'scanner-helper/RedLotus.ScanHelper.exe', 'deno/deno.exe', 'backend/main.py',
                         'backend/boot.py', 'frontend/dist/index.html', 'frontend/dist/fonts/test.woff2',
                         'THIRD-PARTY-NOTICES.md'):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'fixture')
            with self.assertRaisesRegex(FileNotFoundError, 'notices/deno'):
                stage.validate_layout(root, 'player')
            shutil.copytree(stage.PACKAGING / 'notices/deno', root / 'notices/deno')
            stage.validate_layout(root, 'player')
            (root / 'backend/reconciliation.py').write_text('device-only')
            with self.assertRaisesRegex(ValueError, 'device/JSymphonic'):
                stage.validate_layout(root, 'player')

    def test_archive_cannot_escape_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, name in enumerate(("../escaped", "C:/escaped", "/escaped", "..\\escaped")):
                archive = root / f"bad-{index}.zip"
                with zipfile.ZipFile(archive, "w") as zipped:
                    zipped.writestr(name, "untrusted")
                with self.assertRaises(ValueError):
                    stage.extract(archive, root / "destination")
            self.assertFalse((root / "escaped").exists())

    def test_cached_dependency_must_match_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime.zip").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                stage.fetch(dict(filename="runtime.zip", sha256="0" * 64), root, offline=True)

    def test_cleanup_refuses_unrelated_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "keep").write_text("user data")
            with self.assertRaises(ValueError):
                stage.reset_generated(root)
            self.assertEqual((root / "keep").read_text(), "user data")

    def test_wheel_purelib_is_installed_in_site_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "dependency.whl"
            with zipfile.ZipFile(archive, "w") as zipped:
                zipped.writestr("dependency.data/purelib/example.py", "value = 1")
                zipped.writestr("dependency.data/scripts/tool.exe", "unused")
                zipped.writestr("dependency.dist-info/LICENSE", "license")
            stage.extract(archive, root / "site-packages", wheel=True)
            self.assertTrue((root / "site-packages" / "example.py").is_file())
            self.assertFalse(list((root / "site-packages").rglob("*.exe")))
            self.assertTrue((root / "site-packages" / "dependency.dist-info" / "LICENSE").is_file())

    def test_pinned_jsymphonic_archive_mismatch_fails_closed(self):
        lock = json.loads((stage.PACKAGING / "runtime-lock.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packaging = root / "packaging"
            archive = packaging / lock["jsymphonic"]["source_archive"]
            archive.parent.mkdir(parents=True)
            archive.write_bytes(b"not-the-pinned-archive")
            staged = root / "staged-sources"
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                stage.copy_pinned_jsymphonic(lock, staged, packaging)
            self.assertFalse(staged.exists())
            self.assertEqual(list(packaging.rglob("*.partial")), [])


if __name__ == "__main__":
    unittest.main()
