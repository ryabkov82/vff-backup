#!/usr/bin/env python3
"""One-time real encrypted S3 backup/recovery smoke using systemd credentials."""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import ssl
import subprocess
import tempfile
import urllib.request

REPO = "s3:https://10.10.10.156:19443/vff-chatwoot-test/backup"
CA = Path("/home/aiadmin/vff-chatwoot-test-s3-ca.crt")
CA_SHA = "dd7c2113c324ffc50c7eb7bb0e951fc4df094359cf360058a49dc65c244db844"
USER = "vff-chatwoot-test-backup"

def main():
    os.umask(0o077)
    stage = "preflight"
    try:
        addresses = json.loads(subprocess.check_output(["ip","-j","addr","show"]))
        if os.geteuid()!=0 or not any(a.get("local")=="10.10.10.155" for link in addresses for a in link["addr_info"]):
            raise RuntimeError("wrong TEST host")
        directory = Path(os.environ["CREDENTIALS_DIRECTORY"])
        entries = (directory/"vff-chatwoot-test-restic.env").read_text().splitlines()
        cfg = dict(line.split("=",1) for line in entries)
        keys = {"AWS_ACCESS_KEY_ID","AWS_SECRET_ACCESS_KEY","RESTIC_REPOSITORY","RESTIC_PASSWORD_FILE","RESTIC_CACERT","AWS_DEFAULT_REGION"}
        if len(entries)!=len(keys) or set(cfg)!=keys or cfg["AWS_ACCESS_KEY_ID"]!=USER or cfg["RESTIC_REPOSITORY"]!=REPO or cfg["AWS_DEFAULT_REGION"]!="us-east-1" or not re.fullmatch("[a-f0-9]{64}",cfg["AWS_SECRET_ACCESS_KEY"]):
            raise RuntimeError("wrong TEST credentials")
        password = directory/"vff-chatwoot-test-password"
        if not re.fullmatch("[a-f0-9]{64}",password.read_text().strip()):
            raise RuntimeError("invalid password")
        if hashlib.sha256(CA.read_bytes()).hexdigest()!=CA_SHA:
            raise RuntimeError("CA mismatch")
        stage = "tls"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(CA))))
        with opener.open("https://10.10.10.156:19443/minio/health/ready",timeout=10) as response:
            if response.status!=200:
                raise RuntimeError("S3 not ready")
        with tempfile.TemporaryDirectory(prefix="vff-remote-restic-test-") as tmp:
            root=Path(tmp)
            env={"PATH":"/usr/sbin:/usr/bin:/bin","HOME":tmp,"LC_ALL":"C",
                 "AWS_ACCESS_KEY_ID":cfg["AWS_ACCESS_KEY_ID"],"AWS_SECRET_ACCESS_KEY":cfg["AWS_SECRET_ACCESS_KEY"],
                 "AWS_DEFAULT_REGION":"us-east-1","RESTIC_REPOSITORY":REPO,
                 "RESTIC_PASSWORD_FILE":str(password),"RESTIC_CACHE_DIR":str(root/"cache")}
            def run(*args):
                result=subprocess.run(["restic","--cacert",str(CA),*args],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90)
                if result.returncode:
                    raise RuntimeError("restic operation failed")
                return result.stdout
            stage="init"
            run("init","--repository-version","2")
            stage="backup"
            data=root/"generation"
            data.mkdir(mode=0o700)
            fixture=secrets.token_bytes(4096)
            (data/"synthetic.bin").write_bytes(fixture)
            digest=hashlib.sha256(fixture).hexdigest()
            (data/".vff-snapshot.json").write_text(json.dumps({"version":1,"files":{"synthetic.bin":digest}}))
            tag="chatwoot-remote-synthetic-20261118"
            run("backup","--tag",tag,str(data))
            stage="snapshots"
            snapshots=json.loads(run("snapshots","--json","--tag",tag))
            if len(snapshots)!=1 or not re.fullmatch("[a-f0-9]{64}",snapshots[0]["id"]):
                raise RuntimeError("unexpected snapshot count")
            sid=snapshots[0]["id"]
            stage="integrity"
            run("check","--read-data")
            stage="restore"
            dest=root/"restored"
            run("restore",sid,"--target",str(dest))
            restored=list(dest.rglob("synthetic.bin"))
            if len(restored)!=1 or restored[0].read_bytes()!=fixture or (restored[0].parent/".vff-snapshot.json").read_bytes()!=(data/".vff-snapshot.json").read_bytes():
                raise RuntimeError("restore mismatch")
            evidence={"status":"PASS_REMOTE_RESTIC_SYNTHETIC","snapshot_id":sid,"bytes":len(fixture),"sha256":digest,"tls_verified":True,"restic_check_read_data":True,"restored_exact":True,"synthetic_only":True}
        print(json.dumps(evidence))
    except Exception:
        print(json.dumps({"status":"FAIL_REMOTE_RESTIC_SYNTHETIC","stage":stage,"secret_output":False}))
        raise SystemExit(1)
if __name__=="__main__":
    main()
