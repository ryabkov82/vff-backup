# Chatwoot prepared snapshot / recovery contract (TEST candidate)

Status: opt-in prepared backup role integrated with the actual VM205 functional
TEST adapter in vff-support-agent PR #86. The version-2 contract requires
capture/isolated-restore evidence sealed with the exact staged artifacts;
`--contract-only` explicitly selects the historical version-1 structural check.
The version-2 receipt and SHA256 seal establish artifact integrity and trusted
adapter evidence, not an independent attestation or signature. Local native
role execution and restore passed twice, including systemd on 2026-11-24.
No off-host operational job, timer, alert or PROD deployment is accepted yet.

## Fourth-audit safety correction (2026-11-24)

IV-03: preflight and runtime validate root-controlled ancestry before environment loading or execution. The VM205 capture wrapper imports only the reviewed protected module bundle; the deployment/configuration boundary is protected as well. IV-05: the reserved Chatwoot jobs require the Chatwoot semantic verifier, and generic jobs cannot accept recognizable Chatwoot dumps. IV-06: v2 accepts only explicitly inventoried configuration, recognized native database data and checkpoint files, and database-referenced attachment blobs verified by checksum. Extra sealed files, directories, symlinks and hardlinks fail closed. Existing backups are not deleted.

Native VM205 `PASS_VM205_NATIVE_PREPARED_ROLE`: 140.07 s, 110 files, eight database table counts, two blobs, real archive rejection, delivery fence and Restic integrity PASS; all 13 containers resumed. Ephemeral local encrypted repository removed; no new retained/off-host recovery point. Timer remains disabled. This is TEST evidence, not PROD authorization or a new off-host/physical-host-loss acceptance.

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


### 24 Nov — actual DB + Redis delivery fence and retained local recovery
- [x] F-17: after native writer stop, query scoped PostgreSQL outgoing/template records as well as the Redis inventory. Fail closed on foreign/multiple channels, unconfirmed/failed/invalid acknowledgements, invalid statuses or persisted guard reservations even with no queued jobs. Restore requires the same exact DB counts and zero Redis delivery work. The version-2 prepared validator now requires the sealed `source/delivery-fence.json` evidence; earlier ephemeral v2 generations predate this extra requirement.
- [x] Native restored scratch SQL transaction injected a failed, unconfirmed reserved outgoing row; validator refused it and actual rollback restored the exact previous aggregate. No write to primary DB, no replay or external Telegram. Native systemd prepared-role acceptance `job_90873c090863` PASS, 134.17s, 3 DBs, 12 restored services, 110 files, 239 messages, 8 scoped outgoing, 2 blobs. Retained encrypted local Restic snapshot `e330217081dc1ff7088cda1ee2ebaa5734e4e1a46b21f31c456f3e6a7b387b74`, read-data integrity and restored manifest PASS; all 27 previous snapshots preserved. Private receipt `recovery/reports/vm205-prepared-role-d2cd2ff1a5.json`. This is a local recovery point, not off-host or machine-loss acceptance.
- [x] VM205 base operator Compose was the only observed nonsecret runtime file mismatch (old default live flag). Compared complete native combined Compose before/after, only disabled legacy poller default can differ; explicit TEST3 live flag unchanged. Atomic sync PASS `test_vm205_release_config_readback.py`: new base SHA256 d2293a0a0d22cbcea7cfe275199a731771918e4fa1f519466563821d955bf667, original 13 containers unchanged, zero recreated. The retained snapshot predates that config-only sync and includes the previous base file; never restore live poller automatically; reviewed dry-run base and explicit activation remain required.
- [ ] ★ Remaining: authorized dedicated existing S3 TEST prefix/route, clean-target off-host recovery and scheduled job/staleness/alerts/RPO/RTO. Public HTTPS/operator ACL/production adapter/target and independent review remain open. No production mutations.


### 24 Nov — existing VFF TEST S3 on VM201, scoped VM205 access
- Owner confirmed VFF-only TEST storage should be placed on an existing VFF AI-LAB VM. No demand for a general-purpose S3 service established. VM201 already runs VFF's dedicated TEST S3 (Silo/MinIO-compatible, pinned image ID sha256:08ce1505b5eacefbad1ca07159b242b9ce045a11c2817e3bc257b25f77bbc02d, TLS 10.10.10.156:19443), previously used for VM200 synthetic Restic. Do not deploy a second container or VM, change the old bucket, or connect PROD.
- [x] Created separate `vff-chatwoot-vm205-test` bucket and `vff-vm205-test-backup` user with its own policy; actual Silo IAM readback limits access to this bucket and object writes/reads/deletes to backup/*. Both existing and new buckets present, new user enabled; Silo container ID/start time unchanged. Provisioning source in vff-backup tests/provision_vm205_test_s3.py (f10bb4313886bd9b32443f5d90d8c5298969670f0b7998258549abe99850869e). New scoped credentials and independent Restic password held root-private 0600 under VM201 /opt/vff-chatwoot-backup-test/vm205-recovery (0700); not exposed to chat/Git/gateway. This is a lab recovery copy outside VM205, not proof of recovery after physical PVE loss. Production secrets untouched.
- [x] VM205 model isolation now includes exactly one authorized VFF storage exception: root-owned host process to VM201 TCP/19443, established return traffic. All other MODEL IPs/ports and Docker forwarding remain denied; no model application access or shared repository/credentials. Git nft file hash c3e3283d94ee1b804f479e5dd377df85fbf1c598132f88946b96d6143e350b60 matches persistent /etc/vff/functional-model-isolation.nft. Previous config backed up root-private. Native nft --check PASS; actual root HTTPS with pinned TEST CA -> 200, verify=0; unprivileged connection and root connections to VM201:8081 and VM200:3000 all denied. Existing active MODEL and TEST3 services remained running. Focused guard tests 4/4. Existing TEST leaf expires 2026-12-18; renewal required before then, no global CA trust or TLS bypass.
- [ ] ★ Remaining: transfer only the generated VM205 public recipient key (not the private key) through authorized management path. First vmcp get succeeded, public fingerprint 5a4eb0c8b831f4ddd0d3146b1943c8be8681e494beeb02d075cc3646e1944f00; subsequent vmcp put into VM201 rejected by OpenAI tool (file absent), not SSH or Silo. VM205 recipient private file 0600 retained. Separate storage provisioning succeeded without credential transfer; encrypted envelope export/install not yet executed. Local S3 I/O negative-test authoring also refused by tool, so actual scoped authenticated I/O and cross-bucket refusal remain untested. CA certificate transferred and verified. Next use existing source's export-client/install-client and native Restic cross-VM backup/restore with exact sealed snapshot and distinct credentials. No plaintext key transport. Only after that mark off-VM DR; scheduled backup/alerts and physical-host loss/RPO/RTO remain separate.
