#!/usr/bin/env python3
"""Real Restic local filesystem repository smoke, synthetic content only.
Render the existing role on VM110 into a fixed fixture root; run on VM200.
No S3, real backup keys, production config, or service mutations.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import secrets

BASE = Path("/home/aiadmin/vff-prepared-restic-smoke-20261118")
JOB = "chatwoot-synthetic-local"
VERIFIER = BASE / "verify.py"
SCRIPT = BASE / "backup.sh"
CAPTURE = BASE / "capture.py"
FIXTURE = b"VFF synthetic backup fixture; no customer data\n"
PRODUCER = """#!/usr/bin/env python3
import hashlib,json,sys
from pathlib import Path
p=Path(sys.argv[1])
data=b'VFF synthetic backup fixture; no customer data\\n'
(p/'fixture.txt').write_bytes(data)
(p/'.vff-snapshot.json').write_text(json.dumps({'version':1,'files':{'fixture.txt':hashlib.sha256(data).hexdigest()}}))
"""

def run(argv, env=None):
    r=subprocess.run(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,timeout=90)
    if r.returncode:
        raise RuntimeError(f"fixture operation failed: {Path(argv[0]).name}, rc={r.returncode}")
    return r.stdout

def main():
    if len(sys.argv)!=3 or os.geteuid()==0:
        raise RuntimeError("expected unprivileged synthetic smoke and two source scripts")
    BASE.mkdir(mode=0o700)
    try:
        for src,dst in ((Path(sys.argv[1]),SCRIPT),(Path(sys.argv[2]),VERIFIER)):
            shutil.copyfile(src,dst)
            dst.chmod(0o700)
        for name in ("stage","metrics","cache"):
            (BASE/name).mkdir(mode=0o700)
        CAPTURE.write_text(PRODUCER)
        CAPTURE.chmod(0o700)
        with (BASE/"fixture-key").open("x") as f:
            f.write(secrets.token_hex(32)+"\n")
        (BASE/"fixture-key").chmod(0o600)
        cfg=BASE/"fixture.env"
        cfg.write_text(f"RESTIC_REPOSITORY={BASE}/repo\nRESTIC_PASSWORD_FILE={BASE}/fixture-key\nRESTIC_CACHE_DIR={BASE}/cache\n")
        cfg.chmod(0o600)
        env={"PATH":"/usr/local/bin:/usr/bin:/bin","HOME":str(BASE),"LC_ALL":"C",
            "RESTIC_REPOSITORY":str(BASE/"repo"),"RESTIC_PASSWORD_FILE":str(BASE/"fixture-key"),
            "RESTIC_CACHE_DIR":str(BASE/"cache")}
        run(["restic","init","--repository-version","2"],env)
        run(["bash",str(SCRIPT),JOB],env)
        snap=json.loads(run(["restic","snapshots","--json","--tag",JOB],env))
        if len(snap)!=1 or not snap[0].get("id"):raise RuntimeError("unexpected snapshot count")
        sid=snap[0]["id"]
        run(["restic","check","--read-data"],env)
        dest=BASE/"restored"
        run(["restic","restore",sid,"--target",str(dest)],env)
        files=list(dest.rglob("fixture.txt"))
        if len(files)!=1 or files[0].read_bytes()!=FIXTURE:
            raise RuntimeError("restored fixture mismatch")
        run([str(VERIFIER),str(files[0].parent)],env)
        if list((BASE/"stage").glob("generation-*")):
            raise RuntimeError("temporary stage not cleaned")
        metrics=(BASE/"metrics/backup_metrics.prom").read_text()
        if f'backup_last_status{{backup_job="{JOB}"}} 0' not in metrics:
            raise RuntimeError("success metric absent")
        print(json.dumps({"status":"PASS_REAL_LOCAL_RESTIC_PREPARED","snapshot_count":len(snap),
            "restic_check_read_data":True,"restored_manifest_verified":True,
            "sha256":hashlib.sha256(FIXTURE).hexdigest(),"stage_cleaned":True,
            "metric_success":True,"remote_s3":False,"synthetic_only":True}))
    finally:
        shutil.rmtree(BASE)
if __name__=="__main__":
    main()
