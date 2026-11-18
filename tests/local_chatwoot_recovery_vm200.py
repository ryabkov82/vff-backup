#!/usr/bin/env python3
"""One-time local recovery exercise of an existing isolated TEST fixture.

Uses the standard Restic binary; no S3, live service interruption, external
credential, or new production backup implementation. Only the historical
TEST fixture is copied to the temporary local repository. Configuration files
are checked for existence/private modes; their contents are not copied.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import tempfile

ROOT = Path("/opt/vff-chatwoot")
SOURCE = ROOT / "acceptance-backup-7383e2fa23"
PG = "vff-chatwoot-postgres-1"
TABLES = dict(zip(("accounts", "inboxes", "contacts", "conversations", "messages",
                   "attachments", "active_storage_blobs", "active_storage_attachments"),
                  (1, 3, 26, 225, 685, 41, 41, 41)))


def run(args, env=None):
    result = subprocess.run(args, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=120)
    if result.returncode:
        raise RuntimeError("local recovery command failed")
    return result.stdout.decode().strip()


def docker(*args):
    return run(["docker", *args])


def query(db, sql):
    return docker("exec", PG, "psql", "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1",
                  "-U", "chatwoot", "-d", db, "-c", sql)


def private(path, directory=False):
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise ValueError("unsafe fixture root or config")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def tree(root):
    def fail(error):
        raise error
    if not root.is_dir() or root.is_symlink():
        raise ValueError("missing storage")
    result = {}
    for parent, dirs, names in os.walk(root, followlinks=False, onerror=fail):
        for name in dirs:
            if not stat.S_ISDIR((Path(parent) / name).lstat().st_mode):
                raise ValueError("unsupported directory")
        for name in names:
            file = Path(parent) / name
            info = file.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("unsupported file")
            result[file.relative_to(root).as_posix()] = (info.st_size, digest(file))
    return result


def verify_db(dump, storage):
    nonce = secrets.token_hex(6)
    db = "vff_accept_restore_" + nonce
    remote = "/tmp/vff-local-recovery-" + nonce + ".dump"
    if query("chatwoot", "SELECT 1 FROM pg_database WHERE datname='" + db + "'"):
        raise ValueError("scratch collision")
    created = False
    try:
        docker("exec", PG, "createdb", "-U", "chatwoot", db)
        created = True
        docker("cp", str(dump), PG + ":" + remote)
        docker("exec", PG, "pg_restore", "--exit-on-error", "-U", "chatwoot",
               "--no-owner", "--no-acl", "-d", db, remote)
        actual = {name: int(query(db, "SELECT count(*) FROM " + name))
                  for name in TABLES}
        if actual != TABLES or query(db, "SELECT name FROM accounts ORDER BY id") != "VFF":
            raise ValueError("historical snapshot count mismatch")
        verified = 0
        rows = query(db, "SELECT key,checksum,byte_size FROM active_storage_blobs ORDER BY id")
        for row in rows.splitlines():
            key, checksum, size = row.split("|")
            if not re.fullmatch(r"[A-Za-z0-9]{20,128}", key) or not size.isdecimal():
                raise ValueError("invalid blob metadata")
            path = storage / key[:2] / key[2:4] / key
            if not path.is_file() or path.is_symlink() or path.stat().st_size != int(size):
                raise ValueError("blob missing or size mismatch")
            with path.open("rb") as stream:
                md = hashlib.md5(usedforsecurity=False)
                for part in iter(lambda: stream.read(1024 * 1024), b""):
                    md.update(part)
            if base64.b64encode(md.digest()).decode() != checksum:
                raise ValueError("blob checksum mismatch")
            verified += 1
        if verified != TABLES["active_storage_blobs"]:
            raise ValueError("blob count mismatch")
        return actual, verified
    finally:
        try:
            docker("exec", PG, "rm", "-f", remote)
        finally:
            if created:
                docker("exec", PG, "dropdb", "-U", "chatwoot", db)
            if query("chatwoot", "SELECT 1 FROM pg_database WHERE datname='" + db + "'"):
                raise RuntimeError("scratch cleanup incomplete")


def main():
    os.umask(0o077)
    stage = "preflight"
    try:
        if os.geteuid() != 0 or "VFF Chatwoot Community Edition (test environment)" not in (ROOT / "compose.yaml").read_text():
            raise RuntimeError("wrong TEST deployment")
        addresses = json.loads(run(["ip", "-j", "addr", "show"]))
        if not any(a.get("local") == "10.10.10.155" for interface in addresses
                   for a in interface["addr_info"]):
            raise RuntimeError("wrong TEST host")
        if query("chatwoot", "SELECT name FROM accounts ORDER BY id") != "VFF":
            raise RuntimeError("wrong TEST database")
        private(SOURCE, directory=True)
        private(SOURCE / "chatwoot.dump")
        private(SOURCE / "config", directory=True)
        for name in (".env", "compose.yaml", ".env.web-tools"):
            private(SOURCE / "config" / name)
        original = tree(SOURCE / "storage")
        original_dump = digest(SOURCE / "chatwoot.dump")
        if not original:
            raise ValueError("empty historical fixture")
        with tempfile.TemporaryDirectory(prefix="vff-local-recovery-") as tmp:
            root = Path(tmp)
            password = root / "local-password"
            password.write_text(secrets.token_hex(32))
            env = {"PATH": "/usr/sbin:/usr/bin:/bin", "HOME": tmp, "LC_ALL": "C"}
            def restic(*args):
                return run(["restic", "--no-cache", "-r", str(root / "repo"),
                            "-p", str(password), *args], env)
            stage = "local-backup"
            restic("init")
            restic("backup", str(SOURCE / "chatwoot.dump"), str(SOURCE / "storage"))
            stage = "integrity"
            restic("check", "--read-data")
            snapshots = json.loads(restic("snapshots", "--json"))
            if len(snapshots) != 1:
                raise ValueError("unexpected snapshot count")
            stage = "restore"
            dest = root / "restored"
            restic("restore", snapshots[0]["id"], "--target", str(dest))
            recovered = dest / SOURCE.relative_to("/")
            if digest(recovered / "chatwoot.dump") != original_dump or tree(recovered / "storage") != original:
                raise ValueError("restored bytes differ")
            stage = "database"
            counts, verified = verify_db(recovered / "chatwoot.dump", recovered / "storage")
        print(json.dumps({"status": "PASS_LOCAL_CHATWOOT_RESTIC_RECOVERY",
                          "tables": counts, "verified_blobs": verified,
                          "storage_files": len(original),
                          "storage_bytes": sum(x[0] for x in original.values()),
                          "restic_check_read_data": True, "restored_bytes_equal": True,
                          "scratch_removed": True, "config_private_present": 3,
                          "config_restored": False, "remote_used": False}))
    except Exception:
        print(json.dumps({"status": "FAIL_LOCAL_CHATWOOT_RESTIC_RECOVERY", "stage": stage}))
        raise SystemExit(1)

if __name__ == "__main__":
    main()
