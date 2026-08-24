# SkillStore 持锁 I/O 重构方案

**创建日期**: 2026-08-24
**目标文件**: `agent/skills_mgmt/store.py`
**问题类型**: 违反项目硬约束"持锁操作严禁包含 I/O 或外部回调，锁内仅保护内存状态变更"

---

## 一、问题定位

### 1.1 现状：持锁 I/O 调用点

| 方法 | 行号 | 锁内执行的操作 | 严重度 |
|------|------|---------------|--------|
| `upsert()` | L107-112 | `_load()` + 修改 + `_persist()`（json.dump + os.replace + 重试 sleep） | 高 |
| `remove()` | L114-121 | `_load()` + 删除 + `_persist()` | 高 |
| `clear()` | L126-130 | `_cache = {}` + `_persist()` | 高 |
| `merge_skills()` | L163-284 | 合并逻辑 + `_persist()` + `sync_to_legacy_skills_json()`（读+写 skills.json）+ `_rebind_feedback()`（SQLite UPDATE） | 极高 |
| `_load()` 冷路径 | L41-63 | 文件不存在/损坏时 `_persist()`（L46/L63） | 低（仅首载） |

### 1.2 违反的硬约束

项目 `project_memory.md` 明确：
> 持锁操作严禁包含 I/O 或外部回调，锁内仅保护内存状态变更

**影响**：
- `_persist()` 内 `os.replace` 在 Windows 上可能触发 3 次重试 + `time.sleep(0.1)`，持锁时长被 I/O 放大到数百 ms
- `sync_to_legacy_skills_json()` 持锁读写 `skills.json`（文件 I/O）
- `_rebind_feedback()` 持锁执行 SQLite UPDATE + commit（数据库 I/O）
- 高并发写场景（批量技能更新 / evolution loop）锁内串行化放大

### 1.3 与 B1 锁采样报告的印证

B1 报告（`docs/zh/B1_锁优化建议报告_20260814.md`）实测 50 把锁无竞争热点，但 `optimized_storage.py:363` 持锁时长 29.82ms 是唯一显著值——同模式问题：**持锁 I/O 是持锁时长的首要放大源**。

---

## 二、重构目标与不变量

### 2.1 目标

【变易】把文件/DB I/O 移出锁外，锁内仅保护内存 `_cache` 变更。

### 2.2 不变量（【不易】，重构不得破坏）

1. **原子性语义不变**：`merge_skills` 的内存变更必须在锁内一次性完成（防并发交错），落盘可稍后
2. **异常降级语义不变**：`_persist` 失败重试 3 次 + 最终抛出；损坏文件先备份再重置
3. **API 签名不变**：`upsert/remove/clear/merge_skills/list_all/get/count/health` 全部签名与返回结构不变
4. **线程安全不变**：`_cache` 访问仍受 `_lock` 保护
5. **落盘时序**：写操作返回前**必须**确保数据已落盘（保持现有"写后即持久"的调用方假设）——这是与"异步批量落盘"方案的关键差异

---

## 三、重构方案（推荐：写后立即落盘，I/O 移出锁外）

> 方案权衡：
> - **方案 A（本方案）**：锁内只改内存 → 解锁后立即落盘。保持同步持久语义，改动最小，不引入新竞态。
> - 方案 B（异步批量）：落盘放后台线程/批量合并。需引入新状态机（dirty 标记 + flush 时机），改动大、风险高，且改变调用方"写后即持久"假设。
> 选 A 的理由：【简易】最小充分解，同步语义保留，无状态机复杂度。

### 3.1 新增私有方法：`_persist_unlocked()`

```python
def _persist(self) -> None:
    """原子写入 (临时文件 + os.replace) — 锁外调用，内部不获取锁"""
    self._path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", delete=False,
        dir=str(self._path.parent), suffix=".tmp",
    ) as tmp:
        json.dump(self._cache or {}, tmp, ensure_ascii=False, indent=2)
        tmp_path = tmp.name
    for attempt in range(3):
        try:
            os.replace(tmp_path, self._path)
            return
        except OSError:
            if attempt == 2:
                raise
            time.sleep(0.1)
```

**说明**：`_persist` 方法体本身不改（它不获取锁，原本就依赖调用方持锁），改动点在**调用位置**。

### 3.2 `upsert()` — 锁内改内存，锁外落盘

```python
def upsert(self, skill: Skill) -> None:
    """新增或更新 (按 id) — 锁内只改内存，锁外落盘"""
    with self._lock:
        data = self._load()
        data[skill.id] = skill.to_storage_dict()
    # I/O 移出锁外：解锁后才写盘
    self._persist()
```

**要点**：
- `_load()` 内部 `with self._lock` 是 RLock，可重入，锁内调用安全
- 落盘读 `self._cache`（已包含本次修改），无锁保护下的读——因只有当前线程刚解锁且无并发写者，读内存对象是安全的
- 若 `_persist` 抛异常：内存已变更但磁盘未落。**保持现状语义**（当前实现同样如此：锁内 `_persist` 失败也会抛异常且内存已改）。可选择在异常时回滚 `_cache`，但【简易】起见保留现状。

### 3.3 `remove()` — 同上模式

```python
def remove(self, skill_id: str) -> bool:
    with self._lock:
        data = self._load()
        if skill_id not in data:
            return False
        del data[skill_id]
    self._persist()
    return True
```

### 3.4 `clear()` — 同上模式

```python
def clear(self) -> None:
    """清空存储 (谨慎使用) — 锁内改内存，锁外落盘"""
    with self._lock:
        self._cache = {}
    self._persist()
```

### 3.5 `merge_skills()` — 最复杂，I/O 全部移出锁外

```python
def merge_skills(self, src_id, dst_id, *, strategy="auto", feedback_manager=None):
    """合并两个技能 — 保留 dst，删除 src

    重构要点：
    - 锁内：纯内存合并（_load + 合并 + 写回 _cache）
    - 锁外：_persist 落盘 → sync_to_legacy_skills_json → _rebind_feedback
    顺序保持：落盘 → legacy 同步 → feedback 改绑（与现状一致，失败各自降级）
    """
    with self._lock:
        data = self._load()

        # 边界显性化
        if src_id == dst_id:
            raise ValueError(f"src_id 与 dst_id 不能相同: {src_id}")
        if src_id not in data:
            raise ValueError(f"src 技能不存在: {src_id}")
        if dst_id not in data:
            raise ValueError(f"dst 技能不存在: {dst_id}")

        src_skill = Skill.from_storage_dict(data[src_id])
        dst_skill = Skill.from_storage_dict(data[dst_id])

        actual_dst, actual_src, swapped = self._resolve_merge_direction(
            src_skill, dst_skill, strategy
        )
        if swapped:
            actual_dst_id, actual_src_id = dst_id, src_id
            actual_dst = src_skill
            actual_src = dst_skill
        else:
            actual_dst_id, actual_src_id = dst_id, src_id

        merged_fields: List[str] = []
        # 1) tags / 2) dependencies / 3) default_params / 4) versions / 5) metrics
        #    （合并逻辑不变，全部在锁内对 Skill 对象操作）
        ...

        # 6) 落地（内存）
        actual_dst.touch()
        data[actual_dst_id] = actual_dst.to_storage_dict()
        del data[actual_src_id]
        self._cache = data
        # 锁内不落盘 —— 在 with 块外执行

    # ── 锁外 I/O 阶段 ──
    # 6b) 落盘（先持久化合并结果，保证崩溃后不丢失）
    self._persist()

    # 7) 同步 legacy skills.json（锁外，原在锁内）
    try:
        self.sync_to_legacy_skills_json()
    except Exception as e:
        logger.warning("[SkillStore] 合并后同步 legacy 失败: %s", e)

    logger.info(
        "[SkillStore] 已合并技能: src=%s → dst=%s, fields=%s",
        actual_src_id, actual_dst_id, merged_fields,
    )

    # 8) 改绑 feedback（锁外，原在锁内）
    feedback_rebound_count = 0
    if feedback_manager is not None:
        feedback_rebound_count = self._rebind_feedback(
            feedback_manager,
            src_skill_id=actual_src_id,
            dst_skill_id=actual_dst_id,
        )

    return {
        "merged_id": actual_dst_id,
        "removed_id": actual_src_id,
        "merged_fields": merged_fields,
        "version_added": (version_added.version if version_added else None),
        "feedback_rebound_count": feedback_rebound_count,
        "dependency_merge": dep_merge_info,
    }
```

**要点**：
- `version_added`/`dep_merge_info` 在锁内计算，锁外 return 引用（返回值仍是内存对象，安全）
- `_persist()` 失败（如磁盘满）：合并已完成但未持久化。**权衡说明**：现状在锁内失败同样会留下已变更内存 + 抛异常。方案保持等价，不额外引入回滚（【简易】）。

### 3.6 `_load()` 冷路径修正

`_load()` 内部 `_persist()`（L46/L63）发生在 `with self._lock` 内——但 `_load()` 是被 `upsert` 等外层锁调用，`_persist` 在 RLock 内仍是"持锁 I/O"。

**修正**：`_load()` 的冷路径（文件不存在/损坏时初始化）改为**锁外落盘**。重构 `_load` 返回 `(cache, needs_persist)` 太侵入；更简方案：`_load` 只负责读 + 设置 `_cache`，初始化落盘推迟到调用方统一 `_persist()`。

```python
def _load(self) -> Dict[str, dict]:
    """加载全部技能 (带缓存) — 只读内存，落盘由调用方统一处理"""
    with self._lock:
        if self._cache is not None:
            return self._cache
        if not self._path.exists():
            self._cache = {}
            logger.info("[SkillStore] 初始化存储: %s", self._path)
            return self._cache
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                self._cache = json.load(f)
            if not isinstance(self._cache, dict):
                raise ValueError("存储文件根节点必须是对象")
        except (json.JSONDecodeError, ValueError, OSError) as e:
            backup = self._path.with_suffix(".corrupted.json")
            try:
                self._path.rename(backup)
                logger.warning("[SkillStore] 存储损坏已备份到 %s: %s", backup, e)
            except OSError:
                pass
            self._cache = {}
        return self._cache
```

**配合改动**：`list_all()/get()/count()` 只读路径不落盘——首次调用时若文件不存在，`_cache={}` 已返回空结果，无需立即创建文件（懒初始化）。如需保持"首载即建文件"行为，可在这些只读方法锁外补一次 `self._persist()`；但【简易】判断：空 store 无文件不违反语义，删除该副作用。

---

## 四、测试策略

### 4.1 现有测试回归（必须通过）

- `tests/unit/test_skill_merge.py`：merge_skills 全部用例（tags/deps 合并、版本快照、方向选择、feedback 改绑）
- `tests/unit/test_skill_lifecycle.py`：upsert/remove/clear 生命周期
- `tests/unit/test_skills_mgmt_safety.py`：并发安全
- `tests/unit/test_review_enforcement.py` / `test_evolver_regression_gate.py` / `test_evolution_loop.py`：依赖 store 的间接用例

运行命令：
```bash
python -m pytest tests/unit/test_skill_merge.py tests/unit/test_skill_lifecycle.py -v
```

### 4.2 新增针对性测试

**新增**：`tests/unit/test_skill_store_lock_scope.py`

| 用例 | 验证点 |
|------|--------|
| `test_upsert_persist_outside_lock` | 用 mock 包装 `_persist`，断言 `_persist` 调用时 `_lock` 未持有（`_lock.acquire(blocking=False)` 成功） |
| `test_merge_legacy_sync_outside_lock` | mock `sync_to_legacy_skills_json` + `_rebind_feedback`，断言调用时锁未持有 |
| `test_upsert_persist_failure_keeps_cache` | mock `_persist` 抛异常，断言 `_cache` 已更新（内存先行），异常向上传播 |
| `test_merge_atomic_in_memory` | 并发线程同时 merge 不同技能对，断言内存 `_cache` 无交错损坏 |
| `test_cold_load_no_implicit_write` | 文件不存在时 `list_all()` 返回空且**不创建文件**（验证懒初始化副作用移除） |

**锁未持有断言技巧**：
```python
def assert_lock_free(store):
    acquired = store._lock.acquire(blocking=False)
    assert acquired, "调用时锁仍被持有（持锁 I/O 未消除）"
    store._lock.release()
```

---

## 五、改动文件清单

| 文件 | 改动 |
|------|------|
| `agent/skills_mgmt/store.py` | `upsert/remove/clear/merge_skills` 落盘移出锁外；`_load` 冷路径不再锁内落盘；`_persist` 方法体不变（文档注明锁外调用） |
| `tests/unit/test_skill_store_lock_scope.py` | 新增 5 个用例 |

**不新增依赖**，不改 API 签名，不改存储格式。

---

## 六、风险与回滚

| 风险 | 缓解 |
|------|------|
| `_persist` 锁外读 `_cache` 竞态（另一线程并发写） | `_cache` 引用在锁内已替换为新 dict；锁外 `_persist` 读取该引用时若有并发写，可能读到旧快照。**缓解**：落盘前用 `with self._lock: snapshot = dict(self._cache)` 深拷贝一份（O(N) 拷贝，N 为技能数通常 <100，成本可忽略）——在 `_persist` 开头加锁快照 |
| 异常时内存/磁盘不一致 | 与现状等价（现状锁内失败同样内存已改），不引入新风险 |
| 性能 | 锁临界区从"内存+磁盘"缩短为"纯内存"，写并发吞吐提升（I/O 时间不再串行化在锁内） |

**`_persist` 快照修正**（纳入方案）：
```python
def _persist(self) -> None:
    """原子写入 — 锁外调用。开头快照 _cache 防止并发写读半态"""
    with self._lock:
        snapshot = dict(self._cache or {})   # O(N) 浅拷贝，锁内仅内存操作
    self._path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(...) as tmp:
        json.dump(snapshot, tmp, ensure_ascii=False, indent=2)
        tmp_path = tmp.name
    ...
```

这样 `_persist` 内部仅一次短暂的内存锁（浅拷贝），真正文件 I/O 全部在锁外，严格符合"锁内仅保护内存状态变更"。
