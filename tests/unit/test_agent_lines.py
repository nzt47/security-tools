"""能力平面与主线装配 —— 守门测试

覆盖四件事：
  1. **数据完整性**：每个工具 YAML 都必须声明合法 plane/effect/risk
  2. **主线档案**：7 条内置主线全部通过校验，且引用真实工具
  3. **装配语义**：平面保底（防饥饿）、effect 上限、mute、fail-closed、名额上限
  4. **接线层**：未装线返回 None（回退旧路径）、装线返回工具集、internal 工具隐藏

【为什么这些测试重要】
    评估报告（docs/工具集评估与重分类报告.md）查出的头号缺陷是
    "`core(6)+web(9)+file(10)=25` 恰好等于 `max_tools=25` ⇒ 七个类别恒为 0 个工具"。
    平面保底就是针对它的结构修复，因此必须有断言锁住"任何启用平面都不会归零"。
"""
from __future__ import annotations

import glob
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent.lines import (  # noqa: E402
    EFFECTS,
    PLANES,
    RISKS,
    LineProfile,
    assemble,
    get_line_registry,
    load_tool_meta,
)
from agent.lines.integration import line_whitelist  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DEFS = os.path.join(_ROOT, "data", "tool_definitions")


# ════════════════════════════════════════════════════════════
#  1. 数据完整性
# ════════════════════════════════════════════════════════════

def test_every_tool_yaml_declares_plane_effect_risk():
    """每个工具 YAML 都必须有 plane/effect/risk，且取值合法

    这条锁住"治理声明不能缺"：缺声明的工具在装配器里会被 fail-closed 拒绝，
    等于静默失去能力，所以必须在数据层就拦住。
    """
    import yaml

    files = sorted(glob.glob(os.path.join(_DEFS, "*.yaml")))
    assert len(files) >= 83, f"工具定义数量异常偏少: {len(files)}"

    missing = []
    invalid = []
    for path in files:
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
        name = doc.get("name") or os.path.basename(path)
        for field, allowed in (("plane", PLANES), ("effect", EFFECTS), ("risk", RISKS)):
            value = doc.get(field)
            if value is None:
                missing.append((name, field))
            elif value not in allowed:
                invalid.append((name, field, value))

    assert not missing, f"以下工具缺少治理声明: {missing}"
    assert not invalid, f"以下工具的治理声明取值非法: {invalid}"


def test_load_tool_meta_covers_all_yamls():
    meta = load_tool_meta()
    files = {os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(_DEFS, "*.yaml"))}
    assert files == set(meta.keys()), f"YAML 与元数据不一致: {files ^ set(meta.keys())}"


def test_govern_plane_is_the_modification_surface():
    """治理平面必须恰好是"会改变云枢自身能力集"的那批工具"""
    meta = load_tool_meta()
    govern = {n for n, m in meta.items() if m.plane == "govern"}
    # 已知的自身能力改写工具
    # 注：install_tool 已于第 0 档合并进 ext_install(type="auto")（2026-09-17），
    # market_search 合并进 ext_discover —— 故此处不再列它们。
    expected_core = {"generate_tool", "ext_install", "connect_mcp"}
    assert expected_core <= govern, f"治理平面漏了: {expected_core - govern}"
    # 治理平面 = extend 效果（自洽性）
    for n in govern:
        assert meta[n].effect == "extend", f"{n} 在治理平面但 effect={meta[n].effect}"
        assert meta[n].needs_approval, f"{n} 在治理平面但未要求确认"


def test_internal_tools_declared():
    meta = load_tool_meta()
    internal = {n for n, m in meta.items() if m.internal}
    # process_distill_run 是内部执行体：不能注销（call() 要求名字在 _registry），
    # 只能标记 internal 从模型可见集隐藏。
    assert "process_distill_run" in internal, "内部执行体未标记 internal"


# ════════════════════════════════════════════════════════════
#  2. 主线档案
# ════════════════════════════════════════════════════════════

EXPECTED_LINES = {"digital_life", "harness", "engineering", "dev", "knowledge", "recon", "assistant"}


def test_builtin_lines_exist():
    ids = set(get_line_registry().list_ids())
    assert EXPECTED_LINES <= ids, f"缺少主线档案: {EXPECTED_LINES - ids}"


@pytest.mark.parametrize("line_id", sorted(EXPECTED_LINES))
def test_builtin_line_validates(line_id):
    profile = get_line_registry().load(line_id)
    assert profile is not None, f"主线不存在: {line_id}"
    issues = profile.validate(set(load_tool_meta().keys()))
    assert not issues, f"主线 {line_id} 校验失败: {issues}"


@pytest.mark.parametrize("line_id", sorted(EXPECTED_LINES))
def test_builtin_line_mutes_only_real_tools(line_id):
    """mute/boost 只能引用真实工具（typo 会静默失效）"""
    profile = get_line_registry().load(line_id)
    known = set(load_tool_meta().keys())
    unknown = (set(profile.boost) | set(profile.mute)) - known
    assert not unknown, f"{line_id} 引用了不存在的工具: {unknown}"


@pytest.mark.parametrize("line_id", sorted(EXPECTED_LINES))
def test_builtin_line_skills_are_real(line_id):
    """skills 只能引用真实存在的技能 id（与 boost/mute 同款口径）

    为什么必须与工具那条并列：技能侧的失效更隐蔽 —— 工具少了会在装配预览里少一个
    chip，技能少了只是系统提示词少一段。清单类字段的 typo 一律在数据层拦住。
    """
    from agent.lines import known_skill_ids

    profile = get_line_registry().load(line_id)
    known = set(known_skill_ids())
    assert known, "技能目录为空，本断言会假通过"
    unknown = [s for s in profile.skills if s not in known]
    assert not unknown, f"{line_id} 引用了不存在的技能: {unknown}"


def test_lifecycle_line_has_govern_enabled():
    """数字生命体这条线以"进化"为核心之一，必须能拿到治理平面工具"""
    profile = get_line_registry().load("digital_life")
    assert profile.allow_govern is True
    assert "extend" in profile.effect_allow, "allow_govern 未放行 extend（会静默失效）"


def test_pure_dev_lines_have_no_govern():
    """纯研发线不得拥有任何自身能力改写工具"""
    for line_id in ("dev", "engineering", "knowledge", "recon", "assistant"):
        profile = get_line_registry().load(line_id)
        assert profile.allow_govern is False, f"{line_id} 不应允许治理平面"
        assert "govern" not in profile.plane_weights, f"{line_id} 不应有 govern 权重"


# ════════════════════════════════════════════════════════════
#  3. 装配语义
# ════════════════════════════════════════════════════════════

def _all_tools() -> list:
    return sorted(load_tool_meta().keys())


def _profile(**kw) -> LineProfile:
    base = dict(
        id="t", name="t",
        plane_weights={"resident": 1.0, "perceive": 1.0, "act": 1.0},
        plane_floors={"resident": 2, "perceive": 3, "act": 3},
        max_tools=10,
    )
    base.update(kw)
    return LineProfile(**base)


def test_plane_floor_prevents_starvation():
    """**核心回归**：名额被高权重平面吃光时，保底平面仍须有工具

    这正是旧口径的缺陷：core+web+file 恰好 25 ⇒ code/system/... 全为 0。
    """
    profile = _profile(
        plane_weights={"resident": 10.0, "perceive": 10.0, "act": 0.01},
        plane_floors={"resident": 2, "perceive": 2, "act": 4},
        max_tools=12,
    )
    res = assemble(profile, _all_tools())
    assert len(res.by_plane["act"]) >= 4, (
        f"act 平面未获保底: {res.by_plane['act']}（饥饿缺陷回归）")


def test_resident_tools_always_survive_cap():
    profile = _profile(plane_floors={"resident": 6, "perceive": 0, "act": 0}, max_tools=8)
    res = assemble(profile, _all_tools())
    assert len(res.by_plane["resident"]) >= 6


def test_effect_ceiling_blocks_higher_effects():
    """effect_allow=[read] ⇒ 只读；write/execute/extend 全被拦"""
    meta = load_tool_meta()
    profile = _profile(effect_allow=["read"], plane_weights={"perceive": 1.0, "act": 1.0},
                       plane_floors={"perceive": 3, "act": 3}, max_tools=20)
    res = assemble(profile, _all_tools())
    for name in res.tools:
        assert meta[name].effect == "read", f"{name} 突破了 effect 上限"
    assert res.denied_by_effect, "应有工具被 effect 上限拦下"


def test_writes_blocked_when_only_read_allowed():
    meta = load_tool_meta()
    profile = _profile(effect_allow=["read"], plane_weights={"act": 1.0},
                       plane_floors={"act": 2}, max_tools=30)
    res = assemble(profile, _all_tools())
    assert "write_file" not in res.tools
    assert "edit" not in res.tools
    assert "shell_execute" not in res.tools


def test_mute_excludes_named_tools():
    profile = _profile(mute=["write_file", "edit"], max_tools=30)
    res = assemble(profile, _all_tools())
    assert "write_file" not in res.tools and "edit" not in res.tools
    assert set(res.muted) >= {"write_file", "edit"}


def test_unknown_tools_are_fail_closed():
    """无 plane/effect 声明的工具一律拒绝（fail-closed），不静默放行"""
    profile = _profile(max_tools=30)
    res = assemble(profile, _all_tools() + ["totally_unknown_tool"])
    assert "totally_unknown_tool" not in res.tools
    assert "totally_unknown_tool" in res.denied_unknown


def test_boost_ranks_tool_higher():
    profile = _profile(boost=["shell_execute"], max_tools=6)
    res = assemble(profile, _all_tools())
    assert "shell_execute" in res.tools
    assert res.reasons.get("shell_execute", "").startswith(("打分入选", "平面保底"))


def test_cap_is_respected_when_floors_fit():
    profile = _profile(plane_floors={"resident": 2, "perceive": 2, "act": 2}, max_tools=9)
    res = assemble(profile, _all_tools())
    assert len(res.tools) <= 9


def test_govern_plane_absent_unless_enabled():
    profile = _profile(plane_weights={"act": 1.0}, plane_floors={"act": 2}, max_tools=30)
    res = assemble(profile, _all_tools())
    assert "govern" not in res.by_plane
    for name in res.tools:
        assert load_tool_meta()[name].plane != "govern"


def test_needs_approval_reported():
    profile = _profile(boost=["shell_execute", "write_file"], plane_floors={"act": 5},
                       max_tools=30)
    res = assemble(profile, _all_tools())
    assert "shell_execute" in res.needs_approval


def test_assembly_is_deterministic():
    profile = _profile(max_tools=15)
    a = assemble(profile, _all_tools()).tools
    b = assemble(profile, _all_tools()).tools
    assert a == b


# ════════════════════════════════════════════════════════════
#  4. 接线层
# ════════════════════════════════════════════════════════════

def test_line_whitelist_returns_none_when_no_line_active(monkeypatch):
    """未装线必须返回 (None, None) ⇒ 编排器回退旧路径（向后兼容的关键）"""
    reg = get_line_registry()
    monkeypatch.setattr(reg, "get_active", lambda: None)
    monkeypatch.setattr("agent.lines.integration.get_line_registry", lambda: reg)
    tools, res = line_whitelist(None)
    assert tools is None and res is None


def test_line_whitelist_returns_tools_when_active(monkeypatch):
    reg = get_line_registry()
    monkeypatch.setattr(reg, "get_active", lambda: "engineering")
    monkeypatch.setattr("agent.lines.integration.get_line_registry", lambda: reg)
    # 显式给候选集：本测试进程没有注册任何工具，不能依赖注册表全量
    tools, res = line_whitelist(_all_tools())
    assert tools and res is not None
    assert res.line_id == "engineering"
    assert "read_file" in tools and "shell_execute" in tools


def test_line_whitelist_survives_broken_profile(monkeypatch):
    """档案损坏时必须回退，而不是抛异常打断对话"""
    reg = get_line_registry()

    def _boom(_line_id):
        from agent.lines import LineRegistryError
        raise LineRegistryError("坏的档案")

    monkeypatch.setattr(reg, "get_active", lambda: "engineering")
    monkeypatch.setattr(reg, "load", _boom)
    monkeypatch.setattr("agent.lines.integration.get_line_registry", lambda: reg)
    tools, res = line_whitelist(None)
    assert tools is None and res is None


def test_disabled_line_falls_back(monkeypatch):
    reg = get_line_registry()
    real_load = reg.load

    def _disabled(line_id):
        p = real_load(line_id)
        if p is not None:
            p.enabled = False
        return p

    monkeypatch.setattr(reg, "get_active", lambda: "engineering")
    monkeypatch.setattr(reg, "load", _disabled)
    monkeypatch.setattr("agent.lines.integration.get_line_registry", lambda: reg)
    tools, res = line_whitelist(None)
    assert tools is None and res is None


# ════════════════════════════════════════════════════════════
#  5. get_tool_defs 的 internal 隐藏
# ════════════════════════════════════════════════════════════

def test_internal_tools_hidden_from_model_but_kept_in_registry():
    from agent import tools as T

    T.clear()

    @T.register("visible_tool", "可见")
    def _v(**kw):
        return {"ok": True}

    @T.register("process_distill_run", "内部执行体")
    def _i(**kw):
        return {"ok": True}

    names = {d["function"]["name"] for d in T.get_tool_defs()}
    assert "visible_tool" in names
    assert "process_distill_run" not in names, "internal 工具泄漏到模型可见集"
    # 但仍在注册表里，call() 依旧可用
    assert "process_distill_run" in {t["name"] for t in T.list_tools()}
    T.clear()


# ════════════════════════════════════════════════════════════
#  6. 动态工具持久化（云枢自生成的新工具能否活过重启）
# ════════════════════════════════════════════════════════════

def test_dynamic_persistence_entrypoints_exist():
    """lifecycle_manager 调用的三个符号必须存在

    历史缺陷：`init_dynamic_tools_persistence` / `load_dynamic_tools` 在
    `agent/tools/__init__.py` 里根本不存在，每次启动 AttributeError 被吞成 warning，
    自生成的工具重启即失（评估报告 §4.4c）。
    """
    from agent import tools as T
    from agent import tool_router as tr

    assert callable(getattr(T, "init_dynamic_tools_persistence", None))
    assert callable(getattr(T, "load_dynamic_tools", None))
    assert callable(getattr(tr, "set_discovery_service", None))


def test_generated_tool_survives_reload_and_gets_governance(tmp_path, monkeypatch):
    """端到端：磁盘上的自生成工具模块 → 加载注册 → 自动补治理声明 → 可被装配

    这是"云枢自主生成新工具"这条链路的最小闭环证明。
    """
    from agent import tools as T
    from agent.tools import persistence as P

    custom_dir = tmp_path / "custom"
    custom_dir.mkdir()
    defs_dir = tmp_path / "defs"
    defs_dir.mkdir()
    monkeypatch.setattr(P, "CUSTOM_TOOLS_DIR", str(custom_dir))
    monkeypatch.setattr(P, "TOOL_DEFS_DIR", str(defs_dir))
    monkeypatch.setattr(P, "_index_path", str(tmp_path / "index.json"))

    (custom_dir / "gen_probe.py").write_text(
        'from agent import tools as _tools\n'
        'def gen_probe(**kw):\n'
        '    return {"ok": True}\n'
        'def register_all(dl=None):\n'
        '    _tools.register_dynamic("gen_probe_tool", "自生成探针",\n'
        '        handler=gen_probe, schema={"type": "object", "properties": {}},\n'
        '        source="generated", source_id="probe")\n',
        encoding="utf-8")

    T.clear()
    try:
        T.init_dynamic_tools_persistence(str(tmp_path / "index.json"))
        assert T.load_dynamic_tools() == 1
        assert "gen_probe_tool" in {t["name"] for t in T.list_tools()}
        # 治理声明被自动补写
        yml = defs_dir / "gen_probe_tool.yaml"
        assert yml.exists(), "自生成工具未获治理声明 ⇒ 装配器会 fail-closed 拒绝它"
        import yaml as _yaml
        doc = _yaml.safe_load(yml.read_text(encoding="utf-8"))
        assert doc["plane"] == "act"
        assert doc["effect"] == "execute"
        # 自生成工具执行 LLM 写的代码 ⇒ 必须落 critical ⇒ 必须审批
        assert doc["risk"] == "critical"
        # 台账记录
        assert "gen_probe_tool" in [e["name"] for e in P.list_persisted()]
    finally:
        T.unregister("gen_probe_tool")
        T.clear()
        P.reset_dynamic_tools_state()


def test_generated_tool_defaults_need_approval():
    """保守默认：自生成工具默认 risk=critical ⇒ needs_approval"""
    from agent.lines.models import ToolMeta
    m = ToolMeta(name="x", plane="act", effect="execute", risk="critical")
    assert m.needs_approval is True
    m2 = ToolMeta(name="y", plane="perceive", effect="read", risk="low")
    assert m2.needs_approval is False


# ════════════════════════════════════════════════════════════
#  7. internal 工具的"三处隐藏"必须齐全
# ════════════════════════════════════════════════════════════

def test_internal_tools_absent_from_all_three_surfaces():
    """`internal: true` 的工具必须在**三个面**上都不出现

    为什么是三处：模型拿到工具的途径有三条，只堵一条等于没堵——
      ① `get_tool_defs()`（直发全量 / 白名单两条路径）
      ② `data/tool_index.json`（hybrid 检索路由的候选来源，**不做分类过滤**）
      ③ `tool_router.TOOL_CATEGORIES`（关键词路由的分类表）

    这不是假想问题：`process_distill_run` 曾只被 ① 隐藏，而它已在 ② 里，
    hybrid 把它召回进 top-5 ⇒ 模型看得见、也能通过 tool_defs 调它，
    `get_tool_defs` 的隐藏被整条绕开。
    """
    import json
    from agent.lines import load_tool_meta
    from agent.tool_router import ALL_TOOLS_SET

    internal = {n for n, m in load_tool_meta().items() if m.internal}
    assert internal, "至少应有 process_distill_run 一个 internal 工具"

    # ③ 分类表
    leak = internal & ALL_TOOLS_SET
    assert not leak, f"internal 工具泄漏进路由分类表: {leak}"

    # ② 检索索引
    index_path = os.path.join(_ROOT, "data", "tool_index.json")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            idx = json.load(f)
        indexed = {t["name"] for t in idx.get("tools", [])}
        leak2 = internal & indexed
        assert not leak2, (
            f"internal 工具泄漏进 tool_index.json（hybrid 会召回它）: {leak2}；"
            "请检查 scripts/sync_tool_index.py 的 _build_index 是否过滤 internal")

    # ① get_tool_defs（注册表里要有、可见集里没有）
    from agent import tools as T
    T.clear()
    try:
        @T.register("visible_probe", "可见")
        def _v(**kw):
            return {"ok": True}

        @T.register("process_distill_run", "内部")
        def _i(**kw):
            return {"ok": True}

        visible = {d["function"]["name"] for d in T.get_tool_defs()}
        assert "visible_probe" in visible
        assert "process_distill_run" not in visible
        # 但注册表里必须在（AsyncExecutor 按名调用，call() 要求名字存在）
        assert "process_distill_run" in {t["name"] for t in T.list_tools()}
    finally:
        T.clear()
