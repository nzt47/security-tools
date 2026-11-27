#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""云枢 headless CLI（E4-5 平台集成第一批）：走**既有 REST** 的薄客户端。

【为什么是客户端而不是新服务】E4-5 的「ACP/SDK/headless」里，headless 的最短路径是
让脚本/终端直接用已经在跑的 REST（同一套令牌、同一套审计），而不是再开一个监听面。
本文件**不新增服务端路由、不新增工具、不改任何运行时行为**；删掉它对线上零影响。

用法::

    python scripts/yunshu_cli.py health
    python scripts/yunshu_cli.py version
    python scripts/yunshu_cli.py ask "帮我看下今天的告警" [--session sess_xxx] [--timeout 600]

令牌：只从环境变量 YUNSHU_API_TOKEN 或仓库 .env 的 FLASK_API_TOKEN 读，
**永不打印**（错误信息里也不出现）。Base URL 用 YUNSHU_BASE_URL，缺省
http://127.0.0.1:5678。

退出码：0 成功；1 请求失败；2 参数/用法错误。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

DEFAULT_BASE_URL = "http://127.0.0.1:5678"
#: ask 的默认等待秒数（LLM 一轮可能很慢；只影响客户端超时，不改服务端）
DEFAULT_ASK_TIMEOUT = 600
#: 错误体最多回显多少字符（不搬运整段响应）
_MAX_ERROR_CHARS = 300


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def token_from_env() -> str:
    """令牌：环境变量优先，其次 .env；都没有就空串（服务端会回 401，提示配置）"""
    token = str(os.environ.get("YUNSHU_API_TOKEN") or "").strip()
    if token:
        return token
    path = os.path.join(_repo_root(), ".env")
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("FLASK_API_TOKEN="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _base_url() -> str:
    return (str(os.environ.get("YUNSHU_BASE_URL") or "").strip()
            or DEFAULT_BASE_URL).rstrip("/")


def _error_text(exc: urllib.error.HTTPError) -> str:
    """HTTP 错误只取服务端 JSON 的 error/message 字段（截断），不回整段正文"""
    try:
        raw = exc.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 读不到错误体就只报状态码
        return "HTTP %s" % exc.code
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            detail = str(data.get("error") or data.get("message") or "")
            if detail:
                return "HTTP %s: %s" % (exc.code, detail[:_MAX_ERROR_CHARS])
    except ValueError:
        pass
    return "HTTP %s" % exc.code


def request_json(method: str, path: str, payload: Optional[Dict[str, Any]] = None,
                 token: str = "", timeout: float = 30.0) -> Any:
    """发一个 JSON 请求并返回解析后的 JSON（dict 或 list；错误抛 RuntimeError，消息已脱敏）

    【为什么返回 Any】/api/health 回的是**传感器读数数组**（不是对象）；早期版本
    只接受 dict 会把"健康检查"误报成"响应不是 JSON 对象"。这里按 JSON 原样返回，
    由各命令自己决定怎么读。
    """
    url = _base_url() + path
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method=method.upper())
    req.add_header("Accept", "application/json")
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(_error_text(exc)) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("请求失败：%s" % type(exc).__name__) from None
    try:
        return json.loads(raw)
    except ValueError:
        raise RuntimeError("响应不是 JSON（%d 字符）" % len(raw)) from None


def _print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def cmd_health(args: argparse.Namespace) -> int:
    data = request_json("GET", "/api/health", token=token_from_env(), timeout=args.timeout)
    if args.json:
        _print_json(data)
        return 0
    if isinstance(data, list):
        abnormal = [row for row in data
                    if isinstance(row, dict)
                    and str(row.get("severity") or "normal") not in ("normal", "ok")]
        print("health: %d 项读数，异常 %d 项" % (len(data), len(abnormal)))
        return 0 if not abnormal else 1
    if isinstance(data, dict):
        print("health: ok=%s" % data.get("ok", data.get("status", "?")))
        return 0
    print("health: 无法识别的响应形状")
    return 1


def cmd_version(args: argparse.Namespace) -> int:
    data = request_json("GET", "/api/version", token=token_from_env(), timeout=args.timeout)
    if args.json:
        _print_json(data)
        return 0
    payload = data.get("data") if isinstance(data.get("data"), dict) else data
    backend = payload.get("backend") or {}
    frontend = payload.get("frontend") or {}
    print("backend.commit: %s" % (backend.get("commit") or "-"))
    print("frontend.build_commit: %s" % (frontend.get("build_commit") or "-"))
    bundle = frontend.get("bundle") or payload.get("bundle") or "-"
    print("bundle: %s" % bundle)
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    question = str(args.question or "").strip()
    if not question:
        print("ask 需要一个问题文本", file=sys.stderr)
        return 2
    payload: Dict[str, Any] = {"message": question}
    if args.session:
        payload["session_id"] = str(args.session).strip()
    data = request_json("POST", "/api/chat", payload=payload,
                        token=token_from_env(), timeout=args.timeout)
    if args.json:
        _print_json(data)
    else:
        answer = str(data.get("Yunshu") or data.get("response") or "")
        if answer:
            print(answer)
        else:
            print("（响应里没有 Yunshu 字段；用 --json 看完整结构）")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="yunshu_cli", description="云枢 headless CLI（走既有 REST）")
    sub = parser.add_subparsers(dest="command", required=True)

    health = sub.add_parser("health", help="GET /api/health")
    health.add_argument("--json", action="store_true", help="输出完整 JSON")
    health.add_argument("--timeout", type=float, default=15.0)
    health.set_defaults(func=cmd_health)

    version = sub.add_parser("version", help="GET /api/version（backend / frontend / bundle）")
    version.add_argument("--json", action="store_true", help="输出完整 JSON")
    version.add_argument("--timeout", type=float, default=15.0)
    version.set_defaults(func=cmd_version)

    ask = sub.add_parser("ask", help="POST /api/chat（同步等待整轮回复）")
    ask.add_argument("question", help="问题文本")
    ask.add_argument("--session", default="", help="会话 id（缺省用服务端当前会话）")
    ask.add_argument("--json", action="store_true", help="输出完整 JSON（含 steps/metadata）")
    ask.add_argument("--timeout", type=float, default=DEFAULT_ASK_TIMEOUT)
    ask.set_defaults(func=cmd_ask)
    return parser


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except RuntimeError as exc:
        print("失败：%s" % exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已中断", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
