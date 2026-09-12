"""隔离性实测探针（TASK-S8-03 步骤 5 / 验收清单第 4 条）

**它做什么**：对**每一个**隔离等级（`in_process` / `subprocess_hardened` /
`container`）分别跑同一份探针定义，把"环境 / 凭据 / 文件 / 网络 / 资源"五类边界
实测出来，输出**机器可读证据**（JSON）与**对比表**（Markdown）。

**为什么要"分别跑"**：只跑一条路径等于没测——差距恰恰藏在两条路径的**不同**里。
本脚本因此拒绝把某个等级的结果外推到另一个等级：跑不了的等级，单元格如实写
`未运行（<原因>）`，并另存 `skip_reason`。

**诚实底线（本脚本的立身之本）**

- 容器不可用时**不冒充容器**：`container` 等级写 `skipped` + 原始理由，
  而不是拿子进程的结果填格；
- 探针的"越界写"目标由本脚本自建、用后即删：它测的是**能不能**越出去，
  不是去破坏真实数据；
- 跑完对真实目录做**前后指纹比对**（"未双写"证据）并报告残留；
- 退出码：`0` = 所有选中等级都给出了结论（含"如实不可用"）；`2` = 某等级
  既没 skip 又一条探针都没跑成（真故障，不粉饰）。

用法::

    python scripts/verify_isolation.py                      # 三个等级都跑
    python scripts/verify_isolation.py --level container     # 只跑容器
    python scripts/verify_isolation.py --json out.json --md out.md
    python scripts/verify_isolation.py --quiet               # 只出文件

产物默认落在 `data/isolation/`（**运行时区，gitignore**）。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agent.digestion import isolation as ISO  # noqa: E402

DEFAULT_OUT_DIR = os.path.join(_ROOT, "data", "isolation")
EXIT_OK = 0
EXIT_EVIDENCE_MISSING = 2

#: 见证目录：放在仓库根下的临时目录，用来实测"真实环境未被改动"
WITNESS_DIRNAME = ".tmp_iso_witness"
WITNESS_FILENAME = "witness.txt"


def _executor_for(level: str, args: argparse.Namespace) -> Any:
    quota = ISO.IsolationQuota.from_env()
    if args.job_timeout:
        quota.timeout_s = float(args.job_timeout)
    if args.memory_mb:
        quota.memory_mb = int(args.memory_mb)
    if args.pids_limit:
        quota.pids_limit = int(args.pids_limit)
    common: Dict[str, Any] = {"quota": quota, "source_root": _ROOT}
    if level == ISO.ISOLATION_CONTAINER:
        return ISO.ContainerExecutor(image=args.image or "", **common)
    if level == ISO.ISOLATION_SUBPROCESS_HARDENED:
        return ISO.SubprocessHardenedExecutor(**common)
    return ISO.InProcessExecutor(**common)


def _run_level(level: str, args: argparse.Namespace,
               secret_dir: str) -> Tuple[Dict[str, Any], List[ISO.ProbeRun]]:
    """跑一个等级的探针套件 → （证据块, 原始 ProbeRun 列表）；**永不抛**"""
    started = time.time()
    block: Dict[str, Any] = {"level": level, "started_at": started,
                             "skipped": False, "skip_reason": "", "ran": 0,
                             "probes": [], "error": ""}
    try:
        executor = _executor_for(level, args)
    except Exception as exc:  # noqa: BLE001
        block.update({"skipped": True, "error": f"{type(exc).__name__}: {exc}",
                      "skip_reason": f"执行器构造失败：{exc}"})
        return block, []
    try:
        runs = ISO.run_probe_suite(executor, host_secret_dir=secret_dir,
                                   job_timeout_s=float(args.job_timeout))
    except Exception as exc:  # noqa: BLE001
        block.update({"error": f"{type(exc).__name__}: {exc}",
                      "skip_reason": f"探针套件异常：{exc}"})
        return block, []
    block["executor"] = executor.describe()
    block["probes"] = [r.to_dict() for r in runs]
    block["ran"] = sum(1 for r in runs if r.result.ran)
    if block["ran"] == 0:
        # 一条都没跑成 ⇒ **如实标为不可用**（Docker 不在 / 无执行隔离）
        first = runs[0].result if runs else None
        block["skipped"] = True
        block["skip_reason"] = ((first.error or first.refused_reason)
                                if first is not None else "无探针结果")
    block["finished_at"] = time.time()
    block["elapsed_s"] = round(block["finished_at"] - started, 3)
    return block, list(runs)


def _collect_levels(args: argparse.Namespace) -> List[str]:
    if str(args.level).strip().lower() == "all":
        return list(ISO.ISOLATION_LEVELS)
    normalized = ISO.normalize_level(args.level)
    if normalized is None:
        raise SystemExit(
            f"[verify_isolation] --level={args.level!r} 非法"
            f"（合法值：all / {' / '.join(ISO.ISOLATION_LEVELS)}）")
    return [normalized]


def _write_outputs(report: Dict[str, Any], args: argparse.Namespace,
                   markdown: str) -> Tuple[str, str]:
    json_path = args.json or os.path.join(args.out_dir, "isolation_evidence.json")
    md_path = args.md or os.path.join(args.out_dir, "isolation_comparison.md")
    for path in (json_path, md_path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(markdown)
        fh.write("\n## 未双写与残留\n\n")
        fh.write(f"- 真实目录前后指纹比对：`{json.dumps(report['not_double_written'], ensure_ascii=False)}`\n")
        fh.write("- 判读：`clean=true` 表示除**探针自己的落脚文件**外，"
                 "真实目录没有新增/修改/删除；源码树上的探针临时目录在清理后"
                 f"已消失（`source_tree_residue_after_cleanup="
                 f"{report['source_tree_residue_after_cleanup']}`）。\n")
        fh.write(f"- 清理前残留：{report['residue'] or '无'}\n")
        fh.write(f"- 清理后残留：{report['residue_after_cleanup'] or '无'}\n")
        fh.write("- 各等级结论："
                 + "；".join(f"`{lvl}` = {blk.get('skip_reason') or '已实测'}"
                             for lvl, blk in report["levels"].items()) + "\n")
    return json_path, md_path


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="隔离等级实测探针（TASK-S8-03 步骤 5）")
    parser.add_argument("--level", default="all",
                        help="all / in_process / subprocess_hardened / container")
    parser.add_argument("--image", default="", help="容器镜像（默认 python:3.12-slim）")
    parser.add_argument("--job-timeout", type=float, default=4.0,
                        help="单作业墙钟（秒）；超限探针据此推导量级")
    parser.add_argument("--memory-mb", type=int, default=0, help="内存配额（MB）")
    parser.add_argument("--pids-limit", type=int, default=0, help="进程数配额")
    parser.add_argument("--network-target", default="1.1.1.1:53",
                        help="网络探针目标 host:port")
    parser.add_argument("--json", default="", help="证据 JSON 输出路径")
    parser.add_argument("--md", default="", help="对比表 Markdown 输出路径")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR,
                        help="默认产物目录（gitignore 的运行时区）")
    parser.add_argument("--quiet", action="store_true", help="不向 stdout 打印对比表")
    args = parser.parse_args(list(argv) if argv is not None else None)

    levels = _collect_levels(args)
    os.makedirs(args.out_dir, exist_ok=True)

    # ── 探针落脚点（**本脚本自建、用后即删**）──────────────────
    secret_dir = tempfile.mkdtemp(prefix="cp-iso-verify-secret-")
    with open(os.path.join(secret_dir, "host-credential.txt"), "w",
              encoding="utf-8") as fh:
        fh.write("ISOLATION-PROBE-SENTINEL（宿主凭据替身；探针用后删除）")
    source_probe_dir = os.path.join(_ROOT, ISO.SOURCE_PROBE_DIRNAME)
    #: 见证目录：只为"真实环境未被改动"提供一个小而确定的观测点（**不扫全仓**：
    #: 对整棵源码树逐个哈希既慢又会把 .git 卷进来，那不是"证据"，是噪声）
    witness_dir = os.path.join(_ROOT, WITNESS_DIRNAME)
    os.makedirs(witness_dir, exist_ok=True)
    witness_file = os.path.join(witness_dir, WITNESS_FILENAME)
    with open(witness_file, "w", encoding="utf-8") as fh:
        fh.write("witness-v1")

    watched = [secret_dir, witness_file, source_probe_dir]
    before = ISO.snapshot_paths([secret_dir, witness_file])
    report: Dict[str, Any] = {
        "task": "TASK-S8-03",
        "generated_at": time.time(),
        "generated_at_iso": time.strftime("%Y-%m-%d %H:%M:%S"),
        "platform": {"sys_platform": sys.platform,
                     "python": sys.version.split()[0],
                     "executable": sys.executable, "repo_root": _ROOT},
        "docker": ISO.probe_docker(refresh=True).to_dict(),
        "requested_levels": levels,
        "job_timeout_s": float(args.job_timeout),
        "network_target": args.network_target,
        "host_secret_dir": secret_dir,
        "levels": {},
        "comparison": [],
        "not_double_written": {},
        "residue": [],
        "residue_after_cleanup": [],
        "notes": [
            "单元格逐格来自本脚本的实测输出；未运行的等级不外推、不补写。",
            "越界写探针的落点由本脚本自建（宿主临时目录 / 容器内未挂载的 "
            "/host-secrets），用后即删：探针测的是「能不能」，不是去破坏。",
            "容器路径的 HOME 由 Docker/runc 按 passwd 条目覆写（实测 -e HOME= "
            "挡不住），故另记 env_raw 平台原始值；执行体内再统一清空为空串。",
            "子进程等级的「宿主凭据（绝对路径）可见」「源码树可写」等格子是"
            "**如实差距**，不是缺陷掩盖：该等级本就没有内核级边界。",
        ],
    }

    runs_by_level: Dict[str, List[ISO.ProbeRun]] = {}
    try:
        for level in levels:
            if not args.quiet:
                print(f"[verify_isolation] 运行等级 {level} …", flush=True)
            block, runs = _run_level(level, args, secret_dir)
            report["levels"][level] = block
            runs_by_level[level] = runs
    finally:
        diff = ISO.diff_snapshot(before, ISO.snapshot_paths([secret_dir, witness_file]))
        #: 越界写探针**本来就会**在它自己的落脚目录里造出这个文件——那不是
        #: "双写真实环境"，而是**证据本身**（证明该等级越得出去）。故把它单列，
        #: 并把"干净"定义为：除它以外没有新增/修改/删除。
        # 注意：`diff` 里的条目可能带盘符（`C:\...`），故**只归一分隔符、不按冒号切**
        # ——按 ":" 切会把 `C:` 切成 `C`，于是探针自己的产物被报成"意外新增"，
        # 把一份本该 clean 的证据说成不干净（实现期实测踩到过）。
        def _norm(p: Any) -> str:
            return str(p).replace("\\", "/").rstrip("/")

        expected_artifact = _norm(os.path.join(secret_dir, "escape-probe.txt"))
        created = {_norm(p) for p in (diff.get("created") or [])}
        diff["expected_probe_artifacts"] = sorted(p for p in created
                                                 if p == expected_artifact)
        diff["unexpected"] = sorted(p for p in created if p != expected_artifact)
        diff["clean"] = bool(not diff.get("changed") and not diff.get("removed")
                             and not diff["unexpected"])
        report["not_double_written"] = diff
        report["residue"] = [p for p in watched if os.path.exists(p)]
        if os.path.isdir(source_probe_dir):
            shutil.rmtree(source_probe_dir, ignore_errors=True)
        escape_artifact = os.path.join(secret_dir, "escape-probe.txt")
        if os.path.exists(escape_artifact):
            try:
                os.remove(escape_artifact)
            except OSError:
                pass
        if os.path.isdir(witness_dir):
            shutil.rmtree(witness_dir, ignore_errors=True)
        shutil.rmtree(secret_dir, ignore_errors=True)
        report["residue_after_cleanup"] = [p for p in watched if os.path.exists(p)]
        report["source_tree_residue_after_cleanup"] = os.path.exists(source_probe_dir)

    rows = ISO.comparison_rows(runs_by_level)
    for row in rows:
        for level in ISO.ISOLATION_LEVELS:
            if level in runs_by_level:
                continue
            blk = report["levels"].get(level)
            row[level] = ("未测（本次未选中）" if blk is None
                          else f"未运行（{blk.get('skip_reason') or '无结论'}）")
    report["comparison"] = rows
    markdown = ISO.render_comparison_markdown(rows)

    json_path, md_path = _write_outputs(report, args, markdown)
    if not args.quiet:
        print(markdown)
        print(f"[verify_isolation] 证据 JSON → {json_path}")
        print(f"[verify_isolation] 对比表   → {md_path}")

    broken = [lvl for lvl, blk in report["levels"].items()
              if not blk.get("skipped") and not blk.get("ran")]
    if broken:
        print(f"[verify_isolation] 故障：等级 {broken} 既未 skip 也无任何探针结果",
              file=sys.stderr)
        return EXIT_EVIDENCE_MISSING
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
