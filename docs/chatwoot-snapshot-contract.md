# Chatwoot prepared snapshot / recovery contract (TEST candidate)

Status: opt-in prepared backup role integrated with the actual VM205 functional
TEST adapter in vff-support-agent PR #86. The version-2 contract requires
capture/isolated-restore evidence sealed with the exact staged artifacts;
`--contract-only` explicitly selects the historical version-1 structural check.
The version-2 receipt and SHA256 seal establish artifact integrity and trusted
adapter evidence, not an independent attestation or signature. Local native
role execution and restore passed twice, including systemd on 2026-11-24.
No off-host operational job, timer, alert or PROD deployment is accepted yet.

## Current VM205 adapter

Use the existing role with `prepared_snapshot.kind: chatwoot`, an approved
private stage, root-owned capture program, and `schedule_enabled: false`
until acceptance. Install the reviewed `vff-vm205-capture` wrapper and its
exact agent operations modules on VM205 only. Do not reuse VM200/201 secrets.
The adapter records exact original writer IDs durably before quiescence,
checks actual Redis deliveries after the stop, captures the three databases,
attachments, effective configuration, image IDs and scoped checkpoint, and
restores into isolated containers without starting the scratch Telegram
consumer. It compares eight DB table counts and actual attachment hashes,
resumes original writers and checks HTTPS; only then is the prepared tree
sealed. `--recover` is an explicit VM205-only operation for the persistent
interruption marker, refuses a mismatched container identity, and requires
operator review. Actual abrupt-kill recovery is not yet proven.

Native local role PASS: 142.35 s under systemd; 109 restored files, two blobs,
239 messages, 113 conversations, 125 contacts, two accounts/inboxes.
The temporary encrypted Restic repository was removed; this is not a retained
DR restore point. Actual pg_restore rejected the fake custom-dump header.
Existing MinIO target/TEST prefix authorization, off-host clean-target restore,
scheduled run and alerts/RPO/RTO remain open. The following version-1 contract
and VM200/201 discussion are historical, not the current integration route.

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
