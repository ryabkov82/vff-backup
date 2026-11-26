"""Online archive format is distinct from manual v3 (structural fixtures)."""
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

    def test_manual_archive_not_accepted_as_online(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pin, scope = fixture(root)
            seal(root)
            with self.assertRaises(ValueError):
                c.verify_operator_online_capture(root, pin, scope)


if __name__ == "__main__":
    unittest.main()
