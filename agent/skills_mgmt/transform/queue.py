# -*- coding: utf-8 -*-
"""P4：转化**队列**（把 P2 的归档摆到界面上） + 按 transform_id 的**反向补丁回滚**。

【为什么要队列】#1398 的 P2 把产物落在 `data/ingestion/transforms/<sha256:12>/`
（raw / card / compare / review / manifest），但**界面看不见**：用户只能 ls 目录才知道
"上一条外来技能被判定成 duplicate 还是 conflict、审核有没有 block、最后并进了谁"。
本模块给出只读清单 + 单条详情，供 `/api/skills-mgmt/transforms` 直接吐给界面。

【回滚口径（§11.37 C · P4 承诺的那一条）】
P2 的落地只有两种"补丁"：
  - `--into <id>`  ⇒ 写 `data/skills_repo/<id>/CARD.md`；
  - `--new <id>`   ⇒ 写 `data/skills_repo/<id>/skill.md` + `CARD.md`。
回滚就是**只删这两类文件**，且有两条硬约束：
  1. **内容必须与归档一致**（sha256 比对 `card.md` / `skill.md`）—— 人改过的文件一律不删，
     报 `modified` 并让人自己决定（半删会造成"卡在中间"的状态，比不删更糟）；
  2. **路径必须在 `data/skills_repo/` 之下**（用 resolve + relative_to 校验，不是字符串前缀）。
另外**永不动归档目录本身**：只写 `manifest.json` 的 `rolled_back_at`，保留 review 证据链。

【边界】本模块不碰主轨登记（DB 行）。若该技能已被登记，回滚只在 `hints` 里提示
"还要去技能中心停用/删除"，不代做 —— 主轨与文件轨是两套，混淆过一次（陷阱 7）。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[3]
TRANSFORMS = Path("data") / "ingestion" / "transforms"
SKILLS_REPO = Path("data") / "skills_repo"

#: transform_id = sha256(raw)[:12]（见 apply.transform_id），只需校验形状。
_TID_RE = re.compile(r"^[0-9a-f]{12}$")

#: 单条详情里这些正文不回吐（raw 是原文，可能很大；界面按需再看磁盘）。
_DETAIL_SKIP_FILES = {"raw.md"}


def _base(root: Optional[Path]) -> Path:
    return Path(root) if root else ROOT


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 归档可能被人手改坏；队列不该因此 500
        return default


def _card_title(card_md: str) -> str:
    for line in card_md.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _targets(base: Path, tdir: Path, manifest: Dict[str, object]) -> List[Dict[str, object]]:
    """P2 到底写了哪些文件（按 manifest 的 into/new 推），附**期望内容 sha256**。"""
    out: List[Dict[str, object]] = []
    card = tdir / "card.md"
    card_sha = _sha(card) if card.is_file() else ""
    into = str(manifest.get("into") or "")
    new = str(manifest.get("new") or "")
    skills_root = (base / SKILLS_REPO).resolve()
    if into:
        out.append({"path": base / SKILLS_REPO / into / "CARD.md", "expected_sha256": card_sha,
                    "role": "card", "skill_id": into})
    elif new:
        skill_md = tdir / "skill.md"
        out.append({"path": base / SKILLS_REPO / new / "skill.md",
                    "expected_sha256": _sha(skill_md) if skill_md.is_file() else "",
                    "role": "skill", "skill_id": new})
        out.append({"path": base / SKILLS_REPO / new / "CARD.md", "expected_sha256": card_sha,
                    "role": "card", "skill_id": new})
    for item in out:
        target = Path(item["path"])
        try:
            target.resolve().relative_to(skills_root)
            item["inside_skills_repo"] = True
        except Exception:  # noqa: BLE001
            item["inside_skills_repo"] = False
        item["rel"] = str(target.relative_to(base)).replace("\\", "/")
    return out


def _inspect_targets(base: Path, tdir: Path, manifest: Dict[str, object]) -> List[Dict[str, object]]:
    """给每个目标文件打状态：pending(存在且未改) / missing(已不在) / modified(人改过) / outside(越界)。"""
    items = _targets(base, tdir, manifest)
    for item in items:
        target = Path(item["path"])
        if not item["inside_skills_repo"]:
            item["state"] = "outside"
            continue
        if not target.is_file():
            item["state"] = "missing"
            continue
        if item["expected_sha256"] and _sha(target) == item["expected_sha256"]:
            item["state"] = "pending"
        else:
            item["state"] = "modified"
    return items


def _summarize(tdir: Path, manifest: Dict[str, object], items: List[Dict[str, object]]) -> Dict[str, object]:
    review = _read_json(tdir / "review.json", {}) or {}
    compare = _read_json(tdir / "compare.json", {}) or {}
    findings = review.get("findings") or []
    counts = {"block": 0, "warn": 0, "info": 0}
    for f in findings:
        sev = str(f.get("severity") or "")
        if sev in counts:
            counts[sev] += 1
    card_md = ""
    card = tdir / "card.md"
    if card.is_file():
        card_md = card.read_text(encoding="utf-8", errors="replace")
    raw = tdir / "raw.md"
    raw_chars = len(raw.read_text(encoding="utf-8", errors="replace")) if raw.is_file() else 0
    pending = [i for i in items if i["state"] == "pending"]
    bad = [i for i in items if i["state"] in ("modified", "outside")]
    return {
        "transform_id": tdir.name,
        "created_at": _iso(tdir.stat().st_mtime),
        "artifact": manifest.get("artifact") or "",
        "chars": raw_chars,
        "verdict": manifest.get("verdict") or compare.get("verdict") or "",
        "review_verdict": manifest.get("review") or review.get("verdict") or "",
        "review_counts": counts,
        "identical_to": (compare.get("best") or {}).get("skill_id") or "",
        "identical_score": (compare.get("best") or {}).get("score"),
        "absorbed_into": manifest.get("absorbed_into") or "",
        "into": manifest.get("into") or "",
        "new": manifest.get("new") or "",
        "next": manifest.get("next") or [],
        "card_title": _card_title(card_md),
        "card_chars": len(card_md),
        "files": sorted(p.name for p in tdir.glob("*") if p.is_file()),
        "targets": [{k: i[k] for k in ("rel", "role", "skill_id", "state")} for i in items],
        "revertible": bool(pending) and not bad,
        "revert_reason": (
            "" if (pending and not bad) else
            ("没有可回滚的文件（P2 只落了归档）" if not pending and not bad else
             "目标文件已被改动或越界：请人工确认，系统不代删")
        ),
        "rolled_back_at": manifest.get("rolled_back_at") or "",
    }


def list_transforms(root: Optional[Path] = None, limit: int = 200) -> Dict[str, object]:
    """队列清单：按落地时间倒序。坏目录跳过但计数（不静默隐藏）。"""
    base = _base(root)
    troot = base / TRANSFORMS
    items: List[Dict[str, object]] = []
    broken = 0
    if troot.is_dir():
        for tdir in sorted(troot.iterdir()):
            if not tdir.is_dir():
                continue
            manifest = _read_json(tdir / "manifest.json", None)
            if not isinstance(manifest, dict):
                broken += 1
                continue
            items.append(_summarize(tdir, manifest, _inspect_targets(base, tdir, manifest)))
    items.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return {
        "count": len(items),
        "broken": broken,
        "items": items[: max(0, int(limit))],
        "root": str(troot),
    }


def get_transform(transform_id: str, root: Optional[Path] = None) -> Dict[str, object]:
    """单条详情：判定/审核（带行号证据）/卡片正文/将回滚的文件。"""
    base = _base(root)
    if not _TID_RE.match(transform_id or ""):
        return {"ok": False, "error": "非法 transform_id（应为 sha256 前 12 位十六进制）"}
    tdir = base / TRANSFORMS / transform_id
    if not tdir.is_dir():
        return {"ok": False, "error": "没有这条转化记录"}
    manifest = _read_json(tdir / "manifest.json", None)
    if not isinstance(manifest, dict):
        return {"ok": False, "error": "manifest.json 缺失或损坏"}
    items = _inspect_targets(base, tdir, manifest)
    summary = _summarize(tdir, manifest, items)
    review = _read_json(tdir / "review.json", {}) or {}
    compare = _read_json(tdir / "compare.json", {}) or {}
    texts = {}
    for name in ("card.md", "skill.md"):
        p = tdir / name
        if p.is_file() and name not in _DETAIL_SKIP_FILES:
            texts[name] = p.read_text(encoding="utf-8", errors="replace")
    return {
        "ok": True,
        "item": summary,
        # path 是 Path 对象（Flask 不能序列化）—— 对外只给相对路径与状态
        "targets": [{k: i[k] for k in ("rel", "role", "skill_id", "state")} for i in items],
        "findings": review.get("findings") or [],
        "compare": {k: compare.get(k) for k in ("verdict", "best", "facets", "note")},
        "texts": texts,
    }


def rollback_transform(transform_id: str, *, execute: bool = False,
                       root: Optional[Path] = None) -> Dict[str, object]:
    """按 transform_id 反向补丁：默认**只预览**（execute=False）。

    全有或全无：只要有一个目标文件被改过/越界，就整体拒绝（不半删）。
    """
    base = _base(root)
    if not _TID_RE.match(transform_id or ""):
        return {"ok": False, "error": "非法 transform_id（应为 sha256 前 12 位十六进制）"}
    tdir = base / TRANSFORMS / transform_id
    if not tdir.is_dir():
        return {"ok": False, "error": "没有这条转化记录"}
    manifest = _read_json(tdir / "manifest.json", {})
    if manifest.get("rolled_back_at"):
        return {"ok": True, "already_rolled_back": True,
                "rolled_back_at": manifest.get("rolled_back_at"),
                "deleted": manifest.get("rolled_back_files") or [],
                "transform_id": transform_id}

    items = _inspect_targets(base, tdir, manifest)
    pending = [i for i in items if i["state"] == "pending"]
    bad = [i for i in items if i["state"] in ("modified", "outside")]
    to_delete = [i["rel"] for i in pending]
    if bad:
        return {"ok": False, "stage": "conflict", "transform_id": transform_id,
                "error": "目标文件已被改动或越界，拒绝回滚（不半删）",
                "blocked": [{"rel": i["rel"], "state": i["state"]} for i in bad],
                "would_delete": to_delete}
    if not pending:
        return {"ok": True, "transform_id": transform_id, "dry_run": not execute,
                "deleted": [], "would_delete": [],
                "note": "没有可回滚的文件（P2 只落了归档，或已经删过）"}

    hints: List[str] = []
    for skill_id in sorted({str(i["skill_id"]) for i in pending}):
        hints.append("若 %s 已登记进主轨（DB），还要去「技能中心」停用/删除该技能；本回滚只动文件轨" % skill_id)

    if not execute:
        return {"ok": True, "dry_run": True, "transform_id": transform_id,
                "would_delete": to_delete, "hints": hints}

    deleted: List[str] = []
    for item in pending:
        target = Path(item["path"])
        try:
            target.unlink()
        except FileNotFoundError:
            continue
        deleted.append(item["rel"])
        parent = target.parent
        try:  # --new 建出来的目录：删空后一并收掉（非空就留着）
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
                deleted.append(str(parent.relative_to(base)).replace("\\", "/") + "/")
        except Exception:  # noqa: BLE001
            pass

    manifest["rolled_back_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    manifest["rolled_back_files"] = deleted
    (tdir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True, "dry_run": False, "transform_id": transform_id,
            "deleted": deleted, "hints": hints}
