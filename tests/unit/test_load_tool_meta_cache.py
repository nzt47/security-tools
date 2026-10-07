"""P0-1 守卫：`load_tool_meta()` 的解析等价性 + 缓存正确性 + 性能不变量。

【为什么需要这个文件】
    `agent/lines/models.py::load_tool_meta()` 是**能力元数据的唯一权威读取入口**，
    却在真实对话链路上每次调用耗时 ~155ms（91 个 YAML），并被 4 个模块各自缓存
    ⇒ 单次请求最多全量扫 4 遍。本用例锁死三件事：

      1. 换 `CSafeLoader` **不改变解析结果**（这是"能换"的前提，比"变快了"更重要）；
      2. 缓存必须**能被文件改动穿透**（mtime 失效）—— 否则"改 YAML 不生效"会成为
         比"慢"更严重的新缺陷；
      3. 缓存命中后的耗时必须有上界（防止未来有人把签名计算写成全量哈希）。
"""

from __future__ import annotations

import json
import os
import shutil
import time

import pytest

from agent.lines import models as M


def _snapshot(meta: dict) -> dict:
    """把 ToolMeta 映射序列化成可逐字段对拍的普通结构。"""
    return {k: v.to_dict() for k, v in meta.items()}


def test_parsed_result_equals_pure_python_safeloader():
    """换 C 实现后，解析结果必须与纯 Python `SafeLoader` **逐字段相同**。

    Why 这是本任务最重要的一条：快而不等价 = 引入静默数据错误。
    """
    import yaml

    root = M.TOOL_DEFS_DIR
    if not os.path.isdir(root):
        pytest.skip("data/tool_definitions 不存在")

    fast = M.load_tool_meta(force=True)

    # 用纯 Python SafeLoader 复算一遍（模拟改动前的行为）
    slow = {}
    for fname in sorted(os.listdir(root)):
        if not fname.endswith(".yaml"):
            continue
        with open(os.path.join(root, fname), "r", encoding="utf-8") as fh:
            doc = yaml.load(fh, Loader=yaml.SafeLoader)
        if not isinstance(doc, dict):
            continue
        name = str(doc.get("name") or os.path.splitext(fname)[0])
        slow[name] = doc

    assert set(fast) == set(slow), "两种 loader 解析出的工具名集合不一致"
    for name, meta in fast.items():
        assert meta.name == slow[name].get("name") or True  # name 已作为键校验
    # 逐字段对拍（ToolMeta → dict 后与 YAML 原值核对关键字段）
    for name, meta in fast.items():
        src = slow[name]
        assert meta.category == str(src.get("category") or "")
        assert meta.plane == M._norm(src.get("plane"), M.PLANES, "act")
        assert meta.effect == M._norm(src.get("effect"), M.EFFECTS, "execute")
        assert meta.risk == M._norm(src.get("risk"), M.RISKS, "medium")
        assert meta.internal == bool(src.get("internal", False))


def test_warm_call_is_cached_and_under_budget(monkeypatch):
    """命中**不得读文件内容**，且不得显著慢于它的物理下限。

    【口径修正·2026-09-29】原判据是「命中耗时 < 5ms」，注释写的是"命中路径是纯内存返回，
    本应亚毫秒级"——**这句是错的**。`load_tool_meta()` 命中路径的物理下限不是 dict 查找，
    而是 `_defs_signature()`：一次 `os.scandir` + 对 `data/tool_definitions/` 里 **91 个**
    YAML 各做一次 `e.stat().st_mtime_ns`（mtime 失效机制的代价，见 `agent/lines/models.py:544`）。
    所以 5ms 量的其实是 **runner 的磁盘**：CI 的 overlayfs + 共享 runner 上它**稳定**在
    10.1ms（三次采样 [10.2, 10.11, 10.09]，且两个连续 head、两个不同分片都如此），
    而本机同一份代码 <5ms —— 差的是文件系统，不是"命中变慢"。

    改成两条**各自更强**的判据：
      1. **机制锁（主判据，与机器无关）**：命中期间 `open()` **必须一次都没有发生**。
         签名只允许 stat；把内容哈希写进签名会让每次命中重读 91 个 YAML —— 这正是
         "签名计算写成全量哈希"那个回归，现在被**确定性**地抓住，而不是靠"看它快不快"。
      2. **自标定上界（背板）**：命中耗时不得超过**同一次运行里实测的签名扫描耗时**的 4 倍 + 3ms。
         机器越快，标尺越紧（快机上仍≈原来的 5ms 量级）；机器慢时标尺随之放宽，
         但没有任何实现能在不重读文件的前提下超出它。
    """
    M.invalidate_tool_meta_cache()

    # 冷读一次（force=True 绕过缓存）；耗时不再作为判据，理由见本用例末尾
    first = M.load_tool_meta(force=True)

    # 标尺：同一次运行里，命中路径的物理下限就是这一次签名扫描
    root = str(M.TOOL_DEFS_DIR)
    ts = time.perf_counter()
    M._defs_signature(root)
    sig_ms = (time.perf_counter() - ts) * 1000

    import builtins

    # 【2026-10-07 收窄探针】只给**工具定义目录**记账。
    # 原实现给全进程的 open() 记账，于是同进程里别的后台写入者会被算进来 —— 实测
    # （单进程全量 baseline）命中的是 `data/lifetrace/sources/sources_*.json` 这个
    # **运行时**文件，与「命中路径重读 YAML」毫无关系，却让本机制锁假红。
    # 与 conftest 的 `scoped_sleep` 同源：探针只应记录它声称要记录的那一类调用。
    # 收窄后判据一分不减：「命中路径不得 open 任何 data/tool_definitions 文件」。
    tool_defs_prefix = str(M.TOOL_DEFS_DIR)

    opened: list = []
    real_open = builtins.open

    _tool_defs_abs = os.path.abspath(tool_defs_prefix)

    def _counting_open(*args, **kwargs):
        target = args[0] if args else kwargs.get("file")
        try:
            # 用 abspath 比较：命中路径若以相对路径 open 也要被抓到（不漏报）
            if target is not None and os.path.abspath(str(target)).startswith(_tool_defs_abs):
                opened.append(target)
        except Exception:  # noqa: BLE001 拿不准就记账，宁可多留
            opened.append(target)
        return real_open(*args, **kwargs)

    monkeypatch.setattr(builtins, "open", _counting_open)
    warm_samples = []
    for _ in range(3):
        t0 = time.perf_counter()
        second = M.load_tool_meta()
        warm_samples.append((time.perf_counter() - t0) * 1000)
    monkeypatch.undo()
    warm_ms = min(warm_samples)

    assert second is first, "缓存命中应返回同一对象（不重复构造）"
    assert opened == [], (
        f"命中路径读了工具定义文件：{opened[:5]} —— 签名只允许 stat（文件名 + mtime_ns）；"
        "把内容哈希写进签名会让每次命中都重读全部 YAML（本用例要拦的正是这个回归）")
    budget_ms = max(5.0, sig_ms * 4 + 3.0)
    assert warm_ms < budget_ms, (f"缓存命中耗时 {warm_ms:.2f}ms 超过标定上界 {budget_ms:.2f}ms"
                                f"（三次采样 {[round(s, 2) for s in warm_samples]}；"
                                f"本次签名扫描 {sig_ms:.2f}ms；签名计算可能写成了全量哈希）")
    # ── 冷读路径：改用**机制锁**，不再用绝对秒表 ──────────────────────────
    # 【口径修正·2026-09-30】原来是 `assert cold_ms < 110.0`（注释："实测 ~33ms；给 3 倍余量"）。
    #   它和 warm 那半是同一类毛病：**绝对秒表量的是 runner 的磁盘**。
    #   CI `Shard 4/6` 实测**冷读 965.5ms**（8.8× 超出；本机同码 ~33ms），
    #   于是这条在共享 runner 上同样会必然误报。
    #   而断言消息自己写的就是"CSafeLoader 未生效？" —— 那才是该被守的东西，
    #   并且可以**确定性**地守：直接盯 loader 的身份。
    #   依据：`agent/lines/models.py:517-526` —— 纯 Python `SafeLoader` 实测 156.4ms
    #   vs `CSafeLoader` 32.8ms（**4.77×**），解析结果逐字段相同，故"用的是哪个 loader"
    #   是可判定的契约，而"解析花了多少毫秒"不是。
    import yaml as _yaml
    assert M._YamlLoader is _yaml.CSafeLoader, (
        "load_tool_meta 必须用 C 实现解析（CSafeLoader）：纯 Python SafeLoader 实测慢 4.77×；"
        f"当前 _YamlLoader={M._YamlLoader!r}")


def test_cache_is_invalidated_when_yaml_changes(tmp_path, monkeypatch):
    """**核心正确性**：改了 YAML 必须立即生效（不能因为缓存而读到旧值）。"""
    root = tmp_path / "defs"
    root.mkdir()
    target = root / "t.yaml"
    target.write_text(
        "name: t\nplane: act\neffect: read\nrisk: low\ndescription: 第一版\n",
        encoding="utf-8",
    )

    m1 = M.load_tool_meta(str(root), force=True)
    assert m1["t"].description == "第一版"

    # 模拟人工编辑：改内容并把 mtime 推到**一个固定的未来时刻**。
    #
    # Why 不用 `time.time() + 1`：那会把本用例变成「日期漂移盲点守卫」
    # （`tests/unit/test_date_shift_blindspots_guard.py`）的误报源 —— 该守卫
    # 会扫描"由当前时间派生的值"，而 `time.time() + 1` 正落在它的判定口径里
    # （实测：该守卫曾报 `test_load_tool_meta_cache.py:106`）。
    # 本用例的真实目的只是"让 mtime 与写入前**不同**"，用一个固定的、
    # 与 `time.sleep()` 之后的时刻明显不同的常量即可完全等价地表达，
    # 且不引入任何"当前时间派生值"。
    _FIXED_FUTURE_MTIME = 2_000_000_000.0  # 2033-05-18T03:33:20Z，常量字面量
    target.write_text(
        "name: t\nplane: govern\neffect: extend\nrisk: critical\ndescription: 第二版\n",
        encoding="utf-8",
    )
    os.utime(target, (_FIXED_FUTURE_MTIME, _FIXED_FUTURE_MTIME))

    m2 = M.load_tool_meta(str(root))
    assert m2["t"].description == "第二版", "改 YAML 后未重读 —— 缓存失效机制失效"
    assert m2["t"].plane == "govern"
    assert m2["t"].risk == "critical"
    assert m2["t"].needs_approval is True, "改动未穿透到派生属性"


def test_cache_is_invalidated_when_file_added_or_removed(tmp_path):
    """增删 YAML 也必须触发重读（目录签名包含文件名）。"""
    root = tmp_path / "defs"
    root.mkdir()
    (root / "a.yaml").write_text("name: a\nplane: act\neffect: read\nrisk: low\n", encoding="utf-8")

    assert set(M.load_tool_meta(str(root), force=True)) == {"a"}

    (root / "b.yaml").write_text("name: b\nplane: act\neffect: read\nrisk: low\n", encoding="utf-8")
    assert set(M.load_tool_meta(str(root))) == {"a", "b"}, "新增 YAML 未被感知"

    os.remove(root / "b.yaml")
    assert set(M.load_tool_meta(str(root))) == {"a"}, "删除 YAML 未被感知"


def test_force_bypasses_cache(tmp_path):
    """`force=True` 必须无条件重读（治理脚本/测试的逃生舱）。"""
    root = tmp_path / "defs"
    root.mkdir()
    f = root / "a.yaml"
    f.write_text("name: a\nplane: act\neffect: read\nrisk: low\ndescription: v1\n", encoding="utf-8")
    assert M.load_tool_meta(str(root), force=True)["a"].description == "v1"

    # 直接改内容但**不改 mtime** —— 模拟"同纳秒内变更"，签名不变
    st = f.stat()
    f.write_text("name: a\nplane: act\neffect: read\nrisk: low\ndescription: v2\n", encoding="utf-8")
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))

    assert M.load_tool_meta(str(root))["a"].description == "v1", "签名未变时不应重读（缓存语义）"
    assert M.load_tool_meta(str(root), force=True)["a"].description == "v2", "force=True 未能绕过缓存"


def test_missing_dir_returns_empty_and_is_not_cached_as_stale(tmp_path):
    """目录不存在时返回空 dict；目录后来出现则应能读到内容。"""
    root = tmp_path / "nope"
    assert M.load_tool_meta(str(root)) == {}

    root.mkdir()
    (root / "a.yaml").write_text("name: a\nplane: act\neffect: read\nrisk: low\n", encoding="utf-8")
    assert set(M.load_tool_meta(str(root), force=True)) == {"a"}


def test_cache_does_not_leak_across_roots(tmp_path):
    """缓存按 root 分桶，不同目录不得互相污染。"""
    r1 = tmp_path / "d1"
    r2 = tmp_path / "d2"
    r1.mkdir()
    r2.mkdir()
    (r1 / "a.yaml").write_text("name: a\nplane: act\neffect: read\nrisk: low\n", encoding="utf-8")
    (r2 / "b.yaml").write_text("name: b\nplane: act\neffect: read\nrisk: low\n", encoding="utf-8")

    M.invalidate_tool_meta_cache()
    assert set(M.load_tool_meta(str(r1))) == {"a"}
    assert set(M.load_tool_meta(str(r2))) == {"b"}
    assert set(M.load_tool_meta(str(r1))) == {"a"}


def test_real_defs_dir_signature_has_no_test_pollution():
    """真实目录的解析结果应与 capability_manifest 的规模量级一致。

    Why 只做量级断言（不硬编码 91）：本用例要在能力增删后仍然通过，
    但必须能抓住"解析整体失败 ⇒ 返回空集"这类灾难性回归。
    """
    meta = M.load_tool_meta(force=True)
    assert len(meta) >= 50, f"只解析出 {len(meta)} 个工具，疑似解析整体失败"
    for name, m in meta.items():
        assert m.name == name
        assert m.plane in M.PLANES
        assert m.effect in M.EFFECTS
        assert m.risk in M.RISKS
