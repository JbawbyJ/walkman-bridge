"""Explicit maintainer action: lock downloaded Windows wheels against PyPI hashes."""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel_dir", type=Path)
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("python-wheels.lock.json"))
    args = parser.parse_args()
    rows = []
    names = set()
    for wheel in sorted(args.wheel_dir.glob("*.whl")):
        name, version = wheel.name.split("-")[:2]
        if name in names:
            raise ValueError(f"Multiple wheel versions for {name}; use a fresh wheel directory")
        names.add(name)
        with urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=30) as response:
            metadata = json.load(response)
        artifact = next(item for item in metadata["urls"] if item["filename"] == wheel.name)
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        if digest != artifact["digests"]["sha256"]:
            raise ValueError(f"PyPI artifact hash mismatch: {wheel.name}")
        rows.append(dict(name=name, version=version, filename=wheel.name,
                         url=artifact["url"], sha256=digest))
    if not rows:
        raise ValueError("No wheels to lock")
    args.output.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    args.output.with_name("python-runtime-constraints.txt").write_text(
        "".join(f"{row['name']}=={row['version']}\n" for row in rows), encoding="utf-8")
    print(f"Locked {len(rows)} verified wheels: {args.output}")


if __name__ == "__main__":
    main()
