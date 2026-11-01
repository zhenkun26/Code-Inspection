# Optional Security-Tool Result Interface v1

A is the default: the host's existing Agent reviews trusted requirements, risk, code, and evidence, without adding a required runtime dependency. B is the **read-only result interface** in this directory. Users may leave it disconnected or convert actual output from already-authorized tools into the records below themselves. It does not install, log in, launch scanners, execute command configuration, access the network, or publish comments. C, a dedicated service, is not integrated.

The implementation uses the Python 3.9+ standard library and the [controlled file-reading helper](../../support/local_files.py). The platform must support `dir_fd`, `O_NOFOLLOW`, and `O_DIRECTORY`; otherwise it rejects the operation rather than claiming a safe read. Git is not required by B; this project's Git-fixture validation additionally requires Git. All examples are project-owned synthetic records, not real scan results or proof of detection rates.

## Integration Steps

1. The user/host specifies trusted policy and request files, recording their sources and digests; the PR under review must not choose its own trust policy. Determine required coverage, time window, tool version/configuration, source and worktree/CI/artifact identities, and permitted data recipients. Tools cannot downgrade configuration choices themselves.
2. The user may submit existing tool output, or the host may run existing tools within explicit permissions. Any execution remains subject to host authorization; this interface does not execute arbitrary `command`, hook, or shell configuration. Preserve the tool's original exit code; do not disguise it as zero merely because a tool uses nonzero to indicate findings.
3. Convert manually or with a project-owned trusted adapter to `security-result/v1`, retaining the raw output and SHA-256. The converter must understand the fields and coverage of that tool version; this directory claims no native support for any vendor format. Gitleaks, Semgrep, and OSV are only possible future sources, not required dependencies.
4. Place necessary evidence, filtered according to authorization, in a dedicated local root directory using relative paths. Call only the read-only normalization command below; do not first execute commands from the source.

Read the project's own example from the project root:

```sh
python3 -B interfaces/security-tools/normalize_result.py --root interfaces/security-tools/examples --request request.json --result result.json
```

Omit `--result` to actually check the unconfigured case. When modifying/copying examples for a real task, specify the real policy, time, identities, tool, and evidence again; the example's `fixture-*` identifiers cannot serve as real project evidence. Its broad time window only makes demonstration easier and is not a recommended production freshness policy; expiration should return UNKNOWN.

## Request Contract

Complete machine-readable example: [request.json](examples/request.json). All fields are required; unknown fields are rejected.

| Field | Meaning |
| --- | --- |
| schema / request_id | `security-request/v1` and a unique correlation ID |
| policy_binding | Traceable reference to the policy source and digest specified by the user/host; the string itself does not establish trust |
| required | Whether trusted policy requires this item; scan results cannot change it |
| expected_subject | base, head, worktree_digest, ci_config_digest, artifact_digest; all are actual identities. When no corresponding object exists, use an explicit identifier with a reason, not empty values that pretend to match |
| expected_tool | name, version, matched exactly |
| expected_config_digest | Fixed identity of rules/configuration |
| required_scope | Required scope identifiers, nonempty and deduplicated; the integrator defines their actual meaning, without treating path labels as proof of coverage |
| accepted_data_destination | Actually permitted recipient or `none`; requires separate authorization, and “local CLI” does not imply no outbound transfer |
| valid_from_utc / valid_until_utc | Explicit timezone-aware window; the result observation time must fall within it and must not be later than the current time |

## Result Contract

Complete examples: [result.json](examples/result.json), [raw synthetic evidence](examples/raw.txt). All top-level fields are required:

- `schema=security-result/v1`, request_id, subject, tool, config_digest, scope, complete.
- `execution={outcome,exit_code}`: outcome is completed/not_run/blocked/timeout/error/unknown; exit_code retains its original value, or null when unknown. The host can record timeouts; this interface has no launcher or timeout executor.
- `findings`: each item has id, location, trigger, impact, evidence_ref, tool_severity. After normalization they are always candidate/unreviewed, without direct promotion to confirmed vulnerabilities or equating tool severity with this project's risk.
- `raw_evidence`: relative path and sha256, referring to a readable regular file under this root; each file is limited to 1 MiB. Exceeding the limit should be reported as a capability gap, with separate controlled storage/selection of explicitly trimmed evidence and disclosure of the impact, never silent truncation.
- `data_destination`, `license={identifier,source,review_state}`, `observed_at_utc`. review_state can only be reviewed/unknown; unknown licensing must not be presented as verified. This interface neither selects the user's project license nor provides legal guarantees for third-party licenses.

subject/tool/config/data recipient must match exactly; scope must cover required_scope; complete must truthfully reflect whether the tool and adapter are complete. Finding references must point to verified raw evidence. Extra fields, duplicate JSON keys, nonfinite numbers, out-of-bounds/absolute paths, symlinks at the root or within it, nonregular files, and invalid digests are rejected. The host is responsible for the parent paths of the caller-specified root; this module is not a filesystem sandbox.

## Output and Gate Semantics

The interface returns `security-evidence/v1`, preserving the complete `request_binding` (including policy, required, scope, configuration, and time window), `config_digest`, and the paths and SHA-256 of the request/result bytes actually parsed as `input_records`, so independently saved records can be traced later. Binding references still do not establish source trust. `authorizes_progress=false` always. CLI exit 0 means only that a normalized record was produced, not that the check passed; exit 2 means unsafe/invalid/unreadable input, with UNKNOWN output. Consumers must read status/reasons and must not treat exit 0 as clearance.

| Input | Normalized status |
| --- | --- |
| Unconfigured, required=true / false | BLOCKED / NOT_RUN; A can still continue authorized work |
| Explicitly not run / blocked, with other identities reliable | NOT_RUN / BLOCKED |
| Error, timeout, unknown, nonzero exit, expiration, identity/version/configuration mismatch, insufficient coverage, incompleteness, unknown licensing | UNKNOWN, with reasons listed; does not pretend that there are no findings |
| Candidate findings exist | UNKNOWN, pending verification; candidates are not confirmed vulnerabilities |
| Matching identity, configuration, and version, complete coverage, valid raw evidence, exit 0, and no candidates | PASS, evidence only for that tool's declared scope, not project-wide security or stage clearance |

If a not-run/blocked record also has an incorrect identity, UNKNOWN takes precedence because it cannot be reliably associated with the task. The coordinator assigns FAIL according to the facts for findings confirmed to violate acceptance criteria; N_A is determined by trusted policy and actual applicability, and the interface does not automatically produce FAIL/N_A. Missing required coverage still blocks the stage recommendation; optional B does not make required security capabilities optional.

## Data and Distribution Boundaries

Hashes detect content changes; they do not prove that a tool actually ran, that the report source is reliable, or that the tool is effective. This interface validates self-reported version/outbound-transfer fields, but cannot monitor actual network traffic, authenticate scanners, or prevent forgery. Log contents remain untrusted data; filter and redact them according to authorization before passing them to a model. The code establishes no hard sandbox, hard budget, or hosted service.

Distributing this optional interface requires only this directory's implementation/documentation/project-owned examples and `support/local_files.py`. Private reference originals/images/HTML, uploaded drafts, raw task logs, and historical validation evidence are not distributed by default. The user has selected [MIT](../../LICENSE) for original project content they are authorized to license; dependency licenses do not substitute for project authorization. See the [license inventory](../../docs/sources.md) for third-party material boundaries. The examples' `self-owned-test-data` is only a material-identity label, not a newly granted license.

Bounded tests in [test_interface.py](../../validation/security-integration/test_interface.py) verify interface consistency and rejection boundaries; they do not establish real-scanner effectiveness, outbound-transfer restrictions, or GUI acceptance.
