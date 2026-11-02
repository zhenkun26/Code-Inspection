# Native Windows scope

The optional local record runner has a Windows backend implemented with Python's standard-library ctypes and system DLLs. The intended combination is Windows Server 2025 x64, CPython 3.13, Git for Windows and a local fixed NTFS volume. Check the exact commit's Actions results before relying on this support; source availability is not evidence that a native test ran.

The original Ubuntu/POSIX backend remains available. No additional Python package, registry change, Developer Mode change or new credentials are needed. Native test fixture creation must already be allowed by the host. Failure to create a required symlink/junction test is a failed verification, not a skipped proof of safety.

## File and trust boundary

Evidence is opened one component at a time relative to a held directory handle, using NtCreateFile and OBJ_DONT_REPARSE. The checked leaf handle also supplies the bounded bytes. All reparse tags are refused, including junctions and cloud placeholders. Directory checks alone and lexical prefix comparisons are not containment mechanisms.

Relative record paths use `/`. ADS, device names, ambiguous trailing dots/spaces, traversal and user-provided UNC/device namespaces are refused. Chinese, supplementary Unicode characters and ordinary spaces are supported. Trusted caller root selections may be ordinary relative paths such as `.` (anchored to the host working directory); root-relative and drive-relative forms are refused. Initial absolute root paths must be shorter than 240 UTF-16 units; this release does not enable long-path system settings. Mapped drives, network filesystems, non-NTFS volumes, WSL and ARM are outside the support claim. Path syntax refusal tests cover some unsupported inputs; they do not constitute execution testing on every unsupported filesystem.

Policy/authority/state roots and their ancestors must remain under trusted host control. LockFileEx serializes cooperating callers; it is not authentication or protection from a writer with host permissions. State replacement uses a same-directory temporary file. Sharing or cleanup failure is reported and never handled by deleting the old ledger first. Hardlinks and memory mappings are not a provenance or confidentiality guarantee. The isolated Git adapter separately requires a protected, self-owned repository; safe Python reads do not sandbox Git.

Source text is distributed as LF via .gitattributes. Raw evidence can contain CRLF, but its hash binds the actual bytes; changing line endings invalidates the prior hash. Commands use explicit UTF-8 text artifacts. Do not edit a trusted policy's bytes and expect an old pin to remain valid.

## Source directory and Git manifests

The optional source collector uses held-handle `FileIdBothDirectoryInfo` enumeration with a fixed buffer, then relative no-reparse opens for metadata and payload. It pins the root/ancestor and visited directory chain against rename/deletion and retains included file handles with read-only sharing through capture, compares volume/file IDs and size/link/write/change metadata, and repeats bounded enumeration. Named data streams, included reparse points and hardlinks are refused before payload reads. Ordinary write/delete opens conflict with these retained files; an already open writer causes acquisition to fail. Retained leaf handles provide the bounded bytes. Attribute-only writers and hostile host control remain outside this sharing guarantee. It does not freeze all writers or prove a cross-file atomic instant. Host-controlled frozen inputs remain required; unsafe/unsupported observations produce UNKNOWN/HOLD, not a partial complete proof.

The distinct `windows-readonly/v1` mode records only the read-only attribute (0/1); it is not an ACL or executable permission. Git HEAD/index modes remain exact, and `core.filemode=false` must be explicit. Working files are actual bytes, so CRLF transformations may legitimately prevent equivalence with HEAD. Existing POSIX proofs and pure historical recovery retain their original meaning. See [source manifests](source-manifests.md) for scope, exclusions, limits and binding.

## Verification

```text
python -B validation/run_checks.py --keep-artifacts
python -B validation/mutation_check.py
```

A Windows-only early step runs native manifest boundary tests in both Python modes before the full checker, so native regressions fail promptly without reducing full coverage. The native safety file adds process lock contention/release, ABI/path tests, real reparse fixtures and synchronized rename/attribute mutation attempts, occupied replacement, cleanup failure and handle-leak controls. It also exercises portable Unicode/CRLF/timeout/interruption controls. Native cases are explicitly N_A on POSIX; they are mandatory on Windows. Windows has no POSIX FIFO test. These platform distinctions must remain visible in reports.

Windows CI uses two serial jobs on the same supported runner class: native prechecks and the complete maintained inventory (40-minute ceiling), followed by the complete Windows mutation inventory, both API observations and a strict completion gate (25-minute ceiling). Ubuntu jobs remain 15 minutes each. Windows maximum concurrency stays one per workflow; configured cumulative ceilings rise from 50 to 65 Windows runner-minutes and from 80 to 95 for the full workflow. A second checkout/runtime setup is necessary; serial scheduling does not promise a shorter wall time or lower cumulative cost. These are configured limits, not bills or measured reliability percentiles.

The sizing evidence is [run 41362302803](https://github.com/zhenkun26/Code-Inspection/actions/runs/41362302803): Windows maintenance passed in 37m10s, but the old combined 50-minute job ended after only 153 of 196 mutations; both API observations were skipped. The targeted local mutation optimization had not yet been reached, so that run did not measure its Windows saving. Splitting separates the time budgets without removing work. The new ceilings still require exact-revision measurement and are not guaranteed to fit every runner. A further timeout must be diagnosed; no automatic retries or unreviewed ceiling increases are implied.

Every maintained command retains 180 seconds, repeated native prechecks now explicitly use that same command bound, each unmodified/mutated subprocess retains 90 seconds, and each API-observation step two minutes (with the existing worker/parent bounds). All original checks, normal/optimized modes, native prechecks, positive controls and applicable mutations remain required. Independent runners use fresh fixture roots; the only cross-job data is bounded metadata tied to candidate SHA/tree, workflow configuration, run and attempt. The final Windows check retains its previous name but now validates both stages: missing/failed/skipped/cancelled outputs, wrong versions and incomplete or duplicate inventories cannot pass. Workflow validation is not a claim that branch protection has been enabled, nor does it enable product cleanup or prove power-loss durability.

Timeout control covers the direct subprocess and retained output. It does not promise termination of an arbitrary process tree or a sandbox. Failed cleanup retains its primary error and diagnostic cause. For an uncertain merge/delivery result, query before any further attempt.

The offline lifecycle demo performs a real tiny content check and isolated Git CAS. Review/person decisions and delivery remain simulations. Native CI does not validate an agent host's Skill discovery, production deployment or model-review quality.

Sources: [NtCreateFile](https://learn.microsoft.com/en-us/windows/win32/api/winternl/nf-winternl-ntcreatefile), [OBJECT_ATTRIBUTES](https://learn.microsoft.com/en-us/windows/win32/api/ntdef/ns-ntdef-_object_attributes), [LockFileEx](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-lockfileex).

Source collector references: [directory records](https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-file_id_both_dir_info), [handle queries](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-getfileinformationbyhandleex), [stream records](https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-file_stream_info), [Git filemode](https://git-scm.com/docs/git-config#Documentation/git-config.txt-corefileMode). These specify APIs, not validation of this implementation.

The optional managed temporary write/recovery capability is currently POSIX-only and refuses Windows before managed mutation. Existing Windows save and metadata-only temp-report remain available; native managed deletion requires a separate identity-bound handle backend and native validation. See [temporary-state boundaries](temporary-state.md).

## Managed temporary qualification experiment

[The isolated qualification experiment](windows-temporary-qualification.md) records native API observations only. Windows managed cleanup remains unsupported; successful probe execution is not durability or production acceptance.

The [isolated qualification experiment](windows-temporary-qualification.md) records native API observations only. Windows managed cleanup remains unsupported; a successful experiment does not establish durability or production acceptance.
