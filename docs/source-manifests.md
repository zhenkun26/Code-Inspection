# Source manifests / 源输入清单

## English

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

POSIX uses descriptor-relative no-follow traversal. Windows supports CPython 3.13 on Windows Server 2025 x64 with a local fixed NTFS volume and Git for Windows, within [the native boundary](windows-support.md). Directory enumeration and leaf reads use held native handles; unsupported namespaces/filesystems and unavailable safety operations fail closed. Native tests must actually collect directory and HEAD/index/worktree inputs; skipped POSIX-specific cases are not native collection evidence.

Stored `posix-permissions/v1` proofs keep their original semantics. New Windows proofs use `windows-readonly/v1`: mode `0` or `1` records absence/presence of FILE_ATTRIBUTE_READONLY for files and directories. This is not an ACL, effective-write-access or executable-bit assertion. HEAD/index retain Git modes `100644`/`100755`; an index-only executable change changes identity without fabricating a working-file executable bit. Changing read-only attributes also changes full identity. The validator is platform-independent and never rewrites a historical mode model or upgrades an approval. Windows Git capture requires explicit `core.filemode=false`; a missing/true setting is unsupported. Raw working bytes, including CRLF, are compared as bytes: no implicit line-ending normalization makes a changed checkout look committed.

Supported paths have strict UTF-8 spelling and portable relative components. Entries are sorted deterministically; ambiguous case/Unicode aliases are rejected rather than silently normalized. Included symlinks/reparse points, hardlinks, special files, traversal escapes, unsupported index states and submodules are refused. Git mode also refuses LFS pointer content instead of claiming the external object was captured; directory mode describes literal file bytes only and never resolves LFS objects. Ordinary Git checkouts require local supported objects and a validated metadata layout; linked worktrees, alternate object stores, partial/sparse/split-index configurations and missing objects are outside this version. No network fetch, checkout, filter, hook or index write is performed.

Collection caps are 1,024 selected file/directory entries, 1 MiB per file/blob, 8 MiB aggregate logical bytes (all HEAD/index/worktree projections count), depth 32, 4,096 UTF-8 path bytes, 4,096 visited entries per enumeration, and a 1 MiB canonical manifest. Git commands have bounded stdout/stderr, a per-command deadline and a bounded capture group. Policy can lower the configurable file/count/byte caps. Errors, caps, observed concurrent changes and unsupported input produce no complete proof. Held descriptors, repeated observations and metadata checks detect observed races but do not prove a directory-wide atomic instant or defeat a malicious trusted writer. The result is scoped consistency under the declared host isolation, not a sandbox, hard memory quota or authenticated provenance.

Directory **artifacts** are not accepted by the current single-file deployment consumer. See [artifact content](artifact-content.md) for its distinct identity and immutable-byte consumption. Do not replace an artifact digest with a source manifest and claim a deployment was verified.

## 简体中文

本接口补充有界、可复算的**源输入身份**，不是构建来源认证、全机采集、目录部署或“测试确实使用了这些输入”的证明。沿用既有五字段 subject、证据、预算、批准及恢复规则。

`directory` 记录声明范围内的普通文件、目录（包括空目录）、平台声明的模式投影、长度和原始字节 SHA-256。`git-worktree` 分别记录 HEAD、stage-0 index 和实际工作文件；空侧表示新增/删除，重命名表示删除加新增。同一 HEAD 下，暂存、未暂存、删除、纳入范围的 untracked 和可执行模式变化都不再被 HEAD 掩盖。CRLF 原样保留，不运行 Git 内容过滤器。

清单包含模式、范围、字面排除规则、限制、模式模型及确定性排序的条目，身份写入 `subject.worktree_digest` 的 `source-manifest-sha256:` 前缀。Git 模式还绑定完整 base/head 及 HEAD tree。绝对源路径、时间戳和 inode 不作为内容身份；源根由已固定摘要的宿主政策选择。完整仅针对声明范围：ignored 文件不能自动当作无关或安全，须明确排除或先解决覆盖缺口，不能偷偷读取其内容。排除参与构建/检查的输入必须如实说明；`.git` 不是源 payload。清单文件名本身也可能敏感，不默认公开。

上述 `source_content` JSON 只是配置示例。宿主选择根、scope、exclusions、上限和 required，并保护路径父级、Git 可执行程序和元数据对象存储。优先使用受控冻结副本；账本锁不冻结源树，也不能凭字符串声明证明隔离。scope/exclusions 是按 UTF-8 字节排序且不重复的字面相对路径前缀，不执行任意命令或 glob。directory 模式在枚举前拒绝根及父级路径的 .git 目录、文件或链接，防止绕过 Git 忽略路径检查；非 Git 目录不存在隐式 Git 忽略语义，所需排除必须由宿主明确提供。

捕获后，将精确 manifest 身份和 `source-content/v1` sidecar 一起用于初始化/advance；sidecar 关联当前 subject 摘要，不能产生循环摘要或自动补出批准。捕获不是审查轮次。后续新动作重新捕获；内容、index、模式、范围、排除或关联变化使旧证据不再适用，应显式 advance、按影响复验及重新批准。必需证明缺失、范围不支持、读取/稳定性失败为 UNKNOWN/HOLD；不是产品缺陷 FAIL，也不清零预算。未选择可选能力的旧流程保持，提供了证明就必须校验。

以上 `source-capture` 命令可在 init 前运行。宿主先在可信根保存 `source-capture/v1` 请求及真实五字段 subject，命令只派生 worktree_digest，返回建议 subject 和 sidecar；其余字段仍为宿主声明。它不写账本/批准/政策、不开始轮次或收费，但同步锁可能被创建。PASS 仅指本次支持范围内的捕获完成，构建来源仍 UNKNOWN、动作授权仍 false。将输出的两项一起放入原有 handoff 或 subject-change，再执行 init/advance；输出必须在源范围之外，避免清单包含自身。示例的大写值是待替换的输入说明，不是可用默认身份。

新的源绑定合并计划为 `merge-proposal/v3`，完整 sidecar 参与计划 ID。现有合并适配器只组合提交对象：目录模式或带暂存/未暂存/相关 untracked 字节或 Git 模式差异的源不能冒充待合并提交内容，须先解决不匹配；仍可进行本地脏工作树的只读审查。提交等价要求 HEAD/index 模式一致及选中文件字节一致；POSIX 还比较实际 owner-executable 位。Windows 明确要求 core.filemode=false，不从扩展名或只读属性推断可执行权限；空目录和其他 POSIX 权限位并不会因此进入 Git tree；这些属性仍参与完整源身份，变化使旧证据失效，相关行为应由集成检查核验。合法历史 pending/receipt 的查询和幂等恢复不重新读取当前源、不重复收费或执行；旧标签不自动升级，旧缺失绑定的回执也不因此放行。账本 1 MiB 上限不变，不能删历史容纳清单。

POSIX 使用目录描述符/no-follow 遍历；Windows 支持范围为 Windows Server 2025 x64、CPython 3.13、Git for Windows、本地固定 NTFS 卷及[原生平台边界](windows-support.md)。枚举和读取使用持有的原生句柄；不支持的命名空间/文件系统或安全能力不可用时拒绝。Windows 测试须真实采集目录和 HEAD/index/worktree，跳过 POSIX 专属测试不算 Windows 采集通过。

旧 posix-permissions/v1 证明保持原语义。新 windows-readonly/v1 的 mode 为 0/1，表示文件/目录只读属性的缺席/存在，不代表 ACL、有效写权限或执行位。HEAD/index 保留 Git 的 100644/100755；仅改 index 执行位会改变身份，而不伪造工作文件执行位。只读属性变化也改变完整身份。纯校验不依赖当前平台，不改写旧证明或升级批准。Windows Git 采集要求明确 core.filemode=false；缺失或 true 为不支持。CRLF 等工作文件字节原样比较，不能借隐式换行转换把不同字节视作已提交。包含链接/reparse、硬链接、特殊类型、路径逃逸、歧义大小写/Unicode、非法编码、不支持的 index/submodule、linked worktree 或对象布局时拒绝。Git 模式还拒绝 LFS 指针，不能冒称已捕获外部对象；目录模式只描述字面文件字节，不解析 LFS 对象。枚举、文件/字节/深度/路径/输出及命令时间均有界，失败或截断不生成完整证明。没有网络、checkout、过滤器、hook 或 index 写入。

重复观察和元数据检查不能证明跨文件原子快照，也不能抵御控制受信宿主的恶意写入者。目录产物与源目录不同：现有交付仍只消费受控的单文件快照，本清单不能替代其 artifact 身份或构建来源认证。实际限制与本批测试结果须按所运行平台分别阅读。
