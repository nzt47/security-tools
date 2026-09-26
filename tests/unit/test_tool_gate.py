"""集中式工具闸门（``agent/tool_gate.py``）单元测试

覆盖：
    1. 总开关 ``CP_TOOL_GATE_ENABLED`` 关闭 → 放行（即使命中黑名单）；
    2. ``denied_tools`` 命中 → 拒绝，且返回结构为
       ``{"ok","blocked","error_code","error"}``（``blocked=True`` /
       ``error_code="PERMISSION_DENIED"``）；
    3. ``denied_tools`` 含 ``"*"`` → 拒绝一切；
    4. 未命中黑名单 → 放行；多角色 ``denied_tools`` 并集生效；
    5. 描述符 ``trust.requires_approval=true`` → 拒绝，且工具原名与
       ``cp.<source>.<name>`` 形态**双向**都能命中；
    6. 策略/描述符文件缺失、JSON 损坏、结构异常 → 放行（fail-open）且不抛异常；
    7. 接线：``ToolCallingService._execute_safe`` 在闸门拒绝时**零次**实际执行
       （未进入 3 次重试循环），并原样返回拒绝结果。

纪律：**不触碰真实 ``data/`` 文件**——所有用例都把模块级常量
``POLICY_POLICIES_PATH`` / ``DESCRIPTORS_PATH`` 指向 ``tmp_path`` 下的构造文件。
"""
import json

import pytest
from unittest.mock import MagicMock, patch


# ════════════════════════════════════════════════════════════
#  fixtures
# ════════════════════════════════════════════════════════════

class _GateFiles:
    """把闸门的两个文件路径指向 tmp_path，并提供写入/调用辅助"""

    def __init__(self, module, policy_path, desc_path):
        self.mod = module
        self.policy_path = policy_path
        self.desc_path = desc_path

    def write_policy(self, data):
        self.policy_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def write_policy_raw(self, text):
        self.policy_path.write_text(text, encoding="utf-8")

    def write_descriptors(self, data):
        self.desc_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def write_descriptors_raw(self, text):
        self.desc_path.write_text(text, encoding="utf-8")

    def check(self, func_name, args=None):
        return self.mod.check_tool_call(func_name, args)


@pytest.fixture
def gate(tmp_path, monkeypatch):
    """把两个路径常量指向 tmp_path 的假文件；默认两份文件都是"空规则"

    【审批边界开关】``CP_TOOL_GATE_APPROVAL_ENFORCE`` **默认开启**（2026-09-17 反转：
    审批闭环接通后，拦截是一条能走通的路径——挂单 → 人工在收件箱裁决 → 原样重试即放行）。
    本夹具仍**显式**写上 ``=1``：让"审批边界生效"成为用例的显式前提，而不是依赖默认值
    （默认值的两态由 ``TestApprovalEnforceSwitch`` 单独钉住）。
    """
    import agent.tool_gate as G

    files = _GateFiles(G, tmp_path / "permission_policies.json",
                       tmp_path / "descriptors.json")
    monkeypatch.setattr(G, "POLICY_POLICIES_PATH", str(files.policy_path))
    monkeypatch.setattr(G, "DESCRIPTORS_PATH", str(files.desc_path))
    monkeypatch.delenv(G.GATE_ENABLED_ENV, raising=False)
    monkeypatch.setenv(G.APPROVAL_ENFORCE_ENV, "1")
    G._reset_cache()
    files.write_policy({"version": 1, "default_role": "guest", "roles": {}})
    files.write_descriptors({"schema_version": 1, "descriptors": {}})
    return files


def _descriptor_entry(cid, name, requires_approval):
    """最小描述符条目（只保留闸门读取的字段）"""
    return {
        "meta": {"id": cid},
        "capability": {"name": name},
        "trust": {"requires_approval": requires_approval},
    }


# ════════════════════════════════════════════════════════════
#  一、总开关
# ════════════════════════════════════════════════════════════

class TestGateSwitch:

    @pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "Off", " off "])
    def test_开关关闭时放行即使命中黑名单(self, gate, monkeypatch, value):
        """总开关关闭 → 无论策略怎么写都放行（含通配符黑名单）"""
        gate.write_policy({"roles": {"admin": {"denied_tools": ["*"]}}})
        monkeypatch.setenv(gate.mod.GATE_ENABLED_ENV, value)
        assert gate.check("write_file") is None

    def test_缺省视为开启(self, gate):
        """未设置环境变量 → 闸门生效（黑名单仍拦截）"""
        gate.write_policy({"roles": {"guest": {"denied_tools": ["write_file"]}}})
        result = gate.check("write_file")
        assert result is not None and result["blocked"] is True

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", ""])
    def test_开关开启时闸门生效(self, gate, monkeypatch, value):
        gate.write_policy({"roles": {"guest": {"denied_tools": ["write_file"]}}})
        monkeypatch.setenv(gate.mod.GATE_ENABLED_ENV, value)
        assert gate.check("write_file")["blocked"] is True


# ════════════════════════════════════════════════════════════
#  二、显式黑名单（roles[*].denied_tools 并集）
# ════════════════════════════════════════════════════════════

class TestDeniedTools:

    def test_命中黑名单即拒绝且结构兼容(self, gate):
        """拒绝结果必须与既有工具失败结果兼容，且带 blocked/error_code"""
        gate.write_policy({"roles": {"developer": {"allowed_tools": ["web_search"],
                                                   "denied_tools": ["write_file"]}}})
        result = gate.check("write_file", {"path": "x.txt"})
        assert result is not None
        assert set(result) == {"ok", "blocked", "error_code", "error"}
        assert result["ok"] is False
        assert result["blocked"] is True
        assert result["error_code"] == "PERMISSION_DENIED"
        assert "write_file" in result["error"]            # 含工具名
        assert "denied_tools" in result["error"]          # 含命中的规则来源

    def test_未命中黑名单放行(self, gate):
        gate.write_policy({"roles": {"developer": {"denied_tools": ["system_format"]}}})
        assert gate.check("read_file") is None
        assert gate.check("grep") is None

    def test_通配符拒绝一切(self, gate):
        gate.write_policy({"roles": {"admin": {"denied_tools": ["*"]}}})
        for name in ("read_file", "write_file", "grep", "edit", "shell_execute", "任意工具"):
            result = gate.check(name)
            assert result is not None, name
            assert result["blocked"] is True
            assert result["error_code"] == "PERMISSION_DENIED"
            assert name in result["error"]
        assert "通配符" in gate.check("read_file")["error"]

    def test_多角色_denied_tools_并集生效(self, gate):
        """并集：任一角色拉黑的工具都拦；都没拉黑的工具放行"""
        gate.write_policy({
            "roles": {
                "admin": {"allowed_tools": ["*"], "denied_tools": []},
                "developer": {"denied_tools": ["system_format"]},
                "guest": {"denied_tools": ["shell_execute", " "], "extra": 1},
                "auditor": {"denied_tools": ["write_file"]},
            }
        })
        assert gate.check("system_format")["blocked"] is True
        assert gate.check("shell_execute")["blocked"] is True
        assert gate.check("write_file")["blocked"] is True
        assert gate.check("read_file") is None
        assert gate.check(" ") is None          # 空白条目被忽略，且空名放行

    def test_黑名单写成_canonical_id_也能命中原名(self, gate):
        """策略里写 cp.<source>.<name>，用工具原名调用同样命中（双向等价）"""
        gate.write_policy({"roles": {"r": {"denied_tools": ["cp.builtin.write_file"]}}})
        assert gate.check("write_file")["blocked"] is True
        assert gate.check("cp.builtin.write_file")["blocked"] is True
        assert gate.check("read_file") is None

    def test_大小写不敏感(self, gate):
        gate.write_policy({"roles": {"r": {"denied_tools": ["Write_File"]}}})
        assert gate.check("write_file")["blocked"] is True


# ════════════════════════════════════════════════════════════
#  三、描述符审批要求（trust.requires_approval）
# ════════════════════════════════════════════════════════════

class TestRequiresApproval:

    def test_描述符要求审批即拒绝(self, gate):
        gate.write_descriptors({"descriptors": {
            "cp.builtin.write_file": _descriptor_entry("cp.builtin.write_file",
                                                       "write_file", True)}})
        result = gate.check("write_file")
        assert result is not None
        assert result["blocked"] is True
        # 2026-09-17 行为统一：描述符的 trust.requires_approval 与 YAML 的 needs_approval
        # 是同一语义，统一走"审批边界"⇒ 结构化拒绝的 error_code 是 APPROVAL_REQUIRED
        # （"要先走审批"），不再是 PERMISSION_DENIED（"不许用"）。
        assert result["error_code"] == "APPROVAL_REQUIRED"
        assert "write_file" in result["error"]                     # 工具名
        assert "requires_approval" in result["error"]              # 规则来源
        assert "cp.builtin.write_file" in result["error"]          # 命中的能力 id
        assert "审批" in result["error"]

    def test_canonical_id_形态与原名双向命中(self, gate):
        """台账 id 为 cp.<source>.<name>：原名调用命中；id 形态调用亦命中"""
        gate.write_descriptors({"descriptors": {
            "cp.mcp.write_file": _descriptor_entry("cp.mcp.write_file",
                                                   "write_file", True)}})
        assert gate.check("write_file")["blocked"] is True
        assert gate.check("cp.mcp.write_file")["blocked"] is True
        assert gate.check("cp.builtin.write_file")["blocked"] is True   # 名字段同源
        assert gate.check("read_file") is None

    def test_描述符列表形态也能命中(self, gate):
        """台账为 list[entry]（id 取自 meta.id）时同样生效"""
        gate.write_descriptors([_descriptor_entry("cp.builtin.shell_execute",
                                                  "shell_execute", True)])
        assert gate.check("shell_execute")["blocked"] is True
        assert gate.check("read_file") is None

    def test_requires_approval_为假时放行(self, gate):
        """描述符 `requires_approval=false` ⇒ **该条规则本身**不拦截

        【2026-09-20 契约更新：因为 L0–L3 落地（TASK-06）】
          原用例断言的是 `gate.check("write_file") is None`。该断言在 L0–L3 之后不再
          成立 —— 变的**不是描述符规则**，而是 `write_file` 的确认级别改由
          `data/tool_definitions/write_file.yaml` 的治理三轴派生（`risk: high` ⇒ **L2**）
          ⇒ 即便描述符写着"不需要审批"，它仍要进审批。
          故本用例改用 **L0 工具（`read_file`）** 断言"描述符的 false 不拦"这一**原始
          意图**；新契约（`write_file` 仍被拦，且拦它的不是描述符）由下一个用例单独钉住。
        """
        gate.write_descriptors({"descriptors": {
            "cp.builtin.read_file": _descriptor_entry("cp.builtin.read_file",
                                                      "read_file", False)}})
        assert gate.check("read_file") is None

    def test_描述符写_false_也拦不住_L2_工具(self, gate):
        """【2026-09-20 新增契约】描述符的 false 不是高危工具的"免检证"

        拦截必须发生在**描述符这一步之后**，且理由来自 YAML 派生而非描述符 ——
        否则"把 descriptors.json 里的 requires_approval 改成 false"就成了
        "关掉高危工具审批"的后门（数据文件可写 ⇒ 治理可被无声放宽）。
        """
        gate.write_descriptors({"descriptors": {
            "cp.builtin.write_file": _descriptor_entry("cp.builtin.write_file",
                                                       "write_file", False)}})
        result = gate.check("write_file")
        assert result is not None and result["blocked"] is True
        assert result["error_code"] == "APPROVAL_REQUIRED"
        assert "confirm_level=L2" in result["reason"], result["reason"]
        assert "requires_approval" not in result["error"], \
            "理由不该来自描述符（它写的是 false）"

    def test_字符串_true_也算要求审批(self, gate):
        """宽松判定：数据侧写成 "true" 同样拦截（不认识的值才放行）"""
        gate.write_descriptors({"descriptors": {
            "cp.builtin.write_file": _descriptor_entry("cp.builtin.write_file",
                                                       "write_file", "true")}})
        assert gate.check("write_file")["blocked"] is True

    def test_黑名单与审批两规则互不干扰(self, gate):
        """只有描述符规则命中时，报错来源必须是描述符而非黑名单"""
        gate.write_policy({"roles": {"r": {"denied_tools": ["system_format"]}}})
        gate.write_descriptors({"descriptors": {
            "cp.builtin.write_file": _descriptor_entry("cp.builtin.write_file",
                                                       "write_file", True)}})
        assert "requires_approval" in gate.check("write_file")["error"]
        assert "denied_tools" in gate.check("system_format")["error"]
        assert gate.check("read_file") is None


class TestApprovalEnforceSwitch:
    """审批边界开关的两态：**默认拦截** / 显式关闭才只告警（2026-09-17 反转后的口径）

    【为什么单独钉住"默认口径"】``CP_TOOL_GATE_APPROVAL_ENFORCE`` 默认**开启**：
    审批闭环（挂单 → 人工在收件箱裁决 → 原样重试即放行，单次有效）接通后，
    "默认不拦"的理由（"拦了就没法用"）不再成立。这是**安全姿态**，必须显式可断言：
    若哪天默认又被改成不拦，本用例会失败，提醒同步 ``agent/settings/registry.py``
    的条目与部署侧开关。
    """

    @staticmethod
    def _write_approval_descriptor(gate):
        gate.write_descriptors({"descriptors": {
            "cp.builtin.write_file": _descriptor_entry(
                "cp.builtin.write_file", "write_file", True)}})

    def test_默认即拦截(self, gate, monkeypatch):
        import agent.tool_gate as G

        monkeypatch.delenv(G.APPROVAL_ENFORCE_ENV, raising=False)
        assert G._approval_enforce_enabled() is True
        self._write_approval_descriptor(gate)
        result = gate.check("write_file")
        assert result is not None and result["blocked"] is True
        assert result["error_code"] == "APPROVAL_REQUIRED"

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " on "])
    def test_显式打开即拦截(self, gate, monkeypatch, value):
        import agent.tool_gate as G

        monkeypatch.setenv(G.APPROVAL_ENFORCE_ENV, value)
        assert G._approval_enforce_enabled() is True
        self._write_approval_descriptor(gate)
        result = gate.check("write_file")
        assert result is not None and result["blocked"] is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off"])
    def test_显式关闭才不拦截(self, gate, monkeypatch, value):
        """回滚口径：置 0/false/no/off ⇒ 回到"只告警、照常放行"（一处环境变量即回滚）"""
        import agent.tool_gate as G

        monkeypatch.setenv(G.APPROVAL_ENFORCE_ENV, value)
        assert G._approval_enforce_enabled() is False
        self._write_approval_descriptor(gate)
        assert gate.check("write_file") is None

    def test_空值按默认处理即拦截(self, gate, monkeypatch):
        """设了但为空串 ⇒ 视同未设置（默认拦截）。

        刻意不把空串当作"关闭"：关闭必须是一个**明确写下**的假值，
        否则"环境变量拼错/被清空"会静默把审批边界关掉。
        """
        import agent.tool_gate as G

        monkeypatch.setenv(G.APPROVAL_ENFORCE_ENV, "   ")
        assert G._approval_enforce_enabled() is True
        self._write_approval_descriptor(gate)
        assert gate.check("write_file") is not None


# ════════════════════════════════════════════════════════════
#  四、fail-open：文件缺失 / JSON 损坏 / 结构异常 / 自身异常
# ════════════════════════════════════════════════════════════

class TestFailOpen:
    """**运行期策略层**的 fail-open：文件缺失 / JSON 损坏 / 结构异常 / 自身异常

    【2026-09-20 契约更新：因为 L0–L3 落地（TASK-06）——这是本次最大的一处改动】
      本类原先所有用例都用 `write_file` 断言 `gate.check(...) is None`。那是**旧契约**
      （`risk: high` 不触发任何确认）。L0–L3 之后 `write_file` 派生为 **L2**，
      于是 9 条用例同时变红。逐条判断的结论是：**被测的意图没变、被测的对象变了** ——

      · 本类的意图 = "**策略/描述符这两个运行期文件**读不到时，那两条规则不拦人"
        （`check_tool_call` 的 fail-open 纪律）。它与 `confirm_level` **是两层东西**：
        confirm_level 来自 `data/tool_definitions/*.yaml`（设计期声明，D1 的单一真相源），
        **根本不读**那两个文件。
      · 故断言改用 **L0 工具（`read_file`）**：它既不受 L2 影响，也仍然经过策略/描述符
        两步 ⇒ 原意图被**原样保留**。
      · 同时用 `_assert_l2_still_blocked()` **显式钉住**新契约：`write_file` 在该场景下
        仍要确认。这是刻意的（见 `agent/tool_gate.py::_confirm_level_outcome` 的
        "任务 B 裁决"一节）：让"删掉一个文件"成为"13 个高危工具免确认"的开关，
        等于给治理面开一条无声旁路。
    """

    #: L0 工具（`effect: read` + `risk: low` ⇒ 免确认）：用来隔离"策略层 fail-open"这一件事
    L0_TOOL = "read_file"
    #: L2 工具（`risk: high` ⇒ 逐次确认）：用来钉住"conflict_level 不受文件缺失影响"
    L2_TOOL = "write_file"

    @staticmethod
    def _assert_l2_still_blocked(gate):
        """【任务 B 裁决的锁定】策略/描述符文件异常时，L2 工具**仍然**要求确认

        判据的不对称是刻意的：**"依据读不到" ≠ "策略不存在"**。
        """
        result = gate.check(TestFailOpen.L2_TOOL)
        assert result is not None and result["blocked"] is True, \
            "confirm_level 是设计期声明（YAML），不该被运行期策略文件缺失削弱"
        assert result["error_code"] == "APPROVAL_REQUIRED"
        assert "confirm_level=L2" in result["reason"], result["reason"]

    def test_策略文件缺失放行(self, gate):
        gate.policy_path.unlink()
        gate.mod._reset_cache()
        assert gate.check(self.L0_TOOL) is None
        self._assert_l2_still_blocked(gate)

    def test_描述符文件缺失放行(self, gate):
        gate.desc_path.unlink()
        gate.mod._reset_cache()
        assert gate.check(self.L0_TOOL) is None
        self._assert_l2_still_blocked(gate)

    def test_两个文件都缺失且目录也不存在时放行(self, gate, monkeypatch, tmp_path):
        monkeypatch.setattr(gate.mod, "POLICY_POLICIES_PATH",
                            str(tmp_path / "nope" / "a.json"))
        monkeypatch.setattr(gate.mod, "DESCRIPTORS_PATH",
                            str(tmp_path / "nope" / "b.json"))
        gate.mod._reset_cache()
        assert gate.check(self.L0_TOOL) is None
        self._assert_l2_still_blocked(gate)

    def test_JSON_损坏放行(self, gate):
        gate.write_policy_raw("{这不是 JSON")
        gate.write_descriptors_raw("[1, 2,")
        gate.mod._reset_cache()
        assert gate.check(self.L0_TOOL) is None
        self._assert_l2_still_blocked(gate)

    def test_策略结构异常放行(self, gate):
        for bad in ([1, 2, 3], "text", 42, {"roles": ["not-a-dict"]},
                    {"roles": {"r": "not-a-dict"}}, {"roles": {"r": {"denied_tools": "x"}}},
                    {"roles": {"r": {"denied_tools": [None, 7, {"a": 1}]}}}):
            gate.write_policy(bad)
            gate.mod._reset_cache()
            assert gate.check(self.L0_TOOL) is None, bad

    def test_描述符结构异常放行(self, gate):
        for bad in ("text", 42, {"descriptors": "not-a-dict"},
                    {"descriptors": [None, 7, "x"]},
                    {"descriptors": {"cp.x.y": "not-a-dict"}},
                    {"descriptors": {"cp.x.y": {"trust": "not-a-dict"}}}):
            gate.write_descriptors(bad)
            gate.mod._reset_cache()
            assert gate.check(self.L0_TOOL) is None, bad

    def test_闸门自身异常时放行(self, gate, monkeypatch):
        """双保险：判定内部抛异常时，**只读/低危**工具仍放行

        【2026-09-20 契约更新：因为 L0–L3 落地（TASK-06 §1 缺陷 4）】
          原用例用 `write_file` 断言"异常必放行"。TASK-06 把异常路径改为**按工具性质
          分流**：治理动作 fail-closed，其余 fail-open（理由见 `check_tool_call` 的
          except 块：对写/删/改能力集的动作，"依据读不到就放行"的代价无上界）。
          `write_file` 现在属治理动作 ⇒ 它的断言搬到下一个用例（并改判为拒绝）。
          本用例保留原意图（闸门自身 bug 不得阻断日常读取），对象改为 `read_file`。
        """
        def _boom(_data):
            raise RuntimeError("builder down")

        monkeypatch.setattr(gate.mod, "_build_denied_union", _boom)
        gate.mod._reset_cache()
        assert gate.check(self.L0_TOOL) is None

    def test_闸门自身异常时治理动作_fail_closed(self, gate, monkeypatch):
        """【2026-09-20 新增契约】闸门内部抛异常时的**治理动作**必须被拒绝

        TASK-06 §1 缺陷 4："工具闸门任何异常一律 fail-open 放行"⇒ 改为治理动作
        fail-closed。否则"让一次判定抛异常"就是一条绕过治理的现成路径。
        判据来自 YAML（`needs_approval` / L3），**且元数据读不到时也按拒绝处置**
        （"证不出它无害"正是更该拒绝的情形）。
        """
        def _boom(_data):
            raise RuntimeError("builder down")

        monkeypatch.setattr(gate.mod, "_build_denied_union", _boom)
        gate.mod._reset_cache()
        result = gate.check(self.L2_TOOL)
        assert result is not None and result["blocked"] is True, \
            "治理动作在闸门自身异常时必须 fail-closed，不得放行"
        assert result["error_code"] == "PERMISSION_DENIED"
        assert "fail-closed" in result["error"], result["error"]

    def test_环境变量读取异常时不影响放行(self, gate, monkeypatch):
        """只替换闸门模块内的 ``os`` 名字绑定，不动进程级 ``os.environ``"""
        import os as _os

        class _BadEnviron(dict):
            def get(self, *a, **k):
                raise RuntimeError("env down")

        class _OsShim:
            environ = _BadEnviron()

            def __getattr__(self, name):
                return getattr(_os, name)

        monkeypatch.setattr(gate.mod, "os", _OsShim())
        assert gate.check("read_file") is None

    def test_空工具名放行(self, gate):
        gate.write_policy({"roles": {"r": {"denied_tools": ["*"]}}})
        assert gate.check("") is None
        assert gate.check(None) is None

    def test_不抛异常且只读不写(self, gate):
        """调用前后两份文件字节不变（闸门绝不修改数据文件）"""
        gate.write_policy({"roles": {"r": {"denied_tools": ["write_file"]}}})
        before = (gate.policy_path.read_bytes(), gate.desc_path.read_bytes())
        gate.check("write_file")
        gate.check("read_file")
        after = (gate.policy_path.read_bytes(), gate.desc_path.read_bytes())
        assert before == after

    def test_缓存按文件指纹失效(self, gate):
        """改写策略文件后立即生效（缓存必须按指纹失效）

        【2026-09-20 契约更新：因为 L0–L3 落地】原用例用 `write_file`：清空黑名单后
        再调它，期望 `None`。而 `write_file` 现在**另有一层** YAML 派生的 L2 确认
        ⇒ "黑名单已清空"这件事被 L2 挡住，测不到缓存失效。
        改用不受确认级别影响的 `grep`（L0），原意图不变。
        """
        gate.write_policy({"roles": {"r": {"denied_tools": ["grep"]}}})
        assert gate.check("grep")["blocked"] is True
        gate.write_policy({"roles": {"r": {"denied_tools": []}}})
        assert gate.check("grep") is None


# ════════════════════════════════════════════════════════════
#  五、接线：唯一汇聚点 tools.call()（含 orchestrator 直连路径）
# ════════════════════════════════════════════════════════════

#: 探针工具名（注册进真实注册表，用后精确还原）
PROBE_TOOL = "probe_gate_tool"


def _make_service(monkeypatch):
    """构造 ToolCallingService，并把 trace recorder 关掉（不落任何 trace 库）"""
    from agent.tool_calling import ToolCallingService

    service = ToolCallingService(llm_service=MagicMock(model="test-model"))

    def _no_recorder(*_a, **_k):
        raise RuntimeError("recorder disabled in unit test")

    from agent.observability.tool_trace import ToolTraceRecorder
    monkeypatch.setattr(ToolTraceRecorder, "instance", staticmethod(_no_recorder))
    return service


@pytest.fixture
def probe_tool():
    """注册一个计数探针工具到真实注册表；用完还原注册表与健康表"""
    from agent import tools as registry

    calls = {"n": 0}

    def _handler(**kwargs):
        calls["n"] += 1
        return {"ok": True, "result": "handler-ran", "echo": kwargs}

    saved_entry = registry._registry.get(PROBE_TOOL)
    saved_health = registry._tool_health.get(PROBE_TOOL)
    registry.register(PROBE_TOOL, "闸门接线探针工具", handler=_handler)
    try:
        yield calls
    finally:
        if saved_entry is None:
            registry.unregister(PROBE_TOOL)
        else:
            registry._registry[PROBE_TOOL] = saved_entry
            registry._registry_version += 1
        if saved_health is None:
            registry._tool_health.pop(PROBE_TOOL, None)
        else:
            registry._tool_health[PROBE_TOOL] = saved_health


class TestToolsCallIsEnforcementPoint:
    """完整性用例：绕过 ``_execute_safe`` 的直连路径同样受闸门约束

    ``agent/orchestrator/orchestrator.py:3519`` / ``:3557`` 直接调
    ``_tools.call(_fn_name, **_fn_args)``，**不经过** ``_execute_safe``；强制点
    因此必须落在唯一汇聚点 ``agent/tools/__init__.py::call()`` 上。
    """

    def test_直连_tools_call_被闸门拒绝(self, gate):
        """orchestrator 直连形态（位置参数工具名）"""
        from agent import tools as registry

        gate.write_policy({"roles": {"guest": {"denied_tools": ["read_file"]}}})
        result = registry.call("read_file", path="x.txt")

        assert result["ok"] is False
        assert result["blocked"] is True
        assert result["error_code"] == "PERMISSION_DENIED"
        assert "read_file" in result["error"]

    def test_直连_tools_call_被闸门拒绝_关键字名形态(self, gate):
        """``call(**params_with_name)`` 形态同样被拦"""
        from agent import tools as registry

        gate.write_policy({"roles": {"r": {"denied_tools": ["write_file"]}}})
        result = registry.call(name="write_file", path="x.txt", content="c")

        assert result["blocked"] is True
        assert result["error_code"] == "PERMISSION_DENIED"

    def test_拒绝发生在注册表查找与自动发现之前(self, gate, monkeypatch):
        """未注册工具命中闸门时返回拒绝 dict，而不是抛 ToolError"""
        from agent import tools as registry

        discovery = MagicMock()
        monkeypatch.setattr(registry, "_discovery_service", discovery)
        gate.write_policy({"roles": {"r": {"denied_tools": ["never_registered_tool_x"]}}})
        assert "never_registered_tool_x" not in registry._registry

        result = registry.call("never_registered_tool_x")

        assert result["blocked"] is True
        assert result["error_code"] == "PERMISSION_DENIED"
        discovery.on_tool_not_found.assert_not_called()

    def test_描述符审批也在直连路径生效(self, gate, probe_tool):
        from agent import tools as registry

        cid = "cp.builtin." + PROBE_TOOL
        gate.write_descriptors({"descriptors": {
            cid: _descriptor_entry(cid, PROBE_TOOL, True)}})

        result = registry.call(PROBE_TOOL, a=1)

        assert result["blocked"] is True
        assert result["error_code"] == "APPROVAL_REQUIRED"   # 走审批边界，非硬拒绝
        assert probe_tool["n"] == 0          # handler 未被调用

    def test_闸门排在限流之前(self, gate, monkeypatch, probe_tool):
        """拒绝要尽早：即使限流器已超限，闸门拒绝也先返回且不触碰限流器"""
        from agent import tools as registry

        limiter = MagicMock()
        limiter.check.return_value = False   # 若走到限流，会返回"频率过高"
        monkeypatch.setattr(registry, "_rate_limiter", limiter)
        gate.write_policy({"roles": {"r": {"denied_tools": [PROBE_TOOL]}}})

        result = registry.call(PROBE_TOOL)

        assert result["error_code"] == "PERMISSION_DENIED"
        limiter.check.assert_not_called()
        assert probe_tool["n"] == 0

    def test_放行时_call_行为不变(self, gate, monkeypatch, probe_tool):
        """零行为回归：未被拦时限流、健康追踪与返回值与接线前一致

        【2026-09-18】本用例**显式关掉审批边界**：探针工具是"已注册但无 YAML 元数据"
        的工具，而新的 HITL 兜底网（`agent/tool_gate.py::_hitl_boundary`）正对这类情形
        fail-closed ⇒ 不关掉它，测到的是兜底网而不是"放行路径的机制"。
        兜底网自身的行为由 `tests/unit/test_tool_gate_fallback.py` 专门覆盖。
        """
        monkeypatch.setenv(gate.mod.APPROVAL_ENFORCE_ENV, "0")
        from agent import tools as registry

        limiter = MagicMock()
        limiter.check.return_value = True
        monkeypatch.setattr(registry, "_rate_limiter", limiter)

        result = registry.call(PROBE_TOOL, a=1)

        assert result["ok"] is True and result["result"] == "handler-ran"
        assert result["echo"] == {"a": 1}
        assert probe_tool["n"] == 1
        limiter.check.assert_called_once_with(PROBE_TOOL)
        assert registry._tool_health[PROBE_TOOL]["last_ok"] is True

    def test_未知工具的_ToolError_语义不变(self, gate):
        from agent import tools as registry

        with pytest.raises(registry.ToolError):
            registry.call("definitely_not_registered_tool_xyz")

    def test_缺失工具名的_ToolError_语义不变(self, gate):
        from agent import tools as registry

        with pytest.raises(registry.ToolError):
            registry.call()

    def test_开关关闭时直连路径也放行(self, gate, monkeypatch, probe_tool):
        from agent import tools as registry

        gate.write_policy({"roles": {"r": {"denied_tools": [PROBE_TOOL]}}})
        monkeypatch.setenv(gate.mod.GATE_ENABLED_ENV, "off")

        result = registry.call(PROBE_TOOL)

        assert result["ok"] is True
        assert probe_tool["n"] == 1

    def test_闸门模块导入失败时未登记工具被拒_fail_closed(self, probe_tool):
        """闸门不可用（导入异常）→ **fail-closed**：判不出级别的工具按最严拒绝。

        [A2/R3 契约变更 2026-09-25] 本用例原为 `test_闸门模块导入失败时_call_照常执行`，
        断言 `result["ok"] is True` —— 那是在钉住**旧的 fail-open 行为**（审计 S2：
        `tools/__init__.py` 把闸门 import 写在 try 内，异常即全量放行，含 L3）。
        该行为已被 A2 明确改为 fail-closed（见 `docs/audit_skill_governance/A2.md`），
        故本用例随之更新为断言新契约。

        `PROBE_TOOL` 经 `registry.register(...)` 登记且**无 YAML 元数据** ⇒
        `_confirm_level_without_gate()` 判不出级别 ⇒ 按 `L3` 处置 ⇒ 拒绝。
        放行面（L0/L1 在闸门不可用时仍放行）由 `test_confirm_gate_no_bypass.py`
        的 `TestS2GateImportFailureIsFailClosed` 覆盖，本用例只钉住拒绝侧。
        """
        import sys
        from agent import tools as registry

        with patch.dict(sys.modules, {"agent.tool_gate": None}):
            result = registry.call(PROBE_TOOL)

        assert result["ok"] is False, result
        assert result.get("blocked") is True, result
        assert probe_tool["n"] == 0, "被拒的工具**不得**执行 handler"


class TestExecuteSafeRetrySemantics:
    """``_execute_safe`` 对"拒绝"只尝试一次（拒绝不会被重试 3 次）

    既有控制流只在**异常**时重试——``for attempt in range(3)`` 里的
    ``return result`` 是无条件的；闸门拒绝是**返回 dict**而非抛异常，因此天然
    只走一次。本用例把这条语义钉死：core 调 1 次、闸门调 1 次、handler 0 次。
    """

    def test_拒绝只尝试一次_闸门只被调用一次(self, gate, monkeypatch, probe_tool):
        import agent.tool_gate as G

        gate.write_policy({"roles": {"r": {"denied_tools": [PROBE_TOOL]}}})
        service = _make_service(monkeypatch)

        counts = {"core": 0, "gate": 0}
        real_core = service._execute_safe_core
        monkeypatch.setattr(
            service, "_execute_safe_core",
            lambda fn, a: (counts.__setitem__("core", counts["core"] + 1),
                           real_core(fn, a))[1])
        real_check = G.check_tool_call
        monkeypatch.setattr(
            G, "check_tool_call",
            lambda *a, **k: (counts.__setitem__("gate", counts["gate"] + 1),
                             real_check(*a, **k))[1])

        result = service._execute_safe(PROBE_TOOL, {})

        assert counts == {"core": 1, "gate": 1}      # 不是 3 次
        assert probe_tool["n"] == 0                  # handler 从未执行
        assert result["ok"] is False and result["blocked"] is True
        assert result["error_code"] == "PERMISSION_DENIED"
        assert result == real_check(PROBE_TOOL, {})  # 原样返回

    def test_经_execute_safe_放行时正常执行一次(self, gate, monkeypatch, probe_tool):
        """同上：显式关掉审批边界，只验 `_execute_safe` 的"放行即执行一次"机制"""
        monkeypatch.setenv(gate.mod.APPROVAL_ENFORCE_ENV, "0")
        service = _make_service(monkeypatch)

        result = service._execute_safe(PROBE_TOOL, {"x": 1})

        assert result["ok"] is True
        assert probe_tool["n"] == 1

    def test_经_execute_safe_通配符拒绝时一次也不执行(self, gate, monkeypatch, probe_tool):
        gate.write_policy({"roles": {"admin": {"denied_tools": ["*"]}}})
        service = _make_service(monkeypatch)

        result = service._execute_safe(PROBE_TOOL, {})

        assert probe_tool["n"] == 0
        assert result["blocked"] is True

    def test_经_execute_safe_描述符审批拒绝(self, gate, monkeypatch, probe_tool):
        cid = "cp.builtin." + PROBE_TOOL
        gate.write_descriptors({"descriptors": {
            cid: _descriptor_entry(cid, PROBE_TOOL, True)}})
        service = _make_service(monkeypatch)

        result = service._execute_safe(PROBE_TOOL, {})

        assert probe_tool["n"] == 0
        assert result["error_code"] == "APPROVAL_REQUIRED"   # 走审批边界，非硬拒绝
