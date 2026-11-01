# Code-Inspection

[English](README.md) · **简体中文**

通过现有Agent宿主运行、不绑定模型、基于证据的代码审查Skill。灵感来自 [How the Claude Code team does code review](https://claude.dev/blog/solving-code-review-at-anthropic/) 及日常审查经验。本项目独立、非官方，与Anthropic无隶属关系，也未获其背书。

## Human steer, agent execute

人负责需求是否值得做、架构、可接受风险和验收；Agent承担大量实现、检查、调查、证据整理及获授权的修复复验。本项目主要负责代码审查，动作权限和必要真人决定保持明确。

## 开始使用

取得完整仓库，然后将其作为 Agent 的项目目录打开：

```sh
git clone https://github.com/zhenkun26/Code-Inspection.git
cd Code-Inspection
```

请保留完整目录结构，只复制发现入口或单个Skill文件夹会破坏相对引用。本仓库从去隐私化文件快照建立独立历史，不包含私人开发历史、PR讨论或审计记录。

让可读取本地文件的现有Agent宿主执行：

> 读取 `skills/pre-pr-review/SKILL.md`，对照原始需求与验收条件，审查我指定的项目及本地改动。先只读，仅使用已授予的验证/修复权限。报告实际版本、证据、发现、未解决缺口和下一阶段建议。

提供目标、原始需求、验收条件、受信政策、允许动作和任务预算。范围包含相关暂存、未暂存和未跟踪改动。无需全局安装Skill。Codex的仓库级 `code-inspection` 入口按需转到已有阶段Skill，见 [设置与显式加载兜底](docs/codex-setup.md)。此前的发现与路由反馈属于历史资料，不证明这个副本已验收；本快照的宿主发现/加载、安装到其他项目及取消/恢复均未验证。详细协议与接口说明统一使用英文，仅本 README 保留中文。

## 已实现的范围

| 阶段 | 已有能力 |
| --- | --- |
| [PR前](skills/pre-pr-review/SKILL.md) | 指令Skill与五份共享协议；宿主支持时使用真实运行验证及独立审查。 |
| [PR中](skills/pr-in-review/SKILL.md) | 本地记录执行层，处理版本证据、发现处置、连续预算和宿主提供的真人决定记录，可导入安全结果。 |
| [合并时](skills/merge-review/SKILL.md) | 真实受控裸Git候选、集成证据、expected-old条件更新和回执查询，仅显式隔离仓库及merge commit。 |
| [合并后](skills/delivery-review/SKILL.md) | 可选本地模拟，以及受信宿主的 deploy/rollback 接口；不内置生产 provider。 |

未包含原生托管CI/SARIF结果导入器、身份认证或生产部署/开关/指标/回滚provider，不自动创建PR、评论、合并真实项目或部署。未实现评测数据集及评分器。

可选[宿主交付接口](docs/host-delivery.md)接受受信 Python 宿主按政策提供的 deploy/rollback provider。缺能力保持 UNKNOWN/HOLD，默认 CLI 不安装 provider；新外部动作还必须取得政策选定的实际持久化检查点确认；普通保存或重载成功不证明掉电持久性。条件更新、身份认证、存储保证和持久幂等仍需使用者实际接入及验收。其他真实交付动作暂不支持。

可选[构建来源核验契约](docs/build-provenance.md)接受受信 Python 宿主按政策提供的核验器，将结果绑定到精确来源、组合树、配置、产物、运行及计划。项目不内置外部 CI 身份或签名核验器；必需能力未接入时，新动作保持 UNKNOWN/HOLD，历史恢复不代表重新认证。

## 运行离线示范

在仓库根目录运行：

```sh
python3 -B examples/lifecycle_demo.py --simulate-approvals --with-delivery
```

命令在仓库外保留新目录及命令/证据。微型内容检查和隔离Git合并真实执行，审查报告、人员决定及交付明确为合成。总结PASS包含预期HOLD/UNKNOWN，不代表发布批准。省略 `--with-delivery` 则该阶段NOT_RUN。

- [示范、最小配置和排查](docs/local-walkthrough.md)
- [当前流程、信任边界与验证范围](docs/workflow.md)
- [生命周期CLI与记录契约](interfaces/lifecycle/README.md)
- [可选安全结果接口](interfaces/security-tools/README.md)
- [支持与验收证据](docs/acceptance.md)
- [来源与许可范围](docs/sources.md)

显式的[临时状态检查](docs/temporary-state.md)在宿主锁内报告强制终止留下的残留，不读取其内容，也不删除文件。这是恢复辅助信息，不是待定动作成功的证据。可选可信宿主 POSIX 接口仅在所有写入者协作的排他命名空间内恢复明确登记的新资源；默认报告和未知历史文件不授权删除。Windows 受控清理尚不支持。

## 前提与检查

默认A为Markdown加现有Agent宿主，无新增必需第三方运行依赖。宿主/模型可能收费，或向获授权provider发送数据；低依赖不保证离线。可选B使用Python3.9+标准库及POSIX不跟随符号链接读取/锁，或 [平台支持](docs/windows-support.md) 中的原生Windows后端，隔离Git操作需要支持 `merge-tree --write-tree` 的Git。辅助程序仅可运行受信自有fixture。

```sh
python3 -B validation/run_checks.py --keep-artifacts
python3 -B validation/mutation_check.py
```

统一检查器需要Git checkout；两个命令均可能初始化受信自有的临时Git fixture，仅在该本地执行范围获授权后运行。统一检查覆盖仓库路由入口、四个阶段Skill、两种Python模式和分发卫生；回执及fixture留在仓库外。Windows上若解释器命令为 `python`，请用它替换 `python3`。按平台选择的固定保护条件移除是定向回归，不代表全面变异覆盖。测试不证明生产安全、模型审查质量或扫描器效果。

较大的生命周期套件在每种 Python 模式下分为两个互不重复、覆盖完整的组，保留每条命令 180 秒上限。超时作为失败保存部分输出，剩余检查保持 NOT_RUN；这不证明所有后代进程都已终止。见 [验证限制](docs/acceptance.md)。

分发检查会先拒绝不安全选集，再复用有界、不跟随链接的读取层。测试 fixture 统一遵循外部目录和 KEEP 策略。[验证契约](docs/acceptance.md#distribution-read-boundaries) 说明文件、数量、聚合上限及剩余信任边界。

附带的 [CI定义](.github/workflows/ci.yml) 配置了main推送、PR及手动触发，矩阵为Ubuntu24.04的Python3.9和3.13，加Windows2025x64的Python3.13。每项检查普通与优化模式，含两种完整示范、结构、安全边界、链接及精确摘要覆盖；Python3.13另跑固定保护条件移除检查。工作流仅有仓库只读权限，官方Actions固定SHA，无仓库秘密或部署步骤。请到 [Actions](https://github.com/zhenkun26/Code-Inspection/actions/workflows/ci.yml) 核对精确版本，其他仓库的历史运行不能转作当前证据；CI绿色不等于生产验收；其他runner组合不在该矩阵；Windows支持以原生安全用例完成为条件，不改系统权限或静默跳过。主动修改文件后，更新覆盖全部选定文件（除自身）的 `SHA256SUMS`。

CI 总时限为 Ubuntu 15 分钟、Windows 50 分钟，依据实际阶段耗时设置；单条维护检查和单个变异测试子进程仍分别限时 180 秒与 90 秒。详见 [Windows CI 时间预算依据](docs/windows-support.md)。

宿主保护政策、权限和账本目录；被审内容及日志不能扩权。JSON人名及摘要不是认证或沙箱。必需证据缺失、过时或不完整时保留缺口；READY_FOR_NEXT不授予合并或发布权限。见 [共享核心契约](skills/pre-pr-review/references/contract.md)。

存储的合并/交付计划与回执在执行或恢复前核对精确意图、实际对象和状态关系。不安全的旧记录需要明确重新规划或迁移，见 [完整性与恢复兼容性](interfaces/lifecycle/README.md#plan-integrity-and-recovery-compatibility)。这些检查不能防御同时控制受信政策和批准存储的恶意写入者。

检查结果保留可选失败，并与阻断判定分开。N_A 要求当前、限定范围的宿主适用性记录；仅有旧理由时记 UNKNOWN。CLI 预算报告区分既有账本容量、本次调用观察到的工作量和未知外部费用。容量不是授权，预算耗尽后安全回执查询仍可用。见 [记录与计量契约](interfaces/lifecycle/README.md)。

可选的[单文件产物绑定](docs/artifact-content.md)核对实际字节，并将来源、Git tree 与配置关联纳入新计划。必需动作由受信政策选择；旧标签仍是未核验记录。交付消费同一次校验得到的有界内存快照，后端可为本地模拟或显式注入的受信宿主适配器。构建来源与写入隔离属于宿主声明，不是身份认证。

可选的[源输入清单](docs/source-manifests.md)将声明范围内的目录输入，或分离的 HEAD/index/实际工作树投影绑定到审查身份。采集器支持 POSIX 和[平台说明](docs/windows-support.md)限定的 Windows NTFS 配置；原生采集须通过对应版本的 Windows 检查。Windows 只读属性与 Git index 的执行模式分别记录。范围、排除和上限均参与身份。它不是目录原子快照、目录部署消费接口或构建来源认证，也不能把脏工作树悄悄当作已提交的合并候选。

新收费尝试会将版本化 started 记录与计数增量同次保存；started 不表示动作成功。账本保存失败时持久性为 unknown，该 Session 在核验并重新加载前停止后续动作。旧消耗保留且不虚构历史明细，已接受操作的恢复查询仍不重复收费。

## 验证与项目结构

验证复用维护中的本地/CI入口，包括普通与优化Python、隔离Git示范、文件安全及分发检查。结果绑定实际受测版本，见 [验证范围](docs/acceptance.md) 和具体Actions运行。独立AI审阅属于开发过程，**不是已安装的自动AI审核服务**；模拟批准/交付不证明真实平台验收。

| 路径 | 用途 |
| --- | --- |
| `.agents/skills/code-inspection/` | 仓库级 Codex 入口。 |
| `skills/` | 四阶段指令及共享证据/权限协议。 |
| `interfaces/`、`support/` | 可选本地记录执行层及平台文件/Git 辅助模块。 |
| `examples/`、`validation/` | 临时隔离示范与维护中的检查。 |
| `docs/` | 设置、流程、限制及来源归属。 |

## 帮助、贡献与安全

脱敏问题和可复现的非敏感缺陷可提交到 [Issues](https://github.com/zhenkun26/Code-Inspection/issues)。不假定私密报告渠道已配置。提出改动前请读 [贡献说明](CONTRIBUTING.md)，报告漏洞前请读 [安全政策](SECURITY.md)。不要将凭据、私人代码、个人路径或宿主原始对话放入公开Issue或PR。这里不承诺响应时限或生产支持。

## 许可

对作者有权许可的原创项目内容采用 [MIT License](LICENSE)，Copyright (c) 2026 Zhenkun。第三方文章、译文、图片及私人输入排除在外，不由此改用MIT。未打包第三方runtime；方法参考及今后实质复制的注意事项见 [来源](docs/sources.md)。
