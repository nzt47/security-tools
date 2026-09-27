# -*- coding: utf-8 -*-
r"""yunshu learn —— DSH 历史会话 -> 经验库 的本地流水线（方案交付物 A）。

子命令（与方案 §5A 对齐）：
    probe   <session-dir>   扫描 schema，产出报告（P0）
    extract <session-dir>   提炼经验样本 -> samples.ndjson（P1）
    ingest  <ndjson>        建索引 + 审计留痕（P2）
    eval    <questions>     检索质量评测（P5）

【两种调用方式】
    python -m agent.experience_cli <sub> ...      仓库惯用（agent 已在 packages.find）
    yunshu learn <sub> ...                        方案原文写法（见 pyproject [project.scripts]）
  后者的 argv 会多一个 "learn"，main() 会自动跳过。

【零 token】全部子命令纯本地 CPU，不调用任何 API（方案硬约束 ①）。

【为何不注册进 capregistry】P0 实测其为构建期只读派生视图（无写入方法，
数据源是受 CI 守门的生成物），运行期经验数据无法接入 —— 已在方案「二·补」节记录。
"""
from __future__ import annotations

import argparse
import sys
from typing import List, Optional

__version__ = "1.0.0"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="yunshu learn",
        description="DSH 历史会话 -> 经验库 本地流水线（零 token）",
    )
    p.add_argument("--version", action="version", version="yunshu learn %s" % __version__)
    sub = p.add_subparsers(dest="command", required=True)

    def _common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--verbose", action="store_true", help="进度打到 stderr")

    # probe
    sp = sub.add_parser("probe", help="扫描会话库 schema，产出报告（P0）")
    sp.add_argument("sessions", help="会话根目录（递归查找 session.jsonl.zstd）")
    sp.add_argument("--out", help="Markdown 报告输出路径")
    sp.add_argument("--json-out", help="聚合 JSON 输出路径")
    sp.add_argument("--topn", type=int, default=20, help="工具名 top-N（默认 20）")
    sp.add_argument("--limit", type=int, help="只扫前 N 个文件（调试）")
    sp.add_argument("--privacy", action="store_true", help="附加脱敏命中统计（只计数）")
    sp.add_argument("--snapshot-until", dest="snapshot_until",
                    help="只统计 time <= 该 ISO 时间的记录（会话库是活的，冻结后结果可复现）")
    _common(sp)
    from .probe import cmd_probe
    sp.set_defaults(func=cmd_probe)

    # extract
    sp = sub.add_parser("extract", help="提炼经验样本（P1）")
    sp.add_argument("sessions", help="会话根目录或单个 session.jsonl.zstd")
    sp.add_argument("--out", required=True, help="samples.ndjson 输出路径")
    sp.add_argument("--rejected", help="rejected.ndjson 输出路径（拒收原因，不含内容）")
    sp.add_argument("--snapshot-until", dest="snapshot_until", help="冻结到该 ISO 时间")
    _common(sp)
    from .extract import cmd_extract
    sp.set_defaults(func=cmd_extract)

    # ingest
    sp = sub.add_parser("ingest", help="建检索索引 + 审计留痕（P2）")
    sp.add_argument("ndjson", help="samples.ndjson 路径")
    sp.add_argument("--persist-dir", dest="persist_dir",
                    default=__import__("os").path.join("data", "skill_vectors", "experience"))
    sp.add_argument("--no-vector", dest="no_vector", action="store_true",
                    help="只建 BM25 腿（不加载 BGE-m3，秒级完成）")
    _common(sp)
    from .ingest import cmd_ingest
    sp.set_defaults(func=cmd_ingest)

    # inspect
    sp = sub.add_parser("inspect", help="人工抽检辅助：把检索结果摊成待打勾的表（P5）")
    sp.add_argument("--question", action="append",
                    help="单个问题（可重复传多次）")
    sp.add_argument("--questions-file", dest="questions_file",
                    help="问题清单文件，每行一问（# 开头为注释）")
    sp.add_argument("--samples", required=True, help="samples.ndjson 路径")
    sp.add_argument("--persist-dir", dest="persist_dir",
                    default=__import__("os").path.join("data", "skill_vectors", "experience"))
    sp.add_argument("--top-k", dest="top_k", type=int, default=5)
    sp.add_argument("--min-score", dest="min_score", type=float, default=None,
                    help="相关性下限（BM25 原始分）；默认取实测标定值")
    sp.add_argument("--mark-out", dest="mark_out",
                    help="输出 TSV 标注表（含 relevant 空列，填 y/n）")
    sp.add_argument("--no-vector", dest="no_vector", action="store_true")
    _common(sp)
    from .inspect import cmd_inspect
    sp.set_defaults(func=cmd_inspect)

    # eval
    sp = sub.add_parser("eval", help="检索质量评测（P5）")
    sp.add_argument("questions", help="questions.yaml 路径")
    sp.add_argument("--samples", required=True, help="samples.ndjson 路径")
    sp.add_argument("--persist-dir", dest="persist_dir",
                    default=__import__("os").path.join("data", "skill_vectors", "experience"))
    sp.add_argument("--top-k", dest="top_k", type=int, default=5)
    sp.add_argument("--min-score", dest="min_score", type=float, default=None,
                    help="相关性下限（BM25 原始分）；默认取 index 的实测标定值")
    sp.add_argument("--no-vector", dest="no_vector", action="store_true")
    sp.add_argument("--json-out", dest="json_out")
    _common(sp)
    from .evaluate import cmd_eval
    sp.set_defaults(func=cmd_eval)

    return p


def _resolve_default_min_score() -> float:
    """相关性下限的默认取值：环境变量 CP_EXPERIENCE_MIN_SCORE > 常量默认。

    ⚠️ 该常量的标定依据已被独立复核证伪（详见 experience_index 中
    _DEFAULT_MIN_BM25_SCORE 的注释）—— 它衡量的是查询长度而非相关性。
    """
    import os
    env = os.environ.get("CP_EXPERIENCE_MIN_SCORE", "").strip()
    if env:
        try:
            return float(env)
        except ValueError:
            pass
    try:
        from agent.skills_mgmt.experience_index import _DEFAULT_MIN_BM25_SCORE
        return float(_DEFAULT_MIN_BM25_SCORE)
    except Exception:
        return 0.0


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    # 「yunshu learn probe ...」经由 console_scripts 进来时首个 token 是 "learn"
    if args and args[0] == "learn":
        args = args[1:]
    parser = build_parser()
    ns = parser.parse_args(args)
    if getattr(ns, "min_score", None) is None:
        # 优先级：显式 --min-score > 环境变量 CP_EXPERIENCE_MIN_SCORE > 常量默认。
        # 【为什么读环境变量】该键已在 agent/settings/registry.py 登记并声明"置 0 即关闭判定"，
        # 若 CLI 与注入路径都只读硬编码常量，运维改开关中心不会生效（登记表在说谎）。
        ns.min_score = _resolve_default_min_score()
    return int(ns.func(ns) or 0)


__all__ = ["main", "build_parser", "__version__"]
