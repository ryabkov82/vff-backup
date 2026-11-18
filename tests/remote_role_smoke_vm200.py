#!/usr/bin/env python3
"""Exercise actual rendered backup role against the existing isolated S3 repo."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from remote_restic_smoke_vm200 import CA, CA_SHA, REPO, USER
BASE=Path("/var/tmp/vff-chatwoot-remote-role-smoke-20261118")
JOB="chatwoot-remote-role-synthetic-20261118"
FIXTURE=b"VFF synthetic role smoke; no real Chatwoot data\n"
PRODUCER="""#!/usr/bin/env python3
import hashlib,json,os,sys
from pathlib import Path
if os.environ.get('VFF_TEST_CAPTURE_FAIL') == '1':
    raise SystemExit(9)
p=Path(sys.argv[1])
data=b'VFF synthetic role smoke; no real Chatwoot data\\n'
(p/'fixture.txt').write_bytes(data)
(p/'.vff-snapshot.json').write_text(json.dumps({'version':1,'files':{'fixture.txt':hashlib.sha256(data).hexdigest()}}))
"""

def main():
    os.umask(0o077)
    phase="preflight"
    created=False
    try:
        addresses=json.loads(subprocess.check_output(["ip","-j","addr","show"]))
        if os.geteuid()!=0 or not any(a.get("local")=="10.10.10.155" for link in addresses for a in link["addr_info"]):
            raise RuntimeError("wrong TEST host")
        directory=Path(os.environ["CREDENTIALS_DIRECTORY"])
        cfg=dict(line.split("=",1) for line in (directory/"vff-chatwoot-test-restic.env").read_text().splitlines())
        password=directory/"vff-chatwoot-test-password"
        if cfg["AWS_ACCESS_KEY_ID"]!=USER or cfg["RESTIC_REPOSITORY"]!=REPO or not re.fullmatch("[a-f0-9]{64}",cfg["AWS_SECRET_ACCESS_KEY"]) or not re.fullmatch("[a-f0-9]{64}",password.read_text().strip()) or hashlib.sha256(CA.read_bytes()).hexdigest()!=CA_SHA:
            raise RuntimeError("wrong TEST configuration")
        BASE.mkdir(mode=0o700)
        created=True
        for name in ("stage","metrics","cache"):
            (BASE/name).mkdir(mode=0o700)
        for src,dst in (("remote_role_rendered.sh","backup.sh"),("verify_prepared_snapshot.py","verify.py")):
            shutil.copyfile(Path(__file__).parent/src,BASE/dst)
            (BASE/dst).chmod(0o700)
        (BASE/"capture.py").write_text(PRODUCER)
        (BASE/"capture.py").chmod(0o700)
        env={"PATH":"/usr/sbin:/usr/bin:/bin","HOME":str(BASE),"LC_ALL":"C",
             "AWS_ACCESS_KEY_ID":USER,"AWS_SECRET_ACCESS_KEY":cfg["AWS_SECRET_ACCESS_KEY"],
             "RESTIC_REPOSITORY":REPO,"RESTIC_PASSWORD_FILE":str(password),"RESTIC_CACERT":str(CA),
             "RESTIC_CACHE_DIR":str(BASE/"cache"),"AWS_DEFAULT_REGION":"us-east-1"}
        def run(args, variables=env, expect_success=True):
            r=subprocess.run(args,env=variables,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90)
            if (r.returncode==0)!=expect_success:
                raise RuntimeError("unexpected operation status")
            return r.stdout
        def snapshots():
            return {x["id"]:x for x in json.loads(run(["restic","snapshots","--json"]))}
        before=snapshots()
        if len(before)!=1 or any(JOB in x.get("tags",[]) for x in before.values()):
            raise RuntimeError("unexpected repository contents")
        phase="role-success"
        run(["bash",str(BASE/"backup.sh"),JOB])
        after=snapshots()
        if not set(before).issubset(after) or len(after)!=len(before)+1:
            raise RuntimeError("unrelated snapshot changed")
        sid=(set(after)-set(before)).pop()
        if JOB not in after[sid].get("tags",[]):
            raise RuntimeError("tag mismatch")
        metric=BASE/"metrics/backup_metrics.prom"
        if f'backup_last_status{{backup_job="{JOB}"}} 0' not in metric.read_text() or list((BASE/"stage").glob("generation-*")):
            raise RuntimeError("success metric or cleanup failed")
        phase="role-restore"
        run(["restic","check","--read-data"])
        restore=BASE/"restored"
        run(["restic","restore",sid,"--target",str(restore)])
        files=list(restore.rglob("fixture.txt"))
        if len(files)!=1 or files[0].read_bytes()!=FIXTURE:
            raise RuntimeError("restored data mismatch")
        run([str(BASE/"verify.py"),str(files[0].parent)])
        phase="role-failure"
        failed_env=dict(env,VFF_TEST_CAPTURE_FAIL="1")
        run(["bash",str(BASE/"backup.sh"),JOB],failed_env,False)
        if set(snapshots())!=set(after) or f'backup_last_status{{backup_job="{JOB}"}} 1' not in metric.read_text() or list((BASE/"stage").glob("generation-*")):
            raise RuntimeError("failure isolation or metric failed")
        print(json.dumps({"status":"PASS_REMOTE_ROLE_SYNTHETIC","snapshot_id":sid,"snapshots_before":len(before),"snapshots_after":len(after),"restored_manifest":True,"success_and_failure_metrics":True,"failed_capture_no_snapshot":True,"prior_snapshot_retained":True}))
    except Exception:
        print(json.dumps({"status":"FAIL_REMOTE_ROLE_SYNTHETIC","phase":phase,"secret_output":False}))
        raise SystemExit(1)
    finally:
        if created:
            shutil.rmtree(BASE)
if __name__=="__main__":
    main()
