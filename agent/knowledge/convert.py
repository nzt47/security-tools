# -*- coding: utf-8 -*-
r"""外部会话 -> 知识卡片 转换器（文档库导入前置）。

【为什么需要这一层】文档库（agent/knowledge）的卡片**强制要求 YAML frontmatter**
（card.py:_md_to_card 无 frontmatter 直接抛 ValueError），且 slug 必须等于
slugify(title)。而两类外部来源都不符合：

    DSH  会话   ~\.dsh\sessions\<ws>\<uuid>\session.jsonl.zstd   —— 多帧 zstd，无任何 importer
    Trae 文档   ~\.trae-cn\<数字>\<hash>\documents\*.md          —— 纯 markdown，**无 frontmatter**

故本模块做"转换 + 落暂存目录"，**不直接写文档库**；由人工过目后再跑
    python -m agent.knowledge import <暂存目录>
（与仓库"draft + 人工确认"的纪律一致）。

用法：
    # DSH 会话 -> 卡片
    python -m agent.knowledge.convert --kind dsh --src "%USERPROFILE%\.dsh\sessions" --out <暂存目录>
    # Trae 文档 -> 卡片
    python -m agent.knowledge.convert --kind trae --src "%USERPROFILE%\.trae-cn\1127420477105817" --out <暂存目录>

    # 落库（确认无误后）
    python -m agent.knowledge import <暂存目录>
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

#: 卡片类型白名单（schema.VALID_TYPES）；外部会话一律归 insights
CARD_TYPE = "insights"
#: 卡片状态：一律 draft —— 外部来源未经人工确认，不得直接 current
CARD_STATUS = "draft"
#: 正文中最多渲染多少条 diff / 踩坑（卡片宜精不宜长）
MAX_DIFFS = 5
MAX_PITFALLS = 8
#: 单卡正文上限（字符）
MAX_BODY_CHARS = 12000


def _slugify(title: str) -> str:
    from agent.knowledge.schema import slugify
    return slugify(title)


#: 去重后缀的单调计数器（见 _unique_slug 的两条硬约束）
_SLUG_SEQ = itertools.count(1)


def _unique_slug(title: str, used: set) -> str:
    """slug 必须 == slugify(title)（card.py 的一致性校验），故冲突时改**标题**。

    去重后缀不得用数字：slugify 会循环剥除尾部 '-数字'（保证幂等），
    用 '-2' 会被剥掉导致仍然冲突。改用 4 位十六进制。
    """
    base_title = title.strip() or "未命名"
    slug = _slugify(base_title)
    if slug and slug not in used:
        used.add(slug)
        return base_title
    # 【两条硬约束，均踩过坑】
    #  1) 后缀不得是纯数字：slugify 会循环剥除尾部 '-数字'（保幂等）⇒ 后缀若全是数字，
    #     slug 会被剥回与首次相同、去重静默失效（4 位十六进制有 (10/16)^4≈15% 全数字）。
    #     故加字母前缀 'e'，使尾部数字正则永不匹配。
    #  2) **不得以时间为去重熵源**：Windows 上 time.time_ns() 分辨率约 15ms，循环内连续
    #     取值会拿到同一时间戳 ⇒ 候选恒定 ⇒ 200 次全撞车后抛 RuntimeError。实测已复现。
    #     改用单调计数器：既保证唯一，又对同一输入序列**确定可复现**（文件按名排序处理）。
    for _ in range(100000):
        cand = "%s-e%x" % (base_title, next(_SLUG_SEQ))
        s = _slugify(cand)
        if s and s not in used:
            used.add(s)
            return cand
    raise RuntimeError("无法生成唯一 slug")


def _card_text(title: str, source: str, date: str, body: str,
               insight: str = "", scope: str = "",
               extra: Optional[Dict[str, Any]] = None) -> str:
    """渲染 frontmatter + 正文。字段顺序对齐 schema 定义。

    【insight 必填】schema.validate_card 第 6 条：「缺少一句话核心洞见」即不通过。
    但**不得凭空编造** —— 方案对 decision/root_cause 的零 token 处理规定：
    不做语义摘要，用元数据模板占位，由人工补一句自然语言。此处沿用同一口径。
    """
    import yaml
    fm: Dict[str, Any] = {
        "title": title,
        "slug": _slugify(title),
        "status": CARD_STATUS,
        "type": CARD_TYPE,
        "source": source,
        "date": date,
        "insight": insight or "【待人工补充】",
        "scope": scope or "【待人工补充】",
    }
    if extra:
        fm["metadata"] = extra
    head = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False,
                          default_flow_style=None).strip()
    return "---\n%s\n---\n\n%s\n" % (head, body.rstrip()[:MAX_BODY_CHARS])


# ════════════════════════════════════════════════════════════
#  DSH 会话 -> 卡片
# ════════════════════════════════════════════════════════════

def _sample_to_body(s: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("## 任务")
    lines.append(s.get("task", "") or "（无）")
    lines.append("")
    st = s.get("stack") or {}
    lines.append("## 概况")
    lines.append("- 类型：%s" % (s.get("task_type") or "?"))
    lines.append("- 技术栈：%s" % (st.get("lang") or "?"))
    lines.append("- 改动文件：%s 个" % (st.get("files_changed") or 0))
    lines.append("- 验证：%s" % (s.get("verified") or "?"))
    src = s.get("source") or {}
    lines.append("- 来源会话：%s（turn %s，seq %s~%s）"
                 % (src.get("session"), src.get("turn"),
                    src.get("seq_from"), src.get("seq_to")))
    lines.append("")

    diffs = (s.get("diffs") or [])[:MAX_DIFFS]
    if diffs:
        lines.append("## 改动文件")
        for d in diffs:
            lines.append("- `%s`（%s，%s 字节%s）"
                         % (d.get("path"), d.get("op"), d.get("bytes"),
                            "，已截断" if d.get("truncated") else ""))
        lines.append("")
        lines.append("## 关键改动")
        for d in diffs[:2]:
            lines.append("")
            lines.append("```diff")
            lines.append((d.get("diff") or "").strip())
            lines.append("```")
        lines.append("")

    pits = (s.get("pitfalls") or [])[:MAX_PITFALLS]
    if pits:
        lines.append("## 踩坑记录")
        for p in pits:
            lines.append("- %s（来源：%s）" % (p.get("symptom"), p.get("verified_by")))
        lines.append("")
    return "\n".join(lines)


def convert_dsh(src: str, out: str, *, snapshot: Optional[str] = None,
                limit: Optional[int] = None, verbose: bool = False) -> Dict[str, Any]:
    """复用经验流水线的 extract()（已含配对/六条规则/脱敏），再映射为卡片。"""
    from agent.experience_cli.extract import extract

    samples, rejected, stat, _reg = extract(src, snapshot, verbose)
    if limit:
        samples = samples[:limit]
    used: set = set()
    written = blocked = 0
    outdir = Path(out)
    outdir.mkdir(parents=True, exist_ok=True)

    for s in samples:
        body = _sample_to_body(s)
        # 二次脱敏闸门：extract 已做过，此处对最终成稿再扫一遍（防止拼接引入）
        from agent.experience_cli._common import scan_hard_block
        hit = scan_hard_block(body)
        if hit:
            blocked += 1
            continue
        title = (s.get("task") or "").strip().replace("\n", " ")[:60] or ("会话 %s" % s.get("id"))
        title = _unique_slug(title, used)
        date = (s.get("created_at") or "")[:10] or time.strftime("%Y-%m-%d")
        src_decl = "dsh:%s#turn%s" % ((s.get("source") or {}).get("session"),
                                      (s.get("source") or {}).get("turn"))
        # insight/scope 用元数据模板占位（零 token 原则，不编造语义摘要）
        st = s.get("stack") or {}
        sid = (s.get("source") or {}).get("session")
        insight = "【待人工补充】%s（%s/%s，改动 %s 文件，验证 %s）" % (
            (s.get("task") or "").strip().replace(chr(10), " ")[:60],
            s.get("task_type"), st.get("lang"),
            st.get("files_changed"), s.get("verified"))
        scope = "适用于 %s 项目的 %s 场景；来源会话 %s" % (
            st.get("lang") or "?", s.get("task_type") or "?", sid)
        text = _card_text(title, src_decl, date, body,
                          insight=insight, scope=scope,
                          extra={"task_type": s.get("task_type"),
                                 "lang": st.get("lang"),
                                 "verified": s.get("verified")})
        (outdir / (_slugify(title) + ".md")).write_text(text, encoding="utf-8")
        written += 1
    return {"written": written, "blocked": blocked, "rejected_by_extract": len(rejected),
            "extract_stat": dict(stat)}


# ════════════════════════════════════════════════════════════
#  Trae 文档 -> 卡片
# ════════════════════════════════════════════════════════════

_H1 = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)

#: 代码围栏三连反引号 —— 用 chr(96) 构造，避免源码里出现裸反引号
_FENCE = chr(96) * 3


def _first_paragraph(text: str, limit: int = 200) -> str:
    """取首个正文段落（跳过标题/代码块/表格/列表），作为一句话洞见的候选。"""
    in_code = False
    for ln in text.split(chr(10)):
        s = ln.strip()
        if s.startswith(_FENCE):
            in_code = not in_code
            continue
        if in_code or not s or s[0] in "#|-":
            continue
        return s[:limit]
    return ""


def _trae_files(src: str) -> List[Path]:
    """Trae 的 markdown 文档：<src>/**/documents/*.md（跳过依赖/内置目录）。"""
    root = Path(src)
    skip = ("node_modules", "binaries", "builtin", "extensions", "design_libraries")
    out: List[Path] = []
    for p in root.rglob("*.md"):
        parts = set(p.parts)
        if parts & set(skip):
            continue
        if "documents" in p.parts or "refactor" in p.parts:
            out.append(p)
    return sorted(out)


def convert_trae(src: str, out: str) -> Dict[str, Any]:
    from agent.experience_cli._common import scan_hard_block

    files = _trae_files(src)
    used: set = set()
    written = blocked = skipped = 0
    outdir = Path(out)
    outdir.mkdir(parents=True, exist_ok=True)

    for p in files:
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            skipped += 1
            continue
        if not raw.strip():
            skipped += 1
            continue
        hit = scan_hard_block(raw)
        if hit:
            blocked += 1
            continue
        m = _H1.search(raw)
        # Trae 文档首行常是 "# 1. 问题" 这类分节标题，故优先用文件名做标题
        title = p.stem.strip() or (m.group(1).strip() if m else "未命名")
        title = _unique_slug(title, used)
        try:
            date = time.strftime("%Y-%m-%d", time.localtime(p.stat().st_mtime))
        except Exception:
            date = time.strftime("%Y-%m-%d")
        rel = os.path.relpath(str(p), src).replace("\\", "/")
        # Trae 文档本身已是"重构洞察"，其首个正文段落即一句话洞见 —— 直接抽取，不编造
        insight = _first_paragraph(raw) or "【待人工补充】"
        text = _card_text(title, "trae:%s" % rel, date, raw,
                          insight=insight,
                          scope="来源 Trae 文档：%s" % rel)
        (outdir / (_slugify(title) + ".md")).write_text(text, encoding="utf-8")
        written += 1
    return {"written": written, "blocked": blocked, "skipped": skipped,
            "scanned": len(files)}


def main(argv: Optional[List[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(
        prog="python -m agent.knowledge.convert",
        description="外部会话（DSH / Trae）-> 知识卡片（暂存），供 agent.knowledge import 使用")
    ap.add_argument("--kind", required=True, choices=["dsh", "trae"])
    ap.add_argument("--src", required=True, help="源目录")
    ap.add_argument("--out", required=True, help="卡片暂存目录（不直接写文档库）")
    ap.add_argument("--limit", type=int, help="dsh：最多转换多少条（调试）")
    ap.add_argument("--snapshot-until", dest="snapshot_until", help="dsh：冻结到该 ISO 时间")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv)

    if not os.path.isdir(a.src):
        print("[FATAL] 源目录不存在: %s" % a.src, file=sys.stderr)
        return 2
    t0 = time.time()
    if a.kind == "dsh":
        r = convert_dsh(a.src, a.out, snapshot=a.snapshot_until,
                        limit=a.limit, verbose=a.verbose)
    else:
        r = convert_trae(a.src, a.out)
    print("=" * 62)
    print("转换完成：%s -> %s（%.1fs）" % (a.kind, a.out, time.time() - t0))
    for k, v in r.items():
        print("  %-22s %s" % (k, v))
    print()
    print("下一步（确认无误后落库）：")
    print("  python -m agent.knowledge import %s" % a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
