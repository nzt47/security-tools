#!/usr/bin/env python
"""「立即压缩一次」运维入口 —— 触发一次真正的记忆压缩，并**回读自证**。

【为什么需要】#1336 修掉了「空摘要照样落盘」，但历史那份 v88 = 0 字节的摘要
只能等下一次成功压缩覆盖；而压缩由阈值触发（消息量不到就不跑），
于是「memory.compress 一直 warn」没有任何人能手动收口（§11.17 第 3 项）。

【走生产写路径】不自己 new MemoryManager：那会在**另一个进程**里读写同一份摘要，
与运行中的服务各持一份内存缓存。这里调运行中服务的
`POST /api/context/compress` → `MemoryManager.compress_now()` → `_execute_compression()`，
与服务自动压缩是同一条代码路径。

【自证】压缩前后各读一次 data/summary.txt + data/summary_version.txt：
版本必须前进、字节数必须 > 0，否则如实报失败（不把"接口 200"当成"摘要变好了"）。

用法：
    python scripts/compress_memory_now.py             # 先看现状再触发，最后回读
    python scripts/compress_memory_now.py --dry-run   # 只看现状，不触发
    python scripts/compress_memory_now.py --json
退出码：0 版本前进且摘要非空 / 1 压缩没生效（详情见输出）/ 2 参数·环境·HTTP 错误。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Tuple

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = ROOT / "data" / "summary.txt"
SUMMARY_VERSION = ROOT / "data" / "summary_version.txt"


def read_state() -> Dict[str, Any]:
    """当前摘要落盘状态（只读，取不到就如实给 None）。"""
    size = None
    version = None
    try:
        size = SUMMARY.stat().st_size
    except OSError:
        size = None
    try:
        version = int(SUMMARY_VERSION.read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        version = None
    return {"summary_bytes": size, "summary_version": version}


def evaluate(before: Dict[str, Any], after: Dict[str, Any]) -> Tuple[bool, str]:
    """判定压缩是否真的生效：版本前进 + 摘要非空，两个都要满足。"""
    bv, av = before.get("summary_version"), after.get("summary_version")
    size = after.get("summary_bytes")
    if size is None:
        return False, "读不到 data/summary.txt，无法自证"
    if size == 0:
        return False, "压缩后摘要仍是 0 字节（LLM 返回空，服务端已拒绝落盘，版本不变）"
    if bv is None or av is None:
        return False, "读不到摘要版本号，无法自证"
    if av <= bv:
        return False, "摘要版本没有前进（v%s → v%s）：本次压缩未生效" % (bv, av)
    return True, "摘要 v%s → v%s，%d 字节" % (bv, av, size)


def _token() -> str:
    import os

    value = str(os.environ.get("FLASK_API_TOKEN") or "").strip()
    if value:
        return value
    env_file = ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("FLASK_API_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def trigger(base_url: str, timeout: float) -> Dict[str, Any]:
    """调运行中服务的压缩端点（与自动压缩同一实现）。"""
    headers = {"Content-Type": "application/json"}
    token = _token()
    if token:
        headers["X-API-Token"] = token
    request = urllib.request.Request(base_url.rstrip("/") + "/api/context/compress",
                                     data=b"{}", headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise RuntimeError("压缩端点被拒绝（HTTP %s）：%s" % (exc.code, detail)) from exc
    try:
        return json.loads(raw or "{}")
    except ValueError:
        return {"raw": raw[:200]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="立即压缩一次记忆（走运行中服务）")
    parser.add_argument("--base-url", default="http://127.0.0.1:5678")
    parser.add_argument("--timeout", type=float, default=180.0, help="压缩可能调用 LLM，默认 180s")
    parser.add_argument("--dry-run", action="store_true", help="只看现状，不触发压缩")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    before = read_state()
    if args.dry_run:
        payload = {"dry_run": True, "before": before}
        print(json.dumps(payload, ensure_ascii=False, indent=1) if args.json
              else "当前：摘要 v%s · %s 字节（未触发压缩）"
                   % (before["summary_version"], before["summary_bytes"]))
        return 0

    try:
        response = trigger(args.base_url, args.timeout)
    except Exception as exc:  # noqa: BLE001 失败要如实报
        print("触发失败：%s" % exc)
        return 2

    after = read_state()
    ok, reason = evaluate(before, after)
    data = response.get("data") if isinstance(response, dict) else None
    payload = {"ok": ok, "reason": reason, "before": before, "after": after,
               "response": data if isinstance(data, dict) else response}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        print("压缩前：v%s · %s 字节" % (before["summary_version"], before["summary_bytes"]))
        print("服务回报：%s" % json.dumps(payload["response"], ensure_ascii=False))
        print("压缩后：v%s · %s 字节" % (after["summary_version"], after["summary_bytes"]))
        print(("[OK] " if ok else "[FAIL] ") + reason)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
