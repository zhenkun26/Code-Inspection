# Planning and Independent Review Assignments

## From Requirement Risks to Capabilities

Establish the contract inputs first, then map requirements to risks, checks, and evidence. Assess risk by actual impact, business rules, permissions/data, irreversibility, dependencies, platform, and detectability, rather than filenames, line counts, or bugfix/config labels. For each planned item, state coverage, criteria, whether it is required and why, version/environment, executor, authorization, dependencies, evidence location, and budget. List unconfirmed check suggestions separately from confirmed required items.

Actual-use validation, deterministic checks, and semantic review may run in parallel; tests do not have to precede review. Execute serially when prior evidence is a dependency or mutable state is shared. A diff-first approach still requires reading callers, interfaces, configuration, data, and related tests, rather than only added lines.

## Open-Ended Review Perspectives and Assignment

PM/requirements, Architect/architecture, QA, Product, DevOps, security and privacy, A11y, licensing and data compliance, performance, and i18n are non-exhaustive examples, not a default selection of all perspectives, a complete taxonomy, or a scope ceiling. Add, combine, or split perspectives according to business/architecture/dependencies and risk, and state uncovered areas; completing a checklist or counting Agents does not establish sufficient coverage. IMK/lexicons are domain-specific; IPC depends on the architecture.

One Agent may cover several related perspectives, while high-risk areas may receive a separate in-depth review. Default headcount/models/specialist routing are unspecified; the coordinator selects them in the project/task plan and records the rationale. Do not invoke every role merely because an external Skill defines it. Independent review means a context separate from the implementation conversation; it does not guarantee statistical independence or objectivity.

The coordinator maintains the task ledger, total budget, and dependencies. Reviewers handle only assigned work; they do not add roles/call chains, recursively create teams, or start separate budgets. Return discovered gaps to the coordinator. Parallel reads use a stable snapshot, and validation data is isolated according to the runtime protocol; do not share mutable checkout/index/HEAD/branch operations.

## Minimal Fact Package and Assignment Contract

Give each reviewer the original requirement and acceptance criteria, base/head/workspace digests and diff, trusted constraints, access to actual code and impact context, existing runtime evidence/limitations, and the current assignment. The implementer's summary is navigation, not the sole source of facts; do not inherit the author's entire conversation as review context, and do not lose access to original facts.

State the responsibility/problem domain, inputs and identities, criteria, required evidence, non-goals, permitted actions/environments, dependencies and budget, and return format in the assignment. Findings about standards must cite specific rules; reasonable user expectations are only hypotheses to check and cannot automatically expand requirements. Do not mechanically turn design principles into mandatory patterns.

In the first round, reviewers may independently analyze the same fact package before consolidating findings and rebuttals; do not substitute others' conclusions for verification from the assigned perspective in advance. Zero findings are valid. Report the scope actually read/verified and its limitations, without inventing issues to fill a report or meet a fixed comment count. Retain original outputs by review perspective and verify them together separately; do not present deduplication as unanimous agreement.

## Capability Fallbacks

- Isolation is possible but parallelism is not: execute serially and mark isolated_context; do not claim parallel execution.
- Only self-checking is possible: mark same_context_self_check; if an independent context is required, the item is BLOCKED and cannot pass under a false label.
- A model/tool is unavailable: complete checks that can run independently and leave related required items incomplete. A substitute capability needs policy acceptance, truthful identity, and no expansion of authorization.
- Context is insufficient: split work by dependency risk and record unread scope; do not recommend readiness when key scope is uncovered. Model fallback must not cross capability, recipient, geographic, or secret boundaries.

At assignment completion, use the [record template](record-template.md) to return findings/questions/zero findings, evidence, uncovered items, and budget used; read [verification and rechecks](recheck.md) when findings need verification.
