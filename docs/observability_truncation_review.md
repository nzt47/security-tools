# 可观测性截断逻辑边界条件审查报告

> 审查对象：`agent/skills_mgmt/observability.py::_sanitize_observability_payload`
> 审查日期：2026-07-20
> 审查目的：确认 `retrieved_chunks` 超过 50 项时 `truncated` 标记是否正确打标

## 1. 截断契约回顾【不易】

```python
_MAX_RETRIEVED_CHUNKS = 50

def _sanitize_observability_payload(payload):
    if not isinstance(payload, dict):
        return payload
    chunks = payload.get("retrieved_chunks")
    if isinstance(chunks, list) and len(chunks) > _MAX_RETRIEVED_CHUNKS:
        return {
            **payload,
            "retrieved_chunks": chunks[:_MAX_RETRIEVED_CHUNKS],
            "retrieved_chunks_truncated": True,
            "retrieved_chunks_original_count": len(chunks),
        }
    return payload
```

**契约要点：**
- 触发条件：`len(chunks) > 50`（严格大于，50 项不触发）
- 截断后字段：`retrieved_chunks_truncated=True` + `retrieved_chunks_original_count=N`
- 字段命名：用 `retrieved_chunks_truncated` 而非 `truncated`，避免与业务返回值 `MatchResult.truncated` 字段冲突（守不易：命名空间隔离）

## 2. 边界值分析

| 输入项数 | `len(chunks) > 50` | 触发截断 | `truncated` 标记 | 截断后 chunks 长度 | 行为正确性 |
|---------|-------------------|---------|-----------------|-------------------|-----------|
| 0 项（空 list）| False | 否 | 字段缺失 | 0 | ✅ 正确 |
| 1 项 | False | 否 | 字段缺失 | 1 | ✅ 正确 |
| 49 项 | False | 否 | 字段缺失 | 49 | ✅ 正确 |
| **50 项（边界）** | False | 否 | 字段缺失 | 50 | ✅ 正确（严格大于） |
| **51 项（首次触发）** | True | 是 | `True` | 50 | ✅ 正确 |
| 100 项 | True | 是 | `True` | 50 | ✅ 正确 |
| 1000 项 | True | 是 | `True` | 50 | ✅ 正确 |

**结论：截断边界条件正确，50/51 是临界点，符合设计意图。**

## 3. 非常规输入防御性分析

| 输入类型 | `isinstance(chunks, list)` | 行为 | 评价 |
|---------|---------------------------|------|------|
| `None` | False | 原样返回 | ✅ 防御正确 |
| dict `{"a": 1}` | False | 原样返回 | ✅ 防御正确 |
| 字符串 `"chunks"` | False | 原样返回 | ✅ 防御正确 |
| tuple `(1,2,...,60)` | False | 原样返回 | ⚠️ 不截断 tuple（潜在缺口，但调用方约定传 list） |
| list 含 None 元素 | True | 按 len 截断 | ✅ 不校验元素类型，符合"防御性但不报错"原则 |
| payload 非 dict（如 None）| - | 立即原样返回 | ✅ 第一道防线 |

## 4. 截断应用点审查

`_sanitize_observability_payload` 在 3 处被调用：

| # | 调用位置 | 文件:行 | 触发时机 | 评估 |
|---|---------|--------|---------|------|
| 1 | `traced_action` 入口 | observability.py:89 | start 日志前清洗 payload | ✅ 正确 |
| 2 | `traced_action` 出口 | observability.py:102 | end 日志前清洗 merged ctx | ✅ 正确 |
| 3 | `report_retrieval_observability` | observability.py:276 | 持久化 span 前清洗 | ✅ 正确 |

## 5. 潜在防御性缺口【变易】

### 缺口 1：`persist_observability_span` 不自行截断

**位置：** `observability.py:157-188`

**问题：** 该函数直接把 `**fields` 透传给 `record_span_attributes`，不调用 `_sanitize_observability_payload`。

**风险评估：**
- **当前调用方**只有 2 个：
  1. `report_retrieval_observability`（line 279）— **已预先清洗**，传入的 `retrieved_chunks` 已截断 ✅
  2. `emit_eval_score_metric`（line 243）— 传入 `eval_score` 字段，**不传 `retrieved_chunks`**，无截断需求 ✅
- **未来风险：** 若新增调用方直接传未清洗的 `retrieved_chunks`，会绕过截断契约

**建议（不强制）：** 在 `persist_observability_span` 入口增加一次防御性清洗，作为最后一道防线。代码：

```python
def persist_observability_span(*, trace_id=None, **fields):
    try:
        from agent.monitoring.tracing import record_span_attributes
        # [变易] 最后一道防线：即便调用方漏清洗也兜底
        fields = _sanitize_observability_payload(dict(fields))
        record_span_attributes(trace_id=trace_id, **fields)
        ...
```

**权衡：** 多一次清洗（O(N) 切片）vs 防御性深度。考虑到 50 项以内切片极快，且 _sanitize 内部对非 list 直接返回，建议加上。

### 缺口 2：`emit_eval_score_metric` 中 `eval_score` 不截断

**位置：** `observability.py:243-249`

**问题：** `eval_score` 是 dict，不会被 `_sanitize_observability_payload` 处理（该函数只关心 `retrieved_chunks` 字段）。如果 `eval_score` 包含超大字段（如完整轨迹），会原样写入 span。

**风险评估：** 低。`eval_score` 结构由代码定义（4 个标量字段），不存在过大风险。

**建议：** 无需改动，但应在 `emit_eval_score_metric` 文档中明确 `eval_score` 的预期 schema。

### 缺口 3：`traced_action` 中 ctx 自定义字段未清洗

**位置：** `observability.py:96-102`

**问题：** `safe_merged` 合并 ctx 字段后清洗，但只清洗 `retrieved_chunks` 一个字段。若 ctx 中放入其他大对象（如 `instructions` 全文、`tool_outputs` 大数据），不会被截断。

**风险评估：** 中。`traced_action` 是通用上下文管理器，无法预知所有大对象字段。

**建议：** 不在通用层处理，由各调用方负责自身大对象字段。在 `traced_action` 文档中加注释提示。

## 6. 验证方法

可通过以下单元测试覆盖边界条件（已存在或建议新增）：

```python
@pytest.mark.parametrize("count,expected_truncated,expected_len", [
    (0, False, 0),
    (1, False, 1),
    (49, False, 49),
    (50, False, 50),   # 临界点：不截断
    (51, True, 50),    # 首次截断
    (100, True, 50),
    (1000, True, 50),
])
def test_sanitize_truncation_boundary(count, expected_truncated, expected_len):
    chunks = [{"skill_id": f"s{i}", "score": 0.1} for i in range(count)]
    payload = {"retrieved_chunks": chunks, "trace_id": "t1"}
    result = _sanitize_observability_payload(payload)
    assert ("retrieved_chunks_truncated" in result) == expected_truncated
    assert len(result["retrieved_chunks"]) == expected_len
    if expected_truncated:
        assert result["retrieved_chunks_original_count"] == count
        assert result["retrieved_chunks_truncated"] is True
```

## 7. 结论【简易】

✅ **截断逻辑正确：** 50/51 临界点处理符合设计意图，`truncated` 标记在 51 项及以上时正确打标。

⚠️ **存在 3 处防御性缺口（非阻塞）：**
1. `persist_observability_span` 不自行截断 — **建议补**（最后一道防线）
2. `emit_eval_score_metric` 中 `eval_score` 不截断 — 无需改动
3. `traced_action` 中其他大对象字段未清洗 — 无需改动，文档提示即可

**最终判定：超过 50 项时 `truncated` 标记正确打标，可放心接入生产。**
