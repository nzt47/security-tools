# 一次 CI 并发修复教会我的两件事：import 副作用与隐式目录依赖

> 草稿：技术博客（基于 2026-08-10 真实 CI 治理实战）
> 素材来源：Shard 4 串行段 10 failed 追查、Shard 1 FileNotFoundError 追查

---

## 引言：测试在 CI 上失败，但本地永远复现不了

```
Shard 4：串行段 10 failed，serial 标记后依然失败
Shard 1：1 failed — FileNotFoundError: /home/runner/.../logs/audit.log
```

两起故障的共同点：**本地无法复现，CI 偶发失败**。这类问题最消耗排查时间，也最容易误判为"并发 flake"而误伤正确代码。本文记录两起真实根因——模块级 `logging.disable` 的 import 副作用、`FileHandler` 不创建父目录——以及各自的最小修复。

---

## 事件一：加了 serial 标记，Shard 4 还是失败

### 现象

项目用 pytest-xdist 并行 + 6-shard 分片跑全量测试。`test_knowledge_observability.py` 的 4 个日志捕获测试（`assertLogs`/`caplog`）被标记 `@pytest.mark.serial` 后走单进程串行段，理论上规避了并发日志状态竞争——**但串行段 10 个测试全部失败**。

第一次误判：以为是 serial 机制失效，甚至一度记录"serial 根治证伪"。

### 追查：从结果反推污染源

排查思路：`assertLogs` 失败通常意味着 `logging` 全局状态被污染。污染必须发生在**同一进程**，而串行段与并行段共用同一 pytest 进程（`--cov-append` 追加）。候选污染源在 **collection 阶段**——测试文件被 import 即执行模块顶层代码。

- `patch logging.disable` 打印调用栈 → 无输出（怀疑被绕过）
- 改用 pytest hook `pytest_collectstart` 逐模块监控 `manager.disable` → 一次命中：

```
[POLLUTE] during collection of tests/performance/test_knowledge_link_perf.py:
  manager.disable 0 -> 50
```

真凶现形：`tests/performance/test_knowledge_link_perf.py` 模块顶层有一行：

```python
logging.disable(logging.CRITICAL)   # 关闭业务日志，避免日志 IO 干扰计时
```

**问题不在这行代码本身，而在它的位置**。pytest 收集该文件时会 import 模块，`logging.disable` 是**进程级全局状态**（`manager.disable` 0→50），禁用后从不恢复——同进程所有 `assertLogs`/`caplog` 断言静默失效。

雪上加霜：`--ignore` 参数**无法拦截**分片脚本（`split_unit_tests.py`）显式传入的文件路径——目录被排除后，该文件才彻底移出测试进程。

### 修复：语义等价，位置平移

```python
@pytest.fixture(autouse=True)
def _silence_broken_link_warnings():
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)
```

fixture 内禁用、`finally` 恢复——**语义等价，零 import 副作用**。CI 转绿，`manager.disable` 回到 0。

### 教训

1. **模块顶层零副作用**是测试文件的红线：`logging.disable`、`basicConfig`、`setLevel`、改 `sys.path`、写文件，都可能在 collection 阶段执行。
2. 排查"偶发失败"时，先问：**有没有模块在 import 时改全局状态？** 用 hook 逐模块观测，比打堆栈更可靠。
3. serial 标记治"测试间并发竞争"，治不了"import 副作用全局污染"——两类问题要分开定性。

---

## 事件二：Shard 1 的 FileNotFoundError——干净环境才暴露

### 现象

修复事件一后，Shard 1 出现新失败：

```
FAILED test_audit_safety_logging_singleton.py::test_safe_logger_audit_logger_returns_same_instance
FileNotFoundError: [Errno 2] No such file or directory: '/home/runner/work/security-tools/security-tools/logs/audit.log'
```

本地跑同一测试：通过。又是"本地复现不了"。

### 根因：FileHandler 不创建父目录

```python
# agent/log_system/safe_logger.py（修复前）
if not self._logger.handlers:
    handler = logging.FileHandler(
        os.path.join(os.path.dirname(__file__), '..', '..', 'logs', 'audit.log'),
        encoding='utf-8'
    )
```

`logging.FileHandler` **只打开文件，不创建目录**。`logs/audit.log` 的父目录 `logs/` 必须预先存在。

为什么本地不炸？本地是历史覆盖安装，`logs/` 目录残留；CI 每次全新 checkout，`logs/` 不存在 → 首次实例化即抛异常。同项目另一个日志模块 `logging_utils.py` 有完整写法：

```python
log_path = os.path.join(os.path.dirname(__file__), '..', 'logs', 'audit.log')
os.makedirs(os.path.dirname(log_path), exist_ok=True)   # 目录保障
handler = logging.FileHandler(log_path, encoding='utf-8')
```

修复就是对齐这 3 行。验证：本地 `Test-Path logs = False`（模拟 CI 干净环境）→ 19/19 测试通过。

### 教训

1. **FileHandler/RotatingFileHandler 不建目录**，所有日志文件路径必须前置 `os.makedirs(exist_ok=True)`。
2. "本地能跑"不等于"环境无关"：检查是否存在**残留目录/文件掩盖了路径依赖**。CI 干净 checkout 是照妖镜。
3. 同类资源路径（缓存目录、临时目录）都应显式创建，不依赖宿主环境遗留。

---

## 方法论：两类"CI 专用 bug"的排查套路

| 特征 | import 副作用型 | 环境依赖型 |
|---|---|---|
| 现象 | 同进程大量断言集体失败 | 特定文件路径 FileNotFoundError |
| 触发 | collection 阶段 import 模块 | 首次实例化/首次写路径 |
| 复现 | 需 CI 等价命令（含分片/标记） | 删除残留目录/文件 |
| 定位 | pytest hook 观测全局状态 | 对比有/无目录两种环境 |
| 根治 | 模块顶层零副作用 | 路径显式 makedirs |

通用手法：**复现是第一步**。复现不了的环境差异（Windows vs Linux、残留目录 vs 干净 checkout、有无 `--cov`）逐项对照，直到 CI 失败条件在本地等价还原。

---

## 通用防御清单

1. **测试文件模块顶层**：不执行 `logging.disable`/`setLevel`/`basicConfig`、不改 `sys.path`、不写文件。需要静默日志 → autouse fixture + `try/finally`。
2. **日志/缓存路径**：统一 `os.makedirs(os.path.dirname(path), exist_ok=True)`，禁止依赖目录预存在。
3. **CI 触发路径（paths）完整性**：任何"承担全项目验证职责"的 workflow，其 `paths` 必须覆盖全部被测试业务代码目录，否则模块修复会静默绕过 CI——检查后发现 `agent/**` 未全覆盖、分片脚本未纳入，已补。
4. **函数内也要 finally**：`logging.disable` 即使不在模块顶层，也应包进 `try/finally`，断言失败时不留污染。

---

## 关联

- 修复提交：`305282cf`（logging.disable 副作用）、`2b6d51d2`（makedirs + paths）
- CI 验证：run 31358792972 全绿（6/6 shard + 质量门禁）
