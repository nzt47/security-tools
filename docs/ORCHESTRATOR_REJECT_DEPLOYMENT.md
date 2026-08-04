# 主链路拒识机制 — 环境部署说明

> 任务3 部署文档 | 拒识阈值与日志级别配置指南
> 关联设计文档：[ORCHESTRATOR_REJECT_DESIGN.md](ORCHESTRATOR_REJECT_DESIGN.md)
> 关联代码：[config.yaml](../config.yaml) · [orchestrator.py](../agent/orchestrator/orchestrator.py)

---

## 1. 部署概览

拒识机制已内置在主链路 `Orchestrator.process()` 中，**无需额外安装**，仅需配置阈值与日志级别即可生效。

### 部署检查清单

- [ ] config.yaml `orchestrator.reject` section 已配置
- [ ] config.yaml `logging.modules.agent.orchestrator` 日志级别已配置
- [ ] 环境变量按需设置（可选，覆盖 config.yaml）
- [ ] 验证脚本通过：`python scripts/verify_reject_mechanism.py`
- [ ] 单元测试通过：`python -m pytest tests/unit/test_orchestrator_reject.py -v`

---

## 2. 配置项清单

### 2.1 config.yaml 配置（`orchestrator.reject` section）

```yaml
orchestrator:
  reject:
    enabled: true                    # 拒识总开关
    threshold: 0.3                   # 拒识阈值（语义最高分 < 此值且双未命中时拒识）
    llm_min_confidence: 0.5          # LLM 低置信度降级阈值（预留）
```

### 2.2 config.yaml 日志级别（`logging.modules`）

```yaml
logging:
  modules:
    agent.orchestrator: INFO         # 生产默认；排查时改 DEBUG
```

| 级别 | 可见日志 | 适用场景 |
|------|---------|---------|
| `INFO` | 拒识执行 WARNING + LLM 置信度 INFO + 兜底 WARNING | **生产默认** |
| `DEBUG` | 上述 + `_should_reject` 各分支判定 + LLM 置信度判定过程（low_reason） | 排查拒识/置信度问题 |
| `WARNING` | 仅拒识执行 + 兜底 WARNING | 降低日志噪音 |

### 2.3 环境变量（优先级：环境变量 > config.yaml > 硬编码默认值）

| 环境变量 | 默认值 | 说明 | 覆盖 config.yaml |
|---------|--------|------|----------------|
| `ORCHESTRATOR_REJECT_ENABLED` | `true` | 拒识总开关（`false`/`0`/`no` 关闭） | `orchestrator.reject.enabled` |
| `ORCHESTRATOR_REJECT_THRESHOLD` | `0.3` | 拒识阈值 | `orchestrator.reject.threshold` |
| `ORCHESTRATOR_LLM_MIN_CONFIDENCE` | `0.5` | LLM 低置信度降级阈值 | `orchestrator.reject.llm_min_confidence` |
| `ORCHESTRATOR_REJECT_MIN_LENGTH` | `3` | 长度拒识阈值（补充判定，保留原逻辑） | — |

### 2.4 硬编码默认值（`_REJECT_DEFAULTS`，最终兜底）

```python
_REJECT_DEFAULTS = {
    "enabled": True,
    "threshold": 0.3,
    "llm_min_confidence": 0.5,
}
```

> 当 config.yaml 缺失或解析失败时，降级到硬编码默认值，确保主链路不中断。

---

## 3. 部署步骤

### 3.1 标准部署（默认配置）

拒识机制开箱即用，默认配置已写入 config.yaml，无需额外操作：

```bash
# 1. 验证 config.yaml 配置
python -c "import yaml; d=yaml.safe_load(open('config.yaml','r',encoding='utf-8')); print('reject:', d['orchestrator']['reject']); print('log:', d['logging']['modules']['agent.orchestrator'])"

# 2. 运行验证脚本
python scripts/verify_reject_mechanism.py --verbose

# 3. 运行单元测试
python -m pytest tests/unit/test_orchestrator_reject.py -v
```

### 3.2 自定义阈值部署

**场景**：生产环境需调高拒识阈值（如 0.4），避免误拒。

#### 方式 A：修改 config.yaml（推荐，持久化）

```yaml
orchestrator:
  reject:
    enabled: true
    threshold: 0.4       # 调高阈值
    llm_min_confidence: 0.5
```

#### 方式 B：环境变量（运维 hotfix，无需改文件）

```bash
# Windows PowerShell
set ORCHESTRATOR_REJECT_THRESHOLD=0.4

# Linux/macOS
export ORCHESTRATOR_REJECT_THRESHOLD=0.4
```

### 3.3 日志级别切换

**场景**：排查拒识问题时，需查看判定过程日志。

#### 方式 A：修改 config.yaml（持久化）

```yaml
logging:
  modules:
    agent.orchestrator: DEBUG    # 改为 DEBUG 查看判定过程
```

#### 方式 B：验证脚本自带 verbose 模式（临时）

```bash
python scripts/verify_reject_mechanism.py --verbose
```

> 排查完毕后，建议将日志级别恢复为 `INFO`，避免 DEBUG 日志量过大。

---

## 4. 验证方式

### 4.1 自动化验证（推荐）

```bash
# 1. 模拟验证脚本（5 场景，含 DEBUG 日志）
python scripts/verify_reject_mechanism.py --verbose

# 2. 23 条验收标准测试
python -m pytest tests/unit/test_orchestrator_reject.py::TestAcceptanceCriteria -v

# 3. 全量回归测试
python -m pytest tests/unit/test_orchestrator_reject.py tests/unit/test_orchestrator_refactor.py -v
```

### 4.2 配置优先级验证

| 测试 | 命令 | 预期 |
|------|------|------|
| 环境变量覆盖 | `set ORCHESTRATOR_REJECT_THRESHOLD=0.5 && python -c "from agent.orchestrator.orchestrator import Orchestrator; print(Orchestrator._load_reject_config()['threshold'])"` | `0.5` |
| config.yaml 生效 | 修改 `config.yaml` 中 `threshold: 0.4` | `0.4` |
| 非法值降级 | `set ORCHESTRATOR_REJECT_THRESHOLD=abc` | `0.3`（降级 + WARNING） |
| 硬编码兜底 | 删除 config.yaml 中 `reject` section | `0.3`（硬编码默认） |

### 4.3 拒识/兜底生效验证

构造低置信度场景，观察日志与返回文案：

```bash
# verbose 模式可见拒识判定过程
python scripts/verify_reject_mechanism.py --verbose 2>&1 | findstr "拒识判定"
```

预期日志（DEBUG 级别）：
```
[DEBUG] orchestrator.should_reject.rejected: 规则层+语义层双未命中 + 低置信度 → 拒识
[DEBUG] orchestrator.process.llm.confidence_judge: 置信度判定: low (reason=empty_or_too_short)
```

---

## 5. 回滚方案

### 5.1 完全禁用拒识（保留代码，关闭开关）

```bash
# 环境变量方式（即时生效，无需重启服务）
set ORCHESTRATOR_REJECT_ENABLED=false

# 或 config.yaml 方式（需重启服务）
# orchestrator:
#   reject:
#     enabled: false
```

### 5.2 仅禁用 LLM 置信度降级

当前 LLM 置信度判定为启发式（空响应/错误标记 → low），`ORCHESTRATOR_LLM_MIN_CONFIDENCE` 为预留阈值，暂不影响启发式判定。如需禁用兜底，需调整 `orchestrator.py` 中 `_llm_confidence` 判定逻辑。

### 5.3 恢复默认配置

```bash
# 清除所有环境变量覆盖
set ORCHESTRATOR_REJECT_ENABLED=
set ORCHESTRATOR_REJECT_THRESHOLD=
set ORCHESTRATOR_LLM_MIN_CONFIDENCE=

# 恢复 config.yaml 默认值（见 2.1 节）
```

---

## 6. 日志 Action 速查

### 拒识相关日志（按级别）

| action | 级别 | 触发条件 | 排查用途 |
|--------|------|---------|---------|
| `orchestrator.should_reject.disabled` | DEBUG | 拒识禁用 | 为何未拒识 |
| `orchestrator.should_reject.semantic_hit` | DEBUG | 语义层命中放行 | 为何放行 |
| `orchestrator.should_reject.rule_high_confidence` | DEBUG | 规则层高置信度放行 | 为何放行 |
| `orchestrator.should_reject.rejected` | DEBUG | 拒识触发 | 拒识判定上下文 |
| `orchestrator.process.reject` | WARNING | 拒识返回文案 | 拒识执行（含各层分数） |
| `orchestrator.process.llm.confidence_judge` | DEBUG | LLM 置信度判定 | 为何判 low |
| `orchestrator.process.llm.confidence` | INFO | LLM 置信度结果 | 置信度监控 |
| `orchestrator.process.llm.low_confidence_fallback` | WARNING | 低置信度兜底 | 兜底触发 |

### 日志过滤命令

```bash
# 查看所有拒识日志
findstr "should_reject" logs/agent.log

# 查看拒识执行（WARNING 级别）
findstr "orchestrator.process.reject" logs/agent.log

# 查看 LLM 低置信度兜底
findstr "low_confidence_fallback" logs/agent.log
```

---

## 7. 关键文件清单

| 文件 | 说明 |
|------|------|
| [config.yaml](../config.yaml) | `orchestrator.reject` section + `logging.modules.agent.orchestrator` |
| [agent/orchestrator/orchestrator.py](../agent/orchestrator/orchestrator.py) | `_should_reject` / `_load_reject_config` / `_REJECT_DEFAULTS` |
| [tests/unit/test_orchestrator_reject.py](../tests/unit/test_orchestrator_reject.py) | 40 个测试（17 基础 + 23 AC 验收） |
| [scripts/verify_reject_mechanism.py](../scripts/verify_reject_mechanism.py) | 模拟验证脚本（5 场景） |
| [docs/ORCHESTRATOR_REJECT_DESIGN.md](ORCHESTRATOR_REJECT_DESIGN.md) | 设计与验收标准文档（23 条 AC） |

---

## 8. 常见问题

### Q1: 拒识阈值与语义层 min_score 的关系？

- `orchestrator.semantic_layer.min_score`（0.3）：语义层命中下限，top1.score < 此值时 `_semantic_layer_match` 返回 None
- `orchestrator.reject.threshold`（0.3）：拒识阈值，双未命中时判定
- 两者独立配置，默认值相同（0.3），可独立调优
- 当 `semantic_result is None` 时，隐含 top1.score < min_score，即满足拒识条件

### Q2: 生产环境应该用 INFO 还是 DEBUG？

**生产用 `INFO`**。DEBUG 级别会输出每次 `_should_reject` 的判定过程，日志量大。仅在排查拒识问题时临时切换为 DEBUG。

### Q3: 如何确认拒识已生效？

```bash
# 1. 运行验证脚本
python scripts/verify_reject_mechanism.py

# 2. 检查日志（生产 INFO 级别可见 WARNING）
findstr "orchestrator.process.reject" logs/agent.log
```

### Q4: 禁用拒识后，所有输入都会到 LLM 吗？

是。`ORCHESTRATOR_REJECT_ENABLED=false` 后，跳过拒识层，所有规则层+语义层未命中的输入都会到 LLM。长度拒识（`ORCHESTRATOR_REJECT_MIN_LENGTH`）仍保留。
