# TASK-02 上线检查清单（反思沉淀与认知评估上线）

> 适用版本：TASK-02 变更说明对应提交
> 作用：上线前人工逐项确认开关配置与日志监控点，验证观察/保守模式接线真实生效，降低灰度风险。

---

## 一、开关配置核对表（需人工确认）

### 1.1 config.yaml 三开关预期值

| # | 配置键 | 当前值 | 上线预期值 | 说明 |
|---|--------|--------|-----------|------|
| 1 | `learning.reflection_persist` | `true` | `true` | 反思产物写入向量检索面（观察模式） |
| 2 | `features.critic_evaluation_enabled` | `true` | `true` | 反思后追加规则评估，只记录不干预（保守模式） |
| 3 | `learning.experience_persist` | `false` | `false`（首期）/ `true`（确认后） | 规划经验/教训落盘，依赖 Reflector 落盘目录 |

> 【不易】三开关默认值均为 `false`：未开启时接线分支整体 inert，生产行为与上线前逐字节等价。
> 开关 1/2 已在本地上线验证中开启；开关 3 建议首期保持 `false` 观察，待规划链路（TASK-01 D7）稳定后再开。

### 1.2 环境变量覆盖（优先级最高，运维 hotfix）

| 环境变量 | 覆盖的配置键 | 有效值 |
|----------|-------------|--------|
| `LEARNING_REFLECTION_PERSIST` | `learning.reflection_persist` | `true/1/yes` → 开，否则关 |
| `CRITIC_EVALUATION_ENABLED` | `features.critic_evaluation_enabled` | `true/1/yes` → 开，否则关 |
| `LEARNING_EXPERIENCE_PERSIST` | `learning.experience_persist` | `true/1/yes` → 开，否则关 |

> ⚠️ **人工确认项**：若生产环境变量中设置了上述任一变量，将覆盖 config.yaml。上线前必须核对：
> `echo $LEARNING_REFLECTION_PERSIST $CRITIC_EVALUATION_ENABLED $LEARNING_EXPERIENCE_PERSIST`
> （期望为空或按预期设置；本地上线验证时环境变量为空，开关完全由 config.yaml 控制）

### 1.3 配置优先级（三层）

```
环境变量 > config.yaml > 硬编码默认值（_LEARNING_DEFAULTS）
```

---

## 二、日志监控点（grep 即查）

### 2.1 反思接线主链路（orchestrator.self_reflect）

| 日志 action | 级别 | 预期（开关开启后） | grep 命令 |
|------------|------|-------------------|-----------|
| `orchestrator.self_reflect.learning_gate` | INFO | 每轮对话一条，`reflection_persist=True critic_evaluation_enabled=True` | `grep "learning_gate"` |
| `orchestrator.self_reflect.eval.start` | INFO | 评估触发：`input_len / output_len` | `grep "eval.start"` |
| `orchestrator.self_reflect.eval` | INFO | 评估完成：`score=1.00 passed=True` | `grep "self_reflect.eval'"` |
| `orchestrator.self_reflect.eval.fallback` | WARNING | 评估异常降级（不应出现；出现即排查） | `grep "eval.fallback"` |
| `orchestrator.self_reflect.persist.start` | INFO | 写入开始：`反思长度=` | `grep "persist.start"` |
| `orchestrator.self_reflect.persist` | INFO | 写入成功：返回 `item_id` | `grep "self_reflect.persist'"` |
| `orchestrator.self_reflect.persist.skipped` | INFO | 检索面未初始化跳过（排查"为何没写入"先看此条） | `grep "persist.skipped"` |
| `orchestrator.self_reflect.persist.fallback` | WARNING | 写入失败静默降级（不应出现） | `grep "persist.fallback"` |

### 2.2 规划经验落盘（planning.core，开关 3 开启后）

| 日志关键字 | 级别 | 预期 |
|-----------|------|------|
| `[经验落盘] 观察模式跳过` | INFO | 开关 3=false 时每轮收尾一条 |
| `[经验落盘] 开始沉淀` | INFO | 开关 3=true 时触发 `learn_from_experience` |
| `[经验落盘] 调用失败` | WARNING | 不应出现 |

### 2.3 检索面写入验证（memory.vector_store）

| 日志关键字 | 预期 |
|-----------|------|
| `✅ 添加记忆 [Fallback]` | 反思/对话记忆真实写入检索面 |
| `🔍 搜索记忆` | 检索面查询被触发 |

### 2.4 一屏定位命令（本地/生产通用）

```bash
# 单次对话完整接线链路
grep -E "learning_gate|eval.start|self_reflect.eval|persist.start|persist'|persist.fallback" <日志> | tail -20

# 指标确认（Prometheus）
#   learning.eval.total / learning.eval.passed / learning.eval.failed
#   learning.eval.score 直方图
```

---

## 三、验证方法（上线前必跑）

```bash
# 1) 单元回归（反射管线 + 编排器重构）
python -m pytest tests/unit/test_reflection_pipeline.py tests/unit/test_orchestrator_refactor.py -p no:randomly -q

# 2) 模拟三场景（默认零写入 / 仅持久化 / 持久化+评估）
python scripts/task02_reflection_simulate.py

# 3) 完整对话验证（config.yaml 开关真实生效，2 轮对话写入真实检索面）
python scripts/task02_full_dialogue.py
#    期望输出：全部断言 PASS；检索面反思记录=2；learning.eval.total=2；响应未被拦截
```

### 人工验收点（观察/保守模式语义）

- [ ] 响应未被规则评估拦截（保守模式只记录不干预，无重试/无拦截文案）
- [ ] 反思产物写入检索面后内容前缀为 `反思(#N):`，metadata 含 `type/interaction/task_id/input_hash/score/suggestions/created_at`（schema 供 TASK-04 消费）
- [ ] `learning.eval.*` 指标随交互递增（TASK-03 度量体系消费）

---

## 四、回滚路径（一键降级）

```bash
# 方案 A：环境变量强制关闭（无需改 config，立即生效，重启进程后仍生效）
export LEARNING_REFLECTION_PERSIST=false
export CRITIC_EVALUATION_ENABLED=false
export LEARNING_EXPERIENCE_PERSIST=false

# 方案 B：config.yaml 三开关改回 false 后重启进程
```

> 回滚后 `learning_gate` 日志显示 `reflection_persist=False critic_evaluation_enabled=False`，
> 接线分支整体 inert：self_reflect 仅写内存/日志，行为与上线前一致。

---

## 五、上线后观察窗口（建议）

| 观察项 | 阈值/动作 |
|--------|----------|
| `eval.fallback` / `persist.fallback` 频次 | >0 即排查（评估器/检索面异常） |
| `learning.eval.failed` 占比 | 持续偏高 → 人工复核规则评估器 |
| 检索面反思记录增长 | 应随对话递增；停滞 → 查 `persist.skipped` |
| 对话响应耗时 | 评估为 6 维规则零 Token，P99 增量应 < 5ms；超预期 → 查 ReflectionEngine 初始化 |

---

*关联文档：TASK-02_变更说明.md（含反思产物 schema 与完整回归记录）*
