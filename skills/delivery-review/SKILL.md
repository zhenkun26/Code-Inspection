---
name: delivery-review
description: 按已选模块核对交付身份、有限观察和恢复建议，在明确授权内调用适配；不隐式部署或建立常驻监控。
---

# 可选交付观察与恢复

沿用 [核心契约](../pre-pr-review/references/contract.md)、[预算/复验](../pre-pr-review/references/recheck.md) 和 [合并后设计](../../docs/workflow.md)。先确认整个模块和 flags/channels/observation/recovery 的选用、责任人与授权；未启用不是线上验收，必需能力缺失不能 N_A。

区分 merge/deploy/release/exposure，绑定实际产物、环境、群体、配置和有限窗口。计划不等于授权；结果未知先 query。同产物晋级，重建需新身份和补验；缺/旧/空/NaN/样本不足不通过，不自动延长窗口。pause/off、兼容旧产物回滚与前向修复分别记录，关闭不撤销历史副作用。

需要执行时按需读 [生命周期接口](../../interfaces/lifecycle/README.md)。默认 provider 只读写本地模拟状态；可选[宿主交付接口](../../docs/host-delivery.md)支持政策选定的 deploy/rollback 适配，其他真实动作暂不支持。不能把模拟或接口测试回执当生产生效、健康或恢复成功。真实后端须由可信宿主接入并验证权限、条件更新、持久幂等与实际结果，缺能力报告 BLOCKED/UNKNOWN，不安装平台补齐。

人负责关键风险、验收和授权，Agent 在明确或已有预授权范围内执行。协调者控制任务和预算，不递归组队；前向修复交指定实现者再回到适用审查阶段。交付 [模板](../pre-pr-review/references/record-template.md) 时填实际 stage、观察覆盖与缺口，不自动连接服务、外发、部署、切生产开关或创建持续任务。
