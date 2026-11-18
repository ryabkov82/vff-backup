#!/usr/bin/env python3
"""Capture transaction coordinator; host effects are supplied by a trusted adapter.

No Docker, database, network, credential or restore commands are implemented
here. The adapter must implement externally verified, idempotent operations
and a maintenance exclusion shared with other deployment/test processes.
"""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import tempfile

from chatwoot_snapshot_contract import (
    REQUIRED_WRITERS, OPTIONAL_WRITERS, seal,
)
from verify_prepared_snapshot import check_private, unique

JOURNAL = "capture-intent.json"
WRITERS = REQUIRED_WRITERS | OPTIONAL_WRITERS
PRODUCERS = ("rails", "telegram-business-poller", "telegram-poller",
             "web-drafter", "gemma-drafter")
START_ORDER = ("sidekiq", "rails", "telegram-business-poller",
               "telegram-poller", "web-drafter", "gemma-drafter")
class CaptureError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise CaptureError(message)


def validate_states(states):
    require(isinstance(states, dict) and set(states) == WRITERS,
            "incomplete writer inventory")
    require(all(states[name] in ("running", "stopped") for name in REQUIRED_WRITERS)
            and all(states[name] in ("running", "stopped", "absent")
                    for name in OPTIONAL_WRITERS), "unsupported writer state")


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class IntentStore:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / JOURNAL

    @contextmanager
    def locked(self):
        info = self.root.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
                and stat.S_IMODE(info.st_mode) == 0o700, "unsafe journal directory")
        fd = os.open(self.root / ".coordinator.lock",
                     os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                    and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1,
                    "unsafe coordinator lock")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise CaptureError("capture coordinator busy") from None
            yield
        finally:
            os.close(fd)

    def exists(self):
        return self.path.exists() or self.path.is_symlink()

    def load(self):
        info = self.path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1
                and info.st_size <= 8192, "unsafe capture journal")
        data = json.loads(self.path.read_text(), object_pairs_hook=unique)
        require(isinstance(data, dict) and set(data) ==
                {"version", "target", "initial", "fenced"}, "invalid capture journal")
        require(type(data["version"]) is int and data["version"] == 1
                and isinstance(data["target"], str)
                and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", data["target"])
                and type(data["fenced"]) is bool, "invalid capture intent")
        validate_states(data["initial"])
        return data

    def create(self, data):
        require(not self.exists(), "unresolved capture intent")
        fd, tmp = tempfile.mkstemp(prefix=".intent-", dir=self.root)
        try:
            with os.fdopen(fd, "w") as out:
                json.dump(data, out, sort_keys=True)
                out.flush()
                os.fsync(out.fileno())
            # A no-replace link publishes a complete record, even if a stale
            # journal appeared unexpectedly. The temporary link is then removed.
            os.link(tmp, self.path)
            os.unlink(tmp)
            sync_dir(self.root)
        finally:
            Path(tmp).unlink(missing_ok=True)

    def clear(self):
        self.path.unlink()
        sync_dir(self.root)


class CaptureCoordinator:
    """Backend contract:
    maintenance(): context manager enforcing shared external exclusion.
    target(): stable identity of the exact reviewed deployment.
    inventory(): complete writer inventory, raising on unknown writers.
    fenced(): independently observed ingress gate state.
    fence(bool): idempotent, validated ingress gate mutation.
    stop(name), start(name): exact-container operation, never recreate.
    drain(): verify all delivery work drained while Sidekiq is running.
    assert_quiescent(): independently verify no writers or external writes.
    collect(generation): collect DB, storage, checkpoint and verified evidence.
    assert_recovered(initial): health and exact original writer/transport checks.
    """
    def __init__(self, backend, store):
        self.backend = backend
        self.store = store

    def observe(self):
        states = self.backend.inventory()
        validate_states(states)
        return dict(states)

    def set_fence(self, value):
        self.backend.fence(value)
        require(self.backend.fenced() is value, "ingress fence not confirmed")

    def restore(self, intent):
        require(self.backend.target() == intent["target"], "deployment identity changed")
        initial = intent["initial"]
        current = self.observe()
        # Unexpected states may be concurrent changes: do not overwrite them.
        require(all(current[name] == original or
                    (original == "running" and current[name] == "stopped")
                    for name, original in initial.items()), "writer identity/state drift")
        self.set_fence(True)
        for name in START_ORDER:
            if initial[name] == "running" and current[name] == "stopped":
                self.backend.start(name)
                require(self.observe()[name] == "running", "writer restart not confirmed")
        require(self.observe() == initial, "writer restore differs")
        self.backend.assert_recovered(initial)
        self.set_fence(intent["fenced"])
        self.store.clear()

    def reconcile(self):
        with self.store.locked(), self.backend.maintenance():
            require(self.store.exists(), "no pending capture intent")
            self.restore(self.store.load())

    def capture(self, generation):
        with self.store.locked(), self.backend.maintenance():
            require(not self.store.exists(), "pending recovery; reconcile first")
            generation = Path(generation)
            check_private(generation, directory=True)
            require(not any(generation.iterdir()), "generation not empty")
            require(not self.store.root.resolve().is_relative_to(generation.resolve()),
                    "journal cannot reside inside generation")
            initial = self.observe()
            initial_fence = self.backend.fenced()
            target = self.backend.target()
            require(type(initial_fence) is bool and isinstance(target, str)
                    and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", target),
                    "invalid deployment preflight")
            require(initial["sidekiq"] == "running", "Sidekiq must be available for drain")
            intent = {"version": 1, "target": target, "initial": initial,
                      "fenced": initial_fence}
            self.store.create(intent)
            try:
                self.set_fence(True)
                for name in PRODUCERS:
                    if initial[name] == "running":
                        self.backend.stop(name)
                        require(self.observe()[name] == "stopped", "producer stop not confirmed")
                self.backend.drain()
                self.backend.stop("sidekiq")
                self.backend.assert_quiescent()
                self.backend.collect(Path(generation))
                self.backend.assert_quiescent()
            except BaseException:
                # A failed mutation can already have changed its real target.
                # Observe instead of relying on the successful-call history.
                self.restore(intent)
                raise
            self.restore(intent)
            # A failed restore retains the journal and never reaches sealing.
            return seal(Path(generation))
