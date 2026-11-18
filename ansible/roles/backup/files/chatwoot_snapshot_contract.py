#!/usr/bin/env python3
"""Offline validation of an already-captured Chatwoot staging generation.

This module does not access Docker, PostgreSQL, Redis, credentials, or S3.
The capture adapter must supply independently queried evidence while all
writers are quiescent; this module cannot establish that fact by itself.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys

from verify_prepared_snapshot import MANIFEST, check_private, digest, unique, verify

TABLES = frozenset(("accounts", "inboxes", "contacts", "conversations",
                    "messages", "attachments", "active_storage_blobs",
                    "active_storage_attachments"))
MIN_CONFIG = frozenset((".env", "compose.yaml",
                        "compose.operator-privacy.override.yaml",
                        "telegram-poller/poller.rb"))
SERVICES = frozenset(("rails", "sidekiq", "telegram-business-poller",
                      "postgres", "redis"))
REQUIRED_WRITERS = frozenset(("rails", "sidekiq", "telegram-business-poller"))
OPTIONAL_WRITERS = frozenset(("telegram-poller", "web-drafter", "gemma-drafter"))
EVIDENCE = "capture-evidence.json"
MAX_EVIDENCE = 32 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def integer(value):
    return type(value) is int and value >= 0


def reject_walk_error(error):
    raise error


def inventory(root):
    """Check the entire private tree before opening its files."""
    check_private(root, directory=True)
    result = {}
    for parent, dirs, names in os.walk(root, followlinks=False,
                                        onerror=reject_walk_error):
        for name in dirs:
            check_private(Path(parent) / name, directory=True)
        for name in names:
            path = Path(parent) / name
            check_private(path)
            result[path.relative_to(root).as_posix()] = path
    return result


def safe_relative(value):
    if not isinstance(value, str):
        return False
    path = PurePosixPath(value)
    return (not path.is_absolute() and bool(path.parts)
            and value == path.as_posix()
            and all(re.fullmatch(r"[A-Za-z0-9_.-]+", part)
                    and part not in (".", "..") for part in path.parts))


def validate_counts(evidence):
    counts = evidence["counts"]
    require(isinstance(counts, dict) and set(counts) == TABLES
            and all(integer(value) for value in counts.values()),
            "invalid table count evidence")
    require(counts["accounts"] >= 1, "empty account inventory")
    restored = evidence["dump_counts"]
    require(isinstance(restored, dict) and set(restored) == TABLES
            and all(integer(value) for value in restored.values())
            and counts == restored, "dump/source counts differ")
    require(not counts["active_storage_attachments"] or counts["active_storage_blobs"] > 0,
            "attachments without persisted blobs")
    return counts


def validate_writer_state(evidence):
    require(evidence["ingress_fenced"] is True, "ingress not fenced")
    require(integer(evidence["other_active_writers"])
            and evidence["other_active_writers"] == 0, "unclassified active writer")
    states = evidence["writer_states"]
    require(isinstance(states, dict)
            and set(states) == REQUIRED_WRITERS | OPTIONAL_WRITERS,
            "incomplete writer inventory")
    require(all(states[name] == "stopped" for name in REQUIRED_WRITERS)
            and all(states[name] in ("stopped", "absent") for name in OPTIONAL_WRITERS),
            "writer not quiescent")


def validate_config(evidence, files):
    config = evidence["config"]
    require(isinstance(config, list) and all(safe_relative(x) for x in config)
            and len(set(config)) == len(config)
            and MIN_CONFIG <= set(config), "invalid configuration inventory")
    actual = {name.removeprefix("config/") for name in files
              if name.startswith("config/")}
    require(actual == set(config), "configuration inventory mismatch")
    require(all(files["config/" + name].stat().st_size > 0 for name in config),
            "empty required configuration")
    images = evidence["images"]
    require(isinstance(images, dict) and set(images) == SERVICES
            and all(isinstance(value, str) and
                    re.fullmatch(r"sha256:[0-9a-f]{64}", value)
                    for value in images.values()), "invalid image inventory")


def validate_checkpoints(evidence, files):
    offsets = evidence["business_offsets"]
    require(isinstance(offsets, dict) and bool(offsets), "missing Business state")
    expected = set()
    for channel, offset in offsets.items():
        require(isinstance(channel, str) and re.fullmatch(r"[1-9][0-9]*", channel)
                and integer(offset), "invalid checkpoint identity or value")
        name = f"business-state/channel-{channel}.offset"
        expected.add(name)
        require(name in files, "checkpoint missing")
        require(files[name].stat().st_size <= 64 and
                files[name].read_bytes() == (str(offset) + "\n").encode(),
                "checkpoint mismatch")
    require(expected == {name for name in files if name.startswith("business-state/")},
            "unexpected checkpoint file")


def validate_blobs(evidence, counts, files):
    blobs = evidence["blobs"]
    require(isinstance(blobs, list) and len(blobs) == counts["active_storage_blobs"],
            "incomplete blob inventory")
    seen = set()
    for blob in blobs:
        require(isinstance(blob, dict) and set(blob) == {"key", "byte_size", "checksum"},
                "invalid blob metadata")
        key, size, checksum = blob["key"], blob["byte_size"], blob["checksum"]
        require(isinstance(key, str) and re.fullmatch(r"[A-Za-z0-9]{20,128}", key)
                and key not in seen and integer(size)
                and isinstance(checksum, str), "invalid or duplicate blob")
        seen.add(key)
        name = f"storage/{key[:2]}/{key[2:4]}/{key}"
        require(name in files and files[name].stat().st_size == size,
                "blob missing or size mismatch")
        md = hashlib.md5(usedforsecurity=False)
        with files[name].open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                md.update(chunk)
        require(base64.b64encode(md.digest()).decode("ascii") == checksum,
                "blob checksum mismatch")


def validate_generation(root):
    root = Path(root)
    files = inventory(root)
    require(EVIDENCE in files and files[EVIDENCE].stat().st_size <= MAX_EVIDENCE,
            "missing or oversized capture evidence")
    evidence = json.loads(files[EVIDENCE].read_text(), object_pairs_hook=unique)
    require(isinstance(evidence, dict) and set(evidence) == {
        "version", "counts", "dump_counts", "config", "images", "business_offsets",
        "blobs", "unresolved_attachment_refs", "pending_delivery_jobs",
        "ingress_fenced", "writer_states", "other_active_writers"},
        "invalid capture evidence")
    require(type(evidence["version"]) is int and evidence["version"] == 1,
            "unsupported evidence version")
    require(integer(evidence["unresolved_attachment_refs"])
            and evidence["unresolved_attachment_refs"] == 0,
            "unresolved DB attachment references")
    require(integer(evidence["pending_delivery_jobs"])
            and evidence["pending_delivery_jobs"] == 0, "pending delivery jobs")
    require("chatwoot.dump" in files, "missing database dump")
    with files["chatwoot.dump"].open("rb") as stream:
        require(stream.read(5) == b"PGDMP" and files["chatwoot.dump"].stat().st_size > 5,
                "invalid custom dump header")
    require(all(name in (EVIDENCE, "chatwoot.dump", MANIFEST)
                or name.startswith(("config/", "storage/", "business-state/"))
                for name in files), "unexpected top-level data")
    validate_writer_state(evidence)
    counts = validate_counts(evidence)
    validate_config(evidence, files)
    validate_checkpoints(evidence, files)
    validate_blobs(evidence, counts, files)
    return files


def seal(root):
    """Publish the existing Restic-role manifest format, never overwrite it."""
    root = Path(root)
    files = validate_generation(root)
    require(MANIFEST not in files, "generation already sealed")
    manifest = {"version": 1, "files": {
        name: digest(path) for name, path in sorted(files.items())}}
    payload = json.dumps(manifest, sort_keys=True).encode()
    require(len(payload) <= 32 * 1024 * 1024, "manifest too large")
    target = root / MANIFEST
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
        return verify(root)
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def verify_restored(root):
    """Validate bytes first, then Chatwoot's persisted artifact contract."""
    count = verify(Path(root))
    validate_generation(Path(root))
    return count


def main():
    try:
        if len(sys.argv) != 2:
            raise ValueError("one generation required")
        count = verify_restored(Path(sys.argv[1]))
        print(json.dumps({"status": "PASS_CHATWOOT_GENERATION", "files": count}))
        return 0
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        print('{"status":"FAIL_CHATWOOT_GENERATION"}', file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
