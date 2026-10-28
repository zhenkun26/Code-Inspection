# Contributing

Small, evidence-backed changes are welcome. For a new feature or architecture change, first describe the problem and proposed scope in an issue. For a bug, include a minimal sanitized reproduction, exact revision, platform and expected/actual behavior. Follow [SECURITY.md](SECURITY.md) for sensitive reports.

## Keep the contract intact

- Preserve the four-stage workflow, explicit human decisions, version-bound evidence and honest missing-capability states. The [workflow](docs/workflow.md) and shared [contract](skills/pre-pr-review/references/contract.md) are the starting points.
- Keep the default path low dependency. New providers, data destinations or execution permissions require a separate design decision; do not silently enable them.
- Keep English and Chinese README claims aligned. Distinguish simulations, local Git tests and actual platform evidence.
- Include only material you have the right to contribute. Preserve third-party notices where applicable; source links alone do not license copied text or code. Original project contributions use the repository's [MIT License](LICENSE).

## Validate a change

Use an existing supported Python/Git environment and a checkout. Before validation, update `SHA256SUMS` after intentional edits: exactly one SHA-256 entry for each selected distributable file, excluding the checksum file itself. The hygiene check verifies the set and bytes. Then run:

```sh
python3 -B validation/run_checks.py --keep-artifacts
python3 -B validation/mutation_check.py
```

Use `python` on Windows if appropriate. Keep temporary fixtures, receipts and private logs outside the checkout. Do not weaken checks to obtain a green result.

In a PR, explain the problem, resulting behavior, scope, verification and remaining limitations. Include regressions for meaningful behavior changes; a wording correction needs link/consistency checks rather than invented tests. A green CI run or agent recommendation does not itself authorize merging or deploying.

Do not commit secrets, private reference material, user inputs, machine paths or raw transcripts. Inspect the complete proposed diff and any new file before publishing. Avoid personal email addresses in commit metadata if you do not intend to disclose them; GitHub offers a noreply address. Ignoring a path does not remove it from existing commits.
