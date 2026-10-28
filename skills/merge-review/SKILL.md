---
name: merge-review
description: 核验 PR 交接和实际组合，在独立授权下使用受控合并适配；不默认合并用户仓库。
---

# 合并组合与条件落地

复用 [核心契约](../pre-pr-review/references/contract.md)、[预算/复验](../pre-pr-review/references/recheck.md) 和 [合并时设计](../../docs/workflow.md)。接收有效 PR 证据、发现处置、人工批准和已用预算，不把阶段切换当重置。

先 prepare 固定当前 base、候选、组合与产物，再取得其集成验证。preflight、合并授权、执行和 receipt 分开；READY 不授权动作。没有可靠条件更新能力则 HOLD，不能检查一次后普通 merge 冒充原子机制。不同 SHA/相同源码都不能自动证明产物等价。

执行时按需读 [生命周期接口](../../interfaces/lifecycle/README.md)。当前 LocalGit 只接受显式标记的隔离裸仓库和 merge_commit，不用于任意用户仓库。squash/rebase 或真实平台未接入时明确报缺口。结果不确定先 query，不盲重试或绕过拒绝。

协调者控制审阅与预算，不递归组队；冲突解决新增逻辑交指定实现者，按影响复审。人保留必要风险与合并决策，Agent 只在已有授权内执行。交付 [模板](../pre-pr-review/references/record-template.md) 时填实际 stage、组合/证据、授权和真实结果，不自动部署或发布。
