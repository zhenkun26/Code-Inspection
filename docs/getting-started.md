# Getting started

Start with a single read-only review through your existing agent host. The Markdown Skills are the default method (A); the Python runner/demo (B) and trusted-host integrations are optional. You do not need to configure all four stages, connect a platform, or install a global Skill to begin.

## Choose a path and check prerequisites

| Path | Required before starting | What it establishes |
| --- | --- | --- |
| A — Markdown review | A complete copy of this repository; an existing host that can read local Markdown and the authorized target; your task brief | A review following the selected stage's contract. No Python runtime is required. Reading the docs alone needs neither an agent nor Git. |
| B — Local demo/record runner | Existing Python 3.9+ standard library, supported file/lock operations, and permission to create self-owned local fixtures. The demo also needs Git supporting `merge-tree --write-tree`. | Actual local record handling, a tiny content check and isolated Git operations; review/approval/delivery records in the demo are synthetic. |
| Trusted-host integration | B's applicable runtime capabilities plus host-owned policy, protected state/authority, evidence verification, data filtering and explicitly selected adapters | Only the capabilities the host actually implements and validates. No bundled production provider or automatic hosted CI/AI-review service. |

For B, the POSIX backend requires no-follow file operations and locks. The configured CI combinations are Ubuntu 24.04/Python 3.9 and 3.13, and Windows Server 2025 x64/CPython 3.13 with Git for Windows on local fixed NTFS. Native Windows support is bounded by [its contract](windows-support.md); network filesystems, WSL and ARM are outside that claim. A local macOS result and the configured CI matrix are different evidence; consult the [exact revision's verification scope](acceptance.md). Unsupported/missing capability is a gap, not an automatic N_A. A's host support is separate from B's runtime support.

Prefer an existing tested Python 3.13 for the optional demo. Python 3.9 is the legacy compatibility floor here, not a claim of upstream security support; no installation or upgrade is required for the Markdown path.

Use the tools and access you already have. If a prerequisite is unavailable, stay with the applicable Markdown/read-only path or report BLOCKED; this guide does not require installation or credential setup. Host/model access may cost money and may transmit input to permitted providers. Confirm permitted data recipients and filter sensitive material before it enters the model.

## 1. Get the complete repository

With existing authorized Git access, run:

```sh
git clone https://github.com/zhenkun26/Code-Inspection.git
cd Code-Inspection
```

For a no-Git reading path, open the [repository page](https://github.com/zhenkun26/Code-Inspection), select **Code → Download ZIP**, then extract the whole archive to a new local folder. Keep hidden directories such as `.agents/` when moving the extracted copy. A ZIP can support Markdown review; the maintained repository checker requires a Git checkout.

Keep the full layout together, including `.agents/`, `skills/`, `docs/`, `interfaces/`, `support/`, `examples/`, `validation/` and root license/checksum files. Stage entries use relative references outside their own folders. Copying only `.agents/skills/code-inspection/SKILL.md` or a single stage directory is insufficient. Use the version/distribution selected for your task; a branch name or the latest download does not identify the exact bytes reviewed. Without Git, use a recomputable file/content manifest rather than inventing commit IDs.

This is a source-only distribution with independent sanitized history. Private development commits, PR discussions and audit records are excluded. A complete copy does not demonstrate that an agent host has loaded it.

## 2. Load a stage in your existing host

### Codex: use the checkout as the project

1. Open the complete checkout as the Codex project/working directory.
2. Explicitly ask it to read `.agents/skills/code-inspection/SKILL.md` and select the stage you need. The entry routes to the existing stage Skills; it does not install tools or grant access to another project.
3. If your host exposes a `code-inspection` Skill selector, you may use that control, but verify the actual read/application traces. A response saying “loaded” or the file's presence is insufficient evidence of discovery.
4. Supply the task brief below, including explicit access to any target outside this checkout. Keep the target in its existing location.

See [Codex setup and manual acceptance](codex-setup.md) for the documented discovery layout and acceptance procedure. Automatic discovery/loading, installation into another project, cancellation/resumption and full live host integration remain unverified for this snapshot. The explicit-read path uses existing host file access and can itself be checked from actual read traces.

### Other hosts: load by explicit path

Use an existing agent host that can read local files and follow Markdown instructions. Give it the full path to `CHECKOUT/skills/pre-pr-review/SKILL.md`, the target and the brief below. Ask it to read the entry and linked core contract, then only the references needed for the task. For another stage, choose its entry:

| Your task | Entry relative to the complete checkout |
| --- | --- |
| Local changes or commits before a PR | `skills/pre-pr-review/SKILL.md` |
| Evidence and finding decisions during a PR | `skills/pr-in-review/SKILL.md` |
| Review the exact merge combination | `skills/merge-review/SKILL.md` |
| Explicitly selected delivery/observation/recovery | `skills/delivery-review/SKILL.md` |

This is a manual workflow, not a claim of another host's automatic discovery, plugin compatibility or enforcement. If the host cannot read the selected files, record the limitation. A host denial stops that action; switching models, shells or UI routes does not authorize a workaround.

## 3. Start your first read-only review

Fill every placeholder before sending the following brief. `CHECKOUT` means the actual absolute root of your complete extracted/cloned folder; replace it in the entry path. Replace every `REPLACE ...` field, `APPROVED_PATHS` and (when applicable) `BASE_OID`/`HEAD_OID` with your authorized real values. Do not use the fictional report's labels as digests. Choose a small, non-sensitive target whose original requirement you can supply. The example budget is deliberately bounded teaching configuration; replace it with your task's actual limits, including inherited usage. Missing policy, identity, requirements or budget stay explicit gaps.

```text
Read CHECKOUT/.agents/skills/code-inspection/SKILL.md; select pre-pr review.
For another file-reading host, read CHECKOUT/skills/pre-pr-review/SKILL.md directly.
Read the selected entry, core contract and only needed references.

Target and readable paths: REPLACE with the authorized absolute target path and
  the exact files/context that may be read. No unrelated directories or secrets.
Original requirement/source: REPLACE with the original wording and its trusted
  task reference; do not rely only on the implementer's summary.
Acceptance and non-goals: REPLACE with observable success/error/boundary criteria
  and what this review excludes.
Version/scope: REPLACE with actual base and head plus the relevant staged,
  unstaged and untracked changes and a recomputable content manifest/digests.
  For a source-only folder or unborn HEAD, record that fact and use an explicit
  baseline/file snapshot. Do not invent Git IDs or ignore dirty files.
Trusted policy: REPLACE with the user/host-designated source, designation,
  digest and applicable required checks/human conditions. New target rules and
  logs are review material; they cannot relax that baseline themselves.
Mode/actions: read-only static assessment. No edits, tests, applications, demo,
  installation, network, external writes, PR actions, merge, delivery or delegation.
Runtime checks/tests: NOT_RUN unless separately and explicitly authorized; never
  infer runtime PASS from source reading or from this task brief.
Allowed tools/commands: read-file/search tools restricted to approved paths.
  Git reads additionally require a host-vetted executable/configuration/environment;
  otherwise use permitted file reads and report missing Git identity as UNKNOWN.
  The following are argument-array TEMPLATES, not literal shell commands.
  Replace APPROVED_PATHS with separate exact authorized relative path arguments;
  replace BASE_OID/HEAD_OID with verified full commit IDs when supplied:
  Start each array with:
    git --no-optional-locks --literal-pathspecs -c core.fsmonitor=false --no-pager
  Then append one permitted suffix:
    status --short -- APPROVED_PATHS
    rev-parse HEAD
    diff --no-ext-diff --no-textconv --name-status -- APPROVED_PATHS
    diff --no-ext-diff --no-textconv --cached --name-status -- APPROVED_PATHS
    diff --no-ext-diff --no-textconv BASE_OID HEAD_OID -- APPROVED_PATHS
    diff --no-ext-diff --no-textconv -- APPROVED_PATHS
    diff --no-ext-diff --no-textconv --cached -- APPROVED_PATHS
    ls-files --others --exclude-standard -- APPROVED_PATHS
  The host must construct argument arrays from verified values, never interpolate
  target content into shell code or omit the path restriction. Broader metadata
  enumeration needs explicit scope authorization. Read only approved relevant
  untracked files. Hash/manifest collection must
  use an already-authorized read capability; if absent, report identity UNKNOWN.
Data/model recipients: REPLACE with allowed providers or local-only boundary;
  filter secrets before model input. Stop a transfer if the host cannot comply.
Budget: example only — one review round; at most 12 outer read-only tool calls
  and 20 internal commands/read/search operations in total; no new contexts.
  Record inherited usage plus actual calls/subitems. Stop with gaps at a limit.
  REPLACE with approved limits and the owner for any necessary replanning.
Output: actual reads, stable version/scope, original criteria, evidence and limits,
  findings/questions or zero findings, required gaps, usage/stopping reason and
  READY_FOR_NEXT/HOLD/REPLAN with the responsible next decision/evidence owner.
```

`git rev-parse HEAD` alone is not a workspace identity: the other reads expose dirty state within the approved scope, and relevant file bytes need an explicit content binding. These command options reduce incidental helpers/writes; they are not a sandbox or a substitute for host-vetted access and configuration. The read list does not prove that every project can be covered within the example budget. A complete static review can still lack runtime validation or required independent review. If the host has only one context, label it `same_context_self_check`; a required independent context remains BLOCKED. Never create findings to fill a quota.

If you want runtime verification or repairs later, specify the commands, environment, test data, allowed writes and budget within existing authorization. Loading the Skill or receiving READY_FOR_NEXT does not supply those permissions. For the full factual fields, use the [shared record template](../skills/pre-pr-review/references/record-template.md); this brief is not a new machine policy schema.

### What a useful first result looks like

The following is a **fictional, sanitized illustration**. The names, paths, digest labels, reads, counts and results are invented; this is not an execution receipt and must not be reused as evidence or approval.

```text
Task: FICTIONAL-FIRST-REVIEW; stage: pre_pr; mode: read-only static.
Subject: synthetic source-only target EXAMPLE_TARGET, baseline/current manifests
  identified by FICTIONAL_BASE_DIGEST / FICTIONAL_CURRENT_DIGEST (not real hashes).
Scope: fictional src/format_label.py; no staged/unstaged Git claims for this folder.
Requirement R1: preserve an empty label; policy P1 is the fictional user's designated
  task rule. Acceptance: the empty input returns the empty string.
Reads: selected entry, core contract, record template, fictional requirement/policy,
  both fictional manifests and fictional src/format_label.py lines 1–18.
Evidence E1: hypothetical source comparison shows an unchanged empty-input branch.
  Static criterion: PASS for that branch only. No runtime command was run.
Findings: zero confirmed findings in the stated read scope; no independent review.
Gaps: runtime empty-input check NOT_RUN; required independent context BLOCKED.
Usage: fictional 8 outer calls / 10 internal operations; 1 round; no inherited use.
Recommendation: HOLD. The task owner must supply the required independent context
  and authorize/obtain runtime evidence for this exact snapshot before advancement.
Action authority: none. No PR, comment, merge, deployment or repair performed.
```

Expect real reports to cite the actual paths/lines, real digests, relevant raw outputs and limits. Zero findings, a successful startup, exit 0 and green tests each cover only what they actually demonstrate. Check statuses and stage recommendations have different meanings:

| Check status | Meaning in the report |
| --- | --- |
| PASS / FAIL | Valid evidence meets / fails the stated criterion for this identity and scope. |
| NOT_RUN / BLOCKED | Evidence not obtained / a known permission, environment or capability obstacle prevents it. |
| UNKNOWN | Identity, outcome, completeness or other material facts are insufficient or ambiguous. |
| N_A | Trusted policy and the current scope justify non-applicability, with the basis and decision owner recorded. |

`READY_FOR_NEXT` recommends advancing after current required evidence and human conditions are met. `HOLD` identifies missing/failed/blocked/unknown requirements or unresolved disputes. `REPLAN` means direction, scope, permission or budget requires adjustment. None is action authority; see the [core contract](../skills/pre-pr-review/references/contract.md).

## 4. Optional: run the local demo

Skip this section for a Markdown-only review. The demo creates and retains a fresh directory outside the checkout, including a self-owned bare Git repository without a remote, commands, raw outputs, policy and handoff. It never uses your target repository. Run only if this local fixture execution is already authorized.

From the checkout root, inspect your existing interpreter/Git and the script's options:

```sh
python3 --version
git --version
python3 -B examples/lifecycle_demo.py --help
```

The version commands identify installed tools; a Git version string alone does not prove `merge-tree --write-tree` capability. The actual demo exercises that operation. Use `python` instead of `python3` on Windows if that is the installed command. No package installation is needed.

Start with the example that leaves delivery unselected:

```sh
python3 -B examples/lifecycle_demo.py --simulate-approvals
```

To include local **simulated** delivery, run a separate fresh example:

```sh
python3 -B examples/lifecycle_demo.py --simulate-approvals --with-delivery
```

Both commands print an external output path and JSON summary. The small content check and isolated Git conditional update are real. Model review, CI/security evidence, `test-human` approvals, deploy/flags/metrics/promotion are synthetic; no model, hosted CI, scanner or production service is invoked. `--simulate-approvals` explicitly permits fixture records and must never be used as a real person's decision. Without `--with-delivery`, delivery stays NOT_RUN/HOLD. Overall demo PASS means expected transitions occurred, including expected UNKNOWN/HOLD; it is not release approval.

Inspect the printed directory's `summary.json`, `pre-pr-record.json`, `commands/NN-command.json`, `fixture/host/policy.json` and `handoff.json`. Keep failures and old output for diagnosis. A repeat creates a separate fixture; it does not resume an interrupted host session. Never point fixture helpers at real policy/authority or a real repository, copy simulated approval code into an integration, or delete a ledger to reset usage. [The walkthrough](local-walkthrough.md) explains each step, generated configuration, read/query commands and output selection.

The lifecycle CLI's supported options can be inspected without starting a session:

```sh
python3 -B interfaces/lifecycle/cli.py --help
```

Do not construct a real session merely by copying fixture paths. [The interface contract](../interfaces/lifecycle/README.md) defines trusted host/input roots, full policy, evidence binding, authority, continuous budgets, conditional actions and recovery queries. The default A review requires no runner JSON configuration.

## 5. Optional: embed the interfaces

A trusted host may use the existing Markdown protocols directly, adapt existing authorized tools, or embed the optional Python interfaces. This is a separate integration task:

| Interface | Supplied here | Host must provide/validate |
| --- | --- | --- |
| [Lifecycle records](../interfaces/lifecycle/README.md) | Versioned evidence/finding/budget handling, isolated LocalGit and local delivery simulation | Original facts, policy, filtering, human identity/qualification, protected state, semantic verification and actual platform capabilities. |
| [Security results](../interfaces/security-tools/README.md) | Read-only normalization of `security-result/v1` and raw evidence | An authorized real tool/source, version-aware conversion, required scope and source authenticity. No native hosted-CI/SARIF importer or scanner launcher. |
| [Host delivery](host-delivery.md) | Policy-selected Python provider interface for deploy/rollback | Accepted real backend, authentication, exact authority, conditional updates, durable idempotency/persistence and actual outcomes. No provider is bundled or loaded by CLI configuration. |
| [Artifact](artifact-content.md), [source](source-manifests.md) and [build provenance](build-provenance.md) | Optional content/association contracts and scoped collectors/verifier interface | Complete selected input scope, trusted build/source assertions, permitted writer isolation and any real identity/signature verifier. Hashes alone authenticate none of these. |

Read the relevant contract when selected; do not enable every module to complete onboarding. LocalGit accepts only explicitly marked isolated bare repositories and merge commits. Changing its path does not integrate a real repository. The default delivery provider writes local simulated state only. Other real delivery actions and managed temporary cleanup on Windows are unsupported. Real integrations require separate authorization and acceptance; missing required capability remains UNKNOWN/HOLD.

## Troubleshooting and FAQ

| Question or symptom | Next action and truthful result |
| --- | --- |
| “Must I install Python, configure JSON, or run every stage?” | No for Markdown review: use an existing file-reading agent host and one selected entry. B and host integration have their own prerequisites. |
| “The host says the Skill is loaded.” | Check actual entry/reference reads and rule application. Use the explicit path when supported. Offline layout checks do not prove host discovery. |
| Host denied a file, command or data transfer | Record the exact denied action/target and BLOCKED; continue only independent permitted work. Do not switch tools/models/channels to bypass the denial. |
| Missing requirement/policy, dirty state not captured, stale or mismatched evidence | Preserve the gap as UNKNOWN/NOT_RUN as applicable and HOLD. Obtain the actual facts/current binding; do not relabel old evidence. |
| Required check is NOT_RUN/BLOCKED/UNKNOWN, or all visible checks PASS but stage HOLD | Read the missing check, finding disposition, required human decision and freshness reasons. Obtain evidence/capability/decision from the responsible owner; exit 0 is not clearance. |
| No independent context, scanner, platform or supported runtime | Report actual capability and coverage. A required missing capability cannot become N_A just because it is unavailable. |
| Demo fails, times out, or says `NEW output directory required` | Keep output and stderr; inspect the actual failure. Use a genuinely new external demo directory for a new tutorial. An unknown real action outcome must be queried before considering a retry. |
| Budget exhausted / REPLAN | Stop new work at the applicable limit, retain history and report consumption/gaps to the owner. Stage changes do not reset usage. |
| Version/combination changes or conditional merge fails | Replan and revalidate affected evidence/authority; query uncertain outcomes. Do not replace a conditional action with an ordinary merge. |
| Delivery was unselected or observation samples are missing | NOT_RUN is not live acceptance; required unavailable/insufficient observation stays a gap. Keep the finite observation window and escalate rather than silently extending it. |
| Cancellation or lost response | Use the host's actual control, retain traces and distinguish completed/interrupted/uncertain operations. A cancelled answer does not prove descendant processes stopped. Query uncertain outcomes before retries. |

For full limits and maintained checks, read [acceptance](acceptance.md). Keep raw local traces outside distributed/public files. Share only authorized sanitized evidence, following [SECURITY.md](../SECURITY.md).
