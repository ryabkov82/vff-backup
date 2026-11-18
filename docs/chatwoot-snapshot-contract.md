# Chatwoot prepared snapshot / recovery contract (TEST candidate)

Status: offline validator integrated into the existing prepared backup role via
`prepared_snapshot.kind: chatwoot`. This opt-in selects the Chatwoot verifier;
unknown kinds are rejected and existing generic jobs remain unchanged. Offline
positive/negative role tests use fake transport and synthetic evidence. The real
host capture adapter, restore adapter, scheduling and alarm acceptance are not
implemented/verified by this module. Do not schedule as an operational job yet.

## Integration prerequisites

Use the existing `vff-backup` prepared snapshot job; do not create a second
backup system. VM200 is the TEST source and VM201 the isolated TLS S3 target.
Direct Restic synthetic transport/restore already passed. The actual remote
role execution remains blocked; do not retry the blocked invocation through
another route. The active Website AI worker on VM200 must not be stopped during
model E2E. Coordinate a dedicated TEST maintenance window and reconcile the
current Compose inventory before any writer-state change. No PROD action.

## Expected generation

A newly allocated private generation, owner-only directories/files, no
symlinks or hardlinks. The trusted capture adapter runs locally and passes the
generation to `chatwoot_snapshot_contract.seal(path)`. Do not source or execute
captured config. All evidence and content remain in the private generation.

- `chatwoot.dump`: custom PostgreSQL dump. The validator checks its header;
  it does not establish that the archive can actually be restored.
- `config/`: exact inventory in `capture-evidence.json`, at least the
  base Compose, .env, applied privacy override and Business poller source.
  The adapter must derive the additional required paths from the reviewed
  effective Compose/deployment, not accept arbitrary caller paths. Include
  revision/image pins, required TLS/config inputs and no historical dumps,
  logs/traces, or unnecessary AI secrets.
- `storage/`: actual ActiveStorage file tree, including variants if present.
  Every blob in the independently queried database inventory must have
  matching size and ActiveStorage checksum.
- `business-state/channel-N.offset`: exact canonical nonnegative checkpoint
  per enabled Business channel; no unexpected checkpoint files.
- `capture-evidence.json`: strict version 1, exact keys:
  `counts`, `dump_counts` (eight tables from the module; exact counts from
  independent source and isolated restore queries), `config`, `images`
  (immutable local image IDs for five operator services), `business_offsets`,
  `blobs` (key, byte_size, checksum), `unresolved_attachment_refs`,
  `pending_delivery_jobs`, `ingress_fenced`, `writer_states`,
  `other_active_writers`. Counts are nonnegative integers, no hardcoded
  historical corpus thresholds. Required writers stopped; optional AI/legacy
  writers stopped or absent. Ingress fenced, zero unknown active writers,
  unresolved references and pending delivery jobs.
- `.vff-snapshot.json`: generated only after successful validation, exact
  SHA256 inventory in the existing role's format. The seal is not a signature;
  it detects later drift, not a malicious or buggy trusted producer.

## Required adapter sequence

1. Preflight TEST identity, exclusive lock, disk space, pinned effective
   configuration and running/stopped state of every writer. Refuse unknown
   writers or concurrent E2E. Retain rollback state before any changes.
2. Fence the existing ingress and stop message producers including Business
   poller and AI workers. Drain Sidekiq while Redis/DB remain healthy, verify
   queued/retry/scheduled delivery state, capture failure if delivery cannot
   be drained; do not delete or replay jobs. Stop Sidekiq and Rails; verify
   the complete actual writer inventory and no external write connections.
3. While quiescent, take fresh logical dump, storage, checkpoint and effective
   configuration. Query source counts and blob/reference inventory. Verify
   the dump by restoring to an isolated scratch DB (no Rails, AI, Telegram,
   email or push processes connected); query it independently. No raw
   contents or secrets in logs. Captured values are not trusted merely
   because the producer wrote evidence.
4. Recheck actual quiescence and checkpoint, restore exactly the recorded
   running states in failure/success paths, and verify recovery. Do not
   start a previously stopped AI/legacy writer. If rollback cannot be
   verified, fail the job and alert the operator. On crash/kill, require
   persistent recovery state and explicit safe reconciliation.
5. Seal with the validator, then existing prepared-job upload/retention.
   No seal/upload on failed capture or restoration of original service
   state. Check snapshot ID, success/failure/staleness metrics and clean up
   the private temporary generation according to the existing role.
6. Isolated disaster recovery: Restic decrypt and restore; verify manifest
   and contract; perform real pg_restore and independently requery counts,
   blob references/checksums and checkpoint. Validate operator-only behavior
   with outbound transports disabled until explicitly accepted. Record
   elapsed recovery time and do not equate same-hypervisor TEST with
   independent physical-host disaster recovery.

The validator trusts evidence supplied by the adapter. Synthetic test
fixtures use a deliberately fake PGDMP header; PASS is not real database
restore, writer drain, transport, timer or authorization evidence.

## Capture coordinator and crash recovery (offline-tested)

`chatwoot_capture_coordinator.py` implements the host-independent transaction.
It is not a deployable host adapter and does not invoke Docker or a database.
The operator must use an adapter bound to an exact deployment identity and a
maintenance exclusion shared by deployment/E2E tools. A private, persistent
journal directory must be outside the disposable snapshot generation. The
coordinator takes an exclusive lock and persists/fsyncs the original writer
and ingress state before any mutation. It fences ingress, stops producers,
drains Sidekiq, stops Sidekiq, verifies quiescence before and after collection,
then restores only originally running writers, checks recovery and restores the
original ingress state before sealing. A failed recovery leaves the intent;
sealing is prohibited. Reconcile explicitly under maintenance exclusion after
re-observing the same deployment and complete writer inventory; never guess
or restart previously absent/stopped services. A crash during journal
publication can leave a temporary hardlink: if the strict journal loader
rejects it, reconcile with an administrator; no automatic deletion of an
ambiguous intent. If an ingress mutation fails, its actual state must be
observed rather than assuming it remained fenced.

Backend requirements: real shared lock, exact container identities, idempotent
stop/start without recreation, positive confirmation of ingress fence and
full writer inventory, queue drain and external-write exclusion, real dump
restore/evidence collection, and actual health postchecks. A journal stores
intent, not proof those backend operations are correct. Current tests use a
synthetic backend with fault injection; no live process, credentials, or
remote repository was accessed by this coordinator.
