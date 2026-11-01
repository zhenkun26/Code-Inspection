# Task and Result Record Template

Fill in the following fields for the actual task; this is a field template, not approved configuration. Explain unknowns rather than using empty values to imply inapplicability; do not copy unexecuted expectations into actual results. Markdown tables or structured data may be used; no database is required. Statuses and gates are defined only in the [core contract](contract.md).

## Task, Plan, and Assignments

- task_id / scope / stage=pre_pr; location of original requirement wording, acceptance items, and non-goals.
- identity: base, head/equivalent snapshot, workspace content digests/manifest, policy, artifact/environment/data state; acquisition method and references that can be checked again.
- authorization: source, review/verify/repair and permitted actions/paths/environments, and conditions that invalidate it.
- requirements: ID, risk, required or suggested and why, corresponding checks/journeys/perspectives, criteria, gaps, dependencies, and responsible person.
- assignments: ID, scope/question, fact-package references, criteria, evidence requirements, non-goals, permitted verification, and return format; the coordinator manages headcount/models and the shared budget, with no recursive delegation.
- budget: adopted round limit and basis, other confirmed limits or unknowns, measurement unit and scope, outer calls and batch subitems, calls/resources used, and stopping reason.

## Each Evidence Item

`id / requirement / subject / artifact / environment / data_state / specification / tool_or_model_version / config / time / steps / expected / actual / raw_status / status / output_refs / coverage / limitations / freshness`

Keep raw output in a controlled location and record its path and necessary summary; for evidence supplied by others, state the source and whether it was personally checked. Retain historical evidence and link invalidation records to new versions; do not overwrite it into a permanent pass.

## Each Finding and Disposition

`id / subject / location / trigger / impact / severity_reason / confidence_basis / evidence_or_reproduction / confirmed_or_question / introduced_or_pre_existing_or_uncertain / blocking_reason / proposed_recheck / disposition / rebuttals / original_view_reports`

Keep both original reports by review perspective and consolidation records traceable; even with zero findings, list actual coverage and unread/unverified portions. For unresolved key objections, state the responsible person and missing facts; votes do not eliminate them.

## Final Report to the User

1. **Subject and authorization**: the reviewed version, paths/requirements, and modes executed.
2. **Actual validation and review**: check IDs, statuses, evidence references, and coverage/limitations; truthfully describe execution and independent-context use.
3. **Findings and rebuttals**: unified IDs, confirmed facts/questions, impact, dispositions, and disagreements; repair and recheck evidence, and the relationship between old and new versions.
4. **Incomplete work**: key configuration, check, environment, or domain gaps; budget/stopping reason and responsible person.
5. **Stage recommendation**: READY_FOR_NEXT/HOLD/REPLAN and its basis; what is needed next, without implying a PR was created, merged, or released.

The report must be understandable to the user on its own; small tasks may use a concise presentation, but must not hide blockers, unknowns, or actual failures. This template is not a fixed roster of roles or a command set.

## Security and Cross-Stage Handoff Additions

- policy_binding: user/host designator, source path, content digest, designation time, and applicability; differences from reviewed rules and independent approval records.
- data_boundary: readable/writable paths, test data, permitted network and model recipients, filtering capabilities before model entry, redaction, and uncovered boundaries.
- budget_ledger: shared task ID, stage, round definition, cumulative rounds and calls/time/cost (where configured), consumption at this stage, remaining allowance, and stopping reason; entering the next stage does not reset it. Cross-stage allocation remains for project policy to determine.
- handoff: requirements/identity/artifacts/policy, valid and invalidated evidence with reasons, formal findings and objections, missing capabilities/human decisions, and applicable requirements for the next stage. Handoff grants no permission for PR comments, merging, or release.
