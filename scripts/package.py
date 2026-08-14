"""Build my_claw.difypkg — zip of the plugin source honoring .difyignore.

Mirrors the marketplace package layout (manifest.yaml at zip root).
"""
from __future__ import annotations

import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "dist", "my_claw.difypkg")


def load_ignore(root: str) -> set[str]:
    path = os.path.join(root, ".difyignore")
    ignored: set[str] = set()
    if not os.path.isfile(path):
        return ignored
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                ignored.add(line.rstrip("/"))
    return ignored


def should_skip(rel: str, ignored: set[str]) -> bool:
    parts = rel.replace("\\", "/").split("/")
    for i in range(len(parts)):
        prefix = "/".join(parts[: i + 1])
        if prefix in ignored:
            return True
    return rel in ignored or os.path.basename(rel) in ignored


def main() -> int:
    ignored = load_ignore(ROOT)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    count = 0
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for current_root, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".pytest_cache", "dist")]
            for fn in sorted(files):
                full = os.path.join(current_root, fn)
                rel = os.path.relpath(full, ROOT)
                if should_skip(rel, ignored) or fn.endswith((".difypkg", ".pyc")):
                    continue
                z.write(full, rel.replace("\\", "/"))
                count += 1
    print(f"packed {count} files -> {OUT} ({os.path.getsize(OUT)} bytes)")
    # sanity checks
    with zipfile.ZipFile(OUT) as z:
        names = z.namelist()
        assert "manifest.yaml" in names, "manifest missing"
        assert "main.py" in names, "entrypoint missing"
        manifest = z.read("manifest.yaml").decode("utf-8")
        assert "name: clawx" in manifest or "name: my_claw" in manifest
    print("verify ok: manifest + main.py present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
