# -*- coding: utf-8 -*-
"""记忆整理（consolidation）的**只读**扫描：只出候选，绝不删除（2026-11-13）。

【为什么要这个模块（§11.103 F / handoff_next: memory-idle-consolidation）】
    线上"记忆整理"此前只有 `review_quick()`（agent/memory/reviewer.py:131）：它读的是
    **长期记忆库**（`data/memory/long_term.db`，实测 long_term_memory / user_profile 都是 0 行），
    所以每天跑一次也只是 0 陈旧 / 0 重复。真正的对话记忆在 `data/messages.jsonl`（421 条），
    从来没有被任何整理动作看过。

【口径（三条，改之前先读）】
    1. **只报告，不删、不改、不迁移**。删除必须走人工 confirm；无 `session_id` 的历史池按口径
       **冻结**（不迁移、不参与摘要），这里只统计条数。
    2. **不做"内容相同就合并"**：对话里「继续」出现 17 次是用户真的说了 17 次 ——
       按内容哈希去重会毁掉真实历史。唯一一类候选是 **无响应的近重复**：同一角色、同一内容，
       在时间窗内再次出现，且两次之间**没有任何对方角色的消息**（≈"点了没反应又点一次"）。
    3. 报告里只放**摘要**：角色 / 次数 / 时间窗 / 内容的 sha256 前 12 位 / ≤40 字预览。
       不落全文（对话可能含隐私与凭据）。零 token：纯本地统计，不调 LLM。

用法：
    python -m agent.memory.consolidation --scan          # 打印摘要
    python -m agent.memory.consolidation --report        # 落盘 data/maintenance/memory/
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

#: 对话记忆文件（相对仓库根）
DEFAULT_MESSAGES_REL = os.path.join("data", "messages.jsonl")
#: 报告目录（相对仓库根）
DEFAULT_REPORT_DIR_REL = os.path.join("data", "maintenance", "memory")
#: "无响应近重复"的时间窗（秒）：同一句话在该窗口内重复且中间没人回 = 可疑
DEFAULT_WINDOW_SEC = 600.0
#: 预览长度（字符）——只用于人眼识别，不落全文
PREVIEW_CHARS = 40
#: 候选上限（避免报告膨胀）
MAX_CANDIDATES = 50

_ASSISTANT_ROLES = {"assistant", "system", "tool"}


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _content(row: Dict[str, Any]) -> str:
    for key in ("content", "text", "message"):
        value = row.get(key)
        if isinstance(value, str):
            return value.strip()
    return ""


def _parse_ts(raw: Any) -> Optional[float]:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _load_rows(path: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        return []
    return rows


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:12]


def _preview(text: str) -> str:
    flat = " ".join(text.split())
    return flat[:PREVIEW_CHARS] + ("…" if len(flat) > PREVIEW_CHARS else "")


def scan(messages_path: Optional[str] = None, *, window_sec: float = DEFAULT_WINDOW_SEC,
         max_candidates: int = MAX_CANDIDATES) -> Dict[str, Any]:
    """扫描对话记忆，返回"整理候选"摘要（只读、零 token、不删任何东西）。"""
    root = _repo_root()
    path = messages_path or os.path.join(root, DEFAULT_MESSAGES_REL)
    rows = _load_rows(path)

    total = len(rows)
    contents = [_content(r) for r in rows]
    unique = len({c for c in contents if c})

    # 候选：同一角色 + 同一内容，且在时间窗内、两次之间没有任何对方角色的消息
    last_index: Dict[Tuple[str, str], int] = {}
    last_ts: Dict[Tuple[str, str], Optional[float]] = {}
    groups: Dict[Tuple[str, str], Dict[str, Any]] = {}

    def _is_other_role(role: str) -> bool:
        return role in _ASSISTANT_ROLES

    for idx, row in enumerate(rows):
        text = contents[idx]
        if not text:
            continue
        role = str(row.get("role") or "user").strip().lower()
        ts = _parse_ts(row.get("timestamp") or row.get("ts"))
        key = (role, text)

        prev_idx = last_index.get(key)
        prev_ts = last_ts.get(key)
        repeated_ok = False
        if prev_idx is not None and ts is not None and prev_ts is not None:
            if 0 <= ts - prev_ts <= window_sec:
                # 两次之间不能有"对方角色"的消息（assistant/system/tool），否则是正常新一轮
                between = rows[prev_idx + 1: idx]
                between_roles = {str(r.get("role") or "user").strip().lower() for r in between}
                if not any(_is_other_role(r) for r in between_roles):
                    repeated_ok = True

        if repeated_ok:
            group = groups.get(key)
            if group is None:
                group = {
                    "role": role,
                    "count": 1,
                    "first_ts": prev_ts,
                    "last_ts": ts,
                    "content_sha256_12": _digest(text),
                    "preview": _preview(text),
                    "chars": len(text),
                    "reason": "no_response_repeat",
                }
                groups[key] = group
            group["count"] += 1
            group["last_ts"] = ts

        last_index[key] = idx
        last_ts[key] = ts

    candidates = sorted(groups.values(), key=lambda g: (-g["count"], str(g["last_ts"])))
    repeat_lines = sum(g["count"] - 1 for g in candidates)
    repeat_redundant_chars = sum((g["count"] - 1) * int(g.get("chars") or 0) for g in candidates)

    frozen = sum(1 for r in rows if not str(r.get("session_id") or "").strip())

    # v2：两路额外扫描（都只读、零 token、不删）
    agent_mem = scan_agent_memory()
    near_dups = near_duplicate_candidates(rows)
    estimated_redundant_chars = repeat_redundant_chars + int(agent_mem.get("redundant_chars") or 0)

    result: Dict[str, Any] = {
        "scanned_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": path,
        "window_sec": float(window_sec),
        "total_messages": total,
        "unique_contents": unique,
        "duplicate_lines": total - unique if total >= unique else 0,
        "no_response_repeat_groups": len(candidates),
        "no_response_repeat_lines": repeat_lines,
        "candidates": candidates[:max_candidates],
        "candidates_truncated": len(candidates) > max_candidates,
        "frozen_pool": {
            "count": frozen,
            "note": "无 session_id 的历史池：按口径冻结（不迁移、不参与摘要），只统计条数",
        },
        # ── v2（2026-11-13）：让"整理"有量化收益，而不只是把 3 组候选贴上去 ──
        "agent_memory": {
            "available": bool(agent_mem.get("available")),
            "total": int(agent_mem.get("total") or 0),
            "unique_contents": int(agent_mem.get("unique_contents") or 0),
            "duplicate_entries": int(agent_mem.get("duplicate_entries") or 0),
            "redundant_chars": int(agent_mem.get("redundant_chars") or 0),
            "groups_total": int(agent_mem.get("groups_total") or 0),
            "groups": agent_mem.get("groups") or [],
            "note": agent_mem.get("note") or "",
        },
        "near_duplicate_groups": len(near_dups),
        "near_duplicate_candidates": near_dups,
        "savings": {
            "estimated_redundant_chars": estimated_redundant_chars,
            "estimated_tokens": int(round(estimated_redundant_chars / CHARS_PER_TOKEN)),
            "basis": "仅估算『最近一次重复之后多出来的那份』的字符量，按 %.0f 字符≈1 token 折算；"
                     "删除仍需人工确认，这里不代替决策" % CHARS_PER_TOKEN,
        },
        "notes": [
            "只报告不删除：删除必须走人工 confirm",
            "同内容重复不等于噪音（例如『继续』×17 是用户真的说了 17 次）；本报告只把"
            "『时间窗内重复且中间没有任何回复』列为候选",
            "预览截断到 %d 字、内容只留 sha256 前 12 位（不落全文）" % PREVIEW_CHARS,
            "v2 另扫两路：agent_memory.json 的完全重复、同会话 1 小时内的词面近重复"
            "（字符二元组 Jaccard ≥ %.2f）；都没有删除动作" % NEAR_DUP_JACCARD,
        ],
    }
    return result




# ── v2（2026-11-13）：agent_memory 一路 + 词面近重复 + 节省量估算 ──────────────
#: 学习/传感器事件写进记忆的那一路（与对话分开治理，见 §11.103 F-2 分类②）
DEFAULT_AGENT_MEMORY_REL = os.path.join("data", "memory", "agent_memory.json")
#: 近重复判定：字符二元组 Jaccard ≥ 该值才算（只在同会话、1 小时窗内比较）
NEAR_DUP_JACCARD = 0.85
#: 只比较长度 ≥ 该值的文本（太短的一句话相似度高但没有整理价值）
NEAR_DUP_MIN_CHARS = 40
#: 每个候选组最多比较多少条（防 O(n²) 失控）
NEAR_DUP_MAX_ROWS = 400
#: 估算 token：中文 ≈ 1 字 1 token 的上界，这里按 **2 字符≈1 token** 保守估
CHARS_PER_TOKEN = 2.0


def _bigrams(text: str) -> set:
    flat = "".join(str(text).split())
    if len(flat) < 2:
        return set()
    return {flat[i:i + 2] for i in range(len(flat) - 1)}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / float(len(a) + len(b) - inter)


def scan_agent_memory(path: Optional[str] = None, *, max_groups: int = 20) -> Dict[str, Any]:
    """扫描 agent_memory.json 的**完全重复**条目（只读；只报 hash/预览/次数）。"""
    root = _repo_root()
    target = path or os.path.join(root, DEFAULT_AGENT_MEMORY_REL)
    try:
        with open(target, "r", encoding="utf-8", errors="replace") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        return {"available": False, "total": 0, "unique_contents": 0,
                "duplicate_entries": 0, "groups": [], "reason": "read_failed"}
    rows = raw if isinstance(raw, list) else (raw.get("memories") or [])
    if not isinstance(rows, list):
        return {"available": False, "total": 0, "unique_contents": 0,
                "duplicate_entries": 0, "groups": [], "reason": "bad_shape"}
    groups: Dict[str, Dict[str, Any]] = {}
    chars_by_hash: Dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        text = _content(row)
        if not text:
            continue
        digest = _digest(text)
        chars_by_hash[digest] = len(text)
        group = groups.get(digest)
        if group is None:
            groups[digest] = {"sha256_12": digest, "count": 1, "preview": _preview(text),
                              "chars": len(text), "reason": "agent_memory_exact_repeat"}
        else:
            group["count"] += 1
    candidates = [g for g in groups.values() if g["count"] > 1]
    candidates.sort(key=lambda g: (-g["count"], -g["chars"]))
    duplicate_entries = sum(g["count"] - 1 for g in candidates)
    redundant = sum((g["count"] - 1) * g["chars"] for g in candidates)
    return {
        "available": True,
        "total": len(rows),
        "unique_contents": len(groups),
        "duplicate_entries": duplicate_entries,
        "redundant_chars": redundant,
        "groups": candidates[:max_groups],
        "groups_total": len(candidates),
        "note": "内容相同不等于噪音；这里只报计数与 sha256 前 12 位，删除永远人工确认",
    }


def near_duplicate_candidates(rows: List[Dict[str, Any]], *,
                              window_sec: float = 3600.0,
                              threshold: float = NEAR_DUP_JACCARD,
                              max_rows: int = NEAR_DUP_MAX_ROWS,
                              max_candidates: int = MAX_CANDIDATES) -> List[Dict[str, Any]]:
    """同会话、时间窗内**词面高度相似**的助手消息（字符二元组 Jaccard）。

    【为什么不用 embedding】本地没有常驻向量服务，调外部 embedding = 花 token + 外发内容；
    整理是每日/空闲的旁路，用可解释的词面相似度先给"候选"，判断仍由人做。
    """
    picked: List[Tuple[int, str, str, Optional[float]]] = []
    for idx, row in enumerate(rows[:max_rows]):
        role = str(row.get("role") or "").strip().lower()
        if role not in _ASSISTANT_ROLES:
            continue
        text = _content(row)
        if len(text) < NEAR_DUP_MIN_CHARS:
            continue
        picked.append((idx, str(row.get("session_id") or ""), text,
                       _parse_ts(row.get("timestamp") or row.get("ts"))))
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for i in range(len(picked)):
        idx_a, sid_a, text_a, ts_a = picked[i]
        grams_a = _bigrams(text_a)
        if not grams_a:
            continue
        for j in range(i + 1, len(picked)):
            idx_b, sid_b, text_b, ts_b = picked[j]
            if sid_a != sid_b:
                continue
            if ts_a is not None and ts_b is not None and abs(ts_b - ts_a) > window_sec:
                continue
            pair = (idx_a, idx_b)
            if pair in seen:
                continue
            score = _jaccard(grams_a, _bigrams(text_b))
            if score < threshold:
                continue
            seen.add(pair)
            out.append({
                "reason": "lexical_near_duplicate",
                "session": sid_a[:12],
                "similarity": round(score, 3),
                "chars": min(len(text_a), len(text_b)),
                "sha256_12_a": _digest(text_a),
                "sha256_12_b": _digest(text_b),
                "preview_a": _preview(text_a),
                "preview_b": _preview(text_b),
            })
            if len(out) >= max_candidates:
                return out
    return out


def write_report(result: Dict[str, Any], out_dir: Optional[str] = None) -> str:
    """把扫描结果落盘（data/maintenance/memory/consolidation_<ts>.json），返回路径。"""
    target = out_dir or os.path.join(_repo_root(), DEFAULT_REPORT_DIR_REL)
    os.makedirs(target, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(target, "consolidation_%s.json" % stamp)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return path


def latest_report(out_dir: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """最近一份报告（没有则 None）；供 GET /api/memory/review 直接回显。"""
    target = out_dir or os.path.join(_repo_root(), DEFAULT_REPORT_DIR_REL)
    try:
        names = sorted(
            [n for n in os.listdir(target) if n.startswith("consolidation_") and n.endswith(".json")]
        )
    except OSError:
        return None
    if not names:
        return None
    try:
        with open(os.path.join(target, names[-1]), "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _force_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def main(argv=None) -> int:
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(description="记忆整理候选扫描（只读、零 token）")
    parser.add_argument("--scan", action="store_true", help="打印摘要")
    parser.add_argument("--report", action="store_true", help="落盘报告")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON")
    args = parser.parse_args(argv)

    result = scan()
    if args.report:
        path = write_report(result)
        print("[OK] 报告已写入 %s" % path)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("对话记忆: %d 条 / 唯一内容 %d / 无响应近重复 %d 组（%d 行）/ 冻结池 %d 条"
              % (result["total_messages"], result["unique_contents"],
                 result["no_response_repeat_groups"], result["no_response_repeat_lines"],
                 result["frozen_pool"]["count"]))
        for item in result["candidates"][:5]:
            print("  x%-3d %-9s %s  %s" % (item["count"], item["role"],
                                           item["content_sha256_12"], item["preview"]))
        mem = result.get("agent_memory") or {}
        print("agent_memory: %d 条 / 唯一 %d / 完全重复 %d 条（%d 组）"
              % (mem.get("total", 0), mem.get("unique_contents", 0),
                 mem.get("duplicate_entries", 0), mem.get("groups_total", 0)))
        print("词面近重复: %d 组（同会话 1h 内，Jaccard ≥ %.2f）"
              % (result.get("near_duplicate_groups", 0), NEAR_DUP_JACCARD))
        savings = result.get("savings") or {}
        print("可省（估算）: %d 字符 ≈ %d tokens（只估算，不删除）"
              % (savings.get("estimated_redundant_chars", 0), savings.get("estimated_tokens", 0)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
