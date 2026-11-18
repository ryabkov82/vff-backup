#!/usr/bin/env python3
"""Resume the exact isolated fixture after a transient readiness failure."""
import json
import os
import shutil
from provision_test_s3_vm201 import BASE, PUBLIC_CA, ADDRESS, PORT, NAME, IMAGE_ID, BUCKET, USER, POLICY, scoped_input, run

def main():
    os.umask(0o077)
    if os.geteuid() != 0 or not BASE.is_dir() or PUBLIC_CA.exists():
        raise RuntimeError("unexpected fixture state")
    actual = run(["docker","inspect",NAME,"--format","{{.Image}}"]).stdout.decode().strip()
    if actual != IMAGE_ID:
        raise RuntimeError("image mismatch")
    addresses = json.loads(run(["ip","-j","addr","show"]).stdout)
    if not any(a.get("local")==ADDRESS for link in addresses for a in link["addr_info"]):
        raise RuntimeError("wrong host")
    cmd = ["docker","exec",NAME,"mcli","--config-dir","/bootstrap/mc","--json"]
    run(cmd+["ready","local"])
    run(cmd+["stat",f"local/{BUCKET}"])
    run(cmd+["admin","policy","info","local",POLICY])
    if run(cmd+["admin","user","list","local"]).stdout.strip():
        raise RuntimeError("unexpected existing user")
    password = scoped_input()
    run(["docker","exec","-i",NAME,"mcli","--config-dir","/bootstrap/mc","admin","user","add","local"],
        stdin=f"{USER}\n{password}\n".encode())
    run(cmd+["admin","policy","attach","local",POLICY,"--user",USER])
    run(cmd+["admin","user","info","local",USER])
    shutil.copyfile(BASE/"ca.crt",PUBLIC_CA)
    PUBLIC_CA.chmod(0o644)
    print(json.dumps({"status":"PASS_TEST_S3_PROVISIONED","port":PORT,"tls":True,"scoped_user":True}))
if __name__=="__main__":
    try:
        main()
    except Exception:
        print('{"status":"FAIL_TEST_S3_RESUME","secret_output":false}')
        raise SystemExit(1)
