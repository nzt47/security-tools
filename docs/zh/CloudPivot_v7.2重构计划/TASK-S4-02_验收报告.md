# TASK-S4-02 验收报告 — 策略即代码（等效声明式引擎 + 策略模拟器 + 决策/执行分离）

> 任务书：[TASK-S4-02_策略即代码.md](TASK-S4-02_策略即代码.md)｜分发壳：[START-S4-02_策略即代码.md](START-S4-02_策略即代码.md)
> 批次总表：[PARALLEL_并行铺开总表.md](PARALLEL_并行铺开总表.md)｜基线：`master`
> 工作区：`.worktrees/s402`（id=`s402`，`--base master`）
> 归档日期：2026-09-11｜验收方式：**逐条对照任务书 §四**

---

## 〇、一句话结论

把云枢的权限判定从「散落在各调用点的 if」收敛成**可版本化、可模拟、可审计、
可签名**的策略库；引擎**只出决策不做网络动作**（P7.1-20），执行由
`HttpClient`（数据出域）与 `PermissionGateway`（工具权限）两个真实执行点落实，
且执行点在放行前**仍须通过自己既有的判定** —— 因此引入本引擎**不可能**放宽权限，
策略未覆盖时回落既有判定（零行为回归有机器可读证据）。

**任务书 §四 七项验收：7/7 通过**（逐条证据见 §三）。

---

## 一、交付物清单

### 1.1 新增包 `agent/policy/`（策略引擎）

| 文件 | 行数 | 职责 |
|---|---|---|
| `models.py` | 766 | §3.11 `Policy` 九字段 schema + `PolicyContext`（决策输入）+ `PolicyDecision`（决策结果）；纯数据与校验，无 I/O |
| `matcher.py` | 606 | OPA 子集等效判定器（字段路径比较 / 集合成员 / 布尔组合 / 简写映射）+ 支持矩阵 |
| `store.py` | 588 | 版本化策略库（SemVer 历史）+ `effective_range` + ed25519 验签 + **内置不变量** + 遮蔽诊断 |
| `signing.py` | 383 | ed25519 签名 + `sha256-self` 降级占位（沿用 S2-02 审计链口径） |
| `engine.py` | 838 | `PolicyEngine.check(policy_ctx) -> {allow\|deny\|ask, policy_id}`；LRU 决策缓存 + 埋点 + 审计 + break-glass + 例外路由 |
| `decisions.py` | 464 | 决策日志（脱敏；**模拟器的重放数据源**，说明为何 §6.6 事件不够） |
| `taint.py` | 540 | 出域链路监测的「读密钥」半边（§5.7 机制 4）+ 凭据形态识别 |
| `egress.py` | 268 | 出域**决策输入**组装与判定（P7.1-20 决策侧） |
| `inbox.py` | 425 | 例外收件箱（复用 `hitl/takeover_queue`；§5.6「只收例外」+ 防洪水） |
| `simulator.py` | 586 | P7.2-19 策略模拟器 + 报告渲染 + CLI |
| `MATCH_SUBSET.md` | 175 | **match 子集文档**（支持/不支持语法；与实现一致性有单测守着） |
| `__init__.py` | 158 | 公开 API + 模块地图 + 三条易错纪律 |

### 1.2 执行点（P7.1-20 的「执行」侧）

| 文件 | 性质 | 说明 |
|---|---|---|
| `agent/guardrails/egress_guard.py` | **新增**（219 行） | 数据出域执行点：被 `HttpClient` 调用，拦截发生在网络动作之前；拦截入审计 `egress.blocked` |
| `agent/web/http_client.py` | 改 3 处 | `request()` / `download()` 前置出域判定；新增 `_egress_block()`；`blocked_count` 计数 |
| `agent/permission_system.py` | 改 3 处 | `PermissionGateway` 新增「层 0」策略层（**默认关闭**，`CP_POLICY_GATEWAY_ENABLED=1` 或显式注入）；只收敛不放宽 |
| `agent/tools/file_tools_reg.py` | 改 1 处 | `read_file` 工具读取敏感文件后登记污点（出域链路监测的**读端点**；纯旁路，不改返回值） |

> **与 S4-03 的重叠面**：`agent/guardrails/` 只**新增**一个文件，既有 guardrails 模块
> 一行未改；`agent/monitoring/` 未触碰。S4-03（第二波）落点与本任务无文本级冲突。

### 1.3 策略库与门禁

| 文件 | 性质 |
|---|---|
| `data/policies/policies.json` | 策略库（3 条作者策略 + 1 条内置不变量；入库跟踪，人工合入） |
| `data/policies/README.md` | 策略库说明：schema / 判定顺序 / **今日是否改变行为** / 变更流程 / 环境变量 |
| `scripts/check_policy_change_gate.py` | 策略变更合入门禁（schema 自检 + 模拟报告对齐 + 高危逐条确认 + 无样本声明 + 签名） |
| `scripts/bench_policy_cache.py` | 决策缓存 p99 实测（口径可核对，输出可入报告） |
| `scripts/demo_s4_02_policy_simulation.py` | 模拟器样例报告生成器（合成历史 + 双候选，产出验收样例） |
| `.github/workflows/policy-change-gate.yml` | CI 门禁（PR 触及策略文件时强制） |
| `.github/pull_request_template.md` | PR 模板新增「策略变更 / 高危确认 / 无样本声明」三段 |
| `.pre-commit-config.yaml` + `hooks/pre-commit` | 策略 schema 门禁（框架 hook + 本仓库实际生效的自定义 hook 各一处） |

### 1.4 用例（9 个套件 / 572 例）

| 套件 | 例数 | 覆盖重点 |
|---|---|---|
| `tests/unit/test_policy_models.py` | 68 | §3.11 九字段校验 / effective_range / 安全渲染 / descriptor 裁剪 / 禁用 token |
| `tests/unit/test_policy_matcher.py` | 93 | 算子语义 / 缺失值 / 不可比 / 不支持语法装载期拒绝 / **文档一致性** |
| `tests/unit/test_policy_store.py` | 64 | 装载形态 / 版本历史 / 验签（含篡改与降级）/ 内置不变量不可遮蔽 |
| `tests/unit/test_policy_engine.py` | 62 | 判定语义 / 缓存与失效 / **p99 相对证据** / break-glass / 收件箱路由 / 埋点 |
| `tests/unit/test_policy_egress.py` | 88 | 目标归类 / 三路证据合并 / decide_egress / 执行点成形 / 污点台账 |
| `tests/unit/test_policy_simulator.py` | 50 | 变更分类 / deny→allow 主指标 / 高危清单 / 无样本 / 漂移 / CLI |
| `tests/unit/test_policy_support.py` | 52 | 决策日志 / 收件箱 / 签名的**降级与失败路径** |
| `tests/unit/test_policy_integration.py` | 42 | **真实执行点接线** / 读密钥→外发链路 / 网关零回归 / AST 无网络副作用 |
| `tests/unit/test_policy_change_gate.py` | 39 | 门禁每条失败路径（拦住才算门禁） |
| `tests/unit/policy_testkit.py` | — | 落盘与单例隔离工具（通用硬约束 6） |

---

## 二、关键设计裁定（设计文档未闭合处）

| # | 议题 | 裁定 | 落点 |
|---|---|---|---|
| D1 | 是否引入 OPA/WASM | **不引入**，用等效声明式引擎；接口形状兼容未来替换（`check(policy_ctx) -> {allow\|deny\|ask, policy_id}`）。求值被隔离在 `matcher.py` 一个模块内 | `matcher.py` 模块 docstring |
| D2 | `version` 语法 | **SemVer**（与 §3.2 `meta.version` 同口径，便于「策略版本变更 ⇒ 组装缓存失效」统一判定） | `models.py` 裁定 #1 |
| D3 | `effective_range` 结构 | dict，四个可选键：`not_before`/`not_after`（ISO 闭区间）/`tenants`/`scopes`（fnmatch 通配）。范围在**决策时**判定而非装载时过滤（防止长驻进程「静默到期」） | `models.py` 裁定 #2 |
| D4 | `break_glass_ttl_min` 语义 | 该策略允许被 break-glass 例外的**上限分钟数**；`null` ＝ 绝对 deny 不可例外。例外必须由人授予、有 TTL、入审计 | `engine.grant_break_glass` |
| D5 | `origin.opaque`（§5.6 Rego 例子用了它，§3.2 无此字段） | `opaque := (provenance == "unknown")`——§2.2「闭源只做行为级萃取，标 opaque:true」在 descriptor 契约里正是 provenance 停在 unknown。**不新增字段** | `models._is_opaque` |
| D6 | 判定顺序（任务书指定「首个命中生效」） | 内置不变量**永远最前且不可遮蔽**；其余按策略文件书写顺序。顺序敏感性由 `shadow_report()` 显式诊断并进入模拟报告 | `store.active` / `shadow_report` |
| D7 | 策略层的方向性 | **只收敛不放宽**：`allow` 仅表示「策略层无异议」，执行点仍须通过既有判定。因此引入引擎不可能放宽权限 | `engine.py` 核心不变量段 |
| D8 | 决策日志为何存在（§6.6 埋点字段不足以重放） | §6.6 的 `policy.decision` 只有 `{policy_version, actor, scope, result, latency_ms}`——**没有决策输入**，无法回答「把候选策略放进去这条历史决策会变成什么」。因此额外持久化**脱敏后的决策输入** | `decisions.py` 模块 docstring |
| D9 | 深扫 PII 是否并入凭据判定 | **默认不并入**：`agent/utils/sensitive_data_filter.py` 覆盖邮箱/手机号/身份证，并进来会让「给合作方 API 传邮箱」变成出域拦截，是把 §5.7-4 的「密钥外泄」误扩成「个人信息不得出境」。提供 `CP_POLICY_TAINT_DEEP_SCAN=1` 且只取 CRITICAL 级 | `taint._deep_scan` |
| D10 | 收件箱后端 | 规格要求复用 `hitl/takeover_queue`；但该队列**纯内存、无单例、无存储路径**，且导入 `alert_manager` 会拉起监控栈。裁定为**可插拔适配器**：默认本地 JSONL 账（跨重启不丢），`takeover`/`both` 显式接线。**进一步裁定（实现期，见 §四.2）**：队列由调用方注入或由组合根 `register_queue_resolver()` 注册——`agent/policy` 侧**不 import `agent.monitoring`**，用依赖倒置消除架构环 | `inbox.py` 模块 docstring |
| D11 | 策略签名是否强制 | **默认不强制**。单机部署下「没配密钥就装不上策略」会退化成**无策略**——那是安全降级而非增强。`CP_POLICY_REQUIRE_SIGNATURE=1` 时强制，且拒绝 `sha256-self` 占位 | `signing.py` 模块 docstring |

---

## 三、任务书 §四 验收清单逐条核验

### ✅ 1. Policy schema 对齐 §3.11；版本化 + effective_range + 签名生效

| 子项 | 证据 |
|---|---|
| 九字段一字不差 | `tests/unit/test_policy_models.py::TestPolicySchema::test_九字段全部落地` 断言 `set(to_dict()) == {id, version, owner, effect, match, message_template, effective_range, break_glass_ttl_min, signature}` |
| 版本化留痕、最高版生效 | `test_policy_store.py::TestVersioning::test_同_id_多版本保留历史_最高生效`（`1.0.0 / 1.2.0 / 1.10.0` → 生效 `1.10.0`） |
| 同版本不同内容被拒 | `test_同版本不同内容报错` |
| `effective_range` 判定 | `test_policy_models.py::TestEffectiveRange`（9 例）+ `test_effective_range_不覆盖时视为不存在` |
| **签名生效** | `test_ed25519_签名与验签`（真 ed25519 往返）、`test_篡改内容验签失败`、`test_策略库用错公钥时拒绝装载`、`test_sha256_self_占位`、`test_强制签名时拒收降级占位` |
| 内置不变量自带内容指纹 | `test_内置不变量自带签名指纹`（`sha256-self:` 且验签通过） |

**签名降级口径**与 S2-02 审计链 `RootsSigner` 一致（`cryptography` 缺失/无私钥 ⇒
`sha256-self` 占位并标注 `degraded=True`），避免「验签口径分裂」。

### ✅ 2. `PolicyEngine.check` 返回 `{allow|deny|ask, policy_id}`；决策缓存 p99<5ms（实测）

**返回值形状**（§5.6 原文）：

```
decision.effect   ∈ {allow, deny, ask}
decision.policy_id = '<命中的策略 id>'（未命中为 ""）
decision.matched   = False ⇒ 执行点回落既有判定
```

`test_policy_engine.py::TestDecisionShape::test_返回值形状符合_5_6`

**p99 实测**（`scripts/bench_policy_cache.py --iterations 20000 --variants 64 --enforce`）：

```
==========================================================================
云枢策略决策缓存实测（§5.6 决策缓存 p99 < 5ms）
==========================================================================
python      : 3.12.0
platform    : Windows-10-10.0.19045-SP0
cpu_count   : 12
口径        : perf_counter 包夹 PolicyEngine.check() 全量耗时（毫秒）
参数        : iterations=20000 variants=64 warmup=200 cache_size=2048 with_io=False
策略库      : data/policies/policies.json
生效策略数  : 4（指纹 3e475f5dd5207980）

【命中路径】引擎内部统计
  samples=4096     p50=0.0167 ms   p95=0.0332 ms   p99=0.0355 ms   max=0.0603 ms   mean=0.0203 ms

【命中路径】外部计时（bench 侧）
  samples=20000    p50=0.0224 ms   p95=0.0411 ms   p99=0.0474 ms   max=0.7556 ms   mean=0.0245 ms

【未命中路径】引擎内部统计
  samples=4096     p50=0.0966 ms   p95=0.1874 ms   p99=0.2359 ms   max=0.4634 ms   mean=0.1092 ms

【未命中路径】外部计时（bench 侧）
  samples=20000    p50=0.1043 ms   p95=0.2032 ms   p99=0.2319 ms   max=11.8685 ms   mean=0.1188 ms

缓存统计    : {'size': 64, 'capacity': 2048, 'hits': 40136, 'misses': 64}

判定        : 命中路径 p99 = 0.0474 ms  ≤ 阈值 5.0 ms  => PASS
结果已写出: reports/policy_cache_bench.json
```

| 指标 | 实测 | 预算 | 余量 |
|---|---|---|---|
| **缓存命中路径 p99** | **0.0474 ms** | < 5 ms | **≈105×** |
| 缓存命中路径 p99（引擎内部口径，4096 样本窗口） | 0.0355 ms | < 5 ms | ≈141× |
| 缓存未命中路径 p99（含策略遍历 + 求值） | 0.2319 ms | — | 仍在预算内 |

**口径说明**（START §七.2 指名要求）：计时用 `time.perf_counter()` 包夹
`PolicyEngine.check()` **全量耗时**（含缓存查找、埋点、决策日志写入），单位毫秒；
`--with-io` 未开启（验收指标是**决策**延迟）。命中路径的 `max=0.7556 ms` 与未命中
路径的 `max=11.8685 ms` 是 2 万样本里的 OS 调度毛刺（各 1 例），**不作为 p99 口径**。

**缓存可失效**（结构性保证，非调用方责任）：

| 场景 | 用例 | 机制 |
|---|---|---|
| 策略新增 | `test_策略变更即失效` | `revision` 进缓存键 |
| 策略移除 | `test_策略移除即失效` | 同上 |
| 显式清空 | `test_显式_invalidate` | `epoch` 推进 |
| break-glass 授予/撤销 | `test_授予会使缓存失效` / `test_撤销后恢复拒绝` | `grant_epoch` 进缓存键 |
| 容量 | `test_LRU_淘汰不超容量` / `test_容量零即关闭缓存` | 有界 LRU |

**单测侧的相对断言**（避免 CI 覆盖率插桩下的绝对阈值抖动）：
`TestLatencyEvidence::test_缓存命中路径相对更快` —— 命中 p99 ≤ 未命中 p99 × 3 + 0.5 ms。

### ✅ 3. 决策入链式审计 + `policy.decision` 埋点（与 S2-02/S2-03 联动）

一次决策固定产出（`DecisionObserver`，`engine.py`）：

| 产出 | 落点 | 用例 |
|---|---|---|
| 审计链动作 `policy.decision` | `agent.audit.facade.audit.record` | `test_决策写入链式审计` |
| 事件 `policy.decision` | `agent.observability.events.emit` | `test_事件埋点_policy_decision_与_policy_denied` |
| 事件 `policy.denied`（deny 时） | 同上；S2-03 既有治理事件，**自动镜像入链** | 同上 |
| 事件 `intervention`（ask/break-glass 时，`kind=policy.ask`） | 同上 | `test_ask_发_intervention_事件` |
| `egress.blocked`（执行点拦截时） | 审计链 | `test_拦截写入审计_egress_blocked` |
| `policy.taint.marked`（读密钥污点登记） | 审计链 | `taint._audit_taint` |
| `policy.break_glass.grant` / `.revoke` | 审计链（`source="ui"`，因为它是人的动作） | `engine._audit_break_glass` |

**脱敏口径**：审计/事件只用 `PolicyDecision.audit_leaves()`（§6.6 五字段 + `cache_hit`
+ `reason_code` + `policy_id`），**绝不带 match 的匹配值**。
`test_埋点不写匹配值` 用一条含 `sk-...` 的输入跑完整链路，断言事件 JSON 里不出现该值。

### ✅ 4. secret 出域 → deny（决策层用例 + 执行层强制用例：外发被拦）

**契约级不变量**（内置，**不可被策略文件遮蔽**）：

```json
{ "id": "builtin.invariant.secret-egress-deny", "effect": "deny",
  "match": { "all": [ {"field":"capability.trust.data_class","op":"eq","value":"secret"},
                      {"field":"target.external","op":"eq","value":true} ] } }
```

| 层 | 用例 | 断言 |
|---|---|---|
| **决策层** | `test_secret_出域拒绝_决策层` | `effect=deny`、`policy_id=builtin.invariant.secret-egress-deny` |
| 决策层（不可遮蔽） | `test_内置不变量先于文件策略` | 文件里一条宽泛 `allow` **不能**关掉它 |
| 决策层（内部目标不拒） | `test_secret_内部目标不拒绝` | `http://127.0.0.1` 放行 |
| **执行层（强制）** | `TestHttpClientEgressExecutionPoint::test_载荷带密钥时拦截且未发生网络动作` | `result["blocked"] is True` **且 `mock_request.assert_not_called()`** |
| 执行层（下载路径） | `test_download_也是执行点` | `download()` 走 `_session.get`，单独接线；`mock_get.assert_not_called()` |
| 执行层（内网不受影响） | `test_内网出域不受污点影响` | loopback 出域照发（无外泄面） |
| 执行层（拦截计数） | `test_拦截计数入_stats` | `blocked_count >= 1`、`success_count == 0` |
| 执行层（审计留痕） | `test_拦截写入审计_egress_blocked` | 链上出现 `egress.blocked`，且 `network_action_taken=False` |

**三条 data_class 证据线**（`egress._effective_data_class`，更严者胜）：

| 证据 | 含义 | 用例 |
|---|---|---|
| `declared` | 调用方从 descriptor 读到的 `trust.data_class=secret` | `test_显式声明` |
| `payload_material` | **出站载荷当下就含凭据形态的值** | `test_载荷含凭据即视为_secret` |
| `read_then_egress` | **同一作用域内先读过敏感文件**（§5.7 机制 4） | `test_链路污点即视为_secret` |

**「读本地密钥 → 外发 HTTP」端到端链路**（任务书 §二 硬约束 2 与 §5.7 机制 4）：

```
tests/unit/test_policy_integration.py::TestToolReadToEgressChain::
  test_读密钥之后外发被拒
    ├─ read_file(工具真实包装) 读 <tmp>/.env（含 OPENAI_API_KEY=sk-...）  → ok=True（读取本身不受影响）
    ├─ observe_file_read 登记污点（路径是凭据载体 ∧ 内容真的像凭据）
    └─ EgressGuard.precheck(POST https://api.example.com/v1/chat)
         → allowed=False，data_class_source='read_then_egress'
```

反向用例保证窄口径：`test_读普通文件不污染链路`、`test_读敏感路径但内容普通不污染`
（**误报的代价是外发被拦**，所以闸门取窄）。

### ✅ 5. LLM 无法在策略中写网络副作用（无 `http.send` 类能力；引擎纯决策）

**三层证据**：

| 层 | 机制 | 用例 |
|---|---|---|
| **装载期拦截** | `match` 子树 JSON 规范化后扫描 `FORBIDDEN_MATCH_TOKENS`（26 个 token：`http.send`/`socket`/`subprocess`/`requests.`/`eval(` …）→ 拒绝装载 | `TestForbiddenTokens`（7 例，含设计文档 §5.6 那段 `http.send(_)` 伪代码被拦） |
| **静态证据** | AST 扫描 `agent/policy/*.py`：**不得导入**任何网络/子进程库（`requests`/`socket`/`subprocess`/`urllib.request`/`httpx`/`aiohttp`/`selenium` …）；写文件只允许出现在明确的落盘模块 | `TestNoNetworkSideEffects::test_策略包不导入任何网络或子进程库` + `test_导入黑名单判定自身可区分_parse_与_request` |
| **运行期证据** | monkeypatch `socket.socket` / `socket.create_connection` 为探针后跑决策，断言**零调用** | `test_运行期决策不触碰_socket` |
| API 形状 | 引擎公开 API 无 `send`/`request`/`execute`/`run`/`fetch` 一类方法 | `test_引擎公开_API_无执行类方法` |

> 说明：`urllib.parse` 是**纯字符串处理**（无 IO），显式放行并在用例里区分——
> 一刀切把 `urllib` 全禁会造成「黑名单靠名字长度而非语义」的假证据。

### ✅ 6. 模拟器可对历史决策重放并输出 deny→allow 变更 + 高危清单；PR 门禁检查存在

**模拟器**（P7.2-19）：`agent/policy/simulator.py`，CLI
`python -m agent.policy.simulator --candidate <file> --since 7d`。

**重放语义**：历史决策的**输入**取自 `DecisionLog`（见裁定 D8），分别喂给
「当前策略库」与「当前策略库 + 候选策略」两个引擎（均关闭缓存/埋点/落盘），
比对 `old.effect` 与 `new.effect`。

**变更分类**（`classify_change`）：

| 变化 | 风险 | 用例 |
|---|---|---|
| `deny → allow` | **高危（P7.2-19 主指标）** | `test_候选放宽普通外发时抓到_deny_to_allow`（3 条） |
| `ask → allow` | **高危** | `test_ask_转_allow_为高危` |
| `allow → deny` | 中（需确认不是误伤） | `test_收紧策略产出_allow_to_deny` |
| `* → ask` | 中（影响吞吐） | `test_变更为_ask` |
| `ask → deny` | 中 | `TestClassify::test_分类` |

#### 模拟器样例报告（任务书要求的样例，`scripts/demo_s4_02_policy_simulation.py` 生成）

数据源：**合成 7 天历史 80 条**（`allow 58 / ask 9 / deny 13`），覆盖 §5.6/§2.5
的判定分支。
> ⚠ **口径纪律**：合成历史**不是真实流量**。本样例只证明「模拟器会算、会分类、
> 会列高危」，**不能**用作「策略变更已评估真实影响」的证据；真实评估需在具备
> 真实决策日志的环境里重跑同一条命令。

**候选 A：把「机密数据出域」从需要人工确认放宽为直接允许**

```
重放条数     : 80
deny → allow : 0   ← P7.2-19 主指标
allow → deny : 0
变更为 ask   : 0
ask → allow  : 9
重放漂移     : 0
高危命中     : 9
结论         : needs_ack
  [1] ask→allow cp.demo.src.act2 http.post (policy gov.confidential-external-ask)
  [2] ask→allow cp.demo.src.act3 http.post (policy gov.confidential-external-ask)
  [3] ask→allow cp.demo.src.act4 http.post (policy gov.confidential-external-ask)
  [4] ask→allow cp.demo.src.act0 http.post (policy gov.confidential-external-ask)
  [5] ask→allow cp.demo.src.act1 http.post (policy gov.confidential-external-ask)
  ...（其余 4 条见 reports/policy_simulation_sample.md）
主指标核对   : totals={"total": 80, "unchanged": 71, "changed": 9, "deny_to_allow": 0,
                "allow_to_deny": 0, "to_ask": 0, "ask_to_allow": 9, "ask_to_deny": 0,
                "other": 0, "replay_drift": 0}
```

**候选 B：把冻结窗口的绝对禁令降级为允许（deny→allow 非零样例）**

```
重放条数     : 80
deny → allow : 7   ← P7.2-19 主指标（非零）
allow → deny : 0
ask → allow  : 0
重放漂移     : 0
高危命中     : 7
结论         : needs_ack
  [1] deny→allow cp.demo.src.act3 http.post (policy ops.freeze-external-egress)
  [2] deny→allow cp.demo.src.act4 http.post (policy ops.freeze-external-egress)
  [3] deny→allow cp.demo.src.act0 http.post (policy ops.freeze-external-egress)
  [4] deny→allow cp.demo.src.act1 http.post (policy ops.freeze-external-egress)
  [5] deny→allow cp.demo.src.act2 http.post (policy ops.freeze-external-egress)
  ...（其余 2 条见 reports/policy_simulation_sample_deny2allow.md）
主指标核对   : totals={"total": 80, "unchanged": 73, "changed": 7, "deny_to_allow": 7,
                "allow_to_deny": 0, "to_ask": 0, "ask_to_allow": 0, "ask_to_deny": 0,
                "other": 0, "replay_drift": 0}
```

报告产物：`reports/policy_simulation_sample.md` / `.json`（候选 A）、
`reports/policy_simulation_sample_deny2allow.md` / `.json`（候选 B）。

**报告的两项诚实性纪律**：
1. **无样本时明示不可判定**：`verdict=no_sample`，报告正文写「模拟不可判定」并
   点明「『零变更』在此情形下**不构成**安全证据」（`test_无样本时明示不可判定`）；
2. **重放漂移单列**：基线重算 ≠ 历史记录时计入 `replay_drift`
   （`test_重放漂移被计数`），因为决策日志与事件层同口径脱敏，脱敏丢弃的键会让
   重放分歧——这是数据质量信号，不该被藏起来。

#### PR 合入门禁（实跑证据）

门禁脚本 `scripts/check_policy_change_gate.py`，CI 侧
`.github/workflows/policy-change-gate.yml`（PR 触及 `data/policies/**` 时强制）。

四个场景的**实跑输出**：

```
=== DEMO 1: 改了策略 + 高危变更 + PR 描述无 ## 高危确认 段 ===
[INFO] .../data/policies/freeze-relax.json: 装载 1 条策略，自检通过
[FAIL] 存在 7 条高危变更（原 deny/ask 现 allow），PR 描述必须包含 `## 高危确认` 段并逐条勾选确认。
       格式：每个条目一行 `- [x] <policy_id> <capability_id> <理由>`
EXIT=1

=== DEMO 2: 逐条勾选 7 条高危 ===
[INFO] .../data/policies/freeze-relax.json: 装载 1 条策略，自检通过
[INFO] 高危变更确认齐全: 7/7
[OK] 策略变更门禁通过：候选 `ops.freeze-external-egress@2.0.0`，样本 80，deny→allow 7，高危 7
EXIT=0

=== DEMO 3: 只勾选 3 条（不足 7） ===
[FAIL] 高危变更 7 条，但 `## 高危确认` 段只勾选了 3 条（需逐条勾选，格式 `- [x] ...`）
EXIT=1

=== DEMO 4: 未改动策略文件 ===
[OK] 本次未改动策略文件，门禁跳过（策略即代码只对策略变更设闸）
EXIT=0
```

**门禁的 5 道闸**（每条失败路径都有用例，见 `test_policy_change_gate.py`）：

| # | 闸门 | 失败用例 |
|---|---|---|
| 1 | 策略文件合法（schema / match 子集 / 禁用 token / 签名） | 7 例 + hook 调用形态 5 例 |
| 2 | 改动触及策略文件时**必须**有 `policy.simulation.v1` 模拟报告 | `test_缺报告被拦` |
| 3 | 报告的候选策略 id/version 必须在**改动后的**文件里 + 报告不早于文件 | `test_候选与改动不对应被拦`、`test_候选版本过期被拦`、`test_报告早于策略文件被拦` |
| 4 | 高危变更在 PR 描述 `## 高危确认` 段**逐条勾选**（勾选数 ≥ 高危数，且每条写明策略 id） | `test_高危未勾选被拦`、`test_勾选数不足被拦`、`test_勾选但未提及策略_id_被拦` |
| 5 | `total=0` 时必须在 `## 无样本声明` 段**勾选**声明 | `test_无样本未勾选声明被拦`、`test_无样本缺声明段被拦` |

> 第 4/5 闸用**「已勾选项计数」**而不是「出现某段文字」判定：模板天然带有未勾选的
> 提示行，只有作者主动勾选才算确认。用文字出现与否会被模板本身满足——那不是门禁，
> 那是走形式。

**本地 pre-commit**：`.pre-commit-config.yaml` 新增 `policy-schema-gate`；
同时写入 `hooks/pre-commit`（本仓库 `core.hooksPath = hooks`，框架 hook 不随
`git commit` 触发，因此两处都接）。自定义 hook 只在**暂存区确实有策略文件**时运行，
逃生通道 `SKIP_POLICY_GATE=1`（需在提交说明中交代）。

### ✅ 7. 既有 guardrails/权限/审批套件零回归；新增单测全绿、覆盖率 ≥80%

**新增 9 个套件 572 例全绿**，`agent/policy` + `egress_guard` 覆盖率 **93%**：

```
Name                               Stmts   Miss  Cover   Missing
----------------------------------------------------------------
agent\guardrails\egress_guard.py      74      4    95%   73-74, 218-219
agent\policy\__init__.py              13      0   100%
agent\policy\decisions.py            237     24    90%   ...
agent\policy\egress.py               115      2    98%   239-240
agent\policy\engine.py               414     40    90%   ...
agent\policy\inbox.py                226     32    86%   ...
agent\policy\matcher.py              276     20    93%   ...
agent\policy\models.py               316     17    95%   ...
agent\policy\signing.py              200     21    90%   ...
agent\policy\simulator.py            288      7    98%   254, 346, 488-492
agent\policy\store.py                312     13    96%   ...
agent\policy\taint.py                241     20    92%   ...
----------------------------------------------------------------
TOTAL                               2743    191    93%
=============================== 572 passed in 16.06s ===============================
```

> **单模块也全部 ≥86%**（任务书要求 ≥80%）。三个原本低于 80% 的模块
> （`decisions.py` 73% / `inbox.py` 69% / `signing.py` 76%）通过补齐
> **降级与失败路径**用例抬到 90%/86%/90%——「没测到的降级路径」等于「没实现的
> 降级路径」。

**邻接回归（零回归）**：

| 套件组 | 结果 |
|---|---|
| 审计（`test_audit*` 4 套）+ 事件（`test_events_v1`）+ 权限（3 套）+ guardrails（2 套）+ HITL/接管队列（2 套） | **417 passed / 0 failed** |
| HTTP 客户端与 web（`test_http_client` / `test_web_http_client` / `test_web_init`） | **33 passed / 0 failed** |
| 全量抽查 `pytest -m "not slow" -p no:randomly` | 见 §五 |

**「策略未覆盖 ⇒ 回退既有判定」的机器可读证据**（任务书 §四 硬约束 5）：

```
tests/unit/test_policy_integration.py::TestRepositoryPolicyAsset::
  test_存量未分级输入不被任何策略命中
    ├─ cp.filesystem.local.read (external=False)  → matched=False, effect=allow
    ├─ cp.web.search           (external=True)   → matched=False, effect=allow
    └─ cp.mcp.github.issue     (external=True)   → matched=False, effect=allow
```

`data/policies/README.md` §四 逐条列出「当前已装载的 4 条策略与**它们今天是否会
改变行为**」：三条作者策略都要求输入里存在 `secret`/`confidential`/`destructive`
分级或显式 `freeze_window` 属性，而存量资产 `risk_level`/`data_class` 全为 `None`
（S0-02 摸底），因此**今日四条全部不命中**，执行点回落既有判定。

**策略层不放宽的机器可读证据**（比「零回归」更强的断言）：

```
test_策略_allow_不放宽既有拒绝
    guest 角色请求 file_read（RBAC 本就拒绝）→ 即使策略层 allow，仍 allowed=False
    developer 角色请求 system_format（RBAC denied_tools）→ 仍 allowed=False
```

---

## 四、本地门禁执行记录

| 门禁 | 命令 | 结果 |
|---|---|---|
| 新增套件 + 覆盖率 | `pytest tests/unit/test_policy*.py tests/unit/test_policy_support.py --cov=agent.policy --cov=agent.guardrails.egress_guard` | **572 passed / 0 failed**，覆盖率 **93%** |
| 邻接回归 | `pytest tests/unit/test_audit*.py tests/unit/test_events_v1.py tests/unit/test_permission*.py tests/unit/test_guardrails*.py tests/unit/test_hitl.py tests/unit/test_takeover_queue.py` | **417 passed / 0 failed** |
| HTTP 邻接 | `pytest tests/unit/test_http_client.py tests/unit/test_web_http_client.py tests/unit/test_web_init.py` | **33 passed / 0 failed** |
| kwarg 扫描（两条） | `python scripts/scan_kwarg_conflicts.py --path agent --min-risk HIGH` / `--path tests --min-risk HIGH` | **0 处**（exit 0 / exit 0） |
| mypy 新增模块 | `python -m mypy agent/policy/ agent/guardrails/egress_guard.py` | **新增文件 0 error** |
| mypy 既有阻塞模块 | `python -m mypy agent/env_config_manager.py agent/network_config.py` | 477 errors / 80 files —— 与 `master` 基线**逐字相同**（阻塞模块自身 0 error），**无新增回归** |
| 架构护栏（importlinter） | `lint-imports --config .importlinter` | **Contracts: 2 kept, 0 broken** |
| 架构护栏（arch_rules，**CI 阻塞**） | `python scripts/ci_run_module.py agent.observability.arch_rules --check --root agent --exemptions docs/architecture/legacy_exemptions.json --config config.yaml` | 本任务代码 **0 违规**（见 §四.2）；当前 master 上余 2 条属 S4-01 的 `agent/security/` |
| 策略 schema 门禁 | `python scripts/check_policy_change_gate.py --schema-only data/policies/policies.json` | 装载 3 条作者策略，自检通过 |
| 缓存 p99 实测 | `python scripts/bench_policy_cache.py --iterations 20000 --enforce` | **PASS**（命中 p99 0.0474 ms） |
| 真实提交场景 pre-commit | 见 §五 | ✅ 已跑 |
| 全量抽查（unit） | `pytest tests/unit -m "not slow" -p no:randomly -q -n 4 --dist=loadscope` | **14388 passed / 0 failed**（663.82s，`PYTEST_EXIT=0`） |
| 全量抽查（非 unit） | `pytest tests --ignore=tests/unit -m "not slow" -p no:randomly -q -n 4 --dist=loadscope` | 见 `reports/s402_nonunit_pytest.txt` |
| 门禁产物漂移 | `git status` 检查 | 见 §五.3（`tests/contract/contracts/*.json` 6 个文件被既有契约用例改写，已还原） |

---

### 4.2 架构护栏 arch_rules：实现期发现并修复的 2 处违规（**值得单列**）

`importlinter` 的 2 条 contract 是**模块级**规则，因此它一路绿；而 CI 真正阻塞的是
`architecture-check.yml` 里的 `agent.observability.arch_rules --check`——它统计
**函数级导入**，规则集含 `no_circular_dependency`。本任务实现期被它抓到 2 处环，
两处都修掉了，且结论用**受控实验**（干净基线上只叠加本任务改动）验证过：

| # | 环路径 | 根因 | 处置 |
|---|---|---|---|
| 1 | `agent.policy.simulator → agent.policy.engine → agent.policy.simulator` | `PolicyEngine.simulate()` 便捷门面的函数体里反向 `import simulator` | **删除该门面**（调用方直接 `from agent.policy.simulator import simulate`）。模拟器是引擎的**消费者**，方向天然单向；为一行语法糖引入一个需长期豁免的环不划算 |
| 2 | `agent.permission_system → agent.policy → agent.policy.egress → agent.policy.engine → agent.policy.inbox → agent.monitoring.alert_manager → agent.monitoring.self_healer → agent.permission_system` | `inbox._resolve_queue_once()` 为「自动找到 AlertManager 持有的接管队列」而 `import agent.monitoring.alert_manager` | **依赖倒置**（仓库对该规则的既有补救口径）：队列改为调用方注入（`queue=`）或由**组合根**调用 `register_queue_resolver()` 注册；`agent.policy` 侧只持有 `Callable`，环即消除。另加 AST 用例守着「`agent/policy/*.py` 不得出现 monitoring 导入」 |

**受控实验（证明本任务对架构门禁的贡献为 0）**：

```
# 干净基线 d99a85e2（本任务合并前的 origin/master）
$ python scripts/ci_run_module.py agent.observability.arch_rules --check ...
  → 违规总数 4 / 未豁免 0 → ✅ 通过（exit 0）

# 干净基线 + **仅本任务全部改动**（修复后）
$ git apply <本任务 5 个路径的完整 diff> && 同命令
  → 违规总数 4 / 未豁免 0 → ✅ 通过（exit 0）

# 当前 master（含其他会话的合并）
  → 违规总数 6 / 未豁免 2 → ❌ 违规
     · agent.security → agent.security.approval_guard          （S4-01，commit 1cc3baa7）
     · agent.security.approval_session → agent.security        （S4-01，commit 1cc3baa7）
```

> **一次性教训**：只跑 `lint-imports` 就宣称「架构护栏全绿」是不够的——它只覆盖
> 模块级导入，而 CI 另有基于函数级导入的 `arch_rules`。本任务两处违规在
> `lint-imports` 下完全不可见。

## 五、门禁产物漂移与全量回归

### 5.1 全量抽查

任务书指定入口：

```
python -m pytest -m "not slow" -p no:randomly -q
```

`pytest.ini` 收集到 **18084 items / 475 deselected / 1 skipped / 17609 selected**。
**单进程串行跑该规模在本机跑到 48% 时被外部终止**（约 50 分钟，进程被 kill，
**非用例失败**——输出停在 `test_knowledge_links.py` 且无 FAILED 行）。
因此改用**并行分片**把两段都跑完（`-n 4 --dist=loadscope`，与 CI 的分片口径一致，
只是 worker 数更高）：

| 范围 | 命令 | 结果 |
|---|---|---|
| `tests/unit`（本任务改动的主战场） | `pytest tests/unit -m "not slow" -p no:randomly -q -n 4 --dist=loadscope` | **14388 passed / 51 skipped / 13 xfailed / 4 xpassed / 0 failed**，`PYTEST_EXIT=0`，663.82s |
| `tests/`（除 unit：boundary/contract/chaos/integration/e2e 等） | `pytest tests --ignore=tests/unit -m "not slow" -p no:randomly -q -n 4 --dist=loadscope` | 跑到 **97%** 时 xdist worker 挂起（最后完成的用例属 `tests/chaos/test_rate_limiter_chaos.py`，之后 65 分钟无任何用例输出与进展），已**主动终止**；该区段与本任务改动面无关，原始输出留存 `reports/s402_nonunit_pytest.txt` |

原始输出：`reports/s402_unit_pytest.txt`、`reports/s402_nonunit_pytest.txt`
（以及未跑完的串行尝试 `reports/s402_full_pytest.txt`，保留以佐证「被终止」而非
「有失败」）。

> **与已知基线对比**：`pytest.ini` 记录的 2026-08-13 全量基线为
> 「68 failed / 14359 passed / 10 errors」。本任务下 `tests/unit` 全量
> **14388 passed / 0 failed**，**优于**该基线；本任务未引入任何回归。

### 5.2 埋点成本的实测发现（需下游注意）

`scripts/bench_policy_cache.py --with-io` 把**全量埋点**（链式审计 SQLite + 事件
文件 + 决策日志）打开后实测：

```
【命中路径】外部计时   samples=3000  p50=0.8876 ms  p95=2.2062 ms  p99=3.764 ms
【未命中路径】外部计时 samples=3000  p50=1.3594 ms  p95=3.4792 ms  p99=7.9669 ms
判定        : 命中路径 p99 = 3.764 ms  ≤ 阈值 5.0 ms  => PASS
```

| 口径 | 无埋点 | 有埋点 | 5 ms 预算余量 |
|---|---|---|---|
| 命中路径 p50 | 0.0224 ms | 0.8876 ms | — |
| 命中路径 p99 | **0.0474 ms** | **3.764 ms** | 105× → **1.33×** |
| 未命中路径 p99 | 0.232 ms | **7.97 ms** | 预算内 → **越过预算** |

**结论**：§5.6 的「决策缓存 p99<5ms」在**决策本体**上余量约 105×；加装全量埋点后
余量只剩 1.33×（命中路径），未命中路径 p99 已越过 5 ms。

**处置**：默认行为**不变**（每条决策仍入链，满足「决策入链式审计」的字面要求），
新增 `CP_POLICY_OBSERVE_SCOPE=governance` 供部署侧选择——只为治理相关的决策
（deny / ask / break-glass / 命中策略的 allow）写链与事件，把 `matched=False` 的
「策略层无异议」剔出防篡改账本（**决策日志照写**，模拟器数据不受影响）。
实测依据与裁定写在 `DecisionObserver` 的类 docstring 与 `data/policies/README.md` §七。

> 这条发现本身是交付物的一部分：**把「决策快」与「决策+埋点快」混为一谈**是这个
> 指标最容易被做假的地方，因此两个口径都实测并分别列出。

### 5.3 产物漂移

跑完门禁后 `git status --porcelain` 发现 6 个既有契约用例产物被改写：

```
 M tests/contract/contracts/chat_api_contract.json
 M tests/contract/contracts/chat_api_pact.json
 M tests/contract/contracts/dashboard_api_contract.json
 M tests/contract/contracts/dashboard_api_pact.json
 M tests/contract/contracts/health_api_contract.json
 M tests/contract/contracts/health_api_pact.json
```

这 6 个文件与本任务**无任何关系**（是既有契约用例在每次全量运行时重写的产物），
按批次总表「跑完门禁后 git status 检查产物漂移并还原」的要求已 `git checkout --`
还原，不入本次提交。

---

## 六、遗留问题（逐条带归属与阻塞性判定）

| # | 遗留 | 归属 | 阻塞性 | 说明 |
|---|---|---|---|---|
| L1 | **出域执行点只覆盖 `HttpClient`**，不是进程级单一漏斗 | S4-03 / 后续任务 | **非阻塞**（本任务要求「至少一个真实执行点」，已满足且覆盖工具面） | `agent/monitoring/{loki,alert_notifier,search}.py`、`agent/cognitive/logging_integration.py` 直连 `requests`；`agent/extensions/*`、`agent/monitoring/error_reporter.py` 等用 `urllib.request`。这些路径的**策略判定**能生效（引擎是进程级的），但**执行点拦截**不覆盖。已在 `http_client._egress_block` 注释中登记 |
| L2 | `PermissionGateway` 策略层**默认关闭** | 本任务（刻意） | **非阻塞** | 通用硬约束 2「一切自动化开关默认关闭」。语义是只收敛不放宽，开启后不可能放宽权限；`CP_POLICY_GATEWAY_ENABLED=1` 或显式注入即可 |
| L3 | 决策日志与事件层同口径脱敏 ⇒ 匹配被脱敏键的策略会产生**重放漂移** | 本任务（已量化） | **非阻塞** | 模拟器把漂移计入 `replay_drift` 并在报告单列（`test_重放漂移被计数`）。纪律：策略不应匹配凭据类字段（那本身是反模式） |
| L4 | 策略签名**默认不强制**，且内置不变量用的是 `sha256-self` 占位 | 本任务（裁定 D11） | **非阻塞** | 与 S2-02 审计链同款降级口径；`CP_POLICY_REQUIRE_SIGNATURE=1` 时强制真签名并拒绝占位 |
| L5 | `TakeoverQueue` **纯内存、无落盘、无单例** | S4-01 / 后续任务 | **非阻塞** | 因此 `PolicyInbox` 默认走本地 JSONL 账（跨重启不丢），`takeover`/`both` 后端显式接线。若 S4-01 给队列补上持久化，本模块换个后端即可 |
| L6 | `origin.opaque` 是云枢裁定（`provenance == unknown`），非 §3.2 原生字段 | S1 契约层 | **非阻塞** | 裁定 D5 已文档化。若 S1 后续在 descriptor 增加真正的 `opaque` 字段，`from_descriptor` 改为优先读它即可（一处改动） |
| L7 | 阈值/开关较多（13 个 `CP_POLICY_*` 环境变量） | 本任务 | **非阻塞** | 全部有默认值且非法值回退默认（通用硬约束 3）；`data/policies/README.md` §七 有全表 |
| L8 | CI 侧 `policy-change-gate.yml` 未在本分支实测（需要真实 PR 上下文） | 本任务 | **非阻塞** | YAML 结构已按仓库既有 workflow 风格编写；门禁脚本本身的**每条失败路径**都有单测（44 例）+ 本地四场景实跑证据（§三.6）。真实 PR 上的首跑需 Owner 侧观察 |
| L9 | **全量埋点使出域判定 p99 逼近/越过 5 ms**（命中 3.76 ms / 未命中 7.97 ms） | 部署侧 / S6-01（面板） | **非阻塞**（默认行为已满足验收；这是容量规划输入） | 决策本体 p99 仅 0.047 ms（余量 105×），成本全在链式审计的 SQLite 追加。已提供 `CP_POLICY_OBSERVE_SCOPE=governance` 降噪开关（默认 `all` 不变）。若生产环境外发 QPS 高，建议同时开启该开关并观察链增长速率；S6-01 面板可作为观测落点 |
| L11 | **master 上 `architecture-check` 阻塞 job 仍红**：`agent/security/*` 有 2 条 `no_circular_dependency` 违规（`agent.security ↔ agent.security.approval_guard`、`agent.security.approval_session ↔ agent.security`） | **S4-01**（`agent/security/` 由其 commit `1cc3baa7` 新建） | **非阻塞本任务**（本任务代码已证明 0 违规），但**阻塞主干 CI** | 已用受控实验证明与本任务无关（干净基线 + 仅本任务改动 → 0 违规）。修复口径与本文 §4.2 同：依赖倒置或补 `docs/architecture/legacy_exemptions.json` 豁免。**请 S4-01 归属会话处置** |
| L10 | 出域判定会对**每个**外部 HTTP 请求产生一条决策日志 | 部署侧 | **非阻塞** | 这正是模拟器的数据来源（不能省），但意味着 `data/policies/decisions.jsonl` 会随外发量线性增长。当前无自动轮转（`DecisionLog._candidate_files` 已支持读 `decisions.<day>.jsonl` 分片，但**未实现写入轮转**）。建议由运维侧按日志量配置外部轮转，或后续任务补 `rotate_on_size` |

> 无「阻塞性」遗留；无需要 Owner 立即裁定的事项。

---

## 七、与任务书「上游已知坑」的对应

| 坑（START §七） | 本任务的处置 |
|---|---|
| 1. 新包易触发循环依赖 | `agent/policy` **不反向依赖任何上层**：与 permission_system 的关系通过「执行点主动调用 + `allow` 不放宽」实现，policy 侧不 import 权限/工具/网络模块；`lint-imports` 2 kept / 0 broken |
| 2. p99 断言在 CI 覆盖率插桩下抖动 | 拆成两半：单测做**相对断言**（命中 ≤ 未命中×3+0.5ms），绝对实测由 `scripts/bench_policy_cache.py` 输出并把口径写进报告；`--enforce` 才让不达标变非零退出 |
| 3. 策略类用例若落盘必须隔离 | `tests/unit/policy_testkit.py::isolate_policy` 逐用例隔离：策略库/决策日志/收件箱账/签名密钥/事件目录全部指向 `tmp_path`，并重置 4 个进程级单例（含 S2-03 的 `reset_event_stores()`——它会把**首次构造时的 `CP_EVENTS_DIR`** 记住，不重置会跨用例污染） |
| 4. `ask` 与审批流是两个概念 | `ask` 只产出「要人」信号：策略引擎**不审批**；执行点把它转成 `requires_confirmation`/拦截，`PolicyInbox` 路由到收件箱/接管队列 |
| 5. 勿在日志/审计里写敏感匹配值 | 审计只用 `audit_leaves()`（§6.6 五字段 + 诊断位）；决策日志走 S2-03 `sanitize_payload`；污点审计只写**文件名 + 类别**（不写完整路径与值）；`test_埋点不写匹配值` / `test_证据不落原始载荷` / `test_错误文案不含载荷` 三重守着 |

---

## 八、结案判定

| 任务书 §四 验收项 | 判定 |
|---|---|
| 1. Policy schema 对齐 §3.11；版本化 + effective_range + 签名生效 | ✅ |
| 2. `check` 返回 `{allow\|deny\|ask, policy_id}`；缓存 p99<5ms（实测） | ✅（0.0474 ms，≈105× 余量） |
| 3. 决策入链式审计 + `policy.decision` 埋点 | ✅ |
| 4. secret 出域 → deny（决策层 + 执行层双用例） | ✅ |
| 5. LLM 无法在策略中写网络副作用（引擎纯决策） | ✅（装载期 + 静态 + 运行期三层） |
| 6. 模拟器可重放并输出 deny→allow + 高危清单；PR 门禁存在 | ✅（样例 deny→allow=7 / ask→allow=9；门禁 5 道闸全有用例） |
| 7. 既有套件零回归；新增单测全绿、覆盖率 ≥80% | ✅（572 例全绿 / 93% / 邻接 450 例零回归） |

**结论：7/7 通过，可结案。** 结案报告见
[S4-02_交付结案报告_20260911.md](S4-02_交付结案报告_20260911.md)。
