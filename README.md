# Code-Inspection

**English** · [Chinese](README-zh.md)

Code-Inspection is a set of review instructions for an AI agent you already use (Claude Code, Codex or any host that can read local files). You point the agent at a change. It checks the change against your requirement and reports what it verified, what it found, what it could not check, and whether the change is ready for the next step.

It needs no installation, no model of a particular kind and no new service. Inspired by [How the Claude Code team does code review](https://claude.dev/blog/solving-code-review-at-anthropic/); independent and unofficial, not affiliated with or endorsed by Anthropic.

**Human steer, agent execute.** You decide what is worth building, the architecture, acceptable risk and final acceptance. The agent does the checking, investigation and evidence gathering, and repairs only what you allow.

## Start

Clone the repository and keep its layout intact (the Skills link to each other by relative path):

```sh
git clone https://github.com/zhenkun26/Code-Inspection.git
```

Then, in your agent, paste this prompt and fill in the brackets:

> Read `<path-to>/Code-Inspection/skills/pre-pr-review/SKILL.md` and follow it.
> Review: `[project path and the change — branch, commits or uncommitted work]`
> Requirement: `[what the change is supposed to do]`
> Acceptance criteria: `[how we know it works]`
> Allowed: read-only review `[or: you may run tests / you may fix confirmed issues]`
> Budget: `[for example: at most 3 review rounds]`

The agent starts read-only. It reviews staged, unstaged and untracked work together, so nothing is hidden behind the last commit. Codex users can instead open this repository as the project and use the `code-inspection` entry; see [Codex setup](docs/codex-setup.md).

## What you get back

A report in five parts, defined in the [record template](skills/pre-pr-review/references/record-template.md). An illustrative shape (not real output):

```text
1. Subject      head 3f2c1e0 + uncommitted changes in src/auth/; read-only plus tests
2. Validation   CHK-1 unit tests        PASS     (log: ...)
                CHK-2 login journey     NOT_RUN  (no browser available)
3. Findings     F-1 token not revoked on logout — confirmed, blocking
4. Gaps         login journey unverified; no load test
5. Recommendation  HOLD — fix F-1, then run CHK-2
```

The recommendation is always one of `READY_FOR_NEXT`, `HOLD` or `REPLAN`. Zero findings is a valid result, but green tests or zero findings alone never mean "ready": unverified areas stay listed as gaps.

## The four stages

| Stage | Use it when | What it relies on |
| --- | --- | --- |
| [Before PR](skills/pre-pr-review/SKILL.md) | You want a change checked before opening a PR. **Start here.** | Markdown instructions only. |
| [During PR](skills/pr-in-review/SKILL.md) | You want findings, decisions and budget tracked across review rounds. | Optional local Python record tool. |
| [At merge](skills/merge-review/SKILL.md) | You want the merged result checked before the branch moves. | Optional Python tool and an isolated Git repository. |
| [After merge](skills/delivery-review/SKILL.md) | You want deployment and rollback gated on evidence. | Optional Python tool; local simulation only unless your host plugs in a provider. |

Only the first stage is needed for everyday use. The later stages are for teams that want an auditable record of each step.

## What it does not do

- It does not create PRs, post comments, merge real projects or deploy anything on its own.
- It does not include production deployment, metrics or rollback integrations, hosted-CI/SARIF importers or identity authentication.
- It is not a hosted AI-review service, and its review quality has not been measured: there is no evaluation dataset or scoring yet.
- Host discovery of the Skills, installation into another project and cancel/resume have not been verified for this snapshot.

## Try the offline demo (optional)

Requires Python 3.9+ and Git with `merge-tree --write-tree`. From the repository root:

```sh
python3 -B examples/lifecycle_demo.py --simulate-approvals --with-delivery
```

It runs all four stages in a new temporary directory outside the checkout. The content check and Git merge are real; reviewer reports, human approvals and delivery are simulated and labeled as such. Its PASS summary includes expected HOLD/UNKNOWN states and is not a release approval. See the [walkthrough](docs/local-walkthrough.md).

## Learn more

- [Workflow, trust boundaries and verification scope](docs/workflow.md)
- [Shared review contract: statuses, authority and gates](skills/pre-pr-review/references/contract.md)
- [Capabilities and limits in detail](docs/capabilities-and-limits.md): optional host integrations, record integrity, budgets, CI matrix
- [Lifecycle CLI and record contract](interfaces/lifecycle/README.md)
- [Optional security-result interface](interfaces/security-tools/README.md)
- [Platform support (Windows)](docs/windows-support.md) and [verification limits](docs/acceptance.md)
- [Sources and license scope](docs/sources.md)

## Repository layout

| Path | Purpose |
| --- | --- |
| `skills/` | The four stage instructions and their shared protocols. |
| `.agents/skills/code-inspection/` | Repository-scoped Codex entry that routes to the stages. |
| `interfaces/`, `support/` | Optional local record tool and file/Git helpers (Python standard library only). |
| `examples/`, `validation/` | Offline demo and maintained checks. |
| `docs/` | Setup, workflow, limits and source attribution. |

## Contributing and checks

Read [contribution guidance](CONTRIBUTING.md) first. The maintained checks are:

```sh
python3 -B validation/run_checks.py --keep-artifacts
python3 -B validation/mutation_check.py
```

They need a Git checkout and create disposable Git fixtures outside it; on Windows use `python` if that is your interpreter. After changing distributed files, update `SHA256SUMS` (excluding the manifest itself). [CI](.github/workflows/ci.yml) runs the same checks on Ubuntu (Python 3.9, 3.13) and Windows (Python 3.13); see [capabilities and limits](docs/capabilities-and-limits.md#checks-and-ci) for scope. Passing checks show the tooling behaves as specified; they do not show production safety or model review quality.

## Help and security

Use [Issues](https://github.com/zhenkun26/Code-Inspection/issues) for sanitized questions and reproducible bugs, and read [the security policy](SECURITY.md) before reporting a vulnerability. Never post credentials, private code, personal paths or raw agent transcripts. No response time or production support is promised.

## License

Original project content that the owner has the right to license is under the [MIT License](LICENSE), Copyright (c) 2026 Zhenkun. Third-party articles, translations, images and private inputs are excluded and are not relicensed. No third-party runtime is vendored; see [sources](docs/sources.md).
