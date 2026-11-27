"""Render the exact backup-role template for the isolated VM205 online fixture."""
from pathlib import Path
import sys

from test_backup_reliability import render_backup_script


def main():
    if len(sys.argv) not in (2, 3) or (len(sys.argv) == 3 and sys.argv[2] != "--checkpoint"):
        raise SystemExit("usage: output [--checkpoint]")
    checkpoint = len(sys.argv) == 3
    lane = "operator-checkpoint-20261127" if checkpoint else "operator-online-20261126"
    base = Path("/opt/vff-functional-205/recovery") / lane
    modules = base / "bundle"
    job = {"name": "chatwoot-operator-online-compatibility", "prepared_snapshot": {
        "kind": "chatwoot", "profile": "operator-online-v1",
        "scope": "OPERATOR_COMPATIBILITY_TEST", "profile_sha256": "a"*64,
        "root": "/var/backups/vff-" + lane,
        "program": "/usr/local/libexec/vff-operator-" + ("checkpoint" if checkpoint else "online") + "-capture",
        "schedule_enabled": False,
        "trusted_paths": [str(base / "profile.json"), str(base / "source-config"),
                          str(modules / "operator_online_backup.py"),
                          str(modules / "operator_prepared_backup.py"),
                          str(modules / "operator_online_recovery.rb"),
                          str(modules / "prepared_trust.py")],
    }}
    render_backup_script(Path(sys.argv[1]), [job], base / "metrics", base / "restic.env",
        backup_prepared_verifier_path=str(modules / "verify_prepared_snapshot.py"),
        backup_chatwoot_verifier_path=str(modules / "chatwoot_snapshot_contract.py"))


if __name__ == "__main__":
    main()
