#!/usr/bin/env python3
"""Offline restore selection and filesystem safety tests; no real Restic."""
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ansible/roles/restore/files"))
from restore_snapshot import execute, private_target, select_snapshot

A = "a" * 64
B = "b" * 64
C = "c" * 64

def row(ident, host="node-a", tags=None, time="2026-11-18T10:00:00Z"):
    return {"id": ident, "hostname": host, "tags": ["chatwoot"] if tags is None else tags,
            "time": time, "paths": ["/fixture"]}


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.rows = [row(A), row(B, "node-b", time="2026-11-18T13:00:00Z"),
                     row(C, tags=["shm"], time="2026-11-18T14:00:00Z")]

    def test_host_tag_and_newest_exact(self):
        newest = row("d"*64, time="2026-11-18T12:00:00+01:00")
        self.assertEqual(select_snapshot([newest, *self.rows], "node-a", "chatwoot")["id"], newest["id"])

    def test_no_fallback_other_host(self):
        with self.assertRaises(ValueError):
            select_snapshot(self.rows, "missing", "chatwoot")

    def test_no_blank_host(self):
        with self.assertRaises(ValueError):
            select_snapshot(self.rows, "", "chatwoot", A)

    def test_explicit_id_cannot_override_scope(self):
        with self.assertRaises(ValueError):
            select_snapshot(self.rows, "node-a", "chatwoot", B)
        with self.assertRaises(ValueError):
            select_snapshot(self.rows, "node-a", "chatwoot", C)
        self.assertEqual(select_snapshot(self.rows, "node-b", "chatwoot", B[:8])["id"], B)

    def test_ambiguous_prefix_and_no_newest(self):
        with self.assertRaises(ValueError):
            select_snapshot([row(A), row("a"*8+"d"*56)], "node-a", "chatwoot", A[:8])
        with self.assertRaises(ValueError):
            select_snapshot(self.rows, "node-a", "chatwoot", newest=False)

    def test_private_new_child(self):
        target = private_target(str(self.root / "new"), temp_root=self.root)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
        with self.assertRaises(OSError):
            private_target(str(target), temp_root=self.root)

    def test_default_allocates_private_child(self):
        target = private_target("", temp_root=self.root)
        self.assertEqual(target.parent, self.root)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)

    def test_automatic_staging_rejects_untrusted_ancestor(self):
        shared = self.root / "untrusted"
        shared.mkdir(mode=0o700)
        below = shared / "private-child"
        below.mkdir(mode=0o700)
        shared.chmod(0o777)
        with self.assertRaises(ValueError):
            private_target("", temp_root=below)
        self.assertEqual(list(below.iterdir()), [])

    def test_automatic_staging_rejects_symlink_root(self):
        link = self.root / "link"
        real = self.root / "real"
        real.mkdir(mode=0o700)
        link.symlink_to(real, target_is_directory=True)
        with self.assertRaises(OSError):
            private_target("", temp_root=link)
        self.assertEqual(list(real.iterdir()), [])

    def test_system_temp_selection_keeps_private_staging(self):
        with patch("restore_snapshot.tempfile.gettempdir", return_value=str(self.root)):
            target = private_target("")
        self.assertEqual(target.parent, self.root)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)

    def test_traversal_relative_and_noncanonical_paths(self):
        for target in ("relative", str(self.root) + "/../escape", str(self.root) + "//new",
                       str(self.root) + "/./new", str(self.root) + "/new/"):
            with self.subTest(target=target):
                with self.assertRaises(ValueError):
                    private_target(target, force=True, temp_root=self.root)

    def test_symlink_and_unsafe_parent(self):
        outside = self.root / "outside"
        outside.mkdir(mode=0o700)
        (self.root / "link").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OSError):
            private_target(str(self.root/"link"/"new"), force=True, temp_root=self.root)
        with self.assertRaises(OSError):
            private_target(str(self.root/"link"), temp_root=self.root)
        outside.chmod(0o777)
        with self.assertRaises(ValueError):
            private_target(str(outside/"new"), force=True, temp_root=self.root)
        self.assertEqual(list(outside.iterdir()), [])

    def test_force_requires_existing_trusted_parent_and_new_target(self):
        parent = self.root / "parent"
        parent.mkdir(mode=0o700)
        with self.assertRaises(ValueError):
            private_target(str(parent/"new"), temp_root=self.root)
        target = private_target(str(parent/"new"), force=True, temp_root=self.root)
        self.assertTrue(target.is_dir())
        with self.assertRaises(OSError):
            private_target(str(parent/"new"), force=True, temp_root=self.root)

    def test_execute_literal_argv_and_private_outer_directory(self):
        calls = []
        options = {"host":"node-a","tag":"chatwoot","snapshot":"","newest":True,
                   "force":True,"target":str(self.root/"restore"),
                   "includes":["/a path/$literal"],"excludes":["/logs/**"]}
        def fake(args):
            calls.append(args)
            if args[0] == "snapshots":
                return json.dumps(self.rows)
            Path(args[args.index("--target")+1]).chmod(0o755)
            return ""
        receipt = execute(options, runner=fake)
        self.assertEqual(receipt["snapshot"], A)
        self.assertEqual(receipt["host"], "node-a")
        self.assertEqual(calls, [
            ["snapshots","--json","--host","node-a","--tag","chatwoot"],
            ["restore",A,"--target",str(self.root/"restore"/"data"),
             "--include","/a path/$literal","--exclude","/logs/**"]])
        self.assertEqual(stat.S_IMODE((self.root/"restore").stat().st_mode), 0o700)

    def test_execute_no_fallback_and_no_destination_on_no_snapshot(self):
        calls = []
        def fake(args):
            calls.append(args)
            return json.dumps(self.rows)
        options = {"host":"missing","tag":"chatwoot","snapshot":"","newest":True,
                   "force":True,"target":str(self.root/"restore"),"includes":[],"excludes":[]}
        with self.assertRaises(ValueError):
            execute(options, runner=fake)
        self.assertEqual(len(calls), 1)
        self.assertFalse((self.root/"restore").exists())

    def test_role_uses_helper_no_shell_and_no_log(self):
        tasks = yaml.safe_load((ROOT/"ansible/roles/restore/tasks/restore.yml").read_text())
        command = next(task for task in tasks if "ansible.builtin.command" in task)
        self.assertTrue(command["no_log"])
        self.assertEqual(command["ansible.builtin.command"]["argv"],
                         ["/usr/bin/python3", "/etc/vff-backup/restore_snapshot.py"])
        self.assertFalse(any("ansible.builtin.shell" in task for task in tasks))

if __name__ == "__main__":
    unittest.main()
