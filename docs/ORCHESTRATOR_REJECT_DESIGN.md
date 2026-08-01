# 主链路拒识机制与 LLM 置信度校验 — 设计与验收文档

> 任务3 交付物 | 拒识层（三层漏斗第 3 层）+ LLM 输出置信度校验
> 关联代码：[orchestrator.py](../agent/orchestrator/orchestrator.py) · [config.yaml](../config.yaml) · [验证脚本](../scripts/verify_reject_mechanism.py)

---

## 1. 背景与目标

### 问题
主链路 `Orchestrator.process()` 存在两个缺陷：
1. **无未知意图拒识**：原拒识仅按 `len(input) < 3` 字符判定，无法识别"语义层未命中 + 规则层低置信度"的未知意图，任何长输入都得到 LLM 回复。
2. **LLM 输出无降级**：LLM 置信度校验仅记录 WARNING，低置信度时仍直接返回 LLM 响应，未触发兜底或转人工。

### 目标
- LLM 调用前：基于"规则层+语义层双未命中 + 语义最高分 < 阈值"实现未知意图拒识
- LLM 调用后：低置信度触发兜底回复（含转人工建议），不阻塞主链路

---

## 2. 架构设计

### 三层漏斗位置

```
用户输入
   │
   ▼
┌──────────────────┐
│ 第1层 规则层      │  WorkflowEngine + IntentRouter 模板匹配
│ (Workflow/模板)   │  命中 → 直接返回
└────────┬─────────┘
         │ 未命中
         ▼
┌──────────────────┐
│ 第2层 语义层      │  _semantic_layer_match (min_score=0.3 过滤)
│ (SkillLoader)     │  命中 → 直接返回
└────────┬─────────┘
         │ 未命中 (semantic_result = None)
         ▼
┌──────────────────┐
│ 第3层 拒识层 ★    │  _should_reject (任务3 新增)
│ (拒识判定)        │  双未命中 + 低置信度 → 拒识回复
└────────┬─────────┘
         │ 放行
         ▼
┌──────────────────┐
│ 第4层 LLM 层      │  _call_llm / _call_llm_v2
│ (LLM 调用)        │  返回响应字符串
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ LLM 置信度校验 ★  │  启发式判定（任务3 增强）
│ (低置信度降级)    │  low → 兜底文案 + 转人工建议
└────────┬─────────┘
         │ high
         ▼
   OutputGuard / 反思 / 记忆保存
```

### 拒识判定逻辑（隐式判定）

【不易】不修改 `_semantic_layer_match` 返回契约（`None` 表示未命中）
【简易】`semantic_result is None` 即视为语义最高分 < 阈值（因 min_score 已过滤）

| 判定条件 | 实现 | 说明 |
|---------|------|------|
| 规则层未命中 | 隐含 | 执行到拒识层即 WorkflowEngine + 模板均未命中 |
| 语义层未命中 | `semantic_result is None` | `_semantic_layer_match` 已用 min_score 过滤低分候选 |
| 语义最高分 < 阈值 | `semantic_result is None` 隐含 | top1.score < min_score(0.3) ≤ threshold(0.3) |
| 规则层置信度低 | `confidence` 非 HIGH | IntentRouter 返回的 Confidence 枚举 |

### LLM 置信度判定（启发式）

| 响应特征 | confidence | low_reason |
|---------|-----------|------------|
| 空响应或 `len(strip()) < 5` | low | `empty_or_too_short` |
| 含错误标记（"抱歉，处理"/"遇到了问题"/"无法完成"/"出错了"） | low | `error_marker_detected` |
| 其他 | high | `normal` |

---

## 3. 配置说明

### config.yaml（`orchestrator.reject` section）

```yaml
orchestrator:
  reject:
    enabled: true                    # 总开关
    threshold: 0.3                   # 拒识阈值（语义最高分 < 此值且双未命中时拒识）
    llm_min_confidence: 0.5          # LLM 低置信度降级阈值（预留，当前用启发式）
```

### 环境变量（优先级：环境变量 > config.yaml > 硬编码默认值）

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `ORCHESTRATOR_REJECT_ENABLED` | `true` | 拒识总开关（`false`/`0`/`no` 关闭） |
| `ORCHESTRATOR_REJECT_THRESHOLD` | `0.3` | 拒识阈值，覆盖 config.yaml |
| `ORCHESTRATOR_LLM_MIN_CONFIDENCE` | `0.5` | LLM 低置信度降级阈值，覆盖 config.yaml |
| `ORCHESTRATOR_REJECT_MIN_LENGTH` | `3` | 长度拒识阈值（保留原逻辑，补充判定） |

### 硬编码默认值（`_REJECT_DEFAULTS`，最终兜底）

```python
_REJECT_DEFAULTS = {
    "enabled": True,
    "threshold": 0.3,
    "llm_min_confidence": 0.5,
}
```

---

## 4. 验收标准

### 4.1 拒识机制验收

| 编号 | 验收项 | 验证方式 | 状态 |
|------|--------|---------|------|
| AC-1 | 规则层+语义层双未命中且语义最高分 < 阈值时返回拒识回复 | `_should_reject(intent, LOW, None)` → `True` | ✅ |
| AC-2 | 语义层命中时不拒识（放行到 LLM） | `_should_reject(intent, LOW, {score:0.8})` → `False` | ✅ |
| AC-3 | 规则层高置信度时不拒识（放行到 LLM） | `_should_reject(intent, HIGH, None)` → `False` | ✅ |
| AC-4 | 拒识返回统一文案 + 转人工建议，不抛异常 | 拒识文案含"转人工" | ✅ |
| AC-5 | 拒识可通过 `ORCHESTRATOR_REJECT_ENABLED=false` 禁用 | 禁用后返回 `False, "reject_disabled"` | ✅ |
| AC-6 | 拒识阈值可通过 `ORCHESTRATOR_REJECT_THRESHOLD` 覆盖 | 环境变量 `0.5` → cfg.threshold=0.5 | ✅ |
| AC-7 | 阈值非法值降级到 config.yaml 默认 | `"abc"` → 降级到 0.3 + WARNING 日志 | ✅ |
| AC-8 | 指代句（DST 补全过）不拒识 | `_is_ellipsis=True` → 跳过语义拒识 | ✅ |
| AC-9 | 保留原长度拒识（输入过短） | `len < 3` 且非指代句 → 拒识 | ✅ |

### 4.2 LLM 置信度校验收

| 编号 | 验收项 | 验证方式 | 状态 |
|------|--------|---------|------|
| AC-10 | LLM 空响应/过短响应判定为低置信度 | `""` / `"嗯嗯"` → low, empty_or_too_short | ✅ |
| AC-11 | LLM 含错误标记判定为低置信度 | "出错了" → low, error_marker_detected | ✅ |
| AC-12 | LLM 低置信度触发兜底回复（含转人工建议） | 兜底文案含"转人工" | ✅ |
| AC-13 | LLM 低置信度提前 return，跳过反思/向量记忆 | 兜底走 `return ResponseBuilder.success(...)` | ✅ |
| AC-14 | 兜底响应仍保存对话记忆（便于排查） | 调用 `score_and_save_message` | ✅ |

### 4.3 日志与可观测性验收

| 编号 | 验收项 | 验证方式 | 状态 |
|------|--------|---------|------|
| AC-15 | 拒识日志记录原因与各层分数 | 含 reject_type/intent/confidence/semantic_result/threshold | ✅ |
| AC-16 | `_should_reject` 各分支有 DEBUG 日志 | disabled/semantic_hit/rule_high_confidence/rejected 4 个 action | ✅ |
| AC-17 | LLM 置信度判定过程有 DEBUG 日志 | confidence_judge action 含 low_reason | ✅ |
| AC-18 | 拒识/兜底记录 trace 状态 | `trace_store.end_trace(status="rejected"/"low_confidence_fallback")` | ✅ |
| AC-19 | 拒识/兜底记录 intent_layer 指标 | `_record_intent_layer("reject"/"llm_low_confidence_fallback")` | ✅ |

### 4.4 不变量守住

| 编号 | 验收项 | 状态 |
|------|--------|------|
| AC-20 | `_call_llm` / `_call_llm_v2` 签名与返回值不变（仍是 `str`） | ✅ |
| AC-21 | `_semantic_layer_match` 返回契约不变（`None` 表示未命中） | ✅ |
| AC-22 | 拒识/兜底不抛异常，返回统一 `ResponseBuilder.success()` 格式 | ✅ |
| AC-23 | `ORCHESTRATOR_REJECT_MIN_LENGTH` 保留作为补充长度拒识 | ✅ |

---

## 5. 拒识/兜底文案

### 拒识文案（语义层+规则层双未命中）
```
抱歉，我不太理解你的意思。能否详细描述一下你想做什么？如需人工帮助，请说「转人工」。
```

### 兜底文案（LLM 低置信度）
```
抱歉，我暂时无法给出令人满意的回答。请尝试换种方式描述你的问题，或说「转人工」由人工协助处理。
```

---

## 6. 日志 Action 索引

### 拒识判定日志（`_should_reject`，DEBUG 级别）

| action | 触发条件 | 关键字段 |
|--------|---------|---------|
| `orchestrator.should_reject.disabled` | 拒识总开关关闭 | — |
| `orchestrator.should_reject.semantic_hit` | 语义层命中放行 | semantic_score, reject_threshold |
| `orchestrator.should_reject.rule_high_confidence` | 规则层高置信度放行 | confidence |
| `orchestrator.should_reject.rejected` | 拒识触发 | intent, confidence, semantic_result, reject_threshold |

### 拒识执行日志（`process`，WARNING 级别）

| action | 触发条件 | 关键字段 |
|--------|---------|---------|
| `orchestrator.process.reject` | 拒识返回文案 | reject_type, intent, confidence, semantic_result, reject_threshold, input_length, is_ellipsis |

### LLM 置信度日志

| action | 级别 | 触发条件 | 关键字段 |
|--------|------|---------|---------|
| `orchestrator.process.llm.confidence_judge` | DEBUG | 置信度判定过程 | llm_confidence, low_reason, response_length, llm_duration_ms |
| `orchestrator.process.llm.confidence` | INFO | 置信度判定结果 | llm_confidence, llm_duration_ms, response_length |
| `orchestrator.process.llm.low_confidence_fallback` | WARNING | 低置信度兜底 | original_response_preview, llm_duration_ms |

### 配置加载日志

| action | 级别 | 触发条件 |
|--------|------|---------|
| `orchestrator.reject.config.fallback` | DEBUG | config.yaml 读取失败降级 |
| `orchestrator.reject.config.invalid_threshold` | WARNING | 阈值环境变量非法值 |
| `orchestrator.reject.config.invalid_llm_confidence` | WARNING | LLM 置信度阈值非法值 |

---

## 7. 验证方式

### 自动化验证（推荐）

```bash
# 运行模拟验证脚本（5 场景，含 DEBUG 日志）
python scripts/verify_reject_mechanism.py --verbose

# 运行单元测试（17 用例）
python -m pytest tests/unit/test_orchestrator_reject.py -v

# 回归测试
python -m pytest tests/unit/test_orchestrator_refactor.py -v
```

### 手动验证

构造低置信度场景：
- 语义层未命中：输入无匹配技能的查询 + IntentRouter 低置信度 → 触发拒识
- LLM 低置信度：mock LLM 返回空响应 → 触发兜底文案

### 配置优先级验证

| 测试 | 预期 |
|------|------|
| `ORCHESTRATOR_REJECT_THRESHOLD=0.5` | cfg.threshold=0.5 |
| config.yaml `threshold: 0.4` | cfg.threshold=0.4 |
| 删除 config.yaml reject section | 降级到硬编码 0.3 |

---

## 8. 回滚方式

| 方式 | 命令 | 影响范围 |
|------|------|---------|
| 禁用拒识 | `set ORCHESTRATOR_REJECT_ENABLED=false` | 跳过拒识层，所有输入降级 LLM |
| 禁用 LLM 置信度降级 | `set ORCHESTRATOR_LLM_MIN_CONFIDENCE=0` | 当前启发式不受影响（预留阈值） |
| 删除 config.yaml reject section | — | 降级到硬编码默认 0.3 |

---

## 9. 相关文件

| 文件 | 说明 |
|------|------|
| [agent/orchestrator/orchestrator.py](../agent/orchestrator/orchestrator.py) | `_should_reject` / `_load_reject_config` / `_REJECT_DEFAULTS` / process 拒识与置信度校验 |
| [config.yaml](../config.yaml) | `orchestrator.reject` section |
| [tests/unit/test_orchestrator_reject.py](../tests/unit/test_orchestrator_reject.py) | 17 个单元测试用例 |
| [scripts/verify_reject_mechanism.py](../scripts/verify_reject_mechanism.py) | 模拟验证脚本（5 场景） |
| [docs/SEMANTIC_LAYER_CONFIG_ARCHITECTURE.md](SEMANTIC_LAYER_CONFIG_ARCHITECTURE.md) | 语义层配置架构（关联文档） |

---

## 10. 设计约束溯源（三义原则）

| 原则 | 约束 | 实现 |
|------|------|------|
| 【不易】 | 拒识阈值通过 `ORCHESTRATOR_REJECT_THRESHOLD` 配置，默认 0.3 | `_REJECT_DEFAULTS["threshold"]=0.3` + 环境变量覆盖 |
| 【不易】 | 拒识返回统一文案 + 转人工建议，不抛异常 | `ResponseBuilder.success(_reject_msg)` |
| 【不易】 | 不破坏 `_call_llm`/`_call_llm_v2`/`_semantic_layer_match` 接口契约 | 隐式判定，不改返回值 |
| 【变易】 | LLM 置信度可由 LLM 自评或后验启发式 | 当前用启发式（响应长度/错误标记），预留自评扩展 |
| 【变易】 | 阈值可与 `semantic_layer.min_score` 解耦调优 | 独立 `ORCHESTRATOR_REJECT_THRESHOLD` |
| 【简易】 | 拒识返回统一文案 + 转人工建议，不抛异常 | 软拒识，提前 return |
| 【简易】 | 隐式判定，不修改 `_semantic_layer_match` 返回契约 | `semantic_result is None` 即视为分数 < 阈值 |
