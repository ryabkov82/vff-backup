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
POLLER_CONFIG = "telegram-poller/poller.rb"
MIN_CONFIG = frozenset((".env", "compose.yaml",
                        "compose.operator-privacy.override.yaml",
                        POLLER_CONFIG))
SERVICES = frozenset(("rails", "sidekiq", "telegram-business-poller",
                      "postgres", "redis"))
REQUIRED_WRITERS = frozenset(("rails", "sidekiq", "telegram-business-poller"))
OPTIONAL_WRITERS = frozenset(("telegram-poller", "web-drafter", "gemma-drafter"))
EVIDENCE = "capture-evidence.json"
MAX_EVIDENCE = 32 * 1024 * 1024
CONFIG_PREFIX = "config/"
STORAGE_PREFIX = "storage/"
SHA256_PATTERN = r"[0-9a-f]{64}"
CHATWOOT_DUMP = "chatwoot.dump"


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
    actual = {name.removeprefix(CONFIG_PREFIX) for name in files
              if name.startswith(CONFIG_PREFIX)}
    require(actual == set(config), "configuration inventory mismatch")
    require(all(files[CONFIG_PREFIX + name].stat().st_size > 0 for name in config),
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
        require(isinstance(channel, str) and re.fullmatch(r"[1-9]\d*", channel, flags=re.ASCII)
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
    require(CHATWOOT_DUMP in files, "missing database dump")
    with files[CHATWOOT_DUMP].open("rb") as stream:
        require(stream.read(5) == b"PGDMP" and files[CHATWOOT_DUMP].stat().st_size > 5,
                "invalid custom dump header")
    require(all(name in (EVIDENCE, CHATWOOT_DUMP, MANIFEST)
                or name.startswith((CONFIG_PREFIX, STORAGE_PREFIX, "business-state/"))
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


# The old v1 contract remains available for explicit offline structural
# review only. A scheduled Chatwoot job must use the tested capture adapter,
# whose full-stack receipt is sealed together with the exact data generation.
V2_RECEIPT = "capture-receipt.json"
V2_REQUIRED = frozenset((
    "source/chatwoot.dump", "source/shm.sql", "source/remna.dump",
    "source/chatwoot.env", "source/backends.env", "source/portal-config.json",
    "source/portal-image.json", "source/test3-scope.env",
    "source/test3-connection.env", "source/test3-checkpoint/channel-1.offset",
    "source/config/compose.operator.yaml", "source/config/compose.functional.yaml",
    "source/config/compose.backends205.yaml", "source/image-inventory.json",
    "source/blob-inventory.json", "source/delivery-fence.json", "source/link/link.env", "source/link/verify",
    "source/link/ca.crt", "source/link/server.crt", "source/link/server.key",
    "source/link/ca.key", "source/link/nginx.conf", V2_RECEIPT))
V2_SERVICES = frozenset((
    "rails", "sidekiq", "telegram-business-test3", "postgres", "redis",
    "shm-mysql", "shm-core", "shm-api", "remna-postgres", "remna-valkey",
    "remna", "vpnbot-webtest", "shm-link-tls"))

# IV-06: v2 is explicitly the VM205 fixture profile, not a PROD contract.
# Exact configuration and recovery artifacts observed in the accepted
# native/remote snapshot. Data-volume patterns are scoped below.
V2_CONFIG_FILES = frozenset((
    "compose.operator.yaml", "compose.functional.yaml", "compose.backends205.yaml",
    "compose.functional.business-test3.yaml",
    "compose.functional.business-test3-contact-link.override.yaml",
    "compose.functional.business-test3-contact-link-live-test.override.yaml",
    "telegram-poller/encryption_preflight.rb", POLLER_CONFIG,
    "ops/operator_delivery_error_privacy.rb", "ops/operator_telegram_delivery_guard.rb",
    "ops/verify_fixture_vm205.rb", "ops/test_vm205_full_restore_e2e.py",
    "ops/test_vm205_link_restore.py", "ops/test_vm205_restore_e2e.py",
    "ops/test_vm205_prepared_backup.py", "ops/test_vm205_sidekiq_class_inventory.rb",
    "web-drafter/contact_linker.rb", "shm-tls/vm205-link.conf"))
V2_REQUIRED = V2_REQUIRED | frozenset(
    "source/config/" + name for name in V2_CONFIG_FILES) | frozenset((
    "source/link/contact_linker.rb", "source/link/linked-test-fixture.json",
    "source/link/overlay.yaml", "source/link/shm-readonly.json",
    "source/storage/vff-restore-205-canary", "source/test3-checkpoint/singleton.lock"))
V2_DATA_DIRS = frozenset((
    "source/shm_data", "source/mails", "source/storage",
    "source/redis", "source/redis/appendonlydir", "source/remna_redis",
    "source/remna_redis/appendonlydir", "source/test3-checkpoint"))
V2_CHECKPOINT_OVERLAY = "source/config/compose.functional.business-test3-checkpoint.override.yaml"
V2_OPTIONAL = frozenset((
    "source/redis/dump.rdb", "source/remna_redis/dump.rdb", V2_CHECKPOINT_OVERLAY))
V2_DATA_PATTERNS = (
    # Only local synthetic SMTP sink messages (not arbitrary mail/log trees).
    re.compile(r"source/mails/\d{19}-[0-9a-f]{24}\.eml", re.ASCII),
    # Redis/Valkey native persisted data, not generic source subdirectories.
    re.compile(r"source/(?:redis|remna_redis)/appendonlydir/appendonly\.aof\.(?:manifest|\d+\.(?:base\.(?:rdb|aof)|incr\.aof))", re.ASCII),
    # VM205's explicitly scoped synthetic send idempotency marker.
    re.compile(r"source/test3-checkpoint/vff-bizpair-test-send-205-[0-9a-f]{16}\.state"),
)


def v2_blob_paths(files, counts):
    inventory_path = files["source/blob-inventory.json"]
    require(inventory_path.stat().st_size <= MAX_EVIDENCE, "oversized blob inventory")
    blobs = json.loads(inventory_path.read_text(), object_pairs_hook=unique)
    # Check the DB-derived blob inventory, sizes and checksums. Only these
    # blob files (plus the explicit TEST canary) may enter the archive.
    storage = {name.removeprefix("source/"): path for name, path in files.items()
               if name.startswith("source/storage/")}
    validate_blobs({"blobs": blobs}, counts, storage)
    return {"source/storage/" + row["key"][:2] + "/" + row["key"][2:4] +
            "/" + row["key"] for row in blobs}


def validate_v2_inventory(root, files, counts):
    fixed = V2_REQUIRED | V2_OPTIONAL | {MANIFEST}
    blobs = v2_blob_paths(files, counts)
    if V2_CHECKPOINT_OVERLAY in files:
        require(files[V2_CHECKPOINT_OVERLAY].read_text() == (
            '# VM205 TEST3 only; migrate the checkpoint before activation.\n'
            'services:\n  telegram-business-test3:\n    environment:\n'
            '      VFF_TELEGRAM_CHECKPOINT_BACKEND: "postgres"\n'),
            "invalid v2 checkpoint overlay")
        exported = files["source/test3-checkpoint/channel-1.offset"].read_text()
        require(re.fullmatch(r"(?:0|[1-9][0-9]{0,18})\n", exported) and int(exported) < 2**63,
                "invalid v2 PostgreSQL checkpoint export")
    require(all(name in fixed or name in blobs or
                any(pattern.fullmatch(name) for pattern in V2_DATA_PATTERNS)
                for name in files), "undeclared v2 capture artifact")
    checkpoints = {name for name in files if name.startswith("source/test3-checkpoint/")}
    required_checkpoints = {"source/test3-checkpoint/channel-1.offset",
                            "source/test3-checkpoint/singleton.lock"}
    require(required_checkpoints <= checkpoints and len(checkpoints - required_checkpoints) <= 1,
            "invalid v2 checkpoint inventory")
    allowed_dirs = set(V2_DATA_DIRS)
    for name in files:
        allowed_dirs.update(parent.as_posix() for parent in PurePosixPath(name).parents
                            if parent != PurePosixPath("."))
    for parent, dirs, _ in os.walk(root, followlinks=False, onerror=reject_walk_error):
        for name in dirs:
            require((Path(parent) / name).relative_to(root).as_posix() in allowed_dirs,
                    "undeclared v2 capture directory")



def validate_delivery_fence(path):
    """Require independently collected DB and Redis fence evidence on both sides."""
    require(path.stat().st_size <= 4096, "oversized delivery fence")
    state = json.loads(path.read_text(), object_pairs_hook=unique)
    require(isinstance(state, dict) and set(state) == {
        "version", "source_db", "restored_db", "source_redis", "restored_redis",
        "scratch_reservation_rejected"
    } and type(state["version"]) is int and state["version"] == 1
        and state["scratch_reservation_rejected"] is True,
        "invalid delivery fence")
    db_keys = {"channels", "scoped_inboxes", "outgoing", "unconfirmed", "failed",
               "invalid_status", "invalid_ack", "reservations"}
    source, restored = state["source_db"], state["restored_db"]
    require(all(isinstance(row, dict) and set(row) == db_keys and
                all(integer(n) for n in row.values()) for row in (source, restored)),
            "invalid DB delivery inventory")
    require(source == restored and source["channels"] == source["scoped_inboxes"] == 1
            and all(source[k] == 0 for k in db_keys -
                    {"channels", "scoped_inboxes", "outgoing"}),
            "pending or mismatched DB delivery")
    for key in ("source_redis", "restored_redis"):
        counts = state[key]
        require(isinstance(counts, dict) and set(counts) == {"queued", "working", "retry"}
                and all(type(n) is int and n == 0 for n in counts.values()),
                "pending Redis delivery")


def verify_prepared_capture(root):
    """Verify integrity and mandatory trusted-adapter evidence, not a DB engine."""
    root = Path(root)
    count = verify(root)
    files = inventory(root)
    require(V2_REQUIRED <= set(files), "missing verified capture artifacts")
    validate_delivery_fence(files["source/delivery-fence.json"])
    receipt = json.loads(files[V2_RECEIPT].read_text(), object_pairs_hook=unique)
    require(isinstance(receipt, dict) and set(receipt) == {
        "version", "scope", "status", "source_counts", "restored_counts",
        "source_blobs", "restored_blobs", "pending_delivery", "writers_resumed",
        "external_delivery", "restored_test3_poller_started", "restore_checks"
    }, "invalid capture receipt")
    require(type(receipt["version"]) is int and receipt["version"] == 2
            and receipt["scope"] == "VM205_FUNCTIONAL_TEST"
            and receipt["status"] == "PASS_VM205_PREPARED_RESTORE",
            "unaccepted capture")
    a, b = receipt["source_counts"], receipt["restored_counts"]
    require(isinstance(a, dict) and isinstance(b, dict) and set(a) == set(b) == TABLES
            and all(integer(v) for v in (*a.values(), *b.values())) and a == b
            and a["accounts"] >= 1, "restore count mismatch")
    validate_v2_inventory(root, files, a)
    require(integer(receipt["source_blobs"]) and integer(receipt["restored_blobs"])
            and receipt["source_blobs"] == receipt["restored_blobs"] == a["active_storage_blobs"],
            "restore blob mismatch")
    require(receipt["pending_delivery"] is False and receipt["writers_resumed"] is True
            and receipt["external_delivery"] is False
            and receipt["restored_test3_poller_started"] is False,
            "unsafe capture or restore")
    checks = receipt["restore_checks"]
    require(isinstance(checks, dict) and set(checks) == {
        "postgres", "mysql", "remnawave", "website", "chatwoot",
        "link_tls", "images", "blob_integrity", "checkpoint", "scratch_cleanup"}
            and all(v is True for v in checks.values()), "restore checks incomplete")
    images = json.loads(files["source/image-inventory.json"].read_text(), object_pairs_hook=unique)
    require(isinstance(images, dict) and set(images) == V2_SERVICES, "incomplete images")
    for value in images.values():
        require(isinstance(value, dict) and set(value) == {"image", "id"}
                and isinstance(value["image"], str) and value["image"]
                and isinstance(value["id"], str)
                and re.fullmatch(r"sha256:[0-9a-f]{64}", value["id"]),
                "invalid captured image")
    # An archive header cannot certify restoration. The real adapter must
    # actually restore it; this checks the sealed receipt and archive shape.
    for name in ("source/chatwoot.dump", "source/remna.dump"):
        with files[name].open("rb") as stream:
            require(stream.read(5) == b"PGDMP" and files[name].stat().st_size > 128,
                    "invalid dump")
    return count


# Version 3 is the standalone operator component. It is not the full VM205
# functional stack, and may never be selected implicitly by the v2 job.
OPERATOR_SCOPES = frozenset(("OPERATOR_COMPATIBILITY_TEST", "OPERATOR_PROD"))
OPERATOR_PROFILE = "operator-profile.json"
OPERATOR_FENCE = "delivery-fence.json"
OPERATOR_CONFIG = frozenset((
    "compose.operator.yaml", ".env.operator", ".env.operator.business",
    ".env.operator.link", "telegram-poller/encryption_preflight.rb",
    POLLER_CONFIG, "web-drafter/contact_linker.rb",
    "ops/operator_delivery_error_privacy.rb", "ops/operator_telegram_delivery_guard.rb",
    "bin/chatwoot-link-verify", "bin/shm-operator-ca.crt"))
OPERATOR_LIVE = "compose.operator-contact-link-live.override.yaml"
OPERATOR_TEST = "compose.operator-recovery-test.override.yaml"
OPERATOR_SMTP = ".env.operator.smtp"
OPERATOR_BOOTSTRAP = "compose.bootstrap.yaml"
# Explicit operational extensions; no arbitrary environment/Compose files.
OPERATOR_CONFIG_EXTENSIONS = frozenset((OPERATOR_SMTP, OPERATOR_BOOTSTRAP))
OPERATOR_CHECKS = frozenset(("postgres", "redis", "blob_integrity", "checkpoint",
                            "native_encryption", "native_hmac", "delivery_fence",
                            "isolated_network", "scratch_cleanup"))
OPERATOR_IMAGES = {
    **dict.fromkeys(REQUIRED_WRITERS, "chatwoot/chatwoot@sha256:04895c7bd0550d9e74b45b7202787b56f7ee4e891ebbde0c429c8c32901b97cf"),
    "postgres": "pgvector/pgvector@sha256:0dd3149a06e4579a0d70d8fef9945636cd514005e7237deb27f9b67886f8dc4f",
    "redis": "redis@sha256:c5d34a98396a4775e223c74e929162c35c5a2182901ba26b50ade939941ce14b",
}


def validate_operator_profile(profile):
    require(isinstance(profile, dict) and set(profile) == {
        "version", "scope", "source_host", "project", "config_root", "stage_root",
        "account_id", "website_inbox_id", "business_inbox_id", "channel_id",
        "bot_name", "contact_link_mode", "config_sha256", "images"},
        "invalid operator profile")
    require(type(profile["version"]) is int and profile["version"] == 3
            and profile["scope"] in OPERATOR_SCOPES, "unsupported operator scope")
    for key in ("source_host", "project", "bot_name"):
        require(isinstance(profile[key], str) and
                re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", profile[key]),
                "invalid operator identity")
    for key in ("config_root", "stage_root"):
        value = profile[key]
        require(isinstance(value, str) and value.startswith("/")
                and safe_relative(value[1:]), "invalid operator path")
    require(all(integer(profile[key]) and profile[key] > 0 for key in
                ("account_id", "website_inbox_id", "business_inbox_id", "channel_id"))
            and profile["website_inbox_id"] != profile["business_inbox_id"],
            "invalid operator inbox identity")
    require(profile["contact_link_mode"] in ("dry-run", "live"), "invalid operator linking mode")
    expected = set(OPERATOR_CONFIG)
    if profile["contact_link_mode"] == "live":
        expected.add(OPERATOR_LIVE)
    if profile["scope"] == "OPERATOR_COMPATIBILITY_TEST":
        expected.add(OPERATOR_TEST)
    config = profile["config_sha256"]
    require(isinstance(config, dict), "invalid operator configuration inventory")
    expected.update(OPERATOR_CONFIG_EXTENSIONS.intersection(config))
    require(set(config) == expected
            and all(isinstance(v, str) and re.fullmatch(SHA256_PATTERN, v)
                    for v in config.values()), "invalid operator configuration inventory")
    require(profile["images"] == OPERATOR_IMAGES, "operator image profile mismatch")
    return profile


def operator_file_inventory(profile, evidence, files, counts):
    config = profile["config_sha256"]
    for name, checksum in config.items():
        path = files.get(CONFIG_PREFIX + name)
        require(path is not None and path.stat().st_size > 0 and digest(path) == checksum,
                "operator configuration mismatch")
    validate_checkpoints(evidence, files)
    require(set(evidence["business_offsets"]) == {str(profile["channel_id"])},
            "operator checkpoint scope mismatch")
    validate_blobs(evidence, counts, files)
    blobs = {STORAGE_PREFIX + row["key"][:2] + "/" + row["key"][2:4] + "/" + row["key"]
             for row in evidence["blobs"]}
    expected = {MANIFEST, OPERATOR_PROFILE, OPERATOR_FENCE, EVIDENCE, CHATWOOT_DUMP, "redis.rdb"}
    expected.update(CONFIG_PREFIX + name for name in config)
    expected.update("business-state/channel-" + key + ".offset"
                    for key in evidence["business_offsets"])
    require(set(files) == expected | blobs, "undeclared operator artifact")
    for name, header in ((CHATWOOT_DUMP, b"PGDMP"), ("redis.rdb", b"REDIS")):
        with files[name].open("rb") as stream:
            require(stream.read(len(header)) == header and files[name].stat().st_size > 9,
                    "invalid native operator archive")


def verify_operator_capture(root, expected_sha, expected_scope):
    """Only an explicitly pinned v3 profile can authorize this verifier."""
    require(expected_scope in OPERATOR_SCOPES and
            re.fullmatch(SHA256_PATTERN, expected_sha), "operator profile pin required")
    root = Path(root)
    count = verify(root)
    files = inventory(root)
    profile_file = files[OPERATOR_PROFILE]
    require(profile_file.stat().st_size <= 65536 and digest(profile_file) == expected_sha,
            "operator profile hash mismatch")
    profile = validate_operator_profile(json.loads(profile_file.read_text(), object_pairs_hook=unique))
    require(profile["scope"] == expected_scope, "operator scope mismatch")
    require(files[EVIDENCE].stat().st_size <= MAX_EVIDENCE, "operator evidence too large")
    evidence = json.loads(files[EVIDENCE].read_text(), object_pairs_hook=unique)
    require(isinstance(evidence, dict) and set(evidence) == {
        "version", "scope", "status", "profile_sha256", "counts", "dump_counts",
        "business_offsets", "blobs", "unresolved_attachment_refs", "pending_delivery_jobs",
        "ingress_fenced", "writer_states", "other_active_writers", "restore_checks",
        "restored_outbound_started", "source_writers_left_stopped"},
        "invalid operator capture evidence")
    require(type(evidence["version"]) is int and evidence["version"] == 3
            and evidence["scope"] == expected_scope
            and evidence["status"] == "PASS_OPERATOR_NATIVE_RESTORE"
            and evidence["profile_sha256"] == expected_sha, "operator receipt mismatch")
    validate_writer_state(evidence)
    require(evidence["restored_outbound_started"] is False
            and evidence["source_writers_left_stopped"] is True,
            "unsafe operator lifecycle")
    require(type(evidence["unresolved_attachment_refs"]) is int
            and evidence["unresolved_attachment_refs"] == 0
            and type(evidence["pending_delivery_jobs"]) is int
            and evidence["pending_delivery_jobs"] == 0, "operator data not quiescent")
    checks = evidence["restore_checks"]
    require(isinstance(checks, dict) and set(checks) == OPERATOR_CHECKS
            and all(v is True for v in checks.values()), "operator restore incomplete")
    counts = validate_counts(evidence)
    operator_file_inventory(profile, evidence, files, counts)
    allowed_dirs = {"storage"} | {parent.as_posix() for name in files
        for parent in PurePosixPath(name).parents if parent != PurePosixPath(".")}
    for parent, dirs, _ in os.walk(root, followlinks=False, onerror=reject_walk_error):
        for name in dirs:
            require((Path(parent) / name).relative_to(root).as_posix() in allowed_dirs,
                    "undeclared operator directory")
    validate_operator_delivery_fence(files[OPERATOR_FENCE])
    return count


def validate_operator_delivery_fence(path):
    require(path.stat().st_size <= 4096, "oversized operator delivery fence")
    state = json.loads(path.read_text(), object_pairs_hook=unique)
    require(isinstance(state, dict) and set(state) == {
        "source_db", "restored_db", "source_redis", "restored_redis"},
        "invalid operator delivery fence")
    keys = {"channels", "scoped_inboxes", "outgoing", "unconfirmed", "failed",
            "invalid_status", "invalid_ack", "reservations"}
    source, restored = state["source_db"], state["restored_db"]
    require(all(isinstance(row, dict) and set(row) == keys and
                all(integer(n) for n in row.values()) for row in (source, restored))
            and source == restored
            and source["channels"] == source["scoped_inboxes"] == 1
            and all(source[k] == 0 for k in keys - {"channels", "scoped_inboxes", "outgoing"}),
            "pending or mismatched operator delivery")
    redis = {"queued", "working", "retry", "scheduled", "maintenance_scheduled", "dead", "processes"}
    for name in ("source_redis", "restored_redis"):
        value = state[name]
        require(isinstance(value, dict) and set(value) == redis
                and all(integer(n) for n in value.values())
                and all(value[k] == 0 for k in redis - {"maintenance_scheduled"}),
                "pending operator Redis delivery")
    require(state["source_redis"] == state["restored_redis"],
            "operator Redis maintenance inventory drift")



ONLINE_POLICY = {
    "redis": "EMPTY_ON_RESTORE",
    "checkpoint": "OBSERVATION_ONLY",
    "external_delivery": "ISOLATED_UNTIL_OPERATOR_RECONCILIATION",
    "ambiguous_telegram_sends": "QUARANTINE_NO_RETRY",
}
CHECKPOINT_POLICY = {**ONLINE_POLICY, "checkpoint": "POSTGRES_RECONCILE_BEFORE_ACTIVATION"}
CHECKPOINT_BACKEND_KEY = "VFF_TELEGRAM_CHECKPOINT_BACKEND"
CHECKPOINT_CONNECTION_KEY = "VFF_TELEGRAM_BUSINESS_CONNECTION_ID"
CHECKPOINT_ENV = ".env.operator.business"
MAX_CHECKPOINT_ID = 2**63 - 1


def operator_checkpoint_config(config, profile):
    """Read only the two scoped declarations, never return credentials."""
    selected = {}
    for name in (".env.operator", CHECKPOINT_ENV, ".env.operator.link"):
        for line in (config / name).read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and key in (CHECKPOINT_BACKEND_KEY, CHECKPOINT_CONNECTION_KEY):
                require(name == CHECKPOINT_ENV and key not in selected,
                        "ambiguous checkpoint configuration")
                selected[key] = value
    backend = selected.get(CHECKPOINT_BACKEND_KEY, "file")
    require(backend in ("file", "postgres"), "invalid checkpoint backend")
    if backend == "file":
        return backend, None
    connection = selected.get(CHECKPOINT_CONNECTION_KEY, "")
    require(re.fullmatch(r"[!-~]{1,256}", connection), "invalid checkpoint connection scope")
    scope = [profile["channel_id"], profile["account_id"], profile["business_inbox_id"],
             profile["bot_name"].lower(), connection]
    fingerprint = hashlib.sha256(json.dumps(scope, separators=(",", ":"),
                                           ensure_ascii=False).encode()).hexdigest()
    return backend, fingerprint


def validate_online_checkpoint(checkpoint, profile, fingerprint):
    require(isinstance(checkpoint, dict) and set(checkpoint) == {
        "channel_id", "scope_sha256", "next_offset"}, "invalid checkpoint inventory")
    require(type(checkpoint["channel_id"]) is int and checkpoint["channel_id"] == profile["channel_id"]
            and checkpoint["scope_sha256"] == fingerprint
            and integer(checkpoint["next_offset"]) and checkpoint["next_offset"] <= MAX_CHECKPOINT_ID,
            "invalid checkpoint scope/cursor")

ONLINE_DELIVERY_KEYS = frozenset(("channels", "scoped_inboxes", "outgoing", "unconfirmed",
                                  "failed", "invalid_status", "invalid_ack", "reservations"))


def validate_online_delivery(state):
    require(isinstance(state, dict) and set(state) == ONLINE_DELIVERY_KEYS
            and all(integer(n) for n in state.values())
            and state["channels"] == state["scoped_inboxes"] == 1
            and all(state[k] <= state["outgoing"]
                    for k in ONLINE_DELIVERY_KEYS - {"channels", "scoped_inboxes", "outgoing"}),
            "invalid online delivery inventory")


def validate_online_evidence(root, evidence, profile, expected_sha, expected_scope):
    backend, fingerprint = operator_checkpoint_config(root / "config", profile)
    postgres_mode = backend == "postgres"
    checkpoint_field = "checkpoint" if postgres_mode else "checkpoint_observations"
    require(isinstance(evidence, dict) and set(evidence) == {
        "version", "mode", "scope", "status", "profile_sha256", "counts", "blobs",
        "snapshot_epoch_seconds", checkpoint_field, "delivery",
        "unresolved_attachment_refs", "source_writers_running", "recovery_policy"},
        "invalid online evidence")
    require(type(evidence["version"]) is int and evidence["version"] == (2 if postgres_mode else 1)
            and evidence["mode"] == "operator-online-v1"
            and evidence["scope"] == expected_scope
            and evidence["status"] == "PASS_OPERATOR_ONLINE_CAPTURE"
            and evidence["profile_sha256"] == expected_sha
            and evidence["source_writers_running"] is True
            and evidence["recovery_policy"] == (CHECKPOINT_POLICY if postgres_mode else ONLINE_POLICY)
            and integer(evidence["snapshot_epoch_seconds"])
            and evidence["snapshot_epoch_seconds"] > 0
            and integer(evidence["unresolved_attachment_refs"])
            and evidence["unresolved_attachment_refs"] == 0, "online receipt mismatch")
    counts = evidence["counts"]
    require(isinstance(counts, dict) and set(counts) == TABLES
            and all(integer(n) for n in counts.values())
            and counts["accounts"] >= 1
            and (not counts["active_storage_attachments"] or counts["active_storage_blobs"] > 0),
            "invalid online table inventory")
    if postgres_mode:
        validate_online_checkpoint(evidence["checkpoint"], profile, fingerprint)
    else:
        observations = evidence["checkpoint_observations"]
        require(isinstance(observations, dict) and set(observations) == {
            "channel", "before_db_snapshot", "after_copy"}
                and observations["channel"] == str(profile["channel_id"])
                and all(integer(observations[k]) and observations[k] < 10**19
                        for k in ("before_db_snapshot", "after_copy")),
                "invalid diagnostic checkpoint observation")
    validate_online_delivery(evidence["delivery"])
    return counts


def verify_operator_online_capture(root, expected_sha, expected_scope):
    """Verify a DB-snapshot/attachment-closed archive, not a quiesced v3 archive."""
    require(expected_scope in OPERATOR_SCOPES and
            re.fullmatch(SHA256_PATTERN, expected_sha), "online profile pin required")
    root = Path(root)
    count = verify(root)
    files = inventory(root)
    profile_file = files[OPERATOR_PROFILE]
    require(profile_file.stat().st_size <= 65536 and digest(profile_file) == expected_sha,
            "online profile hash mismatch")
    profile = validate_operator_profile(json.loads(profile_file.read_text(), object_pairs_hook=unique))
    require(profile["scope"] == expected_scope, "online scope mismatch")
    require(files[EVIDENCE].stat().st_size <= MAX_EVIDENCE, "online evidence too large")
    evidence = json.loads(files[EVIDENCE].read_text(), object_pairs_hook=unique)
    counts = validate_online_evidence(root, evidence, profile, expected_sha, expected_scope)
    for name, checksum in profile["config_sha256"].items():
        file = files.get(CONFIG_PREFIX + name)
        require(file is not None and file.stat().st_size > 0 and digest(file) == checksum,
                "online configuration mismatch")
    validate_blobs(evidence, counts, files)
    blobs = {STORAGE_PREFIX + row["key"][:2] + "/" + row["key"][2:4] + "/" + row["key"]
             for row in evidence["blobs"]}
    expected = {MANIFEST, OPERATOR_PROFILE, EVIDENCE, CHATWOOT_DUMP}
    expected.update(CONFIG_PREFIX + name for name in profile["config_sha256"])
    require(set(files) == expected | blobs, "unexpected online artifact")
    with files[CHATWOOT_DUMP].open("rb") as stream:
        require(stream.read(5) == b"PGDMP" and files[CHATWOOT_DUMP].stat().st_size > 9,
                "invalid online PostgreSQL archive")
    allowed_dirs = {"storage"} | {parent.as_posix() for name in files
        for parent in PurePosixPath(name).parents if parent != PurePosixPath(".")}
    for parent, dirs, _ in os.walk(root, followlinks=False, onerror=reject_walk_error):
        for name in dirs:
            require((Path(parent) / name).relative_to(root).as_posix() in allowed_dirs,
                    "unexpected online directory")
    return count


def main():
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "--contract-only":
            count = verify_restored(Path(sys.argv[2]))
            print(json.dumps({"status": "PASS_CONTRACT_ONLY", "files": count}))
        elif len(sys.argv) == 5 and sys.argv[1] == "--operator-v3":
            count = verify_operator_capture(Path(sys.argv[4]), sys.argv[2], sys.argv[3])
            print(json.dumps({"status": "PASS_OPERATOR_NATIVE_CAPTURE_MANIFEST", "files": count}))
        elif len(sys.argv) == 5 and sys.argv[1] == "--operator-online-v1":
            count = verify_operator_online_capture(Path(sys.argv[4]), sys.argv[2], sys.argv[3])
            print(json.dumps({"status": "PASS_OPERATOR_ONLINE_CAPTURE_MANIFEST", "files": count}))
        elif len(sys.argv) == 2:
            count = verify_prepared_capture(Path(sys.argv[1]))
            print(json.dumps({"status": "PASS_VERIFIED_CAPTURE_MANIFEST", "files": count}))
        else:
            raise ValueError("one generation required")
        return 0
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        print('{"status":"FAIL_CHATWOOT_GENERATION"}', file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
