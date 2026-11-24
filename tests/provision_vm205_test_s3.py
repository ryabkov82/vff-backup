#!/usr/bin/env python3
"""Use existing VM201 VFF-only Silo, with a separate VM205 backup namespace.

The only transferred credential material is an RSA-OAEP SHA256 envelope
encrypted to a fresh VM205 public key over the authenticated management path.
Existing VM200 bucket, credentials, container and application data are untouched.
The server-side root-private recovery copy remains available if VM205 is lost.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys

SERVER_IP = "10.10.10.156"
CLIENT_IP = "10.10.10.238"
PORT = 19443
NAME = "vff-chatwoot-test-s3"
IMAGE_ID = "sha256:08ce1505b5eacefbad1ca07159b242b9ce045a11c2817e3bc257b25f77bbc02d"
CA_SHA = "dd7c2113c324ffc50c7eb7bb0e951fc4df094359cf360058a49dc65c244db844"
SERVER = Path("/opt/vff-chatwoot-backup-test")
RECOVERY = SERVER / "vm205-recovery"
CLIENT = Path("/opt/vff-functional-205/recovery/s3-test-client")
EXPORT = Path("/home/aiadmin")
PUB = "vff-vm205-backup-recipient.pem"
ENVELOPE = "vff-vm205-backup-envelope.bin"
BUCKET = "vff-chatwoot-vm205-test"
USER = "vff-vm205-test-backup"
POLICY = "vff-vm205-test-backup"
REPO = f"s3:https://{SERVER_IP}:{PORT}/{BUCKET}/backup"
OLD_REPO = f"s3:https://{SERVER_IP}:{PORT}/vff-chatwoot-test/backup"


def check(ok, code):
    if not ok:
        raise RuntimeError(code)


def run(args, *, data=None, timeout=60, env=None, allow_failure=False):
    r = subprocess.run(args, input=data, capture_output=True, timeout=timeout, env=env)
    if not allow_failure:
        check(r.returncode == 0, "COMMAND_FAILED_" + Path(args[0]).name.upper())
    return r


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def host(ip):
    check(os.geteuid() == 0, "ROOT_REQUIRED")
    addrs = json.loads(run(["ip", "-j", "addr", "show"]).stdout)
    check(any(a.get("local") == ip for link in addrs for a in link["addr_info"]), "WRONG_HOST")


def private_dir(path):
    i = path.lstat()
    check(stat.S_ISDIR(i.st_mode) and i.st_uid == 0 and stat.S_IMODE(i.st_mode) == 0o700,
          "UNSAFE_PRIVATE_DIRECTORY")


def private_file(path):
    i = path.lstat()
    check(stat.S_ISREG(i.st_mode) and i.st_uid == 0 and i.st_nlink == 1
          and stat.S_IMODE(i.st_mode) == 0o600, "UNSAFE_PRIVATE_FILE")


def write(path, data, mode=0o600):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "wb") as f:
        os.fchmod(f.fileno(), mode)
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def check_secret_pair(data):
    check(re.fullmatch(rb"[0-9a-f]{64}\n[0-9a-f]{64}\n", data) is not None,
          "INVALID_CREDENTIAL_ENVELOPE")
    return data.decode().splitlines()


def save_credentials(directory, data, ca):
    key, password = check_secret_pair(data)
    write(directory / "restic.pass", (password + "\n").encode())
    write(directory / "s3.env", (
        f"AWS_ACCESS_KEY_ID={USER}\nAWS_SECRET_ACCESS_KEY={key}\n"
        f"AWS_DEFAULT_REGION=us-east-1\nRESTIC_REPOSITORY={REPO}\n"
        f"RESTIC_PASSWORD_FILE={directory / 'restic.pass'}\n"
        f"RESTIC_CACERT={directory / 'ca.crt'}\n").encode())
    write(directory / "ca.crt", ca)
    for path in ("restic.pass", "s3.env", "ca.crt"):
        private_file(directory / path)


def prepare_client():
    host(CLIENT_IP)
    private_dir(CLIENT.parent)
    check(not CLIENT.exists() and not (EXPORT / PUB).exists(), "CLIENT_ALREADY_PREPARED")
    CLIENT.mkdir(mode=0o700)
    key = CLIENT / "recipient.key"
    run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:3072",
         "-out", str(key)], timeout=60)
    key.chmod(0o600)
    private_file(key)
    public = run(["openssl", "pkey", "-in", str(key), "-pubout"]).stdout
    write(EXPORT / PUB, public, 0o644)
    print(json.dumps({"status":"PASS_VM205_RECIPIENT", "public_sha256":sha(EXPORT / PUB)}))


def admin(*args, data=None, allow_failure=False):
    cmd = ["docker", "exec"]
    if data is not None:
        cmd += ["-i"]
    cmd += [NAME, "mcli", "--config-dir", "/bootstrap/mc", "--json"]
    return run(cmd + list(args), data=data, allow_failure=allow_failure)


def admin_rows(*args):
    return [json.loads(x) for x in admin(*args).stdout.splitlines() if x.strip()]


def create_storage():
    host(SERVER_IP)
    private_dir(SERVER)
    private_dir(SERVER / "mc")
    private_file(SERVER / "mc/config.json")
    ca = SERVER / "ca.crt"
    check(sha(ca) == CA_SHA, "TEST_CA_DRIFT")
    before = json.loads(run(["docker", "inspect", NAME]).stdout)[0]
    check(before["Image"] == IMAGE_ID and before["State"]["Running"]
          and before["HostConfig"]["PortBindings"] == {
              "9000/tcp":[{"HostIp":SERVER_IP,"HostPort":str(PORT)}]}, "WRONG_TEST_S3")
    users = admin_rows("admin", "user", "list", "local")
    buckets = admin_rows("ls", "local")
    check(all(row.get("accessKey") != USER for row in users)
          and all(row.get("key", "").rstrip("/") != BUCKET for row in buckets),
          "NAMESPACE_ALREADY_EXISTS")
    check(not RECOVERY.exists() and not (EXPORT / ENVELOPE).exists(), "RECOVERY_ALREADY_EXISTS")
    check(shutil.disk_usage(SERVER).free > 5 * 1024**3, "INSUFFICIENT_STORAGE")
    # Generated independently, never print or place in command arguments.
    data = (secrets.token_hex(32) + "\n" + secrets.token_hex(32) + "\n").encode()
    key, _password = check_secret_pair(data)
    RECOVERY.mkdir(mode=0o700)
    save_credentials(RECOVERY, data, ca.read_bytes())
    policy = {"Version":"2012-10-17","Statement":[
        {"Effect":"Allow","Action":["s3:ListBucket","s3:GetBucketLocation",
                                   "s3:ListBucketMultipartUploads"],
         "Resource":["arn:aws:s3:::"+BUCKET]},
        {"Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:DeleteObject",
                                   "s3:AbortMultipartUpload","s3:ListMultipartUploadParts"],
         "Resource":["arn:aws:s3:::"+BUCKET+"/backup/*"]}]}
    policy_file = SERVER / "mc" / (POLICY + ".json")
    write(policy_file, json.dumps(policy,sort_keys=True).encode())
    # Only newly named resources. No restart and no changes to old identities.
    admin("mb", "local/" + BUCKET)
    admin("admin", "policy", "create", "local", POLICY, "/bootstrap/mc/" + policy_file.name)
    admin("admin", "user", "add", "local", data=(USER+"\n"+key+"\n").encode())
    admin("admin", "policy", "attach", "local", POLICY, "--user", USER)
    info = admin_rows("admin", "user", "info", "local", USER)
    check(any(row.get("status") == "success" and row.get("policyName") == POLICY
              for row in info), "SCOPED_USER_READBACK")
    after = json.loads(run(["docker", "inspect", NAME]).stdout)[0]
    check(before["Id"] == after["Id"] and before["State"]["StartedAt"] == after["State"]["StartedAt"],
          "STORAGE_CONTAINER_CHANGED")
    print(json.dumps({"status":"PASS_VM205_S3_NAMESPACE", "bucket":BUCKET,
                      "separate_policy":True,"existing_container_unchanged":True,
                      "recovery_copy_private":True}))


def export_client(expected_pub_sha):
    host(SERVER_IP)
    private_dir(RECOVERY)
    private_file(RECOVERY / "s3.env")
    private_file(RECOVERY / "restic.pass")
    public = Path("/tmp") / PUB
    check(re.fullmatch("[0-9a-f]{64}", expected_pub_sha)
          and public.is_file() and not public.is_symlink()
          and sha(public) == expected_pub_sha, "RECIPIENT_KEY_DRIFT")
    check(not (EXPORT / ENVELOPE).exists(), "ENVELOPE_ALREADY_EXPORTED")
    cfg = dict(line.split("=",1) for line in (RECOVERY/"s3.env").read_text().splitlines())
    check(cfg.get("AWS_ACCESS_KEY_ID")==USER and cfg.get("RESTIC_REPOSITORY")==REPO,
          "RECOVERY_SCOPE_DRIFT")
    data = (cfg["AWS_SECRET_ACCESS_KEY"]+"\n"+(RECOVERY/"restic.pass").read_text()).encode()
    check_secret_pair(data)
    encrypted = run(["openssl", "pkeyutl", "-encrypt", "-pubin", "-inkey", str(public),
                     "-pkeyopt", "rsa_padding_mode:oaep", "-pkeyopt", "rsa_oaep_md:sha256"],
                    data=data).stdout
    check(len(encrypted) == 384, "CREDENTIAL_ENVELOPE_SIZE")
    write(EXPORT / ENVELOPE, encrypted, 0o644)
    print(json.dumps({"status":"PASS_SCOPED_ENVELOPE_EXPORTED",
                      "encrypted_sha256":sha(EXPORT / ENVELOPE),"secret_output":False}))


def install_client(expected_sha):
    host(CLIENT_IP)
    private_dir(CLIENT)
    key = CLIENT / "recipient.key"
    private_file(key)
    encrypted = Path("/tmp") / ENVELOPE
    ca = Path("/tmp/vff-chatwoot-test-s3-ca.crt")
    check(re.fullmatch("[0-9a-f]{64}", expected_sha)
          and encrypted.is_file() and not encrypted.is_symlink()
          and sha(encrypted) == expected_sha and ca.is_file() and not ca.is_symlink()
          and sha(ca) == CA_SHA, "TRANSFER_DIGEST_DRIFT")
    check(not (CLIENT / "s3.env").exists(), "CLIENT_ALREADY_INSTALLED")
    payload = run(["openssl", "pkeyutl", "-decrypt", "-inkey", str(key),
                   "-pkeyopt", "rsa_padding_mode:oaep", "-pkeyopt", "rsa_oaep_md:sha256"],
                  data=encrypted.read_bytes()).stdout
    save_credentials(CLIENT, payload, ca.read_bytes())
    # Plaintext never passed over the management channel; remove the one-time key.
    key.unlink()
    encrypted.unlink()
    (EXPORT / PUB).unlink()
    print(json.dumps({"status":"PASS_VM205_SCOPED_CREDENTIALS", "secret_exported":False,
                      "one_time_key_removed":True, "ca_sha256":CA_SHA}))


if __name__ == "__main__":
    os.umask(0o077)
    try:
        if sys.argv[1:] == ["prepare-client"]:
            prepare_client()
        elif sys.argv[1:] == ["create-storage"]:
            create_storage()
        elif len(sys.argv) == 3 and sys.argv[1] == "export-client":
            export_client(sys.argv[2])
        elif len(sys.argv) == 3 and sys.argv[1] == "install-client":
            install_client(sys.argv[2])
        else:
            raise RuntimeError("INVALID_MODE")
    except Exception as exc:
        code = str(exc) if re.fullmatch(r"[A-Z0-9_]{4,100}", str(exc)) else type(exc).__name__
        print(json.dumps({"status":"FAIL_VM205_S3_SETUP","reason":code,"secret_output":False}))
        raise SystemExit(1)
