"""e2e 权限夹具 ↔ 工具注册表 一致性测试（防「第二份副本」静默漂移）

被守护的对象：``tests/integration/test_permission_gateway_e2e.py`` 里的**文件内自建
策略夹具** ``E2E_POLICY``（该文件不读 ``data/`` 下的任何真实数据）。

为什么需要它（真实事故的第二现场）：
    该夹具第一版把工具名写成 ``file_read`` / ``file_write`` / ``code_runner`` /
    ``system_format`` / ``system_shutdown``——这些名字在注册表
    （``data/tool_definitions/*.yaml``）里**都不存在**（真实名是 ``read_file`` /
    ``write_file`` 等）。夹具自洽（策略与断言同源）⇒ 错名不会让任何断言失败，
    于是整份夹具长期停留在「与真实注册表无关」的状态，成为第二份会静默漂移的
    副本。真实策略那一侧的事故与不变量见
    ``tests/unit/test_permission_policies_consistency.py`` 文件头。

被守护的不变量：
    1. ``E2E_POLICY`` 中 ``roles[*].allowed_tools`` / ``denied_tools`` 与
       ``abac_rules[*].tool`` 引用的工具名必须真实存在于注册表；``"*"`` 通配除外。
    2. 该文件中所有 ``<gateway>.check("<工具名>", ...)`` 调用点使用的工具名同样必须
       真实存在——断言与夹具同源，只看夹具口径会漏掉「断言里写错名」这一类漂移。
    3. 每个角色至少能匹配到一个真实工具（避免角色被写成谁都匹配不上的空壳）。

口径（与 ``test_permission_policies_consistency.py`` 一致）：
    只读 ``data/tool_definitions/*.yaml`` 的文本行 ``^name:\\s*(\\S+)\\s*$``，
    不引入 yaml 解析依赖，也不依赖运行时注册表（工具在启动期由 lifecycle 注册，
    测试期注册表为空）。

【不易】只读文件；用 ``ast`` 取字面量，**不导入**被守护的测试模块（无副作用、
        不触发 agent 包导入链）。
【变易】夹具新增角色 / ABAC 规则 / check 调用点会被自动纳入校验，无需改本文件。
【简易】纯 ``ast`` + ``re`` + ``pathlib``；无网络、无第三方依赖。
"""
from __future__ import annotations

import ast
import difflib
import re
from pathlib import Path
from typing import Dict, List, Set, Tuple

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE_PATH = _PROJECT_ROOT / "tests" / "integration" / "test_permission_gateway_e2e.py"
_DEFS_DIR = _PROJECT_ROOT / "data" / "tool_definitions"

_FIXTURE_CONST = "E2E_POLICY"
_WILDCARD = "*"
_NAME_RE = re.compile(r"^name:\s*(\S+)\s*$")


def _real_tool_names() -> Set[str]:
    """工具注册表口径：``data/tool_definitions/*.yaml`` 里的 ``name`` 字段

    使用文本行匹配而非 yaml 解析：只需 ``name``，且避免为一个字段引入解析依赖
    （与 ``tests/unit/test_permission_policies_consistency.py`` 同一口径）。
    """
    names: Set[str] = set()
    for path in _DEFS_DIR.glob("*.yaml"):
        for line in path.read_text(encoding="utf-8").splitlines():
            matched = _NAME_RE.match(line)
            if matched:
                names.add(matched.group(1))
                break
    return names


def _fixture_tree() -> ast.Module:
    """解析夹具文件源码（不执行、不导入）"""
    return ast.parse(_FIXTURE_PATH.read_text(encoding="utf-8"))


def _fixture_policy() -> Dict[str, object]:
    """从夹具源码中取出 ``E2E_POLICY`` 字面量（纯字面量，可安全 eval）"""
    for node in _fixture_tree().body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(t, ast.Name) and t.id == _FIXTURE_CONST
               for t in node.targets):
            value = ast.literal_eval(node.value)
            assert isinstance(value, dict), (
                f"{_FIXTURE_CONST} 不是字典字面量（夹具结构变了，本测试口径需同步）"
            )
            return value
    raise AssertionError(
        f"{_FIXTURE_PATH.name} 中未找到 {_FIXTURE_CONST} 定义"
        "（夹具被改名/移除 ⇒ 本守卫口径失效，测试本身需修）"
    )


def _fixture_policy_refs(policy: Dict[str, object]) -> List[Tuple[str, str]]:
    """收集夹具策略中所有「位置 → 工具名」引用（排除 ``"*"`` 通配）"""
    refs: List[Tuple[str, str]] = []
    roles = policy.get("roles", {}) or {}
    for role_name, role_cfg in roles.items():  # type: ignore[union-attr]
        for key in ("allowed_tools", "denied_tools"):
            for tool in role_cfg.get(key, []) or []:  # type: ignore[union-attr]
                if tool != _WILDCARD:
                    refs.append((f"E2E_POLICY.roles.{role_name}.{key}", tool))
    for rule in policy.get("abac_rules", []) or []:  # type: ignore[union-attr]
        tool = rule.get("tool", "")
        if tool and tool != _WILDCARD:
            refs.append((f"E2E_POLICY.abac_rules[{rule.get('name', '?')}].tool", tool))
    return refs


def _checked_tool_names() -> List[Tuple[str, str]]:
    """收集夹具文件中 ``xxx.check("<工具名>", ...)`` 调用点的工具名"""
    refs: List[Tuple[str, str]] = []
    for node in ast.walk(_fixture_tree()):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "check"):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            refs.append((f"{_FIXTURE_PATH.name}:{node.lineno}", first.value))
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
def real_tools() -> Set[str]:
    names = _real_tool_names()
    assert names, f"未能从 {_DEFS_DIR} 读到任何工具定义（口径失效，测试本身需修）"
    return names


@pytest.fixture(scope="module")
def policy() -> Dict[str, object]:
    return _fixture_policy()


class TestFixtureWiring:
    """口径自检：守卫确实解析到了夹具本身（防「解析不到 ⇒ 空集 ⇒ 永远绿」）"""

    def test_解析到夹具策略与非空引用(self, policy, real_tools):
        refs = _fixture_policy_refs(policy)
        checked = _checked_tool_names()
        assert refs, "未能从 E2E_POLICY 收集到任何工具引用（夹具结构变了？）"
        assert checked, (
            f"未能从 {_FIXTURE_PATH.name} 收集到任何 check(...) 调用点"
            "（断言写法变了？本守卫口径需同步）"
        )
        assert real_tools, "注册表口径为空"


class TestFixtureReferencedToolsExist:
    """不变量 1：夹具策略引用的工具名必须真实存在"""

    def test_夹具白名单与黑名单中的工具名都存在(self, policy, real_tools):
        missing = [(where, tool) for where, tool in _fixture_policy_refs(policy)
                   if tool not in real_tools]
        assert not missing, (
            "e2e 夹具引用了不存在的工具名（该副本会与真实注册表静默漂移）:\n"
            + _format_missing(missing, real_tools)
        )

    def test_abac规则目标工具都存在(self, policy, real_tools):
        missing = []
        for rule in policy.get("abac_rules", []) or []:  # type: ignore[union-attr]
            tool = rule.get("tool", "")
            if tool and tool != _WILDCARD and tool not in real_tools:
                missing.append(
                    (f"E2E_POLICY.abac_rules[{rule.get('name', '?')}].tool", tool)
                )
        assert not missing, (
            "e2e 夹具的 ABAC 规则目标工具不存在 ⇒ 该规则永不触发，"
            "用例断言的是「夹具自洽」而非真实行为:\n"
            + _format_missing(missing, real_tools)
        )

    def test_每个角色至少能匹配到一个真实工具(self, policy, real_tools):
        """避免角色被写成「谁都匹配不上」的空壳"""
        powerless = []
        roles = policy.get("roles", {}) or {}
        for role_name, role_cfg in roles.items():  # type: ignore[union-attr]
            allowed = set(role_cfg.get("allowed_tools", []) or [])  # type: ignore[union-attr]
            if _WILDCARD in allowed:
                continue
            if not (allowed & real_tools):
                powerless.append(role_name)
        assert not powerless, (
            f"这些角色的 allowed_tools 匹配不到任何真实工具（等于全量拒绝）: {powerless}"
        )


class TestAssertionCallSitesUseRealTools:
    """不变量 2：断言里的工具名（check 调用点）同样必须真实存在"""

    def test_check调用点的工具名都存在(self, real_tools):
        missing = [(where, tool) for where, tool in _checked_tool_names()
                   if tool not in real_tools]
        assert not missing, (
            "e2e 用例的 check(...) 调用点使用了不存在的工具名:\n"
            + _format_missing(missing, real_tools)
        )
