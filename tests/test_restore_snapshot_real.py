#!/usr/bin/env python3
"""Actual Restic CLI acceptance, synthetic bytes and a temporary local repo only."""
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

HELPER = Path(__file__).resolve().parents[1] / "ansible/roles/restore/files/restore_snapshot.py"


@unittest.skipUnless(shutil.which("restic"), "real Restic binary unavailable")
class RealRestoreTest(unittest.TestCase):
    def test_local_restore_exact_source_no_fallback(self):
        with tempfile.TemporaryDirectory(prefix="vff-restore-acceptance-") as directory:
            root = Path(directory)
            fixture = root / "fixture"
            fixture.mkdir(mode=0o700)
            payload = fixture / "payload.bin"
            original = secrets.token_bytes(4096)
            # Never inherit real repository, cloud, password or other credentials.
            env = {
                "PATH": os.environ["PATH"],
                "HOME": str(root),
                "RESTIC_REPOSITORY": str(root / "repo"),
                "RESTIC_PASSWORD": secrets.token_hex(32),
                "RESTIC_CACHE_DIR": str(root / "cache"),
                "LC_ALL": "C",
            }

            def run(argv, options=None):
                result = subprocess.run(argv, cwd=root, env=env, text=True,
                                        input=None if options is None else json.dumps(options),
                                        capture_output=True, timeout=60, check=False)
                return result

            def restic(*args):
                result = run(["restic", *args])
                self.assertEqual(result.returncode, 0, "local fixture Restic command failed")
                return result.stdout

            restic("init")
            payload.write_bytes(original)
            (fixture / "omit.txt").write_text("synthetic excluded file")
            restic("backup", "--host", "fixture-node-a", "--tag", "chatwoot",
                   "--time", "2026-11-18 10:00:00", "fixture")
            payload.write_bytes(b"synthetic different host")
            restic("backup", "--host", "fixture-node-b", "--tag", "chatwoot",
                   "--time", "2026-11-18 11:00:00", "fixture")
            restic("check", "--read-data")
            snapshots = json.loads(restic("snapshots", "--json"))
            by_host = {row["hostname"]: row["id"] for row in snapshots}
            self.assertEqual(set(by_host), {"fixture-node-a", "fixture-node-b"})

            opts = {"host": "fixture-node-a", "tag": "chatwoot", "snapshot": "",
                    "newest": True, "force": True, "target": str(root / "restored"),
                    "includes": [], "excludes": ["**/omit.txt"]}

            def invoke(**overrides):
                return run([sys.executable, str(HELPER)], dict(opts, **overrides))

            missing = root / "missing-host-target"
            self.assertNotEqual(invoke(host="absent", target=str(missing)).returncode, 0)
            self.assertFalse(missing.exists())
            wrong = root / "wrong-id-target"
            self.assertNotEqual(invoke(snapshot=by_host["fixture-node-b"], target=str(wrong)).returncode, 0)
            self.assertFalse(wrong.exists())
            bad_tag = root / "wrong-tag-target"
            self.assertNotEqual(invoke(tag="shm", target=str(bad_tag)).returncode, 0)
            self.assertFalse(bad_tag.exists())
            outside = root / "outside"
            outside.mkdir(mode=0o700)
            (root / "symlink").symlink_to(outside, target_is_directory=True)
            self.assertNotEqual(invoke(target=str(root / "symlink" / "bad")).returncode, 0)
            self.assertEqual(list(outside.iterdir()), [])
            self.assertNotEqual(invoke(target=str(root) + "/../bad").returncode, 0)

            result = invoke()
            self.assertEqual(result.returncode, 0, "exact source restore failed")
            receipt = json.loads(result.stdout)
            self.assertEqual(receipt["snapshot"], by_host["fixture-node-a"])
            self.assertEqual(receipt["host"], "fixture-node-a")
            target = root / "restored"
            self.assertEqual(receipt["target"], str(target / "data"))
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
            files = list((target / "data").rglob("payload.bin"))
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].read_bytes(), original)
            self.assertEqual(list((target / "data").rglob("omit.txt")), [])
            self.assertNotEqual(invoke().returncode, 0)
            self.assertEqual(files[0].read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
