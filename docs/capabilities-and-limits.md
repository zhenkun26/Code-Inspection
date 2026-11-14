# Capabilities and limits

Detailed scope for the optional Python tooling, record integrity and maintained checks. Start with the [README](../README.md) for everyday use; nothing here is needed to run the pre-PR review Skill.

## Optional host integrations

No native hosted-CI/SARIF result importer, identity authentication or production deployment/flag/metrics/rollback provider is included. No automatic PR creation, comments, real-project merging or deployment. Evaluation datasets and scoring are not implemented.

The optional [host delivery interface](host-delivery.md) accepts a policy-selected provider from trusted Python host code for deploy/rollback. Missing capability remains UNKNOWN/HOLD; the default CLI installs none. New external actions also require a policy-selected operational persistence checkpoint; an ordinary successful save or reload is not proof of power-loss durability. Conditional writes, authentication, storage guarantees and durable idempotency require actual host integration and acceptance. Other real delivery actions are unsupported.

The optional [build provenance contract](build-provenance.md) accepts a policy-selected verifier supplied by a trusted Python host. It binds the result to exact source/tree/configuration/artifact/run and plan intent; no external CI identity or signature verifier is bundled. Without the required capability, new actions remain UNKNOWN/HOLD. Historical recovery does not renew authentication.

## Temporary state

An explicit [temporary-state inspection](temporary-state.md) reports crash residues under the host lock, without reading their contents or deleting them. It is a recovery aid, not evidence that a pending action succeeded. An opt-in trusted-host POSIX interface can recover exactly registered new resources under an exclusive cooperating-writer namespace; default inspection and unknown legacy files never authorize deletion. Windows managed cleanup is not supported.

## Record integrity, authority and budgets

The host protects trusted policy, authority and ledger directories. Reviewed content and logs cannot grant permissions; JSON actor names and hashes are not authentication or a sandbox. Required missing/stale/partial evidence remains an explicit gap. READY_FOR_NEXT does not authorize merge or release. See the shared [core contract](../skills/pre-pr-review/references/contract.md).

Stored merge/delivery plans and receipts are checked for exact intent and object/state consistency before execution or recovery. Unsafe legacy records require explicit replanning or migration; see [integrity and recovery compatibility](../interfaces/lifecycle/README.md#plan-integrity-and-recovery-compatibility). These checks do not protect against a malicious writer controlling the trusted policy and approval store.

Check results retain optional failures separately from blocking decisions. N_A requires a current, scoped host applicability record; a legacy reason alone becomes UNKNOWN. CLI budget reports separate existing ledger capacity, observed work in this invocation and unknown external costs. Capacity is not authorization, and safe receipt queries remain available after exhaustion. See the [record and accounting contract](../interfaces/lifecycle/README.md).

Optional [single-file artifact binding](artifact-content.md) checks actual bytes and binds source/tree/configuration associations to new plans. Trusted policy selects required actions; legacy labels remain unverified. Delivery consumes the same bounded in-memory snapshot it verifies, with either the local simulation or an explicitly supplied trusted-host provider. Build provenance and writer isolation are host assertions, not authentication.

Optional [source manifests](source-manifests.md) bind scoped directory inputs or separate HEAD/index/actual-worktree projections to review identity. Collectors support POSIX and the bounded Windows NTFS configuration described in [platform support](windows-support.md); native collection must pass the exact revision’s Windows checks. Windows records read-only attributes separately from Git index executable modes. Scope, exclusions and limits are part of the identity. It is not an atomic directory snapshot, directory-deployment consumer or authenticated build provenance; dirty source cannot silently become a committed merge candidate.

New charged attempts save a versioned started record with the counter increment; started does not mean the action succeeded. A failed ledger save leaves durability unknown and fences that Session until a validated reload. Old usage is preserved without invented history, and accepted-operation recovery remains uncharged.

## Checks and CI

Default A is Markdown plus your agent host: no new mandatory third-party runtime. Host/model access may have costs or send data to authorized providers; low dependency does not mean guaranteed offline operation. Optional B uses Python 3.9+ standard library and POSIX no-follow file operations/locks, or the native Windows backend described in [platform support](windows-support.md); isolated Git operations need Git supporting `merge-tree --write-tree`. Only trusted self-owned fixtures may run through these helpers.

```sh
python3 -B validation/run_checks.py --keep-artifacts
python3 -B validation/mutation_check.py
```

The unified checker requires a Git checkout. Both commands may initialize disposable self-owned Git fixtures; run them only when that local execution is authorized. They cover the repository router, four stage entries, both Python modes and distribution hygiene; receipts/fixtures stay outside the checkout. On Windows, use `python` in place of `python3` if that is your installed interpreter command. The fixed platform guard-removal checks are targeted regressions, not comprehensive mutation coverage. Tests do not establish production safety, model review quality or scanner effectiveness.

The large lifecycle suite runs in two disjoint groups in each Python mode, with complete case coverage and the existing 180-second command limit. Timeout failures retain partial output and leave remaining checks NOT_RUN; they do not prove all descendant processes stopped. See [verification limits](acceptance.md).

Distribution checks reject unsafe selections before reading payloads and reuse the bounded, no-follow reader. Test fixtures share the external-directory and KEEP policy. The [verification contract](acceptance.md#distribution-read-boundaries) defines the file/count/aggregate limits and remaining trust boundaries.

The included [CI definition](../.github/workflows/ci.yml) runs on pushes to `main`, pull requests and manual dispatch. Ubuntu 24.04/Python 3.9 and 3.13 retain their maintained checks; 3.13 also runs all applicable fixed guard-removal checks. Windows 2025 x64/Python 3.13 uses two serial jobs: native prechecks and all ordinary/optimized maintained checks first, then all Windows mutations with their positive controls, both API observations and a completeness gate. Both jobs use independent fresh runners and fixtures. The final Windows check succeeds only when both stages match the same candidate version and run/attempt and every required result is present and successful. It retains read-only permissions, SHA-pinned official Actions and no repository secrets or deployment steps. Read the exact revision in [Actions](https://github.com/zhenkun26/Code-Inspection/actions/workflows/ci.yml); historical results are not current validation. Green CI is not production acceptance. Other runner combinations are outside this matrix, and Windows support still requires its applicable native tests. Update `SHA256SUMS` after intentional selected-file changes, excluding the manifest itself.

CI job ceilings are 15 minutes for each Ubuntu job, 40 minutes for Windows maintenance, and 25 minutes for the dependent Windows mutation/observation job. Windows concurrency remains one per workflow; its cumulative configured ceiling is 65 runner-minutes, and the workflow ceiling is 95. These are execution limits, not measured duration or billed cost. Maintained commands retain 180-second limits and mutation subprocesses retain 90 seconds; the repeated native prechecks now explicitly use the same 180-second command bound. Missing, failed, cancelled or mixed-attempt results cannot satisfy the gate. See [Windows CI scope and budget](windows-support.md).

Validation uses the maintained local/CI entry, including ordinary and optimized Python, isolated Git demos, file safety and distribution checks. Results bind to the tested revision; see [verification scope](acceptance.md) and the actual Actions run. Independent AI reviews are development activities, **not an installed automatic AI-review service**. Simulated approvals/delivery do not prove live platform acceptance.
