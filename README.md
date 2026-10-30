# Code-Inspection

**English** · [简体中文](README-zh.md)

A model-agnostic, evidence-based code-review Skill for your existing agent host. Inspired by [How the Claude Code team does code review](https://claude.dev/blog/solving-code-review-at-anthropic/) and everyday review practice. Independent and unofficial; not affiliated with or endorsed by Anthropic.

## Human steer, agent execute

Humans decide whether a requirement is worthwhile, architecture, acceptable risk and acceptance. Agents perform substantial implementation, checks, investigation, evidence organization and authorized repair/recheck. This project focuses on code review; action permissions and required human decisions remain explicit.

## Start

Get the complete repository and open it as your agent's project directory:

```sh
git clone https://github.com/zhenkun26/Code-Inspection.git
cd Code-Inspection
```

Keep the complete layout: copying only the discovery entry or a single Skill folder breaks relative references. This repository starts from a sanitized file-only snapshot; private development history, PR discussions and audit records are excluded.

Ask an existing agent host that can read local files:

> Read `skills/pre-pr-review/SKILL.md`. Review my specified project and local changes against the original requirements and acceptance criteria. Start read-only. Use only granted verification/repair permissions. Report the actual version, evidence, findings, unresolved gaps and next-stage recommendation.

Supply the target, original requirement, acceptance criteria, trusted policy, permitted actions and task budget. Relevant staged, unstaged and untracked changes are included. No global Skill installation is required. For Codex, the repo-local `code-inspection` entry routes to the existing stage Skills; see [setup and explicit-load fallback](docs/codex-setup.md). Earlier discovery/routing feedback is historical and does not validate this copy. Host discovery/loading, installation into another project and cancellation/resumption are unverified for this snapshot. Detailed protocols currently use Simplified Chinese.

## What is implemented

| Stage | Available capability |
| --- | --- |
| [Before PR](skills/pre-pr-review/SKILL.md) | Instruction Skill, five shared protocols, real-use verification and independent review when the host supports them. |
| [During PR](skills/pr-in-review/SKILL.md) | Local record runner for version-bound evidence, finding decisions, continuous budgets and host-supplied human-decision records; optional security-result import. |
| [At merge](skills/merge-review/SKILL.md) | Real controlled bare-Git candidate, integration evidence, expected-old conditional ref update and receipt query. Only explicitly isolated repositories and merge commits. |
| [After merge](skills/delivery-review/SKILL.md) | Optional local simulation, plus a trusted-host deploy/rollback interface; no bundled production provider. |

No native hosted-CI/SARIF result importer, identity authentication or production deployment/flag/metrics/rollback provider is included. No automatic PR creation, comments, real-project merging or deployment. Evaluation datasets and scoring are not implemented.

The optional [host delivery interface](docs/host-delivery.md) accepts a policy-selected provider from trusted Python host code for deploy/rollback. Missing capability remains UNKNOWN/HOLD; the default CLI installs none. Conditional writes, authentication and durable idempotency require actual host integration and acceptance. Other real delivery actions are unsupported.

The optional [build provenance contract](docs/build-provenance.md) accepts a policy-selected verifier supplied by a trusted Python host. It binds the result to exact source/tree/configuration/artifact/run and plan intent; no external CI identity or signature verifier is bundled. Without the required capability, new actions remain UNKNOWN/HOLD. Historical recovery does not renew authentication.

## Run the offline example

From the checkout root:

```sh
python3 -B examples/lifecycle_demo.py --simulate-approvals --with-delivery
```

It retains a new external directory with commands and evidence. The tiny content check and isolated Git merge are real; review reports, person decisions and delivery are explicitly synthetic. Its PASS summary includes expected HOLD/UNKNOWN states and is not release approval. Omit `--with-delivery` to leave that stage NOT_RUN.

- [Walkthrough, minimum configuration and troubleshooting](docs/local-walkthrough.md)
- [Current workflow, trust boundaries and verification scope](docs/workflow.md)
- [Lifecycle CLI and record contract](interfaces/lifecycle/README.md)
- [Optional security-result interface](interfaces/security-tools/README.md)
- [Support and acceptance evidence](docs/acceptance.md)
- [Sources and license scope](docs/sources.md)

An explicit [temporary-state inspection](docs/temporary-state.md) reports crash residues under the host lock, without reading their contents or deleting them. It is a recovery aid, not evidence that a pending action succeeded. An opt-in trusted-host POSIX interface can recover exactly registered new resources under an exclusive cooperating-writer namespace; default inspection and unknown legacy files never authorize deletion. Windows managed cleanup is not supported.

## Requirements and checks

Default A is Markdown plus your agent host: no new mandatory third-party runtime. Host/model access may have costs or send data to authorized providers; low dependency does not mean guaranteed offline operation. Optional B uses Python 3.9+ standard library and POSIX no-follow file operations/locks, or the native Windows backend described in [platform support](docs/windows-support.md); isolated Git operations need Git supporting `merge-tree --write-tree`. Only trusted self-owned fixtures may run through these helpers.

```sh
python3 -B validation/run_checks.py --keep-artifacts
python3 -B validation/mutation_check.py
```

The unified checker requires a Git checkout. Both commands may initialize disposable self-owned Git fixtures; run them only when that local execution is authorized. They cover the repository router, four stage entries, both Python modes and distribution hygiene; receipts/fixtures stay outside the checkout. On Windows, use `python` in place of `python3` if that is your installed interpreter command. The fixed platform guard-removal checks are targeted regressions, not comprehensive mutation coverage. Tests do not establish production safety, model review quality or scanner effectiveness.

The large lifecycle suite runs in two disjoint groups in each Python mode, with complete case coverage and the existing 180-second command limit. Timeout failures retain partial output and leave remaining checks NOT_RUN; they do not prove all descendant processes stopped. See [verification limits](docs/acceptance.md).

Distribution checks reject unsafe selections before reading payloads and reuse the bounded, no-follow reader. Test fixtures share the external-directory and KEEP policy. The [verification contract](docs/acceptance.md#distribution-read-boundaries) defines the file/count/aggregate limits and remaining trust boundaries.

The included [CI definition](.github/workflows/ci.yml) is configured for pushes to `main`, pull requests and manual dispatch, targeting Ubuntu 24.04 with Python 3.9 and 3.13, plus Windows 2025 x64 with Python 3.13. Each job runs ordinary and optimized checks, including both full demo variants, structure, safety boundaries, links and exact checksum coverage. The Python 3.13 jobs also run the fixed guard-removal checks. It uses read-only repository permissions, SHA-pinned official Actions and no repository secrets or deployment steps. Check the exact revision in [Actions](https://github.com/zhenkun26/Code-Inspection/actions/workflows/ci.yml); historical runs from another repository do not validate it. Green CI is not production acceptance. Other runner combinations are outside this matrix; Windows support is conditional on its native safety tests completing, with no permission changes or silent skips. After an intentional file change, update `SHA256SUMS` for every selected file except itself.

CI total ceilings are 15 minutes for Ubuntu and 50 minutes for Windows, sized from actual stage timings; individual maintained commands and mutation subprocesses retain their 180- and 90-second limits. See the [Windows CI budget rationale](docs/windows-support.md).

The host protects trusted policy, authority and ledger directories. Reviewed content and logs cannot grant permissions; JSON actor names and hashes are not authentication or a sandbox. Required missing/stale/partial evidence remains an explicit gap. READY_FOR_NEXT does not authorize merge or release. See the shared [core contract](skills/pre-pr-review/references/contract.md).

Stored merge/delivery plans and receipts are checked for exact intent and object/state consistency before execution or recovery. Unsafe legacy records require explicit replanning or migration; see [integrity and recovery compatibility](interfaces/lifecycle/README.md#计划完整性与恢复兼容性). These checks do not protect against a malicious writer controlling the trusted policy and approval store.

Check results retain optional failures separately from blocking decisions. N_A requires a current, scoped host applicability record; a legacy reason alone becomes UNKNOWN. CLI budget reports separate existing ledger capacity, observed work in this invocation and unknown external costs. Capacity is not authorization, and safe receipt queries remain available after exhaustion. See the [record and accounting contract](interfaces/lifecycle/README.md).

Optional [single-file artifact binding](docs/artifact-content.md) checks actual bytes and binds source/tree/configuration associations to new plans. Trusted policy selects required actions; legacy labels remain unverified. Delivery consumes the same bounded in-memory snapshot it verifies, with either the local simulation or an explicitly supplied trusted-host provider. Build provenance and writer isolation are host assertions, not authentication.

Optional [source manifests](docs/source-manifests.md) bind scoped directory inputs or separate HEAD/index/actual-worktree projections to review identity. Collectors support POSIX and the bounded Windows NTFS configuration described in [platform support](docs/windows-support.md); native collection must pass the exact revision’s Windows checks. Windows records read-only attributes separately from Git index executable modes. Scope, exclusions and limits are part of the identity. It is not an atomic directory snapshot, directory-deployment consumer or authenticated build provenance; dirty source cannot silently become a committed merge candidate.

New charged attempts save a versioned started record with the counter increment; started does not mean the action succeeded. A failed ledger save leaves durability unknown and fences that Session until a validated reload. Old usage is preserved without invented history, and accepted-operation recovery remains uncharged.

## Verification and project layout

Validation uses the maintained local/CI entry, including ordinary and optimized Python, isolated Git demos, file safety and distribution checks. Results bind to the tested revision; see [verification scope](docs/acceptance.md) and the actual Actions run. Independent AI reviews are development activities, **not an installed automatic AI-review service**. Simulated approvals/delivery do not prove live platform acceptance.

| Path | Purpose |
| --- | --- |
| `.agents/skills/code-inspection/` | Repository-scoped Codex entry. |
| `skills/` | Four stage instructions and shared evidence/authority protocols. |
| `interfaces/`, `support/` | Optional local record runner and platform file/Git helpers. |
| `examples/`, `validation/` | Disposable demonstration and maintained checks. |
| `docs/` | Setup, workflow, limits and source attribution. |

## Help, contributions and security

Use [Issues](https://github.com/zhenkun26/Code-Inspection/issues) for sanitized questions and reproducible non-sensitive bugs. A private reporting channel is not assumed to be configured. Read [contribution guidance](CONTRIBUTING.md) before proposing changes and [the security policy](SECURITY.md) before reporting a vulnerability. Do not put credentials, private code, personal paths or raw host transcripts in public issues or pull requests. No response-time or production-support commitment is implied.

## License

Original project content that the owner has the right to license is available under the [MIT License](LICENSE), Copyright (c) 2026 Zhenkun. Third-party articles, translations, images and private inputs are excluded and are not relicensed. No third-party runtime is vendored; method references and future copying obligations are recorded in [sources](docs/sources.md).
