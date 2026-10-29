# 生命周期执行适配 v1

这是可调用的本地参考实现，不是 GitHub/生产平台客户端。默认 A 仍是宿主 Agent 的方法层；可选 B 现在包括原安全记录校验器和本目录的确定性执行适配。使用 Python 标准库及 POSIX 安全文件/锁能力，或 [限定的原生 Windows 后端](../../docs/windows-support.md)；本地合并另需支持 `merge-tree --write-tree` 的已有 Git。没有安装器、网络调用、任意 shell 配置、自动登录、评论发布或后台任务。协议沿用 [核心契约](../../skills/pre-pr-review/references/contract.md)、[复验/预算](../../skills/pre-pr-review/references/recheck.md) 和各阶段设计，不新增另一套放行状态。

## 信任与运行边界

- `--host-root` 是用户/宿主指定并保护的目录，保存 policy.json、authority.json、state.json 和宿主核验记录。`--policy-sha` 必须从用户/宿主可信渠道取得，不能从被审 PR 的命令里接受。目录内容/进程权限，以及 PATH、HOME 与实际 Git 可执行程序由宿主保护。Git 子进程保留 HOME 以支持宿主包装器，但仍固定禁用全局/系统 Git 配置与用户 attributes、不继承 GIT_DIR/GIT_CONFIG_COUNT 等注入变量；托管裸仓库的元数据同样必须受保护，CAS只保证目标ref条件更新，不防任意并发篡改Git目录布局；哈希、JSON 的 actor 字符串不是认证，也不是沙箱。
- `--input-root` 仅承载不受信工具结果与原始证据。ingest/observe/import-security 从这里读；init/adjudicate/advance/merge-prepare/delivery-plan 从 host-root 读指定记录。工具导入不含人类审批字段，不能写 host authority。宿主先筛选数据和秘密，日志内指令不执行。
- authority.json 是有权人员决定的**宿主侧转录**：只承认政策列出的真人、精确 action/binding/policy、未过期且未撤销的记录。导入文件不能证明真人存在；接入真实平台必须由可信宿主核验身份/资格后记录，不能让 Agent 自签。按同一 decision id 写入后续 revoke，最新同 ID 记录生效。宿主须串行管理权限更新；这里不提供远端即时撤权或密码学签名服务。
- session 文件锁只协调遵守此接口的本地进程，不能阻止有文件权限的人改账本。受控目录不应让目标项目或并行 reviewer 写入。若缺这种隔离，不能把程序当强制门禁。ledger 有 1 MiB 限额，达到时停止，不能删账本清零；迁移/归档需保留历史并由宿主明确处理。
- 命令退出 0 只表示得到结果。读取 recommendation/checks/reasons 或真实 receipt；READY_FOR_NEXT 始终不授予动作权限。错误退出 2 且 UNKNOWN/HOLD，明确预算错误为 REPLAN。操作异常不等于产品缺陷。轮次、操作账本和本次调用观察值的精确范围见下文，不宣称覆盖外部模型的硬预算。

## 模块与宿主接口

| 模块 | 已实现职责 | 宿主必须提供/仍未提供 |
| --- | --- | --- |
| core.py / Session | 固定政策、连续账本、有限计数、时间/身份、真人决定映射、原子文件替换 | 真实授权与政策可信性、秘密筛选、隔离权限、外部调用计数 |
| review.py | 证据与候选导入、核验记录、稳定 ID/原始观察、去重关联/争议、版本失效、四道逻辑门禁 | 原始需求/代码语义核验、CI 实际取得与来源真实性；不自动调用模型或测试工具 |
| merge.py / LocalGit | prepare/preflight/execute/query；隔离裸 Git 候选、集成证据、expected-old 原子 ref 更新、实际 ref 回执 | 只支持显式隔离标记的本地 SHA-1 bare repo、merge_commit；不支持任意用户仓库、远端、squash/rebase/队列供应商 |
| delivery.py / LocalSimulation | plan/read/apply/query、可选能力、有限观察、幂等请求与回执；pause/off/rollback/forward-fix 分开 | 仅本地模拟状态；无真实部署、传播延迟、指标服务、恢复或生产健康证明 |
| cli.py | 明确命令到上述能力的入口 | 不加载任意配置指定的 Python 类/插件/命令 |

真实连接器可在可信宿主中实现相同职责：只读取得身份/检查/人工决定；prepare 固定组合；preflight 核对有效证据；execute 按单独授权和可靠条件更新；query 查询真实结果。交付 provider 的 read/apply/query 必须分别返回实际身份、幂等关联和确认/失败/未知结果。不要直接替换函数名就假称获得原子性/权限或生产验收；目前 CLI 只选择本地两个后端。没有能力就报告 BLOCKED/UNKNOWN，不能退回普通 merge 或猜测成功。

## 计划完整性与恢复兼容性

合并计划在预检、执行及查询时核对完整规范摘要、政策指定的仓库/目标/方法、来源与组合身份，以及实际 Git commit/tree 和有序父提交。新执行还复算组合树并重查当前证据与批准，使用同一已验证目标做 expected-old 更新和结果读取。全零 OID、引用名冒充 commit、错误目标或不一致对象在写入前拒绝。哈希用于一致性检查，不能认证能同时改写受信政策、批准和账本的人。

合并回执使用 `merge-receipt/v2`，绑定 operation、policy、repository、target_ref、base 和 candidate。查询先核对计划/回执关联；已知失败不能仅因当前 ref 恰好相同就归因为该次成功。合法 pending/unknown 尝试可通过实际目标读数协调丢失的响应，重复执行不会再次写入。历史查询不要求当前批准仍有效、当前主体不变或剩余执行预算；它不授予新动作，也不证明后来仍保持同一状态。

交付输入仍为 `delivery-plan/v1`；无内容证明的存储计划为 `delivery-proposal/v2`，带内容证明的新计划为 `delivery-proposal/v3`。`provider_snapshot` 保存非递归 provider 状态及 operations 的摘要，`expected_provider` 是该快照的规范摘要。动作、参数、来源、目标及前置状态都进入计划 ID。新请求/回执分别使用 `delivery-request/v2`、`delivery-receipt/v2`，关联精确计划和结果状态；执行重查当前能力与回滚数据兼容性。回执确认的是该次本地模拟动作，不能据历史回执宣称当前产物、目标或群体仍未变化。

早期缺少上述完整性绑定的旧回执、旧交付计划及不一致记录明确拒绝；不会自动补字段、重算 ID 沿用旧批准或删除账本。保留旧记录，由宿主核对真实状态后在明确授权下重新规划/迁移。需要新动作的计划须重新取得精确批准；已有合法 v2 尝试的恢复查询与幂等性保留。检查适用性与预算报告见下文。新增可选[单文件内容绑定](../../docs/artifact-content.md)：受信政策可要求新动作核验实际 SHA-256/长度；来源与构建配置关联仍是宿主声明，不是构建认证。内容计划使用 merge-proposal/v2 和 delivery-proposal/v3，旧标签/合法已尝试操作保持原样可查询，不自动升级或重计预算。

## 最小配置与记录

### 预算账本与调用观察

CLI 成功结果和运行错误结果附加 `budget_report`（`lifecycle-budget-report/v1`），原有 budget、状态和建议不变。参数解析错误/帮助不创建报告；直接 Python 函数调用不自动生成 CLI 报告。`budget_report` 和顶层 `authorizes_action` 属于响应说明，不属于规范计划；不要将完整 CLI 响应回写为账本计划。报告有三个不同范围：

- `ledger.scope=host_lifecycle_ledger` 保留跨阶段继承的 operations/rounds，不改收费点或重置。`operation_attempts` 的单位为 started_lifecycle_operation；begin-review 同时计一次操作和一轮，ingest/import-security、adjudicate、advance、merge-prepare、首次 merge-execute、delivery-plan、首次 delivery-execute、observe 在各自既有记账点计操作。开始记账后的失败也保留消耗；前置拒绝可能尚未到记账点。init 仅继承，状态/导出/查询、独立 preflight 和已有合法操作恢复不新增操作。
- `observed_work.scope=current_cli_invocation` 观察本次单调时钟墙钟秒数、Git 子进程尝试、仓库扫描尝试及实际遍历条目、merge-tree 写树尝试。尝试在执行前记录，错误/超时和部分扫描也保留；执行内嵌预检共用同一观察器，不再额外收费。预检可能写 Git 对象，因此“账本未增加”不等于无工作或文件系统完全只读。零是本次确实未观察到该项，不代表历史或外部工作为零。
- `external.cost` 与 `external.runtime_seconds` 均为 `{status: "unknown", value: null}`，不推算模型费用或宿主外部总耗时。观察值不写账本、不形成新硬限制，也不声称限制整个文件系统扫描的资源消耗。

每种账本单位给出 used/limit/remaining/exhausted；`capacity.next_operation` 与 `next_review_round` 只说明现有计数是否容纳下一个相应记账，不能代替某条命令的政策、证据、批准或状态前提。轮次恰达上限仍可处理同轮的其他已允许操作，但不能新开一轮；超过任一适用上限不得开展对应新工作。有效最后一轮的全绿结果可以仍是 READY_FOR_NEXT，不能据此绕过后续执行预算。所有报告 `authorizes_action=false`。

只有政策 pin、任务和已读账本绑定成立且计数有效才报告 known；缺失/非法历史计数不补零，显示 unknown/null。每次新记账前，operations 与 rounds 都必须是精确的非负整数；布尔值、负数、非整数或缺字段时拒绝并保留原账本，不通过算术把坏数据转换成正常计数。超过限额的合法整数保留实际 used，remaining 截为零。保存失败后，内存值仅标为 session_snapshot、durability=unknown，不宣称已经持久化；须先核查实际存储与回执，不凭容量重试。既有合法已尝试操作仍能在预算耗尽或计数未知时查询/幂等恢复，恢复不授予新动作。没有自动账本迁移或历史观察数据回填。

完整可运行配置由自有 [fixture.py](../../validation/lifecycle/fixture.py) 在全新目录生成；里面的用户、预算、指标与时间都是**合成例子，不是已批准项目默认值**。真实使用需用户/宿主按需求填完整 policy；没有空配置自动放行。

policy.json (`lifecycle-policy/v1`)：task_id、risk、checks、budget、humans、reviewers、high_expert_count、low_auto、allowed_actions、merge、delivery。checks 每项 id/stage(pr 或 merge)/gate(ci、semantic、security、integration)/scope/config/required/na_reason，可附加下述有版本的 applicability；三个 PR 门禁与集成必需要求必须明确。budget 是 max_rounds/max_operations；handoff 带已消耗值。风险来自用户确认的影响，禁止由工具自行降级；样例数字不覆盖已定试点上限或宿主更严限制。

### 检查适用性与可选结果

普通适用检查继续使用 `na_reason: ""` 且不附 applicability。不适用必须由可信宿主在被固定摘要的 policy 中提供如下记录；示例中的占位摘要不能直接用于运行：

```json
{
  "na_reason": "",
  "applicability": {
    "schema": "check-applicability/v1",
    "reason": "本检查为何不适用",
    "basis": "宿主已核查的规则或材料引用及适用范围依据",
    "decider": "宿主核验并转录的判定者",
    "source_subject_digest": "由 core.digest 对当前源 subject 计算的摘要",
    "scope": ["与该 check.scope 精确一致的范围"]
  }
}
```

理由、依据和判定者不能留空或仅空白；scope 不能重复且必须与检查范围一致。check 的 ID、stage、config、task 和 policy pin 由其所属政策绑定。输出保留完整适用性记录及政策关联。decider 字符串不认证真人，basis 文本不自动证明事实；宿主必须先核查实际依据，不能从工具日志接纳自授豁免。工具返回 N_A 本身不能产生豁免，也不能代替高风险专家的有效 PASS、必要人工决定或阻断发现的处置。

适用性绑定源 subject，advance 后旧声明失效。合并预检仅在候选与来源关系已验证后传入明确的源身份；声明覆盖的是该来源在指定检查范围内派生的组合，不声称另行证明候选特有的适用性，也不把尚未生成的候选 SHA 写进政策造成循环绑定。

旧 `na_reason` 非空（包括纯空白）仍可加载，但不能继续豁免：相应检查记 UNKNOWN，要求宿主明确重核。缺字段、格式错误、范围/来源不符以及旧理由与新记录冲突也不产生 N_A。不会替旧数据编造依据、改 pin、清零预算或沿用旧批准；合法历史回执的只读查询与幂等恢复仍可用。重核后改变政策必须保留旧账本与连续用量，并按既有 pin/批准规则显式处理迁移；本接口不提供自动迁移器。交付能力的显式 opt-out 仍按交付政策处理。

PR/集成结果展示所有已配置检查，逐项给出 required 和 blocking。可选检查的 FAIL/UNKNOWN 仍可见，但不自动阻断；必需失败仍阻断。由任何检查发现的未解决正式阻断问题独立生效，不能靠可选标签关闭。阶段建议和执行授权继续分开。

初始 `pre-pr-handoff/v1`：task_id、五字段 subject（base/head/worktree_digest/ci_config_digest/artifact_digest）、budget(rounds/operations)、requirements_ref、history_ref、findings。宿主核对 PR 前原始证据后转录，不能只导入一个 PASS；历史 findings 按候选继续核验，不默认为已经解决。state 初始化只允许一次，不会在阶段切换时重置预算。

可选 `source_content` 政策和 handoff/advance sidecar 将 [源目录/index/工作树清单](../../docs/source-manifests.md) 绑定到既有 `worktree_digest`，不增加第六个 subject 字段。旧标签不自动升级；新动作按政策重新核对源，合法历史回执恢复保持无新采集/收费。支持范围、配置和非原子快照限制以该文档为准。

`review-batch/v1` 包含 evidence 和 findings。证据字段见 [生成器 evidence 函数](../../validation/lifecycle/fixture.py)：精确 subject/config/scope/status/complete、observed_at/expires_at、provider/run/job/attempt/tool_version/context_id/reviewer、raw.path/sha256。原始文件受 1 MiB/no-follow 限制，摘要在导入时核验并保存；该接口不持续监控外部原始文件，也不提供不可变存储，宿主须归档对应字节供后续审计；partial、过期、错对象或覆盖不足不转 PASS。同 check 按 provider/job/reviewer/context 分组，按受信宿主核验的 observed_at 选择最新观察，不按导入顺序。run/attempt 为不透明标识，不用于排序；同一时刻的实质观察冲突记 UNKNOWN，需后续观察解除；任一必需检查的当前失败/未知仍阻断，不能用另一视角 PASS 覆盖；可选检查的结果保持可见。所有旧失败留存，高风险人数也按最新有效尝试核对。增量依赖实际身份与范围，不按评论时间；跨身份复用需要宿主核查关系后导入新的证明记录，保留原出处，不直接改旧 PASS 的 subject。

候选字段：id/subject/location/trigger/impact/severity/confidence_basis/attribution/attribution_basis/source/evidence_refs。无位置用明确“无法定位”并解释来源，不静默丢掉 off-diff 发现。候选默认阻断直至宿主核验；`finding-decision/v1` 由受信 reviewer 在 host-root 提供 actor/subject/finding/fact/disposition/blocking/reason/evidence_refs/duplicate_of。关闭需当前完整核验证据；只说暂缓/有意/接受风险不能关闭，本参考实现尚不提供豁免控制。去重保留两份观察/决策及关联，不按票数；新观察可重开，缺席不自动解决。程序检查核验记录关联，不证明其语义真实。

`subject-change/v1`：subject、impact、reused_evidence（当前直接重标记列表必须为空）。按影响核对后可重新导入证明，而非自动重跑所有检查；旧证据保留但不直接适用于新身份；缺少可靠影响证明时，已解决和已反驳的非阻断处置均重新待核。所有绑定版本的批准重新核对。高风险要求政策指定的多名 reviewer 与不同 context 的有效报告，再加真人决定；字符串本身不能保证上下文独立。

authority.json (`host-authority/v1`) 包含 decisions，每项 id/actor/kind/action/binding/policy_sha/decision/expires_at。PR/合并批准的 binding 是对应 subject 的规范 JSON SHA-256；merge-prepare 的 binding 是 repository/base/head/policy_sha 对象摘要；merge-execute 是已存 plan.id；交付动作是 delivery plan.id。规范摘要由 core.digest 生成，示例 grant 函数**仅测试模拟真人记录，不能用于伪造真实批准**。允许动作明细见 fixture policy；命令本身不是授权。

原安全 B 接入：`security-import/v1` 包含 check、record（输入根下既有 security-evidence/v1 JSON）。命令 import-security 从同一次读取的字节解析并计算摘要，关联其 request_binding/subject/config/scope/raw，不把 authorizes_progress 当批准；候选继续核验，缺绑定拒绝。没有原生 SARIF/CI 解析器。

### 尝试明细与保存故障

账本仍为 `lifecycle-state/v1`。新收费点在既有 `events` 中增加 `attempt-started`，detail 使用 `lifecycle-attempt/v1`：唯一 `attempt_id`、收费后的 operations 序号 `sequence`、固定 `operation` 标签及 `round_delta`，事件 `at` 记录时间。这条记录与 operations/rounds 增量在同一次原子文件替换中保存，不新增收费点。固定标签不包含原始命令参数、路径或证据正文。后续业务事件关联同一 `attempt_id`；pending 保存时另有 `merge-requested` / `delivery-requested` 事件关联精确计划 ID，回执和请求原有 schema/批准绑定不变。started 只证明开始记账，不证明处理完成或外部动作成功。

`budget_report.current_attempt` 仅说明本次调用的新尝试及其 durability；只读查询、旧账本或未开始新收费时为 null，不能解释成历史消耗为零。旧累计数保持不变，不补造旧明细，不根据新明细反推或重置计数。直接 API 的 `Session.charge(rounds)` 仅接受精确整数 0 或 1；负数、bool、float、其他增量及不受控标签在修改内存/磁盘前拒绝。CLI 原来就使用固定增量，这是底层 API 防御，不是已证实的远程预算绕过。

Session 的任意账本保存异常都会保留 unknown durability 并锁止该会话的后续保存、收费及合并/交付执行和结果查询。不能在捕获异常后把内存 pending 当成已落盘意图；也不能盲目回滚内存并假定磁盘没变，因为文件替换可能已完成才报错。须退出当前锁区间，再成功进入 `locked()` 核验政策并重新读取磁盘。正常会话的预算耗尽不影响原有安全查询路径；这个保存故障锁止条件不等于预算限制。

恢复依据实际持久化边界：只有 started 而无 pending，表示尚未接受执行意图，重新开展操作仍按原收费点形成另一笔尝试；已有合法 pending/回执时仅查询协调，不再次执行或收费。动作完成而响应/最终账本保存丢失时，仍按原有真实 ref/provider 状态和严格绑定恢复，不把新明细当授权。若某个已知动作结果自身未能写入，重启后不能凭明细重建这个丢失的事实；匹配状态不证明具体写入者。既有已持久化失败回执继续按原策略拒绝确认，旧缺绑定回执也不会自动升级。1 MiB 限额仍有效，增长到限时显式停止，由宿主保留历史后处理，不删除事件腾额度。外部费用/token/总耗时不在本次明细范围，仍未知。

## 可执行入口

在项目根目录：

```sh
python3 -B interfaces/lifecycle/cli.py --help
python3 -B validation/lifecycle/fixture.py /tmp/my-new-lifecycle-fixture --trusted-fixture
```

第二条只接受不存在的目录，创建自有文件、无 remote 的裸 Git 和本地测试提交。输出 host/inputs/policy_sha；用实际输出值调用，而非猜测路径：

```text
python3 -B interfaces/lifecycle/cli.py --host-root HOST --policy-sha SHA --input-root INPUTS --record handoff.json init
```

上行 HOST/SHA/INPUTS 是需要替换的说明参数。后续按顺序选择命令，不必每次重跑全部：

1. begin-review 在新轮开始前记账；ingest 导入工具/Agent 的证据；adjudicate 读取宿主核验意见；pr-status 判断进入集成的建议；export 输出带历史的交接（publication 始终 NOT_RUN）。
2. merge-prepare 读取 `merge-plan/v1`（base/head/integration_artifact），需 prepare 授权；得到 candidate/组合 subject。导入该组合集成证据、取得精确人工批准与执行授权，再 merge-preflight/merge-execute；merge-query 先查不确定或重复请求。CAS 失败必须重新准备/核验，不自动重试或换工具。完整 source_subject（不只 base/head）变化使计划失效；拒绝 commondir、alternates、符号路径和 symbolic target，更新使用 --no-deref。工具错误与真实冲突分开，Git 操作 10 秒超时，未确认结果先查询。
3. delivery-plan 读取 `delivery-plan/v1`（action/parameters），只生成计划；在可信 authority 记录精确授权后 delivery-execute --operation ID，delivery-query 查询同 ID。deploy 要已确认合并回执和相同验证产物；promote 和启用 flag 均要求当前产物与本任务已验证合并组合、同目标部署回执相关联，promote 另需有效观察。来源写入计划 ID，计划及执行时均复验；无关初始产物不能借用当前 PR 的通过结论。旧计划/部署记录若缺 source 字段不能自动继承，须重新规划并部署；已完成动作仍可查询。推进动作的计划和执行均重核当前阻断/PR 条件，已有动作授权不能豁免新发现；flag-set 初始群体由政策定义，扩大须观察；pause 仅暂停自动化，不减曝光；rollback 要当前 data_version 的已确认兼容映射；forward-fix 只创建修复交接回执，不声称代码已修复/部署。

交付参数：deploy/rollback={artifact}；promote={artifact,channel}；flag-set={enabled,cohort}；pause={}；forward-fix={finding_ids,handoff_ref}。模拟 provider 只能读写 host-root/sim-provider.json，不接生产。整个 delivery 及各 capabilities 分别 opt-in；required 不允许禁用。整个模块未启用返回 NOT_RUN/HOLD，不是线上验收。

observe 从 input-root 读 `observation/v1`：artifact/target/cohort/config/window_start/window_end/source/query/samples/value/complete/observed_at。政策 observation 明确 duration_seconds/min_samples/max_value/max_age_seconds/source/query，窗口锚定当前 provider effective_at 到该时间加 duration_seconds；不能使用早于配置生效的窗口。来源、样本和有限阈值须匹配；不足、错误目标、过期、NaN 不通过。不无限等待；到窗结束仍不足交 owner/重规划。delivery-status 只解释模拟结果；不出具线上健康保证。

## 验证与分发

```sh
python3 -B validation/lifecycle/test_lifecycle.py
python3 -O -B validation/lifecycle/test_lifecycle.py
```

测试只有自有记录、真实隔离裸 Git、模拟交付三类，不是真实 CI/模型/生产 provider 验收。测试产生的结果位于仓库外的新临时目录，不再覆盖 validation/lifecycle/results/ 的历史文件。设置 CODE_INSPECTION_KEEP_TEST_ARTIFACTS=1 可保留测试目录；统一运行入口 python3 -B validation/run_checks.py --keep-artifacts 会输出日志目录；不会更新本项目 Git refs、创建提交或设置远端。发布需显式选择四个阶段 Skill 入口及仓库路由入口、本接口模块/说明、共享 support、它们引用的核心契约和设计文档；测试源另选，不包含 host-root、真实结果/权限/数据或私人 reference。详细实际结果和限制见 [实施验收](../../docs/workflow.md)。

## 强制终止后的临时文件

`temp-report` 提供持锁、有界、仅元数据的残留检查，不依赖账本可解析。它不删除、恢复或重放任何记录，也不判断预算或批准有效性。所有历史残留归属均为 UNKNOWN，保留待宿主处理。具体命令、上限和后续清理前提见 [临时状态检查](../../docs/temporary-state.md)。
