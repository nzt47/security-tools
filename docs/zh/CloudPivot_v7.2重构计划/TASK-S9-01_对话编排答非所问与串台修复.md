# TASK-S9-01 对话编排「答非所问 + 跨轮串台」修复

> 归属：**S9 真用修复批次**（真用入口条件阻塞项之一）
> 上游依据：[`../真用前置_模型凭证核查_20260913.md`](../真用前置_模型凭证核查_20260913.md) §八 D2（证据已在文档中，勿重新推导）
> 生成：2026-09-13｜基线：`master`｜预估：3–5 人日｜**优先级：最高（不修则「真用」无法开始）**

---

## 一、问题与目标

### 1.1 现象（2026-09-13 实测，可复现）

| 提问 | 实际 `response` | 实际 `reasoning` |
|---|---|---|
| 「帮我列出当前工作目录下的文件」 | 真实目录列表（`list_directory` 执行成功） | 与本次提问相关 ✅ |
| 「2 加 3 等于多少？只回答数字」 | **一整篇 `# self_reflection` 技能文档** | **上一轮"列文件被截断"的思考** ❌ |
| 「1+1 等于几？只回答数字」 | 同上（技能文档） | 同上 ❌ |

**判据**：换了**完全不同**的问题，`tool_steps` 与 `reasoning` 却与上一轮**逐字相同** ⇒ 全局残留，不是本轮结果。

### 1.2 已定位的部分（不用重新查）

1. **跨轮串台的直接原因**（`agent/server_routes/routes_chat.py:428-429`）：
   ```python
   "tool_steps": getattr(Yunshu, '_last_tool_steps', []),
   "reasoning":  getattr(Yunshu, '_last_reasoning', None),
   ```
   二者取自**全局单例 `Yunshu` 的实例属性**（last-write-wins），**未按会话隔离**；
   本轮若没写入，就返回上一轮的值。
   注意同文件注释声称已"根治会话串扰"，但**只覆盖了 `session_id`**，没覆盖这两个属性。

   > **⚠️ 先看这条，否则会改错文件（2026-09-13 02:3x 修正）**
   > 线上 `/api/chat` 的 handler 是 **`plugins/chat.py`**（`plugins/chat.py:313-314`）——
   > 实测响应字段含 `context` / `voice_result` 且**不含** `thinking_mode`；
   > `agent/server_routes/routes_chat.py:428-429` 是同款写法但**不是线上**（它的返回里有 `thinking_mode`）。
   > 两处都有这两行，**以 `plugins/chat.py` 为准**；`routes_chat.py` 若已不生效请在报告中一并说明。
   >
   > **⚠️ 复验必须用请求体的 `session_id`**：`plugins/chat.py:148-149` 只认 body 里的
   > `session_id`（`session` 只在**查询参数**位置被接受）。传错字段会**静默回落到全局会话**，
   > 让你误以为"没有会话隔离"。最初的探测证据就是踩了这个坑；已用正确字段 + 全新会话复验，
   > **D2 仍复现**（「2 加 3 等于多少」→ self_reflection 技能文档；
   > 「帮我列出当前工作目录下的文件」→ 原始工具结果 JSON）。

2. **这两个属性的写入点**（共 7 处，注意有些写法是"不回退"的）：
   - 初始化：`agent/orchestrator/lifecycle_manager.py:445-446`
   - 重置：`agent/orchestrator/orchestrator.py:2880`
   - 赋值：`orchestrator.py:2979, 3017, 3057, 3440-3441, 3450-3451`
   - ⚠️ 3441/3451 用的是 `_result.get("reasoning") or self._last_reasoning` —— **`or` 保留旧值**，
     这正是"串台"最可能的直接注入点之一。

3. **`response` 来源**：`agent/orchestrator/orchestrator.py:429 def chat(self, user_input, *, session_id=None, session_mgr=None)`
   —— 返回技能文档而非答案，**此部分尚未定位**，是本任务的主要排查工作量。

### 1.3 目标

1. **答得对**：普通问题返回与提问相关的答案；工具结果只作为**素材**，不得被当作最终答案直接回吐。
2. **不串台**：`tool_steps` / `reasoning` / `response` 均按**会话隔离**；本轮没有就返回**空**，绝不复用上一轮。
3. **可判据**：给出**机器可读**的验收证据（连续两次不同提问，字段必须不同）。

---

## 二、执行步骤

1. **先固化现象**（写测试，不改代码）：
   - 在 `tests/unit/` 新增用例：连续两次 `chat()`（不同 `session_id`、不同问题），
     断言第二次的 `tool_steps` / `reasoning` **不得**等于第一次（当前应当**失败**，作为回归锚）。
   - 真机复现脚本：连续 POST `/api/chat` 两个不同问题，对比 `tool_steps`/`reasoning`/`response`。
2. **定位 `response` 为何是技能文档**：
   - 顺 `orchestrator.chat()` 走：技能检索/`expand_context` 注入 → 上下文装配 → 模型调用 → 响应后处理；
   - 重点怀疑：注入的检索内容（技能正文）被当作"最终答案"返回（装配顺序错、或某处 `return` 了拼接中间态）；
   - 必须给出**代码行级**根因，不接受"可能/大概"。
3. **修跨轮串台**（按会话隔离）：
   - 方案：把 `_last_tool_steps` / `_last_reasoning` 改为**按 `session_id` 存**（会话级字典或写入 `session_mgr` 的消息元数据），
     或在每轮入口**先清空**；
   - **禁止**用 `or` 回退旧值（`x or self._last_x` 一律改成显式赋值）；
   - 保持既有公开接口行为不变（`/api/chat` 响应字段名与语义不变）。
4. **修答非所问**（按第 2 步的根因）。
5. **回归**：跑相关套件 + 邻接回归，确认未破坏 `tests/unit/test_orchestrator_*`、
   `test_prompt_cache_order.py`、`test_orchestrator三层路由_e2e.py`（它们直接读写这两个属性）。
6. **真机复验**（见 §四 评估标准），截留证据。

---

## 三、预期成果

1. 代码修复：会话隔离 + 答非所问根因修复（含根因说明与代码行号）。
2. 新增/更新测试：跨轮串台回归用例（连续两次不同提问必须不同）、答非所问回归用例。
3. `TASK-S9-01_验收报告.md`：逐条对照 §四，附**真机复验输出**（原始 JSON 片段）。
4. `S9-01_交付结案报告_<日期>.md` + 更新 `00_总览` 状态行。
5. 双远端同点推送。

---

## 四、评估标准

| # | 标准 | 判据（必须可机器读） |
|---|---|---|
| 1 | 不串台 | 连续两次不同提问：`tool_steps` 与 `reasoning` **不相等**；本轮无内容时返回**空**而非上一轮值 |
| 2 | 答得对 | 「2 加 3 等于多少」→ 回答含 `5`；不得返回技能文档正文；不得返回原始工具 JSON |
| 3 | 会话隔离 | 两个不同 `session_id` 并发/交替提问，各自的 `tool_steps`/`reasoning` 互不污染 |
| 4 | 工具仍可用 | 需工具的提问仍能真实执行（`list_directory` 等）并出现在 `tool_steps` 中 |
| 5 | 无回归 | 相关套件全过；既有公开接口字段名与语义不变 |
| 6 | 不编造 | 报告中的每个数字/片段都能从日志或响应中原样复现 |

---

## 五、硬约束（与全局纪律一致）

- **不得为了让用例通过而放宽断言**；现象先固化再修。
- 自动化默认关闭；不改既有公开接口行为；覆盖率 ≥80%。
- 不引入外部依赖。
- **不得编造数字**：真机复验做不到就如实写"未在真机验证"。
- 主工作区禁令：✗ `git checkout`　✗ `git reset --hard`　✗ `git add -A`。

---

## 六、已就绪前置：勿重复实现

- HTTP 入口与响应装配：`agent/server_routes/routes_chat.py`（`api_chat`，约 258-430 行）
- 编排入口：`agent/orchestrator/orchestrator.py::chat`（429 行）
- 统一 Trace（任务级）：`agent/observability/trace_v2.py`
- 技能检索与注入：`agent/skills_mgmt/`（`service.py` 的 `validate_llm_output` 亦在此链路）
- 会话管理：`session_mgr`（`session_id` 已按会话隔离，可直接复用其存储）
- ★ **不要**另建一套会话状态或响应装配

---

## 七、上游已知坑

1. `_last_tool_steps` / `_last_reasoning` 被**多个测试直接读写**（`tests/unit/test_digital_life_comprehensive.py`、
   `test_prompt_cache_order.py`、`test_orchestrator_workflow_learning_layer.py`、`tests/integration/test_orchestrator三层路由_e2e.py`）
   ⇒ 改存储结构时**同步更新**这些用例，不要靠兼容层掩盖。
2. `orchestrator.py` 内 7 处写入点分散在 2880 / 2979 / 3017 / 3057 / 3440 / 3450 附近，
   改一处漏一处会留下同类 bug ⇒ 建议**收敛成单一 setter**。
3. 本任务与 **S9-02（输出护栏 D1）** 相邻但不重叠：D1 改的是 `agent/skills_mgmt/output_guard.py`，
   本任务改的是编排/响应装配。**两边都不要顺手动对方的文件**（避免冲突）。
4. 上一轮遗留"上下文 454.7% 超限"（D4，归 S9-03）会**加剧**答非所问；
   若定位中发现根因其实是上下文超限，请在报告中**明确指认**并转 S9-03，不要在本任务里顺手改预算逻辑。

---

## 八、回报格式

交付物 / 验收逐条（含证据命令与原始输出）/ 根因（代码行级）/ 质量证据（套件、门禁）/ 遗留（带归属）/ 总览更新 / 双远端 SHA。
