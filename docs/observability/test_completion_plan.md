# 测试补全计划：覆盖率提升至 90%+

> **报告日期：** 2026-06-27
> **目标覆盖率：** ≥ 90%（核心模块）
> **当前覆盖率：** 82.85%（新增模块平均）
> **数据来源：** `coverage_report/coverage.json` (coverage.py 7.14.1)

---

## 一、覆盖率现状

| 模块 | 当前覆盖率 | 目标 | 差距 | 优先级 |
|------|-----------|------|------|--------|
| `agent/error_reporting_config.py` | 80.30% | 90% | -9.70% | P0 |
| `agent/monitoring/replay_storage.py` | 84.55% | 90% | -5.45% | P1 |
| **新增模块平均** | 82.85% | 90% | -7.15% | — |

---

## 二、未覆盖分支清单

### 2.1 `error_reporting_config.py` (26 行未覆盖)

| # | 行号 | 功能描述 | 风险等级 | 补全优先级 | 预期覆盖率提升 |
|---|------|---------|---------|-----------|--------------|
| 1 | 54 | `_DEFAULT_SENSITIVE_PATTERNS` 常量定义（含 phone/mobile） | 低 | P3 | +0.7% |
| 2 | 151-152 | `webhook.timeout` 环境变量解析（int 转换） | 中 | P2 | +1.4% |
| 3 | 181 | `_parse_sample_rate` 注释行（无逻辑） | 低 | P3 | +0.7% |
| 4 | 218-220 | `_DSN_PATTERN` 正则编译 + `init_sentry` 函数签名 | 低 | P3 | +2.0% |
| 5 | 232 | `init_sentry` 中 `_sentry_init_lock` 延迟创建分支 | 中 | P2 | +0.7% |
| 6 | 330 | `set_sensitive_patterns` 函数日志行 | 低 | P3 | +0.7% |
| 7 | 340 | `_is_sensitive_key` 归一化匹配的空模式跳过 | 中 | P2 | +0.7% |
| 8 | 378 | `_filter_sensitive_recursive` 中 `list` 分支 | 中 | P1 | +0.7% |
| 9 | 382 | `_filter_sensitive_recursive` 中 `tuple` 分支 | 中 | P1 | +0.7% |
| 10 | 384-385 | `_filter_sensitive_recursive` 中 `str` 内嵌 token 替换 | **高** | P0 | +1.4% |
| 11 | 403-413 | `_sentry_before_send` 主逻辑（脱敏 + trace_id 注入 + breadcrumb） | **高** | P0 | +7.5% |

**补全后预期覆盖率：** 80.30% → **96.6%**（+16.3%）

---

### 2.2 `replay_storage.py` (33 行未覆盖)

| # | 行号 | 功能描述 | 风险等级 | 补全优先级 | 预期覆盖率提升 |
|---|------|---------|---------|-----------|--------------|
| 1 | 139-140 | `__init__` 中 `storage_root` 规范化 + 目录创建 | 低 | P3 | +1.0% |
| 2 | 229-235 | `store()` 中 `replay_id` 格式校验失败分支 | 中 | P2 | +1.5% |
| 3 | 257-258 | `store()` 中 `timestamp` 格式校验失败分支 | 中 | P2 | +1.0% |
| 4 | 285-286 | `store()` 中 gzip 编码 `data.encode("utf-8")` 分支 | 中 | P1 | +1.0% |
| 5 | 373-377 | `get_by_id` 中 `not_found` 返回 None + 日志分支 | **高** | P0 | +2.5% |
| 6 | 431-433 | `get_data_by_id` 中 gzip 解压失败异常分支 | **高** | P0 | +1.5% |
| 7 | 457-459 | `list_by_trace_id` / `list_by_user_session` 函数签名 | 低 | P3 | +1.5% |
| 8 | 582-590 | `stats()` 中 `fully_correlated` + `by_error_id` SQL 查询 | **高** | P0 | +2.5% |
| 9 | 615 | `stats()` 中 `sqlite3.Error` 异常处理 | **高** | P0 | +0.5% |

**补全后预期覆盖率：** 84.55% → **96.6%**（+12.05%）

---

## 三、补全任务清单

### P0 — 高风险核心分支（必须补全）

| 任务 ID | 模块 | 行号 | 功能 | 测试场景 | 预期提升 |
|---------|------|------|------|---------|---------|
| P0-1 | error_reporting_config | 403-413 | `_sentry_before_send` 脱敏 + trace_id 注入 | 构造含敏感字段的 event dict，验证脱敏后 password=[REDACTED]，tags 含 trace_id，breadcrumbs 含 debug 条目 | +7.5% |
| P0-2 | error_reporting_config | 384-385 | `_filter_sensitive_recursive` 字符串内嵌 token 替换 | 输入 `"token=abc123"` / `"api_key: sk-xxx"` / `"Bearer xyz"`，验证替换为 `[REDACTED]` | +1.4% |
| P0-3 | replay_storage | 373-377 | `get_by_id` 查询不存在记录返回 None | 存储后查询不存在的 replay_id，验证返回 None + 日志 result=not_found | +2.5% |
| P0-4 | replay_storage | 431-433 | `get_data_by_id` gzip 解压失败 | 写入损坏 gzip 数据后读取，验证抛出 ReplayStorageError(REPLAY_ERR_DECODE_FAILED) | +1.5% |
| P0-5 | replay_storage | 582-590 | `stats()` 三向关联统计 SQL | 插入含 trace_id/user_session_id/error_id 的完整记录 + 部分缺失记录，验证 fully_correlated 计数正确 | +2.5% |
| P0-6 | replay_storage | 615 | `stats()` 数据库异常处理 | mock sqlite3.Error，验证抛出 ReplayStorageError(REPLAY_ERR_DB_FAILED) | +0.5% |

**P0 合计预期提升：** +15.4%（error_reporting +8.9%，replay_storage +6.5%）

---

### P1 — 中风险重要分支（建议补全）

| 任务 ID | 模块 | 行号 | 功能 | 测试场景 | 预期提升 |
|---------|------|------|------|---------|---------|
| P1-1 | error_reporting_config | 378 | `_filter_sensitive_recursive` list 分支 | 输入 `[{"password": "x"}, {"token": "y"}]`，验证每个元素脱敏 | +0.7% |
| P1-2 | error_reporting_config | 382 | `_filter_sensitive_recursive` tuple 分支 | 输入 `({"password": "x"}, {"token": "y"})`，验证 tuple 递归脱敏 | +0.7% |
| P1-3 | replay_storage | 285-286 | `store()` gzip 编码 str→bytes 转换 | 传入 str 类型 data + compressed=True + encoding="gzip"，验证文件正确写入 | +1.0% |

**P1 合计预期提升：** +2.4%

---

### P2 — 中风险次要分支（可选补全）

| 任务 ID | 模块 | 行号 | 功能 | 测试场景 | 预期提升 |
|---------|------|------|------|---------|---------|
| P2-1 | error_reporting_config | 151-152 | `webhook.timeout` int 解析 | 设置 `ERROR_REPORTING_WEBHOOK_TIMEOUT=10`，验证 config.webhook.timeout=10 | +1.4% |
| P2-2 | error_reporting_config | 232 | `init_sentry` 锁延迟创建 | 首次调用 init_sentry 前置 `_sentry_init_lock=None`，验证锁被创建 | +0.7% |
| P2-3 | error_reporting_config | 340 | `_is_sensitive_key` 空模式跳过 | 调用 `set_sensitive_patterns(["", "password"])`，验证空模式被跳过 | +0.7% |
| P2-4 | replay_storage | 229-235 | `store()` replay_id 格式校验 | 传入 `"invalid id"` / `""` / None，验证抛出 ReplayStorageError | +1.5% |
| P2-5 | replay_storage | 257-258 | `store()` timestamp 格式校验 | 传入 `"not-a-date"`，验证抛出 ReplayStorageError | +1.0% |

**P2 合计预期提升：** +5.3%

---

### P3 — 低风险边角分支（暂缓）

| 任务 ID | 模块 | 行号 | 功能 | 说明 |
|---------|------|------|------|------|
| P3-1 | error_reporting_config | 54 | 常量定义行 | 仅定义 `_DEFAULT_SENSITIVE_PATTERNS`，无逻辑分支 |
| P3-2 | error_reporting_config | 181 | 注释行 | 无可执行代码 |
| P3-3 | error_reporting_config | 218-220 | 正则编译 + 函数签名 | 模块级代码，import 时已执行 |
| P3-4 | error_reporting_config | 330 | 日志输出行 | `set_sensitive_patterns` 的日志副作用 |
| P3-5 | replay_storage | 139-140 | 初始化目录创建 | `__init__` 中 makedirs，已被现有测试间接覆盖 |
| P3-6 | replay_storage | 457-459 | 函数签名 | `list_by_trace_id` 等函数定义行 |

**P3 合计：** 不影响功能正确性，暂不补全

---

## 四、分阶段实施计划

### 阶段一：P0 高风险补全（目标：90%+）

**目标覆盖率：** error_reporting 89.2%，replay_storage 93.0%，平均 **91.1%**

| 任务 | 预计用例数 | 关键验证点 |
|------|-----------|-----------|
| P0-1: `_sentry_before_send` | 5 | 脱敏、trace_id 注入、breadcrumb 追加、异常处理 |
| P0-2: 字符串内嵌 token | 4 | `token=xxx`、`api_key: xxx`、`Bearer xxx`、无匹配 |
| P0-3: `get_by_id` not_found | 2 | 不存在 ID、空表查询 |
| P0-4: gzip 解压失败 | 3 | 损坏数据、截断数据、非 gzip 数据 |
| P0-5: `stats()` 三向关联 | 4 | 完整记录、部分缺失、空表、by_error_id 分组 |
| P0-6: `stats()` DB 异常 | 2 | sqlite3.Error mock、连接关闭 |

**新增用例：** 约 20 个
**预期耗时：** P0 全部完成后覆盖率达标

---

### 阶段二：P1 中风险补全（目标：93%+）

**目标覆盖率：** error_reporting 90.6%，replay_storage 94.0%，平均 **92.3%**

| 任务 | 预计用例数 | 关键验证点 |
|------|-----------|-----------|
| P1-1: list 递归脱敏 | 2 | 嵌套 list 含敏感 dict |
| P1-2: tuple 递归脱敏 | 2 | 嵌套 tuple 含敏感 dict |
| P1-3: gzip str 编码 | 2 | str data + gzip 编码路径 |

**新增用例：** 约 6 个

---

### 阶段三：P2 次要分支补全（目标：95%+）

**目标覆盖率：** error_reporting 93.4%，replay_storage 96.5%，平均 **95.0%**

| 任务 | 预计用例数 | 关键验证点 |
|------|-----------|-----------|
| P2-1: webhook timeout | 2 | 合法 int、非法值降级 |
| P2-2: 锁延迟创建 | 1 | 重置 `_sentry_init_lock=None` 后调用 |
| P2-3: 空模式跳过 | 2 | 空字符串模式、仅空白 |
| P2-4: replay_id 校验 | 3 | 非法格式、空、None |
| P2-5: timestamp 校验 | 2 | 非日期字符串、None |

**新增用例：** 约 10 个

---

## 五、测试用例设计要点

### 5.1 P0-1: `_sentry_before_send` 测试设计

```python
class TestSentryBeforeSend:
    """覆盖 _sentry_before_send 钩子（行 403-413）"""

    def test_filters_sensitive_fields_in_event(self):
        """验证 event dict 中敏感字段被脱敏"""
        event = {
            "extra": {"password": "secret123", "api_key": "sk-xxx"},
            "request": {"headers": {"authorization": "Bearer abc"}},
            "tags": {},
            "breadcrumbs": {"values": []},
        }
        result = _sentry_before_send(event, {})
        assert result["extra"]["password"] == "[REDACTED]"
        assert result["extra"]["api_key"] == "[REDACTED]"
        assert result["request"]["headers"]["authorization"] == "[REDACTED]"

    def test_injects_trace_id_to_tags(self):
        """验证 trace_id 注入到 tags"""
        # mock _safe_get_trace_id 返回固定值
        with patch("agent.error_reporting_config._safe_get_trace_id", return_value="trace-abc"):
            result = _sentry_before_send({"tags": {}}, {})
        assert result["tags"]["trace_id"] == "trace-abc"

    def test_appends_breadcrumb(self):
        """验证 breadcrumb 被追加"""
        event = {"breadcrumbs": {"values": []}}
        result = _sentry_before_send(event, {})
        assert len(result["breadcrumbs"]["values"]) == 1
        assert result["breadcrumbs"]["values"][0]["category"] == "yunshu.before_send"

    def test_handles_non_dict_event(self):
        """验证非 dict event 原样返回"""
        result = _sentry_before_send("not-a-dict", {})
        assert result == "not-a-dict"

    def test_handles_missing_breadcrumbs(self):
        """验证无 breadcrumbs 字段时自动创建"""
        result = _sentry_before_send({}, {})
        assert "breadcrumbs" in result
        assert "values" in result["breadcrumbs"]
```

### 5.2 P0-2: 字符串内嵌 token 替换测试设计

```python
class TestSensitiveTokenPatterns:
    """覆盖 _filter_sensitive_recursive 中 str 分支（行 382-390）"""

    def test_replaces_token_equals_pattern(self):
        """token=xxx → token=[REDACTED]"""
        result = _filter_sensitive_recursive("token=abc123")
        assert "[REDACTED]" in result
        assert "abc123" not in result

    def test_replaces_api_key_colon_pattern(self):
        """api_key: xxx → api_key: [REDACTED]"""
        result = _filter_sensitive_recursive("api_key: sk-secret")
        assert "[REDACTED]" in result
        assert "sk-secret" not in result

    def test_replaces_bearer_token(self):
        """Bearer xxx → Bearer: [REDACTED]"""
        result = _filter_sensitive_recursive("Bearer abc.def.ghi")
        assert "[REDACTED]" in result

    def test_no_match_returns_original(self):
        """无匹配时原样返回"""
        result = _filter_sensitive_recursive("hello world")
        assert result == "hello world"
```

### 5.3 P0-3/P0-4: replay_storage 查询与异常测试设计

```python
class TestReplayStorageQueryNotFound:
    """覆盖 get_by_id 返回 None 分支（行 373-377）"""

    def test_get_by_id_returns_none_for_missing(self, storage):
        result = storage.get_by_id("nonexistent-id")
        assert result is None

    def test_get_by_id_returns_none_on_empty_storage(self, storage):
        result = storage.get_by_id("any-id")
        assert result is None


class TestReplayStorageGzipDecodeFailure:
    """覆盖 get_data_by_id gzip 解压失败（行 431-433）"""

    def test_raises_on_corrupt_gzip(self, storage, tmp_path):
        replay_id = "test-corrupt-gzip"
        file_path = storage._file_path_for(replay_id, "2026-06-27T00:00:00", True)
        # 写入损坏的 gzip 数据
        with open(file_path, "wb") as f:
            f.write(b"not-a-gzip-file")
        # 先在 DB 插入元数据
        storage._conn.execute(
            "INSERT INTO replay (...) VALUES (...)",
            ...
        )
        with pytest.raises(ReplayStorageError) as exc_info:
            storage.get_data_by_id(replay_id)
        assert exc_info.value.code == REPLAY_ERR_DECODE_FAILED

    def test_raises_on_truncated_gzip(self, storage):
        """截断的 gzip 数据"""
        ...


class TestReplayStorageStatsCorrelation:
    """覆盖 stats() 三向关联统计（行 582-590）"""

    def test_fully_correlated_count(self, storage):
        """完整三向关联记录计数"""
        storage.store(replay_id="r1", trace_id="t1",
                      user_session_id="s1", error_id="e1", data="{}")
        stats = storage.stats(hours=24)
        assert stats["fully_correlated"] == 1

    def test_partial_correlation_excluded(self, storage):
        """缺少 error_id 的记录不计入 fully_correlated"""
        storage.store(replay_id="r2", trace_id="t2",
                      user_session_id="s2", error_id=None, data="{}")
        stats = storage.stats(hours=24)
        assert stats["fully_correlated"] == 0
        assert stats["with_trace_id"] == 1
        assert stats["with_error_id"] == 0

    def test_by_error_id_grouping(self, storage):
        """by_error_id 分组统计"""
        ...

    def test_db_error_raises(self, storage):
        """sqlite3.Error 触发异常（行 615）"""
        with patch.object(storage._conn, "execute", side_effect=sqlite3.Error("mock")):
            with pytest.raises(ReplayStorageError) as exc_info:
                storage.stats(hours=24)
            assert exc_info.value.code == REPLAY_ERR_DB_FAILED
```

---

## 六、验收标准

| 阶段 | 目标覆盖率 | 验收用例数 | 验收命令 |
|------|-----------|-----------|---------|
| 阶段一 (P0) | ≥ 90% | +20 | `pytest tests/unit/ -v --cov=agent.error_reporting_config --cov=agent.monitoring.replay_storage --cov-report=term` |
| 阶段二 (P0+P1) | ≥ 93% | +26 | 同上 |
| 阶段三 (P0+P1+P2) | ≥ 95% | +36 | 同上 |

---

## 七、风险与依赖

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| `_sentry_before_send` 依赖 `sentry_sdk` 运行时 | 测试需 mock | 使用 `unittest.mock.patch` mock `_safe_get_trace_id` |
| `get_data_by_id` gzip 解压需构造损坏文件 | 测试复杂度高 | 使用 `tmp_path` fixture + 直接写入字节数据 |
| `stats()` SQL 查询依赖完整 schema | 测试数据准备量大 | 复用现有 `storage` fixture，补充多场景数据 |
| 覆盖率工具版本差异 | 数据不一致 | 统一使用 coverage.py 7.14.1 |

---

## 八、相关文件

| 文件 | 用途 |
|------|------|
| `agent/error_reporting_config.py` | 待补全测试的源文件 (608行) |
| `agent/monitoring/replay_storage.py` | 待补全测试的源文件 (560行) |
| `tests/unit/test_new_modules_mock.py` | 现有测试文件 (758行, 71用例) |
| `tests/unit/test_error_reporting_config.py` | 现有测试文件 (6用例) |
| `docs/observability/new_module_coverage_report.md` | 覆盖率详情报告 |
| `docs/observability/e2e_test_report.md` | E2E 测试报告 |
