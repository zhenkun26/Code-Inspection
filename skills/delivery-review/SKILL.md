---
name: delivery-review
description: Check delivery identity, bounded observation, and recovery recommendations for selected modules, invoking adapters within explicit authorization; does not implicitly deploy or establish persistent monitoring.
---

# Optional Delivery Observation and Recovery

Retain the [core contract](../pre-pr-review/references/contract.md), [budget/rechecks](../pre-pr-review/references/recheck.md), and [post-merge design](../../docs/workflow.md). First confirm selection of the overall module and flags/channels/observation/recovery, the responsible people, and authorization; a disabled module does not establish live acceptance, and a missing required capability cannot be N_A.

Distinguish merge/deploy/release/exposure, binding them to the actual artifact, environment, cohort, configuration, and bounded window. A plan is not authorization; if the outcome is unknown, query first. Promote the same artifact; rebuilding requires a new identity and additional verification. Missing, stale, empty, NaN, or insufficient-sample observations do not pass; do not automatically extend the window. Record pause/off, rollback to a compatible old artifact, and forward repair separately; switching off does not undo historical side effects.

When execution is needed, read the [lifecycle interface](../../interfaces/lifecycle/README.md) as needed. The default provider only reads and writes local simulated state; the optional [host delivery interface](../../docs/host-delivery.md) supports policy-selected deploy/rollback adapters, while other real actions are not yet supported. Do not treat simulation or interface-test receipts as proof of production effect, health, or successful recovery. A trusted host must integrate real backends and verify permissions, conditional updates, durable idempotency, and actual outcomes. Report missing capabilities as BLOCKED/UNKNOWN; do not install platforms to fill the gap.

Humans are responsible for key risk, acceptance, and authorization decisions; the Agent executes within explicit authorization or existing preauthorization. The coordinator controls assignments and budget without recursively forming teams; assign forward repairs to the designated implementer, then return to the applicable review stage. When delivering the [template](../pre-pr-review/references/record-template.md), fill in the actual stage, observation coverage, and gaps; do not automatically connect services, send data externally, deploy, toggle production switches, or create persistent tasks.
