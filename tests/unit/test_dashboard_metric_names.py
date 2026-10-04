# -*- coding: utf-8 -*-
"""看板指标名真实性守卫（§10 B-1 的防复发断言）

【为什么要这个测试】
2026-10-02 的监控清理发现：Grafana 看板里大量面板引用了**本仓根本不存在的指标名** ——
大驼峰 `Yunshu_*` 命名空间（对应那个从未实例化的 exporter）、旧名
（`system_cpu_usage_percent` / `http_requests_total`，真名都带 `yunshu_` 前缀）、
以及只有 pushgateway 链路才会产生的 CI/部署指标。后果是「面板永远空白，却没人知道为什么」，
只能靠人工 grep 事后发现（这次就是）。

本测试把「看板里的每个指标名都必须能追溯到某个产出它的代码 / 记录规则」固化成断言：
新增面板时若写了不存在的名字，CI 直接红，而不是等几个月后有人对着一片空白发呆。

【判据（静态可判，不依赖运行中的服务）】
known = `BUSINESS_METRICS_DEFINITIONS`（业务指标登记表）
      ∪ 生产代码里的 `yunshu_*` / `Yunshu_*` 字符串字面量（prometheus_client 定义与埋点）
      ∪ monitoring 下所有 `record:` 记录规则名（形如 `yunshu:latency_p95:5m`）

【已知豁免（必须显式、必须带说明）】
`_TOLERATED` 里是 10 个 CI/CD 与部署/回滚指标：产出方是 pushgateway 推送链路，本部署没有接入
⇒ 面板恒空。这些面板**保留**，但已就地写入「本部署无数据源」的 description；
接入 pushgateway 后应把名字补进产出侧并清空本集合。
第二个测试断言这些面板确实带着说明，防止「豁免了却没解释」。

【不覆盖（静态判不了）】
「名字存在但没有数据」：例如 `yunshu_safe_file_reader_*` 曾由 utils/file_reader.py 发射，
而该文件在非测试代码里没有调用方 ⇒ 指标恒为 0、9 条告警恒不触发。这属于**接线**问题，
见 docs/closeout/能力层重构交付报告_20261001.md §10（A 类）。
【2026-10-03 更新】上面这个"恒为 0 的空名字"例子已经消失：那 5 个 `yunshu_safe_file_reader_*`
已从 agent/monitoring/prometheus.py 删除（同时清掉 agent/server_routes/routes_logging.py 里
对它们 5 个发射函数的未使用 import）⇒ /metrics 上不再出现。它们**只**留在 utils/file_reader.py
自己内联的那套同名定义里（仅在 import 该模块的进程里注册，生产代码不 import）。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# 看板所在目录：
#   1) monitoring/grafana/dashboards/ —— compose 真正 provisioning 的目录（权威）
#   2) monitoring/grafana_dashboards/ —— 遗留目录（不再被 compose 加载，但仍会被人工导入）
#   3) monitoring/                    —— 早期散落的看板
DASHBOARD_DIRS = (
    ROOT / "monitoring" / "grafana" / "dashboards",
    ROOT / "monitoring" / "grafana_dashboards",
    ROOT / "monitoring",
)

# 生产代码扫描范围：排除测试、文档、脚本与各种归档 / 缓存目录
_EXCLUDED_PARTS = {
    "tests", "docs", "_scratch", "demos", "backup", "backups", ".git", "node_modules",
    "yunshu-ui", "htmlcov", "htmlcov_planning", "coverage_report", "build", "release",
    "releases", ".pytest_tmp", "test_dir", "tmp_backup_test", "restored-frontend",
    "env_archive", "_edge_profile", "__pycache__", ".mypy_cache", ".ruff_cache",
    ".pytest_cache", ".benchmarks", "pytest_chunks", "test_data", "test_reports",
    "_ci_logs", "_t06_logs", "_t08_logs",
    # 【2026-10-04 补：非本仓的树】下面这些都在工作区里、但**不属于本仓的源码**：
    #   · security-tools/ —— 未跟踪的完整嵌套副本（25,813 文件，审计 M-26）。
    #     contract_diff 早已显式排除它（"否则产出双份契约"），本文件此前**没有**。
    #     实测：它贡献了 1,236 个被扫的 .py。今天它**不改变任何结论**
    #     （known 265 -> 265，见下方 _iter_repo_py 的说明里记的实测），
    #     但它是一个**会掩盖缺失的隐患**：只要某个指标名恰好只存在于副本里，
    #     本守卫就会误判"本仓能产出它"。这正是本仓反复记录的
    #     "会漏报的门禁比会误报的更危险，因为它看起来很绿"。
    #   · 其余为工具/备份/临时树，同样不是本仓源码。
    "security-tools", ".devtools", ".worktrees", ".fix_backups",
    "_tmp_rootcause_probe", "patches", ".tmp-merge", "backup_pre_rebase",
}


def _iter_repo_py():
    """产出本仓的 .py 路径：**边走边剪枝**（不进入被排除目录）。

    【为什么必须剪枝，而不是"rglob 之后再过滤"】
    原实现是 `for path in ROOT.rglob("*.py")` 再按 `parts & _EXCLUDED_PARTS` 过滤 ——
    而 rglob **会先把整棵树走完**。本机实测：

        ROOT.rglob("*.py")  ->  15,822 个路径，耗时 **35.17s**
        过滤后需要读的文件   ->  3,263 个，读它们只要 **0.49s**

    也就是说：过滤只省下了"读文件"的 0.49s，**走树的 35s 一分没省**。
    而 15,822 里有 1,236 个来自上面说的嵌套副本 —— 剪枝后根本不进那棵树。

    【为什么这在这条上格外重要】本文件的用例实测耗时 **88.21s**（本机），
    而 CI 分片对单用例的超时是 **60s** ⇒ 它是一个**注定超时**的用例，
    只是碰巧还没在负载够重的 runner 上撞上（同族的 collect_routes_static 与
    test_business_metrics_registration_completeness 都已经撞过了）。
    """
    for dirpath, dirnames, filenames in os.walk(ROOT):
        # 就地剪枝：os.walk 支持通过改写 dirnames 阻止其下降
        dirnames[:] = sorted(d for d in dirnames if d not in _EXCLUDED_PARTS)
        for name in sorted(filenames):
            if name.endswith(".py"):
                yield Path(dirpath) / name

_METRIC_RE = re.compile(r"\b(?:yunshu_|Yunshu_)\w+")
_LITERAL_RE = re.compile(r"""['"]((?:yunshu|Yunshu)_[A-Za-z0-9_]+)['"]""")
_RECORD_RE = re.compile(r"record:\s*([A-Za-z0-9_:]+)")

#: prometheus_client 为直方图 / 计数器自动派生的后缀
_DERIVED_SUFFIXES = ("_bucket", "_count", "_sum", "_created", "_total")

#: 已知豁免：产出方是 pushgateway 推送链路（本部署未接入）的 CI/CD 与部署指标。
#: 对应面板已写入「本部署无数据源」说明；接入链路后应清空本集合。
_TOLERATED = {
    "Yunshu_ci_build_failures_total",
    "Yunshu_ci_pipeline_duration_seconds",
    "Yunshu_ci_pipeline_runs_total",
    "Yunshu_ci_test_coverage_percent",
    "Yunshu_ci_test_failures_total",
    "Yunshu_deployment_duration_seconds",
    "Yunshu_deployment_failures_total",
    "Yunshu_deployment_status",
    "Yunshu_deployment_total",
    "Yunshu_rollback_total",
}


def _known_metric_names() -> set:
    """本仓能产出（或已登记）的指标名集合"""
    known = set()

    from agent.monitoring.business_metrics import BUSINESS_METRICS_DEFINITIONS

    known |= set(BUSINESS_METRICS_DEFINITIONS.keys())

    for path in _iter_repo_py():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:  # pragma: no cover - 权限 / 占用等异常
            continue
        known |= set(_LITERAL_RE.findall(text))

    for path in (ROOT / "monitoring").rglob("*.yml"):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:  # pragma: no cover
            continue
        known |= set(_RECORD_RE.findall(text))

    return known


def _is_known(name: str, known: set) -> bool:
    if name in known:
        return True
    for suffix in _DERIVED_SUFFIXES:
        if name.endswith(suffix) and name[: -len(suffix)] in known:
            return True
    return False


def _iter_panels(node):
    """深度遍历面板树（row 面板里还嵌着子面板）"""
    if isinstance(node, dict):
        if "targets" in node or "panels" in node or "title" in node:
            yield node
        for value in node.values():
            yield from _iter_panels(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_panels(item)


def _dashboard_metric_refs():
    """返回 [(看板相对路径, 面板标题, 指标名, 面板说明)]"""
    refs = []
    for directory in DASHBOARD_DIRS:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            rel = str(path.relative_to(ROOT))
            for panel in _iter_panels(data.get("panels", [])):
                title = str(panel.get("title", "?"))
                desc = str(panel.get("description", ""))
                for target in panel.get("targets") or []:
                    expr = target.get("expr") or ""
                    for name in _METRIC_RE.findall(expr):
                        refs.append((rel, title, name, desc))
    return refs


def test_all_dashboard_metrics_are_producible():
    """看板引用的每个指标名都必须能追溯到产出它的代码 / 记录规则

    失败时的读法：报错里的 `<看板> :: <面板> -> <指标>` 就是「永远空白的面板」。
    修法二选一：① 把查询改成真实指标名；② 确认本部署确实没有产出方时，
    删掉面板，或保留但在面板 description 里写明原因并加入 `_TOLERATED`。
    """
    known = _known_metric_names()
    assert len(known) > 100, "指标名集合异常偏小，判据本身可能已失效"

    unknown = {}
    for rel, title, name, _desc in _dashboard_metric_refs():
        if _is_known(name, known) or name in _TOLERATED:
            continue
        unknown.setdefault((rel, title), set()).add(name)

    if unknown:
        lines = [
            "%s :: %s -> %s" % (rel, title, sorted(names))
            for (rel, title), names in sorted(unknown.items())
        ]
        pytest.fail(
            "以下看板面板引用了本仓不存在的指标名（这些面板会永远空白）：\n  "
            + ("\n  ".join(lines))
            + "\n\n修法：改成真实指标名，或删掉面板，或保留并写明原因后加入 _TOLERATED。"
        )


def test_tolerated_panels_explain_themselves():
    """被豁免的指标所在面板必须写明「无数据源」

    为什么单列一条：豁免表本身会退化成「合法的静默空白」。
    只有把「豁免 = 必须带说明」也断言住，豁免才是有信息量的。
    """
    missing = []
    for rel, title, name, desc in _dashboard_metric_refs():
        if name not in _TOLERATED:
            continue
        if "无数据源" not in desc:
            missing.append("%s :: %s -> %s" % (rel, title, name))

    if missing:
        pytest.fail(
            "以下面板的指标被 _TOLERATED 豁免，但面板没有说明原因"
            "（description 里需含「无数据源」）：\n  " + "\n  ".join(sorted(set(missing)))
        )

class Test扫描范围与代价:
    """守卫本身的**扫描范围**与**代价**（2026-10-04 补）。

    【为什么单列一类】本文件的核心断言是"看板里每个指标名都能追溯到产出方"，
    而"产出方"由一次全仓扫描算出来。扫描范围一旦不对，断言就会**静默失真**：
      · 范围过大（把未跟踪的嵌套副本也算进来）⇒ 副本里恰好存在某个名字时，
        本仓明明产不出来，守卫却判"有产出方" ⇒ **漏报**；
      · 范围过小/遍历失效 ⇒ 判据集合偏空，断言退化成"什么都通过"。
    另外这一节还钉住**代价**：本用例实测曾达 88.21s，而 CI 分片单用例超时是 60s。
    """

    def test_非本仓的树必须排除(self):
        """working tree 里的嵌套副本与工具树不是本仓源码，排除它们既是正确性也是性能。"""
        for part in ("security-tools", ".devtools", ".worktrees", "_tmp_rootcause_probe"):
            assert part in _EXCLUDED_PARTS, (
                part + " 不在 _EXCLUDED_PARTS 里 —— 该树会被算成本仓的产出方（守领会漏报），"
                "同时 rglob 会多走一棵大树（实测 security-tools 一个就带进 1,236 个 .py）"
            )

    def test_遍历真的剪枝(self):
        """_iter_repo_py() **不得**产出任何被排除目录下的路径。

        这条与上一条不同：上一条只证明"名单里有它"，这条证明"遍历真的没进去"。
        原实现是 rglob 之后再过滤 —— 名单再全也照样走完整棵树（35.17s）。
        """
        bad = [p for p in _iter_repo_py()
               if set(p.relative_to(ROOT).parts[:-1]) & _EXCLUDED_PARTS]
        assert not bad, (
            "遍历进入了被排除目录（前 3 个）：" + str(bad[:3])
            + " —— 剪枝失效，走树的代价会回来（本仓对「扫描器必须两把尺子」有记录）"
        )

    def test_遍历产出非空且量级正常(self):
        """防"遍历失效导致空集"：空集会让 known 只剩登记表，断言随之失真。"""
        files = list(_iter_repo_py())
        assert len(files) > 300, (
            "遍历只产出 " + str(len(files)) + " 个 .py，量级不对 —— "
            "剪枝可能连本仓源码一起剪掉了"
        )

    def test_扫描耗时不退化(self):
        """把"走完整棵树"这个退化锁住。

        【为什么可以断言耗时】这里比的不是绝对的秒数，而是**量级**：
        剪枝后坏情况也只走本仓那 3 千来个文件（实测 1.2s）；
        若有人改回 rglob 全树，本机就会到 **88s**（实测），在 CI 上必然超时。
        阈值取 20s：比正常（<2s）宽 10 倍以上，不会因机器抖动假红，
        但足以拦住"走完整棵树"这种数量级退化。
        """
        import time

        t0 = time.time()
        known = _known_metric_names()
        cost = time.time() - t0
        assert cost < 20.0, (
            "指标名扫描耗时 %.1fs，已退化到数量级错误 —— 多半是遍历又走完整棵树了"
            "（原实现 rglob 全树在本机实测 35.17s 走树 + 88.21s 总耗时，"
            "而 CI 分片单用例超时 60s）" % cost
        )
        assert len(known) > 100, "扫描结果异常偏小，判据本身可能已失效"
