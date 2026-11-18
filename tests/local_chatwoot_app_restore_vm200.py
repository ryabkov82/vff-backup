#!/usr/bin/env python3
"""One-time isolated application smoke from the saved TEST fixture.

No original secret config is loaded, no published ports, no external network,
no Sidekiq/poller/AI, no changes to the running source deployment.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import time

from local_chatwoot_recovery_vm200 import ROOT, SOURCE, TABLES, private, tree, digest

PG = "sha256:0dd3149a06e4579a0d70d8fef9945636cd514005e7237deb27f9b67886f8dc4f"
REDIS = "sha256:c5d34a98396a4775e223c74e929162c35c5a2182901ba26b50ade939941ce14b"
RAILS = "sha256:04895c7bd0550d9e74b45b7202787b56f7ee4e891ebbde0c429c8c32901b97cf"
ID = re.compile(r"[0-9a-f]{64}")
DATA_CHECK = """require 'json'; require 'digest'; require 'base64'
counts = {accounts: Account.count, inboxes: Inbox.count, contacts: Contact.count,
conversations: Conversation.count, messages: Message.count, attachments: Attachment.count,
active_storage_blobs: ActiveStorage::Blob.count,
active_storage_attachments: ActiveStorage::Attachment.count}
raise 'wrong account' unless Account.count == 1 && Account.first.name == 'VFF'
verified = 0
ActiveStorage::Blob.find_each do |b|
  bytes = b.download
  raise 'invalid attachment' unless bytes.bytesize == b.byte_size &&
    Base64.strict_encode64(Digest::MD5.digest(bytes)) == b.checksum
  verified += 1
end
puts 'VFF_RECOVERY_RESULT=' + JSON.generate({counts: counts, verified: verified})
"""


def run(args, *, env=None, timeout=45, allow_fail=False):
    r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       env=env, timeout=timeout)
    if r.returncode and not allow_fail:
        raise RuntimeError("command failed")
    return r


def docker(*args, **kw):
    return run(["docker", *args], **kw)


def out(result):
    return result.stdout.decode().strip()


def wait_for(fn, seconds=45):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if fn():
            return
        time.sleep(1)
    raise RuntimeError("service readiness timeout")


def main():
    os.umask(0o077)
    stage = "preflight"
    network = ""
    containers = []
    network_id = ""
    receipt = None
    try:
        if os.geteuid() != 0 or "VFF Chatwoot Community Edition (test environment)" not in (ROOT / "compose.yaml").read_text():
            raise ValueError("not TEST")
        addresses = json.loads(out(run(["ip", "-j", "addr", "show"])))
        if not any(a.get("local") == "10.10.10.155" for interface in addresses
                   for a in interface["addr_info"]):
            raise ValueError("wrong host")
        if shutil.disk_usage("/").free < 5 * 1024**3:
            raise ValueError("disk capacity")
        available = next(int(line.split()[1]) for line in Path("/proc/meminfo").read_text().splitlines()
                         if line.startswith("MemAvailable:"))
        if available < 4 * 1024**2:
            raise ValueError("memory capacity")
        private(SOURCE, directory=True)
        private(SOURCE / "chatwoot.dump")
        private(SOURCE / "config", directory=True)
        private(SOURCE / "config/compose.yaml")
        original = tree(SOURCE / "storage")
        for image in (PG, REDIS, RAILS):
            if out(docker("image", "inspect", "--format", "{{.Id}}", image)) != image:
                raise ValueError("image pin mismatch")
        nonce = secrets.token_hex(6)
        network = "vff-recovery-" + nonce
        with tempfile.TemporaryDirectory(prefix="vff-app-restore-") as tmp:
            base = Path(tmp)
            local_password = base / "restic-password"
            local_password.write_text(secrets.token_hex(32))
            local_env = {"PATH": "/usr/sbin:/usr/bin:/bin", "HOME": tmp, "LC_ALL": "C"}
            def restic(*args):
                return run(["restic", "--no-cache", "-r", str(base / "repo"),
                            "-p", str(local_password), *args], env=local_env)
            stage = "restic"
            restic("init")
            restic("backup", str(SOURCE / "chatwoot.dump"), str(SOURCE / "storage"),
                   str(SOURCE / "config/compose.yaml"))
            restic("check", "--read-data")
            restic("restore", "latest", "--target", str(base / "restore"))
            recovered = base / "restore" / SOURCE.relative_to("/")
            if (tree(recovered / "storage") != original or
                digest(recovered / "chatwoot.dump") != digest(SOURCE / "chatwoot.dump") or
                digest(recovered / "config/compose.yaml") != digest(SOURCE / "config/compose.yaml")):
                raise ValueError("restored bytes mismatch")
            stage = "isolated-network"
            network_id = out(docker("network", "create", "--internal", network))
            if not ID.fullmatch(network_id):
                raise ValueError("invalid scratch network ID")
            password = secrets.token_hex(24)
            def env_file(name, values):
                path = base / name
                path.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
                return str(path)
            def start(suffix, ram, *args):
                identifier = out(docker("run", "-d", "--pull", "never", "--name",
                    network + "-" + suffix, "--network", network_id,
                    "--network-alias", suffix, "--memory", ram, "--cpus", "1.0",
                    "--restart", "no", *args))
                if not ID.fullmatch(identifier):
                    raise ValueError("scratch container ID")
                containers.append(identifier)
                if out(docker("inspect", "--format", "{{json .HostConfig.PortBindings}}",
                              identifier)) not in ("{}", "null"):
                    raise ValueError("unexpected published port")
                return identifier
            try:
                if out(docker("network", "inspect", "--format", "{{.Internal}}", network_id)) != "true":
                    raise ValueError("network not internal")
                stage = "scratch-postgres"
                pg = start("db", "768m", "--env-file", env_file("pg.env", {
                    "POSTGRES_DB": "chatwoot", "POSTGRES_USER": "chatwoot",
                    "POSTGRES_PASSWORD": password}), PG)
                wait_for(lambda: docker("exec", pg, "pg_isready", "-h", "127.0.0.1",
                         "-U", "chatwoot", "-d", "chatwoot", allow_fail=True).returncode == 0, 40)
                docker("cp", str(recovered / "chatwoot.dump"), pg + ":/tmp/restore.dump")
                docker("exec", pg, "pg_restore", "--exit-on-error", "-U", "chatwoot",
                       "--no-owner", "--no-acl", "-d", "chatwoot", "/tmp/restore.dump")
                stage = "scratch-redis"
                redis = start("redis", "128m", REDIS)
                wait_for(lambda: out(docker("exec", redis, "redis-cli", "ping",
                         allow_fail=True)) == "PONG", 15)
                stage = "scratch-rails"
                rails = start("rails", "2048m", "--env-file", env_file("rails.env", {
                    "SECRET_KEY_BASE": secrets.token_hex(64), "RAILS_ENV": "production",
                    "NODE_ENV": "production", "INSTALLATION_ENV": "docker",
                    "FRONTEND_URL": "http://localhost:3000", "FORCE_SSL": "false",
                    "POSTGRES_HOST": "db", "POSTGRES_DATABASE": "chatwoot",
                    "POSTGRES_USERNAME": "chatwoot", "POSTGRES_PASSWORD": password,
                    "REDIS_URL": "redis://redis:6379", "ACTIVE_STORAGE_SERVICE": "local",
                    "ENABLE_ACCOUNT_SIGNUP": "false", "DISABLE_TELEMETRY": "true",
                    "ENABLE_PUSH_RELAY_SERVER": "false", "LOG_LEVEL": "warn"}),
                    "--mount", f"type=bind,src={recovered / 'storage'},dst=/app/storage,readonly",
                    "--entrypoint", "bundle", RAILS, "exec", "rails", "s",
                    "-p", "3000", "-b", "127.0.0.1")
                probe = "exit(Net::HTTP.get_response(URI('http://127.0.0.1:3000/app/login')).code == '200' ? 0 : 1)"
                wait_for(lambda: docker("exec", rails, "ruby", "-rnet/http", "-ruri",
                         "-e", probe, allow_fail=True, timeout=8).returncode == 0, 55)
                stage = "rails-data"
                result = out(docker("exec", rails, "bundle", "exec", "rails", "runner",
                                    DATA_CHECK, timeout=50))
                records = [json.loads(line.split("=", 1)[1]) for line in result.splitlines()
                           if line.startswith("VFF_RECOVERY_RESULT=")]
                if len(records) != 1 or records[0]["counts"] != TABLES or records[0]["verified"] != 41:
                    raise ValueError("application counts or attachments differ")
                receipt = {"status": "PASS_ISOLATED_CHATWOOT_APP_RESTORE",
                           "http_login": 200, "counts": records[0]["counts"],
                           "rails_blob_download_verified": 41, "restic_integrity": True,
                           "saved_compose_bytes_equal": True, "original_secret_config_restored": False,
                           "checkpoint_restored": False, "external_transports_started": False}
            finally:
                stage_before_cleanup = stage
                cleanup_failed = False
                for identifier in reversed(containers):
                    cleanup_failed |= docker("rm", "-f", "-v", identifier,
                                             allow_fail=True).returncode != 0
                if network_id and ID.fullmatch(network_id):
                    cleanup_failed |= docker("network", "rm", network_id,
                                             allow_fail=True).returncode != 0
                if cleanup_failed:
                    stage = "cleanup"
                    raise RuntimeError("scratch cleanup failed")
                stage = stage_before_cleanup
        print(json.dumps(dict(receipt, scratch_removed=True)))
    except Exception:
        print(json.dumps({"status": "FAIL_ISOLATED_CHATWOOT_APP_RESTORE", "stage": stage}))
        raise SystemExit(1)

if __name__ == "__main__":
    main()
