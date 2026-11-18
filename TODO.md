# Third audit corrections
Global: operator-only Chatwoot readiness, existing backup safety. Separate feature from d124d5c; no deployment, no extra capture framework.
- [x] F-03/F-04: new private staging root, exact host/tag and unambiguous full snapshot ID, no fallback, no interpolation into shell. Existing target refused even with force; new data/ subdir documented. 13 offline helper/role-structure tests PASS. Actual Ansible/Restic execution not tested.
- [x] F-16: first failed pause aborts before Restic and EXIT trap unpauses successful earlier containers. 20 mocked backup tests PASS. Legacy dump-before-pause ordering unchanged; no claim of coherent live snapshots.
- [ ] ★ Independent review and live testing solely on approved future isolated route; no deployment or secret operations now.
- [ ] Applicable tests, review, exact SHA Sonar before merge. PROD preparation F-01/F-17/F-18 separately; no live backup or restore operations in this branch.

- [ ] ★ Real local Restic acceptance of the new F-03/F-04 helper (synthetic bytes only, no S3, Chatwoot or live backup). Use isolated VM103, non-installed Ubuntu package, explicit host/tag, wrong-host and wrong-snapshot rejection; compare actual restored bytes and private target. Existing 33 mocked checks remain separate. No rollout.
