"""LinkCache 生产部署演练脚本：按部署手册检查清单模拟真实上线流程。

对照「部署操作手册_LinkCache_20260807.md」逐项演练并输出检查结果：

  [1] 环境检查        Python 版本 + 独立包 yunshu_cache_tools 可导入
  [2] 采样率配置检查   默认 KNOWLEDGE_TIMING_SAMPLE_RATE=0.1 → period 10；
                      构造参数 timing_sample_rate=1.0 → period 1
  [3] 初始化时机演练   快照语义：写库后「不重建」新卡不入缓存；重建 searcher
                      后新卡进入缓存（手册 2.2 核心规则）
  [4] 内存占用估算     深估算 vs 模型估算同数量级（手册第 3 章）
  [5] 监控冒烟         调用 monitor_link_cache_memory.py --once --json（手册第 4 章）
  [6] 压测基线复测     缓存路径 QPS 相对历史基线（test_reports/benchmark_knowledge_search.json
                      cache.qps）退化 < 50% 判为潜在问题（--skip-bench 可跳过）

用法:
    python scripts/dev/drill_link_cache_deploy.py [--skip-bench]
输出:
    test_reports/deploy_drill_link_cache.json（结构化）+ 文本摘要
退出码: 0 全部通过 / 1 存在潜在问题（可带病上线）/ 2 演练失败（阻断上线）

【不易】演练即护栏：快照语义、采样率、内存估算、监控链路任何一项异常都必须在
上线前暴露；压测退化超阈值视为性能回归风险。
【简易】独立脚本，临时知识库用 TemporaryDirectory，不污染仓库数据。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
for _p in (PROJECT_ROOT, PROJECT_ROOT / "packages" / "yunshu_cache_tools" / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from agent.knowledge import Card, CardStore, KnowledgeSearch  # noqa: E402

logger = logging.getLogger("drill_link_cache_deploy")
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s", stream=sys.stderr)
# 抑制演练数据准备阶段的索引/解析 INFO 日志（非演练关注点）。
logging.getLogger("agent.knowledge").setLevel(logging.WARNING)

REPORT_PATH = PROJECT_ROOT / "test_reports" / "deploy_drill_link_cache.json"

# 压测退化阈值：cache QPS 低于历史基线 50% 判为潜在问题（防 CI 机器抖动误报）。
BENCH_QPS_REGRESSION_RATIO = 0.50


def _card(title, slug=None, links=None, type="concepts", status="current", content=""):
    return Card(
        title=title,
        slug=slug or title,
        status=status,
        type=type,
        source="deploy-drill",
        date="2026-08-07",
        content=content,
        insight=f"{title} 的核心洞见",
        links=links or [],
    )


def _est_deep_size(obj, seen=None):
    """递归深估算（与监控脚本同口径）。"""
    if seen is None:
        seen = set()
    oid = id(obj)
    if oid in seen:
        return 0
    seen.add(oid)
    total = sys.getsizeof(obj)
    if isinstance(obj, dict):
        total += sum(_est_deep_size(k, seen) + _est_deep_size(v, seen) for k, v in obj.items())
    elif isinstance(obj, (list, tuple, set, frozenset)):
        total += sum(_est_deep_size(i, seen) for i in obj)
    return total


def step_env_check() -> Tuple[str, str, str]:
    """[1] 环境检查：Python 版本 + 独立包导入。"""
    py = sys.version_info
    if (py.major, py.minor) < (3, 10):
        return "失败", f"Python 版本 {py.major}.{py.minor} < 3.10", ""
    try:
        import yunshu_cache_tools

        ver = yunshu_cache_tools.__version__
    except ImportError as exc:
        return "失败", f"独立包 yunshu_cache_tools 导入失败: {exc}", ""
    return "通过", f"Python {py.major}.{py.minor}.{py.micro} / 包 v{ver}", ""


def step_sample_rate_check() -> Tuple[str, str, str]:
    """[2] 采样率配置：默认 0.1 → period 10；显式 1.0 → period 1。"""
    try:
        default = KnowledgeSearch.__new__(KnowledgeSearch)  # 不触发 _build_index
    except Exception:
        default = None
    # 直接验证 PeriodicSampler 接线：显式构造参数优先级
    from agent.utils.periodic_sampler import PeriodicSampler

    default_rate = float(os.environ.get("KNOWLEDGE_TIMING_SAMPLE_RATE", "0.1"))
    s_default = PeriodicSampler(default_rate)
    s_full = PeriodicSampler(1.0)
    if s_default.period != 10 or s_full.period != 1:
        return "失败", f"采样周期异常: 默认 period={s_default.period}（期望 10）, 全量 period={s_full.period}（期望 1）", ""
    return (
        "通过",
        f"默认 rate={default_rate} → period={s_default.period}（每10次1条）；全量 rate=1.0 → period={s_full.period}",
        "",
    )


def step_snapshot_semantics(tmp_wiki: Path) -> Tuple[str, str, str]:
    """[3] 初始化时机演练：写后不重建不入缓存；重建后进入。"""
    store = CardStore(tmp_wiki)
    store.create(_card("a", slug="a", links=["b"]))
    store.create(_card("b", slug="b", links=["a"]))
    store.create(_card("c", slug="c", links=["ghost"]))
    searcher = KnowledgeSearch(store, timing_sample_rate=1.0)
    if searcher._link_cache.size != 3:
        return "失败", f"初始缓存卡数 {searcher._link_cache.size} != 3", ""

    # 写库后不重建：新卡 d 不入缓存（快照语义）
    store.create(_card("d", slug="d", links=["a"]))
    if searcher._link_cache.size != 3:
        return "失败", f"写后未重建缓存却变化: size={searcher._link_cache.size}", "快照语义被破坏"

    # 重建 searcher（与索引同生命周期）：d 进入缓存
    searcher2 = KnowledgeSearch(store, timing_sample_rate=1.0)
    if searcher2._link_cache.size != 4:
        return "失败", f"重建后缓存卡数 {searcher2._link_cache.size} != 4", ""
    links_d = searcher2._link_cache.expanded_links("d")
    if links_d != [("a", "a")]:
        return "失败", f"重建后 d 的 expanded_links={links_d}", ""
    return (
        "通过",
        "写后未重建缓存卡数保持 3（快照语义正确）；重建后 4 且 d→a 解析正确",
        "",
    )


def step_memory_estimate(wiki_root: Path) -> Tuple[str, str, str]:
    """[4] 内存估算：深估算与模型估算同数量级（独立知识库目录）。"""
    store = CardStore(wiki_root)
    store.create(_card("a", slug="a", links=["b", "c", "d"]))
    store.create(_card("b", slug="b", links=["a"]))
    store.create(_card("c", slug="c", links=["ghost", "archives/old"]))
    store.create(_card("d", slug="d", links=[]))
    searcher = KnowledgeSearch(store)
    cache = searcher._link_cache
    deep_b = _est_deep_size(cache._cache)
    total_links = sum(len(v) for v in cache._cache.values())
    model_b = cache.size * 120 + total_links * 120
    if deep_b <= 0 or model_b <= 0:
        return "失败", f"估算异常: deep={deep_b}B model={model_b}B", ""
    ratio = deep_b / model_b
    if not (0.1 <= ratio <= 10.0):
        return "失败", f"深估算与模型估算数量级不一致: deep={deep_b}B model={model_b}B ratio={ratio:.2f}", ""
    return (
        "通过",
        f"卡片={cache.size} links={total_links} 深估算={deep_b}B 模型={model_b}B ratio={ratio:.2f}",
        "",
    )


def step_monitor_smoke(tmp_wiki: Path) -> Tuple[str, str, str]:
    """[5] 监控冒烟：监控脚本 --once 正常路径退出码 0。"""
    proc = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "monitor_link_cache_memory.py"),
         "--once", "--json", "--cards-dir", str(tmp_wiki)],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        return "失败", f"监控脚本退出码 {proc.returncode}: {proc.stderr.strip()}", ""
    try:
        data = json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        return "失败", f"监控输出非 JSON: {proc.stdout.strip()!r} ({exc})", ""
    if data.get("alarmed"):
        return "失败", f"演练数据集意外触发告警: {data}", ""
    return "通过", f"监控退出码 0，估算 {data.get('used_mb')}MB，未告警", ""


def step_bench_regression() -> Tuple[str, str, str]:
    """[6] 压测基线复测：缓存路径 QPS 相对历史基线退化 < 50%。"""
    hist_path = PROJECT_ROOT / "test_reports" / "benchmark_knowledge_search.json"
    if not hist_path.exists():
        return "跳过", "无历史基线 benchmark_knowledge_search.json", ""
    baseline = json.loads(hist_path.read_text(encoding="utf-8")).get("cache", {}).get("qps", 0)
    out_path = PROJECT_ROOT / "test_reports" / ".drill_bench_tmp.json"
    bench = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "dev" / "benchmark_knowledge_search.py"),
         "--n", "50", "--threads", "4", "--json", str(out_path)],
        capture_output=True, text=True, timeout=600,
    )
    if bench.returncode != 0:
        return "失败", f"benchmark 退出码 {bench.returncode}: {bench.stderr.strip()[:500]}", ""
    try:
        result = json.loads(out_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return "失败", f"benchmark 输出读取失败: {exc}", ""
    cache_qps = result.get("cache", {}).get("qps", 0)
    if cache_qps <= 0:
        return "失败", f"benchmark 未产出 cache.qps: {result}", ""
    if cache_qps < baseline * BENCH_QPS_REGRESSION_RATIO:
        return (
            "潜在问题",
            f"缓存 QPS={cache_qps:.0f} < 历史基线 {baseline:.0f}×{BENCH_QPS_REGRESSION_RATIO:.0%}（性能退化风险）",
            "",
        )
    return "通过", f"缓存 QPS={cache_qps:.0f} ≥ 基线 {baseline:.0f}×{BENCH_QPS_REGRESSION_RATIO:.0%}（未退化）", ""


def main() -> int:
    parser = argparse.ArgumentParser(description="LinkCache 生产部署演练")
    parser.add_argument("--skip-bench", action="store_true", help="跳过压测基线复测（[6]）")
    args = parser.parse_args()

    steps: List[Dict[str, Any]] = []
    issues: List[str] = []
    with tempfile.TemporaryDirectory(prefix="deploy_drill_") as tmp:
        tmp_wiki = Path(tmp) / "knowledge" / "wiki"
        tmp_wiki.mkdir(parents=True)
        memory_wiki = Path(tmp) / "knowledge" / "memory_wiki"
        memory_wiki.mkdir(parents=True)
        for name, fn in [
            ("[1] 环境检查", step_env_check),
            ("[2] 采样率配置检查", step_sample_rate_check),
            ("[3] 初始化时机演练（快照语义）", lambda: step_snapshot_semantics(tmp_wiki)),
            ("[4] 内存占用估算", lambda: step_memory_estimate(memory_wiki)),
            ("[5] 监控冒烟", lambda: step_monitor_smoke(tmp_wiki)),
            ("[6] 压测基线复测", step_bench_regression if not args.skip_bench else lambda: ("跳过", "已指定 --skip-bench", "")),
        ]:
            t0 = time.perf_counter()
            try:
                status, detail, hint = fn()
            except Exception as exc:  # 演练任一环节异常按失败处理
                status, detail, hint = "失败", f"演练异常: {exc!r}", ""
            elapsed = round((time.perf_counter() - t0) * 1000, 1)
            steps.append({"step": name, "status": status, "detail": detail, "hint": hint, "ms": elapsed})
            if status in ("失败", "潜在问题"):
                issues.append(f"{name}: {detail}")
            logger.info("%-28s [%s] %s", name, status, detail)

    has_failure = any(s["status"] == "失败" for s in steps)
    has_issue = any(s["status"] == "潜在问题" for s in steps)
    report = {
        "drill_id": f"DRILL_{time.strftime('%Y%m%d_%H%M%S')}",
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "summary": "失败（阻断上线）" if has_failure else ("通过（含潜在问题）" if has_issue else "全部通过"),
        "steps": steps,
        "issues": issues,
    }
    REPORT_PATH.parent.mkdir(exist_ok=True, parents=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("演练报告: %s", REPORT_PATH)
    if has_failure:
        return 2
    if has_issue:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
