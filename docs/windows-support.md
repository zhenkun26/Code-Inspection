# Native Windows scope

The optional local record runner has a Windows backend implemented with Python's standard-library ctypes and system DLLs. The intended combination is Windows Server 2025 x64, CPython 3.13, Git for Windows and a local fixed NTFS volume. Check the exact commit's Actions results before relying on this support; source availability is not evidence that a native test ran.

The original Ubuntu/POSIX backend remains available. No additional Python package, registry change, Developer Mode change or new credentials are needed. Native test fixture creation must already be allowed by the host. Failure to create a required symlink/junction test is a failed verification, not a skipped proof of safety.

## File and trust boundary

Evidence is opened one component at a time relative to a held directory handle, using NtCreateFile and OBJ_DONT_REPARSE. The checked leaf handle also supplies the bounded bytes. All reparse tags are refused, including junctions and cloud placeholders. Directory checks alone and lexical prefix comparisons are not containment mechanisms.

Relative record paths use `/`. ADS, device names, ambiguous trailing dots/spaces, traversal and user-provided UNC/device namespaces are refused. Chinese, supplementary Unicode characters and ordinary spaces are supported. Trusted caller root selections may be ordinary relative paths such as `.` (anchored to the host working directory); root-relative and drive-relative forms are refused. Initial absolute root paths must be shorter than 240 UTF-16 units; this release does not enable long-path system settings. Mapped drives, network filesystems, non-NTFS volumes, WSL and ARM are outside the support claim. Path syntax refusal tests cover some unsupported inputs; they do not constitute execution testing on every unsupported filesystem.

Policy/authority/state roots and their ancestors must remain under trusted host control. LockFileEx serializes cooperating callers; it is not authentication or protection from a writer with host permissions. State replacement uses a same-directory temporary file. Sharing or cleanup failure is reported and never handled by deleting the old ledger first. Hardlinks and memory mappings are not a provenance or confidentiality guarantee. The isolated Git adapter separately requires a protected, self-owned repository; safe Python reads do not sandbox Git.

Source text is distributed as LF via .gitattributes. Raw evidence can contain CRLF, but its hash binds the actual bytes; changing line endings invalidates the prior hash. Commands use explicit UTF-8 text artifacts. Do not edit a trusted policy's bytes and expect an old pin to remain valid.

## Source directory and Git manifests

The optional source collector uses held-handle `FileIdBothDirectoryInfo` enumeration with a fixed buffer, then relative no-reparse opens for metadata and payload. It pins the root/ancestor and visited directory chain against rename/deletion while capturing, compares volume/file IDs and size/link/write/change metadata, and repeats bounded enumeration. Named data streams, included reparse points and hardlinks are refused before payload reads. It does not freeze all writers or prove a cross-file atomic instant. Host-controlled frozen inputs remain required; unsafe/unsupported observations produce UNKNOWN/HOLD, not a partial complete proof.

The distinct `windows-readonly/v1` mode records only the read-only attribute (0/1); it is not an ACL or executable permission. Git HEAD/index modes remain exact, and `core.filemode=false` must be explicit. Working files are actual bytes, so CRLF transformations may legitimately prevent equivalence with HEAD. Existing POSIX proofs and pure historical recovery retain their original meaning. See [source manifests](source-manifests.md) for scope, exclusions, limits and binding.

## Verification

```text
python -B validation/run_checks.py --keep-artifacts
python -B validation/mutation_check.py
```

The native safety file adds process lock contention/release, ABI/path tests, real reparse fixtures and synchronized rename/attribute mutation attempts, occupied replacement, cleanup failure and handle-leak controls. It also exercises portable Unicode/CRLF/timeout/interruption controls. Native cases are explicitly N_A on POSIX; they are mandatory on Windows. Windows has no POSIX FIFO test. These platform distinctions must remain visible in reports.

Timeout control covers the direct subprocess and retained output. It does not promise termination of an arbitrary process tree or a sandbox. Failed cleanup retains its primary error and diagnostic cause. For an uncertain merge/delivery result, query before any further attempt.

The offline lifecycle demo performs a real tiny content check and isolated Git CAS. Review/person decisions and delivery remain simulations. Native CI does not validate an agent host's Skill discovery, production deployment or model-review quality.

Sources: [NtCreateFile](https://learn.microsoft.com/en-us/windows/win32/api/winternl/nf-winternl-ntcreatefile), [OBJECT_ATTRIBUTES](https://learn.microsoft.com/en-us/windows/win32/api/ntdef/ns-ntdef-_object_attributes), [LockFileEx](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-lockfileex).

Source collector references: [directory records](https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-file_id_both_dir_info), [handle queries](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-getfileinformationbyhandleex), [stream records](https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-file_stream_info), [Git filemode](https://git-scm.com/docs/git-config#Documentation/git-config.txt-corefileMode). These specify APIs, not validation of this implementation.
