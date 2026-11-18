#!/usr/bin/env python3
"""Fault-injection tests with no host, Docker, S3, or database effects."""
from contextlib import contextmanager
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_chatwoot_snapshot_contract import populate_fixture, contract
from chatwoot_capture_coordinator import (
    CaptureCoordinator, CaptureError, IntentStore, WRITERS,
)


class FakeBackend:
    def __init__(self):
        self.states = dict.fromkeys(WRITERS, "running")
        self.states.update({"telegram-poller": "stopped", "gemma-drafter": "absent"})
        self.gate = False
        self.identity = "synthetic-test-host"
        self.events = []
        self.fail = set()
        self.held = False
        self.store = None

    @contextmanager
    def maintenance(self):
        if self.held or "maintenance" in self.fail:
            raise CaptureError("maintenance unavailable")
        self.held = True
        try:
            yield
        finally:
            self.held = False

    def step(self, name):
        self.events.append(name)
        if name in self.fail:
            raise CaptureError("injected " + name)

    def target(self):
        return self.identity

    def inventory(self):
        return dict(self.states)

    def fenced(self):
        return self.gate

    def fence(self, value):
        if value and not self.store.exists():
            raise AssertionError("mutation without durable intent")
        self.gate = value
        self.step("fence:" + str(value))

    def stop(self, name):
        if not self.gate or not self.store.exists():
            raise AssertionError("stop before fence or journal")
        self.states[name] = "stopped"
        self.step("stop:" + name)

    def start(self, name):
        if not self.gate or not self.store.exists():
            raise AssertionError("restart before fence or journal")
        self.step("start:" + name)
        self.states[name] = "running"

    def drain(self):
        if not self.gate or self.states["sidekiq"] != "running":
            raise AssertionError("bad drain sequence")
        if any(self.states[n] == "running" for n in WRITERS - {"sidekiq"}):
            raise AssertionError("producers must stop before drain")
        self.step("drain")

    def assert_quiescent(self):
        if not self.gate or "running" in self.states.values():
            raise CaptureError("not quiescent")
        self.step("quiescent")

    def collect(self, generation):
        self.step("collect")
        populate_fixture(generation)

    def assert_recovered(self, initial):
        if self.states != initial or not self.gate:
            raise CaptureError("not recovered")
        self.step("health")


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = IntentStore(self.root / "state")
        self.store.root.mkdir(mode=0o700)
        self.generation = self.root / "generation"
        self.generation.mkdir(mode=0o700)
        self.backend = FakeBackend()
        self.backend.store = self.store
        self.initial = dict(self.backend.states)
        self.coordinator = CaptureCoordinator(self.backend, self.store)

    def assert_restored(self):
        self.assertEqual(self.backend.states, self.initial)
        self.assertFalse(self.store.exists())
        self.assertNotIn("start:telegram-poller", self.backend.events)
        self.assertNotIn("start:gemma-drafter", self.backend.events)

    def assert_unsealed(self):
        self.assertFalse((self.generation / contract.MANIFEST).exists())

    def test_success(self):
        count = self.coordinator.capture(self.generation)
        self.assertEqual(contract.verify_restored(self.generation), count)
        self.assert_restored()
        self.assertFalse(self.backend.gate)
        self.assertLess(self.backend.events.index("drain"), self.backend.events.index("stop:sidekiq"))
        self.assertLess(self.backend.events.index("health"), self.backend.events.index("fence:False"))

    def test_originally_fenced_remains_fenced(self):
        self.backend.gate = True
        self.coordinator.capture(self.generation)
        self.assert_restored()
        self.assertTrue(self.backend.gate)

    def test_partial_fence_failure_retains_recovery_intent(self):
        self.backend.fail.add("fence:True")
        with self.assertRaises(CaptureError):
            self.coordinator.capture(self.generation)
        self.assertTrue(self.backend.gate)
        self.assertTrue(self.store.exists())
        self.assertEqual(self.backend.states, self.initial)
        self.assert_unsealed()
        self.backend.fail.clear()
        self.coordinator.reconcile()
        self.assert_restored()
        self.assertFalse(self.backend.gate)

    def test_unfence_failure_retains_recovery_intent(self):
        self.backend.fail.add("fence:False")
        with self.assertRaises(CaptureError):
            self.coordinator.capture(self.generation)
        self.assertTrue(self.store.exists())
        self.assertEqual(self.backend.states, self.initial)
        self.assert_unsealed()
        self.backend.fail.clear()
        self.coordinator.reconcile()
        self.assert_restored()
        self.assertFalse(self.backend.gate)

    def test_journal_write_failure_precedes_all_mutations(self):
        with patch.object(self.store, "create", side_effect=OSError("synthetic disk failure")):
            with self.assertRaises(OSError):
                self.coordinator.capture(self.generation)
        self.assertEqual(self.backend.events, [])
        self.assert_unsealed()

    def test_partial_stop_failure_restores_original(self):
        self.backend.fail.add("stop:web-drafter")
        with self.assertRaises(CaptureError):
            self.coordinator.capture(self.generation)
        self.assert_restored()
        self.assertFalse(self.backend.gate)
        self.assert_unsealed()

    def test_drain_failure_restores_original(self):
        self.backend.fail.add("drain")
        with self.assertRaises(CaptureError):
            self.coordinator.capture(self.generation)
        self.assert_restored()
        self.assert_unsealed()

    def test_collect_failure_restores_original(self):
        self.backend.fail.add("collect")
        with self.assertRaises(CaptureError):
            self.coordinator.capture(self.generation)
        self.assert_restored()
        self.assert_unsealed()

    def test_restore_failure_leaves_journal_and_fence(self):
        self.backend.fail.add("start:rails")
        with self.assertRaises(CaptureError):
            self.coordinator.capture(self.generation)
        self.assertTrue(self.store.exists())
        self.assertTrue(self.backend.gate)
        self.assert_unsealed()
        self.backend.fail.clear()
        self.coordinator.reconcile()
        self.assert_restored()
        self.assertFalse(self.backend.gate)
        self.assert_unsealed()

    def test_health_failure_keeps_fence(self):
        self.backend.fail.add("health")
        with self.assertRaises(CaptureError):
            self.coordinator.capture(self.generation)
        self.assertTrue(self.backend.gate)
        self.assertTrue(self.store.exists())
        self.assert_unsealed()

    def test_seal_failure_after_recovery(self):
        with patch("chatwoot_capture_coordinator.seal", side_effect=ValueError("synthetic")):
            with self.assertRaises(ValueError):
                self.coordinator.capture(self.generation)
        self.assert_restored()
        self.assertFalse(self.backend.gate)
        self.assert_unsealed()

    def interrupted(self):
        self.store.create({"version": 1, "target": self.backend.target(),
                           "initial": self.initial, "fenced": False})
        self.backend.gate = True
        self.backend.states["rails"] = "stopped"

    def test_interrupted_run_requires_explicit_reconcile(self):
        self.interrupted()
        with self.assertRaises(CaptureError):
            self.coordinator.capture(self.generation)
        self.assertEqual(self.backend.events, [])
        self.coordinator.reconcile()
        self.assert_restored()
        self.assertFalse(self.backend.gate)
        self.assert_unsealed()

    def test_reconcile_refuses_changed_identity(self):
        self.interrupted()
        self.backend.identity = "other-target"
        with self.assertRaises(CaptureError):
            self.coordinator.reconcile()
        self.assertTrue(self.store.exists())
        self.assertEqual(self.backend.events, [])

    def test_reconcile_refuses_unexpected_writer_state(self):
        self.interrupted()
        self.backend.states["telegram-poller"] = "running"
        with self.assertRaises(CaptureError):
            self.coordinator.reconcile()
        self.assertTrue(self.store.exists())
        self.assertEqual(self.backend.events, [])

    def test_exclusive_lock(self):
        with self.store.locked():
            with self.assertRaises(CaptureError):
                self.coordinator.capture(self.generation)
        self.assertEqual(self.backend.events, [])

    def test_external_maintenance_unavailable(self):
        self.backend.fail.add("maintenance")
        with self.assertRaises(CaptureError):
            self.coordinator.capture(self.generation)
        self.assertFalse(self.store.exists())
        self.assertEqual(self.backend.events, [])

    def test_nonempty_generation_refused(self):
        (self.generation / "old").write_text("synthetic")
        with self.assertRaises(CaptureError):
            self.coordinator.capture(self.generation)
        self.assertFalse(self.store.exists())
        self.assertEqual(self.backend.events, [])

    def test_journal_duplicate_key_refused(self):
        self.interrupted()
        self.store.path.write_text('{"version":1,"version":1}')
        with self.assertRaises(ValueError):
            self.coordinator.reconcile()
        self.assertTrue(self.store.exists())
        self.assertEqual(self.backend.events, [])

    def test_unknown_inventory_refused(self):
        self.backend.states["unknown"] = "running"
        with self.assertRaises(CaptureError):
            self.coordinator.capture(self.generation)
        self.assertFalse(self.store.exists())
        self.assertEqual(self.backend.events, [])


if __name__ == "__main__":
    unittest.main()
