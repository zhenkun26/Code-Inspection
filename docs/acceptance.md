# Verification scope and limits

This repository starts from a sanitized file-only snapshot with independent Git history. Private development commits, PR discussions, run links and session records are excluded. Earlier checks do not transfer as acceptance of a new commit.

| Capability | What verification establishes / remaining limit |
| --- | --- |
| Package layout, references and digests | Maintained structure and hygiene checks cover the selected files, router, four stage entries, shared references and exact checksum coverage. Keep the complete checkout together. |
| Local regression suite | `validation/run_checks.py --keep-artifacts` runs ordinary and optimized Python, security/file/record controls, copied-layout checks and both isolated demo variants. Fixed guard-removal controls are targeted regressions, not comprehensive mutation testing. |
| Hosted CI and native Windows | Read the [current workflow run](https://github.com/zhenkun26/Code-Inspection/actions/workflows/ci.yml) for the exact revision. The matrix is Ubuntu24.04/Python3.9 and3.13 plus Windows2025x64/Python3.13. Windows support is limited by the [native contract](windows-support.md); a skipped/unavailable native check is not PASS. |
| Host discovery/loading and cancellation/resumption | Unverified for this fresh checkout. Earlier reported host feedback is historical, not an independently inspected trace for this version. Offline layout checks do not prove host loading. |
| Hosted PR/merge and production providers | Not integrated. Tiny isolated Git operations are real; person decisions and delivery examples are simulations. The included Git adapter does not authorize real-project actions. |

Local receipts and private audit records stay outside distributed files. CI logs are visible according to repository visibility and must exclude secrets/private input. Results bind content/version and environment; passing tests do not establish model-review quality, production safety, scanner effectiveness, identity authentication or a sandbox.

The 54-case lifecycle integration suite is partitioned into two deterministic, disjoint groups per ordinary/optimized mode. Coverage checks compare the selected IDs to the actual suite so future cases cannot silently disappear. Each group retains the 180-second command limit; fixture CLI calls retain 15 seconds and Git calls 10 seconds. These are separate scopes. The CI outer job limit is 15 minutes on Ubuntu and 20 minutes on Windows, allowing bounded aggregate execution of checks and mutations without changing individual limits or platform combinations. Test counts may grow with later cases; receipts record the actual selection.

The checker announces its receipt before execution, writes child output directly to per-command files and checkpoints completion/timing. A command timeout records failure and partial output, then stops with remaining checks NOT_RUN. Only trusted self-owned test output may be printed to CI. Direct-child timeout handling does not establish termination of the entire process tree; descendant cleanup remains unknown. An externally killed runner cannot guarantee a final receipt. Retained local files are not automatically uploaded as CI artifacts; public logs include a bounded failure tail, and completed-job status is separate from individual checks that did not run.

For a real host run, record exact version/scope, granted actions and actual entry/reference reads. Discovery, explicit loading, cancellation and live platform behavior are separate claims. Follow the existing [shared contract](../skills/pre-pr-review/references/contract.md) and [setup](codex-setup.md); no alternative state model or global installation is introduced.
