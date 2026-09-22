"""Verify the delivered source archive's SHA-256 file manifest (stdlib only)."""
import hashlib
from pathlib import Path
import sys

root = Path(__file__).resolve().parent.parent
manifest = root / "MANIFEST.sha256"
failures = []
for line in manifest.read_text(encoding="utf-8").splitlines():
    digest, relative = line.split("  ", 1)
    path = root / relative
    if path.resolve().is_relative_to(root.resolve()) and path.is_file():
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != digest:
            failures.append(relative + ": hash mismatch")
    else:
        failures.append(relative + ": missing or unsafe path")
if failures:
    print("\n".join(failures))
    raise SystemExit(1)
print("All manifest entries match.")
