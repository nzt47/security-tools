"""策略文件 ↔ 工具注册表 一致性测试（防「错位工具名」死配置复发）

被守护的不变量（``data/permission_policies.json``）：

1. ``roles[*].allowed_tools`` / ``denied_tools``、``abac_rules[*].tool`` 中的工具名必须
   真实存在于工具注册表（以 ``data/tool_definitions/*.yaml`` 的 ``name`` 为准）；
   ``"*"`` 通配除外。
2. 角色键必须是 ``agent.permission_system.Role`` 的合法取值——否则 ``_load_policies``
   会**静默跳过**该角色（``permission_system.py:569-576``），该角色所有工具随即被拒。
3. ``default_role`` 必须是合法角色，且出现在 ``roles`` 中。
4. ``abac_rules`` 规则名唯一；``_inactive_rules`` 与 ``abac_rules`` 不得重名。
5. ``_inactive_rules``（备忘区）的目标工具必须**未注册**——它正是因为目标不存在才被移出
   活规则；若将来注册了同名工具，本测试会失败并要求把它移回 ``abac_rules``。
6. ``_inactive_rules`` 每条必须带非空 ``inactive_reason``（移出时必须留下理由）。
7. ``_policy_notes``（顶层**文档键**，加载器与其它断言都不读）：
   提到的规则名**不得**同时出现在 ``abac_rules``（防"一边备忘已禁用、一边仍在生效"），
   且每条必须写清 ``rule`` / ``disabled_reason`` / ``reenable_hint``。
   存在的理由：``_inactive_rules`` 只收"目标工具**未注册**"的规则，**已注册**工具的
   策略被停用时没有地方留档（首例：``off-hours-shell-restriction``，见文件内注释）。

为什么存在（真实事故）：
    修复前 ``developer.allowed_tools`` 写的是 ``file_read`` / ``file_write`` /
    ``code_runner`` / ``browser_navigate``，``guest.allowed_tools`` 写的是 ``file_read``
    ——这些名字在注册表里**都不存在**（真实名是 ``read_file`` / ``write_file``）；两条
    ABAC 规则 ``internal-only-format`` / ``admin-shutdown-internal-only`` 的目标
    ``system_format`` / ``system_shutdown`` 也不存在 ⇒ 规则永不触发。
    后果：RBAC 是严格白名单语义（``permission_system.py:823-867``），``default_role`` 又是
    ``guest``，故严格模式下 72 个工具只放行 1 个（封杀 98.6%，连 ``read_file`` 都不放过），
    且那两条「格式化/关机仅限内网」的安全约束是**静默失效**的——配置看起来在，实际不管事。

【不易】只读数据文件，不写盘；不依赖运行时注册表（工具在启动期由 lifecycle 注册，测试期
        注册表为空），故以 ``data/tool_definitions/*.yaml`` 作为注册表口径——该口径本身由
        ``tests/unit/test_tool_definitions_yaml.py`` 与 Python ``@register`` 逐条对齐守护。
【变易】新增角色 / ABAC 规则 / 备忘规则会被自动纳入校验，无需改本文件。
【简易】纯 ``json`` + ``pathlib`` + ``difflib``；无网络、无第三方依赖。
"""
from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Dict, List, Set, Tuple

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_POLICY_PATH = _PROJECT_ROOT / "data" / "permission_policies.json"
_DEFS_DIR = _PROJECT_ROOT / "data" / "tool_definitions"

_WILDCARD = "*"
_NAME_RE = re.compile(r"^name:\s*(\S+)\s*$")


def _load_policy() -> dict:
    """读取策略文件（UTF-8；CRLF 由 open 自动处理）"""
    return json.loads(_POLICY_PATH.read_text(encoding="utf-8"))


def _real_tool_names() -> Set[str]:
    """工具注册表口径：``data/tool_definitions/*.yaml`` 里的 ``name`` 字段

    使用文本行匹配而非 yaml 解析：只需 ``name``，且避免为一个字段引入解析依赖。
    """
    names: Set[str] = set()
    for path in _DEFS_DIR.glob("*.yaml"):
        for line in path.read_text(encoding="utf-8").splitlines():
            matched = _NAME_RE.match(line)
            if matched:
                names.add(matched.group(1))
                break
    return names


def _referenced_tools(policy: dict) -> List[Tuple[str, str]]:
    """收集策略中所有「位置 → 工具名」引用（排除 ``"*"`` 通配）"""
    refs: List[Tuple[str, str]] = []
    for role_name, role_cfg in policy.get("roles", {}).items():
        for key in ("allowed_tools", "denied_tools"):
            for tool in role_cfg.get(key, []) or []:
                if tool != _WILDCARD:
                    refs.append((f"roles.{role_name}.{key}", tool))
    for rule in policy.get("abac_rules", []) or []:
        tool = rule.get("tool", "")
        if tool and tool != _WILDCARD:
            refs.append((f"abac_rules[{rule.get('name', '?')}].tool", tool))
    return refs


def _format_missing(missing: List[Tuple[str, str]], real: Set[str]) -> str:
    """把错位项格式化为可操作的失败信息（附最接近的真实工具名建议）"""
    lines = []
    for where, tool in sorted(missing):
        close = difflib.get_close_matches(tool, sorted(real), n=3, cutoff=0.5)
        hint = f"（是否想写: {', '.join(close)}）" if close else "（注册表中无相近名）"
        lines.append(f"  - {where}: {tool!r} 不存在于注册表 {hint}")
    return "\n".join(lines)


@pytest.fixture(scope="module")
def policy() -> dict:
    return _load_policy()


@pytest.fixture(scope="module")
def real_tools() -> Set[str]:
    names = _real_tool_names()
    assert names, f"未能从 {_DEFS_DIR} 读到任何工具定义（口径失效，测试本身需修）"
    return names


class TestReferencedToolsExist:
    """不变量 1：策略引用的工具名必须真实存在"""

    def test_角色白名单与黑名单中的工具名都存在(self, policy, real_tools):
        missing = [(where, tool) for where, tool in _referenced_tools(policy)
                   if tool not in real_tools]
        assert not missing, (
            "策略文件引用了不存在的工具名（RBAC 严格白名单下会造成静默误杀）:\n"
            + _format_missing(missing, real_tools)
        )

    def test_abac规则目标工具都存在(self, policy, real_tools):
        missing = []
        for rule in policy.get("abac_rules", []) or []:
            tool = rule.get("tool", "")
            if tool and tool != _WILDCARD and tool not in real_tools:
                missing.append((f"abac_rules[{rule.get('name', '?')}].tool", tool))
        assert not missing, (
            "ABAC 规则的目标工具不存在 ⇒ 该规则永不触发（安全约束静默失效）:\n"
            + _format_missing(missing, real_tools)
        )

    def test_每个角色至少能匹配到一个真实工具(self, policy, real_tools):
        """避免角色被写成「谁都匹配不上」的空壳（guest 曾是这种状态）"""
        powerless = []
        for role_name, role_cfg in policy.get("roles", {}).items():
            allowed = set(role_cfg.get("allowed_tools", []) or [])
            if _WILDCARD in allowed:
                continue
            if not (allowed & real_tools):
                powerless.append(role_name)
        assert not powerless, (
            f"这些角色的 allowed_tools 匹配不到任何真实工具（等于全量拒绝）: {powerless}"
        )


class TestRoleDefinitions:
    """不变量 2 / 3：角色键与 default_role 必须合法"""

    def test_角色键都是合法枚举值(self, policy):
        from agent.permission_system import Role  # 纯标准库依赖，导入安全
        valid = {role.value for role in Role}
        unknown = sorted(set(policy.get("roles", {})) - valid)
        assert not unknown, (
            f"角色键不是 Role 枚举取值（_load_policies 会静默跳过 ⇒ 该角色全量拒绝）: "
            f"{unknown}；合法取值: {sorted(valid)}"
        )

    def test_default_role_合法且出现在roles中(self, policy):
        from agent.permission_system import Role
        default_role = str(policy.get("default_role", "")).lower()
        valid = {role.value for role in Role}
        assert default_role in valid, f"default_role={default_role!r} 不是合法角色: {sorted(valid)}"
        assert default_role in policy.get("roles", {}), (
            f"default_role={default_role!r} 未在 roles 中定义 ⇒ 该角色无策略 ⇒ 全量拒绝"
        )

    def test_通配符不被误用为工具名(self, policy, real_tools):
        """``"*"`` 只应出现在 allowed_tools；出现在 denied_tools 等于拒绝一切"""
        for role_name, role_cfg in policy.get("roles", {}).items():
            assert _WILDCARD not in (role_cfg.get("denied_tools", []) or []), (
                f"roles.{role_name}.denied_tools 含通配符 '*' ⇒ 该角色所有工具都会被拒"
            )


class TestRuleHygiene:
    """不变量 4 / 5 / 6：规则命名与备忘区纪律"""

    def test_abac规则名唯一(self, policy):
        names = [rule.get("name", "") for rule in policy.get("abac_rules", []) or []]
        duplicated = sorted({n for n in names if names.count(n) > 1})
        assert not duplicated, f"abac_rules 规则名重复: {duplicated}"

    def test_备忘区与活规则不重名(self, policy):
        active = {rule.get("name", "") for rule in policy.get("abac_rules", []) or []}
        inactive = {rule.get("name", "") for rule in policy.get("_inactive_rules", []) or []}
        overlap = sorted(active & inactive)
        assert not overlap, f"同名规则同时出现在 abac_rules 与 _inactive_rules: {overlap}"

    def test_备忘区目标工具必须未注册(self, policy, real_tools):
        """备忘区的存在理由就是「目标工具不存在」；一旦注册就必须移回活规则"""
        promoted = []
        for rule in policy.get("_inactive_rules", []) or []:
            tool = rule.get("tool", "")
            if tool in real_tools:
                promoted.append((rule.get("name", "?"), tool))
        assert not promoted, (
            "下列备忘规则的目标工具现已注册，必须移回 abac_rules 才能生效:\n"
            + "\n".join(f"  - {name}: {tool}" for name, tool in promoted)
        )

    def test_备忘区每条都要写明移出理由(self, policy):
        for rule in policy.get("_inactive_rules", []) or []:
            reason = str(rule.get("inactive_reason", "")).strip()
            assert reason, (
                f"_inactive_rules[{rule.get('name', '?')}] 缺少 inactive_reason："
                "移出活规则必须留下理由，否则后人无从判断该不该恢复"
            )

    # ── 顶层文档键 _policy_notes（纯备忘，加载器与其它断言都不读它）──────────
    # Why 需要它：`_inactive_rules` 的不变量是"目标工具**未注册**"，因此**已注册**工具的
    # 策略一旦被停用就没地方留档。`_policy_notes` 补上这个位置：它不被 `_load_policies`
    # 读取（那里只读 roles / abac_rules / default_role），也不被上面的断言读取。
    # 代价是"备忘说已禁用、活规则里却还在"这种自相矛盾没人管 ⇒ 由下面两条钉死。

    def test_文档键提到的规则不得同时留在活规则里(self, policy):
        """防"一边备忘已禁用、一边还在 abac_rules 里生效"的自相矛盾"""
        active = {rule.get("name", "") for rule in policy.get("abac_rules", []) or []}
        notes = policy.get("_policy_notes", []) or []
        assert isinstance(notes, list), "_policy_notes 必须是数组"
        conflicting = sorted(
            {str(note.get("rule", "")).strip() for note in notes if isinstance(note, dict)}
            & active
        )
        assert not conflicting, (
            f"这些规则同时出现在 _policy_notes（声明已禁用）与 abac_rules（仍在生效）: "
            f"{conflicting} —— 二者只能取其一"
        )

    def test_文档键每条都要写明禁用理由与恢复方法(self, policy):
        """格式纪律：只有写清"为什么禁用 + 怎么恢复"，备忘才有价值"""
        for note in policy.get("_policy_notes", []) or []:
            assert isinstance(note, dict), f"_policy_notes 元素必须是对象: {note!r}"
            for key in ("rule", "disabled_reason", "reenable_hint"):
                assert str(note.get(key, "")).strip(), (
                    f"_policy_notes[{note.get('rule', '?')}] 缺少非空字段: {key}"
                )


class TestPolicyLoadable:
    """策略文件本身必须可被加载（结构损坏会静默降级为「仅正则黑名单」）"""

    def test_顶层结构完整(self, policy):
        for key in ("version", "default_role", "roles", "abac_rules"):
            assert key in policy, f"策略文件缺少顶层键: {key}"
        assert isinstance(policy["roles"], dict) and policy["roles"], "roles 不能为空"
        assert isinstance(policy["abac_rules"], list), "abac_rules 必须是数组"

    def test_规则必备字段齐全(self, policy):
        for rule in policy.get("abac_rules", []) or []:
            for key in ("name", "tool", "deny_if"):
                assert key in rule and rule[key], (
                    f"abac_rules[{rule.get('name', '?')}] 缺少必备字段: {key}"
                )

    def test_描述字段登记了本不变量(self, policy):
        """顶层 description 是本不变量的书面出口，防止后人删掉测试却不知其存在"""
        description = str(policy.get("description", ""))
        assert "tool_definitions" in description, (
            "策略文件 description 应登记「工具名必须存在于 data/tool_definitions/*.yaml」"
            "这一不变量（本测试文件是它的执行者）"
        )
