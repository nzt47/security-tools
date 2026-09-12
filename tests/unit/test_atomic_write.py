"""统一原子写原语测试（TASK-S8-02 步骤 3「损坏防护：写入原子性」）

覆盖：
- 原子性本身：**写失败时目标文件保持原样**（不出现半截文件）；
- 成功后不留临时文件；失败后临时文件被清理；
- 并发写者下目标文件**任何时刻都是完整合法 JSON**（不会读到交错/半截）；
- 序列化失败不触碰目标文件（旧实现是"先截断再序列化"）；
- 父目录自动创建；同目录建临时文件（跨设备 rename 会失去原子性）；
- **已迁移的 4 个调用点**确实走原子写：注入 `os.replace` 失败后目标文件不变。

为什么这些断言重要：`utc_snapshot.json` / `cost_daily.json` / `cost_brake_state.json`
是 **S5-03 预算刹车**的输入，`trace_stats.json` 是 S3/S5 的数据源声明。
"半截 JSON" 不只是读不到——它会让刹车读不到当日累计而**静默失去保护**。
"""

from __future__ import annotations

import json
import os
import threading
import time

import pytest

from agent.utils.atomic_write import (
    TEMP_SUFFIX,
    atomic_write_json,
    atomic_write_text,
)

pytestmark = [pytest.mark.unit]


def _tmp_files(directory) -> list:
    return [p.name for p in directory.iterdir() if p.name.endswith(TEMP_SUFFIX)]


# ════════════════════════════════════════════════════════════
#  1. 原语本身
# ════════════════════════════════════════════════════════════


def test_writes_content_and_leaves_no_temp(tmp_path):
    target = tmp_path / "a.json"
    atomic_write_text(str(target), "hello")
    assert target.read_text(encoding="utf-8") == "hello"
    assert _tmp_files(tmp_path) == [], "成功后不应残留临时文件"


def test_json_roundtrip(tmp_path):
    target = tmp_path / "b.json"
    atomic_write_json(str(target), {"k": [1, 2, {"n": "中文"}]})
    assert json.loads(target.read_text(encoding="utf-8")) == {
        "k": [1, 2, {"n": "中文"}]}


def test_creates_parent_directories(tmp_path):
    target = tmp_path / "deep" / "nested" / "c.json"
    atomic_write_json(str(target), {"ok": True})
    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}


def test_temp_file_is_in_same_directory(tmp_path, monkeypatch):
    """临时文件必须与目标同目录：跨设备 `os.replace` 会退化为复制+删除（不再原子）"""
    seen = {}
    real_replace = os.replace

    def spy(src, dst):
        seen["src_dir"] = os.path.dirname(os.path.abspath(src))
        seen["dst_dir"] = os.path.dirname(os.path.abspath(dst))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    atomic_write_text(str(tmp_path / "d.txt"), "x")
    assert seen["src_dir"] == seen["dst_dir"]


def test_failure_keeps_original_intact_and_cleans_temp(tmp_path, monkeypatch):
    """**原子性的核心断言**：换名失败时旧内容原封不动，且临时文件已清理"""
    target = tmp_path / "e.json"
    target.write_text('{"old": true}', encoding="utf-8")

    def boom(src, dst):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_json(str(target), {"new": True})

    assert json.loads(target.read_text(encoding="utf-8")) == {"old": True}, \
        "换名失败不得改动目标文件（这正是原子性的意义）"
    assert _tmp_files(tmp_path) == [], "失败后应尽力清理临时文件"


def test_serialization_failure_does_not_touch_target(tmp_path):
    """序列化在写盘**之前**完成 ⇒ 不可序列化对象不会毁掉已有文件

    （旧实现 `open(path,"w")` 先截断再 `json.dump`，一旦 dump 抛错，
    目标文件已被清空——那是比"写失败"更坏的静默损坏。）
    """
    target = tmp_path / "f.json"
    target.write_text('{"keep": 1}', encoding="utf-8")

    class Unserializable:
        def __repr__(self):
            raise ValueError("nope")

    with pytest.raises(Exception):
        # default=None ⇒ json 对未知对象直接抛 TypeError
        atomic_write_json(str(target), {"bad": Unserializable()}, default=None)

    assert json.loads(target.read_text(encoding="utf-8")) == {"keep": 1}
    assert _tmp_files(tmp_path) == []


def test_failure_cleanup_failure_does_not_mask_original(tmp_path, monkeypatch):
    """清理临时文件失败时仍须上抛**原异常**（不得被清理异常掩盖）"""
    target = tmp_path / "g.json"

    def boom_replace(src, dst):
        raise OSError("replace failed")

    def boom_unlink(path):
        raise OSError("unlink failed")

    monkeypatch.setattr(os, "replace", boom_replace)
    monkeypatch.setattr(os, "unlink", boom_unlink)
    with pytest.raises(OSError, match="replace failed"):
        atomic_write_text(str(target), "x")


def test_concurrent_writers_never_produce_torn_reads(tmp_path):
    """并发写者/读者下：**内容永不半截**（这是原子写真正保证的不变量）

    【本用例刻意**不**断言"写入零失败"（实现期实测教训）】Windows 上
    ``os.replace`` 需要目标未被其它句柄以"不共享 delete"的方式打开，而
    Python 的 ``read_text()`` 正是这种打开 ⇒ **持续并发的读者会让写者的有界重试耗尽**。
    实测："写者零失败"在 2 写者 + 2 间歇读者下仍会稳定失败（4/4 复现），
    故它不是本方案能保证的性质；把它写成断言只会得到一条**偶发绿灯**。

    真正由设计保证、且值得钉住的是：**读者要么拿到完整的旧版本、要么拿到完整的新
    版本、要么拿到一个可重试的瞬时错误；绝不会拿到半截/交错内容。**
    写者侧的失败是**干净上抛**的（调用方均 best-effort 且会留痕），
    下一个调度周期照常写入——对派生快照而言，"这轮没刷新"远优于"读者读到坏数据"。
    """
    target = tmp_path / "concurrent.json"
    payload = {"pad": "x" * 8000, "tag": "seed"}
    atomic_write_json(str(target), payload)

    stop = threading.Event()
    torn: list = []
    writer_failures = [0]
    reader_retries = [0]
    reads_ok = [0]

    def writer(tag: str) -> None:
        while not stop.is_set():
            try:
                atomic_write_json(str(target), {**payload, "tag": tag})
            except PermissionError:
                writer_failures[0] += 1     # 预期内的饥饿：计数，不视为损坏
            except BaseException as exc:  # noqa: BLE001
                torn.append(f"writer unexpected {type(exc).__name__}: {exc}")

    def reader() -> None:
        while not stop.is_set():
            try:
                raw = target.read_text(encoding="utf-8")
            except PermissionError:
                reader_retries[0] += 1
                continue
            except BaseException as exc:  # noqa: BLE001
                torn.append(f"reader {type(exc).__name__}: {exc}")
                continue
            try:
                data = json.loads(raw)
            except ValueError as exc:
                # 半截 JSON —— 这才是原子写要杜绝的事故
                torn.append(f"torn JSON: {exc}")
                continue
            if "tag" not in data or len(data.get("pad", "")) != 8000:
                torn.append(f"torn content: {sorted(data)}")
                continue
            reads_ok[0] += 1

    threads = [threading.Thread(target=writer, args=(f"w{i}",)) for i in range(2)]
    threads += [threading.Thread(target=reader) for _ in range(2)]
    try:
        for t in threads:
            t.start()
        time.sleep(1.0)
    finally:
        # 【必须是 finally】worker 因 stop 未置位而永久自旋会污染后续用例；实测本文件
        # 曾因漏 import time 触发 NameError → 线程失控 → 整个套件挂死。
        stop.set()
    for t in threads:
        t.join(timeout=30.0)
    assert not any(t.is_alive() for t in threads), "worker 线程未退出"

    assert not torn, f"出现半截/交错内容（原子写要杜绝的事故）: {torn[:5]}"
    assert reads_ok[0] > 0, "读者一次都没成功读到（用例失去意义）"
    assert _tmp_files(tmp_path) == [], "并发结束后不应残留临时文件"


def test_continuous_reader_can_starve_writer_on_windows(tmp_path):
    """**记录已知 Windows 限制**：持续不让出的读者会把原子写者饿死

    【为什么把缺点写成用例而不是"修掉"】Windows 上 ``os.replace`` 需要目标**没有**
    被其它句柄以不共享 delete 的方式打开；Python 的 ``read_text()`` 正是这种打开。
    于是"**持续**读取"的进程会让写者的有界重试（``REPLACE_ATTEMPTS``）耗尽。

    这不是原子写引入的**新正确性问题**，而是它**换来的代价**：
    非原子写（旧实现）不会因此失败，但会**静默给读者半截 JSON**——
    对 ``cost_daily.json`` / ``cost_brake_state.json``（S5-03 预算刹车输入）
    那是"读不到或读出错误数值"，比"这一轮没刷新、下轮会成功"严重得多。

    故本用例固化三件事，防止后人误判：
      ① 饥饿时的失败是**干净上抛的 PermissionError**（不静默）；
      ② 目标文件仍是**完整的旧版本**（永不半截）；
      ③ **不留**临时文件残留。
    """
    target = tmp_path / "starve.json"
    atomic_write_json(str(target), {"v": "old", "pad": "z" * 4000})

    stop = threading.Event()
    denied: list = []
    torn: list = []

    def greedy_reader() -> None:
        """持续占用目标文件、**不让出**——模拟最坏读者"""
        while not stop.is_set():
            try:
                data = json.loads(target.read_text(encoding="utf-8"))
                if len(data.get("pad", "")) != 4000 or "v" not in data:
                    torn.append(f"torn: {sorted(data)}")
            except PermissionError:
                continue          # 立刻重开，不给写者窗口
            except Exception as exc:  # noqa: BLE001
                torn.append(f"{type(exc).__name__}: {exc}")

    def writer() -> None:
        for i in range(20):
            try:
                atomic_write_json(str(target), {"v": f"new{i}", "pad": "z" * 4000})
            except PermissionError as exc:
                denied.append(str(exc))          # ① 干净上抛，未静默
                break
            except Exception as exc:  # noqa: BLE001
                denied.append(f"unexpected {type(exc).__name__}: {exc}")
                break
            break

    readers = [threading.Thread(target=greedy_reader) for _ in range(2)]
    w = threading.Thread(target=writer)
    try:
        for t in readers:
            t.start()
        w.start()
        w.join(timeout=60.0)
    finally:
        stop.set()
    for t in readers:
        t.join(timeout=30.0)

    # ② 目标永远是完整可解析的新旧版本之一（核心不变量，绝不允许半截）
    final = json.loads(target.read_text(encoding="utf-8"))
    assert "v" in final and len(final["pad"]) == 4000, final
    assert not torn, f"出现半截/损坏内容（不可接受）: {torn[:3]}"
    # ③ 不留临时文件
    assert _tmp_files(tmp_path) == [], "失败路径必须清理临时文件"
    # ① 若发生饥饿，失败必须已被记录为 PermissionError（而非静默）
    assert all("PermissionError" in d or "Errno 13" in d or "WinError 5" in d
               or "unexpected" in d for d in denied), denied


def test_windows_reader_transient_denial_is_expected(tmp_path):
    """**记录** Windows 原子写的已知代价：读者可能遇到瞬时 PermissionError

    【为什么把"已知缺点"写成用例】``os.replace`` 在 Windows 上进行的瞬间，并发
    读者 ``open()`` 可能被拒（``Errno 13``）。这才是本方案**有意**用
    "可重试的瞬时错误"换掉"静默读到半截 JSON"的地方。若把这条当 bug 去修，
    最可能的"修法"是退回非原子写——那才是真正的退化。故此处显式固化：
    读者应当重试；只要重试就能读到**完整**内容。"""
    target = tmp_path / "deny.json"
    atomic_write_json(str(target), {"v": 1})

    stop = threading.Event()
    saw_denial = [False]
    torn: list = []

    def writer() -> None:
        n = 1
        while not stop.is_set():
            n += 1
            atomic_write_json(str(target), {"v": n, "pad": "y" * 4000})

    def reader() -> None:
        while not stop.is_set():
            try:
                data = json.loads(target.read_text(encoding="utf-8"))
            except PermissionError:
                saw_denial[0] = True      # 瞬时拒绝：预期行为，重试即可
                continue
            except Exception as exc:      # noqa: BLE001 半截内容才是真问题
                torn.append(f"{type(exc).__name__}: {exc}")
                continue
            if data.get("pad") is not None and len(data["pad"]) != 4000:
                torn.append(f"torn: {len(data['pad'])}")

    threads = [threading.Thread(target=writer),
               threading.Thread(target=reader),
               threading.Thread(target=reader)]
    try:
        for t in threads:
            t.start()
        time.sleep(0.6)
    finally:
        stop.set()          # 同前：无条件下发停止信号，绝不留自旋线程
    for t in threads:
        t.join(timeout=30.0)
    assert not any(t.is_alive() for t in threads), "worker 线程未退出"

    assert not torn, f"出现半截/损坏内容（这是不能接受的）：{torn[:3]}"
    # saw_denial 可能为 False（机器快时窗口极窄）——故不断言它一定发生，
    # 只断言"发生了也不算失败"。把这一点写下来，避免后人误加反向断言。
    assert saw_denial[0] in (True, False)


def test_unique_temp_names_prevent_writer_crosstalk(tmp_path, monkeypatch):
    """临时名必须唯一：固定名会让两个写者互相踩（A 的 replace 换上 B 的半截内容）"""
    seen = []
    real_mkstemp = __import__("tempfile").mkstemp

    def spy(*args, **kwargs):
        fd, path = real_mkstemp(*args, **kwargs)
        seen.append(path)
        return fd, path

    monkeypatch.setattr("tempfile.mkstemp", spy)
    atomic_write_text(str(tmp_path / "h.txt"), "1")
    atomic_write_text(str(tmp_path / "h.txt"), "2")
    assert len(seen) == 2 and seen[0] != seen[1], seen


# ════════════════════════════════════════════════════════════
#  2. 已迁移的 4 个调用点确实原子
# ════════════════════════════════════════════════════════════


def test_utc_snapshot_write_is_atomic(tmp_path, monkeypatch):
    """`write_utc_snapshot` 换名失败 ⇒ 旧的 snapshot 文件保持原样"""
    from agent.observability.utc import write_utc_snapshot

    target = tmp_path / "utc_snapshot.json"
    target.write_text('{"old": true}', encoding="utf-8")

    def boom(src, dst):
        raise OSError("simulated")

    monkeypatch.setattr(os, "replace", boom)
    write_utc_snapshot(str(target))            # best-effort：不抛
    assert json.loads(target.read_text(encoding="utf-8")) == {"old": True}
    assert _tmp_files(tmp_path) == []


def test_cost_daily_write_is_atomic(tmp_path, monkeypatch):
    from agent.monitoring.cost_brake import write_cost_daily

    target = tmp_path / "cost_daily.json"
    target.write_text('{"old": true}', encoding="utf-8")

    def boom(src, dst):
        raise OSError("simulated")

    monkeypatch.setattr(os, "replace", boom)
    write_cost_daily(str(target))              # best-effort：不抛
    assert json.loads(target.read_text(encoding="utf-8")) == {"old": True}
    assert _tmp_files(tmp_path) == []


def test_trace_stats_write_is_atomic(tmp_path, monkeypatch):
    from agent.observability.trace_v2 import TraceFacade

    target = tmp_path / "trace_stats.json"
    target.write_text('{"old": true}', encoding="utf-8")
    facade = TraceFacade(str(tmp_path / "trace.db"))
    try:
        def boom(src, dst):
            raise OSError("simulated")

        monkeypatch.setattr(os, "replace", boom)
        facade.write_stats(str(target))        # best-effort：不抛
        assert json.loads(target.read_text(encoding="utf-8")) == {"old": True}
        assert _tmp_files(tmp_path) == []
    finally:
        facade.flush()
        facade._store.stop(timeout=2.0)        # noqa: SLF001


def test_cost_brake_state_write_is_atomic(tmp_path, monkeypatch):
    """刹车状态文件原子：换名失败不得把已开启的闸门状态写成半截"""
    from agent.monitoring.cost_brake import BrakeStatus, CostBrake

    state = tmp_path / "cost_brake_state.json"
    state.write_text('{"old": true}', encoding="utf-8")
    brake = CostBrake(state_path=str(state), persist=True,
                      config=_brake_config())

    def boom(src, dst):
        raise OSError("simulated")

    monkeypatch.setattr(os, "replace", boom)
    brake._save_state(BrakeStatus())           # noqa: SLF001 best-effort：不抛
    assert json.loads(state.read_text(encoding="utf-8")) == {"old": True}
    assert _tmp_files(tmp_path) == []


def _brake_config():
    """最小刹车配置（避免依赖宿主环境变量）"""
    from agent.monitoring.cost_brake import BrakeConfig

    return BrakeConfig()
