#!/usr/bin/env python3
"""Docker adapter tests with a synthetic command runner."""
import hashlib
import json
import unittest

from test_chatwoot_snapshot_contract import contract
from chatwoot_docker_lifecycle import (
    DockerLifecycle, CaptureError, MANDATORY, PROJECT, parse_inventory,
)


def cid(name):
    return hashlib.sha256(name.encode()).hexdigest()


class FakeDocker:
    def __init__(self):
        self.rows = {}
        self.calls = []
        self.held = True
        self.transition_changes = True
        self.list_drift = False
        self.list_count = 0
        for service in MANDATORY | {"web-drafter", "telegram-poller"}:
            self.add(f"{PROJECT}-{service}-1", service,
                     "exited" if service == "telegram-poller" else "running")
        self.add("vff-chatwoot-gemma-drafter", None, "exited", None)
        self.add("vff-chatwoot-telegram-poller", None, "exited", None)

    def add(self, name, service=None, state="running", project=PROJECT):
        ident = cid(name)
        self.rows[ident] = [ident, "/" + name, "sha256:" + cid("image:" + name),
                            state, project, service]
        return ident

    def check_lease(self):
        if not self.held:
            raise CaptureError("maintenance unavailable")

    def __call__(self, args, timeout=30):
        self.calls.append(args)
        if args == ["ps", "-a", "-q", "--no-trunc"]:
            self.list_count += 1
            ids = sorted(self.rows)
            if self.list_drift and self.list_count % 2 == 0:
                ids.append(cid("new-container"))
            return "\n".join(ids)
        if args[:2] == ["inspect", "--format"]:
            return "\n".join(json.dumps(self.rows[id_]) for id_ in args[3:])
        if args[0] in ("stop", "start"):
            if self.transition_changes:
                self.rows[args[-1]][3] = "exited" if args[0] == "stop" else "running"
            return args[-1]
        raise AssertionError("unexpected command")


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.docker = FakeDocker()
        self.adapter = DockerLifecycle("a" * 64, self.docker.check_lease, self.docker)

    def writer(self, name):
        return self.docker.rows[cid(f"{PROJECT}-{name}-1")]

    def mutations(self):
        return [args for args in self.docker.calls if args[0] in ("stop", "start")]

    def test_inventory_matches_contract(self):
        states = self.adapter.inventory()
        self.assertEqual(set(states), contract.REQUIRED_WRITERS | contract.OPTIONAL_WRITERS)
        self.assertEqual(states["web-drafter"], "running")
        self.assertEqual(states["telegram-poller"], "stopped")
        self.assertEqual(states["gemma-drafter"], "stopped")
        self.assertEqual(self.mutations(), [])

    def test_exact_id_stop_start_and_stable_fingerprint(self):
        original = self.adapter.target()
        self.adapter.stop("rails")
        self.assertEqual(self.adapter.inventory()["rails"], "stopped")
        self.adapter.start("rails")
        self.assertEqual(self.adapter.target(), original)
        self.assertEqual(self.mutations(), [
            ["stop", "--time", "60", self.writer("rails")[0]],
            ["start", self.writer("rails")[0]]])

    def test_absent_optional(self):
        del self.docker.rows[cid(f"{PROJECT}-web-drafter-1")]
        self.assertEqual(self.adapter.inventory()["web-drafter"], "absent")
        with self.assertRaises(CaptureError):
            self.adapter.start("web-drafter")
        self.assertEqual(self.mutations(), [])

    def test_unknown_running_rejected(self):
        self.docker.add("unclassified", None)
        with self.assertRaises(CaptureError):
            self.adapter.inventory()

    def test_unknown_stopped_allowed(self):
        self.docker.add("unclassified", None, "exited")
        self.assertIn("rails", self.adapter.inventory())

    def test_legacy_unsupported_active(self):
        self.docker.rows[cid("vff-chatwoot-telegram-poller")][3] = "running"
        with self.assertRaises(CaptureError):
            self.adapter.inventory()

    def test_legacy_mutation_refused(self):
        self.adapter.target()
        with self.assertRaises(CaptureError):
            self.adapter.start("gemma-drafter")
        self.assertEqual(self.mutations(), [])

    def test_project_label_mismatch(self):
        self.writer("rails")[4] = "other"
        with self.assertRaises(CaptureError):
            self.adapter.target()

    def test_service_label_mismatch(self):
        self.writer("rails")[5] = "sidekiq"
        with self.assertRaises(CaptureError):
            self.adapter.target()

    def test_missing_mandatory(self):
        del self.docker.rows[cid(f"{PROJECT}-rails-1")]
        with self.assertRaises(CaptureError):
            self.adapter.target()

    def test_dependency_stopped(self):
        self.writer("postgres")[3] = "exited"
        with self.assertRaises(CaptureError):
            self.adapter.target()

    def test_paused_writer_rejected(self):
        self.writer("rails")[3] = "paused"
        with self.assertRaises(CaptureError):
            self.adapter.target()

    def test_dependency_mutation_refused(self):
        self.adapter.target()
        with self.assertRaises(CaptureError):
            self.adapter.stop("postgres")
        self.assertEqual(self.mutations(), [])

    def test_image_drift_refused_before_mutation(self):
        self.adapter.target()
        self.writer("rails")[2] = "sha256:" + "b"*64
        with self.assertRaises(CaptureError):
            self.adapter.stop("rails")
        self.assertEqual(self.mutations(), [])

    def test_id_replacement_refused_before_mutation(self):
        self.adapter.target()
        old = self.writer("rails")
        del self.docker.rows[old[0]]
        old[0] = cid("replacement")
        self.docker.rows[old[0]] = old
        with self.assertRaises(CaptureError):
            self.adapter.stop("rails")
        self.assertEqual(self.mutations(), [])

    def test_inventory_race_refused(self):
        self.docker.list_drift = True
        with self.assertRaises(CaptureError):
            self.adapter.target()

    def test_missing_lease_refused(self):
        self.adapter.target()
        self.docker.held = False
        with self.assertRaises(CaptureError):
            self.adapter.stop("rails")
        self.assertEqual(self.mutations(), [])

    def test_unpinned_mutation_refused(self):
        with self.assertRaises(CaptureError):
            self.adapter.stop("rails")
        self.assertEqual(self.mutations(), [])

    def test_no_success_without_independent_confirmation(self):
        self.adapter.target()
        self.docker.transition_changes = False
        with self.assertRaises(CaptureError):
            self.adapter.stop("rails")

    def test_incomplete_inspection_refused(self):
        with self.assertRaises(CaptureError):
            parse_inventory("", {cid("missing")})

    def test_idempotent_stopped_writer(self):
        self.adapter.target()
        self.adapter.stop("telegram-poller")
        self.assertEqual(self.mutations(), [])


if __name__ == "__main__":
    unittest.main()
