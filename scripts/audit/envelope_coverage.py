"""X-Envelope 覆盖面盘点（阶段 5 / R5 · P1-front 的前置测量）。

【解决什么问题】
前端页面层的 hubGet/hubPost 仍在用 pickObj/pickList **启发式猜**信封形态。
删掉启发式的前置是「那些端点先以 X-Envelope 显式声明形态」，但**没有人量化过这个前置**：
agent/api_envelope.py 早就实现了 ok()，可是**是否真有端点在用**、**成功路径到底有没有这个头**，
此前没有可复算的答案。本脚本给出这个答案。

【为什么不静态扫源码就下结论】
ok( 这个词在仓库里出现在很多无关位置（含 Path.ok( 之类），纯文本计数会假高；
反过来只看 import 又会漏掉别名引入。故本脚本**以运行期实测为准**：
对真实服务发一次请求，读 X-Envelope 头 —— 只有响应头是权威事实
（同 audit_modules_registry 用运行期 url_map 而非手写清单的道理）。

【用途】
  python scripts/audit/envelope_coverage.py                 # 活体实测（需服务在跑）
  python scripts/audit/envelope_coverage.py --replay PATH   # 回放已有报告，不再打服务
  python scripts/audit/envelope_coverage.py --json-out P    # 同时落盘，供回放

【令牌】从环境变量 FLASK_API_TOKEN 或仓库 .env 读取；**绝不打印、绝不落盘**。
成功路径被鉴权挡住时本脚本会显示 401 并给出原因，不会把它误报成「端点不存在」。

【期望值（本脚本不判死数值）】
守卫不锚在「今天的数字」上 —— 覆盖数是**进度指标**，会随收口合法上升或下降。
它只回答「现在覆盖到哪」，不声称「必须等于 N」。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: 前端启发式所在的目录（页面层）。只读，用于**发现**调用点。
UI_SRC = ROOT / "yunshu-ui" / "src"

#: 端点字面量：单/双引号包裹的 /api/... 路径。
ENDPOINT_RE = re.compile(r"""['"](/api/[A-Za-z0-9_\-/{}.$]*)['"]""")

#: 启发式函数名 —— 它们存在的前提就是「后端形态未知」。
HEURISTIC_RE = re.compile(r"\b(pickObj|pickList)\b")

SKIP_DIRS = {"node_modules", "dist", ".vite", "coverage"}


def _iter_ui_files():
    for dirpath, dirnames, filenames in os.walk(UI_SRC):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn.endswith((".ts", ".tsx")):
                yield Path(dirpath) / fn


def discover_guarded_endpoints():
    """返回 {端点: [文件, ...]} —— 被启发式**同文件**守护的端点。

    【口径】同一文件里既出现启发式、又出现端点字面量 ⇒ 该端点被启发式解析。
    这是**上界**（同文件可能有多处互不相关的用法），但对「盘点前置」足够，
    且不会漏（宁可多留不可少留）。
    """
    found = {}
    for p in _iter_ui_files():
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if not HEURISTIC_RE.search(text):
            continue
        rel = p.relative_to(ROOT).as_posix()
        for m in ENDPOINT_RE.finditer(text):
            ep = m.group(1)
            # 动态片段（模板串）无法直接探测，跳过但如实计数。
            if "{" in ep or "$" in ep:
                continue
            found.setdefault(ep, []).append(rel)
    return found


def load_token():
    tok = str(os.environ.get("FLASK_API_TOKEN", "") or "").strip()
    if tok:
        return tok, "env"
    envf = ROOT / ".env"
    if envf.exists():
        for line in envf.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.match(r"^\s*FLASK_API_TOKEN\s*=\s*(.+)$", line)
            if m:
                return m.group(1).strip().strip('"').strip("'"), ".env"
    return "", "none"


def probe(base, ep, token, timeout=20):
    """探测单个端点。返回 dict（不抛异常 —— 一个端点失败不该终止整轮盘点）。"""
    url = base.rstrip("/") + ep
    req = urllib.request.Request(url, method="GET")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    out = {"endpoint": ep, "status": None, "envelope": None, "shape": None, "error": None}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, headers, body = r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        status, headers, body = e.code, dict(e.headers), e.read()
    except Exception as e:  # noqa: BLE001 连通性失败如实记录，不伪装成端点缺失
        out["error"] = type(e).__name__ + ": " + str(e)[:120]
        return out

    out["status"] = status
    out["envelope"] = headers.get("X-Envelope")
    try:
        j = json.loads(body.decode("utf-8"))
    except Exception:  # noqa: BLE001
        out["shape"] = "non-json"
        return out
    if isinstance(j, dict):
        out["shape"] = "dict"
        out["keys"] = sorted(j.keys())
    elif isinstance(j, list):
        out["shape"] = "list"
        out["len"] = len(j)
    else:
        out["shape"] = type(j).__name__
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="X-Envelope 覆盖面盘点（P1-front 前置测量）")
    ap.add_argument("--base", default="http://127.0.0.1:5678", help="服务地址")
    ap.add_argument("--replay", default="", help="回放已有报告 JSON（不打服务）")
    ap.add_argument("--json-out", default="", help="把本轮结果落盘，供 --replay")
    ap.add_argument("--quiet", action="store_true", help="只打汇总")
    args = ap.parse_args(argv)

    print("=" * 78)
    print("  X-Envelope 覆盖面盘点（P1-front 前置测量）")
    print("=" * 78)

    guarded = discover_guarded_endpoints()
    print("  前端启发式守护的端点（静态发现）: %d 个" % len(guarded))

    if args.replay:
        report = json.loads(Path(args.replay).read_text(encoding="utf-8"))
        rows = report["rows"]
        print("  来源: 回放 %s（%s）" % (args.replay, report.get("generated_at", "?")))
    else:
        token, src = load_token()
        if not token:
            print("  [警告] 未找到 FLASK_API_TOKEN（env / .env）—— 受保护端点会返回 401，")
            print("         此时测到的是**错误路径**的头，不能代表成功路径。")
        else:
            print("  令牌来源: %s（原文不打印、不落盘）" % src)
        rows = [probe(args.base, ep, token) for ep in sorted(guarded)]
        if args.json_out:
            Path(args.json_out).write_text(
                json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(),
                            "base": args.base, "rows": rows},
                           ensure_ascii=False, indent=2),
                encoding="utf-8")
            print("  已落盘: %s（可用 --replay 复算）" % args.json_out)

    if not args.quiet:
        print()
        print("  %-42s %-5s %-9s %s" % ("endpoint", "code", "envelope", "成功体形状"))
        print("  " + "-" * 92)
        for r in sorted(rows, key=lambda x: x["endpoint"]):
            shape = r.get("shape") or "-"
            if shape == "dict":
                shape = "dict keys=" + ",".join((r.get("keys") or [])[:6])
            elif shape == "list":
                shape = "list len=" + str(r.get("len"))
            if r.get("error"):
                shape = "ERROR " + r["error"]
            print("  %-42s %-5s %-9s %s" % (
                r["endpoint"], r.get("status"), r.get("envelope") or "-", shape))

    ok200 = [r for r in rows if r.get("status") == 200]
    with_env = [r for r in ok200 if r.get("envelope")]
    print()
    print("-" * 78)
    print("  端点总数             : %d" % len(rows))
    print("  成功(200)            : %d" % len(ok200))
    print("  成功且带 X-Envelope  : %d   <-- **P1-front 的前置覆盖**" % len(with_env))
    print("  非 200（被挡/不存在） : %d" % (len(rows) - len(ok200)))
    if ok200:
        shapes = {}
        for r in ok200:
            k = r.get("shape")
            if k == "dict":
                k = "dict:" + ",".join((r.get("keys") or [])[:3])
            shapes[k] = shapes.get(k, 0) + 1
        print("  成功体的形状种类     : %d" % len(shapes))
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
