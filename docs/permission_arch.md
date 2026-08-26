# 三层权限架构说明（RBAC + ABAC + 正则黑名单）

> 适用文件：`agent/permission_system.py` · `data/permission_policies.json`
> 单元测试：`tests/unit/test_permission_gateway.py`
> 集成测试：`tests/integration/test_permission_gateway_e2e.py`

## 1. 设计目标

原 `PermissionSystem` 是纯正则黑名单（`DANGEROUS_PATTERNS` / `BLACKLIST` / `SENSITIVE_EXTENSIONS`），适合做兜底，但无法表达"用户 A 可调用 `shell_execute` 但不可调用 `system_format`"这类基于角色/属性的权限控制。本架构在其上加两层：

- **RBAC**（基于角色）：admin / developer / guest，每角色一份工具允许/禁止列表
- **ABAC**（基于属性）：会话来源、时间窗口、IP 段等动态属性
- **正则黑名单**：原 `PermissionSystem`，作为最后兜底

## 2. 不变量（不可破坏）

| # | 不变量 | 说明 |
|---|--------|------|
| 1 | 正则规则集保留 | `DANGEROUS_PATTERNS` / `BLACKLIST` / `SENSITIVE_EXTENSIONS` / `SENSITIVE_DIRS` / `DANGEROUS_KEYWORDS` 全部保留，不删不弱化 |
| 2 | `PermissionResult` 数据结构兼容 | `allowed` / `reason` / `requires_confirmation` / `backup_path` 字段不变 |
| 3 | `RiskLevel` 四级不动 | `hitl.py` 的 `LOW` / `MEDIUM` / `HIGH` / `CRITICAL` 不动 |
| 4 | `dangerous_commands.json` 两级结构不动 | `critical` / `warning` 两级不动 |
| 5 | 拒绝原因对外统一 | RBAC/ABAC 拒绝一律返回 `reason="权限不足"`，不向 LLM 暴露角色名/工具名/规则名/时间窗口 |
| 6 | 降级模式 | 策略文件加载失败 → 跳过 RBAC/ABAC，仅走正则黑名单 |

## 3. 调用流程

```
PermissionGateway.check(tool_name, params, context)
  │
  ├─ 生成 trace_id (uuid4 前 12 位), 贯穿本次调用所有日志
  │
  ├─ [层1] RBAC 角色拦截
  │    policy = self._policies[role.value]
  │    if tool in denied_tools       → 拦截 (reason="权限不足")
  │    elif tool not in allowed_tools → 拦截 (reason="权限不足")
  │    else                           → 通过,进入层2
  │
  ├─ [层2] ABAC 属性校验
  │    for rule in abac_rules:
  │        if rule.tool != tool_name and rule.tool != "*": continue
  │        if "time_outside"      in deny_if and 当前时间不在窗口内 → 拦截
  │        if "session_source_in" in deny_if and 来源命中         → 拦截
  │        if "ip_not_in_cidr"    in deny_if and IP 不在白名单段   → 拦截
  │    全部规则通过 → 进入层3
  │
  └─ [层3] 正则黑名单兜底
       action = tool_name + " " + params.values()
       PermissionSystem.check_action(action)
         1. BLACKLIST          → 直接禁止 (reason="...黑名单...")
         2. DANGEROUS_PATTERNS → 二次确认 (requires_confirmation=True)
         3. SENSITIVE_DIRS     → 二次确认
         4. SENSITIVE_EXTENSIONS → 二次确认
         5. 全部通过           → allowed=True
```

### 3.1 短路语义

三层是**顺序短路**：任意一层拦截即返回，后续层不执行。
- RBAC 拦截 → ABAC 不执行（即使配置了时间窗口规则也不会触发）
- ABAC 拦截 → 正则兜底不执行
- 这是安全设计的直接体现：**先做最便宜的角色判断，再做属性判断，最后做最重的正则匹配**

## 4. 配置文件：`data/permission_policies.json`

```json
{
  "version": 1,
  "default_role": "guest",
  "roles": {
    "admin": {
      "description": "管理员,全部工具可用(ABAC 约束危险操作)",
      "allowed_tools": ["*"],
      "denied_tools": []
    },
    "developer": {
      "description": "开发者",
      "allowed_tools": ["web_search", "file_read", "file_write", "shell_execute", "code_runner"],
      "denied_tools": ["system_format", "system_shutdown"]
    },
    "guest": {
      "description": "访客,仅查询",
      "allowed_tools": ["web_search", "file_read"],
      "denied_tools": []
    }
  },
  "abac_rules": [
    { "name": "off-hours-shell-restriction", "tool": "shell_execute",
      "deny_if": { "time_outside": ["09:00", "18:00"] } },
    { "name": "scheduled-no-write", "tool": "file_write",
      "deny_if": { "session_source_in": ["scheduled"] } },
    { "name": "internal-only-format", "tool": "system_format",
      "deny_if": { "ip_not_in_cidr": ["10.0.0.0/8", "192.168.0.0/16", "172.16.0.0/12"] } },
    { "name": "admin-shutdown-internal-only", "tool": "system_shutdown",
      "deny_if": { "ip_not_in_cidr": ["10.0.0.0/8", "192.168.0.0/16", "172.16.0.0/12"] } }
  ]
}
```

### 4.1 字段说明

#### `roles.<role_name>`
| 字段 | 类型 | 说明 |
|------|------|------|
| `allowed_tools` | `string[]` | 允许调用的工具名列表；`"*"` 表示通配全部 |
| `denied_tools` | `string[]` | 禁止调用的工具名列表；`"*"` 表示全禁（优先级高于 allowed） |
| `description` | `string` | 角色描述（可选） |

**判定优先级**：`denied_tools` > `allowed_tools`。即先查 denied，命中即拦；再查 allowed。

#### `abac_rules[]`
| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | `string` | 规则名（仅出现在 DEBUG 日志，不暴露给 LLM） |
| `tool` | `string` | 作用工具名；`"*"` 表示对所有工具生效 |
| `deny_if` | `object` | 拒绝条件，见下表 |
| `description` | `string` | 规则描述（可选） |

#### `deny_if` 支持的条件
| 条件 | 格式 | 语义 |
|------|------|------|
| `time_outside` | `["HH:MM", "HH:MM"]` | 当前本地时间**不在** `[start, end]` 窗口内 → 拒绝 |
| `session_source_in` | `["src", ...]` | 当前会话来源**命中**列表 → 拒绝（来源值：`cli` / `web` / `api` / `scheduled`） |
| `ip_not_in_cidr` | `["cidr", ...]` | 当前 IP**不在**任一 CIDR 段内 → 拒绝 |

同一规则的多个 `deny_if` 条件**各自独立**判定，任一命中即拒绝。

## 5. 数据类

### `Role` (Enum)
```python
class Role(Enum):
    ADMIN = "admin"
    DEVELOPER = "developer"
    GUEST = "guest"
```

### `Permission` (dataclass)
```python
@dataclass
class Permission:
    tool_name: str
    allowed: bool = True
    requires_confirmation: bool = False
    description: str = ""
```

### `ABACContext` (dataclass)
```python
@dataclass
class ABACContext:
    role: Role = Role.GUEST
    session_source: str = "cli"          # cli | web | api | scheduled
    time_window: Optional[Tuple[str, str]] = None
    ip: Optional[str] = None
```

### `PermissionResult` (dataclass，不变量)
```python
@dataclass
class PermissionResult:
    allowed: bool
    reason: str = ""
    requires_confirmation: bool = False
    backup_path: str = ""
```

## 6. Trace 日志格式（标准 JSON）

每次 `check()` 调用生成一个 `trace_id`（uuid4 前 12 位），贯穿入口、各层、出口汇总日志。所有日志为**单行 JSON**，可直接接入 ELK / Splunk / Loki。

### 6.1 统一字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `ts` | string | ISO 时间戳（ELK 映射为 `@timestamp`） |
| `module` | string | 固定 `"permission_gateway"` |
| `event` | string | 事件类型（见下表） |
| `trace_id` | string | 关联一次 check 的所有日志 |
| `tool` | string | 工具名 |
| `role` | string | 角色名 |
| `source` | string | 会话来源 |
| `ip` | string\|null | 客户端 IP |
| `allowed` | bool | 是否允许 |
| `reason` | string | 拒绝原因 |
| `layer` | string | 决策层（`RBAC` / `ABAC` / `REGEX` / `REGEX_DEGRADED`） |
| `duration_ms` | number | 本次 check 耗时（毫秒） |
| `rule_name` | string | ABAC 命中规则名（仅 DEBUG 日志） |
| `params` | object | 参数快照（单值截断 50 字符） |

### 6.2 事件类型

| event | 级别 | 触发 |
|-------|------|------|
| `policy_loaded` | INFO | 策略加载成功 |
| `policy_load_failed` | WARNING | 策略加载失败 |
| `degraded_mode` | WARNING | 进入降级模式 |
| `check_entry` | INFO | check 入口 |
| `rbac_pass` / `rbac_block` | DEBUG / INFO | 层1 结果 |
| `abac_pass` / `abac_block` | DEBUG / INFO | 层2 结果 |
| `abac_hit_*` | DEBUG | ABAC 命中具体条件 |
| `regex_entry` / `regex_result` | DEBUG | 层3 过程 |
| `decision` | INFO | **出口决策汇总，每次 check 必发一条** |

### 6.3 日志示例

**场景 A：GUEST 被 RBAC 拦截**
```json
{"ts":"2026-07-14T01:22:33.123","module":"permission_gateway","event":"check_entry","trace_id":"abc123def456","tool":"shell_execute","role":"guest","source":"cli","ip":null,"degraded":false,"params":{"cmd":"ls -la"}}
{"ts":"2026-07-14T01:22:33.124","module":"permission_gateway","event":"rbac_block","trace_id":"abc123def456","tool":"shell_execute","role":"guest","allowed":false,"reason":"权限不足"}
{"ts":"2026-07-14T01:22:33.124","module":"permission_gateway","event":"decision","trace_id":"abc123def456","layer":"RBAC","tool":"shell_execute","allowed":false,"requires_confirmation":false,"reason":"权限不足","duration_ms":0.243}
```

**场景 D：ADMIN 执行 `rm -rf /` 被正则兜底拦截**
```json
{"ts":"2026-07-14T01:22:33.130","module":"permission_gateway","event":"check_entry","trace_id":"def456ghi789","tool":"shell_execute","role":"admin","source":"cli","ip":"10.0.0.1","degraded":false,"params":{"cmd":"rm -rf /"}}
{"ts":"2026-07-14T01:22:33.131","module":"permission_gateway","event":"regex_result","trace_id":"def456ghi789","action":"shell_execute rm -rf /","tool":"shell_execute","allowed":false,"requires_confirmation":false,"reason":"操作已被列入黑名单，禁止执行。匹配规则: rm\\s+-rf\\s+/"}
{"ts":"2026-07-14T01:22:33.131","module":"permission_gateway","event":"decision","trace_id":"def456ghi789","layer":"REGEX","tool":"shell_execute","allowed":false,"requires_confirmation":false,"reason":"操作已被列入黑名单，禁止执行。匹配规则: rm\\s+-rf\\s+/","duration_ms":0.339}
```

### 6.4 ELK / Splunk 集成

```bash
# 拉某次调用的完整决策链（按 trace_id 过滤）
grep "abc123def456" /path/to/logs

# ELK: 在 Kibana 中按 trace_id 字段做聚合
# Splunk 查询示例:
#   index=agent module="permission_gateway" event="decision" | stats count by layer

# 统计各层拦截次数
grep "event.*decision" logs | grep -oP '"layer":"\K\w+' | sort | uniq -c

# 找出所有耗时 > 10ms 的 check
grep "event.*decision" logs | grep -oP '"duration_ms":\K[\d.]+' | awk '$1 > 10'
```

## 7. 降级模式

策略文件加载失败（文件不存在 / JSON 解析失败）时：

- `self._degraded = True`
- `check()` 跳过 RBAC/ABAC，直接走正则兜底
- `gw.is_degraded` 属性可查
- 日志输出 `degraded_mode` 事件（WARNING）

**安全语义**：降级 = 放宽 RBAC/ABAC，但**不放宽正则黑名单**。即 LLM 仍不能执行 `rm -rf /` 等黑名单操作，但原本被 RBAC 拦截的 GUEST 调用 `shell_execute` 在降级模式下会通过 RBAC 检查（最终是否允许取决于正则兜底）。

## 8. 使用示例

```python
from agent.permission_system import (
    PermissionGateway, ABACContext, Role,
)

gw = PermissionGateway()  # 默认加载 data/permission_policies.json

# 场景 1: GUEST 查询
ctx = ABACContext(role=Role.GUEST, session_source="cli")
result = gw.check("web_search", {"q": "hello"}, ctx)
assert result.allowed is True

# 场景 2: GUEST 调 shell_execute 被 RBAC 拦截
result = gw.check("shell_execute", {"cmd": "ls"}, ctx)
assert result.allowed is False
assert result.reason == "权限不足"

# 场景 3: DEVELOPER 工作时间调 shell_execute (安全命令)
ctx = ABACContext(role=Role.DEVELOPER, session_source="cli")
result = gw.check("shell_execute", {"cmd": "ls -la"}, ctx)
assert result.allowed is True

# 场景 4: DEVELOPER 执行 rm -rf / 被正则兜底拦截
result = gw.check("shell_execute", {"cmd": "rm -rf /"}, ctx)
assert result.allowed is False
assert "黑名单" in result.reason

# 场景 5: 降级模式
gw_degraded = PermissionGateway(policy_path="/nonexistent.json")
assert gw_degraded.is_degraded is True
result = gw_degraded.check("shell_execute", {"cmd": "rm -rf /"}, ABACContext(role=Role.GUEST))
assert result.allowed is False
```

## 9. 扩展指南

### 9.1 新增角色
1. 在 `Role` enum 加新成员
2. 在 `data/permission_policies.json` 的 `roles` 加策略
3. 无需改代码（策略热加载）

### 9.2 给角色加 ABAC 约束（如 ADMIN 危险操作仅内网 IP）
1. 在 `abac_rules` 加规则，`tool` 指向目标工具
2. 配置 `deny_if.ip_not_in_cidr` 指定内网网段
3. 该角色仍通过 RBAC（allowed_tools=["*"]），但危险操作受 IP 约束

### 9.3 新增 ABAC 条件类型
1. 在 `_ABACRule.deny_if` 文档加新条件
2. 在 `_check_abac` 方法加对应的 `if "xxx" in deny_if:` 分支
3. 加单测覆盖

### 9.4 与 HITL 集成
PermissionGateway 不直接调用 `hitl.py`。集成时：
1. 调 `gw.check()` 拿到 `PermissionResult`
2. 若 `result.requires_confirmation is True`，调 `HITLManager.request_approval()` 走人工确认
3. 若 `result.allowed is False`，直接拒绝，不进入 HITL 流程

## 10. 测试覆盖

| 测试文件 | 覆盖范围 | 测试数 |
|---------|---------|--------|
| `tests/unit/test_permission_system.py` | 原 PermissionSystem 正则黑名单 | 18 |
| `tests/unit/test_permission_edge_cases.py` | 边界场景 + SafetyGuard | 39 |
| `tests/unit/test_permission_gateway.py` | RBAC/ABAC/正则/三层叠加/降级/统一reason/JSON日志 | 43 |
| `tests/integration/test_permission_gateway_e2e.py` | 端到端角色矩阵/时间/IP/会话模拟 | 35 |
| **合计** | | **135** |

### 10.1 关键测试用例

- `TestRBAC::test_guest_blocked_for_shell` — GUEST 被 RBAC 拦截
- `TestABACTimeWindow::test_time_window_outside_blocks` — 时间窗口外拦截
- `TestABACSessionSource::test_scheduled_source_blocked_for_write` — 来源拦截
- `TestABACIP::test_external_ip_blocked_for_format` — 外网 IP 拦截
- `TestRegexFallback::test_blacklist_blocks_rm_rf_root` — 正则兜底
- `TestThreeLayerStack::test_rbac_short_circuits_before_abac` — 短路语义验证（RBAC 拦截后 ABAC 不执行）
- `TestDegradedMode::test_degraded_still_blocks_blacklist` — 降级仍拦黑名单
- `TestUnifiedReason::test_rbac_does_not_leak_role_name` — reason 不泄露角色名
- `TestJSONLogFormat::test_all_logs_are_valid_json` — 日志均为合法 JSON
- `TestJSONLogFormat::test_same_trace_id_throughout_check` — trace_id 贯穿
- `TestAdminABACConstraint::test_admin_external_ip_blocked_for_shutdown` — ADMIN ABAC 约束
- `TestCrossRoleMatrix::test_system_format_access_matrix` — 跨角色对比矩阵
- `TestSessionSimulation::test_developer_typical_session` — 完整会话模拟

## 11. 相关文件

| 文件 | 作用 |
|------|------|
| `agent/permission_system.py` | PermissionSystem（正则兜底）+ PermissionGateway（三层入口 + JSON 日志） |
| `data/permission_policies.json` | RBAC 角色策略 + ABAC 规则配置 |
| `data/dangerous_commands.json` | 正则黑名单关键词库（critical/warning 两级） |
| `agent/human_in_the_loop/hitl.py` | 人工确认流程（RiskLevel 四级） |
| `tests/unit/test_permission_gateway.py` | 三层架构单元测试 |
| `tests/integration/test_permission_gateway_e2e.py` | 端到端集成测试 |
