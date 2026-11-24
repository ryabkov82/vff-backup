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
        self.job={"name":"chatwoot-test","prepared_snapshot":{
            "root":str(self.stage),"program":str(self.capture)}}

    def render(self):
        base.render_backup_script(self.harness.script,[self.job],self.harness.metrics,
            self.harness.env_file,backup_prepared_verifier_path=str(self.modules/"verify_prepared_snapshot.py"),
            backup_chatwoot_verifier_path=str(self.modules/"chatwoot_snapshot_contract.py"))
        subprocess.run(["bash","-n",str(self.harness.script)],check=True)

    def run_job(self,env=None):
        return self.harness._run(env or {},job="chatwoot-test")

    def status(self):
        p=self.harness.metrics/"backup_job-chatwoot-test.prom"
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
        self.assertIn("--tag chatwoot-test",calls[1])
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
        self.job["prepared_snapshot"]["kind"]="chatwoot"
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
        path=self.harness.metrics/"backup_job-chatwoot-test.prom"
        self.assertEqual(path.stat().st_mode&0o777,0o644)
        self.harness.env_file.write_text("false\n")
        self.assertNotEqual(self.run_job().returncode,0)
        self.assertEqual(self.status(),1)
        self.assertEqual(len(self.harness._restic_calls()),2)

    def test_metric_failure_does_not_report_job_success(self):
        self.render()
        (self.harness.metrics/"backup_job-chatwoot-test.prom").mkdir()
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
            f=root/name;f.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            f.write_bytes(b"PGDMP"+b"x"*256 if name.endswith(".dump") else b"synthetic")
            f.chmod(0o600)
        (root/contract.V2_RECEIPT).write_text(json.dumps(receipt))
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
