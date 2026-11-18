# Backup development

## Chatwoot operator-only PROD readiness
- [x] Existing local Chatwoot DB and attachment restore validated in TEST.
- [ ] ★ Host-prepared snapshot support in existing backup role (feature/chatwoot-prepared-snapshot-20261118): explicitly opt in; no Compose recreation; fresh private stage; verify manifest; fail closed; scoped retention; existing job regression tests.
- [ ] Service-specific sealed Chatwoot capture with writer drain and recovery procedure.
- [ ] Isolated remote S3 integration, actual restore, scheduled run and failure monitoring (S3 client provisioning currently blocked; no production storage).
- [ ] Independent review and exact candidate checks before PR/merge.
