# -*- coding: utf-8 -*-
r"""yunshu learn —— 共享工具层。

【为何放在 agent/ 下而非新建顶层包】cloudshu/cli.py 的文档明确警告：
    pyproject.toml 的 packages.find 未登记的顶层包，pip install -e . 后会
    ModuleNotFoundError（"本地能跑、装完就没了"）。agent 已在 where 列表中，
    其子包自动被包含 ⇒ 放这里零打包风险。

【零 token】本包全部子命令纯本地 CPU，不调用任何 API（方案硬约束 ①）。
"""
from __future__ import annotations

import collections
import datetime as _dt
import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional

try:
    import zstandard as zstd
except ImportError:  # pragma: no cover
    zstd = None  # type: ignore

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

#: 进入蒸馏的白名单（实测其余 81.0% 为噪声）
CORE_TYPES = {
    "tool/call", "tool/result", "user/message", "assistant/message",
    "turn/start", "turn/end", "step/start", "step/end", "todo/write", "session",
}
NOISE_TYPES = {"reasoning-chunks", "tool-call-chunks", "assistant/chunk", "text-chunks"}

#: 工具名归一（方案原文 -> 实测小写名）
TOOL_ALIAS = {
    "Write": "write", "Edit": "edit", "MultiEdit": "edit",
    "Read": "read", "Bash": "pwsh", "Shell": "pwsh",
    "write_file": "write", "str_replace_editor": "edit",
    "search_files": "grep", "list_dir": "glob",
}

#: 硬阻断（P0 假阳性审计后的定稿集：身份证号校验位仅 4.1% 通过、AWS AKIA 100% 为文档示例，均已剔除）
HARD_BLOCK = [
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("api_key_sk", re.compile(r"\bsk-[A-Za-z0-9_\-]{24,}")),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{20,}")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36}\b")),
    ("slack_webhook", re.compile(r"https://hooks\.slack\.com/\S+")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("url_with_creds", re.compile(r"://[^/\s:@]{1,64}:[^/\s:@]{1,64}@")),
]
#: 脱敏替换（高频；拒收会导致模型失学 —— 实测绝对路径命中占 result 的 41.1%）
REPLACE = [
    ("abs_path", re.compile(r"[A-Za-z]:\\Users\\[^\\\s\"']{1,60}"), "<PATH>"),
    ("email", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "<EMAIL>"),
    ("ip_private", re.compile(r"\b(?:10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}\b"), "<IP>"),
]


#: base64 长串 / data:image 检测（P0 实测：语料中 base64 仅个位数命中、图片 0 命中）
B64_RE_PAT = re.compile(r"[A-Za-z0-9+/]{200,}={0,2}")
IMG_RE_PAT = re.compile(r"data:image/")


def scan_privacy(files: List[str]) -> Dict[str, Any]:
    """脱敏命中统计。**只输出计数，不输出任何命中明文。**

    判定用 BM25 以外的正则集，与 HARD_BLOCK / REPLACE 同源，保证"统计口径 = 拦截口径"。
    """
    block = collections.Counter()
    repl = collections.Counter()
    for path in files:
        for line in decompress_lines(path):
            try:
                o = json.loads(line)
            except Exception:
                continue
            t = o.get("type")
            d = o.get("data")
            if not isinstance(d, dict):
                continue
            texts: List[str] = []
            if t == "tool/call" and isinstance(d.get("arguments"), str):
                texts.append(d["arguments"])
            elif t == "tool/result":
                m = d.get("message")
                if isinstance(m, dict) and isinstance(m.get("content"), list):
                    for b in m["content"]:
                        if isinstance(b, dict):
                            tx = result_text(b)
                            if tx:
                                texts.append(tx)
            elif t == "user/message":
                tx = content_text(d.get("content"))
                if tx:
                    texts.append(tx)
            for txt in texts:
                for name, rx in HARD_BLOCK:
                    block[name] += len(rx.findall(txt))
                for name, rx, _sub in REPLACE:
                    repl[name] += len(rx.findall(txt))
    return {"hard_block": block.most_common(), "redact_replace": repl.most_common()}


def iter_sessions(root: str) -> List[str]:
    """递归查找 session.jsonl.zstd。"""
    out: List[str] = []
    for dp, _dn, fn in os.walk(root):
        if "session.jsonl.zstd" in fn:
            out.append(os.path.join(dp, "session.jsonl.zstd"))
    return sorted(out)


def decompress_lines(path: str) -> Iterable[str]:
    """整文件解压成行。**多帧安全**。

    【必需】session.jsonl.zstd 是连续多帧拼接（平均 3,256 帧/文件），
    只解首帧的 API 会**静默截断**（实测 3,830 条 -> 1 条且不报错）。
    故必须 read_across_frames=True，并保留按帧魔数手工切分的回退路径。
    """
    if zstd is None:
        raise RuntimeError("需要 zstandard（当前未安装；注意它此前未列入 requirements.txt）")
    dctx = zstd.ZstdDecompressor()
    buf = b""
    with open(path, "rb") as fh:
        try:
            with dctx.stream_reader(fh, read_across_frames=True) as reader:
                while True:
                    chunk = reader.read(1 << 20)
                    if not chunk:
                        break
                    buf += chunk
        except Exception:  # 回退：按帧魔数手工切分
            fh.seek(0)
            raw = fh.read()
            buf = b""
            offs = [m.start() for m in re.finditer(re.escape(ZSTD_MAGIC), raw)]
            offs.append(len(raw))
            for i in range(len(offs) - 1):
                try:
                    buf += dctx.decompress(raw[offs[i]:offs[i + 1]])
                except Exception:
                    pass
    for line in buf.decode("utf-8", errors="replace").split("\n"):
        if line.strip():
            yield line


#: 秒 / 毫秒的分界（1e11 毫秒 ≈ 1973 年，1e11 秒 ≈ 5138 年，不会误判）
_EPOCH_MS_CUT = 1e11


def to_epoch_ms(v: Any) -> Optional[float]:
    """把会话记录的时间戳统一成 epoch 毫秒。

    【必须支持两种形态 —— 这是一个真实踩过的坑】
      DSH 会话里 `time` 是 **epoch 毫秒整数**（实测 1789200668629），
      **不是** ISO 字符串。此前 ts_le 写的是 `if not isinstance(ts, str): return True`，
      于是对真实会话**恒为真** ⇒ `--snapshot-until` 成了静默空操作，
      "冻结后结果才可复现"这一保证实际不成立。
      单元测试没发现，是因为测试夹具把 time 写成了 ISO 字符串（与真实 schema 不符，假绿）。
    """
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        n = float(v)
        return n * 1000.0 if abs(n) < _EPOCH_MS_CUT else n
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            return to_epoch_ms(float(s))
        except ValueError:
            pass
        try:
            dt = _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return dt.timestamp() * 1000.0
    return None


def ms_to_iso(ms: Any) -> Optional[str]:
    """epoch 毫秒 -> 本地 ISO（秒级），失败返回 None。"""
    e = to_epoch_ms(ms)
    if e is None:
        return None
    try:
        return _dt.datetime.fromtimestamp(e / 1000.0).strftime("%Y-%m-%dT%H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return None


def ts_le(ts: Any, cutoff: Optional[str]) -> bool:
    """记录时间是否 <= 快照截止点（会话库是活的，冻结后结果才可复现）。

    【无法解析时不筛】宁可多收，不可因为解析失败而静默丢掉整段历史。
    但这正是旧实现的隐患来源，故新增 tests/unit 回归用例**锁死真实形态（ms 整数）**。
    """
    if not cutoff:
        return True
    a, b = to_epoch_ms(ts), to_epoch_ms(cutoff)
    if a is None or b is None:
        return True
    return a <= b


def result_text(block: Dict[str, Any]) -> str:
    """结果块文本：content 是 [{type,text}] 数组（不是字符串，实测）。"""
    inner = block.get("content")
    if isinstance(inner, str):
        return inner
    if isinstance(inner, list):
        return "".join((it.get("text", "") if isinstance(it, dict) else it) for it in inner)
    return ""


def content_text(c: Any) -> str:
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return result_text({"content": c})
    return ""


def desensitize(text: str):
    """返回 (处理后文本, 硬阻断模式名 or None, 替换次数)。"""
    if not isinstance(text, str):
        return "", None, 0
    for name, rx in HARD_BLOCK:
        if rx.search(text):
            return text, name, 0
    n = 0
    for _name, rx, sub in REPLACE:
        text, k = rx.subn(sub, text)
        n += k
    return text, None, n


def scan_hard_block(obj: Any) -> Optional[str]:
    """递归扫描硬阻断模式。只返回模式名，不返回命中值。"""
    if isinstance(obj, dict):
        for v in obj.values():
            hit = scan_hard_block(v)
            if hit:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = scan_hard_block(v)
            if hit:
                return hit
    elif isinstance(obj, str):
        for name, rx in HARD_BLOCK:
            if rx.search(obj):
                return name
    return None


def normalize_tool(name: Optional[str]) -> str:
    if not name:
        return "<none>"
    return TOOL_ALIAS.get(name, name.lower())


def pct(a: int, b: int) -> str:
    return ("%.1f%%" % (100.0 * a / b)) if b else "n/a"


def stats(vals: List[int]) -> Optional[Dict[str, int]]:
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    return {"n": n, "min": s[0], "p50": s[n // 2],
            "p90": s[min(n - 1, int(n * 0.9))], "max": s[-1],
            "mean": round(sum(s) / n)}


def atomic_write_lines(path: str, rows: Iterable[Dict[str, Any]]) -> None:
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
