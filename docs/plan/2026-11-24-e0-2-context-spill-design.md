# E0-2 设计：上下文工程（大输出 spill + read_spill）（2026-11-24）

> 路线图 `docs/plan/2026-11-24-dsh-parity-roadmap.md` §3 Phase 0 / E0-2。本页只写**决定与边界**；
> 参照 DSH `spill` / `spill-policy`（工具结果超过阈值落盘 + `{spilled, locator, preview}` + 再读）。

## 1. 问题

现状是各工具的**硬截断**：`shell_tools._truncate_output` 100KB 之后直接丢掉、
`read_file` 10MB 上限、`run_tests`/`git` 各自截断 ⇒ **丢了就没了**，模型无法再取回。
云枢的 `/api/context/status` 只有占用率，没有"大结果落盘 + 分段取回"。

## 2. 决定

### 2.1 一个大输出通道（唯一汇聚点）

- 落点：`tools.call()`——本仓工具分发的唯一汇聚点（`plugins/chat.py` 与 orchestrator 直连都走它）。
  handler 返回后、交给调用方前：序列化结果，超过阈值则落盘并**替换**为信封。
- 信封：`{ok, spilled: true, locator: "spill:<16 hex>", bytes, chars, tool, preview, hint}`。
  `ok` **原样保留**（下游/审批依赖它）；`error` 若存在也带上。
- 落盘：`agent/data/spill/<id>.txt` + `<id>.json`（tool/created_at/chars/bytes/sha256_16）。
  运行期状态，不提交。
- 预览：头 4/5 + 尾 1/5（默认 8000 字符预算），中间插一句"用 read_spill 取回"。

### 2.2 再读工具 `read_spill`

- 入参 `locator` / `offset` / `limit`（**字符**计，默认 20000，上限 200000）。
- 返回 `{ok, locator, offset, limit, chars_total, returned_chars, next_offset, content}`；
  `next_offset=null` 表示读完。
- **路径安全**：locator 只接受 `^spill:[0-9a-f]{16}$`，路径 = 常量目录 + 该 id ⇒
  不接受任意路径（CodeQL 的"常量键→常量路径"口径）。
- 只读工具（plane=perceive / effect=read / risk=low），显式 `enabled: true`：停用它会让
  spill 信封里的 locator 无法取回。

### 2.3 生产者上限

spill 开着时把 shell（含 Windows 受限执行器）的捕获上限 100KB → **1MiB**，
否则落盘的是"已经被截断的文本"。超过 1MiB 的输出仍在生产者处截断（本次不做无上限捕获）。

### 2.4 开关与回滚

| env | 默认 | 说明 |
|---|---|---|
| `CP_SPILL_ENABLED` | true | 置 0 ⇒ 完全回到原来的硬截断（无需回滚代码） |
| `CP_SPILL_THRESHOLD_BYTES` | 100000 | 触发阈值（4KB–1MB） |
| `CP_SPILL_PREVIEW_BYTES` | 8000 | 预览字符预算（512–65536） |

三条都是 A 级、hot 生效（改完下一次工具调用即生效）。spill 自身异常**只记 debug、原样返回结果**
——内容保全机制不能反过来让工具调用失败。

## 3. 边界（如实写）

- 不做读侧/权限边界：spill 文件落 `agent/data/spill/`，宿主内可读；它不是沙箱（E0-1 才是）。
- 只处理 `dict` 结果（工具返回契约都是 dict）；`read_spill` 自身不 spill（防递归）。
- 不做会话级目录：工具调用点拿不到稳定 session_id，按天/平铺落盘，locator 全局唯一。
- **污点账**：原文在替换前已按工具类型打标；预览是原文子串（切片边界不同，摘要可能对不上）
  所以对预览**再打一次**（`mark_result_foreign(name, {"content": preview})`，非外来工具幂等无害）；
  `read_spill` 自身是 file/read ⇒ 按 `read_file` 同口径记 `file` 来源（保守，不漏标）。
- 不做自动清理：`agent/data/spill/` 会增长（本次不设保留策略，列入余项评估）。
- 余项（登记在 `data/handoff_next.json::dsh-parity-e0-2-remaining`）：
  ② tool-result pruner（头/中/尾策略 + 可回放）、③ 图片/截图 offload、④ token-meter 驱动压缩。

## 4. 验收（真机）

`.local/verify_e0_2_spill.py` 8 项全过：小结果不落盘、`read_file` 200KB 经 `tools.call` 被 spill、
分段拼回全文且头/中/尾标记齐全、locator 文件在 `data/spill/` 下、非法 locator 结构化拒绝、
`CP_SPILL_ENABLED=0` 时不落盘。服务侧 `.local/_check_spill_setting.py`：3 条设置 + 工具 102（read_spill 启用）。
单测 `tests/unit/test_context_spill.py`（27）。派生产物：tool_index 97 / BM25 S0 5.6993 / 路由报告 /
能力清单 147 / 盘点表（干净 worktree 重派生，五项 `--check` 全 0）。

## 5. 二期：tool-result pruner（2026-11-26 已交付，63e2629b6）

- **要解决的问题**：spill 只在 >100KB 时保全全文；8–100KB 的结果（一次大 diff、一次宽表查询）仍整段进上下文。
- **决定**：在 `tools.call()` 里、**spill 之后**再收一道：超 `CP_TOOL_RESULT_PRUNE_CHARS`（默认 32000）
  的字符串字段只留头 70% / 尾 30%，中段替换为「已裁剪 N 字符」标记。
- **只动这些字段**：`stdout/stderr/content/result/output/text/diff/body`；**不新增/删除键**（形状不变），
  非 dict 结果不裁。`CP_TOOL_RESULT_PRUNER=0` 完全关闭。
- **与 spill 的分工**：spill 保全全文（`read_spill` 可分段取回）；pruner 只为预算，**不落盘**——
  要细节请用更窄的参数重跑。被 spill 吸收的小信封不会再被裁。
- **失败姿态**：裁剪异常只记 debug、原样返回；与 spill 同一条纪律（内容保全不得反过来让工具失败）。
- **验收**：`tests/unit/test_tool_result_pruner.py` 7 条（阈值/头尾比例/形状/开关/阈值收敛/call() 接线）；
  `test_context_spill.py` 与 `test_tool_calling_refactor.py` 无回归。
