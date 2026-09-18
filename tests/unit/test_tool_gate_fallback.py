"""工具闸门的**兜底判据**：HITL fail-closed 网 + 伦理硬规则（2026-09-18 接线）

背景（两条都是实测出来的"文档承诺了、代码里不存在"）：

1. `agent/tool_gate.py` 的模块 docstring 一直写着"真正的 fail-closed 由
   `agent/human_in_the_loop/hitl.py::HITLManager.assess` 承担（未登记工具 ⇒ HIGH）"，
   而那一层**在生产里零调用方**（全仓只有测试引用）⇒ 对"已注册但 `data/tool_definitions/`
   没有条目的工具"（动态生成、外部接入）两处都不拦：**兜底网根本不存在**。
2. `agent/human_in_the_loop/ethics.py::EthicsEngine` 自称"不可突破的硬约束"（禁
   `rm -rf /`、禁格式化、禁关机…），实测同样零生产调用方 ⇒ 6 条硬规则一条都没生效。

本文件把接线后的**契约**钉住，重点是三条边界：
- **只补 YAML 覆盖不到的那一块**：已登记工具仍以 YAML 为唯一权威（否则 `write_file`
  这类 `risk: high` 但 `needs_approval=False` 的工具会被推去审批，治理变骚扰）；
- **名字不存在时不挂单**：那是拼错工具名，正确反馈是 `ToolError`，不该让人去批一个
  不存在的工具；
- **伦理只查「会造成后果」的工具**：其规则是子串匹配（误报面大），对读类工具生效会
  变成纯噪声。
"""
from __future__ import annotations

import pytest

from agent import tools as registry
import agent.tool_gate as G

#: 已注册、但**故意不给 YAML 元数据**的探针（模拟动态生成 / 外部接入的工具）
UNMETA_TOOL = "probe_no_metadata_tool"

#: 必定不存在的工具名（用于验 ToolError 语义不被兜底网改写）
GHOST_TOOL = "definitely_not_registered_tool_xyz"


def _pending_count() -> int:
    """当前审批收件箱里 ``tool_call`` 待办条数（会话级已隔离到临时目录）"""
    import agent.tool_approval as TA
    return int(TA.pending_snapshot().get("count") or 0)


@pytest.fixture(autouse=True)
def _enforce_on(monkeypatch):
    """默认开启审批边界（本文件的判据只在"边界生效"时有意义）"""
    monkeypatch.delenv(G.GATE_ENABLED_ENV, raising=False)
    monkeypatch.setenv(G.APPROVAL_ENFORCE_ENV, "1")
    G._reset_cache()
    yield
    G._reset_cache()


@pytest.fixture
def meta_less_tool():
    """注册一个没有 YAML 元数据的探针工具；用完精确还原注册表"""
    calls = {"n": 0}

    def _handler(**kwargs):
        calls["n"] += 1
        return {"ok": True, "result": "handler-ran"}

    saved = registry._registry.get(UNMETA_TOOL)
    saved_health = registry._tool_health.get(UNMETA_TOOL)
    registry.register(UNMETA_TOOL, "无元数据探针（兜底网用例）", handler=_handler)
    try:
        yield calls
    finally:
        if saved is None:
            registry.unregister(UNMETA_TOOL)
        else:
            registry._registry[UNMETA_TOOL] = saved
            registry._registry_version += 1
        if saved_health is None:
            registry._tool_health.pop(UNMETA_TOOL, None)
        else:
            registry._tool_health[UNMETA_TOOL] = saved_health


class TestHitlFailClosedNet:
    """已注册但无元数据的工具：fail-closed（原为**直接放行**）"""

    def test_已注册但无元数据的工具要审批(self, meta_less_tool):
        result = registry.call(UNMETA_TOOL, a=1)

        assert result["blocked"] is True, f"无元数据工具未走审批: {result}"
        assert result["error_code"] == "APPROVAL_REQUIRED"
        assert "未登记元数据" in result["reason"]
        assert result["approval_id"], "未挂单 ⇒ 人工无从批准，等于永久不可用"
        assert meta_less_tool["n"] == 0, "被拦的调用不得执行 handler"

    def test_关闭开关后兜底网只告警(self, meta_less_tool, monkeypatch):
        """一处环境变量即可整体回滚（与审批边界的既有口径一致）"""
        monkeypatch.setenv(G.APPROVAL_ENFORCE_ENV, "0")

        result = registry.call(UNMETA_TOOL, a=1)

        assert result.get("ok") is True and meta_less_tool["n"] == 1


class TestYamlStaysAuthoritative:
    """兜底网**不得**越权：已登记工具一律由 YAML 决定要不要审批"""

    @pytest.mark.parametrize("tool,args", [
        ("write_file", {"path": "a.txt", "content": "x"}),
        ("edit", {"path": "a.txt", "old": "a", "new": "b"}),
        ("apply_patch", {"patch": "--- a\\n+++ b\\n"}),
        ("git", {"args": ["status"]}),
    ])
    def test_risk_high_但无需审批的工具不被推去审批(self, tool, args):
        """`write_file`/`edit`/`git` 等是 `risk: high` 但 `needs_approval=False`：
        若把 HITL 的 HIGH 也搬进来，日常写文件都会要人点确认 —— 那是把治理做成骚扰。"""
        assert G.check_tool_call(tool, args) is None


class TestEthicsRulesAreLive:

    def test_执行类工具命中伦理规则要审批(self):
        """`run_program`（effect=execute, needs_approval=False）带关机参数 ⇒ 伦理规则命中"""
        result = G.check_tool_call("run_program", {"program": "shutdown -h now"})

        assert result is not None and result["blocked"] is True, \
            "伦理硬规则没生效（EthicsEngine 仍是死代码）"
        assert "伦理" in result["reason"], result["reason"]
        assert "E003" in result["reason"], "未指出命中的规则 id，人无法判断该不该批"

    def test_读类工具不查伦理避免噪声(self):
        """`grep` 的 pattern 里出现 shutdown 是正常排查动作，不该因此要审批"""
        assert G.check_tool_call("grep", {"pattern": "shutdown", "path": "logs"}) is None

    def test_写类工具不查伦理(self):
        """`write_file`（effect=write）不查：伦理规则面向"造成后果的执行"，写类由 YAML 管"""
        assert G.check_tool_call("write_file", {"path": "x", "content": "shutdown"}) is None


class TestGhostToolSemantics:
    """拼错名字的语义：报"未知工具"，而不是挂一张让人白批的单"""

    def test_不存在的工具仍抛_ToolError_而非挂单(self):
        before = _pending_count()

        with pytest.raises(registry.ToolError):
            registry.call(GHOST_TOOL)

        assert _pending_count() == before, \
            "为不存在的工具挂单 ⇒ 人工会看到一张批了也没用的单"
