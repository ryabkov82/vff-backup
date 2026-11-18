# Backup development

## Chatwoot operator-only PROD readiness
- [x] Existing local Chatwoot DB and attachment restore validated in TEST.
- [x] Host-prepared snapshot support implemented on feature/chatwoot-prepared-snapshot-20261118: fresh private stage, manifest verification, failure-before-upload/retention, no Compose override for prepared-only jobs, scoped retention. 23/23 mocked automated tests and real local Restic synthetic restore PASS. Actual Ansible/systemd execution and exact SonarQube gate still required before merge.
- [ ] ★ Implement service-specific sealed Chatwoot capture with coordinated writer drain, DB/storage/checkpoint, and recovery procedure.
- [ ] Isolated remote S3 integration, actual restore, scheduled run and failure monitoring (S3 client provisioning currently blocked; no production storage).
- [ ] Independent review and exact candidate checks before PR/merge.

- [x] Independent manifest audit (separate branch): reproduced silent os.walk traversal failure; regression with injected scandir error failed before fix; reject traversal errors; 24/24 mocked backup tests PASS after fix. No live capture or shared worktree changes.
- [ ] ★ Integrate independent manifest fix via PR; run exact-SHA Sonar gate and applicable role/restore checks. Verify retention isolation if sharing repositories.

## Remote TEST S3 acceptance (VM200 → VM201), 2026-11-18
- [x] Owner installed encrypted Restic credentials on VM200; independently verified owner/mode/size (no plaintext read), Restic 0.16.4 installed; VM201 Silo image present, dedicated port free.
- [ ] ★ Prepare private TLS S3 service on VM201 (separate admin and scoped backup identity, unique bucket, resource limits); verify health and isolation.
- [ ] Run synthetic-only backup, exact restore and integrity check from VM200 using owner-provisioned credentials; no secret values in output or Git.
- [ ] Integrate service-specific Chatwoot capture, restore, schedule and failure metrics; coordinate concurrent work before touching live TEST writers.
- [ ] Exact candidate SonarQube, independent audit, PR. No PROD/main or existing SHM service changes.
