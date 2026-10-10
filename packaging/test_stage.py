import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

spec = importlib.util.spec_from_file_location("nightops_stage", Path(__file__).with_name("stage.py"))
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)


class StagingSafetyTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
