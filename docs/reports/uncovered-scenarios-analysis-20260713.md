# 未覆盖极端场景分析：snapshot.py 与 config_manager.py

**分析日期**: 2026-07-13
**当前覆盖率**: snapshot.py 94%, config_manager.py 94%
**分析依据**: `coverage report --show-missing` 输出 + 源码审查

---

## 1. 分析方法

对 `coverage report --show-missing` 列出的所有未覆盖行号，逐行对照源码判断：
- 该分支的触发条件
- 是否可通过测试触发
- 补充测试的边际价值

按**可补充性**和**业务价值**分为 A/B/C 三类。

---

## 2. 分类总览

| 类别 | 含义 | 数量 | 建议 |
|------|------|------|------|
| A | 可补充的高价值场景 | 5 | **推荐补充** |
| B | 不可达/死代码 | 1 | 无需补充（建议修复源码） |
| C | 日志行/低价值 | ~12 | 不推荐（边际收益递减） |

---

## 3. A 类：可补充的高价值场景

### A1. snapshot.py L213-215 — `_load_snapshot_data` 外层异常

**源码位置**: `_load_snapshot_data` 方法

```python
except Exception as e:
    logger.error(f'[P6] 快照加载失败: {e}')
    return None
```

**触发条件**: `_load_from_path` 对完整快照文件抛异常（非增量路径）。

**当前覆盖**: 已有测试 `test_load_incremental_file_corrupted_falls_back_to_full` 覆盖了增量文件损坏回退完整快照，但完整快照文件本身损坏时走外层 except 未覆盖。

**补充方案**:
```python
def test_load_corrupted_full_snapshot_returns_none(self, manager):
    # 写入损坏的完整快照文件
    path = manager._get_snapshot_path("corrupt_full", is_incremental=False)
    path.write_bytes(b"not_valid_pickle_data")
    result = manager._load_snapshot_data("corrupt_full")
    assert result is None  # 外层 except 捕获后返回 None
```

**价值**: 中 — 验证快照加载的最终异常兜底。

---

### A2. snapshot.py L972-974 — `load_snapshot` 恢复验证异常

**源码位置**: `load_snapshot` 方法第 5 步恢复验证

```python
try:
    core_modules_exists = (
        hasattr(Yunshu, "_body") and
        hasattr(Yunshu, "_behavior") and
        hasattr(Yunshu, "_permission")
    )
    ...
except Exception as e:
    logger.warning(f'[P6] ├─ ⚠️ 恢复验证异常: {e}')
```

**触发条件**: `hasattr(Yunshu, "_body")` 抛非 AttributeError 异常。需用自定义元类使 `__getattr__` 抛 RuntimeError。

**补充方案**:
```python
def test_load_snapshot_verification_exception(self, manager, fake_digital_life):
    manager.save_snapshot(fake_digital_life, snapshot_id="verify_exc", force=True)
    
    class RaisingMeta(type):
        def __getattr__(cls, name):
            raise RuntimeError("meta fail")
    
    class RaisingLife(metaclass=RaisingMeta):
        pass
    
    # 恢复验证抛异常但流程继续
    result = manager.load_snapshot(digital_life_class=RaisingLife, snapshot_id="verify_exc")
    # 不应因验证异常而返回 None
```

**价值**: 中 — 验证恢复流程的异常容错。

---

### A3. config_manager.py L661-662 — `apply_search_instances` ImportError

**源码位置**: `apply_search_instances` 方法末尾

```python
try:
    from agent.tools import sync_web_search_engines
    sync_web_search_engines([], search_engine=search_engine)
except ImportError:
    pass  # 工具模块不可用时跳过
```

**触发条件**: `agent.tools` 模块不可用时 `import` 抛 `ImportError`。

**补充方案**:
```python
def test_apply_search_instances_import_error_swallowed(self, manager):
    search_engine = MagicMock()
    search_engine._engine_registry = {}
    search_engine._api_keys = {}
    with patch.dict(sys.modules, {"agent.tools": None}):
        # 不应抛异常
        manager.apply_search_instances(search_engine)
```

**价值**: 中 — 验证可选依赖缺失时的降级处理。

---

### A4. config_manager.py L1102-1113 — `apply_to_app` LLM 实例选择内部分支

**源码位置**: `apply_to_app` 方法 LLM 实例选择逻辑

```python
if selected:
    instance_source = f"default({selected.get('name')})"
# 没有默认实例，用第一个启用的
if not selected:
    selected = next((i for i in llm_instances if i.get('enabled', False)), None)
    if selected:
        instance_source = f"first_enabled({selected.get('name')})"

if selected:
    provider = selected.get('provider') or provider
    ...
```

**触发条件**: 已有测试覆盖了入口（`configure_llm` 被调用），但 `instance_source` 的不同来源（default vs first_enabled）未单独验证。

**补充方案**:
```python
def test_apply_to_app_instance_source_default(self, manager, secure_manager):
    # 设置默认实例，验证 instance_source = "default(...)"
    manager.add_llm_instance({"name": "default_src", "provider": "openai", "model": "gpt-4"})
    raw = manager.get_raw_config()
    inst_id = next(i["id"] for i in raw["llm_instances"] if i["name"] == "default_src")
    manager.set_default_llm_instance(inst_id)
    secure_manager._store[f"llm_{inst_id}_api_key"] = "sk-key123456789"
    
    app = MagicMock()
    app.configure_llm = MagicMock(return_value={"ok": True})
    manager.apply_to_app(app)
    app.configure_llm.assert_called_with(
        provider="openai", api_key="sk-key123456789",
        model="gpt-4", base_url=""
    )
```

**价值**: 中 — 验证 LLM 实例选择策略的正确性。

---

### A5. snapshot.py L1029-1030 — `cleanup_snapshots` 保留逻辑

**源码位置**: `cleanup_snapshots` 方法

```python
for snap_info in snapshots[keep_count:]:
    snap_path = self._get_snapshot_path(snap_info.snapshot_id, is_incremental)
    ...
```

**触发条件**: `keep_count` 参数控制保留数量，超出的快照被删除。当前测试 `test_cleanup_deletes_excess` 已覆盖基本删除，但 `keep_count=0`（删除全部）的边界未覆盖。

**补充方案**:
```python
def test_cleanup_keep_zero_deletes_all(self, manager, fake_digital_life):
    for i in range(3):
        manager.save_snapshot(fake_digital_life, snapshot_id=f"keep0_{i}", force=True)
        time.sleep(0.01)
    deleted = manager.cleanup_snapshots(keep_count=0)
    assert deleted == 3
    assert len(manager.list_snapshots()) == 0
```

**价值**: 低 — 边界条件验证。

---

## 4. B 类：不可达/死代码

### B1. config_manager.py L519-522 — `_update_mcp_config` else 分支（死代码）

**源码位置**: `_update_mcp_config` 方法

```python
for service in mcp_config["services"]:
    if 'id' in service:
        existing = next((s for s in config["mcp"]["services"] if s["id"] == service["id"]), None)
        if existing:                          # ← L517
            self._add_change_log('update', ...)
        else:                                 # ← L518
            service["id"] = str(uuid.uuid4()) # ← L519 死代码
            service["created_at"] = ...       # ← L520
            service["updated_at"] = ...       # ← L521
            self._add_change_log('add', ...)  # ← L522
```

**不可达原因**: 在循环前执行了 `config["mcp"] = mcp_config`，使 `config["mcp"]["services"]` 与 `mcp_config["services"]` 是同一引用。遍历时 `next(...)` 会找到 service 自身（因为 service 就在列表中且 id 匹配），所以 `existing` 始终不为 None，else 分支永不执行。

**建议**: 修复源码 — 在 `config["mcp"] = mcp_config` 之前先记录旧 services 列表用于查重：
```python
old_services = config.get("mcp", {}).get("services", [])
config["mcp"] = mcp_config
for service in mcp_config["services"]:
    if 'id' in service:
        existing = next((s for s in old_services if s["id"] == service["id"]), None)
        ...
```

**当前测试**: `test_update_mcp_service_without_id_no_auto_generate` 已验证此 bug 行为。

---

## 5. C 类：日志行/低价值（不推荐补充）

| 文件 | 行号 | 说明 |
|------|------|------|
| snapshot.py | 533-534 | behavior 增量未变化 info 日志（分支已覆盖） |
| snapshot.py | 561-562 | permission 增量未变化 info 日志（分支已覆盖） |
| snapshot.py | 290-291 | `_cleanup_old_snapshots` 删除失败 warning（已有测试覆盖 unlink 异常，可能 patch 路径未完全匹配） |
| snapshot.py | 744-745 | `_restore_behavior` 未知模式 warning（已覆盖） |
| config_manager.py | 1102, 1110 | `instance_source` 赋值后的 info 日志 |
| config_manager.py | 1120 | LLM 配置状态 info 日志 |
| config_manager.py | 1123 | 调用 configure_llm 前 info 日志 |
| config_manager.py | 1134 | `get_search_engines` api_keys 返回行（已覆盖入口） |

**不补充原因**: 这些是分支已覆盖后的日志输出行，补充测试仅为覆盖率数字提升，无业务验证价值。

---

## 6. 补充优先级建议

| 优先级 | 场景 | 预估测试数 | 覆盖率提升 |
|--------|------|-----------|-----------|
| P1 | A1: 完整快照损坏外层异常 | 1 | +0.3% |
| P1 | A3: apply_search_instances ImportError | 1 | +0.3% |
| P2 | A4: LLM 实例选择 instance_source | 2 | +0.5% |
| P2 | A2: load_snapshot 恢复验证异常 | 1 | +0.2% |
| P3 | A5: cleanup_snapshots keep_count=0 | 1 | +0.1% |
| — | B1: 修复死代码（源码修复） | 0 | +0.4% |

**补充全部 A 类后预估覆盖率**: snapshot.py ~95%, config_manager.py ~95%。

---

## 7. 结论

当前 94% 覆盖率已远超 40% CI 阈值，剩余未覆盖代码段中：
- **5 个 A 类场景**可补充（推荐 P1 和 P2，共 5 个测试，预估提升 ~1.3%）
- **1 个 B 类死代码**建议修复源码而非补测试
- **12 个 C 类日志行**不建议补充（边际收益递减）

**建议**: 优先补充 A1 和 A3（P1 级），其余按需补充。若需进一步提升覆盖率，建议先修复 B1 死代码。
