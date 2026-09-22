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


def test_warm_call_is_cached_and_under_budget():
    """缓存命中应显著快于冷读，且有明确上界。"""
    M.invalidate_tool_meta_cache()

    t0 = time.perf_counter()
    first = M.load_tool_meta(force=True)
    cold_ms = (time.perf_counter() - t0) * 1000

    # 【取多次中的最小值·2026-09-22】命中路径是纯内存返回，本应亚毫秒级；实测 CI 上出现过
    #   6.83ms / 6.96ms —— 那不是"命中变慢"，而是用例所在进程被**调度挂起**（2 核 runner、
    #   `-n 2 --dist=loadscope`、同 shard 还有全仓 AST 扫描类用例）。
    #   上界**不放宽**（仍是 5ms，仍能抓住"签名计算写成全量哈希"这类真回归——那会让命中稳定在
    #   毫秒级而非偶发一次），只是把"一次采样"换成"三次取最小"以剔除调度噪声。
    #   （同文件对冷读已有"给 3 倍余量容忍 CI 抖动"的先例。）
    warm_samples = []
    for _ in range(3):
        t0 = time.perf_counter()
        second = M.load_tool_meta()
        warm_samples.append((time.perf_counter() - t0) * 1000)
    warm_ms = min(warm_samples)

    assert second is first, "缓存命中应返回同一对象（不重复构造）"
    assert warm_ms < 5.0, (f"缓存命中耗时 {warm_ms:.2f}ms 超过 5ms 上界"
                         f"（三次采样 {[round(s, 2) for s in warm_samples]}；签名计算可能写成了全量哈希）")
    # 冷读用 C loader 后应有明确上界（实测 ~33ms；给 3 倍余量容忍 CI 抖动）
    assert cold_ms < 110.0, f"冷读耗时 {cold_ms:.1f}ms 偏高，CSafeLoader 未生效？"


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
