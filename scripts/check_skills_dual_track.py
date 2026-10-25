#!/usr/bin/env python
"""技能双轨对账：文件轨（data/skills_repo/<id>/skill.md）vs 主轨（data/skills_mgmt.json）。

【为什么需要】交接单 §8 陷阱 26：本机 gstack 的主轨条目在某次本地运行后**消失**，
只剩文件轨 ⇒ 它成了"文件轨有、主轨没有"的孤儿，H-3 用例转红。根因未定位，
所以这里做两件事：
  1. `--check`：把孤儿（文件轨有、主轨没有）与主轨独有项列出来，孤儿存在即非零退出
     —— 可以在部署后当作一次只读体检跑，不必等到用例红；
  2. `--fix`：走**生产写路径** `POST /api/skills-mgmt/backfill {"dry_run": false}`
     从 skill.md 补登记（不手改 JSON，理由同陷阱 26）。

用法：
    python scripts/check_skills_dual_track.py                 # 只看
    python scripts/check_skills_dual_track.py --json
    python scripts/check_skills_dual_track.py --fix           # 调生产写路径补登记（需服务在跑）

退出码：0 一致 / 1 有孤儿 / 2 参数或环境错误。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
SKILLS_REPO = ROOT / "data" / "skills_repo"
MAIN_TRACK = ROOT / "data" / "skills_mgmt.json"


def load_file_track(repo_dir: Optional[Path] = None) -> Dict[str, str]:
    """文件轨：{skill_id: skill.md 路径}（跳过 .index/.migration/.vector_index 等点目录）。"""
    base = Path(repo_dir) if repo_dir else SKILLS_REPO
    out: Dict[str, str] = {}
    if not base.is_dir():
        return out
    for child in sorted(base.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        md = child / "skill.md"
        if md.is_file():
            out[child.name] = str(md)
    return out


def load_main_track(path: Optional[Path] = None) -> Dict[str, Any]:
    """主轨：{skill_id: 记录}（读 data/skills_mgmt.json；缺失/损坏按空处理）。"""
    target = Path(path) if path else MAIN_TRACK
    if not target.is_file():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if isinstance(data, dict):
        # 常见形态：{id: {...}}；也兼容 {"skills": [...]}
        if isinstance(data.get("skills"), list):
            return {str(item.get("id")): item for item in data["skills"]
                    if isinstance(item, dict) and item.get("id")}
        return {str(k): v for k, v in data.items() if isinstance(v, dict)}
    if isinstance(data, list):
        return {str(item.get("id")): item for item in data
                if isinstance(item, dict) and item.get("id")}
    return {}


def find_orphans(file_track: Dict[str, Any], main_track: Dict[str, Any]) -> List[str]:
    """文件轨有、主轨没有 —— 陷阱 26 的症状（主轨被运行期清理掉）。

    【为什么只报这一侧】"主轨有、文件轨没有"是另一种形态（技能只在 DB），
    仓库存量允许（#1288 之前就有 8 条），且不影响能力清单口径，故只作提示。
    """
    return sorted(sid for sid in file_track if sid not in main_track)


def find_main_only(file_track: Dict[str, Any], main_track: Dict[str, Any]) -> List[str]:
    return sorted(sid for sid in main_track if sid not in file_track)


def _token() -> str:
    import os

    value = str(os.environ.get("FLASK_API_TOKEN") or "").strip()
    if value:
        return value
    env_file = ROOT / ".env"
    if env_file.is_file():
        try:
            for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line.startswith("FLASK_API_TOKEN="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            return ""
    return ""


def backfill(base_url: str = "http://127.0.0.1:5678", timeout: float = 120.0) -> Dict[str, Any]:
    """从 skill.md 补登记到主轨（生产写路径；失败时异常原文只用于本进程输出）。"""
    body = json.dumps({"dry_run": False}, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    token = _token()
    if token:
        headers["X-API-Token"] = token
    request = urllib.request.Request(base_url.rstrip("/") + "/api/skills-mgmt/backfill",
                                     data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise RuntimeError(f"backfill 被拒绝（HTTP {exc.code}）：{detail}") from exc
    try:
        return json.loads(raw or "{}")
    except ValueError:
        return {"raw": raw[:200]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="技能双轨对账（文件轨 vs 主轨）")
    parser.add_argument("--skills-repo", default="", help="文件轨根（默认 data/skills_repo）")
    parser.add_argument("--main-track", default="", help="主轨 JSON（默认 data/skills_mgmt.json）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--fix", action="store_true", help="调生产写路径补登记（需服务在跑）")
    parser.add_argument("--base-url", default="http://127.0.0.1:5678")
    args = parser.parse_args(argv)

    file_track = load_file_track(Path(args.skills_repo) if args.skills_repo else None)
    main_track = load_main_track(Path(args.main_track) if args.main_track else None)
    orphans = find_orphans(file_track, main_track)
    main_only = find_main_only(file_track, main_track)
    report = {
        "file_track": len(file_track),
        "main_track": len(main_track),
        "orphans": orphans,
        "main_only": main_only,
        "ok": not orphans,
    }
    if args.fix and orphans:
        try:
            report["backfill"] = backfill(args.base_url)
        except Exception as exc:  # noqa: BLE001 补登记失败要如实报
            report["backfill_error"] = f"{type(exc).__name__}: {exc}"
        file_track = load_file_track(Path(args.skills_repo) if args.skills_repo else None)
        main_track = load_main_track(Path(args.main_track) if args.main_track else None)
        report["orphans"] = find_orphans(file_track, main_track)
        report["ok"] = not report["orphans"]

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"文件轨 {report['file_track']} 条 / 主轨 {report['main_track']} 条")
        if report["orphans"]:
            print("[孤儿] 文件轨有、主轨没有（陷阱 26）：")
            for sid in report["orphans"]:
                print(f"  - {sid}")
            print("修法：python scripts/check_skills_dual_track.py --fix "
                  "（走 POST /api/skills-mgmt/backfill，不手改 JSON）")
        else:
            print("[OK] 无孤儿：每条 skill.md 都能在主轨找到登记")
        if report["main_only"]:
            print(f"[提示] 主轨独有 {len(report['main_only'])} 条（只在 DB，非孤儿）："
                  f"{', '.join(report['main_only'][:8])}"
                  f"{' …' if len(report['main_only']) > 8 else ''}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
