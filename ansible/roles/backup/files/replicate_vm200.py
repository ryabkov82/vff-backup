#!/usr/bin/env python3
"""Copy the newest successful SHM/Remnawave snapshot to the private VM200 repo.

Existing primary repository credentials are supplied by systemd EnvironmentFile.
Independent destination credentials are read locally from the service credential
directory. No credentials, captured CLI output or restored data are printed.
"""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile

ENDPOINT = "http://100.84.224.4:9000"
PRIMARY = "s3:https://s3.portalbase.link/vff-backups/"
PROFILES = {
    "shm": ("billing-1", "shm/ru-msk-1", "vff-shm-prod", "shm-billing-1"),
    "remnawave": ("vm34364.it-garage.network", "remnawave/de-fra-1",
                  "vff-remnawave-prod", "remnawave-panel-1"),
}
ID_PATTERN = re.compile(r"[0-9a-f]{64}")
REGION = "us-east-1"


class ReplicationError(RuntimeError):
    pass


def require(condition, reason):
    if not condition:
        raise ReplicationError(reason)


def read_settings(path):
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        require(sep and key not in result and value and not any(c.isspace() for c in value),
                "invalid destination configuration")
        result[key] = value
    return result


def environments(job, hostname, source, directory):
    host, suffix, bucket, identity = PROFILES[job]
    expected_source = PRIMARY + suffix
    require(hostname == host, "unexpected source host")
    require(source.get("RESTIC_REPOSITORY") == expected_source, "unexpected source repository")
    require(source.get("AWS_ACCESS_KEY_ID") and source.get("AWS_SECRET_ACCESS_KEY"),
            "source S3 authentication missing")
    require(source.get("RESTIC_PASSWORD"), "source repository password missing")
    source_env = {"PATH": "/usr/bin:/bin", "HOME": "/root", "LC_ALL": "C",
                  "RESTIC_CACHE_DIR": "/var/cache/restic"}
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
                 "AWS_DEFAULT_REGION", "AWS_REGION", "RESTIC_PASSWORD"):
        if source.get(name):
            source_env[name] = source[name]
    source_env["RESTIC_REPOSITORY"] = expected_source
    cfg = read_settings(directory / "vm200.env")
    require(cfg.get("AWS_ACCESS_KEY_ID") == identity
            and cfg.get("RESTIC_REPOSITORY") == f"s3:{ENDPOINT}/{bucket}/backup/repo"
            and cfg.get("AWS_DEFAULT_REGION") == REGION
            and bool(re.fullmatch(r"[0-9a-f]{64}", cfg.get("AWS_SECRET_ACCESS_KEY", ""))),
            "unexpected destination scope")
    destination = dict(source_env)
    destination["RESTIC_FROM_PASSWORD"] = destination.pop("RESTIC_PASSWORD")
    destination.update({
        "RESTIC_REPOSITORY": f"rclone:vm200:{bucket}/backup/repo",
        "RESTIC_PASSWORD_FILE": str(directory / "vm200.pass"),
        "RCLONE_CONFIG": "/dev/null",
        "RCLONE_CONFIG_VM200_TYPE": "s3",
        "RCLONE_CONFIG_VM200_PROVIDER": "Minio",
        "RCLONE_CONFIG_VM200_ENV_AUTH": "false",
        "RCLONE_CONFIG_VM200_ACCESS_KEY_ID": identity,
        "RCLONE_CONFIG_VM200_SECRET_ACCESS_KEY": cfg["AWS_SECRET_ACCESS_KEY"],
        "RCLONE_CONFIG_VM200_REGION": REGION,
        "RCLONE_CONFIG_VM200_ENDPOINT": ENDPOINT,
        "RCLONE_CONFIG_VM200_FORCE_PATH_STYLE": "true",
        "RCLONE_CONFIG_VM200_NO_CHECK_BUCKET": "true",
    })
    require((directory / "vm200.pass").is_file(), "destination repository password missing")
    return source_env, destination


def command(env, *args):
    result = subprocess.run(["/usr/bin/restic", "--retry-lock", "5m", *args],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            check=False, timeout=7200)
    require(result.returncode == 0, f"restic {args[0]} failed (exit {result.returncode})")
    return result.stdout


def snapshot_time(row):
    return dt.datetime.fromisoformat(row["time"].replace("Z", "+00:00"))


def latest(rows, host, job):
    candidates = [r for r in rows if r.get("hostname") == host and job in r.get("tags", [])
                  and ID_PATTERN.fullmatch(r.get("id", ""))]
    require(candidates, "no matching source snapshot")
    row = max(candidates, key=snapshot_time)
    required = {"/var/backups/db", "/opt/shm"} if job == "shm" else {
        "/var/backups/db", "/opt/remnawave/.env", "/opt/remnawave/docker-compose.yml",
        "/opt/remnawave/docker-compose.override.yml",
    }
    require(required.issubset(row.get("paths", [])), "source snapshot lacks required backup paths")
    require(bool(ID_PATTERN.fullmatch(row.get("tree", ""))), "invalid source snapshot tree")
    return row


def same_snapshot(left, right):
    return (all(left.get(k) == right.get(k) for k in ("tree", "hostname", "paths", "tags"))
            and snapshot_time(left) == snapshot_time(right))


def replicate(job, source, destination, *, copy_only=False):
    host = PROFILES[job][0]
    source_row = latest(json.loads(command(source, "snapshots", "--json", "--host", host,
                                           "--tag", job, "--latest", "1")), host, job)
    # Verify destination opens before any transfer. Never initialize on an error.
    command(destination, "snapshots", "--json")
    command(destination, "copy", "--from-repo", source["RESTIC_REPOSITORY"], source_row["id"])
    rows = json.loads(command(destination, "snapshots", "--json", "--host", host, "--tag", job))
    matches = [r for r in rows if same_snapshot(source_row, r)]
    require(len(matches) == 1, "copied snapshot metadata not confirmed")
    require(ID_PATTERN.fullmatch(matches[0].get("id", "")), "invalid destination snapshot ID")
    if not copy_only:
        # Apply retention to the destination only, and only after verified copy.
        command(destination, "forget", "--host", host, "--tag", job, "--group-by", "host,tags",
                "--keep-last", "7", "--keep-daily", "7", "--keep-weekly", "5",
                "--keep-monthly", "6", "--prune")
    return {"source": source_row["id"], "destination": matches[0]["id"],
            "snapshot_timestamp": int(snapshot_time(source_row).timestamp())}


def metric_write(directory, job, success, timestamp=None):
    target = directory / f"backup_vm200_{job}.prom"
    old = directory / f"backup_vm200_{job}_last_success.prom"
    directory.mkdir(parents=True, exist_ok=True)
    values = [(target, f'backup_vm200_last_attempt_success{{backup_job="{job}"}} {int(success)}\n')]
    if success:
        values.append((old, f'backup_vm200_snapshot_timestamp_seconds{{backup_job="{job}"}} {timestamp}\n'))
    for path, value in values:
        with tempfile.NamedTemporaryFile(mode="w", dir=directory, prefix=".vm200-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(value)
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())
        temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("job", choices=PROFILES)
    parser.add_argument("--copy-only", action="store_true")
    args = parser.parse_args()
    metrics = Path("/var/lib/node_exporter/textfile")
    try:
        source, destination = environments(args.job, socket.gethostname(), os.environ,
                                           Path(os.environ["CREDENTIALS_DIRECTORY"]))
        receipt = replicate(args.job, source, destination, copy_only=args.copy_only)
        metric_write(metrics, args.job, True, receipt["snapshot_timestamp"])
        print(json.dumps({"status": "PASS_VM200_COPY", "job": args.job, **receipt}))
    except (ReplicationError, OSError, KeyError, ValueError, subprocess.TimeoutExpired) as error:
        # Unexpected exception text can contain secrets. Emit only controlled messages.
        reason = str(error) if isinstance(error, ReplicationError) else type(error).__name__
        metric_write(metrics, args.job, False)
        print(json.dumps({"status": "FAIL_VM200_COPY", "job": args.job, "reason": reason}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
