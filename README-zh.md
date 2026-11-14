# Code-Inspection

[English](README.md) · **简体中文**

Code-Inspection 是一套给你现有 AI Agent 用的代码审查指令（Claude Code、Codex 或任何能读取本地文件的宿主都可以）。你把一个改动交给 Agent，它会对照你的需求检查这个改动，并告诉你：验证了什么、发现了什么、哪些没法检查，以及这个改动是否可以进入下一步。

无需安装，不限定模型，也不需要新服务。灵感来自 [How the Claude Code team does code review](https://claude.dev/blog/solving-code-review-at-anthropic/)。本项目独立、非官方，与 Anthropic 无隶属关系，也未获其背书。

**人掌方向，Agent 执行。** 值不值得做、架构、可接受的风险和最终验收由你决定；检查、调查和证据整理由 Agent 完成，修复只在你允许的范围内进行。

## 开始使用

克隆仓库，并保持目录结构完整（各 Skill 之间用相对路径互相引用）：

```sh
git clone https://github.com/zhenkun26/Code-Inspection.git
```

然后在你的 Agent 里粘贴下面这段提示词，把方括号部分换成你的内容：

> 读取 `<路径>/Code-Inspection/skills/pre-pr-review/SKILL.md` 并按它执行。
> 审查对象：`[项目路径和改动范围——分支、提交或未提交的改动]`
> 需求：`[这个改动要实现什么]`
> 验收条件：`[怎样算做对了]`
> 允许的动作：只读审查 `[或：可以运行测试 / 可以修复已确认的问题]`
> 预算：`[例如：最多 3 轮审查]`

Agent 默认只读。它会把已暂存、未暂存和未跟踪的改动一起审查，不会只看最后一次提交。Codex 用户也可以直接把本仓库作为项目打开，使用 `code-inspection` 入口，见 [Codex 设置](docs/codex-setup.md)。

## 你会得到什么

一份分五部分的报告，格式见[记录模板](skills/pre-pr-review/references/record-template.md)。下面是示意（不是真实输出）：

```text
1. 审查对象   head 3f2c1e0 + src/auth/ 下未提交的改动；只读并运行测试
2. 实际验证   CHK-1 单元测试     PASS     （日志：...）
              CHK-2 登录流程     NOT_RUN  （没有可用的浏览器）
3. 发现       F-1 登出后 token 未失效——已确认，阻断
4. 缺口       登录流程未验证；未做压力测试
5. 阶段建议   HOLD——先修复 F-1，再运行 CHK-2
```

阶段建议只有三种：`READY_FOR_NEXT`、`HOLD`、`REPLAN`。零发现是合法结果，但仅凭测试全绿或零发现，永远不等于"可以推进"：没验证的部分会一直作为缺口列出。

## 四个阶段

| 阶段 | 适用场景 | 依赖 |
| --- | --- | --- |
| [PR 前](skills/pre-pr-review/SKILL.md) | 开 PR 之前先把改动检查一遍。**从这里开始。** | 只需 Markdown 指令。 |
| [PR 中](skills/pr-in-review/SKILL.md) | 需要在多轮审查中跟踪发现、决定和预算。 | 可选的本地 Python 记录工具。 |
| [合并时](skills/merge-review/SKILL.md) | 分支移动前先检查合并后的结果。 | 可选 Python 工具和一个隔离的 Git 仓库。 |
| [合并后](skills/delivery-review/SKILL.md) | 部署和回滚要以证据为前提。 | 可选 Python 工具；除非宿主接入 provider，否则只有本地模拟。 |

日常使用只需要第一个阶段。后面三个阶段适合需要为每一步留下可审计记录的团队。

## 它不做什么

- 不会自行创建 PR、发评论、合并真实项目或部署。
- 不包含生产部署、指标或回滚集成，不包含托管 CI/SARIF 导入器，也没有身份认证。
- 不是托管的 AI 审查服务，审查质量也还没有测量过：目前没有评测数据集和评分器。
- 本快照尚未验证宿主对 Skill 的自动发现、安装到其他项目以及取消/恢复。

## 试运行离线示范（可选）

需要 Python 3.9+，以及支持 `merge-tree --write-tree` 的 Git。在仓库根目录运行：

```sh
python3 -B examples/lifecycle_demo.py --simulate-approvals --with-delivery
```

它会在仓库外新建一个临时目录，跑完全部四个阶段。内容检查和 Git 合并是真实执行的；审查报告、人工批准和交付是模拟的，并明确标注。总结中的 PASS 包含预期的 HOLD/UNKNOWN 状态，不代表发布批准。见[示范说明](docs/local-walkthrough.md)。

## 了解更多

以下文档使用英文：

- [流程、信任边界与验证范围](docs/workflow.md)
- [共享审查契约：状态、权限与门禁](skills/pre-pr-review/references/contract.md)
- [能力与限制详解](docs/capabilities-and-limits.md)：可选宿主集成、记录完整性、预算、CI 矩阵
- [生命周期 CLI 与记录契约](interfaces/lifecycle/README.md)
- [可选安全结果接口](interfaces/security-tools/README.md)
- [平台支持（Windows）](docs/windows-support.md)与[验证限制](docs/acceptance.md)
- [来源与许可范围](docs/sources.md)

## 目录结构

| 路径 | 用途 |
| --- | --- |
| `skills/` | 四个阶段的指令及共享协议。 |
| `.agents/skills/code-inspection/` | 仓库级 Codex 入口，转到各阶段。 |
| `interfaces/`、`support/` | 可选的本地记录工具和文件/Git 辅助模块（仅用 Python 标准库）。 |
| `examples/`、`validation/` | 离线示范和维护中的检查。 |
| `docs/` | 设置、流程、限制和来源说明。 |

## 贡献与检查

请先阅读[贡献说明](CONTRIBUTING.md)。维护中的检查：

```sh
python3 -B validation/run_checks.py --keep-artifacts
python3 -B validation/mutation_check.py
```

需要在 Git checkout 中运行，并会在仓库外创建临时 Git fixture；Windows 上如果解释器命令是 `python`，请替换 `python3`。修改分发文件后要更新 `SHA256SUMS`（不包括清单本身）。[CI](.github/workflows/ci.yml) 在 Ubuntu（Python 3.9、3.13）和 Windows（Python 3.13）上运行同样的检查，范围见[能力与限制](docs/capabilities-and-limits.md#checks-and-ci)。检查通过说明工具行为符合规格，不代表生产安全，也不代表模型的审查质量。

## 帮助与安全

脱敏的问题和可复现的缺陷请提交到 [Issues](https://github.com/zhenkun26/Code-Inspection/issues)；报告漏洞前请阅读[安全政策](SECURITY.md)。不要在公开处贴出凭据、私人代码、个人路径或 Agent 原始对话。不承诺响应时限或生产支持。

## 许可

作者有权许可的原创项目内容采用 [MIT License](LICENSE)，Copyright (c) 2026 Zhenkun。第三方文章、译文、图片及私人输入不在此列，也不因此改用 MIT。未打包第三方 runtime，见[来源](docs/sources.md)。
