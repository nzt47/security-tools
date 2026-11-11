# -*- coding: utf-8 -*-
"""把 GitHub Actions 最近几次运行结果同步成 `data/ci/status.json`（报警器的事件源之一）。

【为什么是"同步脚本 + 读文件"，而不是报警器自己去跑 gh】
采集线程每 60s 一轮。若它直接起 `gh` 子进程：① 每轮都要等网络（几秒），报警器会变成新的
不稳定源；② 无 gh / 未登录的环境里每轮都要走一次失败路径。故把"取数"与"判读"分开：
本脚本用 gh 取最近 N 次运行写成状态文件，报警器（`agent/alarms/collector.read_ci_status`）
只读文件 —— 文件缺失或过期（默认 2h）就如实按"数据源不可用"处理（不报警、不判恢复）。

用法：
    python scripts/sync_ci_status.py              # 同步（默认最近 10 次运行）
    python scripts/sync_ci_status.py --limit 20
    python scripts/sync_ci_status.py --check      # 只打印将要写入的内容，不落盘

输出（data/ci/status.json，运行期状态，不入库）：
    {"synced_at": "...", "source": "gh run list", "repo": "owner/name", "runs": [
        {"workflow": "云枢系统测试流程", "name": "...", "status": "completed",
         "conclusion": "success", "branch": "master", "created_at": "...",
         "database_id": 123, "url": "...", "title": "..."}]}
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = ROOT / "data" / "ci" / "status.json"

#: gh 单次调用的超时（秒）：同步脚本是人工/调度触发，不该无限等
GH_TIMEOUT_SEC = 60

#: gh 的 JSON 字段（少取字段 = 少泄露面；url 用浏览器可点的 URL，不含令牌）
GH_FIELDS = "databaseId,name,displayTitle,workflowName,status,conclusion,headBranch,createdAt,url"


def _run_gh(limit: int) -> Optional[List[Dict[str, Any]]]:
    """跑 `gh run list --json ...`；失败返回 None（调用方据此给出可操作的原因）。"""
    cmd = ["gh", "run", "list", "--limit", str(limit), "--json", GH_FIELDS]
    try:
        # 【必须显式 utf-8】Windows 默认按本地代码页（gbk）解码子进程输出，gh 的中文
        #   （工作流名/标题）会直接抛 UnicodeDecodeError（2026-11-11 实测踩到）。
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=GH_TIMEOUT_SEC)
    except FileNotFoundError:
        print("[sync-ci] 找不到 gh 命令：本机没装 GitHub CLI（或不在 PATH）", file=sys.stderr)
        return None
    except subprocess.TimeoutExpired:
        print("[sync-ci] gh 超时（%ds）" % GH_TIMEOUT_SEC, file=sys.stderr)
        return None
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-1:] or [""]
        print("[sync-ci] gh 失败（exit=%s）：%s" % (proc.returncode, tail[0][:200]), file=sys.stderr)
        return None
    try:
        data = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        print("[sync-ci] gh 输出不是合法 JSON", file=sys.stderr)
        return None
    return data if isinstance(data, list) else None


def _repo_slug() -> str:
    """当前仓库 slug（失败留空，不影响同步）"""
    try:
        proc = subprocess.run(["gh", "repo", "view", "--json", "nameWithOwner"],
                              cwd=str(ROOT), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=GH_TIMEOUT_SEC)
        if proc.returncode == 0:
            return str((json.loads(proc.stdout or "{}") or {}).get("nameWithOwner") or "")
    except Exception:  # noqa: BLE001 取不到仓库名不算失败
        pass
    return ""


def build_payload(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """gh 运行列表 → 报警器读的状态文件（字段名与 `collector.ci_candidates` 对齐）。"""
    rows = []
    for run in runs:
        rows.append({
            "workflow": str(run.get("workflowName") or run.get("name") or ""),
            "name": str(run.get("name") or ""),
            "title": str(run.get("displayTitle") or ""),
            "status": str(run.get("status") or ""),
            "conclusion": str(run.get("conclusion") or ""),
            "branch": str(run.get("headBranch") or ""),
            "created_at": str(run.get("createdAt") or ""),
            "database_id": run.get("databaseId"),
            "url": str(run.get("url") or ""),
        })
    return {
        "synced_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "gh run list",
        "repo": _repo_slug(),
        "runs": rows,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="同步 GitHub Actions 最近运行结果（报警器事件源）")
    parser.add_argument("--limit", type=int, default=10, help="取最近多少次运行（默认 10）")
    parser.add_argument("--check", action="store_true", help="只打印，不落盘")
    args = parser.parse_args(argv)

    runs = _run_gh(max(1, int(args.limit)))
    if runs is None:
        return 2
    payload = build_payload(runs)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.check:
        print(text)
        return 0
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(text + "\n", encoding="utf-8")
    failed = [r for r in payload["runs"] if r["conclusion"] in ("failure", "timed_out", "startup_failure")]
    print("[sync-ci] 已写入 %s（%d 次运行，%d 次失败结论）"
          % (OUT_PATH.relative_to(ROOT), len(payload["runs"]), len(failed)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
