#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_dockerignore_dockerfile_consistency.py — .dockerignore 与 Dockerfile COPY 一致性门禁
用途：校验 Docker 构建上下文中，Dockerfile 的每个 COPY 源路径未被 .dockerignore 排除，
      避免出现「源被忽略导致 build 报 not found」的配置矛盾（2026-08-23 ChromaDB 事故教训）。
用法：python scripts/dev/check_dockerignore_dockerfile_consistency.py [--dockerfile Dockerfile] [--dockerignore .dockerignore]
退出码：0=通过（所有 COPY 源可用）；1=存在被排除的 COPY 源
"""
import re
import sys
from pathlib import Path


def load_dockerignore(path: Path) -> list[str]:
    """读取 .dockerignore，返回非注释、非空排除项列表。"""
    excludes = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        excludes.append(line)
    return excludes


def load_copy_sources(path: Path) -> list[str]:
    """提取 Dockerfile 的 COPY 指令源路径（跳过 --from 多阶段拷贝）。"""
    sources = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line.startswith("COPY "):
            continue
        parts = line.split()
        # 跳过 flags（如 COPY --from=xxx / --chown=xxx）
        while parts and parts[0].startswith("--"):
            parts.pop(0)
        if len(parts) >= 2:
            src = parts[0]
            if not src.startswith("http"):
                sources.append(src.rstrip("/"))
    return sources


def is_excluded(src: str, excludes: list[str]) -> list[str]:
    """返回命中 src 的排除项列表（精确/父目录/通配）。"""
    hits = []
    for ex in excludes:
        ex = ex.rstrip("/")
        if ex.startswith("!"):  # 重新包含语法，忽略（本门禁不处理）
            continue
        if ex == src or src.startswith(ex + "/"):
            hits.append(ex)
        elif ex.endswith("*") and src.startswith(ex.rstrip("*")):
            hits.append(ex)
    return hits


def main() -> int:
    root = Path(__file__).resolve().parent.parent.parent  # 仓库根
    args = sys.argv[1:]
    dockerfile = Path(args[args.index("--dockerfile") + 1]) if "--dockerfile" in args else root / "Dockerfile"
    dockerignore = Path(args[args.index("--dockerignore") + 1]) if "--dockerignore" in args else root / ".dockerignore"

    if not dockerfile.exists() or not dockerignore.exists():
        print(f"[FAIL] 文件缺失: dockerfile={dockerfile} dockerignore={dockerignore}")
        return 1

    excludes = load_dockerignore(dockerignore)
    sources = load_copy_sources(dockerfile)
    if not sources:
        print(f"[OK] Dockerfile 无 COPY 源（{dockerfile.name}），跳过")
        return 0

    problems = []
    for src in sources:
        hits = is_excluded(src, excludes)
        if hits:
            problems.append((src, hits))

    if problems:
        print(f"[FAIL] 发现 {len(problems)} 个 COPY 源被 .dockerignore 排除（配置矛盾，会导致 docker build 报 not found）：")
        for src, hits in problems:
            print(f"  - COPY 源: {src}  ← 排除项: {', '.join(hits)}")
        print("修复建议：从 .dockerignore 移除该排除项，或改为排除子路径（如 tests/unit/temp）。")
        return 1

    print(f"[OK] Dockerfile {len(sources)} 个 COPY 源均未被 .dockerignore 排除，上下文一致。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
