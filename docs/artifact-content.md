# Single-file artifact content

The optional lifecycle interface can bind **one regular file, up to 1 MiB**, to a merge candidate or simulated delivery operation. It computes SHA-256 and length from the bytes it actually reads. The artifact identity is `sha256:<raw-byte SHA-256>`; this is distinct from the canonical JSON hash used for a plan ID.

This is content consistency, not authenticated build provenance, a test result, or a production deployment adapter. The host still decides which source, build configuration and artifact belong together. A source-only review does not require a fictional build artifact. Directory manifests, complete dirty/untracked build inputs, remote objects and production providers are unsupported by this capability; do not substitute HEAD for those inputs or claim they were verified.

## Trusted policy and files

Before initializing a new session, the host may add this optional field to `lifecycle-policy/v1`, then obtain its new policy pin through the existing trusted channel:

```json
{
  "artifact_content": {
    "schema": "artifact-content-policy/v1",
    "required_actions": ["merge-execute", "delivery-deploy", "delivery-rollback"],
    "max_bytes": 1048576,
    "isolation": "host-controlled"
  }
}
```

The action list is a project choice, not an installed default. It may be empty, or select a subset of those three actions. A required deployment proof also applies to subsequent promotion/enabling through its confirmed deployment source. Pause, disabling exposure and forward-fix handoff do not consume artifact bytes. Missing policy preserves the existing label-only/source-only flow. A supplied proof is always checked even when not required.

`host-controlled` is an explicit **host assertion** that its artifact namespace and writers are controlled; it is not an OS check performed by this program. Use `unconfirmed` when that prerequisite is unavailable: a new content-bearing action stays UNKNOWN/HOLD. The host protects the policy, authority, ledger and `HOST/artifact-files/` directory. Do not let a reviewed PR, log or concurrent untrusted process choose those permissions or rewrite them. Changing an established policy requires the existing explicit migration/reapproval process; this feature does not change old pins, counters or approvals.

Place the selected file below `HOST/artifact-files/`. Descriptor paths are portable relative locators below that directory, never an absolute path or content identity. Traversal, empty/dot components, reserved names, symlinks/reparse points (including intermediate components), hardlinks, special files and directories are rejected. The shared handle-based reader enforces the configured bound, with at most one extra detection byte if a file grows, and rejects observed instability. It does not claim a whole-process memory limit or immunity to a malicious host. Windows remains limited to the [documented native backend](windows-support.md).

## Plan and approve

For `merge-prepare`, retain the existing `merge-plan/v1` fields and add an `artifact_content` descriptor. The following is a field example, **not runnable evidence**: replace the hashes, size and locator with the host's actual expected values.

```json
{
  "integration_artifact": "sha256:<expected raw-byte SHA-256>",
  "artifact_content": {
    "path": "build/application.bin",
    "algorithm": "sha256",
    "sha256": "<64 lowercase hex characters>",
    "size_bytes": 123,
    "build_config_digest": "<64 lowercase hex characters>"
  }
}
```

The interface reads the file and compares actual bytes against the expected SHA/length. Unknown algorithms, a hash of a label instead of the bytes, a well-formed but incorrect hash, and mismatched artifact identity fail. It derives the proof's source-subject digest, real candidate tree and CI configuration from the already validated session/combination. `build_config_digest` is a host-declared build-configuration identity, **not independently read or authenticated build configuration**. The fixed proof markers `host-declared-association` and `single-file-snapshot` cannot be replaced by an `authenticated` claim.

Content-bearing merge plans use `merge-proposal/v2` and store `artifact_content={proof, locator}`. The locator is outside the proof's content identity, but the whole container is part of the canonical plan ID. The proof also binds source, tree and configuration association. Changing bytes, associations or locator requires a different plan and exact execution approval; adding fields to an old approved plan does not preserve its ID. Integration checks and human approvals still follow the [existing lifecycle contract](../interfaces/lifecycle/README.md).

Deploy plans inherit the exact merge proof automatically. New rollback plans additionally supply the same descriptor shape as `artifact_content` and an `artifact_source_merge` identifying a validated, confirmed historical merge. They verify the rollback target's own bytes and retain the existing compatibility mapping for the current data version. A rollback parameter `artifact` must equal its `sha256:<raw-byte SHA-256>` identity, and the host's `rollback_compatibility` map must explicitly cover that identity. A legacy source merge can provide a new host-declared association, but is never rewritten to pretend its old label proved those bytes or their build origin.

Content-bearing delivery proposals use `delivery-proposal/v3`. Promotion and enabling inherit the exact confirmed deployment proof rather than re-reading a path as evidence of what is deployed. No arbitrary content descriptor can replace that source relationship. Existing request/receipt versions retain their plan-ID binding; byte-consuming simulated receipts additionally record the consumed SHA-256 and length.

## Fresh consumption and historical recovery

New execution rechecks content before charging an execution attempt or recording pending intent. Delivery receives a frozen in-memory `Snapshot` of immutable bytes, hash and length; `LocalSimulation.apply` verifies that same object against the approved proof without reopening the locator. Replacing the file after snapshot acquisition cannot change those consumed bytes. A real adapter must consume the same controlled content object or provide equivalent verified digest enforcement; the current implementation does not connect one.

Merge consumes the already checked Git commit, not a deployment file. Its content check is a point-in-time evidence association; it does not lock a path until future delivery. Delivery therefore acquires its own approved snapshot. Hashing expected bytes does not prove an atomic historical file version or the absence of concurrent same-inode writers. Handle checks and the expected hash reject detected inconsistency; writer isolation remains a host prerequisite, and unavailable isolation stays UNKNOWN/HOLD.

Historical plan/receipt validation checks stored structure and associations without opening artifact files. Valid already-attempted merge/delivery queries and repeat execute remain uncharged recovery paths after file removal, current-subject changes, approval expiry or exhaustion. Response-only `content_binding` identifies legacy labels versus stored file proofs, always reports current-file verification NOT_RUN and build provenance UNKNOWN. It is never a new permission or a fresh file PASS.

Legacy label records remain readable, unverified and unchanged. New required actions cannot proceed without a proof; absent optional proof is not automatically a product FAIL. Unknown/missing content produces UNKNOWN/HOLD, while existing valid recovery still obeys the original strict receipt/intent rules. No history is backfilled, no budget is reset, and malformed/unbound old receipts are not upgraded.

## Verification scope

Run the maintained focused checks from the checkout when disposable local fixtures are authorized:

```sh
python3 -B validation/lifecycle/test_artifact_content.py
python3 -O -B validation/lifecycle/test_artifact_content.py
```

They use real bounded file reads and isolated Git objects, with synthetic human records and a local simulated delivery provider. The unified checker also runs this file in both Python modes; fixed guard-removal checks test selected invariants. The old label-only baseline allowed an associated file to change without invalidating an approval: that is evidence of the previously documented capability gap, not a claim that its simulated provider violated a production guarantee. Tests do not establish build provenance, real deployment, or every filesystem race.
