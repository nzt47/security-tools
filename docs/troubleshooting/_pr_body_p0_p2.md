## Summary

修复 observability-ci.yml 27 个回归测试用例 + yunshui-ui-tests.yml 依赖缺失问题，使 CI 流水线恢复绿色。

### P0 (A 类 - 意图分类回归, 15 用例)
- **文件**: `agent/response_workflows.py`
- **问题**: IntentRouter 返回 'unknown'，ResponseTemplates 返回 None，模板语义层整体失效
- **修复**: `_DEFAULT_RULES` 8 条规则已就位（time_query/identity/capability/weather/greeting/dissatisfaction/follow_up/simple_chat），按 priority 降序匹配
- **测试**: 29/29 通过

### P1 (B 类 - 对话状态/消息处理, 5 用例)
- **文件**: `agent/orchestrator/message_handler.py`
- **问题**: `is_follow_up` 对 None text 不抛 TypeError，追问降级 LLM 逻辑失效
- **修复**: 显式检查 `context['text'] is None` → 抛 TypeError，守调用方契约
- **测试**: 17/17 通过

### P1 (C 类 - TraceContext 接口契约破坏, 2 用例)
- **文件**: `agent/monitoring/tracing.py`
- **问题**: `TraceContext.__init__() takes 3 positional args but 4 given`
- **修复**: 新增 `kind` 可选第 3 参数（向后兼容）；新增 `add_event`/`set_attribute`（OpenTelemetry 兼容）；`inject_trace_context` 无上下文时自动生成 traceparent
- **测试**: 22/22 通过

### P2 (D 类 - 配置校验/幂等性, 5 用例)
- **文件**: `tests/test_error_handling.py`
- **问题**: `test_validate_valid_config` 缺少 `circuit_breaker` 必需配置节
- **修复**: fixture 补齐三级熔断器配置（session/user/global）
- **测试**: 33/33 通过

### yunshui-ui 依赖补齐 (TypeScript 类型检查失败)
- **文件**: `yunshu-ui/package.json` + `yunshu-ui/src/hooks/usePolling.ts` (新建)
- **问题**: `Cannot find module './usePolling'` + 缺少 @sentry/react/pako/rrweb 类型
- **修复**:
  - package.json 添加 @sentry/react ^8.42.0、pako ^2.1.0、rrweb ^2.0.0-alpha.18 及对应 @types
  - 新建 `usePolling.ts`：通用轮询 hook（AbortSignal + 卸载清理 + AbortError 静默 + fetcher ref 保鲜）
- **测试**: tsc 0 error + vitest 246/246 通过

## Test plan

- [x] `pytest tests/unit/test_response_workflows.py` — 29/29 ✓
- [x] `pytest tests/unit/test_message_handler.py` — 17/17 ✓
- [x] `pytest tests/trace_context_test.py tests/unit/test_monitoring_tracing.py` — 22/22 ✓
- [x] `pytest tests/test_error_handling.py` — 33/33 ✓
- [x] `pytest tests/unit/test_tlm_markdown_sync.py::TestIdempotency` — 1/1 ✓
- [x] `pytest tests/integration/test_orchestrator三层路由_e2e.py` — 11/11 ✓
- [x] `pytest tests/unit/test_orchestrator_*.py` — 130/130 ✓
- [x] `npx tsc -b --noEmit` (yunshu-ui) — 0 error
- [x] `npx eslint .` (yunshui-ui) — 0 error (85 warnings, 既有非本次新增)
- [x] `npx vitest run` (yunshui-ui) — 246/246 ✓
- [x] `docker compose config --quiet` — 0 (compose 配置验证通过)
- [x] pre-commit hook: 12/12 核心不变量 + BOM + 编码 + 链接 + 工作流模拟 全通过
- [ ] CI: observability-ci.yml 覆盖率 ≥60%
- [ ] CI: ci-cd.yml Docker Build and Test 7 步全绿
- [ ] CI: yunshui-ui-tests.yml lint + tsc + vitest + 生产构建 全绿
