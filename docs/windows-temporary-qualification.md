# Windows temporary API qualification (experiment only)

This experiment collects API observations for the proposed Windows managed temporary backend. It does **not** enable managed Windows save/recovery, qualify the managed save/recovery ordering contract, migrate residues or prove power-loss durability. The product's Windows managed capability remains unsupported. See [temporary state](temporary-state.md) for the unchanged production contract.

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

## 简体中文

这是 Windows 临时文件原生 API 的隔离资格实验，不是生产清理实现。产品的 Windows 受控保存/恢复仍为 unsupported，不证明受控保存/恢复所需的持久顺序；普通 `Session.persist()` 成功仅表示保存调用成功，不独立证明掉电持久性，不迁移或清理旧残留。

只有明确授权后，才在 Windows Server 2025 x64、CPython 3.13、本地固定 NTFS 上运行上方两条命令。程序不接收用户目录/目标参数，只创建自己的临时目录及固定小文件；不运行 shell/网络命令，不安装依赖、不修改权限/系统设置、不打开用户账本。实验目录及不确定残留有意保留，不递归清扫；输出仅含固定标签和数字状态码，不含私人路径或文件内容。

实验区分 API 调用返回值、同句柄删除/关闭/路径缺失的可观察事实，以及仍然 UNKNOWN 的目录项和掉电持久性。即使调用成功，输出也必须保留“未资格通过、产品未启用”；完成观测不等于生产验收或审批。超时、畸形结果、非预期失败保持未完成/未知，不重试删除，也不由父进程清扫超时工作进程的目录。

重命名保持源句柄和固定的相对根；x64 缓冲区包含完整结构及 UTF-16 名称，字节长度和双字节终止符均在传入范围内。仅调用一次 `NtSetInformationFile`，使用原生 `FileRenameInformation=10`，要求返回成功且 IOSB 确实写入成功结果。pending、未写入或失败都停止，不回退到路径操作或第二次调用。此前 Win32 调用在该 runner 上补足缓冲区后仍返回错误 87，不能据此宣称所有 Win32 相对根调用均不支持。原生失败仍是失败观测；布局和调用路由测试不能证明 Windows 调用成功。

当前工作流在既有 Windows job 的维护检查及变异之后接入两条原生命令，各步骤另设两分钟工作流上限；失败仍使 job 失败，未取消时第二模式仍执行。配置存在不代表已运行，应查精确提交的实际日志；Mac 拒绝/模拟编排测试不能代替 Windows 原生运行。本实验也不是全套归属协议；所有崩溃点、重解析点/映射/ADS 威胁、重启/故障存储及四阶段联动仍需后续验收，POSIX 和旧 Windows 行为不变。

工作进程有 55 秒看门狗，父进程另设 60 秒上限，并有既有有界清理尝试和输出上限；两模式可预留约三分钟，实际原生时长以精确运行日志为准，超时后的清理也可能 UNKNOWN。工作流复用现有 Windows runner，不新增服务/矩阵，不调整既有 job 或检查时限；实际费用取决于账户计划与用时，不能称免费。
