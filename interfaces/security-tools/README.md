# 可选安全工具结果接口 v1

A 为默认：宿主已有 Agent 按受信需求、风险、代码和证据进行审查，不新增必需运行依赖。B 是本目录的**只读结果接口**，用户可不接，也可自行把已获授权工具的实际输出转换成下述记录。它不安装、登录、启动扫描器、执行命令配置、访问网络或发布评论。C 专用服务未接入。

接口实现使用 Python 3.9+ 标准库，以及 [受控文件读取辅助模块](../../support/local_files.py)。需平台支持 `dir_fd`、`O_NOFOLLOW`、`O_DIRECTORY`；不支持时拒绝，不能假称已安全读取。Git 不是 B 的要求；本项目 Git fixture 验证另需 Git。所有示例均自有合成记录，不是真实扫描结果，也没有检出率证明。

## 接入步骤

1. 用户/宿主指定受信政策及请求文件，记录来源和摘要；不可让被审 PR 自选信任政策。确定所需覆盖、时间窗口、工具版本/配置、源码与工作区/CI/产物身份，以及允许的数据接收方。配置选择不能由工具自行降级。
2. 用户可提交现有工具输出，或宿主在明确权限内执行现有工具。任何执行仍受宿主授权；本接口不执行任意 `command`、hook 或 shell 配置。工具原始退出码保留，不因为某工具以非零表示发现就伪装为零。
3. 手工或自有受信 adapter 转换为 `security-result/v1`，保留原始输出及 SHA-256。转换者必须理解对应工具版本的字段和覆盖；本目录没有声称原生支持任何厂商格式。Gitleaks、Semgrep、OSV 仅是未来可能来源，不是必装依赖。
4. 将必要、已按授权筛选的证据放入专用本地根目录；使用相对路径。只调用下列只读规范化命令；不要先执行来源中的命令。

从项目根目录试读自有示例：

```sh
python3 -B interfaces/security-tools/normalize_result.py --root interfaces/security-tools/examples --request request.json --result result.json
```

省略 `--result` 可真实检查未配置情形。修改/复制示例为真实任务时，重新指定真实政策、时间、身份、工具和证据；不能把示例中的 `fixture-*` 标识当真实项目证据。示例宽时间窗口只方便演示，不是推荐生产保鲜策略；到期应返回 UNKNOWN。

## 请求契约

完整机器可读示例：[request.json](examples/request.json)。全部字段必填，未知字段拒绝。

| 字段 | 含义 |
| --- | --- |
| schema / request_id | `security-request/v1` 和唯一关联 ID |
| policy_binding | 用户/宿主指定政策来源与摘要的可追溯引用；字符串本身不证明可信 |
| required | 本项是否为受信策略要求；不能由扫描结果改变 |
| expected_subject | base、head、worktree_digest、ci_config_digest、artifact_digest；均为实际身份。无对应对象时用带理由的明确标识，不用空值假装相同 |
| expected_tool | name、version，精确匹配 |
| expected_config_digest | 规则/配置的固定身份 |
| required_scope | 必需范围标识，非空、去重；由接入者定义其真实含义，不把路径标签当覆盖证明 |
| accepted_data_destination | 实际允许的接收方或 `none`；需单独授权，不能从“本地 CLI”推断不外发 |
| valid_from_utc / valid_until_utc | 显式带时区窗口；结果观测时间必须落在窗口内且不晚于当前时间 |

## 结果契约

完整示例：[result.json](examples/result.json)、[原始合成证据](examples/raw.txt)。全部顶层字段必填：

- `schema=security-result/v1`、request_id、subject、tool、config_digest、scope、complete。
- `execution={outcome,exit_code}`：outcome 为 completed/not_run/blocked/timeout/error/unknown；exit_code 保留原值或未知时 null。超时可以由宿主记录，本接口没有启动器或超时执行器。
- `findings`：每项 id、location、trigger、impact、evidence_ref、tool_severity。规范化后始终是 candidate/unreviewed，不直接升级为确认漏洞，也不把工具严重度等同本项目风险。
- `raw_evidence`：相对 path 与 sha256，关联本根下可读常规文件；单文件最多 1 MiB。超过限额应报告能力缺口，另行受控保存/选择明确裁剪证据并披露影响，不静默截断。
- `data_destination`，`license={identifier,source,review_state}`，`observed_at_utc`。review_state 只能 reviewed/unknown；许可未知不能假装已核验。这里不替用户选择项目许可证，也不替第三方许可提供法律保证。

subject/tool/config/数据接收方须精确一致；scope 须覆盖 required_scope；complete 必须真实反映工具及 adapter 是否完整。发现引用必须指向已校验原始证据。额外字段、重复 JSON 键、非有限数、越界/绝对路径、根及根内符号链接、非常规文件、坏摘要均拒绝。调用者指定的根的上级路径由宿主负责；本模块不是文件系统沙箱。

## 输出和门禁语义

返回 `security-evidence/v1`，保留完整 `request_binding`（含政策、required、范围、配置和时间窗口）、`config_digest` 及实际解析的请求/结果字节路径与 SHA-256 `input_records`，便于独立保存后回溯。绑定引用本身仍不证明来源可信。始终 `authorizes_progress=false`。CLI 退出 0 只表示产生了规范记录，不代表通过；退出 2 为不安全/无效/不可读输入，输出 UNKNOWN。消费端必须读取 status/reasons，不能把退出 0 当绿灯。

| 输入 | 规范化状态 |
| --- | --- |
| 未配置，required=true / false | BLOCKED / NOT_RUN；A 仍能继续已授权工作 |
| 明确未跑 / 被阻且其他身份可靠 | NOT_RUN / BLOCKED |
| 错误、超时、未知、非零退出、过期、身份/版本/配置不符、覆盖不足、不完整、许可未知 | UNKNOWN，并列原因；不伪装无发现 |
| 有候选发现 | UNKNOWN，待核验；候选不是确认漏洞 |
| 同身份、配置、版本、完整覆盖、有效原始证据、退出 0 且无候选 | PASS，仅该工具声明范围证据，不是全项目安全或阶段放行 |

若未跑/被阻记录同时身份错误，优先 UNKNOWN，因为无法可靠关联该任务。确认违反验收的发现由协调者依事实给 FAIL；N_A 由受信策略和真实适用性决定，接口不自动产生 FAIL/N_A。任何必需覆盖缺失仍阻断阶段建议，不能因为 B 可选就把必需安全能力变可选。

## 数据与分发边界

哈希能检出内容变化，不能证明工具真的运行、报告来源可靠或工具有效。此接口校验自报版本/外发字段，不能监控真实网络、认证扫描者或阻止伪造。日志内容仍是不受信数据；先按授权筛选和脱敏，再交给模型。代码未设置硬沙箱、硬预算或托管服务。

发布本可选接口仅需本目录实现/说明/自有示例及 `support/local_files.py`。私人 reference 原文/图片/HTML、上传稿、原始任务日志和历史验证证据不默认分发；用户现已选择对有权许可的原创项目内容采用 [MIT](../../LICENSE)，不以依赖的许可证代替项目授权；第三方材料边界见 [许可盘点](../../docs/sources.md)。示例的 `self-owned-test-data` 只是材料身份标签，不是新授予的许可证。

有界测试位于 [test_interface.py](../../validation/security-integration/test_interface.py)，验证接口一致性和拒绝边界；不代表真实扫描器效果、外发限制或 GUI 验收。
