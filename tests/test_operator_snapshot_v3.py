"""Structural tests for the explicit v3 profile; native acceptance is separate."""
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ansible/roles/backup/files"))
import chatwoot_snapshot_contract as c
from verify_prepared_snapshot import seal


def put(root,name,data):
    path=root/name
    path.parent.mkdir(parents=True,exist_ok=True)
    for parent in path.parents:
        if parent==root:
            break
        parent.chmod(0o700)
    path.write_bytes(data)
    path.chmod(0o600)
    return path


def put_json(root,name,data):
    return put(root,name,json.dumps(data,sort_keys=True).encode())


def fixture(root):
    scope="OPERATOR_COMPATIBILITY_TEST"
    config={n:hashlib.sha256(put(root,"config/"+n,b"synthetic").read_bytes()).hexdigest()
            for n in c.OPERATOR_CONFIG|{c.OPERATOR_TEST}}
    profile={"version":3,"scope":scope,"source_host":"fixture","project":"fixture",
             "config_root":"/opt/operator","stage_root":"/var/backups/operator",
             "account_id":9,"website_inbox_id":17,"business_inbox_id":18,"channel_id":47,
             "bot_name":"synthetic","contact_link_mode":"dry-run",
             "config_sha256":config,"images":c.OPERATOR_IMAGES}
    pin=hashlib.sha256(put_json(root,c.OPERATOR_PROFILE,profile).read_bytes()).hexdigest()
    data=b"synthetic blob"
    key="abcdABCDEFGHIJKLMNOPQRSTUV"
    put(root,"storage/ab/cd/"+key,data)
    put(root,"business-state/channel-47.offset",b"123\n")
    put(root,c.CHATWOOT_DUMP,b"PGDMP synthetic not native evidence")
    put(root,"redis.rdb",b"REDIS0009 synthetic not native evidence")
    counts=dict.fromkeys(c.TABLES,1)
    evidence={"version":3,"scope":scope,"status":"PASS_OPERATOR_NATIVE_RESTORE",
              "profile_sha256":pin,"counts":counts,"dump_counts":dict(counts),
              "business_offsets":{"47":123},"blobs":[{"key":key,"byte_size":len(data),
                "checksum":base64.b64encode(hashlib.md5(data,usedforsecurity=False).digest()).decode()}],
              "unresolved_attachment_refs":0,"pending_delivery_jobs":0,"ingress_fenced":True,
              "writer_states":{**dict.fromkeys(c.REQUIRED_WRITERS,"stopped"),
                               **dict.fromkeys(c.OPTIONAL_WRITERS,"absent")},
              "other_active_writers":0,"restore_checks":dict.fromkeys(c.OPERATOR_CHECKS,True),
              "restored_outbound_started":False,"source_writers_left_stopped":True}
    db=dict(channels=1,scoped_inboxes=1,outgoing=1,unconfirmed=0,failed=0,
            invalid_status=0,invalid_ack=0,reservations=0)
    redis=dict(queued=0,working=0,retry=0,scheduled=0,maintenance_scheduled=1,dead=0,processes=0)
    put_json(root,c.EVIDENCE,evidence)
    put_json(root,c.OPERATOR_FENCE,dict(source_db=db,restored_db=dict(db),
                                      source_redis=redis,restored_redis=dict(redis)))
    return pin,scope


class OperatorProfileTests(unittest.TestCase):
    def test_valid_structural_v3_and_disjoint_scopes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);pin,scope=fixture(root)
            count=seal(root)
            self.assertEqual(c.verify_operator_capture(root,pin,scope),count)
            for p,s in (("0"*64,scope),(pin,"OPERATOR_PROD")):
                with self.assertRaises(ValueError):
                    c.verify_operator_capture(root,p,s)
            with self.assertRaises((ValueError,KeyError)):
                c.verify_prepared_capture(root)

    def test_resealed_unapproved_data_rejected(self):
        for name in ("config/extra.env","business-state/channel-48.offset","logs/raw.log"):
            with self.subTest(name=name),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);pin,scope=fixture(root)
                put(root,name,b"synthetic")
                seal(root)
                with self.assertRaises(ValueError):
                    c.verify_operator_capture(root,pin,scope)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);pin,scope=fixture(root)
            (root/"foreign").mkdir(mode=0o700);seal(root)
            with self.assertRaises(ValueError):
                c.verify_operator_capture(root,pin,scope)

    def test_delivery_and_writer_fence(self):
        for field in ("unconfirmed","failed","reservations"):
            with self.subTest(field=field),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);pin,scope=fixture(root)
                data=json.loads((root/c.OPERATOR_FENCE).read_text())
                data["source_db"][field]=data["restored_db"][field]=1
                put_json(root,c.OPERATOR_FENCE,data);seal(root)
                with self.assertRaises(ValueError):
                    c.verify_operator_capture(root,pin,scope)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);pin,scope=fixture(root)
            data=json.loads((root/c.EVIDENCE).read_text())
            data["writer_states"]["sidekiq"]="running"
            put_json(root,c.EVIDENCE,data);seal(root)
            with self.assertRaises(ValueError):
                c.verify_operator_capture(root,pin,scope)

    def test_prod_profile_rejects_test_overlay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fixture(root)
            profile=json.loads((root/c.OPERATOR_PROFILE).read_text())
            profile["scope"]="OPERATOR_PROD"
            with self.assertRaises(ValueError):
                c.validate_operator_profile(profile)
            del profile["config_sha256"][c.OPERATOR_TEST]
            self.assertEqual(c.validate_operator_profile(profile)["scope"],"OPERATOR_PROD")

    def test_explicit_smtp_and_bootstrap_config_extensions(self):
        for extension in (set(), {c.OPERATOR_SMTP}, {c.OPERATOR_BOOTSTRAP},
                          set(c.OPERATOR_CONFIG_EXTENSIONS)):
            with self.subTest(extension=extension), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _, scope = fixture(root)
                profile = json.loads((root/c.OPERATOR_PROFILE).read_text())
                for name in extension:
                    profile["config_sha256"][name] = hashlib.sha256(
                        put(root, "config/"+name, b"synthetic operational config").read_bytes()
                    ).hexdigest()
                pin = hashlib.sha256(put_json(root, c.OPERATOR_PROFILE, profile).read_bytes()).hexdigest()
                evidence = json.loads((root/c.EVIDENCE).read_text())
                evidence["profile_sha256"] = pin
                put_json(root, c.EVIDENCE, evidence)
                count = seal(root)
                self.assertEqual(c.verify_operator_capture(root, pin, scope), count)

    def test_unknown_operational_extension_rejected_even_with_resealed_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture(root)
            profile = json.loads((root/c.OPERATOR_PROFILE).read_text())
            profile["config_sha256"][".env.unreviewed"] = "a"*64
            with self.assertRaises(ValueError):
                c.validate_operator_profile(profile)

    def test_declared_extension_cannot_be_missing_or_tampered(self):
        for missing in (True, False):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _, scope = fixture(root)
                profile = json.loads((root/c.OPERATOR_PROFILE).read_text())
                profile["config_sha256"][c.OPERATOR_SMTP] = hashlib.sha256(b"expected").hexdigest()
                pin = hashlib.sha256(put_json(root, c.OPERATOR_PROFILE, profile).read_bytes()).hexdigest()
                evidence = json.loads((root/c.EVIDENCE).read_text())
                evidence["profile_sha256"] = pin
                put_json(root, c.EVIDENCE, evidence)
                if not missing:
                    put(root, "config/"+c.OPERATOR_SMTP, b"modified")
                seal(root)
                with self.assertRaises(ValueError):
                    c.verify_operator_capture(root, pin, scope)
