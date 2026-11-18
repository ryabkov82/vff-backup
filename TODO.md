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
- [x] Private TLS S3 fixture on VM201: pinned Silo image, dedicated private bind and bucket, distinct admin and scoped backup identity, 1 GiB memory/1 CPU, no published console, existing SHM unchanged. Initial provisioning failed after ready and stopped container; verified empty bucket/user state, restarted exact container, created bucket/policy and completed scoped user bootstrap. No disk quota; fixture is not independent physical-host DR.
- [x] Real remote synthetic Restic VM200 → VM201: operator-provisioned encrypted systemd credentials loaded using their exact embedded names (first unit failed 243/CREDENTIALS due name mismatch, corrected); TLS CA SHA256 dd7c2113c324ffc50c7eb7bb0e951fc4df094359cf360058a49dc65c244db844 matched on both VMs. Snapshot 1f56b227d34ec97ce29ee78ebb651793a390a24697f8114c99b5221b2c041c1f, 4096 bytes, exact restore and check --read-data PASS. No Chatwoot customer data uploaded.
- [ ] ★ Actual prepared backup role against remote S3: renderer and synthetic success/failure/retention/restore scenario committed, existing reliability tests 24/24 PASS; separate controller bash -n denied by allowlist; live scenario call blocked by OpenAI safety controls, do not retry or reroute. No remote role PASS claimed. Independently review scope-denial tests, service-specific Chatwoot capture/restore, actual timer and alerting; coordinate concurrent model tests before touching writers.
- [ ] Exact candidate SonarQube, independent audit, PR. No PROD/main or existing SHM service changes.

## Independent monitoring regression — 2026-11-18
- [x] Reproduced per-job textfile overwrite: multi-job regression failed before fix. Separate per-job metrics files, atomic publication, 0644 under private umask, early environment error status, publication failure and invalid/ambiguous jobs covered. Existing metric names/labels unchanged; controlled migration/rollback documented. No real credentials or external storage used.
- [x] Service/timer template rendering (legacy/prepared and metrics on/off); remove static ProtectHome=true / WorkingDirectory=/root conflict without weakening sandbox. 29/29 offline tests PASS; not a live systemd execution. Existing remote-role scenario remains blocked, not retried.
- [ ] ★ Exact-SHA Sonar, deployment/migration verification, live capture/restore, timer/alerting and independent audit remain pending.

- [x] systemd-analyze calendar accepted default `*-*-* 02:30:00`; this verifies expression only, not scheduled execution. Post-commit 4a58dd2: 29/29 offline tests PASS, pushed. Sonar reader on VM141 returned HTTP 404 for `ryabkov82_vff-backup` at exact code SHA 4a58dd25b59f54d7fd69df25e8a7a0939a783adf; project existence/access and authorized analysis setup need verification. No new token, no shared project scan or quality gate claim.

## Chatwoot capture validation (independent offline work)
- [x] Implemented Chatwoot-specific staging validation/sealing contract, dynamic independently supplied counts, blob checksums, Business offsets, writer evidence; integrated opt-in `prepared_snapshot.kind: chatwoot` and reject unknown kinds before backup. 26 standalone contract tests and 32 existing/extended role tests PASS (58 total), all synthetic/mocked. Generic-only manifest cannot pass Chatwoot verifier. No actual DB dump or transport proof from these tests.
- [x] Documented capture/restore prerequisites in docs/chatwoot-snapshot-contract.md. Actual VM200 writer inventory shows Website AI worker active; no interruption or live capture. No blocked remote-role retry.
- [ ] ★ Implement and independently test real capture/restore adapter and durable recovery state; coordinate exclusive TEST window before changing writers; actual scheduled backup/alerting still unverified.
- [ ] Separate Sonar setup: existing scanner allows only support-agent and vpnbot. vff-backup project/readback 404; requires authorized private project and analysis config before gate.

## Capture orchestration and recovery journal — 2026-11-18
- [x] Implemented transport-independent capture coordinator: private persistent intent before mutations, exclusive lock, ordered fence/stop/drain, confirm quiescence, restore exact original state and seal only after recovery. Explicit reconciliation refuses deployment/writer identity drift or unresolved intent. Journal is separate from disposable generation.
- [x] 19 synthetic coordinator fault-injection tests PASS, including partial mutation, failed health/restore, ingress failure, interrupted run, journal failure, no restart of stopped/absent writers. Complete offline suite 77/77 PASS. Documentation identifies adapter contract and crash/reconciliation limits.
- [ ] ★ Implement reviewed real host backend (shared E2E/deployment exclusion, ingress and exact Docker state, queue drain, DB dump/restore and evidence), then exclusive TEST maintenance acceptance. Existing unrelated E2E not interrupted; previously blocked remote-role invocation not retried. Real schedule/alerting and Sonar exact SHA still pending.

- [x] Read-only VM200 inventory after coordinator commit 24a08cf: Rails, Sidekiq, Business poller, Website AI-worker, PostgreSQL and Redis active; legacy Compose poller plus old standalone poller/Gemma stopped. AI-worker recently restarted, confirming need for shared exclusive interval, not a Compose-label-only inventory. Chatwoot storage and Business checkpoint volumes present, Rails bound loopback :3000, nginx ingress :3080, 50 GiB free. Actual nginx site is /etc/nginx/conf.d/vff-chatwoot.conf (not sites-enabled); inspect exact effective routes before designing a Chatwoot-only fence, do not stop shared nginx. No live changes, no S3/credentials access. Docker label-format probe failed due argv formatting; ordinary container/volume inventory succeeded. Postcommit 77/77 offline PASS; branch pushed.
