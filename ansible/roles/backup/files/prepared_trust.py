#!/usr/bin/python3
"""Prepared-job execution boundary: trust every path component, not just a leaf."""
import os
from pathlib import Path
import stat
import sys


def trusted_path(value, *, allow_missing=False):
    if not isinstance(value, str) or not value.startswith("/") or any(
            part in ("", ".", "..") for part in value.split("/")[1:]):
        raise ValueError("noncanonical prepared path")
    path = Path(value)
    # Walk from the root down. Once a parent is protected, an unprivileged
    # writer cannot replace the next checked component between lstat and exec.
    owner = os.geteuid()
    current = Path("/")
    for part in path.parts:
        if part != "/":
            current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            if allow_missing and current.parent != Path("/tmp"):
                return
            raise
        directory = stat.S_ISDIR(info.st_mode)
        sticky_temp = (current == Path("/tmp") and directory and
                       info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX))
        if (info.st_uid not in (0, owner) or
                (info.st_mode & 0o022 and not sticky_temp) or
                (current != path and not directory)):
            raise ValueError("untrusted prepared path component")
        if current == path and not (directory or
                (stat.S_ISREG(info.st_mode) and info.st_nlink == 1)):
            raise ValueError("unsafe prepared path leaf")


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
