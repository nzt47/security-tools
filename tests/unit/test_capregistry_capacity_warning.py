"""容量软告警（TASK-04 · W2 目标 2）：超限**可见**但**不阻碍**

【这一组测试要钉住什么】docs/perf/容量压测.md §2.6 实测：容量目标此前
**零强制点** —— 合成 10,001 条时 degraded=False、build_warnings=[]。
本组测试是该结论的**反向对拍**：超限必须留下可见痕迹，同时**行为不变**
（不拒绝、不截断、degraded 不变）。

【为什么不用环境变量开关】见 agent/capregistry/view.py 里
CAPACITY_SOFT_LIMIT_TENANT 上方的注释：agent/settings/registry.py 的 AST 守卫
要求每个开关"声明 ↔ 读取"双向零缺口，而这里只需一条只读提示。
本文件最后一条测试**机械证明**了没有引入新开关。
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agent.capregistry import view as V  # noqa: E402
from agent.capregistry.spec import CapabilityRecord  # noqa: E402

VIEW_SRC = Path(V.__file__)


def _specs(n: int, tenant: str = "default") -> list:
    return [CapabilityRecord(tool_name="cap_%06d" % i, tenant_id=tenant)
            for i in range(n)]


def _specs_multi(spec: dict) -> list:
    out = []
    i = 0
    for tenant, n in spec.items():
        for _ in range(n):
            out.append(CapabilityRecord(tool_name="cap_%06d" % i, tenant_id=tenant))
            i += 1
    return out


# ════════════════════════════════════════════════════════════
#  1. 阈值语义：不超限 ⇒ 一条都不加
# ════════════════════════════════════════════════════════════


def test_阈值刚好等于上限时不告警():
    """边界：== 阈值**不算**超限（判据是 strictly greater）"""
    assert V.capacity_warnings(_specs(V.CAPACITY_SOFT_LIMIT_TENANT)) == []


def test_真实清单规模不产生任何容量告警():
    """真实 114 条（TASK-00 §五基线）⇒ 零容量告警（不制造噪音）"""
    assert V.capacity_warnings(_specs(114)) == []


# ════════════════════════════════════════════════════════════
#  2. 单租户超限
# ════════════════════════════════════════════════════════════


def test_单租户超过_500_产生一条租户告警():
    got = V.capacity_warnings(_specs(V.CAPACITY_SOFT_LIMIT_TENANT + 1))
    assert len(got) == 1
    assert "租户" in got[0] and "501" in got[0]
    assert "不拒绝、不截断" in got[0], "告警必须自证不改变判定语义"


def test_多个租户各自计数_只有超限的那个告警():
    specs = _specs_multi({"a": V.CAPACITY_SOFT_LIMIT_TENANT + 3,
                          "b": 10})
    got = V.capacity_warnings(specs)
    assert len(got) == 1
    assert "'a'" in got[0]
    assert "'b'" not in got[0]


# ════════════════════════════════════════════════════════════
#  3. 全局超限 + 顺序稳定
# ════════════════════════════════════════════════════════════


def test_全局超过_10000_产生全局告警():
    got = V.capacity_warnings(_specs(V.CAPACITY_SOFT_LIMIT_GLOBAL + 1))
    assert any("全局" in g for g in got), got
    assert any("10001" in g for g in got), got


def test_告警顺序稳定_全局在前租户按名字排序():
    specs = _specs_multi({"zeta": V.CAPACITY_SOFT_LIMIT_TENANT + 1,
                          "alpha": V.CAPACITY_SOFT_LIMIT_TENANT + 1})
    a = V.capacity_warnings(specs)
    b = V.capacity_warnings(list(reversed(specs)))
    assert a == b, "同一集合的不同输入顺序必须给出同一顺序的告警（可对拍）"
    assert "alpha" in a[0] and "zeta" in a[1]


# ════════════════════════════════════════════════════════════
#  4. 集成：经 build_registry 走一遍，行为**不拒绝、不截断、degraded 不变**
# ════════════════════════════════════════════════════════════


@pytest.fixture()
def _over_limit_registry(monkeypatch):
    """构造超过单租户阈值的 Registry（**不改任何生产数据**）

    【为什么走 build_registry 而不是直接 new 一个 CapabilityRegistry】
    "超限仍不阻塞启动"这句话的责任在 build_registry：只有走它，
    才能证明告警是**构建期**产物、且降级/正常两条路径都会带上它。
    """
    over = _specs(V.CAPACITY_SOFT_LIMIT_TENANT + 7)
    monkeypatch.setattr(V, "_load_manifest", lambda root: {"entries": []})
    monkeypatch.setattr(
        V, "_build_from_authority",
        lambda root, manifest: (_ for _ in ()).throw(
            V.CapabilitySpecBuildError("测试打桩：强制走降级路径")))
    monkeypatch.setattr(V, "_build_from_snapshot",
                        lambda manifest: (over, []))
    return V.build_registry(root=str(_ROOT))


def test_集成_超限时_build_warnings_增加_且_degraded_不变(_over_limit_registry):
    reg = _over_limit_registry
    warns = [w for w in reg.build_warnings if "容量软告警" in w]
    # 507 条 < 全局阈值 10000 ⇒ 只应有该租户那一条（全局那条由纯函数测试覆盖）
    assert len(warns) == 1, warns
    assert "租户 'default'" in warns[0]
    # 降级标志只由"主源失败"决定，容量告警**不得**影响它
    assert reg.degraded is True


def test_集成_超限时不拒绝不截断(_over_limit_registry):
    """★ 核心：告警**不是**闸门 —— 条目数、可查性、信封 total 全部不变"""
    reg = _over_limit_registry
    n = V.CAPACITY_SOFT_LIMIT_TENANT + 7
    assert len(reg) == n, "不得截断"
    assert len(reg.specs) == n
    # 最后一条（最"边缘"的那条）仍然查得到 ⇒ 没有被拒绝注册
    assert reg.get("cap_%06d" % (n - 1)) is not None
    env = reg.list_envelope()
    assert env["data"]["total"] == n
    assert env["data"]["returned"] == n
    assert len(reg.query()) == n


def test_集成_未超限时_不产生任何容量告警(monkeypatch):
    few = _specs(3)
    monkeypatch.setattr(V, "_load_manifest", lambda root: {"entries": []})
    monkeypatch.setattr(V, "_build_from_authority",
                        lambda root, manifest: (few, []))
    reg = V.build_registry(root=str(_ROOT))
    assert [w for w in reg.build_warnings if "容量软告警" in w] == []
    assert reg.degraded is False


# ════════════════════════════════════════════════════════════
#  5. D5 守卫：本改动**没有**引入环境变量开关
# ════════════════════════════════════════════════════════════


def test_源码里没有为容量阈值新增任何环境变量读取():
    """机械证明：view.py 全文的 os.environ / os.getenv 出现次数为 0

    【为什么这条必须存在】D5 规定"新增环境变量开关必须登记 settings/registry.py
    并满足 5 条双向零缺口 AST 守卫"。本任务选择**不新增开关**；
    这条测试把该选择变成**可验证的约束** —— 一旦有人日后把阈值改成 getenv，
    它会在本地立刻变红，而不是等 CI 的守卫发现。
    """
    src = VIEW_SRC.read_text(encoding="utf-8")
    tree = ast.parse(src)
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in ("environ", "getenv"):
            offenders.append((node.lineno, node.attr))
    assert offenders == [], f"view.py 出现了环境变量读取 {offenders} ⇒ 违反 D5"
    assert "CAPACITY_SOFT_LIMIT_TENANT = 500" in src
    assert "CAPACITY_SOFT_LIMIT_GLOBAL = 10000" in src


def test_容量常量在_all_里导出():
    assert "capacity_warnings" in V.__all__
    assert "CAPACITY_SOFT_LIMIT_TENANT" in V.__all__
    assert "CAPACITY_SOFT_LIMIT_GLOBAL" in V.__all__
