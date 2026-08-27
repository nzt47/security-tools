# entry_assigned 异常时序监控实现方案（初步）

> **编制时间**: 2026-06-26
> **关联报告**: [visibility_improvement_summary.md](visibility_improvement_summary.md) §5.2 / §六-5
> **状态**: 初步方案（待评审）

---

## 一、背景与目标

### 1.1 问题来源

在可见性改造过程中，`api_voice_listen` 的 except 块存在 NameError 风险：若异常发生在
`_tid_entry` 赋值之前（如 `request.get_json()` 抛出 BadRequest），except 块直接引用
`_tid_entry` 会导致日志记录本身失败，违反「日志不得影响主流程」原则。

修复方案在 except 块中引入 `entry_assigned` 布尔字段，标记 `_tid_entry` 是否在异常前
已成功赋值。**但经核查，当前 `routes_chat.py` 中该字段尚未实际落地**（HEAD 与工作区
均无 `_tid_entry`/`entry_assigned`）。因此本方案首先补齐字段，再建立监控。

### 1.2 监控目标

| 目标 | 说明 |
|------|------|
| 异常时序可观测 | 区分「参数解析前异常」与「参数解析后异常」，暴露 BadRequest 类故障 |
| 链路基准可用 | entry 日志携带初始 trace_id，下游节点可比对 trace_id 是否漂移 |
| 自动告警 | `entry_assigned=false` 持续出现时触发告警，而非仅靠人工看日志 |

---

## 二、现状评估（证据）

| 文件 | 状态 | 证据 |
|------|------|------|
| `routes_dashboard.py` | ✅ 已落地（已提交） | HEAD 含 `_tid_entry`/`quality.entry`/`trace_id_changed`（L747-823） |
| `routes_chat.py` | ❌ 未落地 | HEAD 与工作区均无 `_tid_entry`/`entry_assigned`/`get_trace_id` 导入 |
| `log_dict()` | ✅ 自动补字段 | 缺 `trace_id`/`module_name`/`action`/`duration_ms` 时自动填充（logging_utils.py L166-173） |
| 告警规则文件 | ✅ 存在 | `deploy/monitoring/prometheus/alert_rules.yml`，已有 VisibilityExporterDown 等规则 |

**结论**: 监控依赖的 `entry_assigned` 字段必须先补齐到 `routes_chat.py`，方案分「补字段」+「建监控」两阶段。

---

## 三、技术方案

### 3.1 阶段一：补齐 `entry_assigned` 字段（routes_chat.py）

在 `api_voice_listen` 中：

1. **预初始化基准变量**（try 块之前）：
```python
def api_voice_listen():
    # 预初始化：确保 except 块安全引用（防 NameError：request.get_json 可能抛出 BadRequest）
    _vl_start = time.time()
    _tid_entry = get_trace_id() or "no-trace"
    _entry_assigned = True
    try:
        data = request.get_json() or {}
        ...
```

2. **entry 阶段日志**（请求体解析后，关键参数 + 基准 trace_id）：
```python
        _tid_entry = get_trace_id() or _tid_entry
        logger.info(log_dict({
            'module_name': 'routes_chat',
            'action': 'voice_listen.entry',
            'phase': 'entry',
            'trace_id': _tid_entry,
            'params': {'duration': duration, 'raw_duration': data.get('duration', 5)},
            'duration_ms': 0,
        }))
```

3. **except 块安全引用 + 时序标记**：
```python
    except Exception as e:
        _tid_err = get_trace_id() or "no-trace"
        # 安全引用：若异常发生在赋值前，_tid_entry 保持预初始化值
        logger.error(log_dict({
            'module_name': 'routes_chat',
            'action': 'voice_listen.error',
            'phase': 'exception',
            'trace_id': _tid_err,
            'trace_id_entry': _tid_entry,
            'trace_id_changed': _tid_err != _tid_entry,
            'entry_assigned': _entry_assigned,
            'error': str(e),
            'duration_ms': round((time.time() - _vl_start) * 1000, 2),
        }))
        return jsonify({"ok": False, "error": str(e)}), 500
```

> 说明：`entry_assigned` 语义 —— 预初始化置 `True` 表示「trace_id 基准已可用」；
> 若未来改为「参数解析成功后才置 True」，则 except 中可区分「解析前异常」。

### 3.2 阶段二：指标暴露

在 `routes_chat.py` 的 Prometheus 区块新增 Counter（与既有 `yunshu_security_blocks_total` 并列）：

```python
VOICE_ENTRY_UNASSIGNED = _Counter(
    'yunshu_voice_entry_unassigned_total',
    'Total voice_listen requests where entry_assigned=false (exception before param parse)',
    ['phase']
)
```

except 块中触发：

```python
        if not _entry_assigned and PROMETHEUS_AVAILABLE and VOICE_ENTRY_UNASSIGNED:
            VOICE_ENTRY_UNASSIGNED.labels(phase='pre_parse').inc()
```

### 3.3 阶段三：告警规则

追加到 `deploy/monitoring/prometheus/alert_rules.yml`（沿用现有格式）：

```yaml
      - alert: VoiceEntryUnassignedHigh
        expr: increase(yunshu_voice_entry_unassigned_total[10m]) > 3
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "语音接口异常发生在参数解析前"
          description: "10 分钟内 entry_assigned=false 次数超过 3 次，可能为 BadRequest 或客户端协议异常"
```

### 3.4 阶段四：仪表盘面板（可选）

在既有 dashboard 的「追踪」页新增面板：

- **图表**: 柱状图 / 计数器
- **查询**: `sum(rate(yunshu_voice_entry_unassigned_total[5m]))`
- **面板标题**: 「语音接口参数解析前异常率」

---

## 四、实施步骤

| 步骤 | 内容 | 涉及文件 | 验收标准 |
|------|------|---------|---------|
| 1 | 预初始化 + entry 日志 + except 安全引用 | `agent/server_routes/routes_chat.py` | `python -m py_compile` 通过；手测 BadRequest 触发 500 且日志含 `entry_assigned` |
| 2 | 新增 Prometheus Counter | `routes_chat.py` | 导出 `/metrics` 含 `yunshu_voice_entry_unassigned_total` |
| 3 | 追加告警规则 | `deploy/monitoring/prometheus/alert_rules.yml` | `promtool check rules` 通过 |
| 4 | （可选）仪表盘面板 | dashboard JSON | 面板可渲染、查询有数据 |

---

## 五、验证方案

### 5.1 单元/集成测试

在 `tests/integration/` 新增 `test_voice_listen_entry_monitor.py`：

| 用例 | 输入 | 预期 |
|------|------|------|
| 正常请求 | 合法 JSON | 日志 `action=voice_listen.entry`，`entry_assigned=true` |
| 异常请求 | 非法 JSON body（`request.get_json()` 抛错） | 日志 `action=voice_listen.error` 且 `entry_assigned` 字段存在，不抛 NameError |
| 指标递增 | 连发 5 次非法请求 | `yunshu_voice_entry_unassigned_total` 计数 ≥5 |

### 5.2 手工验证

```bash
# 启动服务后
curl -X POST http://localhost:5000/api/voice/listen \
  -H "Content-Type: application/json" \
  --data 'invalid-json{{{'           # 触发 BadRequest
curl http://localhost:5000/metrics | grep yunshu_voice_entry_unassigned_total
```

---

## 六、风险与影响

| 风险 | 影响 | 缓解 |
|------|------|------|
| 新增埋点耗时 | 单次 <1ms（既有规范） | 采用 Counter 直接累加，无 I/O |
| 字段命名与既有规范冲突 | 指标名须符合 `yunshu_<模块>_<动作>` | 已用 `yunshu_voice_entry_unassigned_total` |
| 改动破坏现有语音测试 | 回归风险 | 执行 `pytest tests/unit -k voice` 回归 |
| log_dict 自动补 trace_id 与手动 trace_id 并存 | 日志字段冗余 | entry 阶段显式传 `trace_id`，覆盖自动值 |

---

## 七、待决策项

1. `entry_assigned` 语义：预初始化即 `True`（本方案），还是「参数解析成功才 `True`」？
   - 前者：entry_assigned=false 永不出现（除非未来改为延迟赋值）
   - 后者：能精确区分「解析前异常」，建议采用
2. 告警阈值：`>3 次/10min` 是否合理？需结合生产基线校准。→ **校准建议见 [alert_threshold_calibration_plan.md](alert_threshold_calibration_plan.md)**
3. 是否需要同步到 `monitoring/prometheus/alert_rules.yml`（仓库根目录副本）？

---

## 附：链路图

```
请求 → @trace_route(创建TraceContext)
     → api_voice_listen()
         ├─ [entry] trace_id=T0, params={duration,...}
         ├─ [pre_check] trace_id_changed=false
         ├─ [pre_listen] trace_id_changed=false
         ├─ [post_listen] success=true/false
         └─ [error] trace_id_entry=T0, trace_id_changed=?, entry_assigned=?
                 └─ 若 entry_assigned=false → Counter+1 → 告警
```
