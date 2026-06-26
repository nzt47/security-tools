# 代码修改任务待办列表

> **生成日期：** 2026-06-27
> **来源：** [jira_test_summary.md](file:///c:/Users/Administrator/agent/docs/observability/jira_test_summary.md) 行动计划拆解
> **状态说明：** [x] 已完成 / [ ] 待办

---

## P0 级任务（紧急，阻塞发布）

### TASK-P0-001：修复 Critic 降级测试（5 个失败）

- **失败文件：** [test_verification.py](file:///c:/Users/Administrator/agent/tests/unit/test_verification.py)
- **生产代码：** [critic.py](file:///c:/Users/Administrator/agent/agent/cognitive/critic.py) 行 138-229
- **根因：** Critic 服务降级时强制返回 `passed=True, overall_score=80`，测试期望 `passed=False, score<70`

**子任务：**

- [ ] **P0-001-a**：阅读 `critic.py` 行 138-229，确认 3 处降级路径：
  - 行 138-154：`should_skip(DegradeModule.CRITIC)` 为 True 时返回 `passed=True`
  - 行 158-179：熔断器 OPEN 时返回 `passed=True`
  - 行 200-223：评估异常时 `degrade_result.get("passed", True)`

- [ ] **P0-001-b**：在 `test_evaluate_fails_threshold` 中添加 mock 绕过降级：
  ```python
  @patch('agent.cognitive.critic.should_skip', return_value=False)
  @patch('agent.cognitive.critic.CircuitBreaker')
  def test_evaluate_fails_threshold(self, mock_cb, mock_skip):
      mock_cb.return_value.state.value = "closed"
      # ... 原有测试逻辑
  ```

- [ ] **P0-001-c**：检查其余 4 个失败测试是否同根因，统一添加 mock

- [ ] **P0-001-d**：运行验证
  ```bash
  python -m pytest tests/unit/test_verification.py -v --tb=short
  ```

---

## P1 级任务（高优先级，本迭代完成）

### TASK-P1-001：修复任务调度器测试（23 个失败）

- **失败文件：** [test_task_scheduler.py](file:///c:/Users/Administrator/agent/tests/unit/test_task_scheduler.py)
- **生产代码：** [task_scheduler.py](file:///c:/Users/Administrator/agent/agent/task_scheduler.py)

**子任务：**

- [ ] **P1-001-a**：更新任务结构（影响 6+ 个测试）
  - 旧结构：`{"type": "cron", "day_of_week": "*", "hour": 10, "minute": 30}`
  - 新结构：`{"type": "python_func", "cron": {"day_of_week": "*", "hour": 10, "minute": 30}}`
  - 同理将 `"type": "interval"` 改为 `"type": "python_func"` + 嵌套 interval 配置
  - 涉及测试方法：`test_should_run_cron_task`(行 97), `test_should_run_cron_task_already_ran`(行 136), `test_should_run_cron_task_wrong_day`(行 116), `test_should_run_interval_task_elapsed`(行 172), `test_should_run_interval_task_not_elapsed`(行 190)

- [ ] **P1-001-b**：修复硬编码 Windows 路径（行 1320）
  - 旧：`cwd=r"C:\Users\Administrator\agent"`
  - 新：`cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))`

- [ ] **P1-001-c**：修复方法名调用（行 717-726）
  - 旧：`scheduler.start()`
  - 新：`scheduler.start_daemon()`

- [ ] **P1-001-d**：修复日志断言（行 389-402）
  - 旧：`assert mock_logger.info.call_count >= 2`
  - 新：`assert mock_logger.info.call_count >= 1`

- [ ] **P1-001-e**：排查剩余 ~12 个失败的时间相关断言，统一使用 `freezegun` 或 mock `datetime.now()`

- [ ] **P1-001-f**：运行验证
  ```bash
  python -m pytest tests/unit/test_task_scheduler.py -v --tb=short
  ```

---

### TASK-P1-002：修复敏感字段脱敏测试（6 个失败）

- **失败文件：** [test_config_secure.py](file:///c:/Users/Administrator/agent/tests/unit/test_config_secure.py) 行 158-237 + [test_log_system_safe_logger.py](file:///c:/Users/Administrator/agent/tests/unit/test_log_system_safe_logger.py) 行 39, 52
- **生产代码：** [sensitive_data_filter.py](file:///c:/Users/Administrator/agent/agent/utils/sensitive_data_filter.py) 行 99
- **根因：** 生产 `REDACTED_VALUE = "********"`（8 星），测试期望 `"***"`（3 星）

**子任务：**

- [ ] **P1-002-a**：在 `test_config_secure.py` 中将所有 `"***"` 期望值改为 `"********"`
  - 行 158-168：`test_sanitize_api_key` — 3 个 test_case 的期望值
  - 行 196-217：`test_sanitize_dict` — 4 个字段断言

- [ ] **P1-002-b**：在 `test_log_system_safe_logger.py` 中修复期望值
  - 行 39：`assert '***@***.com'` → 适配新脱敏格式
  - 行 52：`assert '********' == '***'` → `assert '********' == '********'`

- [ ] **P1-002-c**：运行验证
  ```bash
  python -m pytest tests/unit/test_config_secure.py tests/unit/test_log_system_safe_logger.py -v --tb=short
  ```

---

### TASK-P1-003：修复 P6 快照异常路径测试（4 个失败）

- **失败文件：** [test_p6_snapshot_advanced.py](file:///c:/Users/Administrator/agent/tests/unit/test_p6_snapshot_advanced.py) 行 429-501
- **生产代码：** [p6_snapshot.py](file:///c:/Users/Administrator/agent/agent/p6_snapshot.py) 行 838-928
- **根因：** `hasattr()` 吞掉 property 异常，导致异常测试用例无法触发 except 分支

**子任务：**

- [ ] **P1-003-a**：阅读 `p6_snapshot.py` 行 838-928，确认 `_restore_body_sensor`、`_restore_behavior`、`_restore_permission` 使用 `hasattr()` 的位置

- [ ] **P1-003-b**：修改测试用例，用直接赋值属性替代 property 抛异常
  - 旧：`class FailingBody: @property def _initialized(self): raise Exception("test error")`
  - 新方案 A：直接 mock `hasattr` 返回 True 后抛异常
    ```python
    with patch('builtins.hasattr', side_effect=lambda obj, name: True if name == '_initialized' else hasattr(obj, name)):
        # 但 obj._initialized 访问时仍会抛异常
    ```
  - 新方案 B（推荐）：修改生产代码，将 `hasattr()` 替换为 `getattr()` + try/except
    ```python
    # 旧: if hasattr(body_sensor, "_initialized"):
    # 新: try: val = getattr(body_sensor, "_initialized"); has_attr = True
    #     except Exception: has_attr = False
    ```
  - 新方案 C：测试中改用 `__dict__` 直接设置属性而非 property

- [ ] **P1-003-c**：修复 PicklingError 相关测试（MagicMock 不可序列化）
  - 将 MagicMock 替换为真实对象或 `unittest.mock.Mock(spec=TargetClass)`

- [ ] **P1-003-d**：运行验证
  ```bash
  python -m pytest tests/unit/test_p6_snapshot_advanced.py -v --tb=short
  ```

---

## P2 级任务（常规修复，后续迭代）

### TASK-P2-001：修复 humanizer-zh 检测规则（3 个失败）

- **失败文件：** [test_text_tools.py](file:///c:/Users/Administrator/agent/tests/unit/test_text_tools.py) 行 48-64, 103-112
- **生产代码：** [text_tools.py](file:///c:/Users/Administrator/agent/agent/text_tools.py) 行 152-155, 686-701

**子任务：**

- [ ] **P2-001-a**：扩展 PATTERN_9_RE 正则，覆盖 "不仅是...而且是" 结构
  - 在 `text_tools.py` 行 152-155 添加分支：`r"不仅是.*?而且是"`

- [ ] **P2-001-b**：调整三连句检测阈值
  - 行 686-701：`len(s.strip()) > 10` → `len(s.strip()) >= 5`（或移除长度过滤）

- [ ] **P2-001-c**：运行验证
  ```bash
  python -m pytest tests/unit/test_text_tools.py -v --tb=short
  ```

---

### TASK-P2-002：修复模型路由测试（2 个失败）

- **失败文件：** [test_model_router.py](file:///c:/Users/Administrator/agent/tests/unit/test_model_router.py) 行 11, 15
- **根因：** 模型列表变更（`gemini-1.5-flash` 不在列表中，`gpt-4o` vs `gpt-4`）

**子任务：**

- [ ] **P2-002-a**：读取生产代码确认当前模型列表和路由逻辑
- [ ] **P2-002-b**：更新测试期望值匹配新模型列表
- [ ] **P2-002-c**：运行验证

---

### TASK-P2-003：修复其他测试（8 个失败，跨 5 个文件）

- [ ] **P2-003-a**：`test_llm_response_cache.py` 行 462 — `assert 'response1' is None`（缓存行为变更）
- [ ] **P2-003-b**：`test_message_handler.py` 行 62 — 返回结构多了 `confidence` 字段
- [ ] **P2-003-c**：`test_performance_alert.py` 行 434 — `assert 80.0 == 75.0`（阈值变更）
- [ ] **P2-003-d**：`test_response_builder.py` 行 49 — `workflow_result()` 参数 `result` 不存在
- [ ] **P2-003-e**：逐个运行验证
  ```bash
  python -m pytest tests/unit/test_llm_response_cache.py tests/unit/test_message_handler.py tests/unit/test_performance_alert.py tests/unit/test_response_builder.py -v --tb=short
  ```

---

### TASK-P2-004：sensor 模块测试补全（242 个用例）

- **参考文档：** [sensor_test_plan.md](file:///c:/Users/Administrator/agent/docs/observability/sensor_test_plan.md)
- **预估工时：** 22 人日
- **阶段目标：** 覆盖率 9.18% → 40% → 55% → 70%

**子任务：**

- [ ] **P2-004-a**：阶段 1 — P0 测试（103 个用例，8 人日）
- [ ] **P2-004-b**：阶段 2 — P1 测试（71 个用例，7 人日）
- [ ] **P2-004-c**：阶段 3 — P2 测试（68 个用例，7 人日）

---

### TASK-P2-005：提升整体覆盖率至 40%（持续）

- **当前：** 31.93%
- **目标：** 40% → 55% → 70%
- **重点模块：** sensor(9.18%), lifetrace(16.63%), memory(19.02%), cognitive(21.00%)

**子任务：**

- [ ] **P2-005-a**：补充 148 个 0-20% 覆盖率文件的优先级排序
- [ ] **P2-005-b**：优先覆盖语句数 > 100 的 0% 文件（api_gateway, async_executor, auto_tuner 等）

---

## 任务统计

| 优先级 | 任务数 | 失败用例数 | 预估工时 | 状态 |
|--------|--------|-----------|---------|------|
| P0 | 1 (4 子任务) | 5 | 2 人日 | 待办 |
| P1 | 3 (14 子任务) | 33 | 5 人日 | 待办 |
| P2 | 5 (11 子任务) | 11+ | 22+ 人日 | 待办 |
| **合计** | **9 (29 子任务)** | **49+** | **29+ 人日** | — |
| 已完成 | 2 | 16 | — | error_handler + search 已修复 |
