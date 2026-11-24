#!/usr/bin/python3
"""Prepared-job execution boundary: trust every path component, not just a leaf."""
import os
from pathlib import Path
import stat
import sys


def canonical_path(value):
    if not isinstance(value, str) or not value.startswith("/") or any(
            part in ("", ".", "..") for part in value.split("/")[1:]):
        raise ValueError("noncanonical prepared path")
    return Path(value)


def require_trusted_component(info, owner, leaf):
    if info.st_uid not in (0, owner) or info.st_mode & 0o022:
        raise ValueError("untrusted prepared path component")
    if stat.S_ISDIR(info.st_mode):
        return
    if not leaf or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("unsafe prepared path component")


def trusted_path(value, *, allow_missing=False):
    path = canonical_path(value)
    # No public writable ancestor, including sticky shared directories.
    # Walk top-down before allowing a missing tail for installation.
    owner = os.geteuid()
    for current in (*reversed(path.parents), path):
        try:
            info = current.lstat()
        except FileNotFoundError:
            if allow_missing:
                return
            raise
        require_trusted_component(info, owner, current == path)


def main(args):
    allow_missing = bool(args and args[0] == "--allow-missing")
    paths = args[1:] if allow_missing else args
    if not paths:
        raise ValueError("prepared trust paths required")
    for value in paths:
        trusted_path(value, allow_missing=allow_missing)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except (OSError, ValueError):
        print("Unsafe prepared execution/configuration path", file=sys.stderr)
        sys.exit(1)
