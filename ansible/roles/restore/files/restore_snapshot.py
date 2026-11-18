#!/usr/bin/env python3
"""Select an exact-host snapshot and restore only into private new staging."""
import datetime
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile


def require(condition, message):
    if not condition:
        raise ValueError(message)


def snapshot_id(row):
    value = row.get("id")
    require(isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value),
            "invalid snapshot metadata")
    return value


def select_snapshot(rows, host, tag, requested="", newest=True):
    require(isinstance(rows, list), "invalid snapshot list")
    require(isinstance(host, str) and bool(host.strip()), "explicit snapshot host required")
    require(not requested or re.fullmatch("[0-9a-f]{8,64}", requested),
            "invalid snapshot ID")
    require(all(isinstance(row, dict) for row in rows), "invalid snapshot rows")
    candidates = [row for row in rows if row.get("hostname") == host
                  and (not tag or tag in (row.get("tags") or []))]
    if requested:
        candidates = [row for row in candidates if snapshot_id(row).startswith(requested)]
        require(len(candidates) == 1, "snapshot ID missing or ambiguous for requested host/tag")
        return candidates[0]
    require(newest and candidates, "no snapshot for requested host/tag")
    def order(row):
        when = datetime.datetime.fromisoformat(row["time"].replace("Z", "+00:00"))
        require(when.tzinfo is not None, "snapshot timestamp lacks timezone")
        return when, snapshot_id(row)
    return max(candidates, key=order)


def private_target(requested, force=False, temp_root="/tmp"):
    """Create a new private directory; never follow symlinks or reuse paths."""
    root = Path(temp_root)
    if not requested:
        info = root.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, os.geteuid())
                and (not info.st_mode & 0o022 or info.st_mode & stat.S_ISVTX),
                "unsafe temporary root")
        return Path(tempfile.mkdtemp(prefix="vff-restore-", dir=root))
    require(requested.startswith("/") and requested != "/" and
            all(part not in ("", ".", "..") for part in requested.split("/")[1:]),
            "restore target must be canonical absolute path")
    target = Path(requested)
    require(force or target.parent == root, "restore outside direct temporary child requires force")
    # Every parent must be a stable, root/effective-user controlled directory.
    # A system temp root is allowed to be sticky and writable by other users.
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parent = Path("/")
        for part in target.parent.parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
            parent /= part
            info = os.fstat(fd)
            sticky_temp = parent == Path("/tmp") and info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
            require(info.st_uid in (0, os.geteuid()) and
                    (not info.st_mode & 0o022 or sticky_temp),
                    "restore parent not privately controlled")
        require(target.parent.resolve(strict=True) == target.parent,
                "noncanonical restore parent")
        os.mkdir(target.name, mode=0o700, dir_fd=fd)
    finally:
        os.close(fd)
    return target


def restic(args):
    completed = subprocess.run(["restic", *args], check=False, capture_output=True,
                               text=True)
    require(completed.returncode == 0, "restic command failed")
    return completed.stdout


def execute(options, runner=restic):
    host = options["host"]
    tag = options["tag"]
    requested = options["snapshot"]
    require(isinstance(host, str) and host.strip(), "explicit snapshot host required")
    require(isinstance(tag, str) and isinstance(requested, str), "invalid snapshot filters")
    filters = ["--host", host] + (["--tag", tag] if tag else [])
    row = select_snapshot(json.loads(runner(["snapshots", "--json", *filters])),
                          host, tag, requested, options["newest"])
    ident = snapshot_id(row)
    require(type(options["force"]) is bool and type(options["newest"]) is bool
            and isinstance(options["target"], str), "invalid restore controls")
    flags = []
    for key, flag in (("includes", "--include"), ("excludes", "--exclude")):
        values = options[key]
        require(isinstance(values, list) and all(isinstance(x, str) for x in values),
                "invalid restore filters")
        for value in values:
            flags.extend([flag, value])
    # Keep an outer 0700 guard even when Restic restores permissive directory
    # permissions from the archive. Never restore directly into live paths.
    target = private_target(options["target"], options["force"])
    data = target / "data"
    data.mkdir(mode=0o700)
    runner(["restore", ident, "--target", str(data), *flags])
    return {"snapshot": ident, "host": host, "target": str(data),
            "paths": row.get("paths", [])}


def main():
    try:
        options = json.load(sys.stdin)
        result = execute(options)
        print(json.dumps(result))
    except (ValueError, OSError, TypeError, KeyError, RuntimeError):
        print("Restore refused or failed; verify explicit source and private target.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
