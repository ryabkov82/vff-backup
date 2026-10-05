#!/usr/bin/env python3
"""Checks SHM/Remnawave backup paths and fail-closed vff-backup.sh behavior.

The backup script is a Jinja template. These tests render it with the same
variables the role injects and run it against fake docker/restic binaries.
They do not talk to production, MinIO, or real Restic repositories.
"""

from __future__ import annotations

import gzip
import json
import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = ROOT / "ansible" / "roles" / "backup" / "templates"
GROUP_VARS = ROOT / "ansible" / "group_vars"


def _dict2items(mapping, key_name="key", value_name="value"):
    return [{key_name: key, value_name: value} for key, value in mapping.items()]


def _to_nice_json(value):
    return json.dumps(value, indent=4, ensure_ascii=False)


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _ternary(value, when_true, when_false):
    return when_true if value else when_false


def render_backup_script(dest: Path, jobs, metrics_dir: Path, env_file: Path, keep_count: int = 7) -> None:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        trim_blocks=True,
        lstrip_blocks=False,
        keep_trailing_newline=True,
        undefined=StrictUndefined,
    )
    env.filters["dict2items"] = _dict2items
    env.filters["to_nice_json"] = _to_nice_json
    env.filters["bool"] = _as_bool
    env.filters["ternary"] = _ternary
    env.filters["int"] = int
    text = env.get_template("vff-backup.sh.j2").render(
        backup_env_file=str(env_file),
        backup_enable_metrics=True,
        backup_node_exporter_textfile_dirs=[str(metrics_dir)],
        backup_jobs=jobs,
        backup_dump_keep_count=keep_count,
        backup_dump_keep_days=0,
        backup_forget_policy={
            "keep_last": 7,
            "keep_daily": 14,
            "keep_weekly": 8,
            "keep_monthly": 12,
        },
    )
    dest.write_text(text)
    dest.chmod(dest.stat().st_mode | stat.S_IEXEC)


def write_dump(path: Path, payload: bytes, mtime: int) -> None:
    path.write_bytes(gzip.compress(payload))
    os.utime(path, (mtime, mtime))


class BackupConfigTests(unittest.TestCase):
    def test_shm_backup_includes_db_and_opt_shm(self):
        shm = yaml.safe_load((GROUP_VARS / "shm.yml").read_text())
        job = shm["backup_jobs_map"]["shm"]
        self.assertEqual(job["paths"], ["/var/backups/db", "/opt/shm"])
        self.assertTrue(job["db_dump"]["enabled"])
        self.assertEqual(job["db_dump"]["dump_dir"], "/var/backups/db")
        self.assertIn('> "$DUMP_OUT"', job["db_dump"]["command"])
        self.assertNotIn("date +%F_%H%M%S", job["db_dump"]["command"])
        self.assertNotIn("exclude_paths", job)

    def test_remnawave_paths_unchanged(self):
        remnawave = yaml.safe_load((GROUP_VARS / "remnawave.yml").read_text())
        job = remnawave["backup_jobs_map"]["remnawave"]
        self.assertEqual(
            job["paths"],
            [
                "/var/backups/db",
                "/opt/remnawave/.env",
                "/opt/remnawave/docker-compose.yml",
            ],
        )
        self.assertTrue(job["db_dump"]["enabled"])
        self.assertIn('> "$DUMP_OUT"', job["db_dump"]["command"])
        self.assertNotIn("date +%F_%H%M%S", job["db_dump"]["command"])

    def test_rendered_group_vars_embed_expected_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metrics = root / "metrics"
            metrics.mkdir()
            env_file = root / "restic.env"
            env_file.write_text("\n")
            expected_paths = {
                "shm": ["/var/backups/db", "/opt/shm"],
                "remnawave": [
                    "/var/backups/db",
                    "/opt/remnawave/.env",
                    "/opt/remnawave/docker-compose.yml",
                ],
            }
            for name, paths in expected_paths.items():
                data = yaml.safe_load((GROUP_VARS / f"{name}.yml").read_text())
                jobs = list(data["backup_jobs_map"].values())
                script = root / f"{name}.sh"
                render_backup_script(script, jobs, metrics, env_file)
                subprocess.run(["bash", "-n", str(script)], check=True)
                text = script.read_text()
                embedded = json.loads(text.split("<<'EOF'\n", 1)[1].split("\nEOF\n", 1)[0])
                self.assertEqual(embedded[0]["paths"], paths)
                self.assertNotIn("skipping db_dump", text)
                self.assertNotIn("{{", text)
                self.assertIn("gzip -t", text)
                self.assertIn("on_exit", text)


class BackupScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.compose = self.root / "opt" / "shm"
        self.compose.mkdir(parents=True)
        self.dump_dir = self.root / "var" / "backups" / "db"
        self.dump_dir.mkdir(parents=True)
        self.metrics = self.root / "metrics"
        self.metrics.mkdir()
        self.env_file = self.root / "restic.env"
        self.env_file.write_text("\n")
        self.script = self.root / "vff-backup.sh"
        self.restic_log = self.root / "restic.log"
        self.restic_log.write_text("")
        self.docker_log = self.root / "docker.log"
        self.docker_log.write_text("")
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self._write_mock(
            "docker",
            textwrap.dedent(
                """\
                #!/bin/bash
                printf '%s\\n' "$*" >> "${DOCKER_LOG:?}"
                if [[ "${1:-}" == "compose" && "${2:-}" == "ps" ]]; then
                  rc="${MOCK_PS_RC:-0}"
                  if [[ "$rc" != "0" ]]; then
                    echo "compose ps failed" >&2
                    exit "$rc"
                  fi
                  if [[ -n "${MOCK_SERVICES+x}" ]]; then
                    if [[ -n "$MOCK_SERVICES" ]]; then
                      # shellcheck disable=SC2086
                      printf '%s\\n' $MOCK_SERVICES
                    fi
                  else
                    printf '%s\\n' mysql
                  fi
                  exit 0
                fi
                if [[ "${1:-}" == "compose" && "${2:-}" == "exec" ]]; then
                  shift 2
                  while [[ "${1:-}" == -* ]]; do
                    shift
                  done
                  shift
                  exec "$@"
                fi
                if [[ "${1:-}" == "pause" ]]; then
                  exit 0
                fi
                if [[ "${1:-}" == "unpause" ]]; then
                  if [[ -n "${MOCK_UNPAUSE_FAIL:-}" && "${2:-}" == "$MOCK_UNPAUSE_FAIL" ]]; then
                    echo "unpause failed" >&2
                    exit 1
                  fi
                  exit 0
                fi
                echo "unexpected docker invocation: $*" >&2
                exit 99
                """
            ),
        )
        self._write_mock(
            "restic",
            textwrap.dedent(
                """\
                #!/bin/bash
                printf '%s\\n' "$*" >> "${RESTIC_LOG:?}"
                if [[ "${1:-}" == "backup" ]]; then
                  if [[ "${RESTIC_BACKUP_RC:-0}" != "0" ]]; then
                    echo "restic backup failed" >&2
                    exit "${RESTIC_BACKUP_RC}"
                  fi
                  printf '%s\\n' '{"message_type":"summary","data_added":1234}'
                  exit 0
                fi
                if [[ "${1:-}" == "forget" ]]; then
                  if [[ "${RESTIC_FORGET_RC:-0}" != "0" ]]; then
                    echo "restic forget failed" >&2
                    exit "${RESTIC_FORGET_RC}"
                  fi
                  exit 0
                fi
                echo "unexpected restic invocation: $*" >&2
                exit 99
                """
            ),
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _write_mock(self, name: str, body: str) -> None:
        path = self.bin_dir / name
        path.write_text(body)
        path.chmod(0o755)

    def _job(
        self,
        command: str,
        enabled: bool = True,
        compose_dir: str | None = None,
        container: str = "mysql",
        containers: list[str] | None = None,
    ):
        dump = {
            "enabled": enabled,
            "dump_dir": str(self.dump_dir),
            "command": command,
        }
        if container:
            dump["container"] = container
        return {
            "name": "shm",
            "compose_dir": str(self.compose if compose_dir is None else compose_dir),
            "paths": ["/var/backups/db", "/opt/shm"],
            "containers": [] if containers is None else containers,
            "db_dump": dump,
        }

    def _render(self, jobs, keep_count: int = 7) -> None:
        render_backup_script(self.script, jobs, self.metrics, self.env_file, keep_count=keep_count)
        subprocess.run(["bash", "-n", str(self.script)], check=True)

    def _run(self, extra_env: dict | None = None, job: str = "shm"):
        env = os.environ.copy()
        env["PATH"] = f"{self.bin_dir}:{env.get('PATH', '')}"
        env["RESTIC_LOG"] = str(self.restic_log)
        env["DOCKER_LOG"] = str(self.docker_log)
        env["MOCK_SERVICES"] = "mysql"
        env["RESTIC_BACKUP_RC"] = "0"
        env["RESTIC_FORGET_RC"] = "0"
        env["MOCK_PS_RC"] = "0"
        env["MOCK_UNPAUSE_FAIL"] = ""
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["bash", str(self.script), job],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def _status(self) -> int:
        text = (self.metrics / "backup_metrics.prom").read_text()
        statuses = []
        for line in text.splitlines():
            if line.startswith("backup_last_status{"):
                statuses.append(int(line.rsplit(" ", 1)[1]))
        self.assertEqual(len(statuses), 1, text)
        return statuses[0]

    def _restic_calls(self) -> list[str]:
        return [line for line in self.restic_log.read_text().splitlines() if line.strip()]

    def _docker_calls(self) -> list[str]:
        return [line for line in self.docker_log.read_text().splitlines() if line.strip()]

    def _seed_good(self, name: str, mtime: int, payload: bytes | None = None) -> Path:
        path = self.dump_dir / name
        write_dump(path, payload or f"good {name}\n".encode(), mtime)
        return path

    def test_missing_db_service_fails_and_does_not_backup_stale_dump(self):
        good = self._seed_good("old.sql.gz", mtime=1_700_000_000)
        before = good.read_bytes()
        self._render([self._job("echo should-not-run")])
        result = self._run({"MOCK_SERVICES": "redis"})

        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("compose service 'mysql' not found", result.stderr)
        self.assertNotIn("skipping db_dump", result.stderr)
        self.assertEqual(self._restic_calls(), [])
        self.assertEqual(self._status(), 1)
        self.assertEqual(good.read_bytes(), before)
        self.assertEqual(list(self.dump_dir.glob("*.sql.gz")), [good])

    def test_missing_compose_dir_is_fatal(self):
        good = self._seed_good("old.sql.gz", mtime=1_700_000_000)
        missing = self.root / "missing-compose"
        self._render([self._job("echo should-not-run", compose_dir=str(missing))])
        result = self._run()

        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("compose directory", result.stderr)
        self.assertEqual(self._restic_calls(), [])
        self.assertEqual(self._status(), 1)
        self.assertTrue(good.exists())

    def test_failed_dump_command_is_fatal_and_keeps_last_good_dump(self):
        good = self._seed_good("old.sql.gz", mtime=1_700_000_000)
        before = good.read_bytes()
        command = (
            f"echo partial > {self.dump_dir}/partial_$(date +%F_%H%M%S).sql.gz; exit 1"
        )
        self._render([self._job(command)])
        result = self._run()

        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("db dump command failed", result.stderr)
        self.assertEqual(self._restic_calls(), [])
        self.assertEqual(self._status(), 1)
        self.assertEqual(good.read_bytes(), before)
        self.assertEqual(list(self.dump_dir.glob("*.sql.gz")), [good])

    def test_pipefail_rejects_failed_producer_even_if_gzip_succeeds(self):
        good = self._seed_good("old.sql.gz", mtime=1_700_000_000)
        command = f"false | gzip -c > {self.dump_dir}/pipe_$(date +%F_%H%M%S).sql.gz"
        self._render([self._job(command)])
        result = self._run()

        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._restic_calls(), [])
        self.assertEqual(self._status(), 1)
        self.assertEqual(list(self.dump_dir.glob("*.sql.gz")), [good])

    def test_unchanged_stale_dump_is_not_accepted(self):
        good = self._seed_good("old.sql.gz", mtime=1_700_000_000)
        self._render([self._job("true")])
        result = self._run()

        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("no new *.sql.gz", result.stderr)
        self.assertEqual(self._restic_calls(), [])
        self.assertEqual(self._status(), 1)
        self.assertTrue(good.exists())

    def test_overwrite_of_existing_dump_without_new_file_is_fatal(self):
        target = self._seed_good("old.sql.gz", mtime=1_700_000_000)
        untouched = self._seed_good("older.sql.gz", mtime=1_600_000_000)
        target_bytes = target.read_bytes()
        untouched_bytes = untouched.read_bytes()
        command = f"echo changed | gzip -c > {self.dump_dir}/old.sql.gz"
        self._render([self._job(command)])
        result = self._run()

        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("cannot overwrite existing file", result.stderr)
        self.assertEqual(self._restic_calls(), [])
        self.assertEqual(self._status(), 1)
        self.assertEqual(target.read_bytes(), target_bytes)
        self.assertEqual(untouched.read_bytes(), untouched_bytes)

    def test_empty_new_dump_is_fatal_and_restic_does_not_start(self):
        good = self._seed_good("old.sql.gz", mtime=1_700_000_000)
        command = f": > {self.dump_dir}/empty_$(date +%F_%H%M%S).sql.gz"
        self._render([self._job(command)])
        result = self._run()

        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("empty", result.stderr)
        self.assertEqual(self._restic_calls(), [])
        self.assertEqual(self._status(), 1)
        self.assertEqual(list(self.dump_dir.glob("*.sql.gz")), [good])

    def test_invalid_gzip_is_fatal_and_restic_does_not_start(self):
        good = self._seed_good("old.sql.gz", mtime=1_700_000_000)
        command = f"echo nope > {self.dump_dir}/bad_$(date +%F_%H%M%S).sql.gz"
        self._render([self._job(command)])
        result = self._run()

        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("gzip -t failed", result.stderr)
        self.assertEqual(self._restic_calls(), [])
        self.assertEqual(self._status(), 1)
        self.assertEqual([path.name for path in self.dump_dir.glob("*.sql.gz")], ["old.sql.gz"])

    def test_failed_run_does_not_apply_local_retention(self):
        base = 1_700_000_000
        kept = []
        for index in range(8):
            kept.append(self._seed_good(f"old-{index}.sql.gz", mtime=base + index))
        snapshots = {path.name: path.read_bytes() for path in kept}
        self._render([self._job("echo should-not-run")], keep_count=7)
        result = self._run({"MOCK_SERVICES": ""})

        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._restic_calls(), [])
        self.assertEqual(self._status(), 1)
        self.assertEqual(sorted(path.name for path in self.dump_dir.glob("*.sql.gz")), sorted(snapshots))
        for path in self.dump_dir.glob("*.sql.gz"):
            self.assertEqual(path.read_bytes(), snapshots[path.name])

    def test_successful_job_writes_status_zero_and_keeps_newest_dumps(self):
        base = 1_700_000_000
        names = []
        for index in range(3):
            name = f"old-{index}.sql.gz"
            names.append(name)
            self._seed_good(name, mtime=base + index)
        command = f"echo ok | gzip -c > {self.dump_dir}/run_$(date +%F_%H%M%S).sql.gz"
        self._render([self._job(command)], keep_count=2)
        result = self._run()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._status(), 0)
        calls = self._restic_calls()
        self.assertEqual(len(calls), 2, calls)
        self.assertIn("backup", calls[0])
        self.assertIn("/var/backups/db", calls[0])
        self.assertIn("/opt/shm", calls[0])
        self.assertIn("forget", calls[1])
        self.assertIn("--keep-last 7", calls[1])
        remaining = sorted(path.name for path in self.dump_dir.glob("*.sql.gz"))
        self.assertEqual(len(remaining), 2)
        self.assertTrue(any(name.startswith("run_") for name in remaining))
        self.assertIn("old-2.sql.gz", remaining)
        self.assertNotIn("old-0.sql.gz", remaining)

    def test_failed_restic_backup_writes_status_one(self):
        command = f"echo ok | gzip -c > {self.dump_dir}/run_$(date +%F_%H%M%S).sql.gz"
        self._render([self._job(command)])
        result = self._run({"RESTIC_BACKUP_RC": "7"})

        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("restic backup failed", result.stderr)
        self.assertEqual(self._status(), 1)
        calls = self._restic_calls()
        self.assertEqual(len(calls), 1, calls)
        self.assertIn("backup", calls[0])
        self.assertNotIn("forget", calls[0])
        self.assertTrue(any(path.name.startswith("run_") for path in self.dump_dir.glob("*.sql.gz")))

    def test_dump_disabled_still_runs_backup(self):
        self._render([self._job("", enabled=False, container="")])
        result = self._run()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._status(), 0)
        calls = self._restic_calls()
        self.assertIn("backup", calls[0])
        self.assertEqual(list(self.dump_dir.glob("*.sql.gz")), [])

    def test_success_pauses_and_unpauses_once(self):
        command = f"echo ok | gzip -c > {self.dump_dir}/run_$(date +%F_%H%M%S_%N).sql.gz"
        self._render([self._job(command, containers=["mysql", "redis"])])
        result = self._run()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._status(), 0)
        docker = self._docker_calls()
        self.assertEqual(
            [call for call in docker if call.startswith("pause ")],
            ["pause mysql", "pause redis"],
        )
        self.assertEqual(
            [call for call in docker if call.startswith("unpause ")],
            ["unpause mysql", "unpause redis"],
        )

    def test_unpause_when_forget_fails_after_successful_backup(self):
        command = f"echo ok | gzip -c > {self.dump_dir}/run_$(date +%F_%H%M%S_%N).sql.gz"
        self._render([self._job(command, containers=["mysql"])])
        result = self._run({"RESTIC_FORGET_RC": "3"})

        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._status(), 1)
        restic = self._restic_calls()
        self.assertTrue(restic[0].startswith("backup "), restic)
        self.assertTrue(any(call.startswith("forget ") for call in restic), restic)
        docker = self._docker_calls()
        self.assertEqual([call for call in docker if call.startswith("pause ")], ["pause mysql"])
        self.assertEqual([call for call in docker if call.startswith("unpause ")], ["unpause mysql"])

    def test_successful_job_fails_when_unpause_fails(self):
        command = f"echo ok | gzip -c > {self.dump_dir}/run_$(date +%F_%H%M%S_%N).sql.gz"
        self._render([self._job(command, containers=["mysql", "redis"])])
        result = self._run({"MOCK_UNPAUSE_FAIL": "mysql"})

        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("failed to unpause container 'mysql'", result.stderr)
        self.assertEqual(self._status(), 1)
        restic = self._restic_calls()
        self.assertTrue(restic[0].startswith("backup "), restic)
        self.assertTrue(any(call.startswith("forget ") for call in restic), restic)
        docker = self._docker_calls()
        self.assertEqual(
            [call for call in docker if call.startswith("pause ")],
            ["pause mysql", "pause redis"],
        )
        self.assertEqual(
            [call for call in docker if call.startswith("unpause ")],
            ["unpause mysql", "unpause redis"],
        )

    def test_failed_job_stays_failed_when_unpause_fails(self):
        command = f"echo ok | gzip -c > {self.dump_dir}/run_$(date +%F_%H%M%S_%N).sql.gz"
        self._render([self._job(command, containers=["mysql"])])
        result = self._run({"RESTIC_BACKUP_RC": "7", "MOCK_UNPAUSE_FAIL": "mysql"})

        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("restic backup failed", result.stderr)
        self.assertIn("failed to unpause container 'mysql'", result.stderr)
        self.assertEqual(self._status(), 1)
        docker = self._docker_calls()
        self.assertEqual([call for call in docker if call.startswith("pause ")], ["pause mysql"])
        self.assertEqual([call for call in docker if call.startswith("unpause ")], ["unpause mysql"])


if __name__ == "__main__":
    unittest.main()
