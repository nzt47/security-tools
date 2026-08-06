# 规则关键词外置 + DST 连续省略句回归测试报告

- **日期**: 2026-08-06
- **范围**: 规则层 7 条变体关键词 / DST 连续省略句补全 / Python 3.12 兼容补丁
- **结论**: **全部通过**（含真实会话链路修复验证）

---

## 1. 背景

上一轮完成三项改动后需全量回归：

| 改动 | 文件 |
|---|---|
| 规则层关键词外置到 `.env`（追加合并去重） | `agent/workflow_engine/builtin_rules.py` + `.env` / `.env.example` |
| confirmation 默认关键词补齐"没问题" | `agent/workflow_engine/builtin_rules.py` |
| DST 连续省略句回归用例 + DialogStateStub 状态同步修复 | `scripts/test_three_layer_funnel.py` |
| Python 3.12 multiprocess `_recursion_count` 兼容 | `agent/utils/compatibility.py` + `agent/orchestrator/lifecycle_manager.py` |
| 会话链路省略句级联污染修复 | `agent/orchestrator/orchestrator.py`（本次报告新增） |

## 2. 7 条变体关键词验证（规则层命中明细）

`scripts/test_three_layer_funnel.py --scenario normal --no-vector --verbose`，10 条 rule 命中（conf=1.00）：

| 输入 | 命中规则 | 来源 |
|---|---|---|
| 现在几点 | check_time | 默认关键词 |
| 现在几点了 | check_time | **env 变体** |
| 今天日期 | check_date | 默认关键词 |
| 今天几号 | check_date | **env 变体** |
| 你好 | check_health | 默认关键词 |
| 你好呀 | check_health | **env 变体** |
| 早上好 | greeting | **env 变体** |
| 谢谢啦 | thanks | **env 变体** |
| 拜拜 | farewell | **env 变体** |
| 没问题 | confirmation | **env 变体（原缺失项，已补齐）** |

- **7 条变体全部 rule 层命中**，命中方式为关键词子串包含（keyword_match）。
- env 值采用**追加合并去重**：与默认关键词取并集，配错/留空时默认值兜底，规则永不失效。

## 3. DST 连续省略句验证

### 3.1 funnel 层回归（8/8 PASS）

锚点 `"帮我总结一下这个文件夹里的所有文档内容"` 被 6 条省略句稳定继承：

```
[PASS] 帮我总结一下这个文件夹里的所有文档内容  -> None（锚点，刷新上下文）
[PASS] 然后呢   -> '继续 帮我总结一下这个文件夹里的所有文档内容'
[PASS] 那个呢   -> '关于 帮我总结一下这个文件夹里的所有文档内容 呢'
[PASS] 再来一个  -> '继续 帮我总结一下这个文件夹里的所有文档内容'
[PASS] 还有呢   -> '继续 帮我总结一下这个文件夹里的所有文档内容'
[PASS] 嗯      -> None（无关键词不刷新上下文）
[PASS] 那个呢   -> '关于 帮我总结一下这个文件夹里的所有文档内容 呢'
[PASS] 然后呢   -> '继续 帮我总结一下这个文件夹里的所有文档内容'
连续省略句回归: 8 条, FAIL=0
```

### 3.2 会话级验证（真实链路，修复前 → 修复后）

`DigitalLife.chat()` 单进程连续 8 轮，观测 orchestrator `[DST] 省略句补全` 埋点：

| 轮 | 输入 | 修复前 | 修复后 |
|---|---|---|---|
| 1 | 帮我总结这个文件夹... | 锚点（刷新上下文） | 锚点（刷新上下文） |
| 2 | 然后呢 | `继续 帮我总结...` ✅ | `继续 帮我总结...` ✅ |
| 3 | 那个呢 | `关于 **然后呢** 呢` ❌ | `关于 帮我总结... 呢` ✅ |
| 4 | 再来一个 | `继续 **那个呢**` ❌ | `继续 帮我总结...` ✅ |
| 5 | 还有呢 | `继续 **再来一个**` ❌ | `继续 帮我总结...` ✅ |
| 6 | 嗯 | 兜底响应（不补全） | 兜底响应（不补全） |
| 7 | 那个呢 | `关于 **还有呢** 呢` ❌ | `关于 帮我总结... 呢` ✅ |
| 8 | 然后呢 | `继续 **那个呢**` ❌ | `继续 帮我总结...` ✅ |

- 全程 8 轮无崩溃、无 0xC0000005。
- 真实会话链路省略句可流畅连续追问（回复稳定走 `# memory_summary` / `# 主动建议` 模板）。

## 4. 会话链路级联污染 Bug（本次报告新增修复）

- **现象**: funnel 回归 8/8 通过（假阳性），但真实会话链路省略句互相污染。
- **根因**: `agent/orchestrator/orchestrator.py` 路由后无条件
  `_update_dst_after_route(intent, None, user_input)`，用**原始省略句文本**提取关键词
  覆盖 `last_keywords`；funnel 脚本已实现"省略句保留上下文"守卫，真实链路遗漏。
- **修复**: 省略句（`routing_input != user_input`，即本轮已发生 DST 补全）仅回写
  intent，keywords/user_input 传 None（`dialog_state.update` 对 None 有守卫，不覆盖）:

```python
if routing_input != user_input:
    self._update_dst_after_route(intent, None, None)   # 省略句：保留上一轮真实查询
else:
    self._update_dst_after_route(intent, None, user_input)
```

## 5. 三层漏斗场景统计（回归未破坏）

| 场景 | rule | template | semantic | llm | reject |
|---|---|---|---|---|---|
| normal | 47.6% (10) | 0% | 0% | 42.9% (9) | 9.5% (2) |
| rule_off | 0% | 0% | 0% | 90.5% (19) | 9.5% (2) |
| semantic_off | 47.6% (10) | 0% | 0% | 42.9% (9) | 9.5% (2) |
| both_off | 0% | 0% | 0% | 90.5% (19) | 9.5% (2) |

降级兜底机制符合预期：关闭某层后流量转移至后续层，LLM 兜底占比递增。

## 6. 配套验证

- **单元测试**: `tests/unit/test_workflow_engine_comprehensive.py` 110/110（含新增
  `TestRuleKeywordEnv` 5 条：追加去重/缺配兜底/空白兜底/env 新词命中/confirmation 补齐）。
- **Python 3.12 shutdown 告警**: `apply_multiprocess_compat_patch()` 注入成功且幂等，
  multiprocess 真实进程运行+退出无 "Exception ignored"。
- **0xC0000005 崩溃修复**: 预导入 sentence_transformers 成功（本次实测 46s），
  VectorStore 复用 `sys.modules` 避开崩溃路径，启动全程无崩溃。

## 7. 结论

| 验证项 | 结果 |
|---|---|
| 7 条变体关键词 rule 层命中 | 7/7 ✅ |
| DST 连续省略句（funnel 回归） | 8/8 ✅ |
| DST 连续省略句（真实会话链路） | 8/8 ✅（修复后） |
| 三层漏斗降级兜底 | 4 场景符合预期 ✅ |
| 规则层单测 | 110/110 ✅ |
| multiprocess shutdown 告警 | 已消除 ✅ |
| 0xC0000005 崩溃 | 未复现 ✅ |

**结论**: 规则关键词外置与 DST 连续省略句补全在真实链路与回归脚本层均验证通过，
会话链路级联污染 Bug 已修复并经会话级复验确认。
