# 存量豁免清单配置指南

> 本文档说明 `docs/architecture/legacy_exemptions.json` 的字段含义与配置方法，帮助你手动登记新的存量豁免。

## 一、文件结构

```json
{
  "description": "架构规则校验存量豁免清单",
  "purpose": "对存量代码中已知的架构违规进行豁免...",
  "created_at": "2026-06-26",
  "last_updated": "2026-06-26",
  "exemptions": [
    {
      "rule_id": "no_circular_dependency",
      "source": "agent.cognitive.critic",
      "target": "agent.graceful_degrade",
      "reason": "存量循环依赖：...",
      "added_at": "2026-06-26",
      "owner": "architecture-team",
      "tech_debt_ticket": "ARCH-DEBT-001",
      "mitigation": "graceful_degrade.py:464 使用函数内延迟 import..."
    }
  ],
  "guidelines": [
    "豁免清单仅用于存量代码，不得用于掩盖新增违规",
    "每项豁免必须包含 reason、owner、tech_debt_ticket",
    "豁免项应定期评审，推动技术债务清零",
    "新增违规若需豁免，必须经架构评审委员会批准并登记 ticket",
    "循环依赖豁免支持双向匹配：A→B 与 B→A 视为同一循环，无需重复登记"
  ]
}
```

## 二、字段说明

### 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `description` | string | 是 | 文件描述 |
| `purpose` | string | 是 | 豁免目的说明 |
| `created_at` | string | 是 | 文件创建日期（YYYY-MM-DD） |
| `last_updated` | string | 是 | 最后更新日期（每次修改需更新） |
| `exemptions` | array | 是 | 豁免项列表 |
| `guidelines` | array | 否 | 使用规范说明 |

### 豁免项字段（exemptions[]）

| 字段 | 类型 | 必填 | 说明 | 示例 |
|------|------|------|------|------|
| `rule_id` | string | 是 | 规则 ID，必须与 `BUILTIN_RULES` 中的 ID 一致 | `no_circular_dependency` |
| `source` | string | 是 | 违规源模块（点路径） | `agent.cognitive.critic` |
| `target` | string | 是 | 违规目标模块（点路径） | `agent.graceful_degrade` |
| `reason` | string | 是 | 豁免原因（说明为何不修复） | `存量循环依赖：critic 依赖 graceful_degrade...` |
| `added_at` | string | 是 | 登记日期（YYYY-MM-DD） | `2026-06-26` |
| `owner` | string | 是 | 负责人/团队 | `architecture-team` |
| `tech_debt_ticket` | string | 是 | 技术债务工单号 | `ARCH-DEBT-001` |
| `mitigation` | string | 是 | 缓解措施（已采取的临时方案） | `使用函数内延迟 import 打破加载时循环` |

## 三、可用规则 ID

当前内置 7 条规则，可在豁免清单中使用：

| 规则 ID | 说明 | 严重度 |
|---------|------|--------|
| `no_orchestrator_to_dao` | 禁止 orchestrator 直接访问 dao 层 | high |
| `no_cognitive_to_server_routes` | 禁止 cognitive 直接访问 server_routes | high |
| `no_cognitive_to_dao` | 禁止 cognitive 直接访问 dao 层 | high |
| `no_tools_to_dao` | 禁止 tools 直接访问 dao 层 | medium |
| `no_guardrails_to_server_routes` | 禁止 guardrails 直接访问 server_routes | medium |
| `no_circular_dependency` | 禁止循环依赖（A→B→A） | high |
| `no_agent_import_tests` | 禁止 agent/ 下模块直接 import tests/ | high |

## 四、添加新豁免的步骤

### 步骤 1：确认违规是存量而非新增

```bash
# 运行架构规则校验，查看违规清单
python -m agent.observability.arch_rules --check --root agent \
  --exemptions docs/architecture/legacy_exemptions.json \
  --config config.yaml \
  --json-report docs/architecture/arch_rules_report.json
```

查看 `arch_rules_report.json` 中的 `violations` 数组，确认 `is_exempted: false` 的违规项。

### 步骤 2：在豁免清单中添加新项

编辑 `docs/architecture/legacy_exemptions.json`，在 `exemptions` 数组中追加：

```json
{
  "rule_id": "no_circular_dependency",
  "source": "agent.error_handler",
  "target": "agent.monitoring.error_reporter",
  "reason": "存量循环依赖：error_handler 在异常上报时延迟 import error_reporter...",
  "added_at": "2026-06-26",
  "owner": "architecture-team",
  "tech_debt_ticket": "ARCH-DEBT-002",
  "mitigation": "双向均使用函数内延迟 import 打破模块加载时循环..."
}
```

### 步骤 3：更新 last_updated

将顶层 `last_updated` 字段更新为当天日期。

### 步骤 4：重新运行校验确认豁免生效

```bash
python -m agent.observability.arch_rules --check --root agent \
  --exemptions docs/architecture/legacy_exemptions.json \
  --config config.yaml \
  --json-report docs/architecture/arch_rules_report.json
```

确认报告中该违规项的 `is_exempted` 变为 `true`，且 `active_violations` 减少。

## 五、循环依赖豁免的特殊说明

循环依赖规则（`no_circular_dependency`）的豁免支持**双向匹配**：

- 登记豁免 `A → B` 后，自动覆盖反向 `B → A`
- 无需重复登记两个方向

这是因为循环依赖的本质是 A↔B 的双向关系，无论 DFS 从哪一端开始检测，都应被视为同一问题。

**示例：** 假设存在循环 `agent.foo ↔ agent.bar`：
- 登记豁免 `source: agent.foo, target: agent.bar` 即可
- 即使校验器检测到 `agent.bar → agent.foo` 也会自动命中豁免

## 六、当前已登记豁免清单

| Ticket | 规则 | 源 → 目标 | 缓解措施 |
|--------|------|-----------|---------|
| ARCH-DEBT-001 | no_circular_dependency | `agent.cognitive.critic` ↔ `agent.graceful_degrade` | graceful_degrade.py:464 延迟 import |
| ARCH-DEBT-002 | no_circular_dependency | `agent.error_handler` ↔ `agent.monitoring.error_reporter` | 双向延迟 import |

## 七、注意事项

1. **不得用于掩盖新增违规**：豁免清单仅用于存量代码。若新代码引入违规，应直接修复而非登记豁免。
2. **必须登记 ticket**：每项豁免必须有对应的技术债务工单号，便于追踪和定期评审。
3. **定期评审**：建议每季度评审一次豁免清单，推动技术债务清零。
4. **架构评审**：新增豁免需经架构评审委员会批准，不得个人擅自添加。
5. **JSON 格式校验**：修改后建议用 `python -c "import json; json.load(open('docs/architecture/legacy_exemptions.json', encoding='utf-8'))"` 验证 JSON 格式正确。
