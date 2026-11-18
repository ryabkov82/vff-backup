#!/usr/bin/env python3
"""One-time isolated S3 TEST server provisioning on VM201; no secret output."""
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import time

BASE = Path("/opt/vff-chatwoot-backup-test")
INPUT = Path("/home/aiadmin/vff-chatwoot-test-s3.env")
PUBLIC_CA = Path("/home/aiadmin/vff-chatwoot-test-s3-ca.crt")
ADDRESS = "10.10.10.156"
PORT = 19443
NAME = "vff-chatwoot-test-s3"
IMAGE = "pgsty/silo:RELEASE.2026-11-09T00-00-00Z"
IMAGE_ID = "sha256:08ce1505b5eacefbad1ca07159b242b9ce045a11c2817e3bc257b25f77bbc02d"
BUCKET = "vff-chatwoot-test"
USER = "vff-chatwoot-test-backup"
POLICY = "chatwoot-test-backup"

def run(args, *, stdin=None, timeout=60, allow_failure=False):
    result = subprocess.run(args, input=stdin, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode and not allow_failure:
        raise RuntimeError(f"{args[0]} operation failed, rc={result.returncode}")
    return result

def write_private(path, content):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as out:
        out.write(content)

def scoped_input():
    info = INPUT.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_nlink != 1:
        raise RuntimeError("unsafe staged TEST input")
    lines = INPUT.read_text().splitlines()
    entries = dict(line.split("=", 1) for line in lines)
    if len(lines) != 2 or set(entries) != {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"} or entries["AWS_ACCESS_KEY_ID"] != USER or not re.fullmatch("[0-9a-f]{64}", entries["AWS_SECRET_ACCESS_KEY"]):
        raise RuntimeError("invalid scoped TEST input")
    return entries["AWS_SECRET_ACCESS_KEY"]

def main():
    os.umask(0o077)
    if os.geteuid() != 0:
        raise RuntimeError("root required for TEST provisioning")
    addresses = json.loads(run(["ip", "-j", "addr", "show"]).stdout)
    if not any(a.get("local") == ADDRESS for link in addresses for a in link["addr_info"]):
        raise RuntimeError("wrong TEST host")
    if BASE.exists() or PUBLIC_CA.exists():
        raise RuntimeError("existing TEST state; inspect before retry")
    actual = run(["docker", "image", "inspect", IMAGE, "--format", "{{.Id}}"]).stdout.decode().strip()
    if actual != IMAGE_ID:
        raise RuntimeError("image pin mismatch")
    services = run(["docker", "ps", "-a", "--format", "{{.Names}}"]).stdout.decode().splitlines()
    if NAME in services:
        raise RuntimeError("existing TEST container")
    if shutil.disk_usage("/opt").free < 10 * 1024**3:
        raise RuntimeError("insufficient disk")
    client_secret = scoped_input()
    BASE.mkdir(mode=0o700)
    certs = BASE / "certs"
    mc = BASE / "mc"
    for path in (certs, BASE / "data", mc / "certs" / "CAs"):
        path.mkdir(mode=0o700, parents=True)
    ca_key, ca = BASE / "ca.key", BASE / "ca.crt"
    run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "30",
        "-subj", "/CN=VFF isolated TEST S3 CA", "-addext", "basicConstraints=critical,CA:TRUE",
        "-keyout", str(ca_key), "-out", str(ca)])
    run(["openssl", "req", "-new", "-newkey", "rsa:2048", "-nodes",
        "-subj", "/CN=VFF TEST S3", "-keyout", str(certs / "private.key"),
        "-out", str(BASE / "server.csr")])
    ext = BASE / "server.ext"
    write_private(ext, f"basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=IP:{ADDRESS},IP:127.0.0.1\n")
    run(["openssl", "x509", "-req", "-in", str(BASE / "server.csr"), "-CA", str(ca),
        "-CAkey", str(ca_key), "-CAcreateserial", "-days", "30", "-sha256",
        "-extfile", str(ext), "-out", str(certs / "public.crt")])
    shutil.copyfile(ca, mc / "certs" / "CAs" / "test-ca.crt")
    admin = "vff-chatwoot-test-admin"
    root_secret = secrets.token_hex(32)
    root_env = BASE / "server.env"
    write_private(root_env, f"MINIO_ROOT_USER={admin}\nMINIO_ROOT_PASSWORD={root_secret}\nMINIO_BROWSER=off\n")
    write_private(mc / "config.json", json.dumps({"version":"10","aliases":{"local":{"url":"https://127.0.0.1:9000","accessKey":admin,"secretKey":root_secret,"api":"S3v4","path":"on"}}}))
    policy = {"Version":"2012-10-17","Statement":[
        {"Effect":"Allow","Action":["s3:ListBucket","s3:GetBucketLocation","s3:ListBucketMultipartUploads"],"Resource":[f"arn:aws:s3:::{BUCKET}"]},
        {"Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:DeleteObject","s3:AbortMultipartUpload","s3:ListMultipartUploadParts"],"Resource":[f"arn:aws:s3:::{BUCKET}/backup/*"]}
    ]}
    write_private(BASE / "policy.json", json.dumps(policy))
    started = False
    try:
        run(["docker", "run", "-d", "--pull", "never", "--name", NAME,
            "--restart", "unless-stopped", "--memory", "1024m", "--cpus", "1",
            "--pids-limit", "256", "--log-opt", "max-size=10m", "--log-opt", "max-file=3",
            "-p", f"{ADDRESS}:{PORT}:9000", "--env-file", str(root_env),
            "-v", f"{BASE / 'data'}:/data", "-v", f"{certs}:/certs:ro",
            "-v", f"{mc}:/bootstrap/mc", "-v", f"{BASE / 'policy.json'}:/bootstrap/policy.json:ro",
            IMAGE, "server", "/data", "--certs-dir", "/certs", "--console-address", ":9001"])
        started = True
        cmd = ["docker", "exec", NAME, "mcli", "--config-dir", "/bootstrap/mc", "--json"]
        for _ in range(20):
            if not run(cmd + ["ready", "local"], timeout=5, allow_failure=True).returncode:
                break
            time.sleep(1)
        else:
            raise RuntimeError("TLS S3 not ready")
        run(cmd + ["mb", f"local/{BUCKET}"])
        run(cmd + ["admin", "policy", "create", "local", POLICY, "/bootstrap/policy.json"])
        # mcli supports piped keys; no key in process arguments or returned stdout.
        run(["docker", "exec", "-i", NAME, "mcli", "--config-dir", "/bootstrap/mc",
             "admin", "user", "add", "local"], stdin=f"{USER}\n{client_secret}\n".encode())
        run(cmd + ["admin", "policy", "attach", "local", POLICY, "--user", USER])
        run(cmd + ["admin", "user", "info", "local", USER])
        shutil.copyfile(ca, PUBLIC_CA)
        PUBLIC_CA.chmod(0o644)
        print(json.dumps({"status":"PASS_TEST_S3_PROVISIONED","tls":True,
                         "container":NAME,"port":PORT,"bucket":BUCKET,
                         "separate_admin":True,"client_policy":POLICY,"public_ca":str(PUBLIC_CA)}))
    except Exception:
        if started:
            run(["docker","stop",NAME], allow_failure=True)
        raise RuntimeError("TEST S3 provisioning failed; private state retained, inspect before retry") from None

if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
        print('{"status":"FAIL_TEST_S3_PROVISIONING","secret_output":false}')
        raise SystemExit(1)
