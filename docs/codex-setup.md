# Codex setup and manual acceptance

This checkout includes the repo-scoped entry `.agents/skills/code-inspection/SKILL.md`. It routes to the four existing stage entries and their shared contract. Keep the full checkout together: later-stage entries also reference `interfaces/` and `docs/`. Copying only one stage folder, or only the discovery entry, breaks that dependency closure.

## Minimal setup

1. Obtain this repository's selected distribution using your authorized Git access, or copy that complete distribution into a new local directory. Keep `.agents/`, `skills/`, `interfaces/`, `support/`, `docs/`, validation/example files and root license/checksum files together. Do not copy a private development/archive tree or its Git history.
2. Use the checkout as the Codex project/working directory. Official documentation says Codex scans `.agents/skills` from its working directory up to the repository root. In supported CLI/IDE surfaces, the docs describe `/skills` or `$` skill mentions; use only the controls actually available in your host. The expected entry name is `code-inspection`.
3. Supply the target project/path, original requirement, acceptance criteria, exact change scope, trusted policy, permitted actions and budget. Access to a different target project requires the user's permission. Do not move a target's files into this repository merely for review.
4. If discovery is unavailable, use the explicit-read fallback below. If the host denies access, record BLOCKED and stop that action; another model, shell/CLI or UI route is not a workaround.

The path is adapted to the [official Codex skill documentation](https://learn.chatgpt.com/docs/build-skills). That documentation describes the supported discovery layout, not successful loading of this source-only copy. Host discovery/loading, personal installation, another-project discovery and full host integration remain unverified for this snapshot; see [verification limits](acceptance.md). No global configuration, personal Skill directories, permissions or host settings are changed. No installer or lifecycle hook is included. Keep default invocation behavior; action authorization still belongs to the existing stage contract.

For portable use in another project, keep this checkout intact and ask an authorized host to read its entry by explicit path. Do not copy the router into another project's `.agents/skills` by itself. A standalone multi-Skill/plugin package is a separate future integration, not implemented here.

## Explicit-read fallback

Replace the checkout/target/version fields with actual values:

```text
Read CHECKOUT/.agents/skills/code-inspection/SKILL.md and select pre-pr review.
Target: TARGET; requirement: ORIGINAL REQUIREMENT; acceptance: CRITERIA.
Version: actual BASE..HEAD and worktree state when available; otherwise an explicit file/content manifest. Do not invent Git identities for a source-only folder.
Trusted policy: USER/HOST-SPECIFIED SOURCE. Mode: read-only static review.
Budget: one review round, at most 12 outer read-only tool calls; stop with gaps if insufficient.
Read only the selected entry, needed references and the authorized target/context.
Do not run applications/tests/demo, change files, install, use network or delegate.
Report actual read paths and rule application, version/worktree identity, evidence,
findings or zero findings, unverified requirements and next-stage recommendation.
```

The fallback verifies explicit reading and application when real host tool traces show those reads. A response saying “loaded” is insufficient; it does not prove automatic discovery, global installation or independent review contexts.

## Small manual check

Use a fixed already-reviewed change, label it a historical replay and inspect one module, not all four stages. Record the entry/references actually read and the affected code lines. Missing original requirements or project policy stay explicit; a static-only round cannot claim runtime PASS or unconditional stage readiness. Allow zero findings without manufacturing issues.

For cancellation, use your host's actual cancellation control. Retain the visible tool/run record, then ask it to distinguish completed, interrupted and uncertain operations before resuming. A cancelled answer is not proof every process stopped; do not assert termination without execution/process evidence. The static task above should start no test/application processes and write no report files.

For stale evidence, clearly label a hypothetical old record as a test input, not a real PASS. Ask whether its version, scope, policy and environment still match. Conditional reuse needs demonstrated equivalence; changed requirements may be NOT_RUN, insufficient identity UNKNOWN, and a proven unmet criterion FAIL. Do not change old evidence's version label to make it applicable.

The maintained validation entry requires a Git checkout. Its complete-copy layout test initializes a disposable Git fixture and verifies all routes, checksums and references. This is offline package-layout evidence: it never starts Codex and does not prove host discovery or cancellation. See [verification limits](acceptance.md).
