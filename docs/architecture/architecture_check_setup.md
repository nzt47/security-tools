# 架构规则校验 CI 配置指南

> 本文档说明如何将 `architecture-check` 工作流配置为 GitHub Required Status Check，实现 PR 自动阻断合并。

## 一、工作流概览

### 独立工作流文件

[architecture-check.yml](../../.github/workflows/architecture-check.yml) 是独立的架构规则校验工作流，与 `observability-ci.yml` 解耦，专门用于 PR 架构检查。

### 执行流程

```
PR 提交 → architecture-check 工作流触发
  ├── 步骤 1: 生成模块依赖图（AST 静态分析）
  ├── 步骤 2: 架构规则校验（退出码 1 = 阻断合并）
  ├── 步骤 3: 变更影响分析（不阻断，仅 PR 评论）
  ├── 步骤 4: 生成架构合规性报告
  ├── PR 评论: 架构校验结果 + 变更影响分析
  └── 上传 Artifact: 完整报告
```

### 阻断逻辑

| 步骤 | continue-on-error | 阻断合并 | 说明 |
|------|-------------------|----------|------|
| 生成依赖图 | false | 是 | 解析失败时阻断 |
| 架构规则校验 | false | **是** | 未豁免违规时退出码 1，阻断合并 |
| 变更影响分析 | true | 否 | 仅提供参考，不阻断 |
| 合规性报告 | true | 否 | 报告生成失败不阻断 |

## 二、配置 Required Status Check

### 步骤 1：启用分支保护

1. 进入 GitHub 仓库 → **Settings** → **Branches**
2. 点击 **Add branch protection rule**（或编辑现有规则）
3. Branch name pattern: `main`

### 步骤 2：设置必须通过的状态检查

1. 勾选 **Require status checks to pass before merging**
2. 勾选 **Require branches to be up to date before merging**
3. 在搜索框中输入 `architecture-check`
4. 选择 **`architecture-check / 架构规则校验`** 添加为 required check

### 步骤 3：设置合并规则

1. 勾选 **Require a pull request before merging**
2. 勾选 **Require approvals**（至少 1 个）
3. 勾选 **Restrict who can push to matching branches**（仅允许管理员）

### 步骤 4：验证配置

```bash
# 创建一个测试分支
git checkout -b test/arch-check

# 故意引入一个违规（如 orchestrator 直接 import dao）
# 然后提交并推送
git push origin test/arch-check

# 创建 PR，观察 architecture-check 是否失败
# 修复后观察是否通过
```

## 三、工作流触发条件

### PR 触发

当以下路径的文件变更时触发：
- `agent/**` — 核心代码
- `tests/**` — 测试代码
- `config.yaml` — 架构规则配置
- `docs/architecture/legacy_exemptions.json` — 豁免清单
- `.github/workflows/architecture-check.yml` — 工作流本身

### Push 触发

仅 `main` 分支的 `agent/**`、`config.yaml`、`legacy_exemptions.json` 变更时触发，用于自动更新依赖图文档。

## 四、架构规则说明

### 内置规则（7 条）

| 规则 ID | 描述 | 严重度 | 阻断 |
|---------|------|--------|------|
| `no_orchestrator_to_dao` | 禁止 orchestrator 直接访问 dao | high | 是 |
| `no_cognitive_to_server_routes` | 禁止 cognitive 直接访问 server_routes | high | 是 |
| `no_cognitive_to_dao` | 禁止 cognitive 直接访问 dao | high | 是 |
| `no_tools_to_dao` | 禁止 tools 直接访问 dao | medium | 是 |
| `no_guardrails_to_server_routes` | 禁止 guardrails 直接访问 server_routes | medium | 是 |
| `no_circular_dependency` | 禁止循环依赖 | high | 是 |
| `no_agent_import_tests` | 禁止 agent/ import tests/ | high | 是 |

### 豁免机制

- 存量违规可通过 [legacy_exemptions.json](legacy_exemptions.json) 豁免
- 豁免清单配置指南见 [legacy_exemptions_guide.md](legacy_exemptions_guide.md)
- 循环依赖豁免支持双向匹配（A→B 与 B→A 视为同一循环）

## 五、本地验证

### 模拟 PR 测试流程

```bash
# 完整 CI 模拟（依赖图 + 架构校验 + 影响分析 + PR 评论）
python scripts/simulate_pr_review.py --base HEAD~1 --head HEAD

# 仅架构规则校验
python -m agent.observability.arch_rules --check \
  --root agent \
  --exemptions docs/architecture/legacy_exemptions.json \
  --config config.yaml

# 生成合规性报告
python scripts/generate_arch_compliance_report.py

# 依赖图趋势报告
python scripts/dependency_trend_report.py
```

### 预期结果

当前代码库状态：
- 节点数：213，边数：432
- 违规总数：2（全部已豁免）
- 未豁免违规：**0**（CI 通过）

## 六、与 observability-ci.yml 的关系

| 工作流 | 用途 | 阻断合并 | 触发条件 |
|--------|------|----------|----------|
| `architecture-check.yml` | 架构规则校验（轻量） | **是** | PR + push to main |
| `observability-ci.yml` | 可观测性全面检查（重量） | 是（多 job） | PR + push + schedule |

两个工作流独立运行，`architecture-check` 作为首选 required check，`observability-ci` 作为全面质量保障。

## 七、故障排除

### Q: PR 被阻断，如何修复？

1. 查看 PR 评论中的违规详情
2. 下载 Artifact `architecture-check-report` 获取完整报告
3. 修复违规，或登记豁免（需架构评审）
4. 重新推送代码

### Q: 本地通过但 CI 失败？

- 确认本地 `legacy_exemptions.json` 与 CI 一致
- 确认 `config.yaml` 中的 `arch_rules` 配置已提交
- 检查 CI 日志中的 `trace_id`，与本地对比

### Q: 如何临时跳过检查？

**不推荐**，但可通过在提交消息中添加 `[skip ci]` 跳过 CI：
```
git commit -m "fix: 紧急修复 [skip ci]"
```
注意：这会跳过所有 CI 检查，仅用于紧急情况。
