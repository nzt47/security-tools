"""A2 除险回归防线 —— 确认门三处绕过（S1 豁免 / S2 fail-open / S3 技能脚本绕闸）

【本文件钉住什么】（对应 docs/audit_skill_governance/A2.md 与审计 S1/S2/S3）
  · **S2**：`agent/tools/__init__.py::call()` 的闸门导入失败 ⇒ **L2/L3 拒、L0/L1 放行**，
         且留下结构化事件 `tool_gate_import_failed`（改动前是「一律放行 + 零告警」）。
  · **S3**：`agent/skills_mgmt/executor.py::SkillExecutor.execute()` 在起子进程**之前**
         过闸门，`effect=execute` ⇒ 被拦（改动前整条链路不过闸门、不落任何确认决策）。
  · **S1**：豁免名单的生效值变化 ⇒ 审计链出现 `tool.confirm.exempt_changed`
         （old/new/added/removed/levels/actor）；且 **L3 不可被豁免**。
  · **回归**：未加固前本应放行的调用仍然放行（L1 仍可豁免；总开关=0 时照旧全放；
         闸门导入失败时 L0/L1 仍可调用）—— 不为了安全把正常路径封死。

【纪律】对「链上有没有记录」的断言一律**真实读审计链**（`agent.audit.facade`，路径由
        `tests/conftest.py` 隔离到会话临时目录），**不** monkeypatch 审计写入；
        只有「闸门有没有被调用」这类观测点才用 spy。
"""
from __future__ import annotations

import sys
from typing import Any, Dict

import pytest

import agent.tool_gate as G
import agent.tools as tools
from agent.audit.facade import get_audit
from agent.skills_mgmt.executor import SKILL_GATE_UNAVAILABLE_EVENT, SkillExecutor
from agent.skills_mgmt.file_store import SkillFileStore

#: 四个**真实登记**的探针工具（级别取自 data/tool_definitions/*.yaml，见 A2.md §级别盘点）
L3_TOOL = "shell_execute"      # effect=execute / risk=critical ⇒ L3（且被描述符门控）
L2_TOOL = "write_file"         # effect=write   / risk=high     ⇒ L2（逐次确认）
L1_TOOL = "delegate"           # effect=execute / risk=medium   ⇒ L1（摘要确认）
L0_TOOL = "list_directory"     # effect=read    / risk=low      ⇒ L0（免确认）

DELEGATE_ARGS: Dict[str, Any] = {"goal": "计算 1..100 之和"}


@pytest.fixture
def enforce_on(monkeypatch):
    """打开审批边界（会话基线是 0，见 tests/conftest.py 的注释），并清掉名单/影子开关"""
    monkeypatch.setenv(G.APPROVAL_ENFORCE_ENV, "1")
    monkeypatch.delenv(G.CONFIRM_LEVEL_ENFORCE_ENV, raising=False)
    monkeypatch.delenv(G.CONFIRM_LEVEL_SHADOW_ENV, raising=False)
    monkeypatch.delenv(G.CONFIRM_LEVEL_EXEMPT_ENV, raising=False)
    return True


@pytest.fixture
def stub_registry(monkeypatch):
    """把工具注册表换成只有探针 handler 的假表（避免真 handler 的网络/磁盘副作用）"""
    def _stub(**kwargs):
        return {"ok": True, "stub": True, "kwargs": kwargs}

    reg: Dict[str, Dict[str, Any]] = {}
    for name in (L0_TOOL, L1_TOOL, L2_TOOL, L3_TOOL):
        reg[name] = {"name": name, "description": "A2 探针工具", "handler": _stub,
                     "schema": {"type": "object", "properties": {}},
                     "source": tools.SOURCE_BUILTIN}
    monkeypatch.setattr(tools, "_registry", reg)
    monkeypatch.setattr(tools, "_discovery_service", None)
    return reg


def _break_tool_gate(monkeypatch):
    """令 `from agent.tool_gate import ...` 抛 ImportError（sys.modules 置 None 即此语义）"""
    monkeypatch.setitem(sys.modules, "agent.tool_gate", None)


def _chain(action: str, limit: int = 50):
    """真实读审计链（不 mock）：按 action 取最近的记录（seq 升序，最新在末尾）"""
    return get_audit().recent(limit, action=action)


def _last_seq(action: str) -> int:
    """该 action 现有记录的最大 seq（0 = 还没有）—— 用于"本次动作是否新增了记录"的判据"""
    entries = _chain(action)
    return max((e.seq for e in entries), default=0)


# ════════════════════════════════════════════════════════════
#  S2：闸门导入失败 ⇒ 按确认级 fail-closed（L2/L3 拒、L0/L1 放行）
# ════════════════════════════════════════════════════════════


class TestS2GateImportFailureIsFailClosed:
    """改动前：`from agent.tool_gate import check_tool_call` 写在 try 内 ⇒ 导入失败 = 全放行"""

    def test_导入失败时_L3_工具被拒(self, monkeypatch, stub_registry):
        _break_tool_gate(monkeypatch)

        out = tools.call(L3_TOOL, command="echo hi")

        assert isinstance(out, dict) and out.get("blocked") is True, out
        assert out.get("error_code") == "PERMISSION_DENIED", out
        assert out.get("error_code_detail") == "tool_gate_import_failed", out
        assert out.get("confirm_level") == "L3", out
        assert out.get("degraded") is True, out

    def test_导入失败时_L2_工具被拒(self, monkeypatch, stub_registry):
        _break_tool_gate(monkeypatch)

        out = tools.call(L2_TOOL, path="a.txt", content="x")

        assert isinstance(out, dict) and out.get("blocked") is True, out
        assert out.get("confirm_level") == "L2", out

    def test_导入失败时_L0_工具放行(self, monkeypatch, stub_registry):
        """**不能矫枉过正**：闸门坏了不该把只读低危工具一起封死"""
        _break_tool_gate(monkeypatch)

        out = tools.call(L0_TOOL, path=".")

        assert isinstance(out, dict) and out.get("stub") is True, out

    def test_导入失败时_L1_工具放行(self, monkeypatch, stub_registry):
        _break_tool_gate(monkeypatch)

        out = tools.call(L1_TOOL, **DELEGATE_ARGS)

        assert isinstance(out, dict) and out.get("stub") is True, out

    def test_导入失败时未登记工具按最严拒绝(self, monkeypatch, stub_registry):
        """证不出它无害 ⇒ 拒（判不出级别就是 L3）"""
        _break_tool_gate(monkeypatch)

        out = tools.call("__a2_未登记工具__", x=1)

        assert isinstance(out, dict) and out.get("blocked") is True, out
        assert out.get("confirm_level") == "L3", out

    def test_导入失败留下结构化事件(self, monkeypatch, stub_registry):
        """事件必须真的落链 —— 否则这条降级路径又变成看不见的（审计 S2 的原话）"""
        _break_tool_gate(monkeypatch)

        tools.call(L3_TOOL, command="echo hi")

        events = _chain("tool_gate_import_failed")
        assert events, "审计链上没有 tool_gate_import_failed 事件"
        payload = events[-1].payload
        assert payload.get("event") == "tool_gate_import_failed", payload
        assert payload.get("tool") == L3_TOOL, payload
        assert payload.get("decision") == "denied", payload
        assert payload.get("degraded") is True, payload
        assert payload.get("param_keys") == ["command"], payload

    def test_导入失败时_L1_也留下事件但判定为放行(self, monkeypatch, stub_registry):
        _break_tool_gate(monkeypatch)

        tools.call(L1_TOOL, **DELEGATE_ARGS)

        events = _chain("tool_gate_import_failed")
        assert events and events[-1].payload.get("decision") == "allowed_degraded", \
            events[-1].payload if events else None


# ════════════════════════════════════════════════════════════
#  回归：闸门**可用**时的正常路径不被封死
# ════════════════════════════════════════════════════════════


class TestNormalPathNotOverBlocked:
    def test_闸门可用时_L0_照常可调用(self, stub_registry, enforce_on):
        out = tools.call(L0_TOOL, path=".")

        assert isinstance(out, dict) and out.get("stub") is True, out

    def test_总开关为0时_L2_照旧放行(self, monkeypatch):
        """会话基线（CP_TOOL_GATE_APPROVAL_ENFORCE=0）= 操作员显式回滚 ⇒ 分级层不拦"""
        monkeypatch.setenv(G.APPROVAL_ENFORCE_ENV, "0")

        assert G.check_tool_call(L2_TOOL, {"path": "a.txt", "content": "x"}) is None

    def test_L1_工具仍可被豁免(self, monkeypatch, enforce_on):
        """L1（摘要确认）**保持可豁免** —— 本轮只禁止 L3 被豁免，别把 L1 一起禁掉"""
        monkeypatch.setenv(G.CONFIRM_LEVEL_EXEMPT_ENV, L1_TOOL)

        assert G.check_tool_call(L1_TOOL, dict(DELEGATE_ARGS)) is None

    def test_未在名单里的工具照旧要确认(self, monkeypatch, enforce_on):
        monkeypatch.setenv(G.CONFIRM_LEVEL_EXEMPT_ENV, L1_TOOL)

        blocked = G.check_tool_call(L2_TOOL, {"path": "a.txt", "content": "x"})

        assert blocked is not None and blocked["blocked"] is True
        # 待审批路径（`_deny_approval`）的结果里没有 `confirm_level` 字段，级别在 reason 里
        assert "confirm_level=L2" in str(blocked.get("reason") or ""), blocked


# ════════════════════════════════════════════════════════════
#  S1：豁免名单变更入审计链 + L3 不可被豁免
# ════════════════════════════════════════════════════════════


class TestS1ExemptChangeIsAudited:
    """改动前：名单的生效值可以由「直改 data/ui_settings.json / .env / 进程环境」改变，
    这三条通路一条记录都没有（见 A2.md §S1 的原始 SQL 证据）。"""

    def test_新增豁免写入审计链(self, monkeypatch, enforce_on):
        """用本文件其它用例**没用过**的名单值：名单值一旦被记录过，再设同一个值就是
        「无变更」而不该记账 —— 这条纪律本身也是被测行为（见下一个用例）。

        【T-ISO 2026-09-25：基线必须由本用例自己建立】`payload.old_value` 来自
        `_chain_baseline_exempt()` 读到的「链上最近一条 `new_value`」，而链是
        **整轮会话持久**的（`tests/conftest.py` 只把 `AUDIT_DB_PATH` 隔离到会话级临时
        目录，不逐用例清；链又是追加写、无删除 API）⇒ 不先建立基线，`added`/`removed`
        就随前序用例漂移。实测：文件顺序下基线是 `delegate`、随机顺序下是 `shell_execute`
        ⇒ `removed` 断言必然失败（见 docs/audit_skill_governance/T-ISO.md §2）。
        故先用**空名单**消费一次，把链上已知值归零，后面的断言即与执行顺序无关。"""
        monkeypatch.setenv(G.CONFIRM_LEVEL_EXEMPT_ENV, "")
        G._is_confirm_level_exempt(L2_TOOL)                     # 建立基线（= 空名单）
        baseline, readable = G._chain_baseline_exempt()
        assert readable and baseline in (None, ""), (          # 前置条件：基线确实是空的
            "本用例要求空名单基线；出现别的值说明会话内已有其它生效值（跨用例污染）",
            baseline)
        last_before = _last_seq(G.EXEMPT_CHANGE_ACTION)

        new_value = L2_TOOL + "," + "compress"                  # write_file(L2) + compress(L1)
        monkeypatch.setenv(G.CONFIRM_LEVEL_EXEMPT_ENV, new_value)

        G._is_confirm_level_exempt(L2_TOOL)                     # 触发一次对拍（=消费点）

        fresh = [e for e in _chain(G.EXEMPT_CHANGE_ACTION) if e.seq > last_before]
        assert len(fresh) == 1, [e.payload for e in fresh]
        record = fresh[0]
        payload = record.payload
        assert payload.get("old_value") == "", payload
        assert payload.get("new_value") == new_value, payload
        assert payload.get("added") == ["compress", L2_TOOL], payload
        assert payload.get("removed") == [], payload
        assert payload.get("baseline_readable") is True, payload
        assert payload.get("observed_by") == "agent.tool_gate", payload
        # 级别随记录一起落链："豁免了一个 L2/L3"必须一眼可见（S1 事故的审计难点）
        assert payload.get("levels", {}).get(L2_TOOL) == "L2", payload
        assert payload.get("levels", {}).get("compress") == "L1", payload
        assert record.subject == "setting:" + G.CONFIRM_LEVEL_EXEMPT_ENV, record.subject

    def test_移除豁免也写入审计链(self, monkeypatch, enforce_on):
        """收紧方向（移出名单）同样必须留痕："谁把某个工具从豁免名单里删了"也要可查

        （进程内对拍缓存 `_EXEMPT_WATCH` 的复位已收敛到 tests/unit/conftest.py 的
        autouse fixture `_tiso_reset_tool_gate_module_state`，本文件不再逐用例复位。）"""
        monkeypatch.setenv(G.CONFIRM_LEVEL_EXEMPT_ENV, L2_TOOL + ",compress")
        G._is_confirm_level_exempt(L2_TOOL)                      # 触发一次对拍（建立基线）
        last_before = _last_seq(G.EXEMPT_CHANGE_ACTION)

        monkeypatch.setenv(G.CONFIRM_LEVEL_EXEMPT_ENV, L2_TOOL)  # 移出 compress
        G._is_confirm_level_exempt(L2_TOOL)

        fresh = [e for e in _chain(G.EXEMPT_CHANGE_ACTION) if e.seq > last_before]
        assert len(fresh) == 1, [e.payload for e in fresh]
        payload = fresh[0].payload
        assert payload.get("new_value") == L2_TOOL, payload
        assert payload.get("removed") == ["compress"], payload

    def test_同值重复观测不重复记账(self, monkeypatch, enforce_on):
        """名单没变就**不写**（否则每次工具调用都会往链上灌一条噪声记录）

        起点由 conftest 的 autouse fixture 保证（`_EXEMPT_WATCH` 复位为未对拍过），
        故本条无需再逐用例复位。"""
        monkeypatch.setenv(G.CONFIRM_LEVEL_EXEMPT_ENV, L2_TOOL)
        G._is_confirm_level_exempt(L2_TOOL)
        before = len(_chain(G.EXEMPT_CHANGE_ACTION))

        for _ in range(5):
            G._is_confirm_level_exempt(L2_TOOL)

        assert len(_chain(G.EXEMPT_CHANGE_ACTION)) == before

    def test_L3_不可被豁免(self, monkeypatch, enforce_on):
        """L3 = 默认禁止（仅显式预授权）⇒ 豁免名单够不着它（本轮新增的硬规则）

        【T-ISO 2026-09-25 纠错：对照断言原先写错了】原对照只把名单设成 `L3_TOOL`，
        却断言 `L1_TOOL`（delegate）**不被要求确认** —— 而名单里根本没有它，
        `delegate` 本来就该要确认（实得 APPROVAL_REQUIRED）。该断言**单跑同样失败**
        （实测 `-p no:randomly` 单跑该用例 1 failed），所以它从来不是顺序问题，
        而是「把不在名单里的工具当成已豁免」的口径错误。
        对照的正确形态是：名单里**同时**写 L3 与 L1 ⇒ L3 被忽略、L1 照旧豁免，
        这才证明「L3 规则生效」而不是「整层被关掉了」。
        （进程内对拍缓存 `_EXEMPT_WATCH` 的复位已收敛到 conftest 的 autouse fixture。）"""
        monkeypatch.setenv(G.CONFIRM_LEVEL_EXEMPT_ENV, L3_TOOL + "," + L1_TOOL)

        # 直接调分级层：描述符门控判在它**之前**，走 `check_tool_call` 会掩盖本规则
        out = G._confirm_level_outcome(L3_TOOL, {"command": "echo hi"})

        assert out is not None and out.get("blocked") is True, out
        assert "confirm_level=L3" in str(out.get("reason") or ""), out
        # 对照：同一次运行里 L1 的豁免照旧生效（不是"把整层关掉了"）
        assert G._confirm_level_outcome(L1_TOOL, dict(DELEGATE_ARGS)) is None

    def test_L3_豁免不落_exempted_决策(self, monkeypatch, enforce_on):
        """被忽略的豁免**不得**留下 decision=exempted（否则审计会谎报"已豁免放行"）"""
        monkeypatch.setenv(G.CONFIRM_LEVEL_EXEMPT_ENV, L3_TOOL)
        G._confirm_level_outcome(L3_TOOL, {"command": "echo hi"})

        exempted = [e for e in _chain("tool.confirm_decision")
                    if e.subject == L3_TOOL
                    and (e.payload or {}).get("decision") == "exempted"]
        assert exempted == [], [e.payload for e in exempted]


# ════════════════════════════════════════════════════════════
#  S3：技能脚本执行必须过闸门（effect=execute）
# ════════════════════════════════════════════════════════════


class TestS3SkillExecGoesThroughGate:
    """改动前：routes_skills_mgmt → service → executor.py:202 subprocess.run 整条链路,
    既不 import `tool_gate`、也不在 EXEMPT_CALL_SITES 里（审计 S3 / Q1 §5.3）。"""

    SKILL_ID = "scripted-selftest"
    PARAMS = {"greeting": "hi", "count": 1}

    def _executor(self) -> SkillExecutor:
        return SkillExecutor(SkillFileStore())

    def test_执行前调用闸门且声明_effect_execute(self, monkeypatch, enforce_on):
        called = []
        real = G.check_declared_capability

        def _spy(capability, **kwargs):
            called.append((capability, kwargs))
            return real(capability, **kwargs)

        monkeypatch.setattr(G, "check_declared_capability", _spy)

        result = self._executor().execute(self.SKILL_ID, "main.py", params=dict(self.PARAMS))

        assert called, "技能脚本执行链路没有调用确认闸门（S3 未修）"
        capability, kwargs = called[0]
        assert capability == "skill." + self.SKILL_ID, capability
        assert kwargs.get("effect") == "execute", kwargs
        assert kwargs.get("plane") == "resident", kwargs
        assert result.success is False and result.exit_code == -1, result.to_dict()
        assert "APPROVAL_REQUIRED" in (result.error or ""), result.error

    def test_effect_execute_被拦且子进程没起(self, monkeypatch, enforce_on):
        result = self._executor().execute(self.SKILL_ID, "main.py", params=dict(self.PARAMS))

        assert result.success is False, result.to_dict()
        assert result.result is None, result.result
        assert result.stdout == "", "闸门拦下了却仍然起了子进程（stdout 非空）"
        assert result.timed_out is False
        assert "确认闸门拦截" in (result.error or ""), result.error

    def test_总开关为0时脚本照旧可执行(self, monkeypatch):
        """回归：不为了安全把这条正常路径封死（总开关=0 是操作员的显式回滚）"""
        monkeypatch.setenv(G.APPROVAL_ENFORCE_ENV, "0")

        result = self._executor().execute(self.SKILL_ID, "main.py", params=dict(self.PARAMS))

        assert result.success is True, result.to_dict()
        assert result.exit_code == 0, result.to_dict()
        assert isinstance(result.result, dict) and result.result.get("echo") == "hi", result.result

    def test_闸门不可用时拒绝执行(self, monkeypatch):
        """与 S2 同向的 fail-closed：这条链路只有一种能力类别（execute），证不出就拒"""
        _break_tool_gate(monkeypatch)

        result = self._executor().execute(self.SKILL_ID, "main.py", params=dict(self.PARAMS))

        assert result.success is False and result.result is None, result.to_dict()
        assert (SKILL_GATE_UNAVAILABLE_EVENT in (result.error or "")) or \
               ("闸门当前不可用" in (result.error or "")), result.error
        events = _chain(SKILL_GATE_UNAVAILABLE_EVENT)
        assert events and events[-1].payload.get("skill_id") == self.SKILL_ID, \
            events[-1].payload if events else None


# ════════════════════════════════════════════════════════════
#  闸门新入口本身的分级口径（S3 的"让闸门能判级"）
# ════════════════════════════════════════════════════════════


class TestDeclaredCapabilityGrading:
    def test_effect_execute_判为需确认(self, enforce_on):
        out = G.check_declared_capability("skill.demo", plane="resident",
                                          effect="execute", risk="low")

        assert out is not None and out.get("blocked") is True, out
        assert "L1" in str(out.get("reason") or ""), out

    def test_effect_read_且低危免确认(self, enforce_on):
        assert G.check_declared_capability("skill.demo", plane="resident",
                                           effect="read", risk="low") is None

    def test_非法_effect_按最严_L3(self, enforce_on):
        out = G.check_declared_capability("skill.demo", plane="resident",
                                          effect="launch_missiles", risk="low")

        assert out is not None and out.get("blocked") is True, out
        assert out.get("confirm_level") == "L3", out

    def test_治理平面直接判_L3(self, enforce_on):
        out = G.check_declared_capability("skill.demo", plane="govern",
                                          effect="read", risk="low")

        assert out is not None and out.get("blocked") is True, out
        assert out.get("confirm_level") == "L3", out

    def test_总开关为0时整体放行(self, monkeypatch):
        monkeypatch.setenv(G.APPROVAL_ENFORCE_ENV, "0")

        assert G.check_declared_capability("skill.demo", plane="govern",
                                           effect="extend", risk="critical") is None

    def test_能力标识为空直接放行交回调用方(self, enforce_on):
        assert G.check_declared_capability("", plane="resident",
                                           effect="execute", risk="low") is None
