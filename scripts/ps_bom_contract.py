#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ps_bom_contract.py — PS 文件(.ps1/.psm1) BOM 编码契约公共模块。

背景(2026-08-04 事故): 叠加 BOM(EF BB BF xN) 破坏 PS 5.1 块注释解析,
导致 hook 加载失败与 `Missing expression after unary operator`。
check_ps1_encoding.py(检查/分级) 与 fix_ps_bom.py(检测/修复) 曾各自复制
BOM 常量与纯函数, 本次合并为单一事实源, 消除重复代码(【简易】)。

导出(两脚本 import 复用, 勿再复制实现):
  BOM / REQUIRE_BOM_DEFAULT        编码契约常量
  count_leading_bom(data)          统计前导 EF BB BF 连续次数
  is_utf8(data)                    UTF-8 合法性判定
  hex_head(data, n=8)              文件头十六进制预览(诊断用)
  iter_ps_files(root, bases)       遍历 scripts/ packages/ 下 .ps1/.psm1
"""
from pathlib import Path

BOM = b"\xef\xbb\xbf"

# 关键契约文件: 缺 BOM → BLOCK(相对仓库根目录, 单一事实源)
REQUIRE_BOM_DEFAULT = ["scripts/dev/hook_fail_safe.psm1"]


def count_leading_bom(data: bytes) -> int:
    """统计前导 EF BB BF 连续出现次数。"""
    i = 0
    while data.startswith(BOM, i):
        i += 3
    return i // 3


def is_utf8(data: bytes) -> bool:
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def hex_head(data: bytes, n: int = 8) -> str:
    return " ".join(f"{b:02X}" for b in data[:n])


def iter_ps_files(root: Path, bases=("scripts", "packages")):
    """遍历 root 下指定基目录的 .ps1/.psm1 文件(递归, 排序稳定)。"""
    for base in bases:
        d = root / base
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*")):
            if p.suffix.lower() in (".ps1", ".psm1") and p.is_file():
                yield p
