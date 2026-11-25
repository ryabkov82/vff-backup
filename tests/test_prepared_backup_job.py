"""Prepared-mode role contracts; all data and transport are synthetic."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import test_backup_reliability as base
import test_chatwoot_snapshot_contract as old_contract

class PreparedJobTests(unittest.TestCase):
    def setUp(self):
        self.harness=base.BackupScriptTests()
        self.harness.setUp()
        self.addCleanup(self.harness.tearDown)
        self.root=self.harness.root
        self.stage=self.root/"prepared"
        self.stage.mkdir(mode=0o700)
        self.modules=self.root/"modules"
        self.modules.mkdir()
        for name in ("verify_prepared_snapshot.py","chatwoot_snapshot_contract.py"):
            dest=self.modules/name
            dest.write_bytes((base.ROOT/"ansible/roles/backup/files"/name).read_bytes())
            dest.chmod(0o755)
        self.capture=self.root/"capture.py"
        self.capture.write_text(
            '#!/usr/bin/env python3\n'
            'import os,sys\nfrom pathlib import Path\n'
            f'sys.path.insert(0,{str(self.modules)!r})\n'
            'from verify_prepared_snapshot import seal\n'
            'd=Path(sys.argv[1]);p=d/"example";p.write_text("synthetic")\n'
            'mode=os.environ.get("CAPTURE_CASE","good")\n'
            'if mode=="fail":sys.exit(7)\n'
            'if mode=="missing":sys.exit(0)\n'
            'seal(d)\n'
            'if mode=="tamper":p.write_text("changed")\n'
            'if mode=="extra":(d/"extra").write_text("extra")\n'
            'if mode=="link":(d/"link").symlink_to(p)\n'
            'if mode=="permissions":p.chmod(0o644)\n')
        self.capture.chmod(0o755)
        self.job={"name":"generic-test","prepared_snapshot":{
            "root":str(self.stage),"program":str(self.capture)}}

    def render(self):
        base.render_backup_script(self.harness.script,[self.job],self.harness.metrics,
            self.harness.env_file,backup_prepared_verifier_path=str(self.modules/"verify_prepared_snapshot.py"),
            backup_chatwoot_verifier_path=str(self.modules/"chatwoot_snapshot_contract.py"))
        subprocess.run(["bash","-n",str(self.harness.script)],check=True)

    def run_job(self,env=None):
        return self.harness._run(env or {},job=self.job["name"])

    def status(self):
        p=self.harness.metrics/"backup_job-generic-test.prom"
        return int(next(x for x in p.read_text().splitlines() if x.startswith("backup_last_status{")).rsplit(" ",1)[1])

    def test_prepared_success_and_host_scoped_retention(self):
        self.render()
        r=self.run_job()
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertIn("verified prepared snapshot abcdef0123456789",r.stdout)
        self.assertEqual(self.status(),0)
        calls=self.harness._restic_calls()
        self.assertEqual(len(calls),2)
        self.assertIn("--host",calls[0])
        self.assertIn("generation-",calls[0])
        self.assertIn("--group-by host,tags --host",calls[1])
        self.assertIn("--tag generic-test",calls[1])
        self.assertFalse(self.harness._docker_calls())
        self.assertFalse(list(self.stage.glob("generation-*")))

    def test_prepared_failure_never_uploads_or_prunes(self):
        self.render()
        for case in ("fail","missing","tamper","extra","link","permissions"):
            with self.subTest(case=case):
                r=self.run_job({"CAPTURE_CASE":case})
                self.assertNotEqual(r.returncode,0,r.stderr)
                self.assertEqual(self.status(),1)
                self.assertFalse(self.harness._restic_calls())
                self.assertFalse(list(self.stage.glob("generation-*")))

    def test_invalid_mode_or_mixed_config_refused(self):
        self.job["prepared_snapshot"]["kind"]="unsupported"
        self.render()
        self.assertNotEqual(self.run_job().returncode,0)
        self.job["prepared_snapshot"]["kind"]="generic"
        self.job["db_dump"]={"enabled":False}
        self.render()
        self.assertNotEqual(self.run_job().returncode,0)
        self.assertFalse(self.harness._restic_calls())

    def test_chatwoot_job_cannot_use_generic_or_legacy_verifier(self):
        for job_name in ("chatwoot", "chatwoot-prod", "vm205-prepared-drill"):
            for kind in ("generic", None):
                with self.subTest(job=job_name, kind=kind):
                    self.job["name"] = job_name
                    spec = {"root": str(self.stage), "program": str(self.capture)}
                    if kind is not None:
                        spec["kind"] = kind
                    self.job["prepared_snapshot"] = spec
                    self.render()
                    self.assertIn("requires its semantic", self.run_job().stderr)
        self.job = {"name": "chatwoot", "paths": [str(self.stage)]}
        self.render()
        self.assertNotEqual(self.run_job().returncode, 0)
        self.assertFalse(self.harness._restic_calls())

    def test_chatwoot_requires_declared_execution_dependencies(self):
        self.job["name"] = "vm205-prepared-drill"
        self.job["prepared_snapshot"]["kind"] = "chatwoot"
        self.render()
        result = self.run_job()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("execution dependencies required", result.stderr)
        self.assertFalse(self.harness._restic_calls())

    def test_untrusted_ancestor_rejected_before_env_or_capture(self):
        unsafe = self.root / "unsafe"
        unsafe.mkdir(mode=0o777)
        unsafe.chmod(0o777)
        self.job["prepared_snapshot"]["trusted_paths"] = [str(unsafe)]
        marker = self.root / "env-was-sourced"
        self.harness.env_file.write_text(f"touch '{marker}'\n")
        self.render()
        result = self.run_job()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unsafe prepared execution/configuration path", result.stderr)
        self.assertFalse(marker.exists())
        self.assertFalse(self.harness._restic_calls())
        self.assertFalse(list(self.stage.glob("generation-*")))

    def test_safe_leaf_below_writable_or_symlink_ancestor_is_rejected(self):
        unsafe = self.root / "unsafe"
        unsafe.mkdir(mode=0o777)
        unsafe.chmod(0o777)
        capture = unsafe / "capture"
        capture.write_bytes(self.capture.read_bytes())
        capture.chmod(0o755)
        alias = self.root / "alias"
        alias.symlink_to(unsafe)
        for path in (capture, alias / "capture"):
            with self.subTest(path=path):
                self.job["prepared_snapshot"]["program"] = str(path)
                self.render()
                self.assertNotEqual(self.run_job().returncode, 0)
                self.assertFalse(self.harness._restic_calls())

    def test_renamed_chatwoot_capture_cannot_pass_generic_verifier(self):
        self.capture.write_text(
            '#!/usr/bin/env python3\nimport sys\nfrom pathlib import Path\n'
            f'sys.path.insert(0,{str(self.modules)!r})\n'
            'from verify_prepared_snapshot import seal\n'
            'd=Path(sys.argv[1]);(d/"chatwoot.dump").write_bytes(b"PGDMP fixture");seal(d)\n')
        self.render()
        result = self.run_job()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires its semantic", result.stderr)
        self.assertFalse(self.harness._restic_calls())

    def test_unreconciled_generation_is_not_removed(self):
        stale=self.stage/"generation-old"
        stale.mkdir(mode=0o700)
        self.render()
        self.assertNotEqual(self.run_job().returncode,0)
        self.assertTrue(stale.is_dir())
        self.assertFalse(self.harness._restic_calls())

    def test_failed_upload_does_not_prune(self):
        self.render()
        r=self.run_job({"RESTIC_BACKUP_RC":"7"})
        self.assertNotEqual(r.returncode,0)
        self.assertEqual(self.status(),1)
        self.assertEqual(len(self.harness._restic_calls()),1)
        self.assertFalse(list(self.stage.glob("generation-*")))

    def test_old_fake_dump_contract_cannot_pass_job(self):
        self.capture.write_text(
            '#!/usr/bin/env python3\nimport sys\nfrom pathlib import Path\n'
            f'sys.path.insert(0,{str(base.ROOT/"tests")!r})\n'
            'from test_chatwoot_snapshot_contract import populate_fixture,contract\n'
            'd=Path(sys.argv[1]);populate_fixture(d);contract.seal(d)\n')
        self.job["name"]="chatwoot"
        self.job["prepared_snapshot"]["kind"]="chatwoot"
        self.job["prepared_snapshot"]["trusted_paths"]=[str(self.modules)]
        self.render()
        self.assertNotEqual(self.run_job().returncode,0)
        self.assertFalse(self.harness._restic_calls())
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)
            old_contract.populate_fixture(p)
            old_contract.contract.seal(p)
            a=subprocess.run([str(self.modules/"chatwoot_snapshot_contract.py"),
                             "--contract-only",str(p)],capture_output=True,text=True)
            self.assertEqual(a.returncode,0,a.stderr)
            self.assertIn("PASS_CONTRACT_ONLY",a.stdout)
            b=subprocess.run([str(self.modules/"chatwoot_snapshot_contract.py"),str(p)],
                             capture_output=True,text=True)
            self.assertNotEqual(b.returncode,0)

    def test_metrics_are_readable_and_failed_environment_cannot_leave_success(self):
        self.render()
        self.assertEqual(self.run_job().returncode,0)
        path=self.harness.metrics/"backup_job-generic-test.prom"
        self.assertEqual(path.stat().st_mode&0o777,0o644)
        self.harness.env_file.write_text("false\n")
        self.assertNotEqual(self.run_job().returncode,0)
        self.assertEqual(self.status(),1)
        self.assertEqual(len(self.harness._restic_calls()),2)

    def test_metric_failure_does_not_report_job_success(self):
        self.render()
        (self.harness.metrics/"backup_job-generic-test.prom").mkdir()
        self.assertNotEqual(self.run_job().returncode,0)

    def test_unapproved_prepared_timer_disabled_and_no_legacy_compose(self):
        tasks=(base.ROOT/"ansible/roles/backup/tasks/jobs.yml").read_text()
        override=(base.ROOT/"ansible/roles/backup/tasks/compose_override.yml").read_text()
        self.assertIn("schedule_enabled | default(false)",tasks)
        self.assertIn("enabled: false",tasks)
        self.assertIn("when: (backup_jobs_needing_override | default([])) | length > 0",override)

class VerifiedCaptureManifestTests(unittest.TestCase):
    """Synthetic schema coverage; this never substitutes for native pg_restore."""
    def make(self,root):
        contract=old_contract.contract
        counts=dict.fromkeys(contract.TABLES,1)
        receipt={
            "version":2,"scope":"VM205_FUNCTIONAL_TEST",
            "status":"PASS_VM205_PREPARED_RESTORE",
            "source_counts":counts,"restored_counts":dict(counts),
            "source_blobs":1,"restored_blobs":1,"pending_delivery":False,
            "writers_resumed":True,"external_delivery":False,
            "restored_test3_poller_started":False,
            "restore_checks":dict.fromkeys(("postgres","mysql","remnawave","website","chatwoot",
                "link_tls","images","blob_integrity","checkpoint","scratch_cleanup"),True)
        }
        for name in contract.V2_REQUIRED:
            f=root/name
            f.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            # pathlib's intermediate mkdir calls do not inherit mode.
            # Explicitly build a private fixture under either common umask.
            for parent in f.parents:
                if parent == root:
                    break
                parent.chmod(0o700)
            f.write_bytes(b"PGDMP"+b"x"*256 if name.endswith(".dump") else b"synthetic")
            f.chmod(0o600)
        clear=dict(channels=1,scoped_inboxes=1,outgoing=8,unconfirmed=0,
                   failed=0,invalid_status=0,invalid_ack=0,reservations=0)
        queues=dict(queued=0,working=0,retry=0)
        (root/"source/delivery-fence.json").write_text(json.dumps({
            "version":1,"source_db":clear,"restored_db":clear,
            "source_redis":queues,"restored_redis":queues,
            "scratch_reservation_rejected":True}))
        (root/contract.V2_RECEIPT).write_text(json.dumps(receipt))
        blob = root / "source/storage" / old_contract.KEY[:2] / old_contract.KEY[2:4] / old_contract.KEY
        blob.parent.mkdir(parents=True, mode=0o700)
        blob.parent.parent.chmod(0o700)
        blob.write_bytes(old_contract.PAYLOAD)
        blob.chmod(0o600)
        import base64
        (root/"source/blob-inventory.json").write_text(json.dumps([{
            "key": old_contract.KEY, "byte_size": len(old_contract.PAYLOAD),
            "checksum": base64.b64encode(hashlib.md5(old_contract.PAYLOAD, usedforsecurity=False).digest()).decode()
        }]))
        (root/"source/image-inventory.json").write_text(json.dumps({
            service:{"image":"synthetic","id":"sha256:"+"a"*64}
            for service in contract.V2_SERVICES}))
        return receipt

    def test_sealed_receipt_contract_and_tamper(self):
        contract=old_contract.contract
        from verify_prepared_snapshot import seal
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            self.make(root)
            count=seal(root)
            self.assertEqual(contract.verify_prepared_capture(root),count)
            (root/"source/chatwoot.env").write_bytes(b"changed")
            with self.assertRaises(ValueError):
                contract.verify_prepared_capture(root)
            with self.assertRaises(ValueError):
                seal(root)

    def test_semantic_inventory_refuses_sealed_unapproved_data(self):
        contract = old_contract.contract
        from verify_prepared_snapshot import seal
        for path in (
                "source/trace.log", "source/config/unreviewed.env",
                "source/link/extra.key", "source/mails/debug.log",
                "source/storage/ab/cd/undeclared-media", "source/redis/debug.rdb",
                "source/shm_data/unknown.bin", "source/test3-checkpoint/extra.state"):
            with self.subTest(path=path), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self.make(root)
                dest = root / path
                dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                for parent in dest.parents:
                    if parent == root:
                        break
                    parent.chmod(0o700)
                dest.write_bytes(b"synthetic unapproved content")
                dest.chmod(0o600)
                seal(root)
                with self.assertRaisesRegex(ValueError, "undeclared v2 capture artifact"):
                    contract.verify_prepared_capture(root)

    def test_empty_undeclared_directory_is_rejected(self):
        contract = old_contract.contract
        from verify_prepared_snapshot import seal
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make(root)
            (root/"source/unapproved").mkdir(mode=0o700)
            seal(root)
            with self.assertRaisesRegex(ValueError, "undeclared v2 capture directory"):
                contract.verify_prepared_capture(root)

    def test_allowed_native_data_inventory(self):
        contract = old_contract.contract
        from verify_prepared_snapshot import seal
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make(root)
            names = (
                "source/mails/" + "1"*19 + "-" + "a"*24 + ".eml",
                "source/redis/appendonlydir/appendonly.aof.2.base.rdb",
                "source/redis/appendonlydir/appendonly.aof.2.incr.aof",
                "source/redis/appendonlydir/appendonly.aof.manifest",
                "source/remna_redis/dump.rdb",
                "source/test3-checkpoint/singleton.lock",
                "source/test3-checkpoint/vff-bizpair-test-send-205-" + "b"*16 + ".state")
            for name in names:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                for parent in path.parents:
                    if parent == root:
                        break
                    parent.chmod(0o700)
                path.write_bytes(b"fixture")
                path.chmod(0o600)
            (root/"source/shm_data").mkdir(mode=0o700)
            count = seal(root)
            self.assertEqual(contract.verify_prepared_capture(root), count)

    def test_resealed_unrelated_blob_bytes_are_rejected(self):
        contract = old_contract.contract
        from verify_prepared_snapshot import seal
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make(root)
            path = root / "source/storage" / old_contract.KEY[:2] / old_contract.KEY[2:4] / old_contract.KEY
            path.write_bytes(b"X" * len(old_contract.PAYLOAD))
            seal(root)
            with self.assertRaisesRegex(ValueError, "blob checksum mismatch"):
                contract.verify_prepared_capture(root)

    def test_bad_receipt_refused_even_when_resealed(self):
        contract=old_contract.contract
        from verify_prepared_snapshot import seal
        for field,value in (("pending_delivery",True),("writers_resumed",False),
                            ("restored_test3_poller_started",True),("external_delivery",True),
                            ("scope","PROD"),("restored_blobs",2),("restored_counts",{}),
                            ("restore_checks",{})):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);receipt=self.make(root);receipt[field]=value
                (root/contract.V2_RECEIPT).write_text(json.dumps(receipt))
                seal(root)
                with self.assertRaises(ValueError):
                    contract.verify_prepared_capture(root)

    def test_delivery_fence_rejects_db_or_redis_uncertainty_even_when_resealed(self):
        contract=old_contract.contract
        from verify_prepared_snapshot import seal
        for section,key,value in (
                ("source_db","reservations",1),("restored_db","unconfirmed",1),
                ("source_db","failed",1),("restored_db","outgoing",9),
                ("source_db","scoped_inboxes",2),("restored_db","invalid_ack",1),
                ("source_redis","queued",1),("restored_redis","working",1),
                ("restored_redis","retry",False)):
            with self.subTest(section=section,key=key), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);self.make(root)
                path=root/"source/delivery-fence.json"
                state=json.loads(path.read_text())
                state[section][key]=value
                path.write_text(json.dumps(state))
                seal(root)
                with self.assertRaises(ValueError):
                    contract.verify_prepared_capture(root)
