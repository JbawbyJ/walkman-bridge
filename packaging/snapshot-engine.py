"""Explicit maintainer operation: vendor versioned headless source for CI."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--revision", required=True, help="Release snapshot label (letters, digits, dot or hyphen)")
    args = parser.parse_args()
    if not args.revision or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-' for c in args.revision):
        parser.error('Invalid source revision label')
    packaging = Path(__file__).resolve().parent
    lock_path = packaging / "runtime-lock.json"
    lock = json.loads(lock_path.read_text(encoding='utf-8'))
    destination = packaging / "sources" / f"jsymphonic-{args.revision}-src.zip"
    if destination.exists():
        parser.error('Snapshot already exists; choose a new revision label to preserve previous release sources')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source in sorted(args.source.rglob("*")):
            relative = source.relative_to(args.source)
            allowed = relative.parts[0] in {"src", "docs"} or relative.as_posix() in {"pom.xml", "LICENSE", "README.md", "INSTALLING-FFMPEG.md", "NATIVE-PLAYLISTS.md"}
            if source.is_file() and allowed:
                entry = zipfile.ZipInfo(relative.as_posix(), date_time=(2026, 1, 1, 0, 0, 0))
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.external_attr = 0o100644 << 16
                archive.writestr(entry, source.read_bytes())
    lock["jsymphonic"]["source_archive"] = destination.relative_to(packaging).as_posix()
    lock["jsymphonic"]["source_revision"] = args.revision
    lock["jsymphonic"]["source_description"] = "Release snapshot including local changes on the recorded base commit; archive SHA-256 identifies the complete source."
    lock["jsymphonic"]["sha256"] = hashlib.sha256(destination.read_bytes()).hexdigest()
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    print(f"Vendored source: {destination} ({destination.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
