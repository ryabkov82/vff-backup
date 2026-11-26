#!/usr/bin/env python3
"""Scope, failure behavior and native Restic copy checks for the VM200 hook."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

HELPER = Path(__file__).resolve().parents[1] / "ansible/roles/backup/files/replicate_vm200.py"
spec = importlib.util.spec_from_file_location("replicate_vm200", HELPER)
replica = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replica)


def row(**updates):
    data = {"id": "a" * 64, "tree": "b" * 64, "hostname": "billing-1",
            "tags": ["shm"], "paths": ["/opt/shm", "/var/backups/db"],
            "time": "2026-11-26T03:22:22.03051981+03:00"}
    return dict(data, **updates)


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.directory = Path(self.root.name)
        (self.directory / "vm200.pass").write_text("synthetic destination\n")
        self.source = {"RESTIC_REPOSITORY": replica.PRIMARY + "shm/ru-msk-1",
                       "VFF_VM200_S3_ENDPOINT": "http://100.84.224.4:9000",
                       "RESTIC_PASSWORD": "synthetic source", "AWS_ACCESS_KEY_ID": "old-id",
                       "AWS_SECRET_ACCESS_KEY": "old-example"}
        self.settings = {
            "AWS_ACCESS_KEY_ID": "shm-billing-1",
            "AWS_SECRET_ACCESS_KEY": "a" * 64,
            "AWS_DEFAULT_REGION": "us-east-1",
            "RESTIC_REPOSITORY": "s3:http://100.84.224.4:9000/vff-shm-prod/backup/repo",
            "RESTIC_PASSWORD_FILE": "/etc/vff-backup-vm200/restic.pass",
        }
        self.write_settings()

    def write_settings(self):
        (self.directory / "vm200.env").write_text(
            "".join(f"{key}={value}\n" for key, value in self.settings.items()))

    def test_environment_isolation(self):
        source, dest = replica.environments("shm", "billing-1",
                                           self.source, self.directory)
        self.assertEqual(source["AWS_ACCESS_KEY_ID"], dest["AWS_ACCESS_KEY_ID"])
        self.assertNotIn("RESTIC_PASSWORD", dest)
        self.assertEqual(dest["RESTIC_FROM_PASSWORD"], self.source["RESTIC_PASSWORD"])
        self.assertEqual(dest["RCLONE_CONFIG_VM200_ACCESS_KEY_ID"], "shm-billing-1")
        self.assertEqual(dest["RCLONE_CONFIG_VM200_ENV_AUTH"], "false")
        self.assertEqual(dest["RESTIC_REPOSITORY"], "rclone:vm200:vff-shm-prod/backup/repo")

    def test_wrong_host_or_source_rejected(self):
        for host, source in (("wrong", self.source),
                             ("billing-1", dict(self.source, RESTIC_REPOSITORY="wrong"))):
            with self.assertRaises(replica.ReplicationError):
                replica.environments("shm", host, source, self.directory)

    def test_invalid_endpoint_and_non_overlay_route_rejected(self):
        for endpoint in ("https://100.84.224.4:9000", "http://100.84.224.4:443",
                         "http://user@100.84.224.4:9000", "http://100.84.224.4:9000/path"):
            with self.assertRaises(replica.ReplicationError):
                replica.private_endpoint({"VFF_VM200_S3_ENDPOINT": endpoint})
        for device, success in (("tailscale0", True), ("eth0", False)):
            result = subprocess.CompletedProcess(["ip"], 0,
                json.dumps([{"dev": device}]).encode(), b"")
            with patch.object(replica.subprocess, "run", return_value=result):
                if success:
                    replica.check_overlay_route("100.84.224.4")
                else:
                    with self.assertRaises(replica.ReplicationError):
                        replica.check_overlay_route("100.84.224.4")

    def test_wrong_bucket_rejected(self):
        self.settings["RESTIC_REPOSITORY"] = "s3:http://100.84.224.4:9000/vff-remnawave-prod/backup/repo"
        self.write_settings()
        with self.assertRaises(replica.ReplicationError):
            replica.environments("shm", "billing-1", self.source, self.directory)

    def test_newest_current_host_and_paths(self):
        good = row()
        selected = replica.latest([row(hostname="old", time="2026-11-27T00:00:00Z"),
                                   row(time="2026-11-25T00:00:00Z"), good], "billing-1", "shm")
        self.assertEqual(selected, good)
        with self.assertRaises(replica.ReplicationError):
            replica.latest([row(paths=["/var/backups/db"])], "billing-1", "shm")

    def test_copy_scoped_and_retention_only_on_destination(self):
        source, dest = replica.environments("shm", "billing-1",
                                           self.source, self.directory)
        src_row = row()
        dst_row = row(id="c"*64)
        responses = [json.dumps([src_row]), "[]", "", json.dumps([dst_row]), ""]
        with patch.object(replica, "command", side_effect=responses) as call:
            receipt = replica.replicate("shm", source, dest)
        self.assertEqual(receipt["destination"], dst_row["id"])
        self.assertEqual(call.call_args_list[2].args,
                         (dest, "copy", "--from-repo", source["RESTIC_REPOSITORY"], src_row["id"]))
        self.assertEqual(call.call_args_list[-1].args[:3], (dest, "forget", "--host"))
        with patch.object(replica, "command", side_effect=responses[:3]+["[]"]) as call:
            with self.assertRaises(replica.ReplicationError):
                replica.replicate("shm", source, dest)
            self.assertEqual(call.call_count, 4)
        with patch.object(replica, "command", side_effect=responses[:4]) as call:
            replica.replicate("shm", source, dest, copy_only=True)
            self.assertEqual(call.call_count, 4)

    def test_copy_error_does_not_prune(self):
        source, dest = replica.environments("shm", "billing-1",
                                           self.source, self.directory)
        with patch.object(replica, "command", side_effect=[json.dumps([row()]), "[]",
                              replica.ReplicationError("copy failure")]) as call:
            with self.assertRaises(replica.ReplicationError):
                replica.replicate("shm", source, dest)
            self.assertEqual(call.call_count, 3)

    def test_failure_metric_preserves_last_success(self):
        replica.metric_write(self.directory, "shm", True, 12345)
        replica.metric_write(self.directory, "shm", False)
        self.assertIn("12345", (self.directory / "backup_vm200_shm_last_success.prom").read_text())
        self.assertTrue((self.directory / "backup_vm200_shm.prom").read_text().endswith(" 0\n"))


    def test_command_failure_does_not_expose_captured_output(self):
        failed = subprocess.CompletedProcess(["restic"], 9, b"private-output", b"private-error")
        with patch.object(replica.subprocess, "run", return_value=failed):
            with self.assertRaisesRegex(replica.ReplicationError, "^restic copy failed \\(exit 9\\)$"):
                replica.command({}, "copy", "fixture")

    def test_main_success_and_failure_receipts(self):
        import contextlib
        import io
        for result, expected in (({"source": "a"*64, "destination": "b"*64,
                                   "snapshot_timestamp": 123}, 0),
                                 (replica.ReplicationError("scope rejected"), 1)):
            with patch("sys.argv", ["replicate_vm200", "shm", "--copy-only"]), \
                    patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": str(self.directory),
                                            "VFF_VM200_S3_ENDPOINT": self.source["VFF_VM200_S3_ENDPOINT"]}), \
                    patch.object(replica, "environments", return_value=({}, {})), \
                    patch.object(replica, "check_overlay_route"), \
                    patch.object(replica, "replicate", side_effect=[result]), \
                    patch.object(replica, "metric_write") as metric, \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(replica.main(), expected)
                self.assertEqual(metric.call_args.args[2], expected == 0)
                self.assertEqual(json.loads(output.getvalue())["status"],
                                 "PASS_VM200_COPY" if expected == 0 else "FAIL_VM200_COPY")


@unittest.skipUnless(shutil.which("restic") and shutil.which("rclone"),
                     "native Restic or rclone missing")
class RealCopyTest(unittest.TestCase):
    def test_native_copy_and_restore(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source_repo = str(root / "source")
            dest_repo = "rclone:local:" + str(root / "dest")
            src = {"PATH": os.environ["PATH"], "HOME": folder, "LC_ALL": "C",
                   "RESTIC_REPOSITORY": source_repo, "RESTIC_PASSWORD": "fixture-src",
                   "RESTIC_CACHE_DIR": str(root / "cache")}
            dst = dict(src, RESTIC_REPOSITORY=dest_repo, RESTIC_PASSWORD="fixture-dst",
                       RESTIC_FROM_PASSWORD="fixture-src", RCLONE_CONFIG="/dev/null",
                       RCLONE_CONFIG_LOCAL_TYPE="local")
            def run(env, *args):
                result = subprocess.run(["restic", *args], cwd=root, env=env,
                                        capture_output=True, timeout=120, check=False)
                self.assertEqual(result.returncode, 0, "native copy fixture command failed")
                return result.stdout
            payload = root / "payload.bin"
            payload.write_bytes(b"VM200 synthetic fixture\n" * 128)
            run(src, "init")
            run(dst, "init")
            run(src, "backup", "--host", "billing-1", "--tag", "shm", "payload.bin")
            before = json.loads(run(src, "snapshots", "--json"))[0]
            run(dst, "copy", "--from-repo", source_repo, before["id"])
            run(dst, "copy", "--from-repo", source_repo, before["id"])
            copied = json.loads(run(dst, "snapshots", "--json"))
            self.assertEqual(len(copied), 1)
            self.assertTrue(replica.same_snapshot(before, copied[0]))
            run(dst, "check", "--read-data")
            run(dst, "restore", copied[0]["id"], "--target", str(root / "restored"))
            restored = list((root / "restored").rglob("payload.bin"))
            self.assertEqual(len(restored), 1)
            self.assertEqual(restored[0].read_bytes(), payload.read_bytes())
            self.assertEqual(json.loads(run(src, "snapshots", "--json")), [before])


if __name__ == "__main__":
    unittest.main()
