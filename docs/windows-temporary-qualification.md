# Windows temporary API qualification (experiment only)

This experiment collects API observations for the proposed Windows managed temporary backend. It does **not** enable managed Windows save/recovery, qualify the managed save/recovery ordering contract, migrate residues or prove power-loss durability. An ordinary successful `Session.persist()` establishes a successful save call, not independent proof of power-loss durability. The product's Windows managed capability remains unsupported. See [temporary state](temporary-state.md) for the unchanged production contract.

## Scope and invocation

Run only after authorizing disposable fixture creation and exact fixture-file deletion on Windows Server 2025 x64, CPython 3.13, a local fixed NTFS volume. No arbitrary root or target argument is accepted. It uses a fresh test-owned system temporary directory, fixed small payloads, system DLLs and the existing checked native root. It does not install packages, use network/shell commands, alter ACLs/settings or open user state. Unknown historical temporary files are never inspected or removed. The fresh experiment directory is intentionally retained, including uncertain leftovers; the harness never recursively sweeps it. Output uses fixed labels and numeric status codes, not private paths or file contents.

```text
python -B validation/windows_temporary_qualification.py --authorize-owned-fixture
python -O -B validation/windows_temporary_qualification.py --authorize-owned-fixture
```

The current workflow wires both commands into the existing Windows CI job, after the maintained checks and mutations. Each step has an additional two-minute workflow bound; either experiment failure remains a job failure, and the second mode still runs unless the job is cancelled. Configuration is not evidence of execution: inspect the exact commit's actual logs. Pure orchestration tests are included in the normal maintained checker; mocked orchestration and non-Windows refusal are not native qualification.

The experiment keeps three conclusions separate:

- API availability and returned native status codes.
- Observable same-handle disposition, close/absence and expected access/sharing failures on self-created files.
- Directory-entry persistence and real power-loss ordering: **UNKNOWN**, regardless of successful calls.

The result must always say the product capability is not enabled and native cleanup is not qualified. A completed observation command does not grant lifecycle approval or make an unsupported directory barrier acceptable. A timeout, malformed result or unexpected failure is incomplete/unknown, not a partial success. The parent does not retry a deletion or remove a timed-out worker's directory.

## Evidence limits

[Microsoft's handle-information API](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-setfileinformationbyhandle) supports marking an already opened DELETE-capable handle for deletion. An accepted disposition is not itself proof of completed deletion: close and an exact absence observation are separate facts.

The rename probe keeps the source handle and pinned relative root. Its x64 buffer includes the complete fixed structure and UTF-16 name, with byte length and a wide terminator inside the supplied allocation. It makes one [NtSetInformationFile](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntifs/nf-ntifs-ntsetinformationfile) call with native `FileRenameInformation=10`, requiring both a successful return and a written, successful IOSB. Pending, unwritten or failed completion stops the probe; there is no path-based or second-call fallback. The earlier Win32 invocation returned error 87 on the target runner even after its allocation was corrected. This does not establish universal Win32 relative-root incompatibility. Native failures remain failed observations; ABI/routing tests are not evidence the Windows call succeeded.

[NtFlushBuffersFileEx](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntifs/nf-ntifs-ntflushbuffersfileex) describes normal file data/metadata/cache flushing. Native directory-handle behavior and required access must be measured. Successful calls do not establish all ordering guarantees needed for child registration, rename and deletion across actual power loss. [FlushFileBuffers](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-flushfilebuffers) also describes an administrative volume-wide option; this experiment never uses it or requests extra privileges.

This small probe is not a complete ownership/recovery implementation. It does not cover every crash point, reparse/mapping/ADS attack, reboot, faulty storage or lifecycle integration. Those remain required before any separate production enablement proposal. Existing POSIX recovery and legacy Windows behavior remain unchanged.

## Runtime and publication boundary

The worker has a 55-second watchdog; the parent applies a 60-second direct-worker deadline plus the existing bounded cleanup attempt and bounded output. Read measured duration from the exact run; reserve roughly three minutes for both modes, including startup and cleanup allowance. Timeout cleanup can remain UNKNOWN. No process-tree/sandbox guarantee is added. The workflow reuses the existing Windows runner without adding a service or matrix, or changing existing job/check timeouts. Actual billed usage depends on the user's GitHub plan and run duration; this is not a zero-cost claim.
