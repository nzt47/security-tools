# 云枢策略库（策略即代码 / v7.2 §5.6 · §3.11）

> 本目录是**策略即代码**的落点：策略是配置（入库跟踪、走 PR 评审），不是代码。

## 一、目录内容

| 路径 | 性质 | 是否入库 |
|---|---|---|
| `policies.json` | **策略库**（规范形态，含 `schema: policy.v1`） | ✅ 入库跟踪（人工合入） |
| `README.md` | 本文件 | ✅ |
| `decisions.jsonl` | 决策日志（P7.2-19 模拟器的重放数据源） | ❌ 运行时产物（已 gitignore） |
| `inbox.jsonl` | 例外收件箱本地账（§5.6 只收例外） | ❌ 运行时产物（已 gitignore） |
| `policy_signing_key.pem` / `.pub.pem` | 签名密钥（首次签名时生成） | ❌ 运行时产物（已 gitignore） |

## 二、策略 schema（§3.11 九字段，不可增删）

```json
{ "id": "sec.example",
  "version": "1.0.0",
  "owner": "security",
  "effect": "allow|deny|ask",
  "match": { ... OPA 子集 ... },
  "message_template": "用户文案 {capability_id}",
  "effective_range": { "not_before": "", "not_after": "", "tenants": [], "scopes": [] },
  "break_glass_ttl_min": null,
  "signature": "ed25519:..." }
```

- `match` 的支持子集与**明确不支持**的语法见
  [`agent/policy/MATCH_SUBSET.md`](../../agent/policy/MATCH_SUBSET.md)。
  **`match` 里出现 `http.send` 一类网络/执行 token 会被装载期直接拒绝**（P7.1-20）。
- `effective_range` 与 `break_glass_ttl_min` 的结构是云枢裁定（设计文档未闭合），
  见 `agent/policy/models.py` 模块 docstring 的裁定 #2/#3。

## 三、判定顺序（**首个命中生效**）

1. **内置不变量**（`agent/policy/store.py::builtin_policies`）永远排在最前，**不可被
   本文件遮蔽**。当前只有一条：`builtin.invariant.secret-egress-deny`
   —— `data_class=secret` 且目标外部 ⇒ deny（§2.5 / §3.2 契约级不变量）。
   它的位置是刻意的：否则本文件里一条宽泛的 `allow` 就能把它静默关掉。
2. 本文件中的策略，**按书写顺序自上而下**，第一个命中的决定结果。

> **因此：`deny` 写在前面，宽泛的 `allow` 写在后面。**
> `PolicyStore.shadow_report()` 会把「allow 排在 deny 之前」的组合列出来，模拟器
> 报告（P7.2-19）也会带着这份诊断——它是「首个命中生效」这一语义的已知代价。

## 四、当前已装载的策略与**它们今天是否会改变行为**

| id | effect | 命中条件 | 对现状的影响 |
|---|---|---|---|
| `sec.opaque-destructive-deny` | deny | `origin.opaque` ∧ `risk_level=destructive` | **今日不生效**：S0-02 摸底显示存量资产 `risk_level` 全为 `None`（未评估）。S1-02 回填后开始生效——这正是设计意图。 |
| `gov.confidential-external-ask` | ask | `data_class=confidential` ∧ 目标外部 | **今日不生效**：存量 `data_class` 同为 `None`。回填后转为「要人确认」并进收件箱。 |
| `ops.freeze-external-egress` | deny | `attributes.freeze_window=true` ∧ 目标外部 | **默认不生效**：`freeze_window` 由调用方显式声明（变更冻结期/演练期），缺省即无该属性。 |
| `builtin.invariant.secret-egress-deny` | deny | `data_class=secret` ∧ 目标外部 | 仅当能力被**显式分级为 secret** 时生效。 |

**零行为回归的依据**：以上四条都要求输入里存在 `secret`/`confidential`/`destructive`
分级或显式 `freeze_window` 属性；存量未分级输入（`data_class=None` / `risk_level=None`）
在四条上都不命中，引擎返回 `matched=false`，执行点回落既有判定（RBAC/ABAC/正则）。

## 五、变更流程（P7.2-19 合入门禁）

```powershell
# 1) 写候选策略（单独文件，不要直接改 policies.json）
#    2) 对历史决策重放
python -m agent.policy.simulator --candidate data/policies/candidates/my-new-policy.json `
    --since 7d --out reports/policy_simulation.md --json-out reports/policy_simulation.json
# 3) 门禁检查（PR 必附报告；高危变更需逐条确认）
python scripts/check_policy_change_gate.py --changed data/policies/policies.json `
    --report reports/policy_simulation.json --pr-body pr_body.md
```

CI 侧由 `.github/workflows/policy-change-gate.yml` 在 PR 触及策略文件时强制执行。

## 六、签名（可选，默认不强求）

```powershell
$env:CP_POLICY_SIGNING_KEY = "data/policies/policy_signing_key.pem"   # 首次自动生成
python scripts/check_policy_change_gate.py --sign data/policies/policies.json
```

- 签名方案 `ed25519:<hex>`；`cryptography` 不可用或无私钥时降级为
  `sha256-self:<hex>` 占位（**非真签名**，与 S2-02 审计链同款降级口径）。
- 强制签名：`CP_POLICY_REQUIRE_SIGNATURE=1`（此时未签名/降级签名的策略会被拒收）。
- 手写策略默认允许不签名——单机部署下「没配密钥就装不上策略」会退化成
  **无策略**，那是安全降级而不是安全增强。

## 七、环境变量

| 变量 | 默认 | 含义 |
|---|---|---|
| `CP_POLICY_FILE` | `data/policies/policies.json` | 策略库路径（亦可指向目录，读取其中全部 `*.json`） |
| `CP_POLICY_BUILTIN_INVARIANTS` | `1` | 是否装载内置不变量（`0` 仅用于测试/演示） |
| `CP_POLICY_REQUIRE_SIGNATURE` | `0` | 是否强制签名 |
| `CP_POLICY_CACHE_SIZE` | `2048` | 决策缓存容量（`0` 关闭） |
| `CP_POLICY_DECISION_LOG` | `data/policies/decisions.jsonl` | 决策日志路径 |
| `CP_POLICY_DECISION_LOG_ENABLED` | `1` | 是否写决策日志（模拟器数据源） |
| `CP_POLICY_INBOX_BACKEND` | `log` | 例外收件箱后端：`log`/`takeover`/`both`/`off`。**`takeover`/`both` 需要组合根先 `register_queue_resolver(...)` 注册队列解析器**（`agent/policy` 刻意不 import `agent.monitoring`，见下），否则只走本地账 |
| `CP_POLICY_INBOX_PATH` | `data/policies/inbox.jsonl` | 收件箱本地账路径 |
| `CP_POLICY_EGRESS_GUARD` | `1` | 出域执行点开关（`0` 关闭，排障用） |
| `CP_POLICY_TAINT_ENABLED` | `1` | 出域链路污点台账（§5.7 机制 4） |
| `CP_POLICY_TAINT_TTL_SECONDS` | `900` | 污点有效期 |
| `CP_POLICY_OBSERVE` | `1` | 是否写审计/事件埋点 |
| `CP_POLICY_OBSERVE_SCOPE` | `all` | 埋点范围：`all`（每条决策入链）/ `governance`（只为 deny/ask/break-glass/命中策略的 allow 写链与事件；`matched=False` 的「策略层无异议」仍写决策日志）。**默认 `all`** |
| `CP_POLICY_TAINT_DEEP_SCAN` | `0` | 是否并入既有 PII 检测器（`agent/utils/sensitive_data_filter.py`）的 CRITICAL 命中（见下） |
| `CP_POLICY_GATEWAY_ENABLED` | `0` | `PermissionGateway` 策略层开关（默认关闭，见 §四） |

> **`CP_POLICY_OBSERVE_SCOPE` 的实测依据**：链式审计是 SQLite 追加，是出域判定路径的
> 主要开销。`scripts/bench_policy_cache.py --with-io` 实测：命中路径 p50 从
> **0.022 ms（无埋点）→ 0.888 ms**，p99 从 **0.047 ms → 3.764 ms**；未命中路径 p99
> 从 0.232 ms → **7.97 ms（越过 5 ms 预算）**。即 §5.6 的「决策缓存 p99<5ms」在
> **决策本体**上余量约 105×，全量埋点后余量只剩 1.3×。默认仍取 `all`（满足「决策
> 入链式审计」的字面要求）；把「策略层无异议」剔出防篡改账本属于**部署侧的降噪
> 选择**，不由本任务替运维决定。

> **`CP_POLICY_TAINT_DEEP_SCAN` 的口径**：既有检测器 `SensitiveDataFilter.detect()`
> 覆盖邮箱/手机号/身份证等 **PII**，比「凭据」宽。默认并进来会让「给合作方 API 传一个
> 邮箱」变成出域拦截——那是把 §5.7-4 的「密钥外泄」误扩成「任何个人信息不得出境」。
> 因此默认只用本包的**凭据形态**口径；需要更严时开启深扫且**只取 CRITICAL 级**。

## 八、收件箱如何接上 AlertManager 的接管队列（依赖倒置）

`agent/policy` **不** import `agent.monitoring`——不是洁癖，而是架构护栏要求：

```
agent.monitoring.self_healer → agent.permission_system        （既有）
agent.permission_system → agent.policy → … → agent.policy.inbox
agent.policy.inbox → agent.monitoring.alert_manager           （若静态导入）
agent.monitoring.alert_manager → agent.monitoring.self_healer （既有）
                        ⇓
        no_circular_dependency 违规 → CI 阻塞
```

因此队列由**组合根**注入。在 `app_server.py` / `lifecycle_manager.py` 这类启动点写一次：

```python
from agent.monitoring.alert_manager import get_alert_manager
from agent.policy.inbox import register_queue_resolver

register_queue_resolver(
    lambda: getattr(get_alert_manager(), "_takeover_queue", None))

# 之后 takeover / both 后端即可把策略例外路由进人工接管队列
```

也可以对单个实例直接注入：`PolicyInbox(backend="takeover", queue=queue)`。
两者的接线都有用例覆盖（`tests/unit/test_policy_support.py::TestPolicyInbox`）。
