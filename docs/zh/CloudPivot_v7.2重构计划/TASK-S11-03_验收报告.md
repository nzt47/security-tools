# TASK-S11-03 验收报告（补录 · 上下文读数口径统一）

> **补录说明（2026-09-14，主线）**：S11-03 交付时未落独立报告（批次里只有 `TASK-S11-02_验收报告`），
> 本报告由**主线在合入后按实测补录**，内容严格限于**可复现的证据**；
> 凡主线未实测的部分，明确标注"未由主线复验"，不代替原会话的自我验收。

---

## 一、任务与交付

**任务**：上下文读数口径统一 —— ①`context` 块归属**请求会话**；②转发 `metadata`（让新口径的告警可见）。
来源：S10-03 遗留 R3 + R5（见 [`PARALLEL_S11批次_S10遗留待修项.md`](PARALLEL_S11批次_S10遗留待修项.md)）。

**交付提交**：`7cbb8668`（+ 后续小修正 `869dbbc4`）

| 文件 | 改动 |
|---|---|
| `plugins/chat.py` | +123 |
| `agent/orchestrator/orchestrator.py` | +65 |
| `agent/orchestrator/turn_state.py` | +39 |
| `tests/unit/test_s11_03_context_readings.py` | +463（新增） |

## 二、主线验收（实测证据）

| # | 验收点 | 结果 | 证据 |
|---|---|---|---|
| 1 | 新增测试通过 | ✅ **17 passed**（1.81s） | `python -m pytest tests/unit/test_s11_03_context_readings.py -q` |
| 2 | `metadata` 已转发（R5：否则新告警"没人看得见"） | ✅ | `plugins/chat.py` 有 `_json_safe_metadata()`（L58），注释说明"`/api/chat` 响应由 jsonify 序列化，metadata 由编排层装配（`context_notice` / `plan_summary` / `used_planning`…）"；对被丢弃的键**显式披露**在 `metadata_omitted_keys` |
| 3 | 告警口径携带**真实上限**与来源 | ✅ | `plugins/chat.py` 存在 `limit_tokens` / `limit_source`（L104-114）；取不到时**如实**返回 `limit_tokens=None` + `limit_source="unavailable"`（不填 0 冒充） |
| 4 | 会话归属改为**请求指定的会话** | ✅（代码级） | 会话解析处保留既有优先级注释（请求体 `session_id` > 查询参数 `session` > 全局默认） |
| 5 | 合入状态 | ✅ | 主体 `7cbb8668` 早已在 master；尾随修正 `869dbbc4` 由主线合入（`53459322`） |
| 6 | 服务侧生效 | ✅ | 已于 2026-09-14 重启（PID 5864，`/api/health` HTTP 200）；重启后真机复验三问全对，且 **response 正文不再追加"上下文即将耗尽"** |

**未由主线复验的部分**（如实标注）：
- 「两个不同 `session_id` 交替请求，`context` 块各自归属正确」——主线只做了**代码级**确认（存在 `limit_tokens` 机制与会话优先级），**未做**两会话交替的 HTTP 对比实测；
- `orchestrator.py` / `turn_state.py` 的 +65/+39 行内部改动，主线**未逐行审读**。
  ⇒ 若需更强保证，建议按 `tests/unit/test_s11_03_context_readings.py` 的用例设计补一次真机双会话对比。

## 三、遗留（承接 S10-03 R1–R6，标注当前状态）

| 编号 | 事项 | 当前状态 |
|---|---|---|
| R1 | BM25-only 召回面收窄 | ✅ **已由 Owner 裁定**：暂不标定，保持现状；复评条件="出现 ≥20 条可复现的纯 BM25 真命中被拒样本"（见 [`../Owner裁定记录_20260913.md`](../Owner裁定记录_20260913.md) 裁定 #2） |
| R2 | `CP_ENV_FILE` 未登记导致守卫红 | ✅ 已修（主线补登，`4e1f5b67`） |
| R3 | `context.percentage` 口径 | ✅ 本次已处理（切到请求会话 + 披露真实上限与来源） |
| R4 | 三套 token 上限不一致 | ✅ **已由 Owner 裁定并实施**：`config.yaml` 显式 `memory.token_limit: 32768` ⇒ 压缩触发点 **3276 → 26214**；实测 `MemoryManager._token_limit=32768`（裁定 #1） |
| R5 | `metadata` 未转发 ⇒ 告警不可见 | ✅ 本次已处理（见 §二 #2） |
| R6 | 运行期生效需重启 | ✅ **已完成**：2026-09-14 重启（PID 5864），config 生效实测 `_token_limit=32768` |

## 四、口径与纪律

- 本报告只写**可复现**的证据（命令 + 输出）；未复验项已显式标注；
- 未改动 S11-03 的任何代码——补录只补**记录**，不改结论；
- 若原会话另有更完整的自我验收材料，应以**原材料为准**并与本报告合并（本报告不覆盖、不替换它）。

---

**SHA 记录**：交付 `7cbb8668`（+`869dbbc4`）｜补录时 master = `64665851`｜双远端同点。
