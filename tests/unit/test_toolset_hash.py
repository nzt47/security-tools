"""`toolset_hash` 单元测试（TASK-08 E7 / v1.4 §11）

## 本文件锁死的三件事

1. **hash 范围逐项**：工具名 / 版本 / `input_schema` / `description` /
   `llm_visible` / 权限 / 模型能力 —— **每一项各一条用例**，改一项就必须变 hash。
2. **🔴 健康变化不触发重建**（E7 点名"最容易做错"的一条）：
   `health` 既不在 hash 范围内，也不得引起会话重建，只影响过滤/注入。
   本文件用**两路取证**：① `dataclasses.replace(health=…)` 后 hash 逐字不变；
   ② 健康探针在 healthy/unhealthy 之间抖动 10 轮，`rebuild_required` 恒 False
   且 `generation` 恒 1（**没有新建会话**）。
3. **hash 稳定**：同输入多次计算相同；与**记录顺序**、**字典键顺序**、
   **集合顺序**、**PYTHONHASHSEED**（跨进程）全部无关。

## 为什么这些用例值得写

hash 的两种错误方向代价不对称：
  · 该变不变 ⇒ 模型拿着**过期契约**调工具（静默错，最难查）；
  · 不该变却变 ⇒ 会话被无端清空（用户可见的体验事故）。
第 2 类正是"把 health 放进 hash"会造成的后果（health 一天能抖几十次），
故本文件对它单独设类。
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys

import pytest

from agent.capregistry.toolset_hash import (EXCLUDED_FIELDS, HASHED_FIELDS,
                                            SessionToolset, build_entries,
                                            canonical, compute_toolset_hash,
                                            describe_hash_scope, diff_entries)
from agent.capregistry.view import build_registry

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 受测目标：一个**真实**的低危只读工具（改它不会碰到治理语义）
TARGET = "read_file"


@pytest.fixture(scope="module")
def registry():
    """真实 Registry（主源构建 ⇒ 114 条能力；只读，模块级复用）"""
    reg = build_registry()
    assert not reg.degraded, f"主源构建失败（降级态不可用于本用例）: {reg.build_warnings}"
    assert len(reg) >= 114
    return reg


def _mutated(registry, name: str, **fields):
    """把某条能力替换成"只改指定字段"的副本，其余能力保持不变

    【为什么用 `dataclasses.replace`】`CapabilityRecord` 是 frozen 的 —— 这保证了
    "改一个字段"这条用例不会顺带改到别处（否则 hash 变化的原因就不唯一，
    用例变成"改了某个东西 hash 变了"，说明不了任何事）。
    """
    src = registry.get(name)
    assert src is not None, f"受测能力不存在: {name}"
    new = dataclasses.replace(src, **fields)
    return tuple(new if s.tool_name == name else s for s in registry.specs)


def _hash(specs, *, model: str = "") -> str:
    return compute_toolset_hash(specs, model=model)


# ════════════════════════════════════════════════════════════
#  一、hash 范围：纳入的七项，逐项一条用例
# ════════════════════════════════════════════════════════════


class TestHashScopeDeclaration:
    def test_纳入的七个维度逐项声明(self):
        assert HASHED_FIELDS == ("name", "version", "input_schema", "description",
                                 "llm_visible", "permission", "model_capability")

    def test_排除表把健康排在第一并写明理由(self):
        """排除表必须**显式**列出 health，且理由必须引用 v1.4 §11 的原文语义"""
        assert "health" in EXCLUDED_FIELDS
        first_key = next(iter(EXCLUDED_FIELDS))
        assert first_key == "health", "health 必须是排除表的第一条（它是本条验收点）"
        why = EXCLUDED_FIELDS["health"]
        assert "不触发重建" in why and "过滤注入" in why and "v1.4 §11" in why

    def test_可查询的范围说明与常量一致(self):
        scope = describe_hash_scope()
        assert scope["included"] == list(HASHED_FIELDS)
        assert scope["excluded"] == dict(EXCLUDED_FIELDS)
        assert "health" in scope["excluded_hard_rule"]


class TestEachDimensionChangesHash:
    """工具名 / 版本 / input_schema / description / llm_visible / 权限 / 模型能力"""

    def test_工具名变化_触发hash变化(self, registry):
        base = _hash(registry.specs)
        assert _hash(_mutated(registry, TARGET, tool_name="read_file_v2")) != base

    def test_版本变化_触发hash变化(self, registry):
        base = _hash(registry.specs)
        assert _hash(_mutated(registry, TARGET, version="9.9.9")) != base

    def test_input_schema变化_触发hash变化(self, registry):
        src = registry.get(TARGET)
        schema = dict(src.input_schema or {"type": "object", "properties": {}})
        props = dict(schema.get("properties") or {})
        props["brand_new_param"] = {"type": "string", "description": "新增参数"}
        schema["properties"] = props
        base = _hash(registry.specs)
        assert _hash(_mutated(registry, TARGET, input_schema=schema)) != base

    def test_description变化_触发hash变化(self, registry):
        base = _hash(registry.specs)
        assert _hash(_mutated(registry, TARGET,
                              description="（改过的描述）读取文件内容")) != base

    @pytest.mark.parametrize("field,value", [
        ("internal", True),            # 内部工具 ⇒ 不进模型可见集
        ("llm_callable", False),       # 声明不可被 LLM 调用
        ("callable_mode", "manual"),   # 仅人工/系统调用
        ("reachable", False),          # 装配判定为不可达
    ])
    def test_llm_visible变化_触发hash变化(self, registry, field, value):
        """`llm_visible` 的四条判据逐条：任一条翻转都必须变 hash

        【为什么不只测 `internal`】v1.4 §5.1 的 `llm_visible` 在本仓库的**实际**
        落点是 `agent/tools/__init__.py::_hidden_tool_names()`（internal ∪
        non_callable），四条判据缺一条就会漏掉一整类"模型突然看不见"的变化。
        """
        base = _hash(registry.specs)
        assert _hash(_mutated(registry, TARGET, **{field: value})) != base

    @pytest.mark.parametrize("field,value", [
        ("permission_level", "restricted"),
        ("needs_approval", True),
        ("risk", "critical"),
        ("risk", "high"),
    ])
    def test_权限变化_触发hash变化(self, registry, field, value):
        base = _hash(registry.specs)
        assert _hash(_mutated(registry, TARGET, **{field: value})) != base

    def test_模型能力变化_触发hash变化(self, registry):
        """支持 tool calling 与"没有工具通道"是两个世界 ⇒ 必须变 hash"""
        specs = registry.specs
        assert _hash(specs, model="deepseek-chat") != _hash(specs, model="text-davinci-003")
        # 同一模型族内换版本也变（能力判定结果里含 model 名，如实披露）
        assert _hash(specs, model="deepseek-chat") != _hash(specs, model="deepseek-reasoner")

    def test_未指定模型与显式支持模型也可区分(self, registry):
        """`model=""`（不裁剪）与显式指定模型是两种上下文"""
        assert _hash(registry.specs, model="") != _hash(registry.specs, model="gpt-4o")


# ════════════════════════════════════════════════════════════
#  二、🔴 健康变化不触发重建（E7 的核心验收点）
# ════════════════════════════════════════════════════════════


class TestHealthNeverRebuilds:
    """v1.4 §11：**健康频繁变化不触发重建，仅过滤注入**"""

    def test_健康字段变化不改变hash(self, registry):
        """第一路取证：字段层。改 `health` ⇒ hash **逐字不变**"""
        base = _hash(registry.specs)
        for state in ("healthy", "unhealthy", "down", "open", "failed", "unknown", ""):
            assert _hash(_mutated(registry, TARGET, health=state)) == base, state

    def test_全部能力的健康字段翻转_仍不改变hash(self, registry):
        """把 114 条的 health 整体改成 unhealthy ⇒ hash 仍不变"""
        all_bad = tuple(dataclasses.replace(s, health="unhealthy")
                        for s in registry.specs)
        assert _hash(all_bad) == _hash(registry.specs)

    def test_健康变化不触发重建_仅过滤注入(self, registry):
        """第二路取证：会话层。探针从 healthy 翻到 unhealthy ⇒ **不重建**

        同时断言它**确实起了作用**（`health_changed` / `filtered_out` 非空）——
        否则"不重建"可能只是"压根没观测到健康"，那是假绿。
        """
        holder = {TARGET: "healthy"}
        st = SessionToolset(session_id="s-health")
        d1 = st.observe(registry, health_provider=lambda n: holder.get(n, "healthy"))
        assert d1.rebuild_required is False
        h1, gen1 = st.toolset_hash, st.generation

        holder[TARGET] = "unhealthy"
        d2 = st.observe(registry, health_provider=lambda n: holder.get(n, "healthy"))
        assert d2.rebuild_required is False, "健康变化触发了重建（违反 v1.4 §11）"
        # 裁决结构里必须**显式**披露"健康不触发重建"（调用方据此安抚/提示用户）
        assert d2.to_dict()["health_triggers_rebuild"] is False
        assert st.toolset_hash == h1, "健康变化改变了 toolset_hash（health 不该进 hash）"
        assert st.generation == gen1, "健康变化换掉了会话代次（不该换）"
        assert TARGET in d2.health_changed, "健康变化没被观测到（该用例会变成假绿）"
        assert TARGET in d2.filtered_out, "不健康的能力没有进入过滤集"

    def test_健康抖动十轮_零重建且代次不变(self, registry):
        """真实故障形态：上游间歇 5xx ⇒ health 来回抖。**一次都不许重建**"""
        holder = {"write_file": "healthy"}
        st = SessionToolset(session_id="s-flap")
        st.observe(registry, health_provider=lambda n: holder.get(n, "healthy"))
        h0, gen0 = st.toolset_hash, st.generation
        observed_changes = 0
        for i in range(10):
            holder["write_file"] = "unhealthy" if i % 2 == 0 else "healthy"
            d = st.observe(registry, health_provider=lambda n: holder.get(n, "healthy"))
            assert d.rebuild_required is False, f"第 {i} 轮抖动触发了重建"
            observed_changes += len(d.health_changed)
        assert observed_changes >= 8, "抖动没有被观测到（用例假绿）"
        assert st.toolset_hash == h0 and st.generation == gen0

    def test_健康与契约同时变化_以契约为准触发重建(self, registry):
        """反向对照：只有**契约面**变化才重建（证明上面几条不是"永远不重建"）"""
        st = SessionToolset(session_id="s-both")
        st.observe(registry)
        d = st.observe(_mutated(registry, TARGET, description="改了描述"))
        assert d.rebuild_required is True
        assert "description" in d.changed_fields
        assert st.generation == 2


# ════════════════════════════════════════════════════════════
#  三、hash 稳定性（与顺序无关）
# ════════════════════════════════════════════════════════════


class TestHashStability:
    def test_相同输入多次计算结果相同(self, registry):
        hs = {_hash(registry.specs) for _ in range(5)}
        assert len(hs) == 1

    def test_记录顺序无关(self, registry):
        specs = list(registry.specs)
        base = _hash(specs)
        assert _hash(list(reversed(specs))) == base
        shuffled = specs[37:] + specs[:37]
        assert _hash(shuffled) == base

    def test_字典键顺序无关(self, registry):
        """同一份 schema 只要键序不同就 hash 不同 ⇒ 会造出**假重建**"""
        src = registry.get(TARGET)
        props = dict((src.input_schema or {}).get("properties") or {})
        if len(props) < 2:
            props = {"a": {"type": "string"}, "b": {"type": "integer"}}
        forward = {"type": "object", "properties": props}
        backward = {"properties": dict(reversed(list(props.items()))), "type": "object"}
        assert _hash(_mutated(registry, TARGET, input_schema=forward)) == \
            _hash(_mutated(registry, TARGET, input_schema=backward))

    def test_集合顺序无关(self):
        """`canonical()` 对 set/frozenset 必须排序（否则跨进程/跨构造顺序都会漂）"""
        assert canonical({"b", "a", "c"}) == canonical({"c", "b", "a"})
        assert canonical(frozenset({"z", "y"})) == ["y", "z"]

    def test_数组顺序保留(self):
        """数组（`required` / `enum`）的顺序是 JSON Schema 语义，**不得**排序"""
        assert canonical(["b", "a"]) == ["b", "a"]
        assert canonical(["b", "a"]) != canonical(["a", "b"])

    def test_跨进程_PYTHONHASHSEED_无关(self):
        """在**两个不同哈希种子**的子进程里算同一个 hash ⇒ 必须一致

        【为什么必须用子进程】set/dict 的迭代顺序受 `PYTHONHASHSEED` 影响，而它是
        **每进程**随机的。同进程内"跑两次一样"证明不了跨进程一致 —— 而重建判定
        恰恰要在进程重启后仍然可比。
        """
        code = (
            "import sys; sys.path.insert(0, r'%s');"
            "from agent.capregistry.view import build_registry;"
            "from agent.capregistry.toolset_hash import compute_toolset_hash;"
            "print(compute_toolset_hash(build_registry().specs))" % REPO_ROOT
        )
        outs = []
        for seed in ("1", "2"):
            env = dict(os.environ)
            env["PYTHONHASHSEED"] = seed
            env.setdefault("PYTHONUTF8", "1")
            env.setdefault("PYTHONIOENCODING", "utf-8")
            r = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, env=env,
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=180)
            assert r.returncode == 0, f"seed={seed} 子进程失败: {r.stderr[-800:]}"
            outs.append(r.stdout.strip().splitlines()[-1])
        assert outs[0] == outs[1], f"跨 PYTHONHASHSEED 不一致: {outs}"


# ════════════════════════════════════════════════════════════
#  四、会话重建语义（hash 变 ⇒ 提示重建）
# ════════════════════════════════════════════════════════════


class TestSessionRebuild:
    def test_首次观测建立基线_不重建(self, registry):
        st = SessionToolset(session_id="s1")
        d = st.observe(registry)
        assert d.rebuild_required is False
        assert "基线" in d.reason
        assert st.generation == 1
        assert st.session_id == "s1"

    def test_hash变化_自动新建会话并提示(self, registry):
        st = SessionToolset(session_id="s1")
        d0 = st.observe(registry)
        d1 = st.observe(_mutated(registry, TARGET, version="2.0.0"))
        assert d1.rebuild_required is True
        assert d1.previous_hash == d0.toolset_hash
        assert d1.toolset_hash != d0.toolset_hash
        assert "version" in d1.changed_fields
        assert TARGET in d1.changed_tools
        # 提示文案：必须明说"已新建会话"且给出新会话键
        assert "新建会话" in d1.message and st.session_id in d1.message
        assert st.generation == 2 and st.session_id == "s1#g2"

    def test_重建后以新基线继续比较(self, registry):
        """换基是**自动**的：下一次同契约观测不得再报重建（否则会无限重建）"""
        st = SessionToolset(session_id="s1")
        st.observe(registry)
        altered = _mutated(registry, TARGET, description="第一次改")
        assert st.observe(altered).rebuild_required is True
        assert st.observe(altered).rebuild_required is False, "换基失败 ⇒ 会反复重建"
        # 缓存键含源对象身份 ⇒ 换新对象会重算，但 hash 相同 ⇒ 仍不重建
        assert st.observe(tuple(altered)).rebuild_required is False

    def test_模型切换触发重建(self, registry):
        st = SessionToolset(session_id="s1", model="deepseek-chat")
        st.observe(registry)
        d = st.observe(registry, model="text-davinci-003")
        assert d.rebuild_required is True
        assert "model_capability" in d.changed_fields

    def test_同会话内缓存_同一源重复观测不重算契约(self, registry):
        st = SessionToolset(session_id="s1")
        st.observe(registry)
        first = st.snapshot
        st.observe(registry)
        second = st.snapshot
        assert second.entries is first.entries, "同一源未被缓存（每次都在重算）"

    def test_缓存按模型区分(self, registry):
        st = SessionToolset(session_id="s1")
        st.observe(registry, model="deepseek-chat")
        a = st.snapshot.toolset_hash
        st.observe(registry, model="text-davinci-003")
        b = st.snapshot.toolset_hash
        assert a != b

    def test_无变化时裁决文案如实(self, registry):
        st = SessionToolset(session_id="s1")
        st.observe(registry)
        d = st.observe(registry)
        assert d.rebuild_required is False
        assert d.reason == "无变化" and d.health_changed == ()


# ════════════════════════════════════════════════════════════
#  五、辅助：diff 与规范化
# ════════════════════════════════════════════════════════════


class TestDiffAndCanonical:
    def test_diff_逐字段列出变化(self, registry):
        a = build_entries(registry.specs)
        b = build_entries(_mutated(registry, TARGET, version="2.0.0"))
        diff = diff_entries(a, b)
        assert any(d["name"] == TARGET and d["field"] == "version" for d in diff)
        allowed = ("name", "tenant_id", "version", "input_schema", "description",
                   "llm_visible", "permission")
        assert all(d["field"] in allowed or d["field"].startswith("__")
                   for d in diff), "diff 里出现了不该参与 hash 的字段"

    def test_diff_增删可见(self, registry):
        a = build_entries(registry.specs)
        b = build_entries([s for s in registry.specs if s.tool_name != TARGET])
        fields = {d["field"] for d in diff_entries(a, b) if d["name"] == TARGET}
        assert fields == {"__removed__"}

    def test_canonical_非json类型收敛为字符串(self):
        class Weird:
            def __str__(self):
                return "weird"
        assert canonical({"x": Weird()}) == {"x": "weird"}
