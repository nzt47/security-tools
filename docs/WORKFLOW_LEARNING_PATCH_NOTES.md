# 工作流学习层补丁变更说明（代码审查用）

> 变更范围：`agent/workflow_learning/`（executor / service / matcher）+
> `tests/unit/test_workflow_learning.py` 回归测试。
> 触发场景：自动闭环模拟压测（`scripts/simulate_workflow_closed_loop.py`）中发现的两个缺陷。
> 审查重点：单文档退化修复是否改变多文档语义；min_score 透传是否影响既有调用方。

---

## 一、变更总览

| # | 补丁 | 文件 | 风险等级 |
|---|------|------|---------|
| 1 | min_score 覆盖参数透传（executor → service 两层） | `executor.py` / `service.py` | 低 |
| 2 | TF-IDF 单文档平滑下界（`_idf` 恒正） | `matcher.py` | 中 |

两个补丁独立，均可单独回滚。

---

## 二、补丁 1：min_score 透传（拦截层按层配置阈值）

### 2.1 背景

orchestrator 工作流拦截层在 config.yaml 中配置了独立阈值
`workflow_learning_layer.min_score: 0.25`（低于 executor 构造默认值 0.3），
意图是"拦截层更宽松、执行层更严格"。但修复前 `try_execute` 的签名只有
`(task_text, *, params=None)`，`_workflow_learning_layer_match` 传入的
`min_score=0.25` 直接触发 `TypeError`：

```
WorkflowLearningService.try_execute() got an unexpected keyword argument 'min_score'
```

→ 拦截层整个异常降级，自动闭环前置拦截钩子形同虚设。

### 2.2 变更内容

**`agent/workflow_learning/executor.py`** — `WorkflowExecutor.try_execute`（L151-167）：

```python
def try_execute(self, task_text: str, *,
                params: Optional[Dict[str, Any]] = None,
                min_score: Optional[float] = None) -> WorkflowExecutionResult:
    """...
    min_score: 覆盖本次执行的匹配阈值；None 时使用构造时默认值
    """
    score_threshold = self.min_score if min_score is None else min_score
```

**`agent/workflow_learning/service.py`** — `WorkflowLearningService.try_execute`：

```python
def try_execute(self, task_text: str, *,
                params: Optional[Dict[str, Any]] = None,
                min_score: Optional[float] = None) -> WorkflowExecutionResult:
    return self.executor.try_execute(task_text, params=params, min_score=min_score)
```

### 2.3 兼容性分析

- 两个参数均为**可选关键字参数**，默认值 `None` 表示"使用构造时默认值"；
- 既有调用方（`try_execute(task_text, params=...)`）不受影响；
- 唯一行为差异：显式传 `min_score` 时覆盖阈值，未传时行为与修复前完全一致。

---

## 三、补丁 2：TF-IDF 单文档平滑下界（`_idf` 恒正）

### 3.1 背景

自动闭环模拟压测中发现：学习并落库**第一个**工作流后，`svc.try_execute()` 永远返回
`matched=False`，即使输入文本与该工作流的触发文本**完全相同**。

根因在 `matcher.py` 的 TF-IDF 实现：单文档退化（文档数 `N == df == 1`）时

```
idf = log((N + 1) / (1 + df)) = log(2 / 2) = log(1) = 0
```

→ 所有权重归零 → 向量长度为 0 → 余弦相似度恒为 0 → 首个工作流永远无法匹配
（自动闭环"首环失效"）。

### 3.2 变更内容

**`agent/workflow_learning/matcher.py`** — 新增平滑 IDF 函数：

```python
def _idf(n_docs: int, df: int) -> float:
    """平滑 IDF：加下界 0.001 保证恒正；多文档场景原值>0 不受影响。"""
    return max(math.log((n_docs + 1) / (1 + df)), 0.001)
```

`TfidfIndex._rebuild` 与 `query` 两处统一改用 `_idf(N, df)`。

### 3.3 语义影响分析

- **单文档退化（N==df==1）**：修复后 idf=0.001（恒正）→ 归一化后各文档向量同构，
  相似度退化为**纯 TF 余弦（词重叠度）**：完全相同文本 ≈1.0，部分重叠 >0；
- **多文档场景**：`(N+1)/(1+df) > 1` 时 `log(...) > 0`，下界 0.001 不生效，
  原 IDF 语义完全保留；
- **验证**：`tests/unit/test_workflow_learning.py` 新增单文档匹配回归用例
  （首个工作流相同文本命中 + 部分重叠正分）。

### 3.4 风险

下界仅 0.001，数值上可忽略；唯一注意点是单文档场景退化为纯词重叠匹配，
建议自动闭环落地后观察真实命中质量（`scripts/parse_wfl_interception_logs.py`
可监控命中率与 score 分桶）。

---

## 四、回归验证

```powershell
python -m pytest tests/unit/test_workflow_learning.py -q
python scripts/simulate_workflow_closed_loop.py   # 8 轮闭环场景全通过
```

## 五、三义自检

- **不易**：`try_execute` 默认行为不变；多文档 IDF 语义不变；仅修单文档退化；
- **变易**：阈值可按层覆盖（config/env 均可配）；IDF 下界为常量可调；
- **简易**：两处改动均 ≤ 15 行，无新依赖，无抽象层新增。
