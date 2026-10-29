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

**Automatic cleanup is not implemented.** Existing anonymous residues lack verifiable ownership. A future cleanup protocol must register a new temporary object's identity and intended target, establish writer inactivity under suitable locks, and validate the same object at removal. PID/age checks, prefix sweeping, and stat-then-unlink against a hostile mutable namespace are insufficient. Unregistered/legacy/changed objects must remain untouched. No migration, daemon, recursive cleanup or deletion of existing user files is performed here.

The self-owned tests exercise a real process exit after writing/fsync and before replacement, retained residue, unchanged committed state, ordinary-save controls, missing/corrupt state, pin/lock refusal, bounded enumeration, non-following metadata and native junctions where supported. This does not reproduce actual power-loss durability or authenticate ownership.

## 简体中文

`save()` 只在普通退出时清理本次创建的临时文件。强制退出、SIGKILL 或断电可能跳过 `finally`，留下 `.lifecycle-*`。其中可能是未完成或未提交的账本/模拟 provider 状态，不能当作成功回执或覆盖当前账本。

以上 `temp-report` 命令需要用户/宿主明确选择可信根与政策 SHA256；不需要 Git、模型、服务或新依赖。它先持有既有宿主锁，再核对有界读取的原始 `policy.json`，返回前复核政策；不解析 `state.json`，因此账本损坏或缺失仍可检查。锁文件可能因同步需要被创建，账本、预算、批准不变。

仅检查根目录直属条目的元数据，不跟随链接，不读/hash 残留内容，不递归、删除、重命名、采用旧状态或重放动作。最多检查 1,024 个条目、报告 64 个匹配项；检测上限可能多取得一个迭代器条目。达到上限或条目元数据不可用时为 `complete: false` / UNKNOWN；扫描、政策或锁失败则退出码 2 / UNKNOWN / HOLD。有残留时即使枚举完整仍为 UNKNOWN；完整且为空的 PASS 只表示本次有界观察，不是阶段放行或持久性证明。成功交付报告的退出码 0 不代表报告状态 PASS。

硬链接数不可用时（包括 Windows 目录条目元数据）记录为 `null`，不误称单链接或硬链接。JSON 转义异常文件名码点以保留身份并输出有效 UTF-8。

所有匹配项都标记归属 UNKNOWN、动作 RETAIN。名称、PID、时间和文件内容均不足以证明归属或不活跃；锁只约束遵循该锁的写入者。POSIX 使用目录描述符枚举，Windows 在已检查根及锁内按路径观察元数据，均不保证对非协作写入者的原子快照（`atomic_snapshot: false`）。宿主仍须保护根及其父路径。报告可能含本地文件名/大小，应留在私人环境。

本接口不是预算报告，也不解除保存结果不确定状态。保留当前账本与批准，按既有精确操作的 query/恢复协议确认动作，不能将残留覆盖到账本或据此判定成功。

**本批未实现自动删除。** 历史残留没有可验证归属。未来需要新的对象归属/目标记录、可靠不活跃判定及删除时同一对象核验；PID/年龄、前缀清扫或不可信可变目录里的先 stat 再 unlink 不够。旧记录、未登记或发生变化的对象必须保留。本批没有迁移、守护服务、递归清理或用户残留删除。隔离测试验证真实进程无 finally 退出及各项拒绝边界，不等同真实断电或归属认证。
