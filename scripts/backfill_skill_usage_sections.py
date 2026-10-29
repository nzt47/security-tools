#!/usr/bin/env python
"""按「复读描述」补 skill.md 的「何时使用 / When to Use」小节（默认干跑）。

【为什么这么做】§11.19-3：文件轨 36 份里 29 份没有「何时使用」小节（schema 门禁的 warning）。
用户 2026-10-29 明确：**按「复读描述」方式批量补** —— 即以 front matter 的 description 为**唯一来源**
拆成 2–4 条触发条件，不新增任何描述里没有的能力；正文其它内容一字不动。

【口径】
- 只补**缺小节**的文件（已有 何时使用/使用场景/使用说明/When to Use/Usage 的跳过 ⇒ 脚本幂等）；
- 内容来自 description：去掉「Use when / 适用于 / 用于」引导词与「由 1 份素材蒸馏生成」尾注，
  按 ; ； 。 拆句；拆不出来再按 ，/, 拆（最多 4 条），每条 ≤160 字；
- 中文描述 → `## 何时使用`，英文 → `## When to Use`；
- 插入位置：H1 之后的第一段介绍之后（无 H1 则放在正文最前）—— 紧挨技能介绍，读者第一眼能看到；
- 不碰 front matter、不碰正文其它行（diff 应只有新增的几行）。

用法：
    python scripts/backfill_skill_usage_sections.py             # 干跑，列出要补的文件
    python scripts/backfill_skill_usage_sections.py --execute   # 真写
    python scripts/backfill_skill_usage_sections.py --json
退出码：0 成功（干跑也算）/ 1 有文件写失败 / 2 参数或路径错误。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.skills_mgmt.claude_import import _split_front_matter  # noqa: E402
from agent.skills_mgmt.skill_md_schema import USAGE_SECTIONS, _headings  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SKILLS_REPO = ROOT / "data" / "skills_repo"

_CJK = re.compile(r"[\u4e00-\u9fff]")
_TRAILERS = ("由 1 份素材蒸馏生成", "由1份素材蒸馏生成", "。由 1 份素材蒸馏生成")
_TRIGGERS = ("use this skill when", "use this before", "use when", "适用于", "用于")
_MAX_BULLETS = 4
_MAX_BULLET_CHARS = 220
_SENT_SPLIT = re.compile(r"(?<=[。！？])\s*|(?<=[;；])\s*|(?<=[.])\s+")


def _strip_noise(text: str) -> str:
    out = re.sub(r"\s+", " ", (text or "").strip().strip("'\""))
    for trailer in _TRAILERS:
        out = out.replace(trailer, "")
    return out.strip(" 。.;；")


def _clean_bullet(item: str, skill_id: str = "") -> str:
    """去尾注括号（如 `(gstack)`）、首字母大写（英文）、收尾标点归一。"""
    out = re.sub(r"\s+", " ", item).strip(" 。.;；,，")
    if skill_id:
        out = re.sub(r"\s*\(%s\)\s*$" % re.escape(skill_id), "", out).strip()
    out = re.sub(r"\s*\([a-z0-9_-]{2,40}\)\s*$", "", out).strip(" 。.;；,，")
    if out and out[0].isascii() and out[0].islower():
        out = out[0].upper() + out[1:]
    return out


def _trigger_part(body: str) -> str:
    """只留「什么时候用」那部分：出现 Use when / 适用于 / 用于 就从那里开始。"""
    lowered = body.lower()
    starts = [lowered.find(k) for k in _TRIGGERS if lowered.find(k) >= 0]
    if not starts:
        return body
    start = min(starts)
    return body[start:] if start > 0 else body


def _split_sentences(text: str) -> List[str]:
    parts = [p.strip(" 。.;；,，") for p in _SENT_SPLIT.split(text)]
    return [p for p in parts if p]


def derive_section(description: str, skill_id: str = "") -> Tuple[str, List[str]]:
    """从 description 派生 (标题, 条目列表)。

    口径（用户 2026-10-29「复读描述」）：只做**句子级**拆分，不按逗号切 ——
    逗号切会把 `Markdown / or custom format` 这类从句切碎成看不懂的碎片。
    英文去掉 `Use when / Use this skill when` 引导词，中文保留原句。
    """
    body = _strip_noise(description)
    if not body:
        return "", []
    chinese = bool(_CJK.search(body))
    text = _trigger_part(body)
    lowered = text.lower()
    for leader in _TRIGGERS:
        if lowered.startswith(leader):
            text = text[len(leader):].lstrip(" :：,，")
            break
    sentences = _split_sentences(text)
    # 过长的单句再按 " - " 拆一次（pd-* 常见 `条件 - 补充说明` 形态）
    expanded: List[str] = []
    for sent in sentences:
        pieces = ([p.strip() for p in sent.split(" - ") if len(p.strip()) >= 15]
                  if len(sent) > 120 else [sent])
        expanded.extend(pieces or [sent])
    bullets: List[str] = []
    for item in expanded:
        item = _clean_bullet(item, skill_id)
        if not item:
            continue
        if len(item) > _MAX_BULLET_CHARS:
            item = item[: _MAX_BULLET_CHARS - 1].rstrip() + "…"
        bullets.append(item)
        if len(bullets) >= _MAX_BULLETS:
            break
    heading = "## 何时使用" if chinese else "## When to Use"
    return (heading, bullets) if bullets else ("", [])


def insert_section(text: str, heading: str, bullets: List[str]) -> Optional[str]:
    """把小节插到 H1 之后的第一段介绍之后；已存在同类小节则返回 None（幂等）。"""
    meta, body, _warnings = _split_front_matter(text)
    if not isinstance(meta, dict):
        meta = {}
    if any(any(k in h for k in USAGE_SECTIONS) for h in _headings(body)):
        return None
    block = "\n".join([heading, "", *["- %s" % b for b in bullets]])
    lines = body.splitlines()
    # 找 H1
    h1 = next((i for i, ln in enumerate(lines) if ln.startswith("# ")), None)
    if h1 is None:
        new_body = block + "\n\n" + body.strip()
    else:
        # 从 H1 之后找第一段（连续非空行）的结束位置
        i = h1 + 1
        while i < len(lines) and not lines[i].strip():
            i += 1
        while i < len(lines) and lines[i].strip():
            i += 1
        head = "\n".join(lines[:i]).rstrip()
        tail = "\n".join(lines[i:]).lstrip("\n")
        new_body = head + "\n\n" + block + ("\n\n" + tail if tail else "")
    # 还原 front matter 原文（逐字保留分隔符与元数据）
    fm_match = re.match(r"^---\s*\n.*?\n---\s*\n", text, re.S)
    prefix = fm_match.group(0) if fm_match else ""
    return prefix + new_body.strip() + "\n"


def _skill_files(root: Path) -> List[Tuple[str, Path]]:
    out: List[Tuple[str, Path]] = []
    if not root.is_dir():
        return out
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        for name in ("SKILL.md", "skill.md"):
            md = child / name
            if md.is_file():
                out.append((child.name, md))
                break
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="按 description 补 skill.md「何时使用」小节")
    parser.add_argument("--root", default="", help="文件轨根（默认 data/skills_repo）")
    parser.add_argument("--execute", action="store_true", help="真正写入（缺省干跑）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    root = Path(args.root) if args.root else DEFAULT_SKILLS_REPO
    if not root.is_dir():
        print("目录不存在：%s" % root)
        return 2

    plan: List[Dict[str, object]] = []
    failed: List[str] = []
    for skill_id, path in _skill_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            failed.append("%s: %s" % (skill_id, type(exc).__name__))
            continue
        meta, _body, _wr = _split_front_matter(text)
        description = str((meta or {}).get("description") or "")
        heading, bullets = derive_section(description, skill_id)
        if not heading or not bullets:
            continue
        updated = insert_section(text, heading, bullets)
        if updated is None:
            continue
        plan.append({"skill_id": skill_id, "path": str(path), "heading": heading,
                     "bullets": bullets})
        if args.execute:
            try:
                path.write_text(updated, encoding="utf-8")
            except OSError as exc:
                failed.append("%s: %s" % (skill_id, type(exc).__name__))

    report = {"root": str(root), "mode": "execute" if args.execute else "dry-run",
              "planned": len(plan), "failed": failed, "items": plan}
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        print("[%s] %s：需补 %d 份" % (report["mode"], root, len(plan)))
        for item in plan:
            print("  - %s → %s（%d 条）" % (item["skill_id"], item["heading"],
                                          len(item["bullets"])))
        if failed:
            print("写失败：%s" % failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
