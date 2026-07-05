# ResponseBuilder.workflow_result 签名不匹配修复方案

## 问题描述

`ResponseBuilder.workflow_result` 方法当前签名为 `workflow_result(result: Any = None)`，
而 `test_orchestrator_refactor.py` 期望 `workflow_result(output=, intent=, confidence=)`，
导致 2 个测试失败（TypeError: got an unexpected keyword argument 'output'）。

## 影响范围

共 6 个调用点：

| 文件 | 行号 | 调用方式 | 需修改 |
|------|------|----------|--------|
| `agent/orchestrator/response_builder.py` | 58 | 定义处 | 是 |
| `agent/orchestrator/orchestrator.py` | 180 | `workflow_result(workflow_result.output)` | 是 |
| `tests/unit/test_response_builder.py` | 49 | `workflow_result(result={...})` | 是 |
| `tests/unit/test_message_handler.py` | 60 | `workflow_result({"msg": "ok"})` | 是 |
| `tests/unit/test_orchestrator_refactor.py` | 116 | `workflow_result(output=, intent=, confidence=)` | 否（期望新签名） |
| `tests/unit/test_orchestrator_refactor.py` | 131 | `workflow_result()` | 否（期望新默认值） |

## 推荐方案：方向 A（修改主代码签名）

### 修改 1：`agent/orchestrator/response_builder.py`

```python
@staticmethod
def workflow_result(output: Any = "", intent: str = "", confidence: float = 1.0) -> Response:
    return Response(
        success=True,
        data={"output": output, "intent": intent, "confidence": confidence},
        msg="handled_by_workflow",
    )
```

### 修改 2：`agent/orchestrator/orchestrator.py` 第 180 行

```python
# 修改前
return ResponseBuilder.workflow_result(workflow_result.output).to_dict()

# 修改后
return ResponseBuilder.workflow_result(
    output=workflow_result.output,
    intent=workflow_result.intent,
    confidence=workflow_result.confidence,
).to_dict()
```

### 修改 3：`tests/unit/test_response_builder.py` 第 48-52 行

```python
# 修改后
def test_workflow_result_response(self):
    r = ResponseBuilder.workflow_result(output="done", intent="search", confidence=0.9)
    assert r.success is True
    assert r.data["output"] == "done"
    assert r.data["intent"] == "search"
    assert r.data["confidence"] == 0.9
    assert r.msg == "handled_by_workflow"
```

### 修改 4：`tests/unit/test_message_handler.py` 第 59-62 行

```python
# 修改后
def test_workflow_result(self):
    resp = ResponseBuilder.workflow_result(output="ok")
    assert resp.success
    assert resp.data["output"] == "ok"
```

## 备选方案：方向 B（保留 result 参数，改测试）

仅修改 `test_orchestrator_refactor.py` 的 2 个测试用例，保留 `result: Any = None` 签名。
改动最小但丢失 intent/confidence 语义化信息。

## 验证命令

```bash
python -m pytest tests/unit/test_orchestrator_refactor.py tests/unit/test_response_builder.py tests/unit/test_message_handler.py --override-ini=addopts= -p no:cacheprovider --tb=short -v
```
