# 任务与结果记录模板

按实际任务填入以下字段；这是字段模板，不是已批准配置。未知写明原因，不用空值假装不适用；不把尚未执行的预期复制到实际。可用 Markdown 表格或结构化数据承载，无需数据库。状态及门禁只在 [核心契约](contract.md) 定义。

## 任务、计划与分派

- task_id / scope / stage=pre_pr；需求原文位置、验收项、非目标。
- identity：base、head/等价快照、工作区内容摘要/清单、策略、产物/环境/数据状态；获取方法及可复查引用。
- authorization：来源、review/verify/repair 及允许动作/路径/环境、失效条件。
- requirements：ID、风险、必需或建议及依据、对应检查/旅程/视角、判据、缺口、依赖、责任人。
- assignments：ID、范围/问题、事实包引用、判据、证据要求、非目标、允许验证、返回形式；协调者管理数量/模型与共享预算，不允许递归分派。
- budget：采用的轮次上限及依据、其他已确认限额或未知、计量单位与范围、外层调用及批内子项、已用次数/资源、停止原因。

## 每项证据

`id / requirement / subject / artifact / environment / data_state / specification / tool_or_model_version / config / time / steps / expected / actual / raw_status / status / output_refs / coverage / limitations / freshness`

原始输出放受控位置，记录路径与必要摘要；证据从其他人提供时注明来源及是否亲自复核。历史证据保留，失效记录关联到新版本，不能覆盖成永远通过。

## 每项发现与处置

`id / subject / location / trigger / impact / severity_reason / confidence_basis / evidence_or_reproduction / confirmed_or_question / introduced_or_pre_existing_or_uncertain / blocking_reason / proposed_recheck / disposition / rebuttals / original_view_reports`

原始分视角报告与归并记录同时可追溯；零发现也列实际覆盖和未读/未验证部分。无法解开的关键异议注明责任人与待补事实，不靠票数消除。

## 最终给用户的报告

1. **对象与授权**：审查哪个版本、哪些路径/需求，执行了哪些模式。
2. **实际验证与审查**：检查 ID、状态、证据引用、覆盖/局限；运行与独立上下文情况如实注明。
3. **发现和反证**：统一 ID、确认/疑点、影响、处置及分歧；修复和复验证据，新旧版本关系。
4. **未完成**：关键配置、检查、环境或领域缺口，预算/停止原因和责任人。
5. **阶段建议**：READY_FOR_NEXT/HOLD/REPLAN 及依据；下一步需要什么，不暗示已创建 PR、合并或发布。

报告必须能被用户独立理解；小任务可以精简呈现，不能隐去阻断、未知或真实失败。该模板不是固定角色表或命令集合。

## 安全与跨阶段交接补充

- policy_binding：用户/宿主指定者、来源路径、内容摘要、指定时间、适用范围；与被审规则的差异及独立批准记录。
- data_boundary：可读/可写路径、测试数据、允许网络与模型接收方、进入模型前筛选能力、脱敏和未覆盖边界。
- budget_ledger：统一任务 ID、阶段、轮次定义、累计轮次与调用/时间/费用（已配置者）、本阶段消耗、剩余额度及停止原因；进入下一阶段不清零。跨阶段如何分配仍待项目策略确定。
- handoff：需求/身份/产物/策略、有效与失效证据及原因、正式发现和异议、尚缺能力/真人决策、下一阶段适用要求。交接不授予 PR 评论、合并或发布权限。
