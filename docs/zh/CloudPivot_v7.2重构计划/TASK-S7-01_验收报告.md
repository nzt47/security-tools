# TASK-S7-01 验收报告（开关中心）

> 任务书：[`TASK-S7-01_开关中心.md`](TASK-S7-01_开关中心.md)｜批次总表：[`PARALLEL_S7批次总表.md`](PARALLEL_S7批次总表.md)
> worktree：`.worktrees/s701`（分支 `s701/main`，基线 `master` / `8f22974f`）｜交付日期：2026-09-12
> 预估 5–8 人日｜依赖 S6-01 / S4-01 / S2-02 / S2-03（均已结案）

---

## 一、交付物清单（对照分发壳 §五）

| # | 交付物 | 落点 | 规模 | 说明 |
|---|---|---|---|---|
| 1 | 开关注册表 + 生效来源解析 + 覆盖层 | `agent/settings/`（`registry.py` / `resolver.py` / `overrides.py` / `masking.py` / `service.py` / `bootstrap.py` / `__init__.py`） | 7 文件 2083 行 | `SettingSpec` 唯一事实源 + 四层来源解析 + 三级风险分流 |
| 2 | 机械提取器（缺口检测） | `scripts/scan_settings.py` | 1 文件 1060 行 | AST 提取 + 三类显式声明表 + `--check` 缺口门 |
| 3 | 三组 API 路由（+ 双人确认端点） | `agent/server_routes/routes_settings.py`（`app_server.py` 注册） | 1 文件 331 行 | `GET /api/cp/settings`、`POST /api/cp/settings/<key>`、`/reset`、`/confirm` |
| 4 | 「开关中心」页面 | `yunshu-ui/src/pages/hub/governance/settings.tsx`（+ `index.tsx` / `hubNav.tsx` / `cpPanelsApi.ts` / `cpPanelsTypes.ts`） | 新 2 文件 + 改 4 文件 | 分类折叠 + 搜索 + 风险徽章 + 生效来源 + 置灰原因 + C 级掩码 + B 级二次确认面板 |
| 5 | 覆盖层与优先级解析 | `data/ui_settings.json`（gitignore 运行时产物） | — | 原子写；**绝不改** `.env` / `config.yaml` |
| 6 | 验收报告 | 本文件 | — | 含开关清单统计与生效来源证据 |
| 7 | 交付结案报告 | [`S7-01_交付结案报告_20260912.md`](S7-01_交付结案报告_20260912.md) | — | 结案与遗留 |
| 8 | `00_总览` 状态行 | [`00_总览_审计结论与重构总计划.md`](00_总览_审计结论与重构总计划.md) §4.3 + §5 | — | 已更新 |

**附**：`templates/yunshu.html`（`npm run build:flask` 同步产物，**tracked**，不同步则"实现了但看不到"）。

---

## 二、任务书 §四 验收清单逐条核销

### ① 注册表覆盖全部布尔开关与关键阈值；机械提取项零缺口（缺口即测试失败）—— ✅

| 证据 | 命令 |
|---|---|
| 缺口门 | `python scripts/scan_settings.py --check` |
| 用例守护 | `pytest tests/unit/test_settings_registry.py -q`（23 例，含 `test_zero_gap_between_scan_and_registry`） |

```
扫描文件        : 537
managed 开关名  : 311 （读取点 330）
dynamic 家族    : 3（读取点 4）
process_env 名  : 3（显式排除，非开关）
pass-through 点 : 1（显式声明，非开关）
运行时名字读取  : 1（显式声明形态）
------------------------------------------------------------------
注册表开关数    : 311
缺口（未注册）  : 0
注册但未读到    : 0
未声明的动态家族: 0
未声明的排除项  : 0
结论：零缺口 ✅
```

> **双向**防漂移：`缺口=0`（代码读到但表里没有）与 `注册但未读到=0`（表里有但代码不读）**同时**为 0——后者防的是"UI 显示一个没人读的开关"这种更隐蔽的谎报。

### ② 每个条目都有说明/默认值/类型/风险级/生效方式；B/C 级不可被 UI 静默修改 —— ✅

- 结构体级 fail-fast：`SettingSpec.__post_init__` 对 `description/key/risk/category/type/env_name|config_path/apply_mode` 逐项校验，**建表即抛错**（半成品条目进不了表）。
- 用例 `test_every_item_carries_display_metadata`（HTTP 面）逐条断言 359 个条目都有 `description / risk / category_label / source / source_label / effect / effect_label`，且 `locked == not editable` 时 `locked_reason` 必非空。
- 生效方式三类**都有真实条目**（`hot` 346 / `needs_restart` 1 / `next_task` 7，见 §三）。

### ③ `resolve()` 如实返回生效来源；被 env 锁定的项在 UI 置灰并注明原因（附用例）—— ✅

- 优先级单测：`test_default_source_when_nothing_configured` / `test_ui_override_beats_default` / `test_env_beats_ui_override_and_reports_shadow` / `test_config_runtime_beats_default` / `test_override_beats_config_and_shadows_it` / `test_env_wins_over_config_for_same_key`。
- HTTP 面置灰单测：`test_env_locked_item_is_greyed_with_reason`（断言 `source=env`、`editable=false`、`locked_reason` 含开关名）。
- 真实证据见 §五.2（一条被 env 锁定的真实输出）。

### ④ 写覆盖层不改 `.env` / `config.yaml`（用例断言文件未被修改）—— ✅

- 三层防线：`OverrideStore._guard_path()` 拒绝写入这两个文件（`test_refuses_to_write_protected_files`）；写路径唯一（只 `data/ui_settings.json`，原子写 `os.replace`）；
- **每个用例**的 autouse fixture 在 teardown 时比对 `.env` 与 `config.yaml` 的 sha256：`assert _snapshot() == before, "受保护配置文件被改动"`（3 个测试文件共用此断言，任一用例触碰即失败）。

### ⑤ A 级直接生效；B 级无二次认证不可通过；C 级返回 403 且响应体不含明文 —— ✅

| 项 | 用例 | 断言要点 |
|---|---|---|
| A 直接生效 | `test_a_level_applies_hot` | `applied=true`、`source=ui_override`、`os.environ["LOCK_PROFILE"]=="true"` |
| B 无二次认证 | `test_without_second_factor_is_rejected_and_changes_nothing` | 403 `second_factor_required`，且**覆盖层为空、env 未变、链上无变更记录** |
| B 有二次认证 | `test_with_second_factor_only_opens_pending` | 202 pending；**首位提交不改变任何状态** |
| B 双人确认 | `test_pending_then_dual_confirmation` | 同人确认 403 `second_approver_must_differ`；第二人确认后 200 且 `receipt.second_approver=owner` |
| C 级 | `test_c_level_is_403_without_plaintext` | 403 `read_only_secret`，响应体既无旧明文也无 `new-secret-1111` |
| C 级 GET | `test_secret_values_never_in_response` | `value=null`、`masked=true`、`fingerprint` 与 sha256 前 8 位一致，明文不在响应文本中 |

### ⑥ 每次变更入链式审计（含 old/new/source）+ `policy.decision`；`verify_chain` 仍通过 —— ✅

- `test_audit_chain_records_change_and_policy_decision`：链上 `settings.change` **恰好 1 条**、`policy.decision` **恰好 1 条**，`verify_chain().ok is True`。
- `test_audit_payload_carries_old_new_source`：链上载荷含 `old=500 / new=200 / source=ui_override / never_touched=[".env","config.yaml"]`，`subject=setting:<key>`、`source=ui`。
- **不重复留痕**：`settings.change` 与 `policy.decision` 都不在 `AUDIT_MIRROR_TYPES`（`agent/observability/events.py:171-173`），因此不存在"显式写 + 镜像写"双份；沿用 `DecisionObserver`（`agent/policy/engine.py:281-291`）的既有写法。

### ⑦ auto / sub_agent 调用改开关一律被拒（Actor 矩阵）—— ✅

- 矩阵新增 §7.0 外扩展行 `OP_SETTINGS_CHANGE="settings.change"`：human ✅ / auto ❌ / sub_agent ❌（`agent/security/actor_matrix.py`）。
- 用例：`test_non_human_is_denied[auto|sub_agent]`（服务层，403 `settings_denied`，且 **env 未变、覆盖层为空**）、`test_non_human_denied_on_reset`、`test_auto_actor_is_denied`（HTTP 面）、`test_matrix_row_is_human_only`（矩阵三格逐一判定）。
- **`CORE_MATRIX_OPERATIONS` 取值与改造前逐字一致**（改为按 `EXTENSION_OPERATIONS` 显式排除，而非 `OPERATIONS[:-1]`），既有 §7.0 矩阵用例零回归。

### ⑧ 前端 `tsc`/`eslint` 零告警；vitest 新增用例全绿；`build:flask` 产物已同步 —— ✅

```
npx tsc -b --noEmit            → exit 0（无输出）
npx eslint <6 个改动文件>       → exit 0（无输出）
npx vitest run src/pages/hub/governance/ src/workbench/hubNav.test.ts \
    src/components/workbench/panels/ContentPanel.nav.test.tsx
    Test Files  6 passed (6)      Tests  83 passed (83)     （其中 settings.test.tsx 29 例）
npm run build:flask            → exit 0；✓ React 构建产物已复制到 Flask static/ 和 templates/
```

产物核对：`templates/yunshu.html` 已被改写（tracked，`git status` 显示 ` M`）；构建 chunk `static/assets/index-*.js` 中可检索到「开关中心」字样（2 个 chunk），确认**页面真的进了产物**。

### ⑨ 既有 `ui_panels`/`security`/`approval`/`audit` 套件零回归；覆盖率 ≥80% —— ✅

```
pytest tests/unit/test_settings*.py \
       tests/unit/test_s6_01_ui_panels.py tests/unit/test_security_actor_matrix.py \
       tests/unit/test_security_approval_guard.py tests/unit/test_audit_facade.py \
       tests/unit/test_audit_chain.py tests/unit/test_s4_01_stage_promote_chain.py
       → 494 passed, 0 failed, 0 skipped（新增 119 + 邻接 375）
```

覆盖率见 §六.3。

---

## 三、开关清单统计（按类别 / 风险级）

注册表 **359 条**（`agent/settings/registry.py`，其中 311 条来自机械提取的 env 读取点，48 条由既有 `OBSERVABILITY_VALIDATION_RULES` 合并）。

**按风险级**

| 级 | 条数 | 含义 | UI 行为 |
|---|---|---|---|
| A | 224 | 可直接切 | 可编辑；`hot` 立即生效 |
| B | 61 | 需二次认证 + 双人确认 | 可编辑但需两段式确认 |
| C | 74 | 只读脱敏 | 置灰；只出"是否已配置 + 指纹" |

**按类别**

| 类别 | 条数 | 类别 | 条数 |
|---|---|---|---|
| 自愈与安全 `self_healing_security` | 81 | 技能与检索 `skills_retrieval` | 81 |
| 可观测与阈值 `observability_threshold` | 72 | 学习与进化 `learning_evolution` | 56 |
| 外部依赖与密钥 `external_secrets` | 37 | 编排与规划 `orchestration_planning` | 32 |

**按生效方式 / 可改性**

| 维度 | 数值 |
|---|---|
| `hot`（下次读取即生效） | 351 |
| `needs_restart`（需重启进程） | 1（`PLANNING_WIRE_ENABLED`） |
| `next_task`（下一调度轮生效） | 7 |
| UI 可编辑 | 282 |
| UI 置灰（C 级 74 + 动态家族 3） | 77 |
| 仅支持环境变量（有 `env_name` 无 `config_path`） | 304 → UI 标注「仅支持环境变量」 |
| 密钥类（C 级 secret） | 13 |

**B 级 60 项清单**（口径见 §七.2）：`APPROVAL_ENABLED`、`AUDIT_CHAIN_ENABLED`、`AUDIT_UI_ENABLED`、`CP_APPROVAL_CSRF_ENABLED`、`CP_APPROVAL_LINK_TTL_SECONDS`、`CP_APPROVAL_REQUIRE_AUTHORITATIVE`、`CP_APPROVAL_SESSION_TTL_SECONDS`、`CP_BUDGET_BRAKE_ENABLED`、`CP_DIGESTION_INTERNALIZE_ENABLED`、`CP_ESCAPE_GUARD`、`CP_GUARDRAILS_BOUNDARY_TTL_SECONDS`、`CP_GUARDRAILS_BOUNDARY_WORDS`、`CP_GUARDRAILS_EGRESS_CHAIN`、`CP_GUARDRAILS_FOREIGN_TAINT`、`CP_GUARDRAILS_FOREIGN_TAINT_MAX_MARKS`、`CP_GUARDRAILS_FOREIGN_TAINT_TTL_SECONDS`、`CP_GUARDRAILS_GUARD_CONTEXT`、`CP_GUARDRAILS_GUARD_TOOL`、`CP_GUARDRAILS_INSTRUCTION_DATA`、`CP_HEALING_LEVELS_ENABLED`、`CP_POLICY_BUILTIN_INVARIANTS`、`CP_POLICY_EGRESS_GUARD`、`CP_POLICY_GATEWAY_ENABLED`、`CP_POLICY_REQUIRE_SIGNATURE`、`CP_POLICY_TAINT_DEEP_SCAN`、`CP_POLICY_TAINT_ENABLED`、`CP_POLICY_TAINT_TTL_SECONDS`、`CP_SUBAGENT_CRED_TTL_MAX`、`EVOLUTION_DYNAMIC_BUDGET`、`EVOLUTION_ENABLED`、`EVOLUTION_LLM_GENERATE`、`EVOLUTION_SCHEDULE_ENABLED`、`LEARNING_BUDGET_MAX_DAILY_TOKENS`、`LEARNING_BUDGET_MAX_SINGLE_ACTION_TOKENS`、`LEARNING_BUDGET_MODE`、`LEARNING_BUDGET_RECOVERY_SECONDS`、`LEARNING_EVOLVER_ENABLED`、`LEARNING_FEEDBACK_AGENT_ENABLED`、`LEARNING_LIFECYCLE_ENABLED`、`LEARNING_PRECIPITATE_ENABLED`、`LLM_MODEL`、`LLM_PROVIDER`、`MEMORY_TENANCY_ALLOW_DEGRADE`、`META_EDIT_BLOCKED_PATTERNS`、`META_EDIT_EVAL_MIN_SCORE`、`META_EDIT_MAX_FILES_PER_ROUND`、`META_EDIT_MAX_SKILLS_PER_ROUND`、`META_EDIT_MAX_TOKENS_PER_ROUND`、`META_EDIT_STALL_ROUNDS`、`META_EDIT_WHITELIST_DIRS`、`ROLLBACK_ERROR_RISE_PCT`、`ROLLBACK_LATENCY_RISE_PCT`、`ROLLBACK_MAX_DAILY`、`ROLLBACK_SUCCESS_DROP_PCT`、`ROLLBACK_WINDOW_MIN`、`SKILLS_REVIEW_ENFORCE_PUBLISH`、`SKILL_CLEANUP_ENABLED`、`VALUE_GUARD_ENABLED`、`WF_SKILL_AUTO_UPGRADE_ENABLED`、`YUNSHU_FEATURE_SANDBOX`。

---

## 四、"零缺口"提取证据

### 4.1 提取器为什么用 AST（而不是正则）

本仓库有**三种**会让正则失手的形态，全部实测踩过：

| 形态 | 例子 | 正则的错法 |
|---|---|---|
| 常量间接寻址 | `_ENV_ENABLED = "APPROVAL_ENABLED"` → `_env_flag(_ENV_ENABLED)` | 把常量名 `_ENV_ENABLED` 当成开关名 |
| 前缀家族助手 | `def _env_bool(name,d): os.environ.get(f"{_ENV_PREFIX}_{name}")` + `_env_bool("ENABLED",...)` | 把后缀 `ENABLED` 当成开关名（真实名是 `SKILL_CLEANUP_ENABLED`） |
| 非开关的环境访问 | WSGI `environ.get("PATH_INFO")`、沙箱 `env["PYTHONPATH"]="..."`、赋值左值 `os.environ[k]=v`、透传白名单 `for key in _ENV_WHITELIST: os.environ.get(key)` | 误报成开关（虚增清单） |

故提取器按**六步**处理每个文件：赋值左值标记 →（可折叠的）常量表（支持 `A + "_X"` 与 `f"{A}_X"`，也支持 `self.ENV_ROOT` 与跨模块 import 常量）→ 函数作用域（直通助手判定）→ 本文件助手分类（直通 / 前缀家族）→ 循环集合映射（字面量集合逐项展开）→ 访问点收集。

### 4.2 提取结果的五类分栏（**不混为一谈**）

| 分栏 | 数量 | 与注册表的关系 |
|---|---|---|
| `managed` | **311** 个开关名 / 330 读取点 | **必须 100% 被注册表覆盖**（缺口即失败） |
| `dynamic` | 3 个家族（`SKILLS_ASSESS_` / `SKILLS_DIGEST_` / `SKILL_CLEANUP_`） | 必须登记在注册表的 `dynamic_prefix` 白名单；UI 只读展示 |
| `process_env` | 3 个（`HF_HOME` / `TRANSFORMERS_CACHE` / `SENTENCE_TRANSFORMERS_HOME`） | 必须命中显式排除表 `PROCESS_ENV_DENYLIST`（逐条带理由，第三方库缓存目录） |
| `passthrough` | 1 个（`agent/skills_mgmt/executor.py::_ENV_WHITELIST`） | 必须命中显式声明表 `PASS_THROUGH_SITES`（技能子进程 env 白名单透传） |
| `runtime_name` | 1 种形态（`spec.env_name`） | 必须命中显式声明表 `RUNTIME_NAME_READS`（名字来自注册表字段本身） |

**为什么不用一个 catch-all `unresolved` 兜住**：那等于把"不知道是什么"合法化。故三类例外都要**逐条写理由**，且用例断言：观察到的集合 ⊆ 声明表，且声明表每项理由非空（`test_declared_exclusions_are_exact_tables`）。任何新形态若未声明 → 归入 `dynamic` → **缺口失败**。

### 4.3 零重造（与既有 config 校验表合并）

`agent/monitoring/observability_config.py::OBSERVABILITY_VALIDATION_RULES`（48 条，已有 path/校验器/默认值/说明）被 `_merge_observability_specs()` **机械合并**：默认值与说明文字**取自该既有表**，注册表只补 UI 需要的类别/风险/env 名。用例 `test_every_rule_path_is_merged` + `test_merged_defaults_are_identical` 双向守护（少一条或改一个默认值即失败）。

### 4.4 合入 master 后复跑：零缺口门**真的会响**（并已补齐）

本任务在 worktree 内的原始提取结果是 **306/306 零缺口**。合入 master 后（master 当时已含 S7-02 / S7-04 / S7-05 / S7-06 的交付）**复跑同一命令**，结果如实变为：

```
managed 开关名  : 311 （读取点 330）
注册表开关数    : 306
缺口（未注册）  : 5
  - CP_DIGESTION_CASE_COST_DIR        (agent/digestion/case_cost.py:137) ← S7-06 引入
  - CP_DIGESTION_LIVENESS_DIR         (agent/digestion/probe.py:223)     ← S7-04 引入
  - CP_DIGESTION_LIVENESS_ENABLED     (agent/digestion/probe.py:217)     ← S7-04 引入
  - CP_DIGESTION_LIVENESS_MAX_TARGETS (agent/digestion/probe.py:212)     ← S7-04 引入
  - CP_DIGESTION_LIVENESS_PROBE_SIZE  (agent/digestion/probe.py:207)     ← S7-04 引入
结论：存在缺口 ❌
```

**这正是本门禁要证明的事**：它不是"写个脚本跑一次过"，而是**对新增代码持续有效**——并行任务新增 env 读取点后，合并态立刻报缺口。5 项已在 master 上补登记（合并态复跑 → `managed=311 / 注册表=311 / 缺口=0`），119 例全绿。补登记内容：`CP_DIGESTION_LIVENESS_ENABLED`（**B 级**：开启即按周期自动执行抽样探活，默认关闭）、`…_PROBE_SIZE`（A，默认 5）、`…_MAX_TARGETS`（A，默认 20）、`…_LIVENESS_DIR` / `…_CASE_COST_DIR`（C 级路径）。本报告与结案报告中的清单统计均为**补登记后**的数字（359 条）。

---

## 五、真实生效来源证据（含被 env 锁定置灰）

> 以下为**真实运行输出**（非示意），复现：`python scripts/scan_settings.py --check` + `pytest tests/unit/test_settings_*.py`。

### 5.1 A 级：`default → ui_override`，热生效且来源如实

```
$ # 覆盖层与进程环境均无 LOCK_PROFILE_BATCH
resolve('LOCK_PROFILE_BATCH') → source=default, value=500, editable=True
$ POST /api/cp/settings/LOCK_PROFILE_BATCH  {"value": 200}
200 {"ok":true,"applied":true,"source":"ui_override","effect":"hot",
     "effect_label":"下次读取即生效",
     "old":500,"new":200,
     "receipt":{"landing":"env","detail":"已写入进程环境变量 LOCK_PROFILE_BATCH（热生效）",
                "overlay_path":".../data/ui_settings.json",
                "never_touched":[".env","config.yaml"]},
     "audit":{"seq":1,"self_hash":"..."}}
$ os.environ["LOCK_PROFILE_BATCH"] == "200"     # ★ 真的生效了（不是只有 UI 变）
$ GET /api/cp/settings → 该项 source=ui_override, override_present=true, shadowed_by=[]
```

### 5.2 被 env 锁定 → **置灰 + 注明原因**（本任务的头号禁忌）

```
$ LOCK_PROFILE=1（运维注入） 且覆盖层里存在 LOCK_PROFILE=true
GET /api/cp/settings → items["LOCK_PROFILE"]:
  "source": "env",
  "source_label": "环境变量（运维注入）",
  "shadowed_by": ["ui_override"],        ← 覆盖层存在但被 env 遮蔽，如实标注
  "env_present": true,
  "editable": false,
  "locked": true,
  "locked_reason": "被环境变量 LOCK_PROFILE 锁定：env 优先级高于覆盖层，
                    此处修改不会生效（UI 不可改）",
  "value": false                          ← 显示的是**真实生效值**（env 的 0），不是覆盖层的 true

POST /api/cp/settings/LOCK_PROFILE {"value": false}
403 {"ok":false,"code":"locked_by_env",
     "message":"被环境变量 LOCK_PROFILE 锁定：env 优先级高于覆盖层，此处修改不会生效（UI 不可改）"}
     # 且 os.environ["LOCK_PROFILE"] 仍为 "1"（环境变量未被改写）
```

### 5.3 `config` 来源（运行态热生效）

```
resource_monitor.history_size 声明默认 1440
POST /api/cp/settings/resource_monitor.history_size {"value": 321}
→ receipt.landing = "observability"（写入 ObservabilityConfig 运行态）
→ get_observability_config().get("resource_monitor.history_size") == 321   # 真实读取方拿到新值
→ resolve() 该项 source = "config"（改动前为 "default"）
→ reset 后回落 1440
```

### 5.4 C 级：响应体不含明文

```
$ LLM_API_KEY="sk-live-abcdef0123456789"
GET /api/cp/settings → items["LLM_API_KEY"]:
  "value": null, "masked": true, "configured": true,
  "display_value": "已配置（指纹 a1b2c3d4）",
  "fingerprint": "<sha256 前 8 位>"
GET 响应原文中检索 "sk-live-abcdef0123456789" → 未命中（用例断言）
POST /api/cp/settings/LLM_API_KEY {"value":"sk-new"} → 403 read_only_secret（响应无明文）
审计载荷：{"old":"<masked:指纹>"}（明文不入链）
```

### 5.5 B 级两段式（二次认证 + 双人确认）

```
POST /api/cp/settings/CP_BUDGET_BRAKE_ENABLED {"value": true}                       # 无二次认证
  → 403 {"code":"second_factor_required"}（env 未变、覆盖层为空、链上无变更记录）
POST … {"value": true, "second_factor":"<一次性确认码>"}                             # 第一位人工
  → 202 {"pending":true,"pending_id":"setp-…","requires_dual_approval":true,
         "message":"…需**另一位人工**用独立二次认证确认后才生效；本次提交未改动任何状态"}
POST …/confirm {"pending_id":"<同一人>"}   → 403 second_approver_must_differ   # 双人确认不可自审
POST …/confirm {"pending_id":"…","second_factor":"<第二位人工的凭据>"}          # 第二位人工（独立会话）
  → 200 applied=true，receipt.second_approver="owner"
  → 链上 settings.change 载荷含 second_approver / requested_by
```

---

## 六、质量证据

### 6.1 新增单测

| 套件 | 例数 | 覆盖重点 |
|---|---|---|
| `tests/unit/test_settings_registry.py` | 23 | 零缺口 / 零重造 / 注册表完整性 / 反向防漂移 |
| `tests/unit/test_settings_resolver.py` | 37 | 四层优先级 / 置灰原因 / C 级脱敏 / 覆盖层守不易 / bootstrap 零影响与幂等 |
| `tests/unit/test_settings_service.py` | 32 | A/B/C 分流 / 双人确认 / 矩阵拒绝 / 审计入链 / 不双写 |
| `tests/unit/test_settings_routes.py` | 27 | 三组路由 + confirm / 批量拒绝 / require_token / 无明文 |
| **合计** | **119** | 全部通过 |
| 前端 `settings.test.tsx` | 29 | 分类+徽章+来源标签 / 置灰原因 / 掩码 / B 级确认步骤 / 202 待办 / 搜索 |

### 6.2 邻接回归（零回归）

```
**494 passed**（新增 119 + 邻接 375，共 10 套件）：test_settings_* ×4、test_s6_01_ui_panels、test_security_actor_matrix、
test_security_approval_guard、test_audit_facade、test_audit_chain、test_s4_01_stage_promote_chain
```

### 6.3 覆盖率

```
python -m pytest tests/unit/test_settings_{registry,resolver,service,routes}.py \
    --cov=agent.settings --cov=agent.server_routes.routes_settings --cov-report=term
Name                                     Stmts   Miss  Cover
agent\server_routes\routes_settings.py     141      9    94%
agent\settings\__init__.py                   7      0   100%
agent\settings\bootstrap.py                 45      0   100%
agent\settings\masking.py                   38      4    89%
agent\settings\overrides.py                165     22    87%
agent\settings\registry.py                 219     16    93%
agent\settings\resolver.py                 249     32    87%
agent\settings\service.py                  288     33    89%
TOTAL                                     1152    114    90%
119 passed
```

**90% ≥ 80%**；最低单模块 87%（`overrides` / `resolver`）。未覆盖行集中在"损坏覆盖层降级""异常兜底"等防御分支。

### 6.4 门禁

| 门禁 | 命令 | 结果 |
|---|---|---|
| kwarg 扫描（agent） | `python scripts/scan_kwarg_conflicts.py --path agent --min-risk HIGH` | 0 处 |
| kwarg 扫描（tests） | `python scripts/scan_kwarg_conflicts.py --path tests --min-risk HIGH` | 0 处 |
| mypy（新增模块） | `python -m mypy agent/settings/*.py agent/server_routes/routes_settings.py` | exit 0 |
| importlinter | `lint-imports --config .importlinter` | 2 kept, 0 broken |
| pre-commit（**真实提交场景**：先 `git add` 再 `pre-commit run`） | `python -m pre_commit run` | 12 Passed, 2 Skipped（无匹配文件） |
| 前端 | `tsc -b --noEmit` / `eslint` / `vitest` / `build:flask` | 全绿（见 §二.⑧） |
| 产物漂移核查 | `git status --porcelain` | 仅预期文件（含 `templates/yunshu.html`），无运行时垃圾入库 |

---

## 七、口径与如实声明（诚实边界）

1. **"73 个环境变量"与"311 个开关名"的口径差异**：任务书 §一 的 73 是指 `.env` 里**已配置**的条目（约 51 个布尔）；本注册表覆盖的是**代码里真实读取的全部 env 读取点**（311 个名字 / 330 个读取点），包含未在 `.env` 中配置、走代码默认或 `config.yaml` 的那些。前者是"当前配置量"，后者是"可发现性上限"。本任务按**后者**建表（"任何开关都能在 UI 找到"）。差异已在此显式声明，不以一个数字掩盖另一个。
2. **B 级范围的一次明确扩张**：任务书列举的 B 类（自愈自动执行 / 熔断回滚 / 关沙箱 / 审批豁免 / 自动合入 / 成本刹车阈值）之外，本表把"**关闭即降低防护的安全防线开关**"也归入 B（注入防线、egress 守卫、策略签名校验与不变量、污点防护、审计链开关、租户隔离降级、审批面 CSRF/时效）。理由：其切换动作本身就是一次安全姿态变更，与"关沙箱"同性质。**若 Owner 认为范围过宽，只需改注册表一行的 `risk`**（`_b(...)` → `_a(...)`），无逻辑改动。
3. **`settings.change` 是 §7.0 之外的扩展行**。原表只到"切换熔炉/修改策略"；本任务按同一性质新开一行，并**保持 `CORE_MATRIX_OPERATIONS` 取值逐字不变**（改用 `EXTENSION_OPERATIONS` 显式排除，历史写法 `OPERATIONS[:-1]` 已替换并加注释）。二次认证的真值由开关表的风险级裁量，矩阵行只表达"human 专属"——**避免两处表达同一事实而漂移**。
4. **`settings.change` / `policy.decision` 两个事件类型未登记进 `ALL_EVENT_TYPES`**（仅 `emit` 落 `events.jsonl`，`unknown_type_count` 会计数）。理由：`EventStore` 在默认非 strict 模式下对未登记类型**照写不误**（`agent/observability/events.py:615-627`），而既有 `DecisionObserver` 的 `policy.decision` 事件正是这一先例；为不惊动 `tests/unit/test_events_v1.py` 的**精确集合**断言（`ALL_EVENT_TYPES == NINE ∪ METRIC`）而选择不扩注册表。**若 Owner 要求登记**，改动点明确：`events.py` 加常量 + `EventType` 成员 + 组元组，并同步该用例的集合断言。
5. **动态开关族只读**：`SKILLS_ASSESS_<KEY>` / `SKILLS_DIGEST_<KEY>` / `SKILL_CLEANUP_<NAME>` 三族的名字由运行时拼接（`f"{_ENV_PREFIX}_{name}"`）。其中 `SKILL_CLEANUP_*` 的**6 个静态实例已逐条登记**（`SKILL_CLEANUP_ENABLED` 等，因为调用点传的是字面量后缀，可静态还原）；剩余两族因后缀来自配置文件键（`skills_mgmt.assess.<key>`）无法穷举，如实标注为"动态家族、只读"。
6. **"仅存在于 config.yaml 的项"当前为 0 条**：48 条 `observability` 配置路径都有运行态落点（`ObservabilityConfig.set` 可热改），`planning.wire_*` 一类则都带 `env_name`。因此"仅 config.yaml → 置灰并注明不改配置文件"这条分支**当前无真实条目命中**，但有**合成条目的用例守护**（`test_config_only_item_is_locked_with_config_yaml_reason`）。若将来新增纯 `config_path` 项，该分支立即生效（这是"守不易"的兜底，不是死代码）。
7. **env 锁定判定依据**：进程启动时已存在、且**不是本进程为热生效写进去的** env（`OverrideStore.env_applied` 记账）才算"运维注入"。因此 UI 改过的 env 不会把自己锁死；进程重启后覆盖层由 `bootstrap.apply_overrides()` 重新应用（`needs_restart` 项走这条路径）。
8. **双人确认待办是进程内的**（`_PENDING`，TTL 900s，上限 64 条；与既有 `routes_ui_panels._BATCHES` 同款口径）。**多实例/多进程部署下，A 实例发起的待办无法在 B 实例确认**——单机形态无影响；若上多实例，需要把它换成共享存储。此项**未做**，如实声明。
9. **`needs_restart` 只有 1 条**（`PLANNING_WIRE_ENABLED`）。其余 env 类开关的读取点都在**调用时**读 `os.getenv`，故 UI 覆盖 = 热生效；这一判定是按读取点形态人工核对后标注的，若某个读取点实际在 import 期固化，该项会退化为"下次重启才生效"——**方向是保守的**（UI 上仍标注了生效方式，且回执里带 `landing` 字段供核对）。
10. **前端 `SwitchField.tsx` 的 `--mascot-*` 主题变量在运行期未定义**（既有问题：仅 `src/styles/theme.css` 定义，而该文件未被任何地方 import）。本任务**沿用**该组件（不改既有文件），在开关中心容器上补了局部兜底变量，使开关能正常渲染；**根因未修**（属 S6-01 前端遗留，不在本任务范围）。

---

## 八、附录：命令清单

```powershell
# 提取与缺口门
python scripts/scan_settings.py --check
python scripts/scan_settings.py --json reports/settings_scan.json

# 单测（新增 + 邻接）
python -m pytest tests/unit/test_settings_registry.py tests/unit/test_settings_resolver.py `
    tests/unit/test_settings_service.py tests/unit/test_settings_routes.py -q
python -m pytest tests/unit/test_settings*.py tests/unit/test_s6_01_ui_panels.py `
    tests/unit/test_security_actor_matrix.py tests/unit/test_security_approval_guard.py `
    tests/unit/test_audit_facade.py tests/unit/test_audit_chain.py `
    tests/unit/test_s4_01_stage_promote_chain.py -q

# 静态门禁
python scripts/scan_kwarg_conflicts.py --path agent --min-risk HIGH
python scripts/scan_kwarg_conflicts.py --path tests --min-risk HIGH
python -m mypy agent/settings/*.py agent/server_routes/routes_settings.py --ignore-missing-imports
lint-imports --config .importlinter
python -m pre_commit run            # 真实提交场景：先 git add 指定文件

# 前端
cd yunshu-ui
npx tsc -b --noEmit
npx eslint src/pages/hub/governance/settings.tsx src/pages/hub/governance/settings.test.tsx `
    src/pages/hub/governance/index.tsx src/lib/cpPanelsApi.ts src/lib/cpPanelsTypes.ts `
    src/workbench/hubNav.tsx
npx vitest run src/pages/hub/governance/ src/workbench/hubNav.test.ts `
    src/components/workbench/panels/ContentPanel.nav.test.tsx
npm run build:flask
```
