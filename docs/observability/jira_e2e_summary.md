# [E2E] GlitchTip 错误上报链路验证 — 测试通过

| 字段 | 值 |
|------|-----|
| **测试类型** | 端到端 (E2E) |
| **状态** | ✅ PASS |
| **执行日期** | 2026-06-27 |
| **执行环境** | Windows + Docker Desktop + GlitchTip 6.2.0 |
| **关联模块** | `agent/error_reporting_config.py`, `agent/monitoring/replay_storage.py` |
| **报告路径** | `docs/observability/e2e_test_report.md` |

---

## 测试结果摘要

| 维度 | 结果 | 详情 |
|------|------|------|
| Mock 单元测试 | ✅ 77/77 PASS | 通过率 100%, 耗时 1.80s |
| Docker 容器 | ✅ 4/4 Running | postgres + redis + web + worker 全部 Healthy |
| Sentry SDK 初始化 | ✅ PASS | 6015ms 完成 |
| 错误事件上报 | ✅ PASS | event_id=`7260c45f0abb4a56a2b245731c1117a8` |
| 消息事件上报 | ✅ PASS | event_id=`15e16b865f8b487390b561e3c5b2fbd9` |
| 事件落库 | ✅ PASS | 2 Issues + 2 IssueEvents 已持久化 |
| trace_id 注入 | ✅ PASS | `contexts.custom.trace_id = verify-1782495548` |
| 敏感字段过滤 | ✅ PASS | `password=[REDACTED]`, `api_key=[REDACTED]` |

---

## 关键数据

### Mock 测试

| 指标 | 数值 |
|------|------|
| 总用例数 | 77 |
| 通过 | 77 |
| 失败 | 0 |
| 跳过 | 0 |
| 通过率 | **100%** |
| 执行耗时 | 1.80s |
| 覆盖率 | 82.85% (阈值 ≥80%) |

### E2E 验证数据

| 验证项 | 结果 |
|--------|------|
| trace_id 在事件 `contexts.custom.trace_id` | `verify-1782495548` ✅ |
| `password` 字段过滤 | `[REDACTED]` ✅ |
| `api_key` 字段过滤 | `[REDACTED]` ✅ |
| 原始敏感数据泄露检查 | 未发现 ✅ |
| GlitchTip Issues 落库 | 2 条 ✅ |
| GlitchTip IssueEvents 落库 | 2 条 ✅ |

### Docker 环境

| 容器 | 状态 | 端口 |
|------|------|------|
| glitchtip-postgres | Up (healthy) | 5433→5432 |
| glitchtip-redis | Up (healthy) | 6380→6379 |
| glitchtip-web | Up (running) | 8000→8000 |
| glitchtip-worker | Up (running) | — |

---

## GlitchTip 访问信息

| 项 | 值 |
|----|-----|
| Web UI | http://localhost:8000 |
| 登录账号 | `admin@local.test` |
| 登录密码 | `Admin@2026!` |
| 项目路径 | Yunshu → Yunshu Backend |
| DSN | `http://3dec0743-423f-4b28-a6af-919a116ccc41@localhost:8000/1` |

---

## 已知问题

| # | 问题 | 严重程度 | 状态 |
|---|------|---------|------|
| 1 | GlitchTip REST API 路径与通用 Sentry API 不兼容 | 中 | 已通过 Django ORM 规避 |
| 2 | `ALLOWED_HOSTS` 使用通配符 `*` | 低 | 仅开发环境，生产需限制 |
| 3 | IssueTag 表中无独立 trace_id 标签 | 低 | trace_id 存在于 event data 中 |
| 4 | 覆盖率 82.85%，未达 90% 目标 | 中 | 待补全（见测试补全计划） |

---

## 验证脚本

```bash
# 运行 Mock 测试
python -m pytest tests/unit/test_new_modules_mock.py tests/unit/test_error_reporting_config.py -v

# 运行 E2E 验证
set SENTRY_DSN=http://3dec0743-423f-4b28-a6af-919a116ccc41@localhost:8000/1
python docker/glitchtip/verify_error_reporting.py
```

---

## 相关文件

- 完整报告: `docs/observability/e2e_test_report.md`
- JUnit XML: `tests/e2e_mock_results.xml`
- 测试补全计划: `docs/observability/test_completion_plan.md`
- GlitchTip 部署指南: `docs/observability/glitchtip_deployment.md`
