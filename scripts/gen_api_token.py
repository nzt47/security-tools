#!/usr/bin/env python
"""一键生成 API 令牌并（可选）写入 `.env` —— TASK-06 鉴权迁移第 ② 步

## 为什么需要它

本部署实测**未配置任何 API 令牌**（`FLASK_API_TOKEN` / `CP_UI_TOKENS` 皆空）
⇒ `agent/server_auth.py::authorize_token()` 走 `SRC_NO_TOKEN_CONFIGURED` 分支
**直接放行**（fail-open）。迁移路径见 `docs/rfc/鉴权迁移.md`：

| 步 | 动作 | 状态 |
|---|---|---|
| ① | 启动告警 + 健康/状态面暴露 `auth` 状态（不阻断） | ✅ 已落地 |
| ② | **本脚本**：一键生成强随机令牌 | ✅ 本文件 |
| ③ | 文档：客户端如何带令牌（`Authorization: Bearer` / `X-API-Token`） | ⏳ |
| ④ | 下一发布周期改为 fail-closed（带逃生阀） | ⏳ 刻意不做 |

## 用法（默认**不改任何文件**，只打印）

```powershell
python scripts/gen_api_token.py                 # 打印一个强随机令牌 + 环境变量片段
python scripts/gen_api_token.py --write          # 追加/更新 .env 里的 FLASK_API_TOKEN
python scripts/gen_api_token.py --map ui,ci      # 额外给出 CP_UI_TOKENS 映射示例
python scripts/gen_api_token.py --json           # 机器可读输出（便于脚本消费）
```

## 纪律

* **默认零副作用**：不写 `.env`、不重启服务、不动任何数据文件；只有显式 `--write` 才写。
* **`--write` 是追加/替换同名键，不重排 `.env` 既有内容**；写前把原文件另存为
  `.env.bak-<时间戳>`（回退 = 把它拷回来）。
* 令牌只在本进程内存与（可选）`.env` 中出现一次，**不落任何日志**。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ENV_PATH = os.path.join(_ROOT, ".env")
_TOKEN_ENV = "FLASK_API_TOKEN"
_MAP_ENV = "CP_UI_TOKENS"


def _gen_token(nbytes: int = 32) -> str:
    """生成强随机令牌（`secrets.token_urlsafe`：≥32 字节随机 ⇒ 256 位熵）"""
    return secrets.token_urlsafe(int(nbytes))


def _mask(token: str) -> str:
    """脱敏显示（只露前 4 / 后 4）"""
    return token[:4] + "…" + token[-4:] if len(token) > 12 else "…"


def _upsert_env(lines: list, key: str, value: str) -> tuple:
    """在 `.env` 行列表里更新/追加 `key=value`；返回 `(新行列表, 是否已存在)"""
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{key}={value}\n"
            return lines, True
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    lines.append(f"{key}={value}\n")
    return lines, False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="生成 API 令牌（默认只打印，--write 才写 .env）")
    parser.add_argument("--write", action="store_true",
                        help="把令牌写入 .env（会先备份为 .env.bak-<ts>）")
    parser.add_argument("--bytes", type=int, default=32,
                        help="随机字节数（默认 32 ⇒ 256 位熵；下限 16）")
    parser.add_argument("--map", dest="map_names", default="",
                        help="额外的每使用者令牌：逗号分隔的使用者名（生成 CP_UI_TOKENS 片段）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = parser.parse_args(argv)

    if args.bytes < 16:
        print("[FAIL] --bytes 不得小于 16（令牌熵不足等于没配）", file=sys.stderr)
        return 2

    token = _gen_token(args.bytes)
    mapping = {}
    for name in [n.strip() for n in str(args.map_names or "").split(",") if n.strip()]:
        mapping[name] = _gen_token(args.bytes)

    result = {
        "env": {_TOKEN_ENV: token},
        "token_preview": _mask(token),
        "written": False,
        "env_path": _ENV_PATH,
    }
    if mapping:
        # `CP_UI_TOKENS` 的格式由 `agent/security/identity.py::_split_entries` 决定：
        # `<token>:<actor>` 逐条以逗号/分号分隔。此处按该格式给出**可照抄**的片段。
        pairs = ",".join(f"{tok}:{name}" for name, tok in mapping.items())
        result["env"][_MAP_ENV] = pairs
        result["map_preview"] = {name: _mask(tok) for name, tok in mapping.items()}

    if args.write:
        if not os.path.exists(_ENV_PATH):
            print(f"[FAIL] 未找到 {_ENV_PATH}：本脚本**不创建** .env（避免与部署方式冲突）",
                  file=sys.stderr)
            return 2
        backup = f"{_ENV_PATH}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(_ENV_PATH, backup)
        with open(_ENV_PATH, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        for key, value in result["env"].items():
            lines, existed = _upsert_env(lines, key, value)
            result[f"{key}_existing"] = bool(existed)
        with open(_ENV_PATH, "w", encoding="utf-8", newline="") as fh:
            fh.writelines(lines)
        result["written"] = True
        result["backup"] = backup

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("=" * 68)
        print("API 令牌已生成（**只在此处出现一次，请立即保存**）")
        print("=" * 68)
        print(f"  {_TOKEN_ENV}={token}")
        if mapping:
            print(f"  {_MAP_ENV}={result['env'][_MAP_ENV]}")
            for name, tok in mapping.items():
                print(f"    · {name}: {_mask(tok)}")
        print("-" * 68)
        if result["written"]:
            print(f"[OK] 已写入 {_ENV_PATH}（原文件备份：{result.get('backup')}）")
            print("     重启后端后生效；客户端带 Authorization: Bearer <token>")
        else:
            print("（未写任何文件）把上面两行加进 .env 后重启后端即生效。")
            print("     或运行：python scripts/gen_api_token.py --write")
        print("-" * 68)
        print("⚠️ 注意：**未配置令牌时端点不校验**（fail-open）。配置后未带令牌的调用")
        print("   （含本机 UI / 脚本）会被 401 —— 迁移路径与回退见 docs/rfc/鉴权迁移.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
