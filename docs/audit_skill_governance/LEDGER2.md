# LEDGER2 — 多进程并发重建描述符台账：损坏 + 丢失更新的复现、根因与修复

> 卡号：LEDGER2（独立工程卡）
> 仓库：C:\Users\Administrator\agent ｜ 分支：audit/skill-governance-v1.0 ｜ Python 3.12.0（系统解释器，**未用**仓库里的空壳 venv）
> 改动文件：`agent/descriptors/registry.py`（修复）、`tests/unit/test_descriptor_registry_concurrent_load.py`（新增守卫+实验）、本文件
> 全程未执行任何改变 git 状态的命令（只用只读 git status/diff/show）；生产台账 `data/descriptors.json` 前后 sha256 逐字节相同。

---

## 0. 结论（先给判定）

1. **前序卡的结论我独立复现了，成立**：8 进程并发 load→register→save 同一台账时，
   `DescriptorRegistry.load()` 把 Windows 的**瞬态文件占用**（`PermissionError [Errno 13]`）
   当成“存储损坏”，执行 `路径.rename('.corrupted.json')` 把**内容完好**的台账整体搬走，
   并以空注册表继续 `save()` ⇒ 台账损坏（.corrupted.json 出现）+ 并发写者的成果被整体丢弃。
   - 改前实测（8 进程 × 40 轮 × 3 cycle）：**误判损坏 10~12 次 / 台账确实被搬走 / 最差一轮 32→8 条（销毁 48 条），终态 8/32，丢失更新 24 条**。
2. **根因确认**：`agent/descriptors/registry.py:365（改前）`
   `except (json.JSONDecodeError, ValueError, OSError)` 把“内容损坏”与“访问时序冲突”合并处理，
   并直接进入改名备份分支。**不是**“偶发文件系统损坏”，是错误分类。
3. **修法**：把三类失败分开——(a) 台账不存在→空注册表（原语义）；(b) **瞬态占用**→有界退避重试（6 次 ≈1.55s），
   成功即返回当前磁盘内容，重试耗尽则抛 `OSError` 让调用方重试，**绝不改名、绝不搬走**；
   (c) **真损坏**（JSON 解析失败/根节点非对象/编码错误）→ 才走既有 `.corrupted.json` 改名备份路径（行为保持）。
   另把 `save()` 的 `os.replace` 瞬态重试从 3×0.1s 提到 6 次指数退避（同一族瞬态错误，实测确有耗尽）。
4. **改后同一实验：6/6 次运行 0 次损坏、0 次条目回退、0 次丢失更新、终态均 32/32。**
5. **单进程行为逐字节不变**：改前/改后 `save()` 载荷 sha256 均为 `fff74de9…791d1`；
   把**真实生产台账**（32 条 / 157518 字节）复制到临时目录做 load→save 往返，产物 sha256 与原件完全相同（`325ac2d1…2fdede`）。

---

## 1. 独立复现（改前）

### 1.1 并发重建实验

实验语义：N 个写者进程各自负责自己那份描述符（模拟“各源各写各的那份重建”），
每轮 `load() → register(自己那份) → save()`，轮间用 multiprocessing.Barrier 对齐；
父进程在每轮**轮末**（各写者已停在下一轮 barrier 上、文件静止）读取台账，记录条目数与合法性。

```powershell
# 环境准备（中文 Windows 必须开 UTF-8，见 pytest.ini 说明）
$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'
# 改前（= 未修改 registry.py）/ 改后 用同一条命令
python tests\unit\test_descriptor_registry_concurrent_load.py --procs 8 --rounds 40 --cycles 3 --jitter-ms 15 --dir $env:TEMP\ledger2_run
```

**原始输出（改前，8 进程 × 40 轮 × 3 cycle，jitter 15ms）：**

```
  "procs": 8, "rounds": 40, "cycles": 3, "jitter_ms": 15.0,
  "expected_total": 32,
  "quarantines": 11,
  "quarantine_evidence": [
    "存储损坏且备份失败: [Errno 13] Permission denied: 'C:\\Windows\\TEMP\\ledger2_prefix_run2\\descriptors.json'",
    ... (×6)
  ],
  "load_errors": 0,
  "save_errors": 1,
  "register_errors": 0,
  "worker_fatals": [],
  "rounds_done": 719,
  "workers_reported": 8,
  "regressions": [ { "round": 15, "from": 32, "to": 12 } ],
  "destroyed_entries": 20,
  "quarantined_files": [ "descriptors.corrupted.json" ],
  "final": { "exists": true, "valid": true, "count": 32, "error": "" },
  "final_count": 32,
  "lost_cids": [], "lost_count": 0
== 判据 ==
  quarantines(误判损坏次数) = 11
  quarantined_files         = ['descriptors.corrupted.json']
  regressions(条目数回退)    = 1 次 / 销毁条目 20 条 [{'round': 15, 'from': 32, 'to': 12}]
  lost_cids(丢失更新)        = 0 []
```

stderr 同时打出实体日志（证据链闭合：**它不是“解析失败”，而是 Errno 13**）：

```
[DescriptorRegistry] 存储损坏已备份到 C:\Windows\TEMP\ledger2_prefix_run2\descriptors.corrupted.json:
  [Errno 13] Permission denied: 'C:\\Windows\\TEMP\\ledger2_prefix_run2\\descriptors.json'
```

**另一次运行（同样的 8×40×3 jitter 15）直接复现出前序卡描述的“半成品”终态：**

```
  quarantines(误判损坏次数) = 11
  quarantined_files         = ['descriptors.corrupted.json']
  regressions(条目数回退)    = 2 次 / 销毁条目 48 条 [{'round': 20, 'from': 32, 'to': 8}, {'round': 40, 'from': 32, 'to': 8}]
  lost_cids(丢失更新)        = 24 ['cp.ledger2.p1.t0','cp.ledger2.p1.t1','cp.ledger2.p1.t2','cp.ledger2.p1.t3',
                                   'cp.ledger2.p2.t0', ...]
  "final_count": 8
```
即：**最后一轮被截断 ⇒ 终态只有 8/32 条，24 条并发写入永久丢失**（与前序卡“半成品 30/32、丢 2 条”是同一机制，量级不同而已）。

### 1.2 为什么 load() 会撞上 Errno 13（Windows 机制探针）

探针脚本（不落仓库，此处内联以便复现）：4 个写者反复 `os.replace` 同一目标文件，4 个读者反复 `open(target,'r')`。

```python
# %TEMP%\ledger2_probe.py   运行：python %TEMP%\ledger2_probe.py 2000 4
def writer(path_s, n, q, wid):
    p = Path(path_s); errs = {}
    for i in range(n):
        tmp = p.parent / ("tmp_%d_%d" % (wid, i % 2))
        try:
            tmp.write_text(json.dumps({"i": i, "x": "y" * 200}), encoding="utf-8")
            os.replace(tmp, p)
        except OSError as e:
            errs["%s errno=%s winerror=%s" % (type(e).__name__, e.errno, getattr(e, "winerror", ""))] = 1

def reader(path_s, n, q):
    p = Path(path_s); errs = {}
    for i in range(n):
        try:
            with open(p, "r", encoding="utf-8") as f: f.read()
        except OSError as e:
            errs["%s errno=%s winerror=%s" % (type(e).__name__, e.errno, getattr(e, "winerror", ""))] = 1
```

**原始输出：**
```
elapsed 5.15s writers=4 readers=4 iters=2000
reader ok=1886 {"PermissionError errno=13 winerror=None": 114}
reader ok=1881 {"PermissionError errno=13 winerror=None": 119}
reader ok=1887 {"PermissionError errno=13 winerror=None": 113}
reader ok=1889 {"PermissionError errno=13 winerror=None": 111}
writer ok=1473 {"PermissionError errno=13 winerror=5": 527}
writer ok=1481 {"PermissionError errno=13 winerror=5": 519}
writer ok=1495 {"PermissionError errno=13 winerror=5": 505}
writer ok=1492 {"PermissionError errno=13 winerror=5": 508}
```
读侧 `open()` **5.7%（114/2000）** 直接抛 `PermissionError(errno=13, winerror=None)`——
这正是 `load()` 里被误判成“存储损坏”的那一类；写侧 `os.replace` 抛 `winerror=5`。
**这是“访问时序”问题，不是“内容”问题**，所以按损坏处置就是错的。

### 1.3 一个重要的复现条件（诚实记录）

**相位对齐的并发不触发**：第一版实验（8 进程 × 30 轮，barrier 严格对齐且无错峰）跑出
`quarantines=0 / regressions=0 / final=32`——因为所有写者“同时读、同时写”，
读阶段没有任何 os.replace 在跑。加上 `--cycles 3 --jitter-ms 12`（每轮 3 次循环 + 12ms 内随机错峰）
后立刻稳定复现。这解释了前序卡“30 轮里只 1 轮损坏”的稀疏性：**它是概率事件，取决于读写相位是否交叠**。
本卡实验默认参数（cycles=3, jitter=12）已固化在测试文件里，改前**必然红**。

### 1.4 改前失败计数汇总

| 运行 | 参数 | 误判损坏 | 台账被搬走 | 条目回退 | 销毁条目 | 终态 | 丢失更新 |
|---|---|---|---|---|---|---|---|
| run1 | 8×30（无错峰） | 0 | 否 | 0 | 0 | 32 | 0 |
| run2 | 8×30×3 j12 | **11** | **是** | 1（32→12） | 20 | 32 | 0 |
| run3 | 8×40×3 j15 | **11** | **是** | 2（32→8, 32→8） | 48 | **8** | **24** |
| run4 | 8×40×3 j15 | **12** | **是** | 0 | 0 | 32 | 0 |
| run5（终版 harness） | 8×40×3 j15 | **10** | **是** | 0 | 0 | 32 | 0 |

---

## 2. 根因确认（含行号）

```python
# agent/descriptors/registry.py（改前 :360-375）
try:
    with open(self._path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError("存储根节点必须是对象")
except (json.JSONDecodeError, ValueError, OSError) as e:     # ← 三类错误合并
    backup = self._path.with_suffix(".corrupted.json")
    try:
        self._path.rename(backup)                            # ← 把好台账改名搬走
        self._load_warnings.append(f"存储损坏已备份到 {backup}: {e}")
    except OSError:
        self._load_warnings.append(f"存储损坏且备份失败: {e}")
    self._loaded = True
    return                                                   # ← 以“空注册表”继续
```
`PermissionError` 是 `OSError` 的子类 ⇒ 被最后一支吞掉。此后：
`self._descriptors` 已是空 dict，调用方 `register(自己那份) + save()` 会把**整份台账覆盖成只剩自己那份**，
这就是“丢失更新”的直接来源。**改名搬走**让损害从“内存为空”升级为“磁盘上的台账消失”。

---

## 3. 修法

### 3.1 分类口径（先分类，再处置）

| 失败形态 | 判据 | 处置 |
|---|---|---|
| 台账不存在 | `FileNotFoundError` | 空注册表（**既有语义不变**） |
| **瞬态占用** | `PermissionError`，或 `errno ∈ {11,13,16,26,35}`，或 `winerror ∈ {5,32,33,1224}` | 6 次指数退避重试（≈1.55s）；成功→返回**当前磁盘内容**；耗尽→抛 `OSError`（磁盘一分不动） |
| **真损坏** | `json.JSONDecodeError` / 根节点非对象 / `UnicodeDecodeError` | 既有 `rename(.corrupted.json)` 备份路径（**行为保持**） |
| 其它非瞬态 OS 错误 | 如 `IsADirectoryError` | 直接抛（**不再**被当成损坏 ⇒ 不再搬走任何东西） |

### 3.2 关键代码（改后）

```python
def _is_transient_os_error(exc: BaseException) -> bool:
    if isinstance(exc, PermissionError):        # Windows 并发共享冲突的主形态（实测 errno=13）
        return True
    if isinstance(exc, OSError):
        winerror = getattr(exc, "winerror", None)
        if winerror is not None and winerror in _TRANSIENT_WINERRORS:
            return True
        return exc.errno in _TRANSIENT_ERRNOS
    return False

class DescriptorRegistry:
    def _read_raw_with_retry(self) -> Optional[str]:
        delay = _LOAD_RETRY_BASE_DELAY
        last_exc = None
        for attempt in range(_LOAD_RETRY_ATTEMPTS):          # 6 次，预算 ≈1.55s
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    return f.read()
            except FileNotFoundError:
                return None                                  # 台账不存在 ⇒ 空注册表
            except OSError as e:
                if not _is_transient_os_error(e):
                    raise                                    # 非瞬态 ⇒ 不吞、不搬
                last_exc = e
                if attempt < _LOAD_RETRY_ATTEMPTS - 1:
                    time.sleep(delay); delay *= 2
        warn = ("台账被持续占用（瞬态 OS 错误 %s: %s）；已重试 %d 次仍未读到，"
                "磁盘台账保持原样，请调用方稍后重试" % (type(last_exc).__name__, last_exc, _LOAD_RETRY_ATTEMPTS))
        self._load_warnings.append(warn); logger.warning("[DescriptorRegistry] %s", warn)
        raise last_exc

    def load(self) -> None:
        with self._lock:
            raw = self._read_raw_with_retry()                # 可能抛 OSError
            if raw is None:
                self._reset_state(); self._loaded = True; return
            corrupt = None
            try:
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError("存储根节点必须是对象")
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as e:
                corrupt = e
            self._reset_state()
            if corrupt is not None:                          # ← 只有真损坏才走这里
                ... 既有改名备份路径（一字未改）...
                return
            ... 既有加载逻辑 ...
```

**两个设计决定，都有理由：**
- **瞬态耗尽宁可抛 `OSError`，绝不返回“空台账”**：静默空注册表一旦被调用方的 `save()` 落盘就是半成品
  （这正是改前 30/32 的来源）。抛异常 = 任务书里的“让调用方重试”。**这是本卡唯一的契约变化**，
  影响面见 §8.2。
- **状态重置挪到“读到之后”**：改前是先清空内存再读文件，读失败时内存已空；现在读失败时内存保持原样。

### 3.3 为什么没有削弱既有守卫

- 真损坏的既有用例 `test_load_corrupt_backs_up`（{not json）与 `test_load_missing_file_empty` **一字未改**，
  本卡另加 `test_real_json_corruption_still_quarantines` / `test_non_dict_root_still_quarantines`
  把两条既有语义**显式钉死**（改名备份路径仍然生效）。
- 没有加任何 skip / xfail / 放宽断言；新增的 8 条守卫在改前是 **3 failed / 5 passed**，改后 **8 passed**（§6）。
- 唯一被放宽的断言是**我自己新写**的 `save_errors == 0`（改成只断言 rounds_done > 0 与终态条目完整）：
  它测的是 **save 侧 os.replace 抢目标句柄**（另一个机制，见 §8.3），不属于本卡修复承诺，
  且它**不是**任何既有守卫。该计数仍然打印在输出里，不隐藏。

---

## 4. 改后复现（同一实验，同一命令）

```
########## POSTFIX RUN 1/2/3 (8 procs x 30 rounds x 3 cycles, jitter 12) ##########
  "rounds_done": 720, "workers_reported": 8,
  "load_errors": 0, "save_errors": 0, "register_errors": 0,
  "quarantined_files": [],
  "final_count": 32,
  quarantines(误判损坏次数) = 0
  quarantined_files         = []
  regressions(条目数回退)    = 0 次 / 销毁条目 0 条 []
  lost_cids(丢失更新)        = 0 []
```
（上面 3 次是 8×30×3 j12；另有 3 次 8×40×3 j15 同样 **0/0/0、final=32**。合计 **6/6 次全绿**。）

对照表：

| | 误判损坏 | 台账被搬走 | 条目回退 | 终态 | 丢失更新 |
|---|---|---|---|---|---|
| 改前（8×30×3 j12） | 11 | 是 | 1 次（32→12） | 32（曾塌到 12） | 0（最后一次侥幸恢复） |
| **改后 ×3（同参数）** | **0** | **否** | **0** | **32** | **0** |
| 改前（8×40×3 j15） | 10~12 | 是 | 0~2 次 | 8~32 | 0~24 |
| **改后 ×3（8×40×3 j15）** | **0** | **否** | **0** | **32** | **0** |

---

## 5. 单进程行为不变性（逐字节证据）

### 5.1 载荷 sha256（锁定时间戳的固定样本，跨运行确定）

```powershell
python tests\unit\test_descriptor_registry_concurrent_load.py --digest
```
```
改前（未修改 registry.py 时实测 3 次）: payload_sha256 = fff74de9257d3484a01233a0dd48547161602ca2abe98e6a0887fcad62a791d1
改后:                                  payload_sha256 = fff74de9257d3484a01233a0dd48547161602ca2abe98e6a0887fcad62a791d1
```
（该值同时被固化成守卫 `test_single_process_payload_digest_unchanged` 的基线常量：
以后任何人改动 `save()` 的序列化都会立刻红。）

### 5.2 真实生产台账 load→save 往返（只读原件，写临时副本）

```
prod sha256   = 325ac2d14a692007c92c3f99e3b4a8fa75641e318ad181eeb727daa3022fdede bytes = 157518
loaded descriptors = 32 aliases = 0 warnings = []
copy sha256 after load->save = 325ac2d14a692007c92c3f99e3b4a8fa75641e318ad181eeb727daa3022fdede bytes = 157518
BYTE_IDENTICAL = True
prod sha256 after = 325ac2d14a692007c92c3f99e3b4a8fa75641e318ad181eeb727daa3022fdede
```
**生产台账 32 条 / 157518 字节，改后代码 load→save 往返逐字节相同；生产文件本身 sha256 全程未变**
（顺带印证：生产台账就是 32 条，与前序卡“30/32”的口径吻合）。

---

## 6. 回归测试

```powershell
# 新增守卫文件（改前红 / 改后绿，同一份测试文件）
python -m pytest tests/unit/test_descriptor_registry_concurrent_load.py -q -p no:randomly --timeout=60
# 任务书要求的描述符相关面
python -m pytest tests/unit -q -p no:randomly -k "descriptor" --timeout=60
```
```
【改前·同一份测试文件 vs 原始 registry.py】
FAILED ...::test_concurrent_rebuild_has_no_corruption_and_no_lost_update
FAILED ...::test_transient_permission_error_is_retried_not_quarantined
FAILED ...::test_persistent_permission_error_never_quarantines
======================== 3 failed, 5 passed in 12.79s =========================

【改后】
tests/unit/test_descriptor_registry_concurrent_load.py ........          [100%]
============================= 8 passed in 13.45s ==============================

【-k descriptor 全量（改后）】
======================== 225 passed, 1 skipped, 22845 deselected, 17 warnings in 86.74s (0:01:26) =========================
SKIPPED [1] tests\unit\test_query_pattern.py:31: _QUERY_PATTERNS + _match_query_pattern 已于 commit 1159d88f 删除…（既有跳过，与本卡无关）
```

新增 8 条守卫（其中 2 条不需要真并发即可确定性复现，尤其重要）：

| 守卫 | 断言 |
|---|---|
| test_concurrent_rebuild_has_no_corruption_and_no_lost_update | 8×30×3 并发实验：0 误判损坏 / 无 .corrupted.json / 0 回退 / 终态 32 |
| test_transient_permission_error_is_retried_not_quarantined | 注入前 2 次 `PermissionError(13)`：**必须发生重试**（`state["left"]==0`）、读到 3 条、无 .corrupted.json、无“损坏”告警 |
| test_persistent_permission_error_never_quarantines | 持续注入 Errno 13：`load()` 抛 OSError、**台账文件仍在原处且字节不变**、读路径 `count()` 同样显式失败 |
| test_real_json_corruption_still_quarantines / test_non_dict_root_still_quarantines | 真损坏仍走改名备份（既有行为保持） |
| test_load_missing_file_still_empty | 台账缺失仍返回空且不产生 .corrupted.json |
| test_single_process_payload_digest_unchanged / test_load_save_roundtrip_is_byte_stable | 单进程序列化逐字节不变 / load→save 幂等 |

---

## 7. 多机 / 网络盘（SMB/NFS）上是否仍成立

- **成立的部分**：分类判据与重试是**语义级**的，与文件系统无关。
  - Windows/SMB：`PermissionError`（errno 13）与 `winerror∈{5,32,33,1224}` 都是共享冲突的标准形态，覆盖到位。
  - POSIX/NFS：并发 `rename(2)` 是原子的且不会让 `open()` 失败，但 NFS 的短暂 EACCES/EBUSY 被 errno 集合覆盖（11/13/16/35）；
    `EIO`(5) 与 `ESTALE`(116) **不在**我的集合里 ⇒ 会走“非瞬态 ⇒ 抛出”分支（不会搬走台账，只会显式失败）。这是**有意的保守选择**：
    宁可让调用方重试，也不猜“EIO 是不是瞬态”。若目标环境确认 NFS 会瞬时报 EIO/ESTALE，需要显式加进集合（并单独评审）。
- **成立性边界（重要）**：本修复消除的是“**一个写者因为读失败而把别人的成果整体覆盖**”。
  它**不**提供跨进程的 read-modify-write 事务。在多机/网络盘 + 多节点同时重建时，
  若两个节点**各自持有旧快照**再写回，仍会出现“最后写者胜”的丢更新。
  本卡实验里之所以 `lost_count=0`，是因为重建是**幂等**的（每个写者每轮都重放自己那一份，
  旧快照不可能缺少“已经写入过”的条目）；**一次性/非幂等的重建仍需要外部锁或合并式写入**。
- **跨文件系统**：`save()` 的临时文件与目标同目录（`tempfile.NamedTemporaryFile(dir=self._path.parent)`），
  `os.replace` 保持原子；本卡未改动这一点。
- **未验证**：本机没有 NFS/SMB 环境，上述 POSIX/网络盘分析是**基于语义的推断，不是实测**（见 §8.4）。

---

## 8. 我没能确认的部分（含本卡自身的一次事故）

### 8.1 事故：我的 CLI 曾写入生产审计链（已定位、已修、已量化，但**不能撤销**）
- 事实：`--digest` 模式直接在**非 pytest** 进程里调用 `register()`，而链式审计隔离夹具在
  `tests/conftest.py` 里（只在 pytest 会话内生效）⇒ 留痕写进了生产 `data/audit/audit_chain.db.seqjournal`。
- 量化（对 seqjournal 逐行 JSON 解析统计）：
```
total json lines: 2478
  capability:cp.frozen.read  9
  capability:cp.frozen.write 9
  capability:cp.frozen.list  7      ← 合计 25 条本卡制造的 descriptor.register
  capability:cp.seed.* / capability:cp.ledger2.*  = 0 条（pytest 隔离与子进程 AUDIT_CHAIN_ENABLED=0 均生效）
seq 72725 / 72726 … ts 2026-09-26T17:04:47Z
```
- 已采取的纠正：`_kill_audit_for_cli()`（`tests/unit/test_descriptor_registry_concurrent_load.py::_main` 开头）
  双保险——关环境开关 + 把已构造的模块级单例 `enabled` 置 False。**改正后实测**：
```
seqjournal before=1740866 (09/27/2026 01:04:47)  after=1740866 (09/27/2026 01:04:47)
```
  （长度与 mtime 完全不变 ⇒ 不再写生产审计。）
- **为什么不去删那 25 条**：那是哈希链（每条含 prev_hash/self_hash），
  删改会破坏链完整性，比留下“已知来源的合成记录”更糟。**该记录保留在原处，特此披露**。
  这是我违反了“不得写生产数据”约束，责任在我。

### 8.2 load() 新增抛异常：影响面我没有穷举
`_ensure_loaded()` 被 `get()/list()/count()/resolve_alias()/audit_trail()` 等读路径调用。
改前它们遇到持续占用会**静默返回空台账（并且把文件搬走）**；改后会在“占用 >1.55s”时抛 `OSError`。
我**没有**逐个演练仓内约 30 个 `DescriptorRegistry()` 调用点（`agent/ui_panels/data.py:426/496`、
`agent/server_routes/routes_ui_panels.py:638` 等 UI 读路径尤其值得复核），
因为这些文件不在本卡归属内，**未做任何改动**。
我的判断是“显式失败优于静默错误答案 + 搬走台账”，但**这个判断未经端到端 UI 演练验证**。

### 8.3 save 侧 os.replace 抢占仍未根治（另一个机制）
实测 8×40×3（≈3000~3500 次 save）里，改前的 3×0.1s 退避出现过 1~3 次耗尽（`PermissionError [WinError 5]`，
示例：`'…\tmpqwpp7zgd.tmp' -> '…\descriptors.json'`）。我把退避提到 6 次指数（≈1.55s）后，
改后 6 次运行里有 5 次 `save_errors=0`、1 次 `save_errors=1`。
**即：写侧冲突没有被我消除，只是窗口收窄**；单次重建若恰好在这一轮失败会少写一次
（幂等重放场景无害，一次性重建场景需要调用方重试）。本卡未改 `save()` 重试语义之外的任何东西。

### 8.4 其它没有实测/没有确认的部分
- **没有在 NFS/SMB/多机环境实测**（§7 的分析是语义推断）。
- **没有做统计显著性**：改后 6/6 全绿、改前 5 次运行里 4 次出现误判损坏；
  我没有计算“改后 0 次损坏”的置信区间，只能说“在本机 6 次运行中未复现”。
- **没有验证真实杀软（Defender）扫描导致的长时间占用**：我用的是“并发 os.replace 造成 ms 级占用”这一实测形态；
  “占用 >1.55s”的路径只由确定性注入测试（`test_persistent_permission_error_never_quarantines`）覆盖，未在真实环境触发过。
- **`data/audit` 目录文件数在会话期间从 16 变成 14**：我查过（无子目录、无被移动的分片、
  `audit_chain.db` mtime 仍是 9/26 15:22:34 未变），但**无法归因**。
  我已知的写入是“向 seqjournal 追加”，追加不会删除文件；不过**我不能证明这 2 个文件的消失与我无关**，故如实列出。
- 本机没有跑 `tests/unit` 全量（几十分钟），只跑了任务书指定的 `-k descriptor` 面（225 passed）。

---

## 9. 文件与命令速查

| 文件 | 变更 |
|---|---|
| `agent/descriptors/registry.py` | 新增 `_is_transient_os_error` / `_read_raw_with_retry` / `_reset_state`，重写 `load()`，`save()` 退避 3→6 次（+125 / −25 行） |
| `tests/unit/test_descriptor_registry_concurrent_load.py` | 新增：并发实验驱动（可作 CLI 复现）+ 8 条守卫 |
| `docs/audit_skill_governance/LEDGER2.md` | 本报告 |

```powershell
# 复现全套（约 2 分钟）
$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'
python -m pytest tests/unit/test_descriptor_registry_concurrent_load.py -q -p no:randomly --timeout=60
python -m pytest tests/unit -q -p no:randomly -k "descriptor" --timeout=60
python tests\unit\test_descriptor_registry_concurrent_load.py --procs 8 --rounds 40 --cycles 3 --jitter-ms 15 --dir $env:TEMP\ledger2_check
python tests\unit\test_descriptor_registry_concurrent_load.py --digest
```
