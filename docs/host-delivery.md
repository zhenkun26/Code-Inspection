# Optional trusted-host delivery provider

The bundled provider still modifies only local simulation state. A trusted Python host may separately supply a provider for **deploy and rollback**. This interface validates policy selection, intent, content and correlated receipts; it does not contain a production connector, credentials, remote authentication or a deployment service. Its tests use synthetic provider objects and real isolated Git/file fixtures.

## Selection and permissions

The default CLI installs no provider. The embedding host uses `Session(host_root, policy_sha, delivery_providers={provider_id: provider_object})`; the object implements `read(context)`, `apply(request, snapshot)` and `query(request)`. It is trusted application code, not sandboxed code. The host controls transport, credentials, cancellation, bounded callback execution, remote identity, conditional updates and durable idempotency. This package cannot prove those guarantees by checking a returned JSON object.

An optional top-level `delivery_provider` in the pinned policy selects exactly:

```json
{
  "schema": "host-delivery-policy/v1",
  "provider_id": "host-selected-provider",
  "provider_version": "reviewed-implementation-v1",
  "target": "host-selected-target",
  "max_age_seconds": 300,
  "guarantees": ["conditional_apply", "durable_idempotency"]
}
```

The guarantee list is an exact set with no duplicates; order is irrelevant. These values illustrate fields, not approved defaults or a working connection. The host must establish both guarantees before opting in. Changing provider implementation, target or trust meaning requires a new selection/version and policy pin. Reviewed records cannot select imports, shell commands, URLs, credentials or a registry. No automatic install or connection occurs. Missing capability is UNKNOWN/HOLD, never simulation fallback or successful production acceptance.

The existing delivery module/capability selection, exact human authority, artifact policy, optional build-provenance policy and continuous budget remain authoritative. Host-provider deploy/rollback always require a supported actual single-file artifact, even when legacy simulation permits labels. The other real actions (flags, promotion, observation, pause and forward-fix) are not implemented in this interface; they cannot silently run against `sim-provider.json`. Existing simulated actions remain available when no host provider is selected.

## Read, intent and result

`read(context)` receives only the task, policy pin and selected provider/version/target. It returns a strict `host-delivery-state/v1` with those provider selectors, an opaque revision token, artifact, cohort, config, data version, explicit enabled/paused booleans, capabilities and observed time. A read must be current and for the exact target. The snapshot digest excludes observation time so a new read of unchanged state does not manufacture drift. All substantive fields remain bound.

A `host-delivery-proposal/v1` binds the full source subject, validated merge source, content proof, applicable build-verification proof, policy, provider selection, expected state and action effects. Its ID is the approval binding. Deploy uses the current confirmed merge; rollback uses a specified confirmed historical merge and compatibility with the current data version. Config and revision remain actual provider facts, not generated `local-config-N` values.

`apply(request, snapshot)` receives a copied immutable-intent `host-delivery-request/v1` and the exact bounded `artifacts.Snapshot` already checked. It must consume those bytes, not reopen the artifact locator. The request includes the operation/proposal ID, task/policy/provider/target, action, parameters, expected state/token, required effect, content proof and request digest. The provider must enforce that precondition and persist the operation's idempotency association atomically with its own action. A read followed by an unconditional remote write is not a compliant adapter.

`query(request)` must observe this exact operation without initiating it. Merely finding the same artifact at the target does not prove that the requested operation occurred.

A strict `host-delivery-result/v1` binds operation, request digest, provider/version/target and observation time. Status is `confirmed`, `pending`, `unknown`, `not_applied` or `failed`. Confirmed results additionally contain actual `after` state and consumed SHA-256/length. Validation requires the requested artifact, target, cohort/data and disabled exposure postconditions, changed revision and exact consumed bytes. Deploy begins disabled/unpaused; rollback begins disabled/paused. A contract-valid result remains an assertion from the trusted host provider, not independent identity authentication or health proof.

## Persistence and recovery

Fresh execution checks policy, current subject, authority, source/evidence/provenance, compatibility and target state before consuming budget. It acquires immutable artifact bytes, saves the existing charged attempt, and durably records pending intent before one `apply` call. A failed save fences the Session; an in-memory update does not establish durability.

Every existing attempt is **query-only**, including a provider's `not_applied` result. No automatic resend, replacement idempotency key or repeated charge occurs. An unresolved same-target attempt blocks a new plan and keeps stage evaluation on HOLD, even when current state matches an older successful receipt. This prevents retries disguised as new intent and prevents old evidence from hiding an unknown newer outcome. A genuinely new retry needs an explicit workflow with new reviewed intent and approval; deleting history or resetting budgets is not such a workflow.

Only strictly bound terminal results are cached. Cached historical outcomes remain readable without provider availability, fresh artifact files, fresh authority/provenance or remaining budget. They explicitly leave current state UNKNOWN and do not claim renewed health/authentication. Query can recover a missing correlated receipt after a lost response even when that immutable receipt is older than the current-state freshness window; its identity and time ordering still must match. This is historical recovery, not a fresh state assertion. Query cannot overwrite contradictory recorded outcomes. Ordinary callback failures are sanitized; cancellation propagates with the durable pending attempt retained. Provider timeouts/cancellation and termination of external work remain host responsibilities.

A host-selected policy cannot start a fresh legacy simulation action; existing exact attempted simulation recovery remains available. Old v2–v4 simulation plans keep their original validators and recovery path; they are not converted or relabeled real. Adding the optional policy block changes the pin, so an existing ledger cannot silently adopt it. Use the original pin for old historical recovery; any migration is a separately reviewed host action and cannot invent new approval.

## Verification and limits

Use the existing maintained checker and fixed mutation checks. New fixtures cover exact binding, missing capabilities, unsupported actions, immutable consumption, drift, failed/unknown/cancelled calls, persistence faults, historical recovery and no replay/charge. They make no production calls. Actual provider acceptance still requires the user's selected target, authenticated transport, permissions, conditional-write/idempotency implementation, independent outcome readback and negative/failure tests in an authorized environment.

No vendor, service purchase or new credential is required by the default Skill. Without a configured and validated provider, real execution remains unavailable; this interface does not turn the Skill into a hosted delivery platform.
