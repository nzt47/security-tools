# Q7 审计链 append API 能否写入「注册表变更」事件类型？

> 审计范围：`agent/audit/`（8 个模块）、`data/audit/` 实况、`agent/skills_mgmt/`、`agent/descriptors/registry.py`、`agent/observability/events.py`
> 审计方式：只读（sqlite `mode=ro`、Select-String、直接读取）。未启动服务、未跑 pytest、未修改任何被审文件。
> 本报告所有数字均为本次实测值，与代码注释中的声称值不符处已单独标注。

---

## 0. 结论摘要

| # | 问题 | 实测结论 |
|---|---|---|
| 1 | append 的事件类型是自由字符串还是白名单？ | **完全自由字符串**。`chain.append` 仅校验 `action` 非空（`chain.py:1654-1655`）；DB 表 `action TEXT NOT NULL` 无 CHECK 约束（`chain.py:1264-1280`）。**唯一白名单是 `source`**（`chain.py:1656-1658`，4 值）。 |
| 2 | 现有事件类型清单 | 无枚举类型定义在 audit 包内。实测链上 **119 个不同 action**；代码里另有 26 个 `AUDIT_ACTION_*` 常量、4 个 `descriptor.*` 字面量、以及 2 个动态拼接模板。 |
| 3 | 能否写入「注册表变更」事件 | **能，且零改动**——已在生产链上大量存在（`descriptor.register` 51,782 条等，合计 52,512 条 = 全链 73.8%）。但 **技能注册表启停（SkillRegistry.set_enabled）当前完全无留痕**，若要覆盖该路径需新增调用点（见 §3 最小改动清单）。 |
| 4 | 审计链数据实况 | `audit_chain.db` **53,948,416 B**，**71,160 条**，seq 1..71160 连续无重复；全链 71,160 条独立重算 **0 处断链**（0 seq 缺口 / 0 prev_hash 不符 / 0 self_hash 不符）。**但：DB 链头落后于真实链头 1,100 条**（seq 71161..72260 只在 `audit_chain.db.seqjournal` 里）；9 条日根全部签名有效，**其中 1 条（2026-09-21）与当日实际记录集合不一致**；**8 个有记录的 UTC 日没有 Merkle 根**（含占全链 71% 的 2026-09-23）。 |
| 5 | 性能/容量 | `append` 相对链长是 **O(1)**（实测哈希耗时 **0.0224 ms/条**；无任何全量重算）。**读路径是 O(N)**：`facade.recent(limit=50)` 实际全表读取 71,160 行；`stats()` 全表遍历；`verify_chain()` 全链重算实测 **1.641 s**。磁盘实测 **758 B/条**，按 1 万条/日 ≈ **7.6 MB/日 ≈ 2.8 GB/年**（不含 WAL 与索引膨胀）。 |
| 6 | 敏感数据 | **未发现任何 API key / 令牌 / 用户原文**：`sk-`/`AKIA`/`gh[pousr]_`/`JWT`/`Bearer <token>` 形态在 71,160 条 payload 中命中 **0** 次；UI 请求体只落 sha256 指纹（`ui_middleware.py:380-385`）。**但** 存在三处结构性风险：`technical=` 通道**绕过脱敏**（`facade.py:319-320`）、`chain.append` 本身**不做任何脱敏**（`migration.py:583-585` 直连）、以及原始工具调用参数/命令/本地路径确实入链（`approval.submit` 的 `description` 含工具入参摘要与原文命令）。 |

---

## 1. append API 的确切签名 + 事件类型是否白名单

### 1.1 门面入口（facade.py，唯一推荐写入入口）

```python
# agent/audit/facade.py:276-280
def record(self, action: str, actor: Optional[str] = None, subject: str = "",
           payload: Optional[Dict[str, Any]] = None, *, source: str = SOURCE_AGENT,
           trace_id: str = "", workspace_id: str = "", status: str = "",
           ts: Any = None, extra: Optional[Dict[str, Any]] = None,
           technical: Optional[Dict[str, Any]] = None) -> Optional[AuditEntry]:
```

模块级便捷入口（`facade.py:490-493`）：

```python
def record(action: str, actor: Optional[str] = None, subject: str = "",
           payload: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Optional[AuditEntry]:
```

门面内部对 `action` 的处理——**原样透传，无任何校验、无归一化、无白名单**：

```python
# agent/audit/facade.py:301-331（节选）
        if not self._enabled:
            return None
        try:
            src = str(source or SOURCE_AGENT)
            if src not in SOURCES:
                src = SOURCE_AGENT              # ← 只有 source 有白名单，且非法即静默降级
            ...
            entry = chain.append(
                action=str(action), actor=resolved_actor, subject=str(subject or ""),
                payload=body, source=src, ...)
```

### 1.2 链入口（chain.py，真正的 append）

```python
# agent/audit/chain.py:1612-1616
def append(self, action: str, actor: str, subject: str = "",
           payload: Optional[Dict[str, Any]] = None, *,
           source: str = SOURCE_AGENT, trace_id: str = "",
           workspace_id: str = "", ts: Any = None,
           schema_version: int = SCHEMA_VERSION) -> AuditEntry:
```

**全部入参校验（就这 4 条）**：

```python
# agent/audit/chain.py:1650-1658
        if self._role != "writer":
            raise ReadOnlyChainError("只读实例（role='reader'）不可写入审计链")
        if self._closed:
            raise AuditEntryError("审计链已关闭，拒绝追加（请重新 get_audit_chain）")
        if not action:
            raise AuditEntryError("action 不能为空")
        src = str(source or SOURCE_AGENT)
        if src not in SOURCES:
            raise AuditEntryError(f"非法 source: {src}（允许 {sorted(SOURCES)}）")
```

唯一白名单常量（`chain.py:140-144`）：

```python
SOURCE_AGENT = "agent"
SOURCE_UI = "ui"
SOURCE_SYSTEM = "system"
SOURCE_MIGRATION = "migration"
SOURCES = frozenset({SOURCE_AGENT, SOURCE_UI, SOURCE_SYSTEM, SOURCE_MIGRATION})
```

**`action` 的"校验"只到"非空字符串"为止**；其余三处与 action 相关的代码同样无白名单：

| 位置 | 内容 | 是否有类型白名单 |
|---|---|---|
| `chain.py:289-333` `AuditEntry`（dataclass）+ `validate()` | `self.action = str(self.action or "")`；`validate()` 只判 `if not self.action` | 否 |
| `chain.py:1264-1280` DDL | `action TEXT NOT NULL`（无限长、无 CHECK） | 否 |
| `chain.py:977` `get_audit_chain(db_path=None, **kwargs)` | 无 action 相关参数 | 否 |
| `ui_middleware.py:398-401` `audit_action(action, *, subject, subject_arg, payload_keys, view_args_keys)` | 装饰器参数 `action: str`，注释写"语义化动作名（如 `skill.delete`）" | 否 |
| `ui_middleware.py:136-146` `action_from_request` | 由 endpoint 动态拼 `f"ui.{ep}.{verb}"`，**任意 Flask endpoint 名都会变成 action** | 否 |

### 1.3 实测反证：垃圾 action 确实进了生产链

正因无白名单，生产库中存在明显非语义的 action：

| action | 条数 | 判断 |
|---|---|---|
| `x.y` | 13 | 测试遗留 |
| `global_test_action` | 50 | 测试遗留 |
| `ssrf_probe_test` | 1 | 测试遗留 |
| `escape` | 131 | 来自 `observability/events.py:171 EV_ESCAPE`（事件镜像） |

这 4 个 action 合计 195 条，均可通过 `chain.entries(action=...)` 检索——**说明"能写任意事件类型"是已发生的事实，而非设计承诺**。

---

## 2. 现有事件类型清单

### 2.1 代码中的常量（`AUDIT_ACTION_*`，全仓 26 个，实测 grep）

| 文件:行 | 常量 = 值 |
|---|---|
| `agent/settings/service.py:64-66` | `AUDIT_ACTION_CHANGE="settings.change"` / `AUDIT_ACTION_RESET="settings.reset"` / `AUDIT_ACTION_POLICY="policy.decision"` |
| `agent/digestion/gate.py:98-99` | `digest.acceptance.granted` / `digest.acceptance.drifted` |
| `agent/digestion/internalize.py:164-168` | `digest.internalize.evaluated` / `.promote_pr` / `.manual_submitted` / `.manual_applied` / `.manual_rejected` |
| `agent/digestion/probe.py:162-163` | `digest.liveness.probed` / `digest.liveness.degraded` |
| `agent/digestion/resolutions.py:70` | `resolution.record` |
| `agent/digestion/shadow.py:270-277` | `digest.shadow.observed` / `.degraded` / `.blocked` / `.manual_review` / `.enqueue_rejected` / `.review_stale` |
| `agent/digestion/takeover.py:113-114` | `digest.shadow.takeover` / `.takeover_fallback` |
| `agent/retention/archiver.py:75` | `retention.run` |
| `agent/utils/cross_process_lock.py:323-325` | `lock.degraded` / `lock.timeout` / `lock.conflict` |

### 2.2 代码中的字面量 action（非 `AUDIT_ACTION_*` 常量，关键治理动作）

| 文件:行 | action |
|---|---|
| `agent/descriptors/registry.py:585,648,679,700,726,783,817,881` | `descriptor.register` / `descriptor.unregister` / `descriptor.provenance` / `descriptor.stage` / `descriptor.patch` |
| `agent/skills_mgmt/review_gate.py:86` | `skill.review_waiver_publish` |
| `agent/skills_mgmt/service.py:826` | `f"skill.assess.{kind or 'unknown'}"`（**动态拼接**） |
| `agent/skills_mgmt/lineage.py:378` | `lineage.append` |
| `agent/server_routes/routes_skills_mgmt.py:683` | `skill.publish` |
| `agent/server_routes/routes_skills_mgmt.py:752` | `skill.delete` |
| `agent/observability/events.py:1161` | `envelope.type`（**动态：事件类型直接当 action**） |
| `agent/env_config_manager.py:349` | `f'config.env_{action}'`（**动态**） |
| `agent/server_routes/routes_ui_panels.py:716` | `f"ui.action.{key}"`（**动态**） |
| `agent/guardrails/*` | `guardrails.boundary_blocked` / `guardrails.text_approval_rejected` / `guardrails.egress_chain_blocked` / `egress.blocked` / `egress_blocked` / `guardrails.foreign_text_marked` / `guardrails.taint_blocked` / `guardrails.parameter_contaminated` |

### 2.3 生产库中实际出现过的 119 个 action（Top 40，实测 `SELECT action,COUNT(*) GROUP BY action`）

| action | 条数 | action | 条数 |
|---|---:|---|---:|
| descriptor.register | 51,782 | descriptor.patch | 352 |
| config.env_set | 3,601 | policy.denied | 339 |
| trace.redact | 2,754 | descriptor.provenance | 338 |
| lineage.append | 2,551 | tool.confirm_decision | 323 |
| skill.assess.auto | 1,227 | ui.api_system_prompt_config_preview.post | 260 |
| approval.submit | 911 | model.degraded | 258 |
| trace.closed | 883 | ui.chat.api_sessions_set_current.post | 246 |
| guardrails.foreign_text_marked | 729 | approval.approved | 243 |
| trace.tool.error | 382 | skill.assess.review | 224 |
| healing.triggered | 373 | cost.daily_breaker.opened | 208 |
| lock.degraded | 193 | repair.diagnose | 132 |
| guardrails.taint_blocked | 172 | escape | 131 |
| guardrails.boundary_confirmation_issued | 171 | cost.legacy_track.write_attempt | 127 |
| ui.api_agent_lines_preview.post | 137 | repair.delegate / repair.locate | 108 / 108 |
| policy.taint.marked | 108 | cost.fasting.entered | 104 |
| approval.rejected | 98 | healing.incident | 93 |
| guardrails.boundary_blocked | 92 | guardrails.parameter_contaminated | 87 |
| repair.verify | 84 | ui.admin.api_apply_network_config.post | 79 |
| guardrails.boundary_confirmation_redeemed | 77 | policy.decision | 77 |
| guardrails.egress_chain_blocked | 72 | （其余 79 个 action ≤ 63 条） | |

完整 119 个 action 的导出命令见 §4.3 脚本。

### 2.4 与「事件类型」有关的两处真正白名单（不在 append 路径上）

```python
# agent/observability/events.py:198-200
AUDIT_MIRROR_TYPES = frozenset({
    EV_POLICY_DENIED, EV_HEALING_TRIGGERED, EV_MODEL_DEGRADED, EV_ESCAPE,
})
# agent/observability/events.py:1151
        if not self._audit_mirror or envelope.type not in AUDIT_MIRROR_TYPES:
            return
```

- **事件镜像入链有白名单**（4 个事件类型），不在表里的事件类型**不会被镜像**；
- 但 `EventStore.emit()` 本身**不校验**事件类型（`events.py:837-851` 只做 `normalize_type`；`EventType.isin()` 定义在 `events.py:242-245` 却无人调用）；
- 结论：**"事件 → 链"这条路是白名单，"直接调 append"这条路不是。**

---

## 3. 「注册表变更」事件写入可行性

### 3.1 结论：**能，且零代码改动**（能力上），但**覆盖度上有一个真实缺口**

| 注册表 | 现状 | 证据 |
|---|---|---|
| 能力台账（descriptor / capability registry） | **已入链**，且是链上最大宗 | `descriptors/registry.py:445-469` `_audit()` 内 `_audit_facade.record(f"descriptor.{action}", ...)`；实测 52,512 条 |
| 技能注册表启停（`SkillRegistry.set_enabled`） | **完全无留痕** —— 该文件 193 行内 **0 处 audit 调用** | `skills_mgmt/registry.py:1-193`（全文已读）；调用链 `registry.py:127 → service.py:1981 → enhancer.py:679-692`，三处均只有 `store.upsert` + 埋点，无链式审计 |
| 技能分类注册表移动（classes move） | 仅以 UI 路由名入链，**无语义 action** | `ui_middleware.py:136-146` 自动生成 `ui.api_skills_mgmt_classes_move.post`（实测 9 条） |
| 设置注册表（settings registry） | 已入链 | `settings/service.py:589-595`（`settings.change`）、`:604-618`（`policy.decision`） |
| Windows 注册表（winreg） | **全仓 0 处引用**（实测 grep：`import winreg` / `HKEY_` / `winreg.` 均无命中） | — |

### 3.2 最小改动清单（按改动量排序）

**方案 A（推荐，1 行 × 2 处）：在技能注册表写路径补链式留痕**

| # | 文件:行 | 改动 |
|---|---|---|
| A1 | `agent/skills_mgmt/registry.py:118-149`（`SkillRegistry.set_enabled`） | 在 `return {"ok": True, ..., "track": "main"}`（:128-129）与 `track: "file_track"`（:138-139）两个成功分支前，各加一次 `facade.record("skill.registry.set_enabled", actor=..., subject=f"skill:{skill_id}", payload={"enabled": enabled, "track": "main"/"file_track"}, source="agent", status="ok")` |
| A2 | `agent/skills_mgmt/enhancer.py:679-692`（`SkillEnhancer.set_enabled`） | 同上；此处是 `SkillRegistry.set_enabled → service.py:1981 → enhancer.set_enabled` 的**唯一落库点**，若要避免双写，只在此处加即可（A1 只处理文件轨分支） |

**方案 B（若要求"事件 → 链"镜像）：必须先扩白名单**

| # | 文件:行 | 改动 |
|---|---|---|
| B1 | `agent/observability/events.py:198-200` | 在 `AUDIT_MIRROR_TYPES` 中加入新的 `EV_SKILL_REGISTRY_CHANGED` 等常量，否则 `events.py:1151` 直接 `return` |
| B2 | `agent/observability/events.py:178-190` | 在 `CORE_EVENT_TYPES`/`GOVERNANCE_EVENT_TYPES` 中登记常量（`ALL_EVENT_TYPES` 由三者相加） |
| B3 | `agent/observability/events.py:213-240`（`EventType` 枚举体） | 补枚举成员 |

**方案 C（只有在需要"强制白名单"时才做，注意这会改变全仓现有 119 个 action 的写入契约）**

| # | 文件:行 | 改动 |
|---|---|---|
| C1 | `agent/audit/chain.py:1654-1655` | 在 `if not action` 之后追加白名单校验（当前**不存在**任何 action 常量表可供引用，需要新建 `ACTION_TYPES` 常量并回填 119 个历史值） |
| C2 | `agent/audit/facade.py:326-331` | 若在门面层做，需在此处加校验；注意 `facade.py:336-342` 是 best-effort `except Exception`，校验失败会被**吞掉并计 failure_count**，不会阻断主路径——"白名单"在此层会变成"静默丢弃" |

> **风险提示（实测）**：方案 C 若照搬 `facade.record` 的异常语义，未登记的 action 不会报错，只会 `failure_count += 1` 并返回 `None`（`facade.py:335-342`）。这与"拒绝非法事件类型"的治理期望相反。

---

## 4. 审计链实际数据实况

### 4.1 `data/audit/` 文件清单（实测 `Get-ChildItem`）

| 文件 | 大小 (B) | 最后写入 | 说明 |
|---|---:|---|---|
| `audit_chain.db` | 53,948,416 | 2026-09-24 00:57:26 | 主链（SQLite，WAL） |
| `audit_chain.db.seqjournal` | 1,304,568 | 2026-09-24 00:57:26 | 跨进程 seq 预留日志（**含未入库的 1,100 条**） |
| `audit_chain.db.lock` | 1,025 | 2026-09-24 00:57:26 | 跨进程锁文件 |
| `audit_chain.db-shm` | 32,768 | 2026-09-25 18:07:48 | WAL 共享内存（进程未干净退出） |
| `audit_chain.db-wal` | 0 | 2026-09-25 18:07:11 | WAL 已 checkpoint 为空 |
| `daily_roots.jsonl` | 8,091 | 2026-09-23 08:00:00 | 每日 Merkle 根（**只读属性 ar--**） |
| `audit_signing_key.pem` | 119 | 2026-09-14 08:00:01 | ed25519 私钥（**只读属性 ar--**，PEM PKCS8 无口令） |
| `audit_20260913/16/17/18/19/20/21.jsonl`、`audit_20260914.jsonl`(`20271019`) | 233–4,194 | 2026-09-13..09-21 | 旧轨（JSONL 双写过渡） |
| `knowledge_audit.jsonl` | 36,400 | 2026-09-23 18:58:10 | 另一套（知识审计，非本链） |

### 4.2 链完整性抽样与全量校验（直接读库，非调用模块）

**脚本（只读，`mode=ro`；已实际执行）**：

```python
import sqlite3, json, hashlib, collections
p = r"C:\Users\Administrator\agent\data\audit\audit_chain.db"
con = sqlite3.connect("file:///" + p.replace(chr(92), "/") + "?mode=ro", uri=True, timeout=5.0)
con.row_factory = sqlite3.Row; cur = con.cursor()
def cj(d): return json.dumps(d, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
def sh(s): return hashlib.sha256(str(s).encode("utf-8")).hexdigest()
run_prev, bad, n = "0" * 64, [], 0
for row in cur.execute("SELECT * FROM audit_chain ORDER BY seq ASC"):
    n += 1
    canon = cj({"seq": row["seq"], "ts": row["ts"], "actor": row["actor"], "action": row["action"],
                "subject": row["subject"], "source": row["source"], "trace_id": row["trace_id"],
                "workspace_id": row["workspace_id"], "schema_version": row["schema_version"],
                "payload": json.loads(row["payload"] or "{}")})
    ph = sh(canon)                                            # payload_hash 重算
    hh = sh("|".join([str(row["seq"]), row["ts"], row["actor"], row["action"],
                      row["subject"], ph, run_prev]))         # self_hash 重算（前驱用重算值）
    reasons = []
    if int(row["seq"]) != n:                reasons.append("seq_gap")
    if row["prev_hash"] != run_prev:        reasons.append("prev_hash")
    if ph != row["payload_hash"]:           reasons.append("payload_hash")
    if hh != row["self_hash"]:              reasons.append("self_hash")
    if reasons and len(bad) < 20: bad.append((row["seq"], reasons))
    run_prev = hh
print("ROWS_VERIFIED:", n, "BAD:", bad)
```

**结果**：

| 指标 | 实测值 |
|---|---|
| 总记录数 | **71,160** |
| seq 范围 | **1 .. 71,160**（连续，`MAX(seq)=COUNT(*)=71160`，`DUP_SEQ=0`） |
| ts 范围 | **2025-08-10T12:14:03.461232+00:00 .. 2027-10-20T11:52:06.143138+00:00**（**含 2027 年未来时间戳**） |
| 实际覆盖 UTC 日 | **16 天**（`2025-08-10/11`、`2026-09-12..09-23` 共 12 天、`2027-10-19/20`） |
| 全链重算断链数 | **0**（0 seq 缺口 / 0 prev_hash 不符 / 0 payload_hash 不符 / 0 self_hash 不符） |
| 同表 `prev_hash <> 上一条 self_hash` 的相邻对 | **0**（`SELECT COUNT(*) FROM audit_chain a JOIN audit_chain b ON b.seq=a.seq+1 WHERE a.self_hash<>b.prev_hash`） |
| ts 逆序（ts 不是单调不减） | **8 处**：seq 976, 1066, 2114, 2720, 3095, 10603, 20233, 20243 |
| source 分布 | agent 69,502 / ui 1,132 / system 526 / migration **0** |
| 不同 action 数 | **119** |

**最后一条记录（seq=71160）**：

```
ts=2026-09-23T16:57:25.634449+00:00  actor=system  action=descriptor.register
subject=capability:cp.tools.beta  source=agent
payload_hash=0cf1a8199a366ed4bc97769b0cc122140a253a104f61a96bde582a902dae6b19
prev_hash  =300a132bad272b8c54e96e24b4958455abff10793ca6f029f308f990804e5154
self_hash  =15b81a260b2142a731dab8217719ad3a6b8e5526a048631f1d9a556d51fe9169
```
→ 已校验：其 `prev_hash` 恰等于 seq 71159 的 `self_hash`（全链重算中 0 失败的组成部分）。

### 4.3 【重要】DB 链头落后真实链头 1,100 条

| 检查项 | 实测 |
|---|---|
| `audit_chain.db` MAX(seq) | **71,160** |
| `audit_chain.db.seqjournal` 行数 / seq 范围 | **2,012 行 / 70,249 .. 72,260**（连续，无缺口） |
| journal 中 **不在 DB** 的 seq | **1,100 条（71,161 .. 72,260）** |
| 断链与否 | **未断链**：seq 71161 的 `prev_hash = 15b81a260b2142a7…` **恰等于 DB 链头（seq 71160）的 self_hash** |
| 这 1,100 条的 ts 跨度 | 2026-09-23T16:57:25.634 → 2026-09-23T16:57:26.296（**0.66 秒内突发**，全部 `descriptor.register`） |

**含义**：进程在 2026-09-24 00:57 被非正常终止（`-shm` 残留、`-wal` 为 0 字节即此特征），这 1,100 条已分配 seq、已写预留日志、但**尚未落库**。设计上由下次进程启动时 `_load_state`/`_recover_backlog` 重放补写（`chain.py:1291-1340`、`:1826-1840`）。**当前状态下直接对 `audit_chain.db` 验链只能验到 seq 71160，会漏掉最后 1,100 条**。任何以 `COUNT(*)`/`MAX(seq)` 为口径的验收都会低报 1,100 条。

### 4.4 每日 Merkle 根：签名有效，但覆盖与内容有 2 类缺陷

**独立验签（不调用模块，直接用 cryptography）结果**：9 条根记录**全部通过** ed25519 验签，`entry_hash` 外层链连续，签名公钥与 `audit_signing_key.pem` 导出的公钥一致（`b73dd748ad340a2f…`）。

**缺陷 1：8 个有记录的 UTC 日没有 Merkle 根**

| UTC 日 | 记录数 | 日根 |
|---|---:|---|
| 2025-08-10 | 48 | **缺失** |
| 2025-08-11 | 58 | **缺失** |
| 2026-09-12 | 553 | **缺失** |
| 2026-09-13 | 361 | 有 |
| 2026-09-14 | 82 | 有（**同一日写了 2 条**，root_hash 完全相同，重复行） |
| 2026-09-15 | 285 | **缺失** |
| 2026-09-16 / 17 / 18 / 19 | 5,253 / 10,792 / 323 / 192 | 有 |
| 2026-09-20 | 361 | **缺失** |
| 2026-09-21 | 245 | 有（**内容不一致，见缺陷 2**） |
| 2026-09-22 | 288 | 有 |
| **2026-09-23** | **50,523（占全链 71.0%）** | **缺失** |
| 2027-10-19 | 1,738 | **缺失** |
| 2027-10-20 | 58 | **缺失** |

合计 **53,624 条（75.4%）记录没有任何 Merkle 根背书**。代码层面成因可定位：`chain.py:2256-2281 `_maybe_auto_seal`` 只封存 `d < today`（`:2269-2270`）且只针对本进程 `_observed_days`——**未来时间戳的日（2027-10-19/20）永远不会被自动封存**；写入当日（09-23）在当天也不会被封存。

**缺陷 2：2026-09-21 的日根与当日实际记录集合不一致**

| 项 | 值 |
|---|---|
| 文件中 `leaf_count` | **235** |
| DB 中该日实际记录数 | **245**（且 seq 20105..20349 恰好是**连续的 245 条**，全部 ts 属该日） |
| 文件中 `first_seq / last_seq` | 20105 / 20349（**与 DB 实际首尾一致**） |
| 文件中 `first_self_hash` / `last_self_hash` | 均**等于** DB 该日首条/末条的 `self_hash` |
| 按 245 条重算 Merkle 根 | `8303f3064f954de7…` **≠** 存储的 `de9c5a1e69ed1252…` |
| 穷举验证 | 236 种"连续剔除 10 条"组合、按 ts 排序、剔除最早/最晚 10 条、按 source 过滤 —— **均无法复现该存储根** |

→ 结论（**实测事实**）：该日**封印点之后仍有 10 条该日记录进入封印的 seq 区间内**，导致 `verify_daily_root('2026-09-21')` 在 `chain.py:2849-2854` 的"该日 ∩ seq ≤ last_seq"口径下**必然 FAIL**。
→ 成因【推测】：回填/迁移路径（`migration.py:579-587` 的 `record_migration_event` 接受任意 ts）或预留日志重放重排所致，本次审计**未能定位到确切的写入时刻**。

---

## 5. 性能与容量风险

### 5.1 append 是 O(1)（相对链长）——已用代码路径与实测定量确认

`chain.py:1617-1648` 的 docstring 声称"单条 append <5ms"。实现（`chain.py:1699-1718` → `:1802-1855`）实际只做 4 件事，**没有任何按链长增长的步骤**：

| 步骤 | 位置 | 复杂度 |
|---|---|---|
| 取跨进程锁 + 解析链头（`max(内存, 日志末行, DB max(seq))`） | `chain.py:1674-1678`、`_resolve_head` | O(1)（DB 侧 `ORDER BY seq DESC LIMIT 1`，走 seq UNIQUE 索引） |
| 分配 seq + 两级 sha256 | `chain.py:1812-1821` | O(payload 大小) |
| 写预留日志（`append_row`，flush 不 fsync） | `chain.py:1827-1840` | O(1) |
| 入队，由后台 writer 批量 `executemany`（`WRITER_BATCH_SIZE=100`，`WRITER_POLL_INTERVAL=0.5`） | `chain.py:1842-1847`、`1857-1883`；常量 `chain.py:101-102` | O(批量) |

**实测单条哈希成本**（取 DB 中 2,000 条真实记录重放两级哈希）：**0.0224 ms/条**（2,000 条 / 44.9 ms）。即哈希部分相对 5ms 目标有约 200× 余量，**append 路径本身不构成瓶颈**。

### 5.2 读路径是 O(N)——这是真实的性能风险点

| 调用 | 位置 | 实测代价 |
|---|---|---|
| `facade.recent(limit=50)` | `facade.py:402-408`：`rows = chain.entries(**filters)` **未传 limit**，然后 `rows[-int(limit):]` | **全表 71,160 行读出**（实测 `SELECT *` 全表 0.503 s，再构造 71,160 个 `AuditEntry` 约再 0.19 s）。而直接 `ORDER BY seq DESC LIMIT 50` 只需 **0.0003 s** → **约 2000× 差距** |
| `chain.stats()` | `chain.py:2973-2979`：`for e in self.entries():` **先全表遍历**统计 by_source/by_actor | 同上量级 |
| `facade.snapshot()` | `facade.py:434`：`chain.stats(verify=False)` | 继承上面的全表遍历（**传 `verify=False` 也逃不掉**） |
| `chain.verify_chain()` | `chain.py:2641-2676` — 全表读出 + 逐条重算两级哈希 | **实测 71,160 条 1.641 s**（0.023 ms/行，线性） |
| `chain.count()` / `chain_head()` / `seq_range()` | `chain.py:2614-2637`、`:2678-2690` | `_query_rows(limit=None)` → **全表**，各调用一次 |
| `daily_merkle_root(day)` | `chain.py:2707`：`self.entries(day=day)` | 按 `idx_ac_ts` 走 `ts LIKE 'day%'`（`chain.py:2494-2496`），**索引可助**；但叶子集合仍全量载入内存 |
| `entries()` 的 journal 合并 | `chain.py:2560-2561`：只要 `_journal_enabled` 为真**就必然走合并分支** | 每次读都要读预留日志尾部（`read_since(..., limit=2000)`，当前 2,012 行） |
| `_query_rows` 的隐式 flush | `chain.py:2475-2476`：`refresh=True`（默认）且 role=writer → 每次读都 `self.flush(timeout=5.0)` | 每次"读"都先等一次落盘同步 |

**结论**：链越长，任何"看最近几条""看统计概览""看总计"的操作越慢，且是**线性变慢**。若面板/巡检每秒调用一次 `snapshot()`，当前 71k 行下每次约 0.7 s CPU + 全量对象分配。

### 5.3 容量估算

| 项 | 实测/推算 |
|---|---|
| 单条均摊磁盘（主库，含 5 个索引） | **758 B/条**（53,948,416 ÷ 71,160） |
| 单条 payload 均摊 | 193 B（13.7 MB ÷ 71,160）；最大 payload 1,726 B |
| 预留日志均摊 | 648 B/条（1,304,568 ÷ 2,012），受 `seq_journal.DEFAULT_RETAIN_RECORDS = 512`（`seq_journal.py:63`）与 `compact`（`:394`）约束，**有界** |
| 每日 1 万条 | **≈ 7.6 MB/日 ≈ 231 MB/月 ≈ 2.8 GB/年** |
| 每日 5 万条（2026-09-23 实测峰值日 50,523 条） | **≈ 38 MB/日 ≈ 13.9 GB/年** |
| 每日 Merkle 根 | 约 0.9 KB/条（9 条 8,091 B），可忽略 |
| 风险判定 | **单机长期跑无即时风险，但没有保留/轮转策略**：`chain.py` 全模块**只有 1 处 DELETE**（`clear()`，测试专用，`chain.py:33` docstring 明示）。链是 append-only，磁盘只增不减；唯一的容量侧治理是 `retention` 模块（`agent/retention/archiver.py:75` 只写一条 `retention.run` 审计，不裁剪本链）。 |

---

## 6. 敏感数据

### 6.1 脱敏逻辑位置

| 层 | 位置 | 行为 |
|---|---|---|
| 门面（推荐路径） | `facade.py:118-125` `redact_payload()` | 优先用注册的脱敏器（`observability.trace_v2` 注册，`facade.py:85-88`），异常时回落内置掩码 |
| 内置兜底掩码 | `facade.py:159-177` `_minimal_redact()` | 按字段名（`_SENSITIVE_KEYS`，`facade.py:128-129`：password/secret/api_key/token/auth/credential/private_key/authorization…）替换为 `"********"`；文本级正则（`facade.py:141-146`）：`sk-…` / `gh[pousr]_…` / `AKIA…` / JWT；`Bearer` 头（`facade.py:155`） |
| 门面应用范围 | `facade.py:310-318` | `payload` 与 `extra` **都过脱敏** |
| **绕过点 1** | `facade.py:319-320` | `technical=` **不过脱敏**（docstring `:294-296` 自认：「切勿放入用户输入或密钥」） |
| **绕过点 2** | `chain.py:1612-1616` `append()` | **链层完全不做脱敏**；直接调用者绕过门面即绕过脱敏 |
| UI 请求体 | `ui_middleware.py:380-385` | 只落 `sha256(body)` 与字节数，**原文不落盘**（`body_hash` / `body_bytes`） |
| IP / 身份 | `ui_middleware.py:118-133`、`agent/security/identity` | 只落 `actor_ip_masked`（如 `127.0.xxx.xxx`）、`actor_ip_hash_status` |

### 6.2 实测结论（对 71,160 条 payload 全量扫描）

| 检查 | 结果 |
|---|---|
| 密钥形态正则（`sk-` / `gh[pousr]_` / `AKIA` / JWT `eyJ…` / `Bearer <token>`） | **0 命中** |
| 含掩码串 `********` 的记录 | **3,635 条**（脱敏器确实在工作） |
| 顶层 payload 键普查（Top） | `actor_source`(70,859)、`schema`(70,859)、`payload`(69,683)、`status`(63,769)、`legacy`(61,554)、`reason`(54,842)、`detail`(52,705)、`action`(52,351)、`trace_id`(4,427)、`record_id`(4,179)、`pid`(3,861)、`key`(3,648)、`decision`(2,874)、`field_count`(2,754) |
| `config.env_set` | 只记**环境变量名**（实测 seq 20105：`"key":"LLM_TEST-READ-1_API_KEY"`），**不记值** |
| `auth.attempt` | 只记掩码 IP + `success` 布尔 |
| **确有原文入链的地方** | `approval.submit` / `approval.approved` / `approval.rejected` 的 `description`（如 seq 20615：「工具 shell_execute 请求执行（风险 critical）：echo hi（参数摘要 9cc631…）」）、`undo_hint`；`repair.diagnose` 的 `command`（完整 pytest 命令行）；`descriptor.patch` 的 `undo_hint`/`reason`；大量本地绝对路径（含 `.pytest_tmp\pytest-of-AdminWT\…`） |
| 最长字符串 | 401 字符（`escape` 的 `task_id`）；最长 payload 1,726 B |

### 6.3 风险评估

| 风险 | 等级 | 依据 |
|---|---|---|
| API key / 令牌原文入链 | **低（实测未发生）** | 全量扫描 0 命中；UI 请求体只落哈希（`ui_middleware.py:380-385`） |
| `technical=` 通道绕过脱敏 | **中** | `facade.py:319-320` 无脱敏；当前调用方只传内部键（`migration.py:527` `audit_ref`、`digestion/*` `*_version`、`settings/service.py:593-595`），属"约束靠自觉" |
| 直接 `chain.append` 绕过门面脱敏 | **中** | `migration.py:583-585` `record_migration_event()` **不经过 façade**，payload 原样入链；`scripts/chaos_s4_03_drill.py:242`、`scripts/demo_s2_02_audit.py:332` 同样直连 |
| 工具调用参数 / 命令原文入链 | **中** | 已实测存在（`approval.*` 的 description、`repair.diagnose` 的 command）。若这些字段可能含用户输入中的凭据，则依赖字段名启发式（`_SENSITIVE_KEYS`）与文本正则兜底 |
| 审计库文件权限 | **中** | `audit_chain.db` 与私钥文件均**无只读保护**（`audit_chain.db` 为普通 `-a---`；只有 `daily_roots.jsonl` 与 `.pem` 是 `ar--`）。私钥以**无口令 PKCS8 PEM 明文**落盘（`chain.py:725-731`） |
| 测试污染 | **中** | `x.y`(13)、`global_test_action`(50) 等测试 action 与 `.pytest_tmp` 路径已进入生产链，且链**不可删改**——污染永久化 |

---

## 7. 附：本次使用的只读取证命令（可复现）

```powershell
# 目录与文件实况
Get-ChildItem -Path C:\Users\Administrator\agent\data\audit -Recurse -Force |
  Select-Object Mode,LastWriteTime,Length,FullName | Format-Table -AutoSize

# 事件类型/常量普查
Select-String -Path C:\Users\Administrator\agent\agent\*.py, C:\Users\Administrator\agent\agent\**\*.py `
  -Pattern '^AUDIT_ACTION[A-Z_]*\s*=' | Out-String -Width 200
Select-String -Path C:\Users\Administrator\agent\agent\*.py, C:\Users\Administrator\agent\agent\**\*.py `
  -Pattern 'audit(_facade)?\.record\(|_audit\.record\(|facade\.record\(' -Context 0,3
```

（Python 侧取证脚本见 §4.2 与 §4.3；全部使用 `sqlite3.connect("file:///…?mode=ro", uri=True)`，未开启任何写事务。）
