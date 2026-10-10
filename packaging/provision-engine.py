"""Extract the pinned source snapshot locally without GitHub credentials."""
import json
from pathlib import Path
from stage import PACKAGING, ROOT, extract, sha256


record = json.loads((PACKAGING / "runtime-lock.json").read_text())["jsymphonic"]
archive = PACKAGING / record["source_archive"]
if sha256(archive) != record["sha256"]:
    raise ValueError("Vendored Java engine source hash mismatch")
destination = ROOT / ".ci" / "jsymphonic"
if destination.exists():
    raise FileExistsError("Engine source destination already exists; preserve it or choose a clean checkout")
extract(archive, destination)
print(f"Verified Java source extracted: {destination}")
