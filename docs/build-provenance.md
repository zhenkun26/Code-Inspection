# Optional build provenance verification

There are three different claims:

1. [Artifact content](artifact-content.md) checks actual SHA-256 and length. A matching file does not identify its builder.
2. A source manifest and a build-configuration digest associate inputs with that file. The association is still host-declared.
3. A trusted host verifier can evaluate external build evidence against a policy-selected trust root and exact expected inputs. This interface validates its result and binds it to a plan. It does **not** bundle an external identity, signature, CI attestation or production provider verifier.

## Default and trust boundary

The default remains offline and requires no additional service or runtime package. The CLI has no verifier registry. A policy requiring build verification therefore returns UNKNOWN/HOLD until a trusted embedding host supplies the selected capability. A JSON field saying `verified`, an actor name, a CI URL, a self-signed document or a well-formed digest cannot install that capability.

The host may construct `Session(host_root, policy_sha, verifiers={verifier_id: host_verifier})` in its own trusted Python application. The callable is application code with the host's existing permissions, not a sandboxed plugin. Do not obtain it from reviewed code, import a module named in a report, or execute commands/URLs from evidence. The host must control its implementation, timeouts, cancellation, credentials, transport, external trust roots and current revocation checks. This package neither creates these nor silently enables network access. A blocking callback has no hard timeout imposed by this interface.

Treat verifier and trust-root identifiers as versioned host selections: changing their implementation or trust meaning requires a new policy and reapproval, not reusing an old identifier. The existing protected policy pin selects the verifier and trust-root identifier; the report cannot choose them. Protect that policy, the registry, authority and ledger from the reviewed project. This contract cannot defend against an attacker who can rewrite the trusted host itself.

## Policy and evidence

Before creating a session, the host may add `build_provenance` to `lifecycle-policy/v1` and distribute its new policy pin through the trusted channel:

```json
{
  "build_provenance": {
    "schema": "build-provenance-policy/v1",
    "required_actions": ["merge-execute", "delivery-deploy", "delivery-rollback"],
    "verifier_id": "project-build-verifier",
    "trust_root_id": "approved-build-trust-v1",
    "repository": "host-selected repository identity",
    "issuer": "host-selected issuer",
    "builder_id": "host-selected builder",
    "build_environment": "host-selected build environment",
    "max_age_seconds": 3600
  }
}
```

These are illustrative selectors and an illustrative age limit, not approved project defaults or working credentials. The host chooses them explicitly. Required deployment verification also covers promotion and enabling exposure; disabling, pause and forward-fix handoff do not assert a build's origin. Optional absence preserves source-only/legacy use. A supplied proof is always validated.

A `merge-plan/v1` request can include this descriptor alongside its existing actual `artifact_content` descriptor:

```json
{
  "build_provenance": {
    "run_id": "exact immutable build run identity",
    "run_attempt": 1,
    "evidence_path": "build-evidence.bin",
    "evidence_sha256": "<64 lowercase hex characters of actual evidence bytes>"
  }
}
```

Evidence resides below `HOST/provenance-evidence/`. The locator is only a bounded relative path. The existing no-follow reader rejects links/reparse points, hardlinks, special files, escape and oversized input; at most 1 MiB is accepted. Bytes are compared to the expected SHA before the callback is called. The callback receives the exact immutable bytes and a copied expected binding. It must independently check those bytes against its configured trust root, not simply echo the expected binding as a successful result. The run ID/attempt in this request are assertions to check, not evidence of their own authenticity.

The binding includes the complete original source subject (including base/head and scoped worktree identity), exact **combined candidate tree**, CI/build configuration identities, raw artifact SHA/length, repository/issuer/builder/build-environment selectors, run/attempt, task, policy pin, action and the substantive plan digest. The candidate tree is not the original head tree. The adapter must reject evidence for a build of another tree. This version does not claim that a build executed the generated synthetic merge commit: prepare generates that commit, and the build contract is about the specified source identities and combined tree. An integration needing a build of the exact synthetic commit requires a separate prepare/build/reattach workflow, which is not implemented here.

The plan digest in this binding excludes `schema`, `id` and `build_provenance` to avoid a circular digest. The final plan ID includes the entire stored proof, including its descriptor. Replacing evidence, run, source, configuration, artifact, target or plan intent requires replanning and exact approval.

## Host callback contract

`host_verifier(expected_binding, evidence_bytes)` returns only the strict `build-verifier-result/v1` fields:

- `status`: `verified`, `unverified` or `unknown`.
- `binding`: the exact observed association matching the supplied expected binding, after the host verifier has checked it against evidence.
- `evidence_sha256`: digest of the evidence actually checked.
- `built_at`, `checked_at`, `expires_at`: timezone-aware timestamps, ordered and within the selected age window.
- `revoked`: explicit boolean; revoked results cannot pass.
- `basis`: concise, sanitized explanation of the verification basis. Never put credentials, tokens, private paths or complete raw evidence here.

The public Python interface `provenance.verify(...)` returns structured status, check status, reason and scope. A valid selected-host result is `verified`; explicit rejection or revocation is `unverified`; missing capability/evidence, mismatch, malformed/expired result or a failed callback is `unknown`. Neither a result nor a plan grants action permission. `cryptographic_verification` and `remote_identity` remain UNKNOWN in the package's own output: external authentication is the supplied host verifier's responsibility, not something this reference implementation independently performed. All bundled test verifiers are synthetic and do not demonstrate real CI authentication.

Use `provenance.capture(...)` through merge/delivery planning to store a verified result. The package does not accept verifier-result JSON through ordinary evidence ingestion. There is no generic shell executor, URL fetcher, dynamic loader, key enrollment or network retry loop.

## Plans, execution and recovery

Plans carrying a verifier result use `merge-proposal/v4` or `delivery-proposal/v4`. Actual content binding and source-manifest validation remain separate and unchanged. Deploy inherits the original descriptor and cannot introduce one after a provenance-free merge; a host requiring deployment verification must supply it during merge preparation. Promotion/enabling inherits it through the confirmed deployment. Rollback may explicitly supply `build_provenance` with its historical source merge and artifact descriptor. A rollback verifies that target's source and bytes, not the current source's build.

Fresh execution revalidates the selected callback, exact evidence and association, age and revocation before charging a new execution attempt. A later valid observation can renew the check time without mutating an approved plan or changing its original build-completion time; it cannot change the run, evidence, source or action binding. Delivery actions also bind the current target/provider snapshot through the plan intent. Existing exact request/receipt IDs prevent a different build or target from borrowing a previous success.

Historical validation is structural and does not call the verifier or open historical evidence. Exact already-attempted query/repeat execution remains available after evidence removal, expiry, revocation or budget exhaustion, without replay or another charge. Historical output reports a stored result and current verification UNKNOWN, never a renewed authentication. Old labels and plans are not upgraded, counters are not reset, and policy changes still require explicit migration/reapproval. Missing required verification blocks **new** actions rather than disabling safe recovery of a known attempt.

## Verification and remaining integration

The maintained checks exercise real isolated Git and actual bounded evidence reads, plus synthetic host verifiers and simulated delivery. They cover binding mismatches, false claims, missing capabilities, tampering, freshness/revocation, plan/receipt association and historical recovery in ordinary and optimized Python. These are interface and state-machine tests, not signature-validation, hosted-CI or production acceptance.

A real integration still needs the host's selected verifier implementation and version, configured issuer/key or platform identity trust, repository/builder/environment selectors, authenticated evidence acquisition and revocation/freshness policy, plus its own negative and end-to-end tests. No specific vendor, service purchase or new credential is required by this package; without such integration the honest outcome is UNKNOWN/HOLD when required.
