"""Stage one offline-capable Windows product from checksum-pinned dependencies.

This is a build tool, never bundled as a runtime bypass. It vendors wheels
directly into the isolated CPython embeddable distribution as Python recommends.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile
from urllib.request import Request, urlopen
import zipfile

ROOT = Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "packaging"
BUILD = PACKAGING / "build" / "redlotus"
CACHE = PACKAGING / ".cache" / "nightops"
DEVICE_MODULES = {"device.py", "jsymphonic.py"}


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def reset_generated(directory: Path) -> None:
    resolved = directory.resolve()
    boundary = BUILD.resolve()
    if resolved == boundary or not resolved.is_relative_to(boundary):
        raise ValueError(f"Refusing cleanup outside a product staging directory: {resolved}")
    if directory.is_symlink() or (directory.exists() and directory.resolve() != directory.absolute()):
        raise ValueError("Generated staging paths cannot be redirected")
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)


def fetch(record: dict, cache: Path, offline=False) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    dest = cache / record["filename"]
    if dest.exists():
        if sha256(dest) != record["sha256"]:
            raise ValueError(f"Cached dependency hash mismatch: {dest.name}")
        return dest
    if offline:
        raise FileNotFoundError(f"Offline dependency missing: {dest}")
    temporary = dest.with_suffix(dest.suffix + ".partial")
    request = Request(record["url"], headers={"User-Agent": "RedLotus-Packaging/0.4.1"})
    with urlopen(request, timeout=60) as response, temporary.open("wb") as output:
        shutil.copyfileobj(response, output)
    if sha256(temporary) != record["sha256"]:
        raise ValueError(f"Downloaded dependency hash mismatch: {dest.name}")
    temporary.replace(dest)
    return dest


def extract(archive: Path, dest: Path, *, wheel=False) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as source:
        for member in source.infolist():
            relative = PurePosixPath(member.filename)
            if relative.is_absolute() or ".." in relative.parts or "\\" in member.filename or ":" in member.filename:
                raise ValueError(f"Unsafe archive entry: {member.filename}")
            mode = member.external_attr >> 16
            if mode & 0o170000 == 0o120000:
                raise ValueError("Archives must not contain symbolic links")
            parts = relative.parts
            if wheel and parts and parts[0].endswith(".data"):
                if len(parts) < 3 or parts[1] not in {"purelib", "platlib"}:
                    continue  # Runtime does not ship wheel CLI launchers.
                parts = parts[2:]
            target = dest.joinpath(*parts)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(member) as incoming, target.open("wb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing)


def checked(command, **kwargs):
    completed = subprocess.run([str(value) for value in command], check=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), **kwargs)
    return completed


def verify_requirements(wheels):
    locked = {row["name"].lower().replace("_", "-"): row["version"] for row in wheels}
    for line in (ROOT / "backend" / "requirements.txt").read_text().splitlines():
        requirement = line.split("#", 1)[0].strip()
        if not requirement:
            continue
        if "==" not in requirement:
            raise ValueError("Production requirements must be pinned before packaging")
        name, version = requirement.split("==", 1)
        name = name.split("[", 1)[0].lower().replace("_", "-")
        if locked.get(name) != version:
            raise ValueError(f"Wheel lock is stale for {name}; regenerate it explicitly")


def stage(args):
    runtime_lock = json.loads((PACKAGING / "runtime-lock.json").read_text())
    wheels = json.loads((PACKAGING / "python-wheels.lock.json").read_text())
    verify_requirements(wheels)
    helper = args.helper.resolve()
    if not helper.is_file():
        raise FileNotFoundError("Publish the self-contained .NET 10 scanner helper first")
    if not (ROOT / "frontend" / "dist" / "index.html").is_file():
        raise FileNotFoundError("Build the frontend before packaging")
    if args.product == "bridge" and (not args.jar.is_file() or not (args.jsymphonic_source / "src").is_dir()):
        raise FileNotFoundError("Bridge requires the headless JAR and its corresponding source checkout")

    stage_root = BUILD / args.product
    reset_generated(stage_root)
    python_root = stage_root / "python"
    extract(fetch(runtime_lock["python"], CACHE, args.offline), python_root)
    site_packages = python_root / "Lib" / "site-packages"
    for wheel in wheels:
        extract(fetch(wheel, CACHE / "wheels", args.offline), site_packages, wheel=True)
    # The explicit path file ignores PYTHONPATH, registry Python installations,
    # user site-packages and arbitrary working directories.
    (python_root / "python313._pth").write_text(
        "python313.zip\n.\nLib/site-packages\n../backend\nimport site\n", encoding="utf-8")

    ffmpeg_record = runtime_lock["ffmpeg"]
    ffmpeg_cache = CACHE / ffmpeg_record["filename"]
    legacy_cache = PACKAGING / ".cache" / "ffmpeg-essentials.zip"
    if not ffmpeg_cache.exists() and legacy_cache.exists() and sha256(legacy_cache) == ffmpeg_record["sha256"]:
        shutil.copyfile(legacy_cache, ffmpeg_cache)
    with tempfile.TemporaryDirectory(prefix="ffmpeg-", dir=CACHE) as temporary:
        extract(fetch(ffmpeg_record, CACHE, args.offline), Path(temporary))
        unpacked = next(Path(temporary).glob("ffmpeg-*-essentials_build"))
        ffmpeg = stage_root / "ffmpeg"
        ffmpeg.mkdir()
        shutil.copy2(unpacked / "bin" / "ffmpeg.exe", ffmpeg / "ffmpeg.exe")
        for filename in ("LICENSE", "README.txt"):
            shutil.copy2(unpacked / filename, ffmpeg / filename)

    backend = stage_root / "backend"
    backend.mkdir()
    for module in (ROOT / "backend").glob("*.py"):
        if args.product == "player" and module.name in DEVICE_MODULES:
            continue
        shutil.copy2(module, backend / module.name)
    shutil.copytree(ROOT / "frontend" / "dist", stage_root / "frontend" / "dist")
    helper_dir = stage_root / "scanner-helper"
    helper_dir.mkdir()
    shutil.copy2(helper, helper_dir / "RedLotus.ScanHelper.exe")
    extract(fetch(runtime_lock['deno'], CACHE, args.offline), stage_root / 'deno')
    shutil.copytree(PACKAGING / "notices", stage_root / "notices")
    shutil.copy2(PACKAGING / "NOTICES.md", stage_root / "THIRD-PARTY-NOTICES.md")
    shutil.copy2(ROOT / "LICENSE", stage_root / "LICENSE-APPLICATION.txt")
    for package in ("react", "react-dom", "scheduler"):
        license_path = ROOT / "frontend" / "node_modules" / package / "LICENSE"
        if license_path.exists():
            destination = stage_root / "notices" / "frontend"
            destination.mkdir(exist_ok=True)
            shutil.copy2(license_path, destination / f"{package}-LICENSE.txt")

    if args.product == "bridge":
        jdk = args.jdk_home
        if jdk is None or not (jdk / "bin" / "jlink.exe").is_file():
            archive = fetch(runtime_lock["jdk"], CACHE, args.offline)
            jdk_cache = CACHE / "jdk"
            if not jdk_cache.exists():
                extract(archive, jdk_cache)
            jdk = next(jdk_cache.glob("jdk-*"))
        release = (jdk / "release").read_text()
        if f'JAVA_VERSION="{runtime_lock["jdk"]["version"]}"' not in release:
            raise ValueError("JDK version does not match the runtime lock")
        checked([jdk / "bin" / "jlink.exe", "--add-modules",
                 "java.base,java.desktop,java.logging,java.naming,java.management",
                 "--strip-debug", "--no-header-files", "--no-man-pages", "--compress=zip-6",
                 "--output", stage_root / "jre"], timeout=180)
        (backend / "vendor").mkdir()
        shutil.copy2(args.jar, backend / "vendor" / "jsymphonic.jar")
        java_notices = stage_root / "notices" / "java"
        java_notices.mkdir()
        shutil.copy2(jdk / "NOTICE", java_notices / "NOTICE")
        source_notices = stage_root / "notices" / "jsymphonic"
        source_notices.mkdir()
        shutil.copy2(args.jsymphonic_source / "LICENSE", source_notices / "LICENSE")
        sources = stage_root / "sources"
        sources.mkdir()
        with zipfile.ZipFile(sources / "jsymphonic.zip", "w", zipfile.ZIP_DEFLATED) as archive:
            for source in sorted(args.jsymphonic_source.rglob("*")):
                relative = source.relative_to(args.jsymphonic_source)
                allowed = relative.parts[0] in {"src", "docs"} or relative.as_posix() in {"pom.xml", "LICENSE", "README.md", "INSTALLING-FFMPEG.md", "NATIVE-PLAYLISTS.md"}
                if source.is_file() and allowed:
                    archive.write(source, relative.as_posix())

    (stage_root / "product.json").write_text(json.dumps({"product": args.product}) + "\n", encoding="utf-8")
    shutil.copy2(PACKAGING / "python-wheels.lock.json", stage_root / "python-wheels.lock.json")
    validate_layout(stage_root, args.product)
    checked([python_root / "python.exe", "-I", "-B", "-c",
             "import sys,ssl,sqlite3,fastapi,uvicorn,pydantic_core,multipart,yt_dlp,yt_dlp_ejs; "
             "assert sys.flags.isolated; print('Bundled Python',sys.version.split()[0], 'dependencies OK')"],
            cwd=stage_root, timeout=30)
    version = json.loads((ROOT / "package.json").read_text())["version"]
    manifest = {"product": args.product, "version": version, "runtime_lock": runtime_lock,
                "files": {path.relative_to(stage_root).as_posix(): sha256(path)
                          for path in sorted(stage_root.rglob("*")) if path.is_file()}}
    (stage_root / "runtime-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Staged {args.product}: {stage_root} ({len(manifest['files'])} files)")


def validate_layout(root: Path, product: str):
    required = ["product.json", "python/python.exe", "python/python313.dll",
                "python/python313.zip", "python/Lib/site-packages/fastapi/__init__.py",
                "ffmpeg/ffmpeg.exe", "scanner-helper/RedLotus.ScanHelper.exe", "deno/deno.exe",
                "backend/main.py", "backend/boot.py", "frontend/dist/index.html", "THIRD-PARTY-NOTICES.md"]
    if product == "bridge":
        required += ["jre/bin/java.exe", "backend/vendor/jsymphonic.jar", "sources/jsymphonic.zip"]
    for relative in required:
        if not (root / relative).is_file():
            raise FileNotFoundError(f"Product resource missing: {relative}")
    if not list((root / "frontend" / "dist" / "fonts").glob("*.woff2")):
        raise FileNotFoundError("Self-hosted font files are required")
    if product == "player":
        if (root / "jre").exists() or list(root.rglob("*.jar")):
            raise ValueError("Player must not contain Java or a device JAR")
        if any((root / "backend" / name).exists() for name in DEVICE_MODULES):
            raise ValueError("Player must not bundle device/JSymphonic modules")
    forbidden = {".venv", "pyvenv.cfg", "node_modules", ".env", "walkman-jobs.sqlite"}
    if any(any(part in forbidden for part in path.relative_to(root).parts) for path in root.rglob("*")):
        raise ValueError("Development/runtime user data leaked into staging")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--product", required=True, choices=("bridge", "player"))
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--jdk-home", type=Path, default=ROOT.parent / "tools" / "jdk-21.0.12+8")
    parser.add_argument("--jar", type=Path, default=ROOT / "backend" / "vendor" / "jsymphonic.jar")
    parser.add_argument("--jsymphonic-source", type=Path, default=ROOT.parent / "jsymphonic")
    parser.add_argument("--helper", type=Path, default=ROOT / "scanner-helper" / "artifacts" / "win-x64" / "RedLotus.ScanHelper.exe")
    stage(parser.parse_args())


if __name__ == "__main__":
    main()
