#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pre-commit 运行噪音自动清理

背景
----
仓库里部分 JSON 数据文件被 git 追踪，但后端进程运行时会持续改写它们：
    - data/learned_workflows.json：已有工作流的 统计字段 漂移
      （success_count/failure_count/confidence/updated_at/last_used_at 每次
      命中都会 +1 / 刷新时间戳）——不含任何新内容，纯运行噪音；
    - 少数文件仅存在 末尾换行/行尾 差异（内容完全等价）的同类噪音。

本脚本在每次 git commit 前由 hooks/pre-commit 调用，只做两件安全的事：
    1. 统计漂移：对「两边都存在、且除统计字段外完全一致」的条目，把统计字段
       还原为 HEAD 版本 —— 新条目 / 被删条目 / 真实内容改动 一律保留；
    2. 换行噪音：内容与 HEAD 等价（仅尾部换行/CRLF 差异）的文件直接还原。

绝不丢弃真实改动；纯噪音时工作区也一并恢复干净，避免“结案后又冒出改动”。

用法
----
    python scripts/clean_runtime_noise.py
退出码恒为 0（成功/无事可做），失败打印并返回 1（阻断提交）。
"""
import json
import os
import subprocess
import sys

try:  # Windows 控制台默认 gbk，统一按 UTF-8 打印（失败则忽略）
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

# 需要清理的仓库相对路径（相对仓库根）
ACCOUNTING_PATHS = {
    "data/learned_workflows.json": {
        # 每个条目的“运行统计”字段：只这些字段出现漂移时会被还原
        "keys": {"success_count", "failure_count", "confidence",
                 "updated_at", "last_used_at"},
    },
}
# 仅尾部换行/CRLF 差异的文件（内容等价即还原）
NEWLINE_ONLY_PATHS = [
    "data/skills_descriptions_overlay.json",
]


def _run(args: list, check: bool = True) -> subprocess.CompletedProcess:
    # Windows 控制台默认 gbk：git 输出(UTF-8 文件内容)必须显式 utf-8 解码
    return subprocess.run(args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=check)


def repo_root() -> str:
    out = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"],
        text=True, encoding="utf-8", errors="replace").strip()
    return out


def head_text(root: str, path: str):
    """返回 HEAD 中该文件的原文（不存在返回 None）。"""
    p = _run(["git", "show", f"HEAD:{path}"], check=False)
    if p.returncode != 0:
        return None
    return p.stdout


def _write_bytes(path: str, data: str):
    # 文本模式（不传 newline=""）：Windows 下按 CRLF 落盘，兼容 core.autocrlf，
    # 避免“字节等于 HEAD 却仍被 git 判定为修改”的换行噪音
    with open(path, "w", encoding="utf-8") as f:
        f.write(data)


def _dump_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def _restore_head(root: str, path: str) -> bool:
    """把工作区文件还原为 HEAD 内容并从暂存区移除。"""
    base = head_text(root, path)
    if base is None:
        return False
    _write_bytes(os.path.join(root, path), base)
    _run(["git", "restore", "--staged", path], check=False)  # 已暂存则取消
    return True


def _clean_newline_noise(root: str, path: str) -> bool:
    cur_path = os.path.join(root, path)
    base = head_text(root, path)
    if base is None or not os.path.exists(cur_path):
        return False
    with open(cur_path, "r", encoding="utf-8", errors="replace") as f:
        cur = f.read()
    norm = lambda s: s.replace("\r\n", "\n").rstrip("\n").rstrip()
    if cur == base:  # 本来就一致
        return False
    if norm(cur) == norm(base):
        print(f"[clean-runtime-noise] {path}: 仅换行差异，已还原")
        return _restore_head(root, path)
    return False


def _clean_accounting(root: str, path: str, spec: dict) -> bool:
    """统计漂移清理：只还原两边共有且内容(除统计字段)一致的条目的统计字段。

    返回 True 表示发生过清理/暂存调整。
    """
    cur_path = os.path.join(root, path)
    base_text = head_text(root, path)
    if base_text is None or not os.path.exists(cur_path):
        return False
    try:
        with open(cur_path, "r", encoding="utf-8") as f:
            cur = json.load(f)
        base = json.loads(base_text)
    except Exception as e:  # noqa: BLE001 JSON 异常时不动手（保守）
        print(f"[clean-runtime-noise] {path}: JSON 解析失败，跳过（{e}）")
        return False
    if not isinstance(cur, dict) or not isinstance(base, dict):
        return False

    acct = spec["keys"]
    # 逐条目：统计字段一律还原为 HEAD；内容字段保留当前工作区。
    # 全部条目还原后与 HEAD 一致 → 纯噪音，整文件还原；
    # 存在内容改动 → 写“清理后”版本并重新暂存（噪音不随真实改动入库）。
    cleaned: dict = {}
    noise_ids, real_ids = [], []
    for wid, wf in cur.items():
        bf = base.get(wid)
        if bf is None:
            cleaned[wid] = wf            # 新增条目：保留
            real_ids.append(wid)
            continue
        if not isinstance(wf, dict) or not isinstance(bf, dict):
            cleaned[wid] = wf            # 非对象结构：不动
            real_ids.append(wid)
            continue
        merged = dict(bf)                # 以 HEAD 为基础
        for k, v in wf.items():
            if k not in acct:            # 只覆盖非统计字段
                merged[k] = v
        cleaned[wid] = merged
        if merged == bf:
            noise_ids.append(wid)        # 纯统计漂移
        else:
            real_ids.append(wid)         # 有真实内容改动

    if cleaned == base:
        # 只剩统计漂移 → 整文件还原，工作区干净
        print(f"[clean-runtime-noise] {path}: {len(noise_ids)} 条统计漂移，已还原")
        return _restore_head(root, path)

    if noise_ids or cleaned != cur:
        # 真实改动（或字段被清洗）→ 写清理后版本并重新暂存
        txt = json.dumps(cleaned, ensure_ascii=False, indent=2)
        suffix = "\n" if base_text.endswith("\n") else ""  # 跟随 HEAD 换行约定
        with open(cur_path, "w", encoding="utf-8") as f:
            f.write(txt + suffix)
        _run(["git", "add", path], check=False)
        print(f"[clean-runtime-noise] {path}: 还原 {len(noise_ids)} 条统计漂移，"
              f"保留 {len(real_ids)} 条真实改动")
        return True
    return False


def main() -> int:
    try:
        root = repo_root()
    except Exception:  # noqa: BLE001 非 git 仓库环境直接放行
        return 0
    os.chdir(root)
    for path in NEWLINE_ONLY_PATHS:
        try:
            _clean_newline_noise(root, path)
        except Exception as e:  # noqa: BLE001 单项失败不阻断提交
            print(f"[clean-runtime-noise] {path} 处理失败（跳过）: {e}")
    for path, spec in ACCOUNTING_PATHS.items():
        try:
            _clean_accounting(root, path, spec)
        except Exception as e:  # noqa: BLE001
            print(f"[clean-runtime-noise] {path} 处理失败（跳过）: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
