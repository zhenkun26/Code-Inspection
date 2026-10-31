# Source manifests


This optional local capability records actual scoped source inputs. It does not authenticate a build, collect an entire machine, deploy a directory, or prove that a test/build consumed these inputs. Keep the existing [evidence, authority and recovery contract](../interfaces/lifecycle/README.md); the manifest supplies a stronger source identity, not a second approval system.

### Two modes, distinct identities

- `directory` describes selected regular files and directories, including empty directories, the platform’s declared mode projection, byte sizes and SHA-256 digests.
- `git-worktree` describes an ordinary local checkout, with separate HEAD blob, stage-0 index blob and actual working-file projections. A missing projection represents a deletion/addition. The index is not read through the working file, and working bytes are not inferred from HEAD or index stat caches. Staged-only, unstaged-only, deleted, included untracked and executable-mode changes therefore have different representations. Renames are deletion/addition, not a similarity claim. Raw bytes preserve CRLF and do not apply Git clean/smudge filters.

The canonical `source-manifest/v1` document includes mode, declared scope, literal exclusions, effective limits, mode model and deterministically ordered relative entries. Its SHA-256 is domain-tagged as `source-manifest-sha256:…` in the existing `subject.worktree_digest`; the subject still has five fields. Git mode also binds the supplied full base/head identities and actual HEAD tree. Timestamps, absolute source paths and inode numbers are not content identity. The trusted source locator remains in pinned host policy.

Completeness means the declared scope only. Exclusions change identity and must account for every intentionally omitted build/check input. Ignored files are not automatically safe or irrelevant: explicitly exclude them or resolve the coverage gap before capture; the scanner does not silently read their payloads. `.git` is reserved metadata rather than source payload. A name-based policy is not a secret detector. Keep manifest inventories and host paths private unless their disclosure is independently authorized.

### Trusted policy and capture

The optional `source_content` section in the existing pinned `policy.json` is:

```json
{
  "schema": "source-content-policy/v1",
  "root": "/host-selected/source-copy",
  "required": true,
  "config": {
    "mode": "git-worktree",
    "scope": ["."],
    "exclusions": [".env", "build-output", "private"],
    "max_files": 1024,
    "max_file_bytes": 1048576,
    "max_total_bytes": 8388608,
    "isolation": "host-controlled"
  }
}
```

This is an illustrative host choice, not an installed default or proof that those exclusions fit your project. Use `directory` for a non-Git source tree. A `.git` directory, file or link at the selected root or its ancestors is refused in this mode before enumeration; use Git-worktree mode rather than bypass ignored-path checks. Scope and exclusions are unique literal relative prefixes sorted by UTF-8 bytes, not arbitrary glob/shell commands. The root, its ancestors, executable selection and Git metadata/object store must remain under trusted host control. Capture does not freeze unrelated writers; a lifecycle lock is not a source-tree lock. Prefer a host-frozen source copy. Unknown isolation is not upgraded to a verified snapshot.

Capture the source record before initializing or advancing a session, then use its exact identity and sidecar together. A sidecar is `source-content/v1` with `manifest` and `source_subject_digest`; the association digest is computed after assigning the manifest identity to the subject. It is not included recursively in its own manifest digest. The host must preserve the other subject fields and verify their real meaning. Capturing a manifest does not start a review round or manufacture evidence.

Place this request in the trusted host root as `source-request.json`, supplying the task's real existing five-field subject rather than inventing commit/configuration identities:

```json
{"schema":"source-capture/v1","subject":{"base":"FULL_BASE_COMMIT","head":"FULL_HEAD_COMMIT","worktree_digest":"not-yet-captured","ci_config_digest":"HOST_VERIFIED_CONFIG_ID","artifact_digest":"HOST_DECLARED_ARTIFACT_OR_SOURCE_ONLY_ID"}}
```

From the checkout root, with the actual host root and trusted raw-policy digest:

```sh
python3 -B interfaces/lifecycle/cli.py --host-root HOST --policy-sha POLICY_SHA256 --record source-request.json source-capture
```

The command can run before `init`. It returns a `source-capture-result/v1` with a suggested subject and sidecar, and does not write a ledger, approval or policy, start a review round or charge an operation. The existing lock can be created for synchronization. `current_source_status: PASS` means this supported capture completed; `build_provenance: UNKNOWN` and `authorizes_action: false` remain. Only `worktree_digest` is derived; other subject fields are host declarations. Copy the returned `subject` and `source_content` together into the existing handoff or subject-change request, then use the ordinary `init` or `advance` flow. Keep this output outside the source scope to avoid self-reference.

Fresh gates independently capture the selected input again. A changed member, mode, index, scope, exclusion, policy or source association invalidates reuse; advance the subject explicitly, recheck affected evidence and obtain the required new approval. Missing required proof, unavailable/unsupported collection or changed input is UNKNOWN/HOLD, not a product-defect FAIL or a reason to reset the budget. A supplied optional proof is still validated; absence of the optional policy preserves the existing source-only/legacy flow.

New source-bound merge plans use `merge-proposal/v3`, with the exact source proof included in the plan ID. Existing merge preparation combines committed base/head objects: a directory-only proof or a Git proof containing staged, unstaged or included untracked byte/Git-mode differences cannot establish that those inputs are the candidate commit. Resolve that mismatch before merging. This does not prohibit reviewing dirty work in the PR-before/PR-during stages. Committed equivalence requires exact HEAD/index modes and selected file bytes. POSIX also compares the actual owner-executable bit; Windows requires explicit `core.filemode=false` and does not infer executable permissions from a suffix or read-only attribute. It does not put empty directories or other POSIX permission bits into a Git tree. Those filesystem attributes still change the full source identity and invalidate old evidence. Integration checks must establish any behavior that depends on them.

Historical plan validation is pure with respect to source files. A valid already-requested/receipted operation still uses exact historical query/idempotent recovery without recapturing today's source, charging again or replaying an action. Legacy labels are not converted to proofs; adding a sidecar to an approved plan changes its identity. Existing malformed/unbound historical receipts remain refused. The 1 MiB ledger limit remains: a large proof can make persistence stop, never discard history to fit it.

### Supported boundary

POSIX uses descriptor-relative no-follow traversal. Windows supports CPython 3.13 on Windows Server 2025 x64 with a local fixed NTFS volume and Git for Windows, within [the native boundary](windows-support.md). Directory enumeration and leaf reads use held native handles; selected file handles remain open with read-only sharing through capture, blocking ordinary write/delete opens. Attribute-only writes and malicious host control are outside that sharing guarantee; unsupported namespaces/filesystems and unavailable safety operations fail closed. Native tests must actually collect directory and HEAD/index/worktree inputs; skipped POSIX-specific cases are not native collection evidence.

Stored `posix-permissions/v1` proofs keep their original semantics. New Windows proofs use `windows-readonly/v1`: mode `0` or `1` records absence/presence of FILE_ATTRIBUTE_READONLY for files and directories. This is not an ACL, effective-write-access or executable-bit assertion. HEAD/index retain Git modes `100644`/`100755`; an index-only executable change changes identity without fabricating a working-file executable bit. Changing read-only attributes also changes full identity. The validator is platform-independent and never rewrites a historical mode model or upgrades an approval. Windows Git capture requires explicit `core.filemode=false`; a missing/true setting is unsupported. Raw working bytes, including CRLF, are compared as bytes: no implicit line-ending normalization makes a changed checkout look committed.

Supported paths have strict UTF-8 spelling and portable relative components. Entries are sorted deterministically; ambiguous case/Unicode aliases are rejected rather than silently normalized. Included symlinks/reparse points, hardlinks, special files, traversal escapes, unsupported index states and submodules are refused. Git mode also refuses LFS pointer content instead of claiming the external object was captured; directory mode describes literal file bytes only and never resolves LFS objects. Ordinary Git checkouts require local supported objects and a validated metadata layout; linked worktrees, alternate object stores, partial/sparse/split-index configurations and missing objects are outside this version. No network fetch, checkout, filter, hook or index write is performed.

Collection caps are 1,024 selected file/directory entries, 1 MiB per file/blob, 8 MiB aggregate logical bytes (all HEAD/index/worktree projections count), depth 32, 4,096 UTF-8 path bytes, 4,096 visited entries per enumeration, and a 1 MiB canonical manifest. Git commands have bounded stdout/stderr, a per-command deadline and a bounded capture group. Policy can lower the configurable file/count/byte caps. Errors, caps, observed concurrent changes and unsupported input produce no complete proof. Held descriptors, repeated observations and metadata checks detect observed races but do not prove a directory-wide atomic instant or defeat a malicious trusted writer. The result is scoped consistency under the declared host isolation, not a sandbox, hard memory quota or authenticated provenance.

Directory **artifacts** are not accepted by the current single-file deployment consumer. See [artifact content](artifact-content.md) for its distinct identity and immutable-byte consumption. Do not replace an artifact digest with a source manifest and claim a deployment was verified.
