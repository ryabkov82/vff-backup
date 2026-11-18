#!/usr/bin/env python3
"""Fail-closed manifest verifier for a private, freshly prepared backup generation."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

MANIFEST = ".vff-snapshot.json"

def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate manifest key")
        result[key] = value
    return result

def check_private(path, directory=False):
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError("non-private or unsupported staging entry")
    if not directory and info.st_nlink != 1:
        raise ValueError("hardlinked staging file")

def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()

def verify(root):
    check_private(root, directory=True)
    manifest = root / MANIFEST
    check_private(manifest)
    if manifest.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("manifest too large")
    cfg = json.loads(manifest.read_text(), object_pairs_hook=unique)
    if set(cfg) != {"version", "files"} or type(cfg["version"]) is not int or cfg["version"] != 1 or not isinstance(cfg["files"], dict) or not cfg["files"]:
        raise ValueError("invalid manifest")
    expected = cfg["files"]
    actual = {}
    def walk_error(error):
        raise error
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
        for name in dirs:
            check_private(Path(directory) / name, directory=True)
        for name in files:
            path = Path(directory) / name
            check_private(path)
            relative = path.relative_to(root).as_posix()
            if relative == MANIFEST:
                continue
            actual[relative] = digest(path)
    if any(not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v) for v in expected.values()):
        raise ValueError("invalid digest")
    if actual != expected:
        raise ValueError("manifest mismatch")
    return len(actual)

def main():
    try:
        if len(sys.argv) != 2:
            raise ValueError("one generation required")
        print(json.dumps({"status": "PASS_SEALED_GENERATION", "files": verify(Path(sys.argv[1]))}))
    except (OSError, ValueError, TypeError):
        print('{"status":"FAIL_SEALED_GENERATION"}', file=sys.stderr)
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
