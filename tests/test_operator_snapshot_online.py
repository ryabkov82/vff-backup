"""Online archive format is distinct from manual v3 (structural fixtures)."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from test_operator_snapshot_v3 import fixture, put, put_json, c, seal


def online_fixture(root):
    pin, scope = fixture(root)
    previous = json.loads((root / c.EVIDENCE).read_text())
    state = json.loads((root / c.OPERATOR_FENCE).read_text())["source_db"]
    state["unconfirmed"] = 1  # Captured but not safe to send after restore.
    shutil.rmtree(root / "business-state")
    (root / "redis.rdb").unlink()
    (root / c.OPERATOR_FENCE).unlink()
    evidence = {
        "version": 1, "mode": "operator-online-v1", "scope": scope,
        "status": "PASS_OPERATOR_ONLINE_CAPTURE", "profile_sha256": pin,
        "snapshot_epoch_seconds": 1795700000,
        "counts": previous["counts"], "blobs": previous["blobs"],
        "checkpoint_observations": {"channel": "47", "before_db_snapshot": 123,
                                    "after_copy": 456},
        "delivery": state, "unresolved_attachment_refs": 0,
        "source_writers_running": True, "recovery_policy": dict(c.ONLINE_POLICY),
    }
    put_json(root, c.EVIDENCE, evidence)
    return pin, scope, evidence



def checkpoint_fixture(root):
    _, scope, evidence = online_fixture(root)
    profile = json.loads((root / c.OPERATOR_PROFILE).read_text())
    business = b"VFF_TELEGRAM_CHECKPOINT_BACKEND=postgres\nVFF_TELEGRAM_BUSINESS_CONNECTION_ID=synthetic-connection\n"
    put(root, "config/.env.operator.business", business)
    profile["config_sha256"][".env.operator.business"] = hashlib.sha256(business).hexdigest()
    put_json(root, c.OPERATOR_PROFILE, profile)
    pin = c.digest(root / c.OPERATOR_PROFILE)
    backend, fingerprint = c.operator_checkpoint_config(root / "config", profile)
    assert backend == "postgres"
    evidence.pop("checkpoint_observations")
    evidence.update(version=2, profile_sha256=pin, recovery_policy=dict(c.CHECKPOINT_POLICY),
        checkpoint={"channel_id": profile["channel_id"], "scope_sha256": fingerprint,
                    "next_offset": 104})
    put_json(root, c.EVIDENCE, evidence)
    return pin, scope, evidence


class OnlineContractTests(unittest.TestCase):
    def test_valid_online_and_no_manual_v3_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pin, scope, _ = online_fixture(root)
            count = seal(root)
            self.assertEqual(c.verify_operator_online_capture(root, pin, scope), count)
            for verifier, args in (
                (c.verify_operator_capture, (root, pin, scope)),
                (c.verify_prepared_capture, (root,)),
                (c.verify_operator_online_capture, (root, "0"*64, scope)),
                (c.verify_operator_online_capture, (root, pin, "OPERATOR_PROD")),
            ):
                with self.assertRaises((ValueError, KeyError)):
                    verifier(*args)

    def test_no_active_checkpoint_redis_or_unverified_blob(self):
        for case in ("checkpoint", "redis", "corrupt", "missing", "policy", "wrong-channel",
                     "missing-recovery-policy", "scope", "writer-stopped", "invalid-delivery"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                pin, scope, evidence = online_fixture(root)
                blob = evidence["blobs"][0]["key"]
                relative = "storage/" + blob[:2] + "/" + blob[2:4] + "/" + blob
                if case == "checkpoint":
                    put(root, "business-state/channel-47.offset", b"456\n")
                elif case == "redis":
                    put(root, "redis.rdb", b"REDIS0009 old")
                elif case == "corrupt":
                    put(root, relative, b"X"*evidence["blobs"][0]["byte_size"])
                elif case == "missing":
                    (root / relative).unlink()
                elif case == "policy":
                    evidence["recovery_policy"]["checkpoint"] = "REPLAY"
                elif case == "wrong-channel":
                    evidence["checkpoint_observations"]["channel"] = "48"
                elif case == "missing-recovery-policy":
                    del evidence["recovery_policy"]
                elif case == "scope":
                    evidence["delivery"]["channels"] = 2
                elif case == "writer-stopped":
                    evidence["source_writers_running"] = False
                else:
                    evidence["delivery"]["unconfirmed"] = 2
                put_json(root, c.EVIDENCE, evidence)
                seal(root)
                with self.assertRaises((ValueError, KeyError)):
                    c.verify_operator_online_capture(root, pin, scope)

    def test_postgres_checkpoint_online_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pin, scope, _ = checkpoint_fixture(root)
            count = seal(root)
            self.assertEqual(c.verify_operator_online_capture(root, pin, scope), count)

    def test_checkpoint_snapshot_rejects_stale_file_or_invalid_cursor(self):
        for case in ("file", "scope", "cursor", "channel", "journal", "policy", "version"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                pin, scope, evidence = checkpoint_fixture(root)
                checkpoint = evidence["checkpoint"]
                if case == "file":
                    evidence["checkpoint_observations"] = {"channel": "47", "before_db_snapshot": 103,
                                                            "after_copy": 103}
                elif case == "scope":
                    checkpoint["scope_sha256"] = "0"*64
                elif case == "cursor":
                    checkpoint["next_offset"] = -1
                elif case == "channel":
                    checkpoint["channel_id"] += 1
                elif case == "journal":
                    checkpoint["pending_update_id"] = 104
                elif case == "policy":
                    evidence["recovery_policy"] = dict(c.ONLINE_POLICY)
                else:
                    evidence["version"] = 1
                put_json(root, c.EVIDENCE, evidence)
                seal(root)
                with self.assertRaises((ValueError, KeyError)):
                    c.verify_operator_online_capture(root, pin, scope)

    def test_manual_archive_not_accepted_as_online(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pin, scope = fixture(root)
            seal(root)
            with self.assertRaises(ValueError):
                c.verify_operator_online_capture(root, pin, scope)


if __name__ == "__main__":
    unittest.main()
