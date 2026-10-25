# -*- coding: utf-8 -*-
"""torchvision / torchaudio 的「声明但不用」口径守卫（交接单 §9.2）。

口径：这两个包在 pyproject / requirements 里**显式声明并钉版本**，用途是**约束解析**
（torchvision 0.29 会硬钉 torch==2.14，是升级传导源；见 pyproject 注释），
而仓库**没有任何 import**，本机也**不安装**它们。

风险：一旦有人开始 import torchvision，这条口径就悄悄失效了 —— CI 装了它所以能跑，
本机没装所以会炸，且没人知道「配套不用」已经不成立。故用一条守卫用例把口径钉死：
声明还在 + import 仍为 0。
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: 需要扫描 import 的源码根（与 coverage 口径一致）
SOURCE_DIRS = ["agent", "memory", "plugins", "scripts", "core", "cognitive",
               "planning", "persona", "sensor", "lifetrace", "utils"]

IMPORT_RE = re.compile(r"^\s*(?:import|from)\s+(torchvision|torchaudio)\b", re.MULTILINE)


def test_declared_and_pinned():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = "\n".join(data["project"]["dependencies"])
    assert "torchvision>=" in deps and "torchaudio>=" in deps, \
        "解析约束依赖声明被删了？那 torchvision 0.29 会把 torch 顶到 2.14"
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert re.search(r"^torchvision==", req, re.MULTILINE)
    assert re.search(r"^torchaudio==", req, re.MULTILINE)


def test_repo_never_imports_them():
    hits = []
    for name in SOURCE_DIRS:
        base = ROOT / name
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if "node_modules" in path.parts or "build" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for match in IMPORT_RE.finditer(text):
                hits.append(f"{path.relative_to(ROOT)}: {match.group(0).strip()}")
    assert not hits, (
        "仓库开始 import torchvision/torchaudio 了，「声明但不用」的口径已失效："
        "要么把这两个包真正装上并补用例，要么从 pyproject/requirements 里删掉。"
        + "；".join(hits[:5]))
