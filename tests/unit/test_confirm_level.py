"""工具侧**确认分级 L0–L3**（TASK-06 / v1.4 §10.2）单元测试

## 这个文件为什么必须存在

它是 TASK-06 的**验收主体**：`agent/tool_gate.py:395` 与
`agent/lines/models.py:150` 的注释都**引用本文件名**作为"语义被锁定在这里"的证据。
（上一个子代理写了引用但没写文件 —— 那会让注释里的承诺无法核实，属 D8 的缺口。）

## 覆盖的评估标准（逐条对应 TASK-06 §5）

| 用例类 | 对应 |
|---|---|
| `TestDerivation` | 派生规则表本身（顺序敏感、14 组边界） |
| `TestThirteenHighTools` | **E1**：13 个 `risk: high` 工具逐个进确认流 |
| `TestL0StaysFree` | **E2**：L0 确实免确认（分级**不是**一刀切） |
| `TestConsistencyWithPermissionLevel` | **E8**：全量 114 条的派生一致性对拍 |
| `TestTwoSwitchesAreNested` | 两个开关的**从属**关系（总开关 ⊃ 分级开关） |
| `TestFailOpenDoesNotWeakenConfirmLevel` | 任务 B 裁决的锁定（策略文件缺失 ≠ 免确认） |
| `TestNonInteractiveNeverDangles` | **E5**：非交互不挂空单 |
| `TestIdentityIsRequired` | §1 完成判据：**无身份**调用被拒绝（而非放行） |
| `TestServiceAccountPreauthorization` | **交付物 #8**：SA 凭 scope 通过 L2 + 审计可区分 |
| `TestAuditIsDistinguishable` | **E9**：三种身份在审计里可区分 |
| `TestRollbackAndShadow` | §6：回滚开关 + 影子模式 |
| `TestNoYamlWritesNeeded` | 任务 C 裁决：派生 vs 显式声明（91 个 YAML 零改动） |

## 纪律

* 不 monkeypatch ``POLICY_POLICIES_PATH`` / ``DESCRIPTORS_PATH``：分级不读它们，
  本文件要证明的正是这一点（对照：`test_tool_gate.py` 用临时假文件测那两条规则）。
* 凡是改环境变量的用例一律 `monkeypatch`（自动还原），不写任何真实数据文件。
* 审批库路径由 `tests/conftest.py` 隔离到临时目录（会话级），本文件不重复设置。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import agent.tool_gate as G
from agent.lines.callability import (effective_permission_level,
                                     needs_approval_for)
from agent.lines.models import (CONFIRM_LEVELS, derive_confirm_level)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST = _PROJECT_ROOT / "data" / "capability_manifest.json"

#: TASK-06 §2 实测的 13 个 `risk: high` 工具（**名单必须逐字来自 YAML，不手抄**）
EXPECTED_HIGH: tuple = (
    "apply_patch", "connect_mcp", "decompress", "edit", "ext_install",
    "ext_send_channel", "ext_uninstall", "fan_out", "git", "run_program",
    "schedule_task", "workspace_delete", "write_file",
)

#: 13 个里同时是 `plane: govern` / `effect: extend` 的 3 个 ⇒ 按 **L3** 而非 L2 处置
#: （`derive_confirm_level` 的顺序敏感：从严者先判）
HIGH_BUT_L3 = ("connect_mcp", "ext_install", "ext_uninstall")


@pytest.fixture
def enforce_on(monkeypatch):
    """审批边界总开关 = 1（分级层生效的**显式**前提，不依赖默认值）"""
    monkeypatch.delenv(G.GATE_ENABLED_ENV, raising=False)
    monkeypatch.setenv(G.APPROVAL_ENFORCE_ENV, "1")
    monkeypatch.delenv(G.CONFIRM_LEVEL_ENFORCE_ENV, raising=False)
    monkeypatch.delenv(G.CONFIRM_LEVEL_SHADOW_ENV, raising=False)
    G._reset_cache()
    yield
    G._reset_cache()


def _manifest_entries() -> list:
    return list(json.loads(_MANIFEST.read_text(encoding="utf-8"))["entries"])


# ════════════════════════════════════════════════════════════
#  一、派生规则本身
# ════════════════════════════════════════════════════════════


class TestDerivation:
    """`derive_confirm_level` 的规则表（**顺序敏感**：从严者先判）"""

    @pytest.mark.parametrize("plane,effect,risk,want", [
        # ① L3：critical / extend / govern（三者任一）
        ("govern", "read", "low", "L3"),
        ("act", "extend", "low", "L3"),
        ("act", "write", "critical", "L3"),
        ("act", "execute", "critical", "L3"),
        # ② L2：risk high（**本任务要修的核心缺陷**）
        ("act", "write", "high", "L2"),
        ("act", "execute", "high", "L2"),
        # ③ L1：write / execute / medium
        ("act", "write", "low", "L1"),
        ("act", "write", "medium", "L1"),
        ("act", "execute", "low", "L1"),
        ("perceive", "read", "medium", "L1"),
        # ④ L0：read + low
        ("perceive", "read", "low", "L0"),
        ("resident", "read", "low", "L0"),
    ])
    def test_规则表(self, plane, effect, risk, want):
        assert derive_confirm_level(plane, effect, risk) == want

    def test_顺序敏感_critical_govern_优先于_high(self):
        """`shell_execute` 是 `critical`；若先判 risk 会落 L2（"点一下就行"）

        L3 的语义是"默认禁止（须显式预授权）"，与"逐次确认"不是一回事 ⇒ 顺序不能换。
        """
        assert derive_confirm_level("act", "execute", "critical") == "L3"
        # 同时 govern + high（`connect_mcp` 的实际组合）⇒ 必须取更严的 L3
        assert derive_confirm_level("govern", "extend", "high") == "L3"

    def test_execute_low_不是_L0(self):
        """TASK-06 原表没列的组合：`effect: execute` + `risk: low`（实测 2 个工具）

        若归入 L0 就等于"免确认地执行程序"，与 `effective_permission_level` 给它们
        `internal`（而非 public）的口径直接冲突 ⇒ 本实现把 execute 与 write 同等对待。
        """
        assert derive_confirm_level("act", "execute", "low") == "L1"
        assert effective_permission_level(
            "execute", "low", needs_approval_for("act", "execute", "low"), False
        ) == "internal"

    def test_大小写与空白容错(self):
        assert derive_confirm_level("ACT ", " Write", "HIGH") == "L2"
        assert derive_confirm_level("", "", "") == "L0"

    def test_值域与语义文案齐全(self):
        assert CONFIRM_LEVELS == ("L0", "L1", "L2", "L3")
        from agent.lines.models import CONFIRM_LEVEL_SEMANTICS
        assert set(CONFIRM_LEVEL_SEMANTICS) == set(CONFIRM_LEVELS)


# ════════════════════════════════════════════════════════════
#  二、E1：13 个 high 工具逐个进入确认流
# ════════════════════════════════════════════════════════════


class TestThirteenHighTools:
    """E1：TASK-06 §1 的缺陷 1（"13 个 high 完全不触发人工确认"）已修"""

    def test_名单与_YAML_一致(self):
        """名单不手抄：直接从能力 YAML 读出来的 high 集合必须**恰好**是这 13 个

        为什么必须"恰好"：少一个 ⇒ 缺陷仍在；多一个 ⇒ 有工具被误升级（会打挂体验）。
        """
        from agent.lines import load_tool_meta
        metas = load_tool_meta()
        high = sorted(k for k, v in metas.items()
                      if str(v.risk).strip().lower() == "high")
        assert tuple(high) == tuple(sorted(EXPECTED_HIGH)), (
            "risk: high 工具名单已变 ⇒ 必须同步复核 TASK-06 的分级结论与文档")

    @pytest.mark.parametrize("tool", EXPECTED_HIGH)
    def test_每个_high_工具都进确认流(self, tool, enforce_on):
        """逐个断言 `APPROVAL_REQUIRED`（E1 的硬要求：不许只测一个代表）

        【为什么理由串允许两种】`connect_mcp` / `ext_install` / `ext_uninstall`
        在**真实** `data/descriptors.json` 里本来就有 `trust.requires_approval=true`，
        而描述符那一步（第 2 步）排在分级层（第 3 步）**之前** ⇒ 它们的拒绝理由是
        描述符口径。这不影响 E1（"进入确认流"已成立），故本用例只对
        **确认级别**做独立断言（下一个用例逐个查 `effective_confirm_level`），
        对理由串接受两种来源。
        """
        result = G.check_tool_call(tool, {})
        assert result is not None and result["blocked"] is True, f"{tool} 未进确认流"
        assert result["error_code"] == "APPROVAL_REQUIRED", result
        assert ("confirm_level=L2" in result["reason"]
                or "confirm_level=L3" in result["reason"]
                or "requires_approval" in result["reason"]), result["reason"]

    @pytest.mark.parametrize("tool", EXPECTED_HIGH)
    def test_每个_high_工具的生效级别是_L2_或_L3(self, tool):
        from agent.lines import load_tool_meta
        meta = load_tool_meta()[tool]
        assert meta.effective_confirm_level in ("L2", "L3"), tool
        assert meta.needs_approval is True, tool

    @pytest.mark.parametrize("tool", HIGH_BUT_L3)
    def test_govern_的那_3_个抬到_L3(self, tool):
        """13 个一个都没漏，但其中 3 个被**抬到更严**的 L3（不是漏判，是升级）"""
        from agent.lines import load_tool_meta
        assert load_tool_meta()[tool].effective_confirm_level == "L3"

    def test_其余_10_个是_L2(self):
        from agent.lines import load_tool_meta
        metas = load_tool_meta()
        l2 = sorted(k for k in EXPECTED_HIGH if k not in HIGH_BUT_L3)
        assert len(l2) == 10
        for name in l2:
            assert metas[name].effective_confirm_level == "L2", name


# ════════════════════════════════════════════════════════════
#  三、E2：L0 仍免确认（分级不是一刀切）
# ════════════════════════════════════════════════════════════


class TestL0StaysFree:
    """E2：**若把所有工具都变成 L2 即视为不通过**（TASK-06 §5 的"不通过"第 1 条）"""

    def test_全量分布不是一刀切(self):
        from agent.lines import load_tool_meta
        metas = load_tool_meta()
        dist = {}
        for m in metas.values():
            dist[m.effective_confirm_level] = dist.get(m.effective_confirm_level, 0) + 1
        assert set(dist) == set(CONFIRM_LEVELS), f"四级必须**都在用**：{dist}"
        assert dist["L0"] >= 30, f"L0（免确认）太少，说明分级退化成一刀切：{dist}"
        assert dist["L1"] >= 30, f"L1（可批量摘要确认）太少：{dist}"
        # 只有 13 个 high 进 L2/L3，其余不该被牵连
        assert dist["L2"] + dist["L3"] == 20, dist      # 10 个 L2 + 10 个 L3

    @pytest.mark.parametrize("tool", ["read_file", "list_dir", "grep",
                                      "get_weather", "web_search"])
    def test_只读低危工具免确认(self, tool, enforce_on):
        assert G.check_tool_call(tool, {"path": "x", "pattern": "a"}) is None, tool

    def test_L1_工具也要进确认流(self, enforce_on):
        """L1 是"摘要确认、可批量" —— 也是**要确认**的（若不需要就不是 L1）"""
        from agent.lines import load_tool_meta
        l1 = [k for k, v in load_tool_meta().items()
              if v.effective_confirm_level == "L1" and v.effect == "write"]
        assert l1, "没有 L1 工具 ⇒ 分级表失效"
        result = G.check_tool_call(sorted(l1)[0], {})
        assert result is not None and result["blocked"] is True
        assert "confirm_level=L1" in result["reason"], result["reason"]


# ════════════════════════════════════════════════════════════
#  四、E8：与 permission_level 的派生一致性（全量 114 条）
# ════════════════════════════════════════════════════════════


class TestConsistencyWithPermissionLevel:
    """E8：`confirm_level` 与 `permission_level` **不允许两套并行口径**（D1）

    等价关系（`models.derive_confirm_level` 的 docstring 有证明）：

        `confirm_level == "L0"`  ⟺  `permission_level == "public"`

    唯一允许的例外是"被权限策略显式拒绝"（`denied=True` ⇒ permission_level 降到
    `restricted`，而 confirm_level 反映"本来该几级确认"）。该例外**逐条列举理由**，
    不静默放过。
    """

    def test_清单是入库的派生物(self):
        assert _MANIFEST.exists(), "缺少 data/capability_manifest.json（先跑 sync 脚本）"

    def test_工具侧_91_条全量对拍(self):
        """E8 的**适用范围是工具侧 91 条** —— 技能侧不适用，理由见下一个用例

        `derive_confirm_level` 是**工具侧** L0–L3 的派生（TASK-06 §3 第 2 步）。
        """
        tools = [e for e in _manifest_entries() if e.get("kind") == "tool"]
        assert len(tools) == 91, len(tools)

        mismatch = []
        for e in tools:
            plane = str(e.get("plane") or "")
            effect = str(e.get("effect") or "")
            risk = str(e.get("risk") or "")
            cl = derive_confirm_level(plane, effect, risk)
            na = needs_approval_for(plane, effect, risk)
            if str(e.get("confirm_level") or "") != cl:
                mismatch.append((e.get("tool_name"), "清单 confirm_level",
                                 e.get("confirm_level"), cl))
            base = effective_permission_level(effect, risk, na, False)
            if (cl == "L0") != (base == "public"):
                mismatch.append((e.get("tool_name"), "L0 ⟺ public", cl, base))
            # 工具侧的 permission_level 直接取自同一次派生（不得出现第三份口径）
            got = str(e.get("permission_level") or "")
            if got != base:
                mismatch.append((e.get("tool_name"), "permission_level", got, base))

        assert mismatch == [], f"派生口径矛盾（D1 违规）：{mismatch[:5]}"

    def test_技能侧_23_条_不适用该等价关系_逐条有理由(self):
        """E8 的"矛盾项逐条有理由"：23 条技能**全部**是例外，且理由是同一条事实

        实测（本用例就是证据）：技能侧的 `permission_level` **不是**由
        `effect/risk` 派生的，而是
        `declared.permission_level or ("restricted" if is_sensitive/未启用 else "public")`
        —— 见 `agent/lines/callability.py::_skill_entry`。三条事实支撑"不适用"：

        1. 技能**不是模型发起的工具调用**（`llm_callable=false`、`trigger=system`）⇒
           "一次工具调用要不要人点确认"这个概念在技能侧没有对应物；
        2. 技能在**策略声明**（`data/skill_callability.yaml`，D1 认定的技能侧权威）里
           直接声明等级，不是派生 ⇒ 派生口径本就不该覆盖它；
        3. 清单里的技能 `effect/risk` 是**由事实合成**的
           （`effect = read if 无脚本 else execute`、`risk = medium if sensitive else low`），
           把它们喂进 `derive_confirm_level` 会把"技能带脚本"误变成"L1 需确认"，
           而技能走的是**显式调用 + 注入**两条路，与工具闸门无关。

        故断言：技能条目**不携带** `confirm_level` 字段（工具侧概念不外溢），
        且差异条目可被逐条枚举出来（只有 `scripted-selftest` 一个 body 层面不同）。
        """
        skills = [e for e in _manifest_entries() if e.get("kind") == "skill"]
        assert len(skills) == 23, len(skills)
        for e in skills:
            assert "confirm_level" not in e, (
                f"技能 {e.get('tool_name')} 不该有工具侧的 confirm_level 字段")

        diff = []
        for e in skills:
            na = needs_approval_for(e.get("plane", ""), e.get("effect", ""),
                                    e.get("risk", ""))
            base = effective_permission_level(e.get("effect", ""), e.get("risk", ""),
                                              na, False)
            got = str(e.get("permission_level") or "")
            diff.append((e.get("tool_name"), got, base))
        # 技能侧 permission_level 恒为 public/restricted（声明或"未启用"规则），
        # 而工具侧派生会给出 internal —— 差异因此是**规则不同**，不是数据漂移。
        for name, got, base in diff:
            assert got in ("public", "restricted"), (name, got)
        assert all(got == "public" for _n, got, _b in diff), \
            "本部署 23 条技能全部启用且不敏感 ⇒ 全是 public（与声明一致）"
        # 至少有一条在"工具侧口径"下会得出 internal ⇒ 证明两组口径确实不同
        assert any(base == "internal" for _n, _got, base in diff), \
            "若两组口径完全一致，本例外说明就站不住，需重新核验"

    def test_所有_L0_条目的_effect_risk_组合合法(self):
        entries = _manifest_entries()
        for e in entries:
            if derive_confirm_level(e.get("plane", ""), e.get("effect", ""),
                                    e.get("risk", "")) == "L0":
                assert str(e.get("effect")) == "read" and str(e.get("risk")) == "low", \
                    f"{e.get('tool_name')} 落 L0 但 effect/risk 不是 read+low"


# ════════════════════════════════════════════════════════════
#  五、两个开关的从属关系（总开关 ⊃ 分级开关）
# ════════════════════════════════════════════════════════════


class TestTwoSwitchesAreNested:
    """`CP_TOOL_GATE_APPROVAL_ENFORCE`（总）与 `CP_TOOL_CONFIRM_LEVEL_ENFORCE`（窄）

    【为什么必须有这一组】原实现让分级层**不受**总开关约束 ⇒
    ① 既有回滚开关（`test_tool_approval_e2e.py:229` 钉着它）对新层无效，
       操作员以为回滚了实际没有；② `tests/conftest.py:207` 的会话基线（总开关=0）
       被穿透，30+ 条与本任务无关的既有测试被打红。这是**真实缺陷**，已在
       `agent/tool_gate.py::_confirm_level_outcome` 修复，本类把它锁死。
    """

    def test_总开关关闭_分级层也不拦(self, monkeypatch, enforce_on):
        monkeypatch.setenv(G.APPROVAL_ENFORCE_ENV, "0")
        assert G.check_tool_call("write_file", {}) is None
        assert G.check_tool_call("shell_execute", {}) is None

    def test_总开关关闭_分级开关无关(self, monkeypatch, enforce_on):
        monkeypatch.setenv(G.APPROVAL_ENFORCE_ENV, "0")
        monkeypatch.setenv(G.CONFIRM_LEVEL_ENFORCE_ENV, "1")
        assert G.check_tool_call("write_file", {}) is None, \
            "总开关是「要不要拦」的总闸，从属关系不得倒挂"

    def test_分级开关关闭_只回滚新加的那部分(self, monkeypatch, enforce_on):
        """总开关开 + 分级开关关 ⇒ 回到改动前的二值口径

        · `write_file`（新加的 high→L2）⇒ **不再**被分级层拦；
        · `shell_execute`（原本就是 critical）⇒ **仍然**被拦（旧的 needs_approval 路径）。
        """
        monkeypatch.setenv(G.CONFIRM_LEVEL_ENFORCE_ENV, "0")
        assert G.check_tool_call("write_file", {}) is None
        blocked = G.check_tool_call("shell_execute", {})
        assert blocked is not None and blocked["blocked"] is True, \
            "回滚分级层不得把**旧的** needs_approval 边界一起关掉"

    def test_分级开关默认开启(self, monkeypatch, enforce_on):
        assert G._confirm_level_enforce_enabled() is True
        for value in ("0", "false", "no", "off"):
            monkeypatch.setenv(G.CONFIRM_LEVEL_ENFORCE_ENV, value)
            assert G._confirm_level_enforce_enabled() is False, value
        monkeypatch.setenv(G.CONFIRM_LEVEL_ENFORCE_ENV, "   ")
        assert G._confirm_level_enforce_enabled() is True, "空串视同未设置（不得当关闭）"

    def test_总开关默认开启(self, monkeypatch, enforce_on):
        monkeypatch.delenv(G.APPROVAL_ENFORCE_ENV, raising=False)
        assert G._approval_enforce_enabled() is True


# ════════════════════════════════════════════════════════════
#  六、任务 B 的裁决：fail-open 不削弱 confirm_level
# ════════════════════════════════════════════════════════════


class TestFailOpenDoesNotWeakenConfirmLevel:
    """裁决：`permission_policies.json` / `descriptors.json` 缺失 **不影响** confirm_level

    ## 裁决与理由（`agent/tool_gate.py::_confirm_level_outcome` 有同一份说明）

    | 判据 | 来源 | 文件缺失时 |
    |---|---|---|
    | `denied_tools` / `trust.requires_approval` | **运行期策略文件** | 读不到依据 ⇒ **放行**（fail-open） |
    | `confirm_level` | **设计期能力 YAML**（D1 的单一真相源） | **仍然生效** |

    理由三条：
    1. **依据来源不同**："依据读不到" ≠ "策略不存在"。前者是不确定性，后者是确定性事实。
       YAML 里的 `risk: high` 是**已声明**的，不因另一个文件的缺失而变。
    2. **否则就是一条无声旁路**：`data/` 可写 ⇒ "删掉 permission_policies.json"
       会成为"13 个高危工具免确认"的开关。回滚必须是一个**显式、可审计**的动作
       （把总开关/分级开关写成 0），而不是一次文件丢失。
    3. **与治理动作 fail-closed 同向**：闸门自身异常时治理动作已改为拒�绝
       （TASK-06 §1 缺陷 4）；若"文件缺失"反而不拦，两条判据的方向就自相矛盾。

    注意与**总开关**的区别：总开关=`0` 时分级层**确实**不拦 —— 那是操作员
    **显式**写的回滚（见 `TestTwoSwitchesAreNested`），与"文件不见了"不同。
    """

    L2_TOOL = "write_file"

    def test_策略文件缺失_L2_仍拦(self, enforce_on, tmp_path, monkeypatch):
        monkeypatch.setattr(G, "POLICY_POLICIES_PATH", str(tmp_path / "missing.json"))
        G._reset_cache()
        result = G.check_tool_call(self.L2_TOOL, {})
        assert result is not None and result["blocked"] is True
        assert "confirm_level=L2" in result["reason"]

    def test_描述符文件缺失_L2_仍拦(self, enforce_on, tmp_path, monkeypatch):
        monkeypatch.setattr(G, "DESCRIPTORS_PATH", str(tmp_path / "missing.json"))
        G._reset_cache()
        result = G.check_tool_call(self.L2_TOOL, {})
        assert result is not None and result["blocked"] is True
        assert "confirm_level=L2" in result["reason"]

    def test_两个文件都损坏_L2_仍拦(self, enforce_on, tmp_path, monkeypatch):
        bad = tmp_path / "bad.json"
        bad.write_text("{这不是 JSON", encoding="utf-8")
        monkeypatch.setattr(G, "POLICY_POLICIES_PATH", str(bad))
        monkeypatch.setattr(G, "DESCRIPTORS_PATH", str(bad))
        G._reset_cache()
        result = G.check_tool_call(self.L2_TOOL, {})
        assert result is not None and result["blocked"] is True
        assert result["error_code"] == "APPROVAL_REQUIRED"

    def test_策略文件缺失_只读工具仍放行(self, enforce_on, tmp_path, monkeypatch):
        """反向锚点：本裁决**没有**取消策略层的 fail-open"""
        monkeypatch.setattr(G, "POLICY_POLICIES_PATH", str(tmp_path / "missing.json"))
        G._reset_cache()
        assert G.check_tool_call("read_file", {}) is None

    def test_元数据加载失败时治理动作仍拒绝(self, enforce_on, monkeypatch):
        """YAML 自身读不到 ⇒ 连"它是不是治理动作"都证不出 ⇒ fail-closed（不得放行）

        这是 :func:`agent.tool_gate._is_governance_action` 明确取舍过的一条：
        把"证不出"判成"放行"，等于让"让 YAML 读失败"成为一条绕过治理的路径。
        """
        def _boom():
            raise RuntimeError("yaml down")

        monkeypatch.setattr(G, "_tool_meta", _boom)
        result = G.check_tool_call("write_file", {})
        assert result is not None and result["blocked"] is True
        assert result["error_code"] == "PERMISSION_DENIED"


# ════════════════════════════════════════════════════════════
#  七、E5：非交互不挂空单
# ════════════════════════════════════════════════════════════


def _pending_count() -> int:
    """审批收件箱里 `tool_call` 待办条数（conftest 已把库隔离到临时目录）"""
    import agent.tool_approval as TA
    return int(TA.pending_snapshot().get("count") or 0)


class TestNonInteractiveNeverDangles:
    """E5：cron/CI/Webhook/后台 遇 L2 必须**明确拒绝**，且收件箱**没有**悬空待办

    原缺陷（TASK-06 §2.2）：非交互来源一律返回 `APPROVAL_REQUIRED` 并挂单 ⇒
    没有人能批准那张单 ⇒ 调用永远拿不到结果（"挂空单"）。
    """

    @pytest.mark.parametrize("source", ["scheduled", "cron", "ci", "webhook"])
    def test_非交互来源不挂单且给出出路(self, source, enforce_on, monkeypatch):
        monkeypatch.setenv("CP_PERMISSION_SESSION_SOURCE", source)
        before = _pending_count()
        result = G.check_tool_call("write_file", {"path": "x", "content": "c"},
                                   session_source=source)
        assert result is not None and result["blocked"] is True
        assert result["error_code"] == "PERMISSION_DENIED", \
            "非交互必须是**硬拒绝**（不是 APPROVAL_REQUIRED 那张永远等不到的单）"
        assert "非交互" in result["error"] or "无人在场" in result["error"]
        assert result.get("guidance"), "必须给出可操作出路（E5 的'可操作说明'）"
        assert _pending_count() == before, "非交互场景**不得**新增悬空待办"

    def test_后台身份拒绝(self, enforce_on, monkeypatch):
        """身份 = system/service_account 时同样算非交互（身份维度）"""
        from agent.tool_gate import reset_execution_identity, set_execution_identity
        handle = set_execution_identity("system")
        try:
            assert G.is_non_interactive("api", "system") is True
            result = G.check_tool_call("write_file", {})
            assert result is not None and result["blocked"] is True
            assert result["error_code"] == "PERMISSION_DENIED"
        finally:
            reset_execution_identity(handle)

    def test_模型的人机对话不被误判为非交互(self, enforce_on):
        """`api` 来源 + `llm` 身份 = 人在场的模型调用 ⇒ 走挂单，**不能**直接拒

        若只看来源就会把"模型发起的人机对话"误判成非交互 ⇒ 第一次调用就被硬拒，
        人根本没机会批准（审批闭环被打断）。
        """
        from agent.tool_gate import reset_execution_identity, set_execution_identity
        handle = set_execution_identity("llm")
        try:
            assert G.is_non_interactive("api", "llm") is False
            result = G.check_tool_call("write_file", {})
            assert result is not None and result["error_code"] == "APPROVAL_REQUIRED"
            assert result.get("approval_id"), "交互路径必须挂单（可被人工批准）"
        finally:
            reset_execution_identity(handle)

    def test_审批流关闭时不发永远等不到的单(self, enforce_on, monkeypatch):
        """`APPROVAL_ENABLED=0` 时审批流 submit 会直接放行并落 merged 记录

        ⇒ 收件箱里不会出现待办。此时若照常回"已提交审批收件箱、请人工确认后重试"，
        就是发了一张**永远等不到**的单号（模型无限重试、人看不到东西）。

        【为什么用 monkeypatch 造这个状态而不是设 `APPROVAL_ENABLED=0`】实测：
        该开关在审批流对象**构造期**读取（不是每次调用读）⇒ 单测里 setenv 之后
        已存在的流对象仍按原状态 submit（返回 `pending_review`），设环境变量
        **测不到**这条分支。故直接替换 `request_approval` 的返回值 —— 被替换的是
        **边界**（审批流的状态），被测的是**闸门对该状态的处置**，这正是本用例的对象。
        """
        import agent.tool_approval as TA

        monkeypatch.setattr(TA, "request_approval",
                            lambda *a, **k: {"ok": True, "approval_id": "appr-x",
                                             "state": "merged"})
        result = G.check_tool_call("write_file", {})
        assert result is not None and result["blocked"] is True
        assert "收件箱里不会出现待办" in result["error"], result["error"]
        assert result.get("guidance"), "必须给出两条出路（启用审批流 / 显式关审批边界）"

    def test_挂单失败时不放行(self, enforce_on, monkeypatch):
        """`request_approval` 失败 ⇒ 不得放行（审批边界一旦开启，"证不出已批准"就不能执行）"""
        import agent.tool_approval as TA

        monkeypatch.setattr(TA, "request_approval",
                            lambda *a, **k: {"ok": False, "error": "disk full"})
        result = G.check_tool_call("write_file", {})
        assert result is not None and result["blocked"] is True
        assert "挂单失败" in result["error"]


# ════════════════════════════════════════════════════════════
#  八、无身份被拒绝（§1 完成判据的第三种情形）
# ════════════════════════════════════════════════════════════


class TestIdentityIsRequired:
    """TASK-06 §1 完成判据："无身份调用时**被拒绝**（而非放行）"

    【为什么"无身份"单列一支】原实现下 `identity=""` + 来源 `cli` 会走挂单分支 ——
    那对"真的有人在 CLI 前面"是对的，但对"无人值守且没报身份"的路径是挂空单。
    现在的判据：**无身份 + 非交互来源 ⇒ 拒**；无身份 + 交互来源 ⇒ 挂单（可批准）。
    """

    def test_无身份_且非交互_被拒(self, enforce_on):
        result = G.check_tool_call("write_file", {}, session_source="ci")
        assert result is not None and result["blocked"] is True
        assert result["error_code"] == "PERMISSION_DENIED"

    def test_无身份_但交互_仍可挂单(self, enforce_on):
        result = G.check_tool_call("write_file", {}, session_source="cli")
        assert result is not None and result["error_code"] == "APPROVAL_REQUIRED"

    def test_身份值域与_capregistry_一致(self):
        """【不 import 而是对拍】两个模块不互相 import（避免成环），由本用例锁死一致性

        【为什么比集合而不是比元组】值域一致即可，**顺序不是契约** ——
        `agent/capregistry/invoke.py` 是 `(llm, human, ...)`，本模块是
        `(human, llm, ...)`。原实现要求逐位相等会在这种无意义的差异上误报。
        """
        from agent.capregistry.invoke import IDENTITIES
        assert set(G.EXECUTION_IDENTITIES) == set(IDENTITIES)
        assert len(G.EXECUTION_IDENTITIES) == len(set(G.EXECUTION_IDENTITIES))
        assert "service_account" in G.EXECUTION_IDENTITIES, "第四类主体必须在内"

    def test_非法身份值按未声明处理(self, enforce_on):
        from agent.tool_gate import reset_execution_identity, set_execution_identity
        handle = set_execution_identity("root")          # 不在值域内
        try:
            assert G.current_execution_identity() == "root"   # 原值保存（不静默改写）
            assert G.is_non_interactive("api", "root") is False  # 但不被认作已知身份
        finally:
            reset_execution_identity(handle)


# ════════════════════════════════════════════════════════════
#  九、SA 预授权（交付物 #8）
# ════════════════════════════════════════════════════════════


@pytest.fixture
def sa_env(tmp_path, monkeypatch):
    """把 SA 凭据库隔离到 tmp_path（**不碰 data/service_accounts.json**，D6）

    【为什么这里要显式 `install_gate_hook()`】生产路径上它在 `app_server.py` 启动时
    安装（`_install_service_account_hook`）。单测不 import app_server ⇒ 必须显式装。
    这不改变被测语义：装钩子把 `service_account.preauthorize` 注册进闸门，
    正是"预授权是闸门内的一条判定"的接线本身。
    """
    from agent.security import service_account as SA
    monkeypatch.setenv(SA.ACCOUNTS_PATH_ENV, str(tmp_path / "service_accounts.json"))
    monkeypatch.delenv(SA.KEY_ENV, raising=False)
    SA.reset_registry()
    assert SA.install_gate_hook() is True
    yield SA
    SA.reset_registry()
    G.set_preauthorization_hook(None)


class TestServiceAccountPreauthorization:
    """SA 凭 preauthorize 通过 L2：**闸门内的一条判定**，不是旁路

    TASK-06 §5 的"不通过"第 3 条：✗ 让 SA 直接绕过 `tool_gate`。
    故本组用例同时证明两件事：
      ① scope 覆盖 ⇒ 放行（且落审计 `decision=preauthorized`）；
      ② scope **不**覆盖 ⇒ 仍然被拒（预授权是收紧的一条通路，不是万能钥匙）。
    """

    def _sa_with(self, sa_env, caps, level, tmp_path):
        sa_env.create_service_account(
            "ci-bot", scope=sa_env.SAScope(capabilities=frozenset(caps),
                                           max_confirm_level=level),
            created_by="admin")
        return sa_env.issue_token("ci-bot")

    def test_scope_覆盖_L2_则放行且落审计(self, sa_env, enforce_on, tmp_path):
        from agent.security import service_account as SA
        token = self._sa_with(sa_env, ["write_file"], "L2", tmp_path)
        handle = SA.enter_service_account(token)
        try:
            assert G.current_execution_identity() == "service_account"
            assert G.check_tool_call("write_file", {"path": "x", "content": "c"}) is None
        finally:
            handle.reset()

    def test_scope_未覆盖则仍被拒(self, sa_env, enforce_on, tmp_path):
        from agent.security import service_account as SA
        token = self._sa_with(sa_env, ["read_file"], "L2", tmp_path)
        handle = SA.enter_service_account(token)
        try:
            result = G.check_tool_call("write_file", {})
            assert result is not None and result["blocked"] is True, \
                "预授权必须按能力名逐条判定，不能「有 SA 就全放」"
        finally:
            handle.reset()

    def test_级别上限不足则被拒(self, sa_env, enforce_on, tmp_path):
        from agent.security import service_account as SA
        token = self._sa_with(sa_env, ["write_file"], "L1", tmp_path)  # 上限只到 L1
        handle = SA.enter_service_account(token)
        try:
            result = G.check_tool_call("write_file", {})       # 该能力是 L2
            assert result is not None and result["blocked"] is True
        finally:
            handle.reset()

    def test_L3_不接受通配(self, sa_env, enforce_on, tmp_path):
        """L3 = 须**显式**预授权：`*` 不得成为"一把万能钥匙"（service_account 的取舍）"""
        from agent.security import service_account as SA
        token = self._sa_with(sa_env, ["*"], "L3", tmp_path)
        handle = SA.enter_service_account(token)
        try:
            result = G.check_tool_call("shell_execute", {})    # L3 且未逐条点名
            assert result is not None and result["blocked"] is True
        finally:
            handle.reset()

    def test_未注册钩子一概视为未预授权(self, sa_env, enforce_on, tmp_path):
        """钩子未装（例如启动期 install 失败）⇒ 一律不放行（**绝不静默放行**）"""
        from agent.security import service_account as SA
        token = self._sa_with(sa_env, ["write_file"], "L2", tmp_path)
        G.set_preauthorization_hook(None)
        handle = SA.enter_service_account(token)
        try:
            result = G.check_tool_call("write_file", {})
            assert result is not None and result["blocked"] is True
        finally:
            handle.reset()

    def test_钩子抛异常按未授权处理(self, sa_env, enforce_on):
        def _boom(*_a, **_k):
            raise RuntimeError("scope db down")

        G.set_preauthorization_hook(_boom)
        result = G.check_tool_call("write_file", {}, session_source="ci")
        assert result is not None and result["blocked"] is True, \
            "预授权查询失败必须 fail-closed（它是「证不出已授权」的情形）"


# ════════════════════════════════════════════════════════════
#  十、E9：审计可区分（三种身份）
# ════════════════════════════════════════════════════════════


class TestAuditIsDistinguishable:
    """E9：人逐次确认 / SA 预授权 / 无身份被拒 三者**在审计记录里可区分**

    落点是 `agent/audit/chain.py`（真实哈希链 + Merkle + ed25519，TASK-00 认定的
    仓库最高质量设施），**不是** `data/approval_records.jsonl`（被整文件重写、
    不在链上 —— TASK-00 已标为自相矛盾）。
    """

    def _records(self, monkeypatch) -> list:
        """收集 `_audit_confirm_decision` 的入参（不依赖审计链的内部实现）"""
        import agent.tool_gate as mod
        seen = []

        def _fake(*, tool, level, decision, identity, source, reason,
                  tenant_id="", version="", actor=""):
            seen.append({"tool": tool, "level": level, "decision": decision,
                         "identity": identity, "source": source,
                         "tenant_id": tenant_id, "reason": reason})

        monkeypatch.setattr(mod, "_audit_confirm_decision", _fake)
        return seen

    def test_无身份被拒的审计可区分(self, enforce_on, monkeypatch):
        records = self._records(monkeypatch)
        G.check_tool_call("write_file", {}, session_source="ci")
        assert any(r["decision"] == "denied_no_identity" and r["identity"] == ""
                   for r in records), records
        assert all(r["level"] in CONFIRM_LEVELS for r in records)
        assert all(r["tenant_id"] for r in records), "审计必须带 tenant_id（v1.4 §12）"

    def test_非交互被拒的审计可区分(self, enforce_on, monkeypatch):
        from agent.tool_gate import reset_execution_identity, set_execution_identity
        records = self._records(monkeypatch)
        handle = set_execution_identity("system")
        try:
            G.check_tool_call("write_file", {}, session_source="scheduled")
        finally:
            reset_execution_identity(handle)
        assert any(r["decision"] == "denied_non_interactive"
                   and r["identity"] == "system" for r in records), records

    def test_影子模式的审计可区分(self, enforce_on, monkeypatch):
        records = self._records(monkeypatch)
        monkeypatch.setenv(G.CONFIRM_LEVEL_SHADOW_ENV, "1")
        assert G.check_tool_call("write_file", {}) is None
        assert any(r["decision"] == "shadow_alert" for r in records), records

    def test_预授权的审计可区分(self, sa_env, enforce_on, monkeypatch, tmp_path):
        from agent.security import service_account as SA
        sa_env.create_service_account(
            "ci-bot2", scope=sa_env.SAScope(capabilities=frozenset(["write_file"]),
                                             max_confirm_level="L2"))
        token = sa_env.issue_token("ci-bot2")
        records = self._records(monkeypatch)
        handle = SA.enter_service_account(token)
        try:
            G.check_tool_call("write_file", {})
        finally:
            handle.reset()
        assert any(r["decision"] == "preauthorized"
                   and r["identity"] == "service_account" for r in records), records

    def test_三种情形的字段组合互不相同(self):
        """E9 的判据是**可区分**，故把三种组合显式写成断言（避免将来字段退化）"""
        human = ("human", "approved")
        sa = ("service_account", "preauthorized")
        none = ("", "denied_no_identity")
        assert len({human, sa, none}) == 3


class TestAuditLandsOnTheChain:
    """**端到端**复核：确认决策真的写进了 `audit_chain`（不是只在日志里）

    ## 为什么必须单列这一类（D12：任何"修复已生效"的结论都要用真实产物复测）

    上一个子代理的实现把 `source="tool_gate"` 传给 `agent/audit/chain.py::append()`，
    而链上只接受 `{agent, ui, system, migration}` ⇒ 每次 append 都抛
    `AuditEntryError`，被 `_audit_confirm_decision` 的宽 except 吞掉后只留一行 ERROR 日志。
    后果：**E9 与"审批记录并入 audit_chain"表面上已实现，实际一条记录都没落链**。

    上面 `TestAuditIsDistinguishable` 用**假替身**验的是"传给审计的参数对不对"，
    它**测不出**这条缺陷（替身永远成功）—— 那正是"测试夹具冒充生产"。
    故本类用**真实**审计链（`tests/conftest.py` 已把 `AUDIT_DB_PATH` 指向临时目录）
    复测一次：写进去，再**读回来**。
    """

    #: **唯一 subject**：审计链是全局的、只增的，别的用例也会写 `write_file` 的
    #: `tool.confirm_decision` 记录 ⇒ 用通用工具名读"最后一条"会**顺序相关地读到别人的记录**
    #: （实测：与 `test_tenant_isolation.py` 同会话时 2 条断言互串）。
    #: 用一个只在本文件出现的探针名，读回的就是自己那条（与"注册表探针工具"同一手法）。
    SUBJECT = "__selftest_confirm_level__"

    @staticmethod
    def _chain_records(subject: str) -> list:
        """从**隔离的**审计链读回 `tool.confirm_decision`（读的是落盘产物）

        【为什么必须用 `audit.facade.audit.chain` 而不是 `get_audit_chain()`】
          `chain.get_audit_chain()` 无参时落到**硬编码默认路径**
          `data/audit/audit_chain.db`，**不读** `AUDIT_DB_PATH`；而
          `tools/conftest.py` 的隔离只对 facade 生效（它读该环境变量）。
          实测代价：用无参 `get_audit_chain()` 读会读到**生产链**，
          而用无参 `get_audit_chain()` **写**（修复前的实现就是这样）会把测试记录
          写进生产链 —— 两个方向都踩到过。故这里显式走 facade。
        """
        from agent.audit.facade import audit as _facade
        chain = getattr(_facade, "chain", None)
        assert chain is not None, "审计门面未绑定链（AUDIT_CHAIN_ENABLED=0 或未初始化）"
        return [e for e in chain.entries(action="tool.confirm_decision", limit=None)
                if str(getattr(e, "subject", "") or "") == subject]

    def test_决策真的落进审计链(self, enforce_on):
        G._audit_confirm_decision(tool=self.SUBJECT, level="L2",
                                  decision="denied_no_identity", identity="",
                                  source="ci", reason="单测：链上留痕自证",
                                  tenant_id="default", version="1.0.0")
        records = self._chain_records(self.SUBJECT)
        assert records, ("确认决策未落进 audit_chain（检查 append 的 source 是否合法："
                         "链上只接受 agent/ui/system/migration）")
        latest = records[-1]                     # 升序 ⇒ 最后一条是最新
        assert str(getattr(latest, "source", "")) == "system"
        assert str(getattr(latest, "actor", "")) == "unknown"   # identity 为空时的占位
        assert str(getattr(latest, "seq", "")) != ""

    def test_载荷字段齐全(self, enforce_on):
        import json as _json

        G._audit_confirm_decision(tool=self.SUBJECT, level="L3",
                                  decision="preauthorized",
                                  identity="service_account", source="ci",
                                  reason="单测：SA 预授权留痕",
                                  tenant_id="default", version="2.0.0")
        rec = self._chain_records(self.SUBJECT)[-1]
        payload = getattr(rec, "payload", None)
        if isinstance(payload, str):
            payload = _json.loads(payload)
        payload = dict(payload or {})
        for field in ("confirm_level", "decision", "identity", "session_source",
                      "tenant_id", "tool_version", "reason"):
            assert field in payload, f"审计载荷缺 {field}（v1.4 §12 字段纪律）"
        assert payload["confirm_level"] == "L3"
        assert payload["identity"] == "service_account"
        assert payload["tenant_id"] == "default"
        assert str(getattr(rec, "actor", "")) == "service_account"

    def test_审计写入失败不影响执行(self, enforce_on, monkeypatch):
        """留痕是治理要求，不是执行前置条件（否则可观测性会变成可用性风险）"""
        import agent.audit.chain as CH

        def _boom(*_a, **_k):
            raise RuntimeError("chain down")

        monkeypatch.setattr(CH, "get_audit_chain", _boom)
        G._audit_confirm_decision(tool="write_file", level="L2", decision="approved",
                                  identity="human", source="cli", reason="x")
        # 不抛异常即通过；放行判定不因审计失败而改变
        monkeypatch.setattr(G, "_audit_confirm_decision", lambda **_k: None)
        assert G.check_tool_call("read_file", {}) is None


# ════════════════════════════════════════════════════════════
#  十一、回滚与影子模式（§6）
# ════════════════════════════════════════════════════════════


class TestRollbackAndShadow:
    """TASK-06 §6：`high → L2` 是本任务最高风险一行 ⇒ 必须有**可即时生效**的开关"""

    def test_影子模式只告警不拦截(self, enforce_on, monkeypatch):
        """影子模式只覆盖**新加的分级层**（`risk: high → L2`），不覆盖描述符层

        【实测口径】`shell_execute` 在真实 `data/descriptors.json` 里有
        `trust.requires_approval=true` ⇒ 它在**描述符那一步**（第 2 步，排在分级层
        之前）就被拦，与影子模式无关 —— 影子模式不该把**既有**的审批边界也一起
        静默关掉（那会让"先跑一个周期观测"变成"顺手把旧边界也关了"）。
        """
        monkeypatch.setenv(G.CONFIRM_LEVEL_SHADOW_ENV, "1")
        assert G.check_tool_call("write_file", {}) is None, \
            "影子模式下新加的分级层不得拦截"
        assert G.check_tool_call("shell_execute", {}) is not None, \
            "影子模式不得关掉**既有**的描述符审批边界"

    def test_影子模式默认关闭(self, monkeypatch, enforce_on):
        assert G._confirm_level_shadow_enabled() is False
        monkeypatch.setenv(G.CONFIRM_LEVEL_SHADOW_ENV, "1")
        assert G._confirm_level_shadow_enabled() is True

    def test_总开关关闭即回滚(self, enforce_on, monkeypatch):
        monkeypatch.setenv(G.APPROVAL_ENFORCE_ENV, "0")
        assert G.check_tool_call("write_file", {}) is None

    def test_关掉分级层不改变_L0_行为(self, enforce_on, monkeypatch):
        """回滚路径不得引入新行为：L0 在两种口径下都免确认"""
        monkeypatch.setenv(G.CONFIRM_LEVEL_ENFORCE_ENV, "0")
        assert G.check_tool_call("read_file", {}) is None


# ════════════════════════════════════════════════════════════
#  十二、任务 C 裁决：派生 vs 显式声明（91 个 YAML 零改动）
# ════════════════════════════════════════════════════════════


class TestNoYamlWritesNeeded:
    """裁决：`confirm_level` **从治理三轴派生**，不写进 91 个 YAML

    TASK-06 §3 第 2 步原文提到"91 个 YAML 加 `confirm_level`"，但那份清单是**审计期
    假设**（§2 实测写的是"91 个 YAML 里出现 0 次 ⇒ 确实不存在"）。逐条核后的裁决是
    **不写**，理由四条：

    1. **D1 单一真相源**：`risk/effect/plane` 已是权威声明，`confirm_level` 是它们的
       **函数**。再写一份到 YAML 就是同一件事的第二份口径 —— 改 `risk` 而忘了改
       `confirm_level` 会静默产生矛盾（而 `backfill_tool_callability.py` 的 `--check`
       正是为抓这类漂移而存在的，见 TASK-06 实测到的 10 条"声明 vs 派生不一致"）。
    2. **零改动即生效**：91 个 YAML **一个都没写** `confirm_level`，而 13 个 high
       工具**已经全部**进确认流（E1 已断言）⇒ 派生已充分满足本任务目标。
    3. **override 机制已经就位**（TASK-06 §3 第 2 步第 4 项要求的三条都满足）：
       YAML 可写 `confirm_level` + `confirm_level_reason`；`--check` 能盘点
       （清单里有 `confirm_level_declared/_overridden/_reason` 四个字段）；
       **禁止静默降级**（`_resolve_confirm_level` 缺理由即丢弃放宽声明）。
    4. **写了反而更差**：把 `L2` 写进 13 个 YAML 会掩盖"它为什么是 L2"（`risk: high`），
       并让"调 risk 却不调 confirm_level"成为新的漂移源。
    """

    def test_91_个_YAML_仍然零声明(self):
        """本裁决的可复核形式：YAML 侧不写该字段，且这不影响任何判定"""
        from agent.lines import load_tool_meta
        metas = load_tool_meta()
        assert len(metas) == 91, len(metas)
        declared = {k: v.confirm_level for k, v in metas.items() if v.confirm_level}
        assert declared == {}, f"若要写进 YAML 就属于口径变更，必须同时更新本裁决：{declared}"

    def test_生效级别等于派生级别(self):
        from agent.lines import load_tool_meta
        for name, meta in load_tool_meta().items():
            assert meta.effective_confirm_level == meta.derived_confirm_level, name
            assert meta.confirm_level_overridden is False, name

    def test_清单可盘点_override_字段(self):
        """§3 第 2 步第 4 项："override 必须能被查询（盘点表里可见）" """
        entries = [e for e in _manifest_entries() if e.get("kind") == "tool"]
        assert entries
        for e in entries:
            for field in ("confirm_level", "confirm_level_derived",
                          "confirm_level_declared", "confirm_level_overridden",
                          "confirm_level_reason"):
                assert field in e, f"{e.get('tool_name')} 缺盘点字段 {field}"

    def test_override_更严无需理由_更宽必须有理由(self, tmp_path):
        """§3 第 2 步第 4 项："**禁止**静默降级（v1.4 ADR-028 精神）"

        用 `models._resolve_confirm_level` 直接验裁定逻辑（不起 91 个 YAML 的副本）。
        返回值 `""` 表示"该 override 不生效、按派生值"（docstring 的口径）。
        """
        from agent.lines.models import _resolve_confirm_level

        # 更严（L2 → L3）：允许，不需要理由
        value, note = _resolve_confirm_level("L3", "L2", "")
        assert value == "L3" and "收紧" in note

        # 更宽（L2 → L1）**有**理由：允许
        value, note = _resolve_confirm_level("L1", "L2", "该工具业务上高频，见 RFC-xxx")
        assert value == "L1" and "理由" in note

        # 更宽但**无**理由：丢弃声明（回落派生值）并留痕
        value, note = _resolve_confirm_level("L1", "L2", "")
        assert value == "", "缺理由的放宽声明必须被丢弃（禁止静默降级）"
        assert "禁止静默降级" in note

        # 与派生值相同的冗余声明：按未覆盖处理（避免盘点表虚报 override）
        value, note = _resolve_confirm_level("L2", "L2", "")
        assert value == "" and "冗余" in note

        # 未声明：原样返回空（不得凭空造出一个 override）
        assert _resolve_confirm_level("", "L2", "理由") == ("", "")
