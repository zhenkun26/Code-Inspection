# Temporary-state inspection / 临时状态检查

## English

`save()` normally removes only its own temporary file. A forced exit, SIGKILL or power loss can bypass `finally`, leaving a `.lifecycle-*` file. The file may contain an incomplete or uncommitted ledger or simulated-provider state. Its presence is not an action receipt, and its contents must not be adopted as the current ledger.

From the checkout root, with an explicitly selected trusted host directory and the policy pin supplied by the user/host:

```sh
python3 -B interfaces/lifecycle/cli.py --host-root HOST --policy-sha POLICY_SHA256 temp-report
```

Use `python` on Windows if that is the installed command. This optional interface uses the existing Python/platform prerequisites; it needs no Git, model, service or new dependency. The report is `lifecycle-temp-report/v1` and is not a lifecycle decision or budget report.

- It acquires the existing host lock, verifies the bounded raw `policy.json` bytes against the pin before inspection, and rechecks the pin before returning. It does not parse `state.json`, so missing/corrupt state can still be inspected. Opening the existing lock may create the synchronization file; no ledger, counter or authority is changed.
- It inspects immediate entry metadata without following links. It never reads/hashes residue contents, recurses, deletes, renames, adopts state, retries a provider operation or resets a budget. Symlinks/reparse points, hardlinks, directories, special files and regular files are all retained.
- Enumeration stops at 1,024 inspected root entries or 64 reported matches (one additional iterator entry may be fetched to detect a limit). `complete: false` and `status: UNKNOWN` report a cap or unavailable entry metadata. A scan/policy/lock failure returns CLI exit 2 and UNKNOWN/HOLD instead of a successful inventory. A complete inventory with residues is also UNKNOWN. An empty complete inventory is PASS only for this bounded observation; it does not approve any stage or prove durability. Successful report delivery is exit 0 even when the reported status is UNKNOWN.
- Every residue has `ownership: UNKNOWN`, `action: RETAIN`. No PID, timestamp, age or filename proves ownership or inactivity. The lock excludes participating writers only. POSIX enumeration uses an opened directory descriptor; Windows enumeration is a pathname metadata observation under the existing checked root/lock. Neither is a consistent filesystem snapshot against unrelated host writers; `atomic_snapshot` is always false. Keep the selected root and its ancestors under trusted host control.

Unavailable hardlink counts (including Windows directory-entry metadata) are `null`, not a claim of one or multiple links. JSON escapes unusual filename code points so output remains UTF-8 safe. See [Python directory-entry metadata](https://docs.python.org/3.13/library/os.html#os.DirEntry.stat).

Keep reports private: filenames/size metadata can disclose local information. If state persistence was uncertain, a report does not clear that uncertainty. Preserve the current ledger and authority; use the existing exact-operation query/recovery contract to establish whether an operation was accepted. Never copy a residue over `state.json` or infer success from a matching filename.

**Default inspection still never deletes.** Existing anonymous residues lack verifiable ownership and remain untouched. The optional managed interface below applies only to new registered resources; it does not migrate old files, sweep a prefix, run a daemon or infer ownership from PID/age.

The self-owned tests exercise a real process exit after writing/fsync and before replacement, retained residue, unchanged committed state, ordinary-save controls, missing/corrupt state, pin/lock refusal, bounded enumeration, non-following metadata and native junctions where supported. This does not reproduce actual power-loss durability or authenticate ownership.


### Optional managed resources (trusted host, POSIX only)

The [managed temporary interface](../interfaces/lifecycle/owned_temporary.py) is separate from `temp-report`. It requires the trusted host to affirm that the selected namespace and its ancestors are protected and that **every writer participates in the same host lock**. This is a host integration precondition, not something a JSON record, command-line acknowledgement or filesystem permission check can prove. No input record can enable it. Do not enable it for a directory with unrelated writers. Supply a canonical absolute root; symbolic-link ancestors are refused rather than silently resolved (for example, select the trusted canonical macOS path instead of an alias).

```python
from interfaces.lifecycle.core import Session
from interfaces.lifecycle.owned_temporary import ManagedTemporaryNamespace

# These values and the exclusivity assurance come from the trusted host.
namespace = ManagedTemporaryNamespace(host_root, policy_sha, exclusive_namespace=True)
session = Session(host_root, policy_sha, temporary_namespace=namespace)
# Use session.locked() around the existing authorized lifecycle operations.

# A separate, explicit cleanup grant selects exactly one owner record.
with namespace.locked() as selected:
    result = selected.recover(owner_record_name, authorize=True)
```

No global/personal installation, service, model, Git operation or new dependency is required by this interface. The default Session and three-argument `save()` keep the legacy path; managed registration is opt-in. New managed target basenames start with an ASCII letter or digit and otherwise contain only ASCII letters, digits, dot, underscore or hyphen; Unicode host directories remain allowed. Reserved policy/lock/resource names are refused without relying on filesystem case sensitivity, and existing protected policy/lock identities are checked before staging. The namespace pins a POSIX directory descriptor and validates a regular single-link lock. Managed writers retain that lock through staging, registration, publication and ordinary cleanup. Recovery proceeds only if a fresh process actually acquires the lock; a surviving child or other holder can still block it. PID reuse, reboot time and file age are not inactivity evidence. The host must validate the filesystem’s locking and fsync semantics; local tests do not certify every POSIX or network filesystem.

Only a newly exclusively created, fully written and fsynced staging payload can obtain a bounded versioned owner record. The record binds the policy pin, root identity, exact random staging name, intended flat target, object identity/metadata, size and content digest. Registration is not build provenance, a lifecycle receipt or cryptographic ownership against a malicious writer already controlling the trusted root. A crash before complete registration can still leave an unknown residue; a torn, missing, inconsistent or concurrently changed record must be retained. A self-consistent record recreated by code with trusted-root write access cannot be distinguished from a genuine host record; the trust boundary still applies.

Recovery validates the exact selected record and payload under the lock, with bounded no-follow reads, type/single-link checks, root/policy binding and identity/content checks. It never reads arbitrary legacy payloads, traverses recursively, copies staging data into the ledger, resets budgets or retries a provider. It removes a qualifying payload before its registration. Missing payload does not establish whether publication succeeded and must never redirect deletion to the intended target. Errors or uncertain persistence are not successful cleanup or business receipts; retain the existing Session persistence-failure fence and use the normal exact-operation query/recovery protocol.

**POSIX boundary:** descriptor-relative operations and rechecks do not provide atomic “unlink this inode only” protection against a nonparticipating writer replacing a name between verification and unlink. The explicit trusted-namespace precondition is therefore mandatory; without it or without the exact cleanup grant, no deletion is authorized. Never present these tests as proof of safety against a hostile same-host writer. SIGKILL cannot run `finally`; cleanup happens only on a later explicit safe recovery attempt, and some early/uncertain residues remain.

**Windows boundary:** the new managed write/recovery capability fails closed. The existing Windows reader/lock is not an identity-bound deletion backend. Native managed recovery is not implemented or tested in this Mac-only batch; legacy save and metadata-only `temp-report` remain unchanged. Do not interpret skipped POSIX cases as Windows cleanup acceptance. No current user residue is removed by adopting this version.

## 简体中文

`save()` 只在普通退出时清理本次创建的临时文件。强制退出、SIGKILL 或断电可能跳过 `finally`，留下 `.lifecycle-*`。其中可能是未完成或未提交的账本/模拟 provider 状态，不能当作成功回执或覆盖当前账本。

以上 `temp-report` 命令需要用户/宿主明确选择可信根与政策 SHA256；不需要 Git、模型、服务或新依赖。它先持有既有宿主锁，再核对有界读取的原始 `policy.json`，返回前复核政策；不解析 `state.json`，因此账本损坏或缺失仍可检查。锁文件可能因同步需要被创建，账本、预算、批准不变。

仅检查根目录直属条目的元数据，不跟随链接，不读/hash 残留内容，不递归、删除、重命名、采用旧状态或重放动作。最多检查 1,024 个条目、报告 64 个匹配项；检测上限可能多取得一个迭代器条目。达到上限或条目元数据不可用时为 `complete: false` / UNKNOWN；扫描、政策或锁失败则退出码 2 / UNKNOWN / HOLD。有残留时即使枚举完整仍为 UNKNOWN；完整且为空的 PASS 只表示本次有界观察，不是阶段放行或持久性证明。成功交付报告的退出码 0 不代表报告状态 PASS。

硬链接数不可用时（包括 Windows 目录条目元数据）记录为 `null`，不误称单链接或硬链接。JSON 转义异常文件名码点以保留身份并输出有效 UTF-8。

所有匹配项都标记归属 UNKNOWN、动作 RETAIN。名称、PID、时间和文件内容均不足以证明归属或不活跃；锁只约束遵循该锁的写入者。POSIX 使用目录描述符枚举，Windows 在已检查根及锁内按路径观察元数据，均不保证对非协作写入者的原子快照（`atomic_snapshot: false`）。宿主仍须保护根及其父路径。报告可能含本地文件名/大小，应留在私人环境。

本接口不是预算报告，也不解除保存结果不确定状态。保留当前账本与批准，按既有精确操作的 query/恢复协议确认动作，不能将残留覆盖到账本或据此判定成功。

**默认检查仍不删除。** 历史匿名残留没有可验证归属，继续保留。下述可选接口只适用于新登记资源，不迁移旧文件、不按前缀清扫、不运行守护服务，也不依据 PID 或年龄判定归属。

### 可选受控资源（可信宿主，仅 POSIX）

[受控临时资源接口](../interfaces/lifecycle/owned_temporary.py)与 `temp-report` 分开。宿主必须明确保证所选根及祖先目录受保护，且**所有写入者都遵循同一宿主锁**；这是接入前提，不是 JSON、命令行确认或文件权限检查可以证明的事实。被审记录不能启用能力，有无关写入者的目录不能启用。必须提供可信的规范绝对路径；含符号链接的祖先路径会拒绝，不静默解析别名（例如 macOS 应由宿主选定真实规范路径）。

用上方 Python 示例创建 `ManagedTemporaryNamespace(..., exclusive_namespace=True)`，作为 `Session(..., temporary_namespace=namespace)` 的可信宿主参数，再使用已有 `session.locked()` 执行已经授权的生命周期操作。恢复另行在 `namespace.locked()` 内调用 `selected.recover(owner_record_name, authorize=True)`，只授权一个明确的归属记录；没有删除 CLI、后台任务或全局安装。默认 Session/三参数 `save()` 保持旧路径，不自动登记或迁移历史残留。新受控目标文件名以 ASCII 字母或数字开头，其余仅限 ASCII 字母、数字、点、下划线和连字符；宿主目录仍可含 Unicode。政策、锁及资源保留名不依赖文件系统大小写敏感性判断，并在暂存写入前核对既有政策/锁的真实身份，防止别名覆盖。

受控路径固定 POSIX 目录描述符，核验普通单链接锁文件，从创建到登记、发布及普通清理持续持锁。恢复必须由新进程实际取得锁；存活子进程或其他持有者仍可能阻断。PID 复用、重启时间、文件年龄均不是不活跃证据。宿主还需验证文件系统的锁与 fsync 语义，本地测试不认证所有 POSIX 或网络文件系统。仅新独占创建、写完并 fsync 的 payload 才能取得有界版本化登记，绑定政策摘要、根身份、随机暂存名、目标名、真实文件身份/元数据、长度和摘要。这不是构建来源认证、业务回执，也不能对控制可信根的恶意写入者提供密码学归属保证。登记前退出、登记损坏、不完整、不一致或核验期间变化时，未知残留仍保留。有可信根写权限的代码重建自洽记录无法与真实宿主记录区分，原有信任边界仍适用。

恢复只在锁内对明确记录及其 payload 做有界、不跟随链接的身份/类型/单链接/内容/政策核验；先删除符合条件的 payload，再处理登记。不会读取任意历史 payload、递归、覆盖账本、清预算或重放 provider。暂存不存在也不说明业务成功，绝不能改为删除其目标文件。失败或持久化未知不能报告成功；原 Session 保存失败隔离仍有效，业务结果需按原精确操作查询协议确认。

**POSIX 限制：** dir-fd 操作与重查并不提供“仅删除这个 inode”的原子条件；非协作写入者仍可能在检查与 unlink 之间替换名字。因此排他命名空间是强制宿主前提，不满足或无明确删除授权时不得删除。不能把测试称为抵御恶意同机写入者的证明。SIGKILL 不会执行 finally；只能在后续明确的安全恢复中处理符合条件的资源，早期或不确定残留可能继续保留。

**Windows 限制：** 新受控写入/恢复能力保守拒绝；现有安全读取和锁不等于同句柄删除后端。本 Mac 本地批次未实现或原生验证 Windows 受控恢复，旧 save 与仅元数据报告不变。POSIX 用例跳过不是 Windows 清理验收，采用此版本不自动删除任何用户历史残留。
