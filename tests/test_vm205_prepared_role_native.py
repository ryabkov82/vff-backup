#!/usr/bin/env python3
"""Explicit VM205 TEST-only acceptance of the actual rendered prepared role.

Not a unittest fixture or a scheduled backup. No PROD or remote credentials,
no modification of existing Restic repos; all staging is root-private.
"""
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time

RECOVERY=Path("/opt/vff-functional-205/recovery")
ROOT=RECOVERY/"prepared-role-test"
ROLE=RECOVERY/"prepared-role-native.sh"
MODULES=Path("/usr/local/libexec/vff-backup")
STAGE=Path("/var/backups/vff-vm205-prepared")
PG="pgvector/pgvector@sha256:0dd3149a06e4579a0d70d8fef9945636cd514005e7237deb27f9b67886f8dc4f"
def check(ok,code):
    if not ok:raise RuntimeError(code)
def main():
    check(os.geteuid()==0 and socket.gethostname()=="vff-functional-srk-205","VM205_ONLY")
    check(not ROOT.exists() and not ROOT.is_symlink(),"NATIVE_STAGE_EXISTS")
    check(ROLE.is_file() and ROLE.stat().st_uid==0 and not ROLE.stat().st_mode&0o022,"UNSAFE_RENDERED_ROLE")
    check(not list(STAGE.glob("generation-*")),"STALE_PREPARED_GENERATION")
    os.umask(0o077)
    ROOT.mkdir(mode=0o700)
    result={"status":"FAIL_VM205_NATIVE_PREPARED_ROLE","phase":"init"}
    env={k:v for k,v in os.environ.items() if not k.startswith(("AWS_","RESTIC_"))}
    env["LC_ALL"]="C"
    start=time.monotonic()
    def run(args,timeout=60,ok=(0,)):
        p=subprocess.run(args,capture_output=True,env=env,timeout=timeout)
        check(p.returncode in ok,"COMMAND_FAILED_"+Path(args[0]).name.upper().replace("-","_"))
        return p.stdout,p.stderr,p.returncode
    def private(path,data):
        fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,"wb") as f:f.write(data)
    try:
        (ROOT/"metrics").mkdir(mode=0o700)
        secret=ROOT/"password"
        private(secret,(secrets.token_urlsafe(48)+"\n").encode())
        repo=ROOT/"repository"
        private(ROOT/"restic.env",("RESTIC_REPOSITORY="+str(repo)+"\nRESTIC_PASSWORD_FILE="+str(secret)+"\n").encode())
        restic=["restic","-r",str(repo),"-p",str(secret)]
        run(restic+["init"])
        result["phase"]="role"
        # Capture output privately; never print raw Docker/Restic errors.
        proc=subprocess.run([str(ROLE),"vm205-prepared-drill"],capture_output=True,env=env,timeout=480)
        private(ROOT/"role.stdout",proc.stdout);private(ROOT/"role.stderr",proc.stderr)
        if proc.returncode:
            safe=[]
            for line in (proc.stdout+proc.stderr).decode(errors="replace").splitlines():
                try:d=json.loads(line)
                except ValueError:continue
                if isinstance(d,dict) and d.get("status") in ("FAIL_VM205_PREPARED_CAPTURE","FAIL_CHATWOOT_GENERATION"):
                    safe.append({k:v for k,v in d.items() if k in ("status","reason") and isinstance(v,str) and re.fullmatch(r"[A-Za-z0-9_]{1,120}",v)})
            result["safe_diagnostics"]=safe
            raise RuntimeError("PREPARED_ROLE_FAILED")
        match=re.findall(rb"verified prepared snapshot ([0-9a-f]{8,64})",proc.stdout)
        check(len(match)==1,"SNAPSHOT_RECEIPT_MISSING")
        shot=match[0].decode()
        snapshots=json.loads(run(restic+["snapshots","--json"])[0])
        check(len(snapshots)==1 and snapshots[0]["id"].startswith(shot)
              and snapshots[0]["hostname"]==socket.gethostname()
              and snapshots[0]["tags"]==["vm205-prepared-drill"],"SNAPSHOT_SCOPE")
        paths=snapshots[0]["paths"]
        check(len(paths)==1 and Path(paths[0]).parent==STAGE
              and re.fullmatch(r"generation-[A-Za-z0-9]{8}",Path(paths[0]).name),"SNAPSHOT_PATH")
        result["phase"]="restore"
        run(restic+["check","--read-data"],timeout=120)
        restored=ROOT/"restored";restored.mkdir(mode=0o700)
        run(restic+["restore",shot,"--target",str(restored)],timeout=120)
        generation=restored/Path(paths[0]).relative_to("/")
        sys.path.insert(0,str(MODULES))
        import chatwoot_snapshot_contract as contract
        files=contract.verify_prepared_capture(generation)
        receipt=json.loads((generation/contract.V2_RECEIPT).read_text())
        check(receipt["status"]=="PASS_VM205_PREPARED_RESTORE","CAPTURE_RECEIPT_SCOPE")
        # Independent actual native pg_restore list rejects a fake archive;
        # the valid generation already had an actual scratch pg_restore.
        stub=ROOT/"invalid.dump"
        private(stub,b"PGDMP"+b"x"*256)
        def archive_probe(path,success):
            p=subprocess.run(["docker","run","--rm","--pull","never","--network","none",
                "--entrypoint","pg_restore","-v",str(path)+":/probe.dump:ro",
                PG,"--list","/probe.dump"],capture_output=True,env=env,timeout=30)
            check((p.returncode==0)==success,"NATIVE_PG_ARCHIVE_PROBE")
        archive_probe(generation/"source/chatwoot.dump",True)
        archive_probe(stub,False)
        metric_file=ROOT/"metrics/backup_job-vm205-prepared-drill.prom"
        metric=metric_file.read_text()
        check('backup_last_status{backup_job="vm205-prepared-drill"} 0' in metric
              and metric_file.stat().st_mode&0o777==0o644,"METRIC_NOT_SUCCESS")
        check(not list(STAGE.glob("generation-*")),"PREPARED_CLEANUP_INCOMPLETE")
        check(not (RECOVERY/"prepared-writer-fence.json").exists(),"WRITER_FENCE_REMAINS")
        result.update(status="PASS_VM205_NATIVE_PREPARED_ROLE",phase="complete",
                      restored_files=files,restic_integrity=True,snapshot=shot,
                      real_capture_restore=True,real_pg_archive_negative=True,
                      db_counts=receipt["restored_counts"],blobs=receipt["restored_blobs"],
                      prepared_cleanup=True,local_temporary_repo=True,off_host=False,
                      schedule_enabled=False,duration_seconds=round(time.monotonic()-start,2))
    except Exception as e:
        result["reason"]=str(e) if re.fullmatch(r"[A-Z0-9_]{4,120}",str(e)) else type(e).__name__
    finally:
        # Keep only a bounded non-sensitive receipt. No decrypted fixtures
        # are left behind in the role's temporary repository.
        report=RECOVERY/"reports"/("vm205-prepared-role-"+secrets.token_hex(5)+".json")
        private(report,(json.dumps(result,sort_keys=True)+"\n").encode())
        shutil.rmtree(ROOT)
    print(json.dumps(result,sort_keys=True))
    return 0 if result["status"].startswith("PASS") else 1
if __name__=="__main__":
    try:raise SystemExit(main())
    except Exception as e:
        print(json.dumps({"status":"BLOCKED_NATIVE_ROLE","reason":type(e).__name__}))
        raise SystemExit(1)
