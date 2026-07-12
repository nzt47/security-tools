# 死代码修复与边界测试补充 Wiki

> 最后更新：2026-07-13
> 维护者：团队共享
> 关联提交：`812cf880`（死代码修复 + A 类边界测试）、`bd628a4a`（基础测试套件）、`fd7c45a0`（执行日志与未覆盖场景分析）

---

## 概述

本页面记录了 `config_manager.py` 中 `_update_mcp_config` 方法的死代码修复，以及针对 `snapshot.py` 和 `config_manager.py` 两个模块补充的 11 个边界测试用例。

| 修复项 | 类型 | 影响 | 修复提交 |
|--------|------|------|---------|
| `_update_mcp_config` else 分支不可达 | 死代码 | 新 MCP 服务无法正确记录 add 日志和设置时间戳 | `812cf880` |

**核心教训：** 当 `config["mcp"] = mcp_config` 在循环前执行时，查重源 `config["mcp"]["services"]` 与被遍历的 `mcp_config["services"]` 是同一引用，导致 `existing` 始终找到 service 自身，else 分支永不执行。修复时必须在覆盖前保存旧列表。

---

## 缺陷：`_update_mcp_config` else 分支不可达

### 根因

`_update_mcp_config` 方法在遍历 services 之前执行了 `config["mcp"] = mcp_config`，使 `config["mcp"]["services"]` 与 `mcp_config["services"]` 指向同一列表。遍历时 `next((s for s in config["mcp"]["services"] ...))` 会找到 service 自身（因为 service 就在列表中且 id 匹配），所以 `existing` 始终不为 None，else 分支永不执行。

```python
# ❌ 死代码
def _update_mcp_config(self, mcp_config: dict):
    config = self._load()
    config["mcp"] = mcp_config                    # ← 覆盖：config["mcp"]["services"] == mcp_config["services"]

    if 'services' in mcp_config:
        for service in mcp_config["services"]:
            if 'id' in service:
                existing = next(
                    (s for s in config["mcp"]["services"]   # ← 同一引用，始终找到 service 自身
                     if s["id"] == service["id"]),
                    None
                )
                if existing:
                    self._add_change_log('update', 'mcp_service', {...})
                else:
                    service["id"] = str(uuid.uuid4())        # ← 永不执行：死代码
                    service["created_at"] = ...              # ← 永不执行
                    service["updated_at"] = ...              # ← 永不执行
                    self._add_change_log('add', 'mcp_service', {...})  # ← 永不执行
```

### 修复

在 `config["mcp"] = mcp_config` **之前**保存旧 services 列表，查重时用旧列表而非覆盖后的列表。同时移除 else 分支中重新生成 id 的逻辑（service 已有 id，无需覆盖）。

```python
# ✅ 修复后
def _update_mcp_config(self, mcp_config: dict):
    config = self._load()
    old_services = config.get("mcp", {}).get("services", [])   # ← 保存旧列表
    config["mcp"] = mcp_config

    if 'services' in mcp_config:
        for service in mcp_config["services"]:
            if 'id' in service:
                existing = next(
                    (s for s in old_services                   # ← 用旧列表查重
                     if s.get("id") == service["id"]),
                    None
                )
                if existing:
                    self._add_change_log('update', 'mcp_service', {...})
                else:
                    # else 分支现在可达：有 id 但不在旧列表中 → 新增
                    service["created_at"] = datetime.datetime.now().isoformat()
                    service["updated_at"] = service["created_at"]
                    self._add_change_log('add', 'mcp_service', {...})
```

### 为什么不保留 `service["id"] = str(uuid.uuid4())`？

修复前 else 分支会重新生成 id，覆盖用户提供的 id。这不合理：
- 用户传入 `{"id": "my_custom_id", "name": "test"}` 时期望 id 被保留
- 重新生成 id 会导致前端无法用已知 id 查询该服务

修复后 else 分支仅设置 `created_at`/`updated_at` 时间戳和记录 add 日志，保留用户提供的 id。

### 修复影响

| 行为 | 修复前 | 修复后 |
|------|--------|--------|
| 有 id 且在旧列表中 | 记录 update 日志 | 记录 update 日志（不变） |
| 有 id 但不在旧列表中 | **不执行任何操作**（死代码） | 设置时间戳 + 记录 add 日志 |
| 无 id | 不处理 | 不处理（不变，由 `if 'id' in service` 守卫） |

---

## 边界测试补充

### 测试分类总览

基于 [未覆盖场景分析](../reports/uncovered-scenarios-analysis-20260713.md)，补充了 5 个 A 类场景共 11 个测试用例：

| 场景 | 测试数 | 模块 | 覆盖目标 |
|------|--------|------|----------|
| A1: 完整快照损坏外层 except | 2 | snapshot.py | L213-215 |
| A3: apply_search_instances ImportError | 2 | config_manager.py | L661-662 |
| A4: LLM 实例选择 instance_source | 4 | config_manager.py | L1102-1113 |
| A5: cleanup_snapshots keep_count=0 | 2 | snapshot.py | L1029-1030 |
| else 分支可达性验证 | 1 | config_manager.py | L519-522（已修复） |

### A1: 完整快照损坏外层 except

**目标行**: `snapshot.py` L213-215，`_load_snapshot_data` 外层 `except Exception`

**场景**: 完整快照文件存在但内容损坏（非有效 pickle 数据），`_load_from_path` 调用 `pickle.loads` 抛异常，外层 except 捕获后返回 None。

```python
class TestLoadSnapshotDataOuterException:
    def test_load_corrupted_full_snapshot_returns_none(self, manager):
        # 写入损坏的完整快照文件
        path = manager._get_snapshot_path("corrupt_full_snap", is_incremental=False)
        path.write_bytes(b"not_valid_pickle_data_at_all")
        result = manager._load_snapshot_data("corrupt_full_snap")
        assert result is None  # 外层 except 捕获后返回 None

    def test_load_corrupted_full_snapshot_latest_returns_none(self, manager, fake_digital_life):
        # 先保存正常快照，再写入损坏快照（文件名排序在后 = latest）
        manager.save_snapshot(fake_digital_life, snapshot_id="normal_before_corrupt", force=True)
        path = manager._get_snapshot_path("zzz_corrupt_latest", is_incremental=False)
        path.write_bytes(b"corrupted")
        result = manager._load_snapshot_data()  # 加载 latest
        assert result is None
```

### A3: apply_search_instances ImportError 降级

**目标行**: `config_manager.py` L661-662，`apply_search_instances` 末尾 `except ImportError`

**场景**: `agent.tools` 模块不可用时，`from agent.tools import sync_web_search_engines` 抛 `ImportError`，被静默捕获不影响搜索实例注册。

```python
class TestApplySearchInstancesImportError:
    def test_import_error_swallowed(self, manager):
        search_engine = MagicMock()
        search_engine._engine_registry = {}
        search_engine._api_keys = {}
        with patch.dict("sys.modules", {"agent.tools": None}):
            manager.apply_search_instances(search_engine)  # 不应抛异常

    def test_import_error_does_not_affect_registration(self, manager, secure_manager):
        # 即使 ImportError，搜索实例仍应被注册到 search_engine
        manager._update_search_instances([{"name": "pre_import_test", "engine_type": "custom", "enabled": True}])
        search_engine = MagicMock()
        search_engine._engine_registry = {}
        search_engine._api_keys = {}
        with patch.dict("sys.modules", {"agent.tools": None}):
            manager.apply_search_instances(search_engine)
        assert search_engine.register_engine.called  # 注册不受影响
```

### A4: LLM 实例选择 instance_source 验证

**目标行**: `config_manager.py` L1102-1113，`apply_to_app` 中 LLM 实例选择逻辑

**场景**: 验证三种实例选择来源 —— default（默认实例）、first_enabled（第一个启用实例）、legacy（旧版 llm 配置）。

```python
class TestApplyToAppInstanceSource:
    def test_default_instance_selected(self, manager, secure_manager):
        # 设置默认 LLM 实例 → instance_source = "default(...)"
        manager.add_llm_instance({"name": "default_src", "provider": "openai", "model": "gpt-4"})
        raw = manager.get_raw_config()
        inst_id = next(i["id"] for i in raw["llm_instances"] if i["name"] == "default_src")
        manager.set_default_llm_instance(inst_id)
        secure_manager._store[f"llm_{inst_id}_api_key"] = "sk-defaultkey123456"
        app = MagicMock()
        manager.apply_to_app(app)
        assert app.configure_llm.call_args.kwargs.get("provider") == "openai"

    def test_first_enabled_selected_when_no_default(self, manager, secure_manager):
        # 无默认实例 → instance_source = "first_enabled(...)"
        manager.add_llm_instance({"name": "first_enabled", "provider": "anthropic", "model": "claude-3"})
        manager.add_llm_instance({"name": "second_enabled", "provider": "google", "model": "gemini"})
        # ... 第一个启用实例应被选中
        assert app.configure_llm.call_args.kwargs.get("provider") == "anthropic"

    def test_legacy_used_when_no_instances(self, manager, secure_manager):
        # 无 llm_instances → 使用 legacy llm 配置
        manager.update({"llm": {"enabled": True, "provider": "openai", "model": "gpt-4", "api_key": "..."}})
        manager.apply_to_app(app)
        assert app.configure_llm.call_args.kwargs.get("provider") == "openai"

    def test_disabled_instance_not_selected(self, manager, secure_manager):
        # 禁用的实例不应被选中为 first_enabled
        manager.add_llm_instance({"name": "disabled_inst", "provider": "openai", "enabled": False})
        manager.add_llm_instance({"name": "enabled_inst", "provider": "anthropic", "enabled": True})
        manager.apply_to_app(app)
        assert app.configure_llm.call_args.kwargs.get("provider") == "anthropic"
```

### A5: cleanup_snapshots keep_count=0 边界

**目标行**: `snapshot.py` L1029-1030，`cleanup_snapshots` 保留逻辑

**场景**: `keep_count=0` 时删除全部快照的边界条件。

```python
class TestCleanupSnapshotsKeepZero:
    def test_cleanup_keep_zero_deletes_all(self, manager, fake_digital_life):
        for i in range(3):
            manager.save_snapshot(fake_digital_life, snapshot_id=f"keep0_{i}", force=True)
            time.sleep(0.01)
        deleted = manager.cleanup_snapshots(keep_count=0)
        assert deleted == 3
        assert len(manager.list_snapshots()) == 0

    def test_cleanup_keep_one_preserves_latest(self, manager, fake_digital_life):
        for i in range(3):
            manager.save_snapshot(fake_digital_life, snapshot_id=f"keep1_{i}", force=True)
            time.sleep(0.01)
        deleted = manager.cleanup_snapshots(keep_count=1)
        assert deleted == 2
        assert len(manager.list_snapshots()) == 1
```

### else 分支可达性验证

**目标行**: `config_manager.py` L519-522（死代码修复后）

**场景**: 修复后 else 分支可达，验证有 id 但不在旧列表中的 service 被正确处理。

```python
class TestUpdateMcpConfigEdgeCases:
    def test_update_mcp_new_service_with_id_sets_timestamp(self, manager):
        # 修复后 else 分支可达：有 id 但不在 old_services 中
        mcp_config = {
            "enabled": True,
            "services": [{"id": "mcp_new_1", "name": "new_service", "address": "host"}],
        }
        manager._update_mcp_config(mcp_config)
        config = manager._load()
        service = config["mcp"]["services"][0]
        assert service["id"] == "mcp_new_1"          # id 被保留
        assert "created_at" in service                # 时间戳被设置
        assert "updated_at" in service
        # 变更日志记录了 add 操作
        logs = config["change_log"]
        assert any(l["action"] == "add" and l["section"] == "mcp_service" for l in logs)
```

---

## 跳过的场景：A2

**场景**: `snapshot.py` L972-974，`load_snapshot` 恢复验证 except

**跳过原因**: 触发 `hasattr(Yunshu, "_body")` 抛异常需要精确控制 `__getattribute__` 在特定属性上抛非 `AttributeError` 异常，且需避免 `_restore_modules_by_priority` 中的 `hasattr` 提前抛异常。复杂度过高，业务价值低（恢复验证异常是极端边界情况）。

---

## 覆盖率提升

| 模块 | 修复前 | 修复后 | 变化 |
|------|--------|--------|------|
| `config_manager.py` | 94% | **95%** | +1%（死代码 L519-522 从 Missing 消失） |
| `snapshot.py` | 94% | **94%** | 持平（A1 覆盖 L213-215，整体基数不变） |
| **总计** | 94% | **95%** | +1% |

测试总数：224 → **235**（全部通过）

---

## 关键技术发现

### 1. 引用覆盖导致查重失效

当 `config["mcp"] = mcp_config` 在循环前执行时，`config["mcp"]["services"]` 与 `mcp_config["services"]` 指向同一列表对象。Python 的引用语义使得后续查重 `next((s for s in config["mcp"]["services"] ...))` 始终找到 service 自身。

**防范模式**: 覆盖配置前先保存旧值快照：
```python
old_services = config.get("mcp", {}).get("services", [])  # 快照
config["mcp"] = mcp_config  # 覆盖
# 用 old_services 查重，而非 config["mcp"]["services"]
```

### 2. Python 3 `hasattr` 只捕获 `AttributeError`

Python 3 中 `hasattr` 只捕获 `AttributeError`，其他异常会传播。这意味着 `MagicMock(side_effect=...)` 无法触发 `hasattr` 守卫的异常分支（因为属性访问不触发 `side_effect`，且 MagicMock 自动创建所有属性使 `hasattr` 返回 True）。

**测试策略**: 使用 `MagicMock(spec=[...])` 限制属性使 `hasattr` 返回 False，或用自定义辅助类（`_RaisingLen`/`_RaisingStr`/`_RaisingGetDict`）触发不受 `hasattr` 守卫的操作（如 `len()`/`__format__()`/`dict.get()`）。

### 3. `side_effect` 优先于 `return_value`

MagicMock 的 `side_effect` 优先于 `return_value`。在 fixture 中设置 `side_effect` 后，测试中设置 `return_value` 无效。

**测试策略**: 统一用 `_store` 字典填充存储，通过 `side_effect` 函数读取，避免 `return_value` 被忽略。

---

## 相关文档

- [测试执行日志](../reports/test-execution-log-20260713.md) — 235 个测试的详细执行日志
- [未覆盖场景分析](../reports/uncovered-scenarios-analysis-20260713.md) — A/B/C 三类未覆盖场景分析
- [测试报告](../reports/test-report-snapshot-config-manager-20260712.md) — 224 个基础测试的报告
- [并发缺陷修复 Wiki](concurrency_fixes_wiki.md) — 并发编程检查清单
