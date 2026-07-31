#!/usr/bin/env python3
"""性能趋势图生成 — SQLite vs etcd P99 延迟变化（最近 N 次 CI/CD 运行）

用途:
  基于 perf_history/ 下的历史报告或 GitHub Actions artifact，
  生成 SQLite 与 etcd 方案的 P99 延迟趋势对比图（PNG）。

数据来源（三种模式）:
  1. --source auto (默认): 自动探测 CI 运行数，>=5 次成功则用 CI，否则回退本地
  2. --source local: 读取 scripts/perf_history/*.json
     本地多次运行 ci_semantic_perf_regression.py 后快照的报告
  3. --source ci: 用 gh CLI 拉取最近 N 次 perf-regression workflow 运行的 artifact
     需要本机已 gh auth login，且仓库有写权限

运行:
  # 自动模式（默认）— CI 累积 5 次后自动切换为真实 CI 数据
  python scripts/gen_perf_trend.py
  python scripts/gen_perf_trend.py --runs 5

  # 强制本地模式
  python scripts/gen_perf_trend.py --source local

  # 强制 CI 模式（从 GitHub Actions 拉取最近 5 次 artifact）
  python scripts/gen_perf_trend.py --source ci --runs 5

  # 自定义输出路径
  python scripts/gen_perf_trend.py --output docs/perf-charts/custom.png

输出:
  docs/perf-charts/perf_trend_5runs.png  (默认)

设计约束:
  【不易】数据必须真实，禁止编造；数据不足时明确提示并退出
  【变易】auto 模式按 CI 累积运行数自动切换数据源，无需人工干预
  【简易】单一 PNG 输出，包含基线 + SLA 阈值参考线
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import List, Dict, Any, Optional

# 加入项目根
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 配置中文字体（避免 matplotlib 警告）
import matplotlib
matplotlib.use("Agg")  # 非交互模式，避免 Windows 显示问题
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties

# SLA 阈值（与 ci_semantic_perf_regression.py 保持一致，【不易】不可变量）
SQLITE_P99_REGRESSION_MS = 5.0    # SQLite P99 回归阈值
ETCD_P99_SLA_MS = 40.0            # etcd P99 SLA 阈值


def _get_chinese_font() -> Optional[FontProperties]:
    """查找可用的中文字体"""
    candidates = [
        "Microsoft YaHei",      # Windows 微软雅黑
        "SimHei",               # Windows 黑体
        "PingFang SC",          # macOS 苹方
        "Noto Sans CJK SC",    # Linux 思源
        "WenQuanYi Micro Hei",  # Linux 文泉驿
    ]
    for font_name in candidates:
        try:
            font = FontProperties(family=font_name)
            if font.get_name() == font_name:
                return font
        except Exception:
            continue
    return None


_CHINESE_FONT = _get_chinese_font()
if _CHINESE_FONT:
    plt.rcParams["font.family"] = _CHINESE_FONT.get_name()
    plt.rcParams["axes.unicode_minus"] = False  # 修复负号显示


# ═══════════════════════════════════════════════════════════════
# 数据加载
# ═══════════════════════════════════════════════════════════════

def load_from_local(history_dir: Path) -> List[Dict[str, Any]]:
    """从本地 perf_history/ 目录加载历史报告

    文件命名: run_1.json, run_2.json, ...（按文件名排序）
    """
    if not history_dir.exists():
        return []
    files = sorted(history_dir.glob("run_*.json"))
    reports = []
    for f in files:
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
                data["_source_file"] = f.name
                reports.append(data)
        except Exception as e:
            print("  ⚠️ 跳过 %s: %s" % (f.name, e))
    return reports


def load_from_ci(runs: int, repo: str) -> List[Dict[str, Any]]:
    """从 GitHub Actions 拉取最近 N 次 perf-regression 运行的 artifact

    依赖: gh CLI 已登录，且仓库有读取权限
    """
    print("[ci] 拉取最近 %d 次 perf-regression 运行..." % runs)
    run_list = _query_ci_runs(runs, repo)
    if not run_list:
        return []

    reports = []
    for run_info in run_list:
        run_id = run_info.get("databaseId")
        if run_info.get("conclusion") != "success":
            print("  ⚠️ 跳过运行 %s (status=%s conclusion=%s)" % (
                run_id, run_info.get("status"), run_info.get("conclusion")))
            continue
        try:
            artifact = _download_run_artifact(run_id, repo)
            if artifact:
                artifact["_source_file"] = "ci_run_%s" % run_id
                reports.append(artifact)
        except Exception as e:
            print("  ⚠️ 运行 %s artifact 拉取失败: %s" % (run_id, e))
    return reports


def count_ci_success_runs(repo: str, probe_limit: int = 10) -> int:
    """轻量探针: 统计 CI 最近成功的 perf-regression 运行数（不下载 artifact）

    用于 auto 模式判定是否切换到 CI 数据源。
    仅查询运行列表，不触发 artifact 下载，速度快（单次 gh api 调用）。

    Args:
        repo: GitHub 仓库 (owner/repo)
        probe_limit: 探查最近的运行数（默认 10）

    Returns:
        成功 conclusion=success 的运行数；gh 不可用时返回 0
    """
    run_list = _query_ci_runs(probe_limit, repo)
    if not run_list:
        return 0
    return sum(1 for r in run_list if r.get("conclusion") == "success")


def _query_ci_runs(limit: int, repo: str) -> List[Dict[str, Any]]:
    """查询 GitHub Actions perf-regression 运行列表（gh CLI 内部封装）

    【不易】gh CLI 不可用/未登录时返回空列表，不抛异常
    """
    try:
        result = subprocess.run(
            ["gh", "run", "list", "--workflow", "semantic-perf-regression.yml",
             "--repo", repo, "--limit", str(limit),
             "--json", "databaseId,status,conclusion"],
            capture_output=True, text=True, check=True, timeout=30,
        )
        return json.loads(result.stdout) or []
    except FileNotFoundError:
        print("  ⚠️ gh CLI 未安装，CI 数据源不可用")
        return []
    except subprocess.CalledProcessError as e:
        print("  ⚠️ gh CLI 调用失败: %s" % (e.stderr or str(e)))
        return []
    except Exception as e:
        print("  ⚠️ 查询 CI 运行列表失败: %s" % e)
        return []


def _download_run_artifact(run_id: int, repo: str) -> Optional[Dict[str, Any]]:
    """下载单次运行的 perf-report artifact 并解析"""
    result = subprocess.run(
        ["gh", "api", "repos/%s/actions/runs/%s/artifacts" % (repo, run_id)],
        capture_output=True, text=True, check=True, timeout=30,
    )
    artifacts = json.loads(result.stdout).get("artifacts", [])
    # 优先 perf-report，其次 perf-baseline-weekly
    target = next((a for a in artifacts if a["name"] == "perf-report"),
                  next((a for a in artifacts if a["name"] == "perf-baseline-weekly"), None))
    if not target:
        return None

    # 下载 artifact（zip 格式）
    download_url = target["archive_download_url"]
    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = Path(tmpdir) / "artifact.zip"
        subprocess.run(
            ["gh", "api", download_url, "-H", "Accept: application/vnd.github+json"],
            stdout=open(zip_path, "wb"), check=True, timeout=60,
        )
        with zipfile.ZipFile(zip_path) as zf:
            # 优先读 perf_report_latest.json，其次 perf_baseline.json
            for member in ["perf_report_latest.json", "perf_baseline.json"]:
                if member in zf.namelist():
                    with zf.open(member) as fp:
                        return json.loads(fp.read().decode("utf-8"))
    return None


def load_baseline(baseline_file: Path) -> Optional[Dict[str, Any]]:
    """加载当前基线（用于在图表上画参考线）"""
    if not baseline_file.exists():
        return None
    try:
        with open(baseline_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════
# 图表生成
# ═══════════════════════════════════════════════════════════════

def generate_trend_chart(reports: List[Dict[str, Any]],
                          baseline: Optional[Dict[str, Any]],
                          output_path: Path) -> bool:
    """生成 P99 趋势对比图

    Args:
        reports: 历史报告列表（每个含 sqlite.p99 和 etcd.p99）
        baseline: 当前基线（含 sqlite.p99 和 etcd.p99），可为 None
        output_path: PNG 输出路径

    Returns:
        True 表示成功生成；False 表示数据不足
    """
    if len(reports) < 2:
        print("  ❌ 数据点不足（%d < 2），无法生成趋势图" % len(reports))
        return False

    # 提取数据序列
    x_labels = []
    sqlite_p99 = []
    etcd_p99 = []
    for i, r in enumerate(reports, 1):
        x_labels.append("Run %d\n%s" % (i, r.get("timestamp", "")[5:16]))  # MM-DD HH:MM
        sqlite_p99.append(r.get("sqlite", {}).get("p99", 0))
        etcd_p99.append(r.get("etcd", {}).get("p99", 0))

    # 创建图表
    fig, ax = plt.subplots(figsize=(12, 6.5), dpi=120)

    # 主数据线
    sqlite_line, = ax.plot(x_labels, sqlite_p99, marker="o", linewidth=2.2,
                            markersize=9, color="#27ae60", label="SQLite P99",
                            zorder=3)
    etcd_line, = ax.plot(x_labels, etcd_p99, marker="s", linewidth=2.2,
                          markersize=9, color="#2980b9", label="etcd P99",
                          zorder=3)

    # 数据标签（每个点标注数值）
    for i, (s, e) in enumerate(zip(sqlite_p99, etcd_p99)):
        ax.annotate("%.2f" % s, (i, s), textcoords="offset points",
                    xytext=(0, 10), ha="center", fontsize=9,
                    color="#27ae60", fontweight="bold")
        ax.annotate("%.2f" % e, (i, e), textcoords="offset points",
                    xytext=(0, -15), ha="center", fontsize=9,
                    color="#2980b9", fontweight="bold")

    # SLA 阈值参考线（【不易】硬约束）
    ax.axhline(y=SQLITE_P99_REGRESSION_MS, color="#e74c3c", linestyle="--",
               linewidth=1.2, alpha=0.7,
               label="SQLite 回归阈值 (%.0fms)" % SQLITE_P99_REGRESSION_MS)
    ax.axhline(y=ETCD_P99_SLA_MS, color="#c0392b", linestyle=":",
               linewidth=1.2, alpha=0.7,
               label="etcd SLA 阈值 (%.0fms)" % ETCD_P99_SLA_MS)

    # 基线参考线（如果存在）
    if baseline:
        base_sqlite = baseline.get("sqlite", {}).get("p99", 0)
        base_etcd = baseline.get("etcd", {}).get("p99", 0)
        if base_sqlite > 0:
            ax.axhline(y=base_sqlite, color="#27ae60", linestyle="-.",
                       linewidth=1.0, alpha=0.5,
                       label="SQLite 基线 (%.2fms)" % base_sqlite)
        if base_etcd > 0:
            ax.axhline(y=base_etcd, color="#2980b9", linestyle="-.",
                       linewidth=1.0, alpha=0.5,
                       label="etcd 基线 (%.2fms)" % base_etcd)

    # 坐标轴 & 标题
    ax.set_xlabel("CI/CD 运行序号（时间倒序最右）", fontsize=11)
    ax.set_ylabel("P99 延迟 (ms)", fontsize=11)
    n = len(reports)
    ax.set_title("语义层配置读取性能趋势: SQLite vs etcd (最近 %d 次 CI/CD 运行)" % n,
                 fontsize=13, fontweight="bold", pad=15)

    # Y 轴对数刻度（etcd 和 SQLite 数量级差异大）
    ax.set_yscale("log")
    y_min = max(0.1, min(min(sqlite_p99), min(etcd_p99)) * 0.5)
    y_max = max(max(sqlite_p99), max(etcd_p99)) * 1.8
    ax.set_ylim(y_min, y_max)

    ax.grid(True, which="both", alpha=0.3, linestyle="-")
    ax.legend(loc="upper left", fontsize=9, framealpha=0.9)

    # 底部说明
    fig.text(0.5, 0.01,
             "数据来源: %s | 测试参数: n=%s, concurrency=%s | 生成时间: %s" % (
                 reports[0].get("_source_file", "unknown"),
                 reports[0].get("test_params", {}).get("n", "?"),
                 reports[0].get("test_params", {}).get("concurrency", "?"),
                 _now_str(),
             ),
             ha="center", fontsize=8, color="#7f8c8d")

    plt.tight_layout(rect=[0, 0.03, 1, 1])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=120, bbox_inches="tight")
    plt.close()
    print("  ✅ 趋势图已生成: %s" % output_path)
    return True


def _now_str() -> str:
    import time
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="生成 SQLite/etcd P99 性能趋势图")
    parser.add_argument("--source", choices=["auto", "local", "ci"], default="auto",
                        help="数据源: auto(默认,CI>=5次自动切换) / local / ci")
    parser.add_argument("--runs", type=int, default=5,
                        help="选取最近 N 次运行（默认 5）")
    parser.add_argument("--ci-min-runs", type=int, default=5,
                        help="auto 模式下切换到 CI 数据源的最小成功运行数（默认 5）")
    parser.add_argument("--output", type=str, default=None,
                        help="输出 PNG 路径（默认 docs/perf-charts/perf_trend_{N}runs.png）")
    parser.add_argument("--repo", type=str, default=None,
                        help="CI 模式下 GitHub 仓库 (owner/repo)，默认从 git remote 推断")
    args = parser.parse_args()

    history_dir = ROOT / "scripts" / "perf_history"
    baseline_file = ROOT / "scripts" / "perf_baseline.json"

    print("=" * 60)
    print(" 性能趋势图生成: SQLite vs etcd P99")
    print("=" * 60)
    print(" 数据源: %s, 最近 %d 次运行" % (args.source, args.runs))

    # auto 模式: 探测 CI 成功运行数，>=阈值则用 CI，否则回退本地
    effective_source = args.source
    if args.source == "auto":
        effective_source = _auto_select_source(args.ci_min_runs, args.repo)
        print(" 数据源选择: auto → %s" % effective_source)

    # 加载数据
    if effective_source == "ci":
        repo = args.repo or _infer_repo()
        if not repo:
            print("  ❌ 无法推断 GitHub 仓库，请用 --repo owner/repo 指定")
            return 1
        reports = load_from_ci(args.runs, repo)
        # CI 模式数据不足时回退到本地（防御式降级）
        if len(reports) < 2:
            print("  ⚠️ CI artifact 数据不足，回退到本地 perf_history/")
            reports = load_from_local(history_dir)
    else:
        reports = load_from_local(history_dir)

    # 截取最近 N 次
    if len(reports) > args.runs:
        reports = reports[-args.runs:]
        print("  截取最近 %d 次（共 %d 条历史）" % (args.runs, len(reports) + len(reports) - args.runs))

    print("\n 数据汇总:")
    for i, r in enumerate(reports, 1):
        s = r.get("sqlite", {}).get("p99", "?")
        e = r.get("etcd", {}).get("p99", "?")
        print("  Run %d: SQLite P99=%s ms, etcd P99=%s ms" % (i, s, e))

    # 加载基线
    baseline = load_baseline(baseline_file)
    if baseline:
        print("\n 当前基线: SQLite P99=%.3fms, etcd P99=%.3fms (updated %s)" % (
            baseline.get("sqlite", {}).get("p99", 0),
            baseline.get("etcd", {}).get("p99", 0),
            baseline.get("updated_at", "?")))

    # 生成图表
    output_path = Path(args.output) if args.output else (
        ROOT / "docs" / "perf-charts" / ("perf_trend_%druns.png" % args.runs))

    print("\n 生成图表...")
    success = generate_trend_chart(reports, baseline, output_path)
    if not success:
        print("\n ❌ 图表生成失败（数据不足）")
        print("    本地模式: 多次运行 python scripts/ci_semantic_perf_regression.py 后")
        print("    将生成的 perf_report_latest.json 复制到 scripts/perf_history/run_N.json")
        return 1

    print("\n ✅ 完成")
    return 0


def _auto_select_source(ci_min_runs: int, repo_override: Optional[str]) -> str:
    """auto 模式数据源选择: 探测 CI 成功运行数，>=阈值返回 'ci'，否则 'local'

    【不易】gh CLI 不可用/未登录时静默回退 'local'，不抛异常
    【变易】阈值可配（--ci-min-runs），默认 5 次
    【简易】仅做轻量探针（count_ci_success_runs），不下载 artifact
    """
    repo = repo_override or _infer_repo()
    if not repo:
        print("  [auto] 无法推断 GitHub 仓库，使用本地数据源")
        return "local"

    print("  [auto] 探测 CI 成功运行数 (repo=%s, 阈值=%d)..." % (repo, ci_min_runs))
    success_count = count_ci_success_runs(repo, probe_limit=max(ci_min_runs * 2, 10))

    if success_count >= ci_min_runs:
        print("  [auto] CI 已累积 %d 次成功运行 (>= %d)，切换到 CI 数据源" % (
            success_count, ci_min_runs))
        return "ci"
    else:
        print("  [auto] CI 仅 %d 次成功运行 (< %d)，使用本地数据源" % (
            success_count, ci_min_runs))
        return "local"


def _infer_repo() -> Optional[str]:
    """从 git remote 推断 owner/repo"""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, check=True, timeout=5,
        )
        url = result.stdout.strip()
        # git@github.com:owner/repo.git 或 https://github.com/owner/repo.git
        if "github.com" not in url:
            return None
        if url.startswith("git@"):
            path = url.split(":", 1)[1]
        else:
            path = url.split("github.com/", 1)[1] if "github.com/" in url else url
        path = path.replace(".git", "").strip("/")
        return path if "/" in path else None
    except Exception:
        return None


if __name__ == "__main__":
    sys.exit(main())
