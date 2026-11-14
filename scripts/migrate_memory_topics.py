#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把「向量记忆 / 知识库 / 经验」三类存量数据打上主题类（topic）——迁移脚本。

口径（与 docs/handoff/口径.md 一致，写死在代码里）：
1. **默认 dry-run**，只读、只出报告；要真写必须显式 `--apply`。
2. **只增不删**：本脚本只写 `topic` 一个键，绝不删除/改写任何正文或其它 metadata。
3. **可回滚**：每次 apply 写一份 append-only 台账
   （`data/maintenance/topics/ledger_<ts>.jsonl`，每行 {store,id,before,after}），
   `--rollback --ledger <file>` 按台账精确还原；**只还原"当前仍等于 after"的行**
   （否则说明后来被人改过，跳过并计数，绝不覆盖别人的写入）。
4. **不猜**：推断不出的进 `general`。`--infer` 才会用启发式（且只在报告里
   标注 `suggested`，实际写入仍是 general，除非同时给 `--apply --infer`）。
5. 幂等：已有 topic 的记录默认跳过（`--force` 才重写）。

用法：
    python scripts/migrate_memory_topics.py                    # dry-run，全库
    python scripts/migrate_memory_topics.py --apply            # 真写，旧数据 → general
    python scripts/migrate_memory_topics.py --apply --infer    # 经验侧按语言推断 engineering
    python scripts/migrate_memory_topics.py --rollback --ledger data/maintenance/topics/ledger_xxx.jsonl
    python scripts/migrate_memory_topics.py --apply --include-json-fallback   # 连旧 JSON 回退文件一起迁
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent.topics import (  # noqa: E402  (需先补 sys.path)
    DEFAULT_TOPIC,
    TOPICS,
    normalize_topic,
)

#: 向量库（sqlite-vec 后端）元数据表；与 memory/vector_store/sqlite_vec_backend.py 同名
VEC_DB_REL = os.path.join("data", "memory", "agent_memory_vec.db")
VEC_COLLECTION = "agent_memory"
#: JSON 回退文件（legacy；**已被 git 跟踪**，默认不迁，需显式开关）
JSON_FALLBACK_REL = os.path.join("data", "memory", "agent_memory.json")
#: 知识卡片库根（与 CardStore 默认一致）
WIKI_ROOT_REL = "knowledge/wiki"
#: 经验样本
EXPERIENCE_REL = os.path.join("data", "experience", "samples.ndjson")
#: 报告与台账目录
OUT_DIR_REL = os.path.join("data", "maintenance", "topics")

#: 视为"代码类经验"的语言（--infer 时据此建议 engineering；仅是建议，不是事实）
CODE_LANGS = ("python", "typescript", "javascript", "java", "go", "rust", "yaml", "json", "sql")

#: 单次 jsonl 读写块大小（避免一次性把 12MB 样本读进内存）
JSONL_CHUNK = 500


def _rel(path: str) -> str:
    return path.replace(os.sep, "/")


def _stamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


# ════════════════════════════════════════════════════════════
#  台账
# ════════════════════════════════════════════════════════════


class Ledger:
    """append-only 迁移台账（每行一条记录的前后值）"""

    def __init__(self, path: str, *, write: bool):
        self.path = path
        self.write = write
        self.rows: List[Dict[str, Any]] = []
        if write:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "a", encoding="utf-8").close()

    def record(self, store: str, rid: str, before: Any, after: Any) -> None:
        row = {"store": store, "id": rid, "before": before, "after": after,
               "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        self.rows.append(row)
        if self.write:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    @staticmethod
    def load(path: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                out.append(json.loads(line))
        return out


# ════════════════════════════════════════════════════════════
#  向量库
# ════════════════════════════════════════════════════════════


def _vec_meta_table(conn: sqlite3.Connection) -> Optional[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'meta_%'").fetchall()
    if not rows:
        return None
    return rows[0][0]


def migrate_vector(db_path: str, *, apply: bool, force: bool,
                   ledger: Optional[Ledger], infer: bool) -> Dict[str, Any]:
    """给 meta_<collection>.metadata 补 topic。

    只碰 metadata 这一个 JSON 键；正文/时间戳/向量一律不动。
    """
    stat = {"store": "vector", "db": _rel(db_path), "total": 0,
            "already": 0, "changed": 0, "skipped": 0, "by_topic": {}}
    if not os.path.exists(db_path):
        stat["note"] = "db 不存在（后端可能不是 sqlite-vec），跳过"
        return stat
    conn = sqlite3.connect(db_path)
    try:
        table = _vec_meta_table(conn)
        if table is None:
            stat["note"] = "找不到 meta_* 表，跳过"
            return stat
        rows = conn.execute(f"SELECT id, metadata FROM {table}").fetchall()
        stat["total"] = len(rows)
        updates: List[Tuple[str, str]] = []
        for rid, raw in rows:
            try:
                meta = json.loads(raw) if raw else {}
            except Exception:
                meta = {}
            if not isinstance(meta, dict):
                meta = {}
            before = meta.get("topic")
            if before and not force:
                stat["already"] += 1
                stat["by_topic"][str(before)] = stat["by_topic"].get(str(before), 0) + 1
                continue
            after = DEFAULT_TOPIC
            if infer:
                # 忠实于"不猜"：向量条目的 metadata 只有 source/type/batch_id 等
                # 形态字段，推不出业务域 ⇒ 即便 --infer 也只建议 general（留空即 general）。
                after = DEFAULT_TOPIC
            if before is not None and str(before) == after:
                stat["skipped"] += 1
                continue
            meta["topic"] = after
            payload = json.dumps(meta, ensure_ascii=False)
            updates.append((payload, rid))
            stat["changed"] += 1
            stat["by_topic"][after] = stat["by_topic"].get(after, 0) + 1
            if ledger is not None:
                ledger.record("vector", rid, before, after)
            if len(updates) >= JSONL_CHUNK and apply:
                conn.executemany(
                    f"UPDATE {table} SET metadata = ? WHERE id = ?", updates)
                updates = []
        if apply and updates:
            conn.executemany(f"UPDATE {table} SET metadata = ? WHERE id = ?", updates)
            conn.commit()
        if not apply:
            conn.rollback()
        return stat
    finally:
        conn.close()


def migrate_json_fallback(path: str, *, apply: bool, force: bool,
                          ledger: Optional[Ledger]) -> Dict[str, Any]:
    """legacy JSON 回退文件（tracked，默认不跑）。"""
    stat = {"store": "vector_json", "file": _rel(path), "total": 0,
            "already": 0, "changed": 0, "skipped": 0}
    if not os.path.exists(path):
        stat["note"] = "文件不存在"
        return stat
    with open(path, "r", encoding="utf-8") as f:
        items = json.load(f)
    stat["total"] = len(items)
    for item in items:
        meta = item.get("metadata") or {}
        if not isinstance(meta, dict):
            continue
        if meta.get("topic") and not force:
            stat["already"] += 1
            continue
        before = meta.get("topic")
        meta["topic"] = DEFAULT_TOPIC
        item["metadata"] = meta
        stat["changed"] += 1
        if ledger is not None:
            ledger.record("vector_json", str(item.get("id") or ""), before, DEFAULT_TOPIC)
    if apply and stat["changed"]:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    return stat


# ════════════════════════════════════════════════════════════
#  知识卡片
# ════════════════════════════════════════════════════════════


_TOPIC_LINE_RE = re.compile(r"^topic:\s*\S", re.MULTILINE)


def _slugs_declaring_topic(wiki_root: str) -> set:
    """扫 wiki 目录，返回**frontmatter 里真的写了 topic:** 的 slug 集合。"""
    out = set()
    for dirpath, _dirnames, filenames in os.walk(wiki_root):
        for name in filenames:
            if not name.endswith(".md"):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    head = f.read(2048)
            except Exception:
                continue
            if not head.startswith("---"):
                continue
            end = head.find("\n---", 3)
            body = head[3:end] if end > 0 else head
            if _TOPIC_LINE_RE.search(body):
                out.add(name[:-3])
    return out


def migrate_knowledge(wiki_root: str, *, apply: bool, force: bool,
                      ledger: Optional[Ledger]) -> Dict[str, Any]:
    """给卡片 frontmatter 补 topic（用 CardStore 走正规写路径：校验 + 原子写 + 索引同步）。

    不用手写 YAML：`_md_to_card` 只看 dataclass 字段，手写键会在下次 update 时被抹掉。
    """
    stat = {"store": "knowledge", "root": _rel(wiki_root), "total": 0,
            "already": 0, "changed": 0, "skipped": 0, "failed": 0,
            "by_topic": {}}
    if not os.path.isdir(wiki_root):
        stat["note"] = "wiki 根不存在，跳过"
        return stat
    from agent.knowledge.card import CardStore

    store = CardStore(wiki_root)
    cards = store.list()
    stat["total"] = len(cards)
    declared = _slugs_declaring_topic(wiki_root)
    for card in cards:
        before = getattr(card, "topic", None)
        # 【为什么还要看文件】Card.topic 有默认值 general ⇒ 缺键与"显式写了 general"
        # 在对象上一模一样。迁移必须区分这两者，否则"已声明"与"没写"混在一起，
        # 报告会谎报已迁移（旧卡片会被当成迁完了，其实一个字没写）。
        if card.slug in declared and before and not force:
            stat["already"] += 1
            stat["by_topic"][str(before)] = stat["by_topic"].get(str(before), 0) + 1
            continue
        card.topic = DEFAULT_TOPIC
        stat["changed"] += 1
        stat["by_topic"][DEFAULT_TOPIC] = stat["by_topic"].get(DEFAULT_TOPIC, 0) + 1
        if ledger is not None:
            ledger.record("knowledge", card.slug, before, DEFAULT_TOPIC)
        if apply:
            try:
                store.update(card)
            except Exception as exc:  # noqa: BLE001 单卡失败不阻断整批
                stat["failed"] += 1
                stat.setdefault("failures", []).append(
                    {"slug": card.slug, "error": type(exc).__name__})
    return stat


# ════════════════════════════════════════════════════════════
#  经验
# ════════════════════════════════════════════════════════════


def _suggest_experience_topic(sample: Dict[str, Any]) -> str:
    """启发式建议（仅报告/--infer 用）：代码类经验 ⇒ engineering，其余 general。

    【为什么敢建议 engineering】经验样本全部是"改代码 + 踩坑 + 验证"的交付记录
    （samples.ndjson 的 diffs/stack/pitfalls 三件套），其业务域就是工程本身。
    但仍是**建议**：默认不写，要写必须给 --infer。
    """
    lang = str((sample.get("stack") or {}).get("lang") or "").strip().lower()
    if lang in CODE_LANGS:
        return "engineering"
    return DEFAULT_TOPIC


def migrate_experience(path: str, *, apply: bool, force: bool, infer: bool,
                       ledger: Optional[Ledger]) -> Dict[str, Any]:
    """给 samples.ndjson 的每条样本补 topic（文件是 gitignored 运行期产物）。

    实现：逐行读 → 逐行改 → 临时文件 + os.replace（原子替换，中途失败原文件不坏）。
    """
    stat = {"store": "experience", "file": _rel(path), "total": 0,
            "already": 0, "changed": 0, "skipped": 0, "suggest": {},
            "by_topic": {}}
    if not os.path.exists(path):
        stat["note"] = "样本文件不存在，跳过"
        return stat
    out_lines: List[str] = []
    bad = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            raw = line.rstrip("\n")
            if not raw.strip():
                out_lines.append(raw)
                continue
            try:
                sample = json.loads(raw)
            except Exception:
                bad += 1
                out_lines.append(raw)
                continue
            stat["total"] += 1
            before = sample.get("topic")
            suggested = _suggest_experience_topic(sample)
            stat["suggest"][suggested] = stat["suggest"].get(suggested, 0) + 1
            if before and not force:
                stat["already"] += 1
                stat["by_topic"][str(before)] = stat["by_topic"].get(str(before), 0) + 1
                out_lines.append(raw)
                continue
            after = suggested if infer else DEFAULT_TOPIC
            if before is not None and str(before) == after:
                stat["skipped"] += 1
                out_lines.append(raw)
                continue
            sample["topic"] = after
            stat["changed"] += 1
            stat["by_topic"][after] = stat["by_topic"].get(after, 0) + 1
            if ledger is not None:
                ledger.record("experience", str(sample.get("id") or ""), before, after)
            out_lines.append(json.dumps(sample, ensure_ascii=False))
    if bad:
        stat["bad_lines"] = bad
    if apply and stat["changed"]:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(out_lines) + "\n")
        os.replace(tmp, path)
    return stat


# ════════════════════════════════════════════════════════════
#  回滚
# ════════════════════════════════════════════════════════════


def rollback(ledger_path: str, *, apply: bool) -> Dict[str, Any]:
    """按台账把 topic 还原成 before。

    只还原"当前仍等于 after"的行：否则说明该记录在迁移后被别的流程改过，
    覆盖它等于用旧值抹掉新值（宁可跳过并报数）。
    """
    stat = {"rows": 0, "restored": 0, "skipped_mismatch": 0, "failed": 0, "by_store": {}}
    rows = Ledger.load(ledger_path)
    stat["rows"] = len(rows)
    by_store: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        by_store.setdefault(row.get("store") or "", []).append(row)
        stat["by_store"][row.get("store") or ""] = stat["by_store"].get(row.get("store") or "", 0) + 1

    # ── 向量库（一次性连接，按 last-write-wins 逐行还原）──
    vec_rows = by_store.get("vector") or []
    db_path = os.path.join(ROOT, VEC_DB_REL)
    if vec_rows and os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        try:
            table = _vec_meta_table(conn)
            if table:
                for row in vec_rows:
                    rid = row.get("id")
                    got = conn.execute(
                        f"SELECT metadata FROM {table} WHERE id = ?", (rid,)).fetchone()
                    if not got:
                        stat["skipped_mismatch"] += 1
                        continue
                    try:
                        meta = json.loads(got[0]) if got[0] else {}
                    except Exception:
                        meta = {}
                    if not isinstance(meta, dict) or str(meta.get("topic")) != str(row.get("after")):
                        stat["skipped_mismatch"] += 1
                        continue
                    before = row.get("before")
                    if before in (None, ""):
                        meta.pop("topic", None)
                    else:
                        meta["topic"] = normalize_topic(before, strict=False)
                    if apply:
                        conn.execute(f"UPDATE {table} SET metadata = ? WHERE id = ?",
                                     (json.dumps(meta, ensure_ascii=False), rid))
                    stat["restored"] += 1
                if apply:
                    conn.commit()
                else:
                    conn.rollback()
        finally:
            conn.close()

    # ── 知识卡片 ──
    kb_rows = by_store.get("knowledge") or []
    if kb_rows:
        from agent.knowledge.card import CardStore

        store = CardStore(os.path.join(ROOT, WIKI_ROOT_REL))
        for row in kb_rows:
            card = store.get(str(row.get("id") or ""))
            if card is None:
                stat["skipped_mismatch"] += 1
                continue
            if str(getattr(card, "topic", "")) != str(row.get("after")):
                stat["skipped_mismatch"] += 1
                continue
            card.topic = normalize_topic(row.get("before"), strict=False) if row.get("before") else DEFAULT_TOPIC
            if apply:
                try:
                    store.update(card)
                except Exception:  # noqa: BLE001
                    stat["failed"] += 1
                    continue
            stat["restored"] += 1

    # ── 经验 ──
    exp_rows = by_store.get("experience") or []
    if exp_rows:
        path = os.path.join(ROOT, EXPERIENCE_REL)
        want = {str(r.get("id")): r for r in exp_rows}
        if os.path.exists(path):
            out: List[str] = []
            restored_ids = set()
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    raw = line.rstrip("\n")
                    if not raw.strip():
                        out.append(raw)
                        continue
                    try:
                        sample = json.loads(raw)
                    except Exception:
                        out.append(raw)
                        continue
                    row = want.get(str(sample.get("id")))
                    if row is None or str(sample.get("topic")) != str(row.get("after")):
                        out.append(raw)
                        continue
                    before = row.get("before")
                    if before in (None, ""):
                        sample.pop("topic", None)
                    else:
                        sample["topic"] = normalize_topic(before, strict=False)
                    restored_ids.add(str(sample.get("id")))
                    stat["restored"] += 1
                    out.append(json.dumps(sample, ensure_ascii=False))
            stat["skipped_mismatch"] += max(0, len(exp_rows) - len(restored_ids))
            if apply:
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write("\n".join(out) + "\n")
                os.replace(tmp, path)
    return stat


# ════════════════════════════════════════════════════════════
#  CLI
# ════════════════════════════════════════════════════════════


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="三库主题类（topic）迁移：dry-run / apply / rollback")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="真写（默认 dry-run）")
    mode.add_argument("--rollback", action="store_true", help="按台账回滚")
    ap.add_argument("--ledger", default="", help="回滚用的台账文件路径")
    ap.add_argument("--infer", action="store_true",
                    help="经验侧按语言启发式建议 engineering（仅 --apply 时才写）")
    ap.add_argument("--force", action="store_true", help="已有 topic 也重写")
    ap.add_argument("--include-json-fallback", action="store_true",
                    help="连 legacy JSON 回退文件一起迁（该文件被 git 跟踪，默认不动）")
    ap.add_argument("--out-dir", default=OUT_DIR_REL, help="报告/台账目录")
    ap.add_argument("--json", action="store_true", help="只输出 JSON（脚本化用）")
    args = ap.parse_args(argv)

    if args.rollback:
        path = args.ledger or ""
        if not path:
            ap.error("--rollback 需要 --ledger <台账文件>")
        report = rollback(path, apply=args.apply is True)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if not report.get("failed") else 1

    out_dir = os.path.join(ROOT, args.out_dir)
    stamp = _stamp()
    ledger_path = os.path.join(out_dir, f"ledger_{stamp}.jsonl")
    ledger = Ledger(ledger_path, write=args.apply)

    report: Dict[str, Any] = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "mode": "apply" if args.apply else "dry-run",
        "infer": bool(args.infer),
        "force": bool(args.force),
        "vocabulary": list(TOPICS),
        "default_topic": DEFAULT_TOPIC,
        "stores": {},
    }
    report["stores"]["vector"] = migrate_vector(
        os.path.join(ROOT, VEC_DB_REL), apply=args.apply, force=args.force,
        ledger=ledger, infer=args.infer)
    if args.include_json_fallback:
        report["stores"]["vector_json"] = migrate_json_fallback(
            os.path.join(ROOT, JSON_FALLBACK_REL), apply=args.apply,
            force=args.force, ledger=ledger)
    report["stores"]["knowledge"] = migrate_knowledge(
        os.path.join(ROOT, WIKI_ROOT_REL), apply=args.apply, force=args.force,
        ledger=ledger)
    report["stores"]["experience"] = migrate_experience(
        os.path.join(ROOT, EXPERIENCE_REL), apply=args.apply, force=args.force,
        infer=args.infer, ledger=ledger)

    report["changed_total"] = sum(int(s.get("changed") or 0) for s in report["stores"].values())
    report["ledger"] = _rel(ledger_path) if args.apply else ""
    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    if args.apply:
        os.makedirs(out_dir, exist_ok=True)
        rp = os.path.join(out_dir, f"report_{stamp}.json")
        with open(rp, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        report["report_path"] = _rel(rp)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print(f"== 迁移报告（{report['mode']}）==")
    for name, st in report["stores"].items():
        print(f"[{name}] total={st.get('total')} already={st.get('already')} "
              f"changed={st.get('changed')} skipped={st.get('skipped')} "
              f"by_topic={st.get('by_topic')}"
              + (f" suggest={st.get('suggest')}" if st.get("suggest") else "")
              + (f" note={st.get('note')}" if st.get("note") else ""))
    print(f"合计改动 {report['changed_total']} 条"
          + (f"；台账 {report['ledger']}；报告 {report.get('report_path', '')}" if args.apply else "（dry-run：未写任何数据）"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
