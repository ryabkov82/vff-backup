#!/usr/bin/env python3
"""Synthetic, offline contract tests. No live service or backup credentials."""
import base64
import hashlib
import json
from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] /
                     "ansible/roles/backup/files"))
import chatwoot_snapshot_contract as contract

KEY = "abcdABCDEFGHIJKLMNOPQRSTUV"
PAYLOAD = b"example synthetic attachment"

def populate_fixture(root):
    """Build an unsealed synthetic fixture for validator and role tests."""
    def put(name, payload):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        parent = target.parent
        while parent != root:
            parent.chmod(0o700)
            parent = parent.parent
        target.write_bytes(payload)
        target.chmod(0o600)

    counts = dict.fromkeys(contract.TABLES, 1)
    evidence = {
            "version": 1, "counts": counts, "dump_counts": dict(counts),
            "config": sorted(contract.MIN_CONFIG),
            "images": {name: "sha256:" + "a"*64 for name in contract.SERVICES},
            "business_offsets": {"1": 123},
            "blobs": [{"key": KEY, "byte_size": len(PAYLOAD),
                       "checksum": base64.b64encode(hashlib.md5(
                           PAYLOAD, usedforsecurity=False).digest()).decode()}],
            "unresolved_attachment_refs": 0, "pending_delivery_jobs": 0,
            "ingress_fenced": True, "other_active_writers": 0,
            "writer_states": {
                **dict.fromkeys(contract.REQUIRED_WRITERS, "stopped"),
                **dict.fromkeys(contract.OPTIONAL_WRITERS, "absent")},
    }
    put("chatwoot.dump", b"PGDMP synthetic stub, not real archive")
    for name in evidence["config"]:
        put("config/" + name, b"synthetic configuration\n")
    put(f"storage/{KEY[:2]}/{KEY[2:4]}/{KEY}", PAYLOAD)
    put("business-state/channel-1.offset", b"123\n")
    put(contract.EVIDENCE, json.dumps(evidence).encode())
    return evidence


class SnapshotContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.evidence = populate_fixture(self.root)

    @property
    def blob_name(self):
        return f"storage/{KEY[:2]}/{KEY[2:4]}/{KEY}"

    def write(self, name, payload):
        dest = self.root / name
        dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        parent = dest.parent
        while parent != self.root:
            parent.chmod(0o700)
            parent = parent.parent
        dest.write_bytes(payload)
        dest.chmod(0o600)
        return dest

    def save(self):
        self.write(contract.EVIDENCE, json.dumps(self.evidence).encode())

    def reject(self):
        with self.assertRaises((ValueError, OSError, TypeError)):
            contract.seal(self.root)
        self.assertFalse((self.root / contract.MANIFEST).exists())

    def test_valid_and_restore(self):
        count = contract.seal(self.root)
        self.assertGreater(count, 5)
        self.assertEqual(contract.verify_restored(self.root), count)

    def test_empty_legitimate_storage(self):
        self.evidence["blobs"] = []
        for section in ("counts", "dump_counts"):
            for table in ("active_storage_blobs", "active_storage_attachments", "attachments"):
                self.evidence[section][table] = 0
        (self.root / self.blob_name).unlink()
        self.save()
        self.assertEqual(contract.seal(self.root), contract.verify_restored(self.root))

    def test_dynamic_counts(self):
        for name in ("counts", "dump_counts"):
            self.evidence[name]["messages"] = 17
        self.save()
        self.assertGreater(contract.seal(self.root), 0)

    def test_count_mismatch(self):
        self.evidence["dump_counts"]["messages"] += 1
        self.save()
        self.reject()

    def test_missing_dump(self):
        (self.root / "chatwoot.dump").unlink()
        self.reject()

    def test_bad_dump_header(self):
        self.write("chatwoot.dump", b"invalid")
        self.reject()

    def test_missing_blob(self):
        (self.root / self.blob_name).unlink()
        self.reject()

    def test_wrong_blob_bytes_same_size(self):
        self.write(self.blob_name, b"x" * len(PAYLOAD))
        self.reject()

    def test_unresolved_refs(self):
        self.evidence["unresolved_attachment_refs"] = 1
        self.save()
        self.reject()

    def test_pending_jobs(self):
        self.evidence["pending_delivery_jobs"] = 1
        self.save()
        self.reject()

    def test_writer_active(self):
        for writer in contract.REQUIRED_WRITERS | contract.OPTIONAL_WRITERS:
            with self.subTest(writer=writer):
                original = self.evidence["writer_states"][writer]
                self.evidence["writer_states"][writer] = "running"
                self.save()
                self.reject()
                self.evidence["writer_states"][writer] = original

    def test_writer_inventory_missing(self):
        del self.evidence["writer_states"]["web-drafter"]
        self.save()
        self.reject()

    def test_unclassified_writer(self):
        self.evidence["other_active_writers"] = 1
        self.save()
        self.reject()

    def test_ingress_not_fenced(self):
        self.evidence["ingress_fenced"] = False
        self.save()
        self.reject()

    def test_boolean_dump_count_is_not_integer(self):
        self.evidence["dump_counts"]["messages"] = True
        self.save()
        self.reject()

    def test_wrong_checkpoint(self):
        self.write("business-state/channel-1.offset", b"124\n")
        self.reject()

    def test_extra_checkpoint(self):
        self.write("business-state/channel-2.offset", b"1\n")
        self.reject()

    def test_missing_config(self):
        (self.root / "config/.env").unlink()
        self.reject()

    def test_extra_top_level_file(self):
        self.write("logs.txt", b"unexpected")
        self.reject()

    def test_public_file(self):
        (self.root / "chatwoot.dump").chmod(0o644)
        self.reject()

    def test_symlink(self):
        (self.root / "link").symlink_to(self.root / "chatwoot.dump")
        self.reject()

    def test_hardlink(self):
        os.link(self.root / "chatwoot.dump", self.root / "other")
        self.reject()

    def test_traversal_failure(self):
        with patch.object(contract.os, "scandir", side_effect=PermissionError("test")):
            self.reject()

    def test_duplicate_evidence_keys(self):
        self.write(contract.EVIDENCE, b'{"version":1,"version":1}')
        self.reject()

    def test_manifest_no_overwrite(self):
        contract.seal(self.root)
        path = self.root / contract.MANIFEST
        original = path.read_bytes()
        with self.assertRaises(ValueError):
            contract.seal(self.root)
        self.assertEqual(path.read_bytes(), original)

    def test_restored_data_tampered(self):
        contract.seal(self.root)
        self.write(self.blob_name, b"x" * len(PAYLOAD))
        with self.assertRaises(ValueError):
            contract.verify_restored(self.root)

if __name__ == "__main__":
    unittest.main()
