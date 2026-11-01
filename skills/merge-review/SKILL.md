---
name: merge-review
description: Verify PR handoff and the actual combination, using a controlled merge adapter under separate authorization; does not merge user repositories by default.
---

# Merge Combination and Conditional Execution

Reuse the [core contract](../pre-pr-review/references/contract.md), [budget/rechecks](../pre-pr-review/references/recheck.md), and [merge-stage design](../../docs/workflow.md). Receive valid PR evidence, finding dispositions, human approvals, and budget already used; a stage transition does not reset the budget.

First use prepare to fix the current base, candidate, combination, and artifact, then obtain integration verification for them. Keep preflight, merge authorization, execution, and receipt separate; READY does not authorize an action. Without reliable conditional-update capability, use HOLD; a one-time check followed by an ordinary merge cannot pass as an atomic mechanism. Neither different SHAs nor identical source code automatically prove artifact equivalence.

Read the [lifecycle interface](../../interfaces/lifecycle/README.md) as needed during execution. The current LocalGit accepts only explicitly marked, isolated bare repositories and merge_commit; it is not for arbitrary user repositories. Explicitly report gaps when squash/rebase or a real platform is not integrated. If the outcome is uncertain, query first; do not retry blindly or bypass denials.

The coordinator controls reviews and budget without recursively forming teams; assign new logic introduced by conflict resolution to the designated implementer and review it again according to impact. Humans retain required risk and merge decisions; the Agent executes only within existing authorization. When delivering the [template](../pre-pr-review/references/record-template.md), fill in the actual stage, combination/evidence, authorization, and actual outcome; do not automatically deploy or release.
