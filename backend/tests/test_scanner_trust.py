"""Scanner discovery tests never execute Defender or request elevation."""
import os
from pathlib import Path

import pytest

import scan


@pytest.mark.parametrize("variable,relative", [
    ("ProgramFiles", "Windows Defender/MpCmdRun.exe"),
    ("ProgramData", "Microsoft/Windows Defender/Platform/999.0/MpCmdRun.exe"),
    ("PATH", "MpCmdRun.exe"),
])
def test_launch_environment_cannot_select_scanner(monkeypatch, tmp_path, variable, relative):
    fake = tmp_path / relative
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_bytes(b"not an executable")
    monkeypatch.setenv(variable, str(tmp_path))
    # Hide the host installation so all legacy fallback branches are covered.
    real_is_file = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda path: path == fake and real_is_file(path))
    assert scan._defender_exe() is None


@pytest.mark.skipif(os.name != "nt", reason="Windows known-folder API")
def test_windows_discovery_ignores_spoofed_environment(monkeypatch, tmp_path):
    before = (scan._known_folder("program_files"), scan._known_folder("program_data"), scan._system_directory())
    for name in ("ProgramFiles", "ProgramW6432", "ProgramData", "SystemRoot", "WINDIR", "SystemDrive", "PATH"):
        monkeypatch.setenv(name, str(tmp_path))
    assert (scan._known_folder("program_files"), scan._known_folder("program_data"), scan._system_directory()) == before
    assert all(path.is_absolute() and path.is_dir() and path != tmp_path for path in before)


def test_discovery_failure_never_falls_back_to_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    fake = tmp_path / "Windows Defender" / "MpCmdRun.exe"
    fake.parent.mkdir()
    fake.write_bytes(b"not an executable")
    def unavailable(_):
        raise OSError("known-folder API failed")
    monkeypatch.setattr(scan, "_known_folder", unavailable)
    assert scan._defender_exe() is None


def test_scanner_reparse_and_unexpected_paths_are_rejected(monkeypatch, tmp_path):
    program_files = tmp_path / "program-files"
    program_data = tmp_path / "program-data"
    monkeypatch.setattr(scan, "_known_folder", lambda name: program_files if name == "program_files" else program_data)
    trusted = program_files / "Windows Defender" / "MpCmdRun.exe"
    trusted.parent.mkdir(parents=True)
    trusted.write_bytes(b"fixture")
    assert scan._defender_exe() == trusted
    monkeypatch.setattr(scan, "_has_reparse_component", lambda path: path == trusted)
    assert scan._defender_exe() is None


def test_platform_directories_require_version_names_and_numeric_order(monkeypatch, tmp_path):
    monkeypatch.setattr(scan, "_known_folder", lambda name: tmp_path / name)
    platform = tmp_path / "program_data" / "Microsoft" / "Windows Defender" / "Platform"
    for version in ("9.9.0.0-0", "10.0.0.0-0", "evil-99999"):
        candidate = platform / version / "MpCmdRun.exe"
        candidate.parent.mkdir(parents=True)
        candidate.write_bytes(b"fixture")
    assert scan._defender_exe() == platform / "10.0.0.0-0" / "MpCmdRun.exe"


@pytest.mark.skipif(os.name != "nt", reason="Windows system directory and known-folder API")
def test_scanner_child_environment_drops_injection_and_spoofed_roots(monkeypatch, tmp_path):
    for key in ("DOTNET_STARTUP_HOOKS", "COMPlus_ReadyToRun", "CORECLR_PROFILER_PATH", "COR_PROFILER", "PSModulePath", "ProgramFiles", "ProgramData", "SystemRoot", "WINDIR", "PATH"):
        monkeypatch.setenv(key, "untrusted")
    env = scan._trusted_process_environment()
    assert "untrusted" not in env.values()
    assert Path(env["PATH"]) == scan._system_directory()
    assert Path(env["ProgramFiles"]) == scan._known_folder("program_files")
    assert Path(env["ProgramData"]) == scan._known_folder("program_data")


def test_untrusted_selection_cannot_reach_process_launch(monkeypatch, tmp_path):
    fake = tmp_path / "MpCmdRun.exe"
    fake.write_bytes(b"fixture")
    monkeypatch.setattr(scan, "_defender_exe", lambda: fake)
    def forbidden(*args, **kwargs):
        pytest.fail("untrusted scanner reached process creation")
    monkeypatch.setattr(scan.subprocess, "run", forbidden)
    with pytest.raises(OSError):
        scan._invoke_defender(tmp_path / "song.mp3")


@pytest.mark.skipif(os.name != "nt", reason="Windows file identity and sharing")
def test_open_scanner_retains_identity_and_denies_writes(monkeypatch, tmp_path):
    monkeypatch.setattr(scan, "_known_folder", lambda name: tmp_path / name)
    candidate = tmp_path / "program_files" / "Windows Defender" / "MpCmdRun.exe"
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(b"harmless executable-identity fixture")
    with scan._locked_scanner(candidate):
        with pytest.raises(PermissionError):
            candidate.write_bytes(b"replace")
        with pytest.raises(PermissionError):
            candidate.unlink()
    assert candidate.read_bytes() == b"harmless executable-identity fixture"


@pytest.mark.skipif(os.name != "nt", reason="Windows directory junction")
def test_real_junction_cannot_redirect_scanner(monkeypatch, tmp_path):
    import _winapi
    program_files = tmp_path / "program_files"
    program_files.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "MpCmdRun.exe").write_bytes(b"fixture")
    junction = program_files / "Windows Defender"
    _winapi.CreateJunction(str(outside), str(junction))
    monkeypatch.setattr(scan, "_known_folder", lambda name: tmp_path / name)
    try:
        assert scan._defender_exe() is None
        with pytest.raises(OSError):
            with scan._locked_scanner(junction / "MpCmdRun.exe"):
                pytest.fail("junction scanner accepted")
    finally:
        junction.rmdir()


@pytest.mark.skipif(os.name != "nt", reason="Windows file identity and sharing")
def test_invocation_holds_scanner_and_uses_fixed_command_and_environment(monkeypatch, tmp_path):
    from types import SimpleNamespace
    monkeypatch.setattr(scan, "_known_folder", lambda name: tmp_path / name)
    candidate = tmp_path / "program_files" / "Windows Defender" / "MpCmdRun.exe"
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(b"harmless fixture never executed")
    media = tmp_path / "song.mp3"
    monkeypatch.setenv("DOTNET_STARTUP_HOOKS", "untrusted")
    seen = []
    def observe_run(command, **options):
        assert command == [str(candidate), "-Scan", "-ScanType", "3", "-File", str(media), "-DisableRemediation"]
        assert options["cwd"] == str(scan._system_directory())
        assert "untrusted" not in options["env"].values()
        with pytest.raises(PermissionError):
            candidate.write_bytes(b"replace while running")
        seen.append(command)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(scan.subprocess, "run", observe_run)
    assert scan._invoke_defender(media).returncode == 0
    assert len(seen) == 1
