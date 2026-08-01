# orchestrator.py 意图识别埋点代码审查清单

> **目的**：当生产环境出现 `yunshu_intent_layer_*` 指标占比异常（如某层缺失、重复计数、虚高/虚低）时，按本清单逐条审查 5 个命中点，快速定位埋点漏掉或重复计数的根因。
>
> **适用版本**：`agent/orchestrator/orchestrator.py`（_record_intent_layer 统一入口在 L59-69）
>
> **使用方式**：生产告警 → 跑 `scripts/diagnose_intent_layer.py --log-file <orchestrator.log>` → 按下表对应层逐项排查。

---

## 一、不变量（【不易】核心契约，违一则埋点失真）

| 编号 | 不变量 | 违反后果 |
|------|--------|----------|
| INV-1 | 每个意图分支（rule/template/semantic/llm/reject）的 `return` 路径上**有且仅有一次** `_record_intent_layer(<layer>)` 调用 | 漏调用 → 该层计数虚低；重复调用 → 该层计数虚高 |
| INV-2 | `_record_intent_layer` 必须在**业务结果已确定**之后调用（即调用时该 layer 的命中已成定局） | 在"可能降级"路径调用 → 虚高（如 semantic 命中后又降级 LLM 会双重计数） |
| INV-3 | `_record_intent_layer` 调用**不得**放在 `try/except` 的 except 分支前（除 Exception 会跳过埋点） | except 路径漏埋点 → 该层计数虚低 |
| INV-4 | `_record_intent_layer("llm")` 应在 LLM 调用**前**记录（保证"进入 LLM 路径即计数"，即便 LLM 调用失败也计入） | 放在 LLM 成功后 → LLM 异常时漏计数 |
| INV-5 | `_record_intent_layer` 统一入口的异常被静默吞掉（`pass`），**不得**影响主链路 | 反例：把 metric 失败抛出 → 主链路崩 |
| INV-6 | `return ResponseBuilder...().to_dict()` 必须在 `_record_intent_layer` **之后**执行 | 反例：return 在前 → 埋点永远不执行 |
| INV-7 | 所有 5 层的 `layer` 字符串必须与 `prometheus.py` 的标签值**字面一致**（rule/template/semantic/llm/reject） | 拼写错误 → 创建新 label series，原 series 计数虚低 |

---

## 二、5 个命中点逐项审查

### 命中点 1: rule 层（WorkflowEngine 命中）

**位置**：`agent/orchestrator/orchestrator.py:198`

```python
if workflow_result is not None and workflow_result.matched:
    logger.info(...)  # 业务日志
    self._memory.score_and_save_message("user", user_input)
    self._memory.score_and_save_message("assistant", workflow_result.output)
    if trace_id:
        trace_store.end_trace(trace_id, workflow_result.output)
    _record_intent_layer("rule")           # ← L198 埋点
    return ResponseBuilder.workflow_result(...).to_dict()
```

| 审查项 | 验证方法 | 风险等级 |
|--------|----------|----------|
| ✓ 在 `return` 之前调用 | 查看上下文 L198-203，return 在 L199 | OK |
| ✓ 命中已确定（`workflow_result.matched == True`） | L192 条件保证 | OK |
| ⚠ try_match 抛异常时是否漏埋点？ | 若 `self._workflow_engine.try_match` 抛异常，会直接冒泡到外层 except（L360+ 的路由 except），不进入本分支。**这是设计如此**（异常时降级 LLM 会被 LLM 埋点捕获），但需确认外层 except 不会重复记录 rule | 低 |
| ✓ 仅调用一次 | 本 return 路径唯一调用 | OK |
| ⚠ InputGuard 拦截（L181 BLOCK）时未记录 rule | 设计如此：被拦截不算意图识别层命中。需确认 dashboard 上"拦截率"有独立指标，不与 rule 混淆 | 中 |
| ✓ layer 字符串 "rule" 与 prometheus.py 一致 | 字面校对 | OK |

---

### 命中点 2: template 层（IntentRouter 模板命中）

**位置**：`agent/orchestrator/orchestrator.py:358`

```python
try:
    # ... 模板回复逻辑 ...
    if trace_id:
        trace_store.add_span(...)
        trace_store.end_trace(trace_id, response)
    _record_intent_layer("template")       # ← L358 埋点
    return ResponseBuilder.success(response).to_dict()
except ImportError as _ie:
    logger.warning(...)  # 模板语义层失效告警
except Exception as e:
    logger.debug(...)    # 路由失败降级 LLM
```

| 审查项 | 验证方法 | 风险等级 |
|--------|----------|----------|
| ✓ 在 `return` 之前调用 | L358-359 上下文 | OK |
| ⚠ **关键风险**：埋点在 `try` 块内，如果 `trace_store.end_trace` 抛异常 → 跳过 `_record_intent_layer("template")` → template 计数虚低 + LLM 计数虚高（被 except 捕获后降级 LLM） | 检查 trace_store.end_trace 是否有 try/except 保护 | **高** |
| ⚠ ImportError 分支未记录 template（设计如此：模板层失效） | 需确认 dashboard 有"template 层失效"独立告警 | 中 |
| ⚠ Exception 分支未记录 template（设计如此：降级 LLM 会被 LLM 埋点捕获） | 检查 L418 的 llm 埋点是否会执行（流程会继续到 reject/llm 判定） | 中 |
| ✓ 仅调用一次 | 本 return 路径唯一调用 | OK |
| ✓ layer 字符串 "template" 一致 | 字面校对 | OK |

**修复建议**：将 `_record_intent_layer("template")` 移到 `try` 块外、`return` 前，或用 `try/finally` 包裹 trace_store 调用，避免 trace_store 异常导致埋点丢失。

---

### 命中点 3: reject 层（拒识）

**位置**：`agent/orchestrator/orchestrator.py:410`

```python
_reject_min_len = int(_os_reject.environ.get("ORCHESTRATOR_REJECT_MIN_LENGTH", "3"))
_is_ellipsis = (routing_input != user_input)  # DST 补全过 → 指代句
if (semantic_result is None and not _is_ellipsis
        and len(user_input.strip()) < _reject_min_len):
    _record_intent_layer("reject")           # ← L410 埋点
    logger.warning(...)
    _reject_msg = "抱歉，我不太理解..."
    if trace_id:
        trace_store.end_trace(trace_id, _reject_msg, status="rejected")
    return ResponseBuilder.success(_reject_msg).to_dict()
```

| 审查项 | 验证方法 | 风险等级 |
|--------|----------|----------|
| ✓ 在 `return` 之前调用 | L410-415 上下文 | OK |
| ⚠ **配置风险**：`ORCHESTRATOR_REJECT_MIN_LENGTH` 环境变量未配置时默认 3，若被误改为 0 → 永不拒识 → reject 指标永远为 0 | 检查生产 .env：`grep ORCHESTRATOR_REJECT_MIN_LENGTH` 应为 3 或合理值 | **高** |
| ⚠ **隐藏分支风险**：`_is_ellipsis` 为 True（指代句）时即使输入短也不拒识 → 直接落入 LLM。若业务上指代句占比高，reject 计数会持续偏低 | 这是设计如此，但 dashboard 应区分"指代句降级 LLM"和"正常 LLM 调用" | 中 |
| ⚠ **覆盖完整性**：拒识条件是"输入过短且三层未命中"。**是否还有其他拒识场景未覆盖？**（如黑名单、敏感词拦截） | 检查是否有其他 return 路径未走 _record_intent_layer("reject") | **高** |
| ✓ 仅调用一次 | 本 return 路径唯一调用 | OK |
| ✓ layer 字符串 "reject" 一致 | 字面校对 | OK |

---

### 命中点 4: llm 层（LLM 调用）

**位置**：`agent/orchestrator/orchestrator.py:418`

```python
_record_intent_layer("llm")                 # ← L418 埋点（LLM 调用前）
ts_llm = time.time()
try:
    if self._v2_lifetrace and self._trace_recorder:
        response = self._call_llm_v2(user_input, body_status)
    else:
        response = self._call_llm(user_input, body_status)
except Exception as e:
    logger.error(...)
    # 注意：此 except 分支不再记录 llm（已在 L418 记录），符合 INV-4
```

| 审查项 | 验证方法 | 风险等级 |
|--------|----------|----------|
| ✓ 在 LLM 调用**前**记录（符合 INV-4） | L418 在 try 之前 | OK |
| ✓ LLM 异常时仍计入（埋点已在 try 前） | except 分支不重复埋点 | OK |
| ⚠ **关键风险**：从 L358 template except 分支降级下来的请求、从 semantic 层 load_instruction 失败降级（见命中点 5）下来的请求，**都会再次记录 llm** → llm 计数虚高 | 检查是否有"已记录过 template/semantic 的请求再次进入 llm 埋点"的路径 | **高** |
| ⚠ V2 路径 vs 标准路径分流：`_call_llm_v2` 内部是否还有内部埋点或 return 路径未走 L418 之后？ | 通读 `_call_llm_v2` 实现，确认无短路 return | 中 |
| ✓ 仅调用一次（主链路上） | L418 唯一调用 | OK |
| ✓ layer 字符串 "llm" 一致 | 字面校对 | OK |

---

### 命中点 5: semantic 层（SkillLoader 三路融合命中）

**位置**：`agent/orchestrator/orchestrator.py:913`（位于 `_semantic_layer_match` 方法内）

```python
top1 = result.matches[0]
if top1.score < min_score:
    logger.info(...)
    return None  # 未命中，不埋点
logger.info(...)  # semantic.hit 日志
_record_intent_layer("semantic")            # ← L913 埋点

# 加载 top1 技能的 instruction（Layer 2）
try:
    instr_data = svc.loader.load_instruction(top1.skill_id)
    ...
except Exception as instr_e:
    logger.warning(...)
    return None  # ← 降级 LLM，但已记录 semantic！

if not instruction.strip():
    logger.info(...)
    return None  # ← 降级 LLM，但已记录 semantic！
```

| 审查项 | 验证方法 | 风险等级 |
|--------|----------|----------|
| ⚠⚠ **最高风险**：埋点在 `load_instruction` **之前**，但 `load_instruction` 失败（L923-925）或 instruction 为空（L927-929）会 `return None` 降级 LLM。**此时 semantic 已记录，主链路继续到 L418 又记录 llm → 双重计数**：semantic 虚高 + llm 虚高 | 通读 L913-929，确认所有 `return None` 路径都"已记录 semantic 但实际降级" | **极高** |
| ⚠ 埋点在 helper 方法 `_semantic_layer_match` 内，而非主 `process` 方法。**helper 返回 None 后主链路会继续执行 reject 检查和 LLM 埋点**，需确认不会重复计数 | 检查主 process 方法 L374-399：semantic_result is None 时继续到 L401 reject 检查；非 None 时短路 return | **高** |
| ✓ 在 `return None`（未命中）路径**不**埋点 | L904、L911 的 return None 在 L913 之前 | OK |
| ✓ layer 字符串 "semantic" 一致 | 字面校对 | OK |
| ⚠ SkillLoader.match 异常时是否漏埋点？ | 检查 _semantic_layer_match 的 try/except 是否捕获 match 异常并 return None（不埋点） | 中 |

**修复建议（优先级 P0）**：
```python
# 将 _record_intent_layer("semantic") 移到 load_instruction 成功且 instruction 非空之后：
if not instruction.strip():
    logger.info(...)
    return None  # 降级 LLM，不记录 semantic（符合 INV-2）
logger.info(...)
_record_intent_layer("semantic")  # ← 移到这里
output_text = self._build_semantic_output(...)
return {"output": output_text, ...}
```

---

### 命中点 6: llm_low_confidence_fallback 层（LLM 低置信度兜底）

> ⚠️ **版本说明**：本节分析基于含 LLM 低置信度兜底功能的版本。当前生产版本 orchestrator.py 中该功能（`_record_intent_layer("llm_low_confidence_fallback")`）**尚未启用**（仅 5 个调用点：rule/template/semantic/llm/reject）。以下分析为该功能上线后的**预防性审查**，相关单元测试（`tests/unit/test_llm_low_confidence_dual_counting.py`）已用模拟 layer 值验证 ratio 计算逻辑的通用正确性。

**位置**：`agent/orchestrator/orchestrator.py:553`（位于 `process` 方法内，LLM 调用成功后的低置信度判断分支）

**控制流**：

```python
# L467: 进入 LLM 路径（第四步）
_record_intent_layer("llm")                    # ← 第 4 命中点：记录 llm
ts_llm = time.time()
try:
    response = self._call_llm(...)             # LLM 调用成功
except Exception as e:
    ...                                        # 异常路径不进入低置信度判断

# L544: LLM 调用成功后，判断置信度
if _llm_confidence == "low":
    ...
    _record_intent_layer("llm_low_confidence_fallback")  # ← 第 6 命中点
    ...
    return ResponseBuilder.success(_fallback_msg).to_dict()  # L563 return
```

| 审查项 | 验证方法 | 风险等级 |
|--------|----------|----------|
| ⚠⚠ **最高风险**：L467 已记录 `llm`，L553 再记录 `llm_low_confidence_fallback`。**一次请求同时计入两个 Counter** → 双重计数 | 通读 L467→L553 控制流，确认中间无 return（LLM 调用成功后才到 L544） | **极高** |
| ✓ L553 之后有 return（L563） | 确认低置信度分支短路返回，不再继续到其他埋点 | OK |
| ⚠ LLM 正常置信度时只记 llm（L467），不记 fallback | 高/中置信度不走 L544 分支，单次计数 | OK |
| ⚠ LLM 调用异常时只记 llm（L467），不记 fallback | except 分支不进入 L544 判断 | OK |
| ✓ layer 字符串 "llm_low_confidence_fallback" 一致 | 字面校对 | OK |

---

#### ratio 总和是否超过 100% 分析

**结论：✅ ratio 总和始终 = 1.0，不会超过 100%。但 Counter 总和 > 实际请求数，占比会失真。**

**ratio 计算逻辑**（`agent/monitoring/prometheus.py:737-763`）：

```python
_intent_layer_counts[layer] = _intent_layer_counts.get(layer, 0) + 1
total = sum(_intent_layer_counts.values())    # 所有 layer 计数之和
for _layer, _count in _intent_layer_counts.items():
    yunshu_intent_layer_ratio.labels(layer=_layer).set(_count / total)
```

**数学证明**：

```
ratio 总和 = Σ(count_i / total) = (Σ count_i) / total = total / total = 1.0
```

无论双重计数多少次，ratio 总和恒等于 1.0（分母 total 也包含了多算的计数）。

**但以下指标会失真**：

| 指标 | 双重计数影响 | 示例（10 次 LLM 请求含 3 次低置信度） |
|------|------------|--------------------------------------|
| `yunshu_intent_layer_total` 总和 | **> 实际请求数** | 实际 13 次请求 → Counter 总和 = 16（多 3） |
| `llm` ratio | **虚高**（分母含多算的 fallback） | llm=10, total=16 → 62.5%（实际应为 10/13=77%，被稀释） |
| `llm_low_confidence_fallback` ratio | 新增层，稀释其他层占比 | = 3/16 = 18.75% |
| 其他层（rule/semantic/template/reject）ratio | **被稀释**（分母变大） | 原本 rule=3/13=23%，变为 3/16=18.75% |

**dashboard 影响**：
- 若面板用 `sum(yunshu_intent_layer_total)` 计算总请求数 → **虚高**
- 若面板用 `yunshu_intent_layer_ratio` 做饼图 → 总和仍 = 100%，但 **llm 占比被稀释**（因 fallback 分了一块）
- 若面板有独立的 "LLM 低置信率" 指标（`llm_low_confidence_fallback / llm`）→ **正确反映业务**

---

#### 修复建议（优先级 P1）

**方案 A（推荐）：保留埋点，dashboard 独立展示**

`llm_low_confidence_fallback` 是有业务意义的子指标（LLM 兜底率），不应删除。但需确保 dashboard 不把它纳入"四层占比"饼图：

```promql
# 四层占比饼图（排除 fallback）
yunshu_intent_layer_ratio{layer=~"rule|template|semantic|llm|reject"}

# LLM 低置信率（独立面板）
yunshu_intent_layer_total{layer="llm_low_confidence_fallback"} 
  / yunshu_intent_layer_total{layer="llm"}
```

**方案 B：L467 不记 llm，改为 L553 后按置信度分别记**

将 L467 的 `_record_intent_layer("llm")` 删除，改为在 LLM 调用后按置信度分别记录：
- 高/中置信度 → `_record_intent_layer("llm")`
- 低置信度 → `_record_intent_layer("llm_low_confidence_fallback")`（互斥，不双计）

**缺点**：改变 llm 埋点语义（从"进入 LLM 路径"变为"LLM 成功且高置信"），违 INV-4。

**推荐方案 A**：保留双重计数，dashboard 层面隔离展示。理由：llm 计数代表"进入 LLM 路径的请求数"（含低置信度），fallback 计数代表"其中低置信度的子集"，两者是包含关系而非重复。

---

## 三、跨命中点系统性风险

### 风险 A：双重计数（template/semantic 降级 LLM）

**场景**：template 层 try 块中 `trace_store.end_trace` 抛异常 → 进入 except → 降级到 LLM → L418 记录 llm。但 template 已经"接近记录但被异常打断"，**实际未记录 template**，所以此场景是 template 虚低 + llm 虚高（非双重计数）。

**真正双重计数场景**：semantic 层 L913 已记录 → load_instruction 失败 return None → 主链路到 L418 再记录 llm。**一次请求同时计入 semantic 和 llm**。

**验证脚本**：
```bash
# 模拟 load_instruction 失败场景，检查指标是否双重计数
python scripts/diagnose_intent_layer.py --log-file orchestrator.log --since 3600
# 若 semantic 计数 > 实际语义层命中业务日志数，则存在双重计数
```

### 风险 B：trace_id 上下文丢失

`_record_intent_layer` 在 L59-69 的统一入口**未携带 trace_id**。若需关联埋点与请求，需依赖 ContextVar 的 `get_trace_id()`。

**审查项**：检查 `_record_intent_layer` 内的日志（如已加诊断日志）是否正确读取 `get_trace_id()`，而非生成新 ID。

### 风险 C：环境变量导致埋点分支偏移

| 环境变量 | 影响命中点 | 风险 |
|---------|----------|------|
| `ORCHESTRATOR_REJECT_MIN_LENGTH` | reject (L410) | 误改为 0 → reject 永远为 0 |
| `SKILL_FUSION_WEIGHT_*` | semantic (L913) | 权重失衡 → semantic 命中率变化（非埋点问题，但会表现为指标偏移） |
| `MIN_SCORE` 阈值 | semantic (L913) | 阈值过高 → semantic 虚低（设计如此，非 bug） |

---

## 四、自动化检查脚本

### 4.1 快速诊断命令

```bash
# 1. 启动诊断脚本对比日志与 Prometheus 指标
python scripts/diagnose_intent_layer.py --prometheus localhost:9090 \
       --log-file /var/log/yunshu/orchestrator.log --since 3600

# 2. 模拟极端流量验证埋点链路
python scripts/mock_intent_layer_traffic.py \
       --distribution rule:10,semantic:10,llm:10,reject:70 --duration 8

# 3. 验证 trace_id 上下文传递
python scripts/verify_intent_layer_trace.py
```

### 4.2 静态检查清单（PR 合并前必查）

- [ ] 5 个 `_record_intent_layer` 调用点位置未变（L198/L358/L410/L418/L913）
- [ ] 新增的 return 路径是否遗漏埋点（grep `_record_intent_layer` 计数 == 5）
- [ ] layer 字符串拼写与 `prometheus.py` 标签值一致
- [ ] semantic 层埋点是否移到 load_instruction 成功之后（修复风险点 5）
- [ ] template 层埋点是否移到 try 块外（修复风险点 2）
- [ ] `_record_intent_layer` 统一入口异常仍被静默吞掉（守 INV-5）

```bash
# 一键静态检查
grep -c "_record_intent_layer" agent/orchestrator/orchestrator.py  # 期望 6（1 定义 + 5 调用）
grep -n "_record_intent_layer" agent/orchestrator/orchestrator.py  # 确认行号
```

---

## 五、判定决策树

```
指标占比异常
  │
  ├─ 某层计数 = 0？
  │   ├─ 是 → 该层命中点未触发或被 except 跳过 → 查命中点 N 的 try/except 保护
  │   └─ 否 → 进入下一步
  │
  ├─ metric_failed > 0？
  │   ├─ 是 → prometheus_client 异常 → 查 prometheus.py 可用性/网络
  │   └─ 否 → 进入下一步
  │
  ├─ 日志 recorded 比例 = Prometheus 比例？
  │   ├─ 是 → 流量分布问题（真实流量偏离 35/55/10 标准）→ 分析业务流量来源
  │   └─ 否 → 进入下一步
  │
  ├─ semantic 计数 > 业务 semantic.hit 日志数？
  │   ├─ 是 → 双重计数（命中点 5 风险）→ 检查 load_instruction 失败路径
  │   └─ 否 → 进入下一步
  │
  └─ llm 计数 > 业务 llm 调用日志数？
      ├─ 是 → 双重计数（命中点 4 风险）→ 检查 template/semantic 降级路径
      └─ 否 → 埋点正常，排查其他原因
```

---

## 六、版本历史

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-08-01 | v1.0 | 初版：基于 orchestrator.py 当前版本生成 5 命中点审查清单 |

---

**审查人签字**：__________  **日期**：__________
