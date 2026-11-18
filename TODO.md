# Backup development

## Chatwoot operator-only PROD readiness
- [x] Existing local Chatwoot DB and attachment restore validated in TEST.
- [x] Host-prepared snapshot support implemented on feature/chatwoot-prepared-snapshot-20261118: fresh private stage, manifest verification, failure-before-upload/retention, no Compose override for prepared-only jobs, scoped retention. 23/23 mocked automated tests and real local Restic synthetic restore PASS. Actual Ansible/systemd execution and exact SonarQube gate still required before merge.
- [ ] ★ Implement service-specific sealed Chatwoot capture with coordinated writer drain, DB/storage/checkpoint, and recovery procedure.
- [ ] Isolated remote S3 integration, actual restore, scheduled run and failure monitoring (S3 client provisioning currently blocked; no production storage).
- [ ] Independent review and exact candidate checks before PR/merge.
