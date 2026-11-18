#!/usr/bin/env python3
"""Exact-container lifecycle component for the reviewed VM200 TEST layout.

This is not a complete capture backend. The caller must own shared maintenance
exclusion and validate the host/deployment identity before permitting mutations.
Docker inspection is restricted to non-secret identity and state fields.
"""
from __future__ import annotations
import hashlib
import json
import re
import subprocess

from chatwoot_capture_coordinator import CaptureError, require

PROJECT = "vff-chatwoot"
MANDATORY = frozenset(("rails", "sidekiq", "telegram-business-poller",
                       "postgres", "redis"))
OPTIONAL = frozenset(("telegram-poller", "web-drafter"))
LEGACY = {
    "vff-chatwoot-telegram-poller": "legacy-standalone-poller",
    "vff-chatwoot-gemma-drafter": "gemma-drafter",
}
KNOWN = {f"{PROJECT}-{service}-1": service for service in MANDATORY | OPTIONAL}
KNOWN.update(LEGACY)
HEX = re.compile(r"[0-9a-f]{64}")
IMAGE = re.compile(r"sha256:[0-9a-f]{64}")
STOPPED = frozenset(("exited", "created"))
INSPECT_FORMAT = (
    '[{{json .Id}},{{json .Name}},{{json .Image}},{{json .State.Status}},'
    '{{json (index .Config.Labels "com.docker.compose.project")}},'
    '{{json (index .Config.Labels "com.docker.compose.service")}}]'
)


def docker_command(args, timeout=30):
    """Never return raw stderr; Docker errors may contain private metadata."""
    try:
        result = subprocess.run(["docker", *args], check=False, text=True,
                                capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise CaptureError("Docker command unavailable") from None
    if result.returncode != 0:
        raise CaptureError("Docker command failed")
    return result.stdout


def listed_ids(output):
    values = output.splitlines()
    require(all(HEX.fullmatch(x) for x in values) and
            len(set(values)) == len(values), "invalid container list")
    return set(values)


def parse_inventory(rows, expected):
    seen = set()
    names = set()
    result = {}
    for line in rows.splitlines():
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            raise CaptureError("invalid container metadata") from None
        require(isinstance(row, list) and len(row) == 6
                and all(isinstance(x, str) for x in row[:4]),
                "invalid container metadata shape")
        cid, name, image, state, project, service = row
        require(cid in expected and cid not in seen and name.startswith("/")
                and name not in names and IMAGE.fullmatch(image),
                "unexpected container identity")
        seen.add(cid)
        names.add(name)
        name = name[1:]
        if name not in KNOWN:
            require(state in STOPPED, "unknown active container")
            continue
        key = KNOWN[name]
        if name in LEGACY:
            require(state in STOPPED, "unsupported standalone writer active")
        else:
            require(project == PROJECT and service == key,
                    "unexpected Compose identity")
        require(state == "running" or state in STOPPED,
                "container in transitional or unsupported state")
        result[key] = (cid, image, "running" if state == "running" else "stopped")
    require(seen == expected, "incomplete container inspection")
    require(MANDATORY <= set(result), "mandatory container missing")
    require(all(result[name][2] == "running" for name in ("postgres", "redis")),
            "dependency not running")
    return result


class DockerLifecycle:
    def __init__(self, deployment, assert_maintenance, runner=docker_command):
        require(isinstance(deployment, str) and HEX.fullmatch(deployment),
                "reviewed deployment fingerprint required")
        self.deployment = deployment
        self.assert_maintenance = assert_maintenance
        self.runner = runner
        self.pinned = None

    def scan(self):
        args = ["ps", "-a", "-q", "--no-trunc"]
        ids = listed_ids(self.runner(args))
        require(bool(ids), "empty container inventory")
        rows = self.runner(["inspect", "--format", INSPECT_FORMAT, *sorted(ids)])
        require(listed_ids(self.runner(args)) == ids, "container inventory changed")
        state = parse_inventory(rows, ids)
        identity = {name: list(value[:2]) for name, value in sorted(state.items())}
        fingerprint = hashlib.sha256(json.dumps(
            [self.deployment, identity], sort_keys=True,
            separators=(",", ":")).encode()).hexdigest()
        if self.pinned is None:
            self.pinned = fingerprint
        require(fingerprint == self.pinned, "deployment container identity changed")
        return state, fingerprint

    def target(self):
        return self.scan()[1]

    def inventory(self):
        state, _ = self.scan()
        return {name: state[name][2] if name in state else "absent"
                for name in (MANDATORY - {"postgres", "redis"}) | OPTIONAL
                | {"gemma-drafter"}}

    def transition(self, name, desired):
        require(name in (MANDATORY - {"postgres", "redis"}) | OPTIONAL,
                "unsupported lifecycle target")
        # Require the shared external lease at every operation.
        self.assert_maintenance()
        require(self.pinned is not None, "deployment identity not pinned")
        state, _ = self.scan()
        require(name in state, "writer absent")
        cid, _, current = state[name]
        if current == desired:
            return
        command = ["start", cid] if desired == "running" else ["stop", "--time", "60", cid]
        self.assert_maintenance()
        self.runner(command, timeout=90)
        observed, _ = self.scan()
        require(observed[name][2] == desired, "container transition not confirmed")

    def stop(self, name):
        self.transition(name, "stopped")

    def start(self, name):
        self.transition(name, "running")
