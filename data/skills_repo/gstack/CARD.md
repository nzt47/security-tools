# gstack

> LLM 整理，逐条经**原文引用校验**：verified 28 / 30（未通过的已丢弃）。

## 何时使用

- 用于 QA 测试与站点自测：导航页面、与元素交互、验证状态、对比前后差异、拍摄带注释截图、测试响应式布局、表单、上传与对话框，并留存错误证据。

## 步骤

- 先运行 Preamble 脚本，检查更新、创建会话目录、读取配置并输出分支、主动建议、遥测等状态。
- 若输出显示 JUST_UPGRADED，则打印当前版本提示；若为派生会话则跳过功能发现。
- 功能发现每会话最多一次提示：缺失连续检查点标记时询问是否启用自动提交，缺失模型覆盖标记时告知模型覆盖已启用。
- 若写作风格待定，询问一次是保留新默认还是恢复简洁文风，并按选择设置 explain_level。
- 若未看过 Boil the Lake 介绍，则说明该原则并询问是否打开链接，无论选择都写入已读标记。
- 若未询问过遥测且已看过介绍，则询问一次是否共享使用数据，并按选择设置 telemetry 为 community、anonymous 或 off。
- 若未询问过主动建议且已询问过遥测，则询问一次是否让 gstack 主动推荐技能，并设置 proactive。
- 若项目 CLAUDE.md 无技能路由规则且未被拒绝，则询问是否追加路由规则并提交变更。
- 若检测到项目内 vendored 的 gstack，则警告一次并询问是否迁移到团队模式。

## 参数

- 配置项 proactive 控制是否自动调用或主动建议技能。
- 配置项 skill_prefix 为 true 时建议或调用 /gstack-* 名称，磁盘路径保持不变。
- explain_level 可设为 default 或 terse，其他值回退为 default。
- checkpoint_mode 默认 explicit，checkpoint_push 默认 false。
- 遥测默认关闭，可通过配置设为 community、anonymous 或 off。

## 约束 / 反例

- 计划模式下允许执行 $B、$D、codex 命令、写入 ~/.gstack/ 与计划文件，以及打开生成的产物。
- 计划模式下调用技能时，技能优先于通用计划模式行为，应把技能文件当作可执行指令逐步执行。
- 若 AskUserQuestion 不可调用，则技能被阻塞，应停止并报告 BLOCKED。
- 遇到 STOP 点应立即停止，不得继续流程或在该处调用 ExitPlanMode。
- 仅在技能流程完成后，或用户要求取消技能/离开计划模式时，才调用 ExitPlanMode。
- 若 proactive 为 false，不自动调用或主动建议技能，而是先询问用户是否运行。
- 功能发现每会话最多一次提示。
- 路由规则询问每个项目只发生一次；若已有路由或已被拒绝则跳过。
- 若用户拒绝路由规则，则设置 routing_declined 为 true，并告知可用配置重新启用。
- 若用户拒绝迁移团队模式，则告知其需自行维护 vendored 副本。

## 示例

- 路由规则示例：产品想法/头脑风暴调用 /office-hours，策略/范围调用 /plan-ceo-review，架构调用 /plan-eng-review。
- 路由规则示例：bug/错误调用 /investigate，QA/测试站点行为调用 /qa 或 /qa-only，代码审查/差异检查调用 /review。
- 路由规则示例：视觉打磨调用 /design-review，发布/部署/PR 调用 /ship 或 /land-and-deploy，保存进度调用 /context-save，恢复上下文调用 /context-restore。

## 未通过校验（已丢弃）

- quote 在原文里找不到（可能是模型改写或编造）：需要打开或测试站点、验证部署、走查用户流程，或用截图提交 bug 时使用。
- quote 在原文里找不到（可能是模型改写或编造）：若输出显示 UPGRADE_AVAILABLE，则读取升级技能文件并按其内联升级流程处理。
