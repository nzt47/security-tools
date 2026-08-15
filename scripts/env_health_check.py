#!/usr/bin/env python3
"""环境健康检查脚本

用途（P0 任务清单 T-8）：回归/修复前确认关键依赖完整可用，避免被环境问题干扰判定。
检查项：
1. Python 解释器与版本
2. 关键依赖 import 冒烟（必需：flask/requests/yaml；可选：transformers/sqlite_vec/chromadb/sentence_transformers）
3. sys.path 污染检测（仓库内同名目录 / 损坏模块）
4. 数据目录可写性
5. venv 与全局解释器提示

--ci 模式（CI 流水线专用）：
- 必需依赖缺失 → FAIL（阻断）
- 可选依赖缺失 → WARN（不阻断，业务代码有降级路径：sqlite-vec→FTS5+BM25、chromadb→BM25、
  transformers/sentence_transformers→SKILLS_OFFLINE mock）

退出码: 0=健康, 1=存在 FAIL 项
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# 必需依赖（缺失即阻断，本地与 CI 一致）
REQUIRED_DEPS = [
    ("flask", "Web 服务"),
    ("requests", "HTTP 客户端"),
    ("yaml", "配置解析"),
]

# 可选依赖（--ci 模式下缺失仅 WARN；本地完整环境缺失按 FAIL 处理）
OPTIONAL_DEPS = [
    ("transformers", "LLM/Embedding 模型库"),
    ("sqlite_vec", "向量检索扩展（不可用时降级纯 FTS5+BM25）"),
    ("chromadb", "向量库（不可用时降级纯 BM25）"),
    ("sentence_transformers", "Embedding 推理"),
]

# 数据目录（相对仓库根）
DATA_DIRS = [
    "data",
    "data/logs",
    "data/feedback",
]

# 已知"仓库内同名目录会污染 sys.path"的候选
PATH_POLLUTION_HITS = ("transformers", "sqlite_vec", "chromadb")


def check_import(mod_name: str) -> bool:
    try:
        __import__(mod_name)
        return True
    except Exception:
        return False


def check_deps(deps: list[tuple[str, str]], ci_mode: bool) -> bool:
    """依赖冒烟；返回是否有 FAIL 项。ci_mode 下调用方已将可选依赖降级为 WARN。"""
    failed = False
    for mod, desc in deps:
        ok = check_import(mod)
        status = "OK" if ok else ("FAIL" if not ci_mode else "WARN(可选,降级路径)")
        print(f"  [{status}] {mod:<22} ({desc})")
        if not ok and not ci_mode:
            failed = True
    return failed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ci", action="store_true",
                        help="CI 模式：可选依赖缺失降级为 WARN 不阻断（业务代码有降级路径）")
    args = parser.parse_args()

    failed = False
    print(f"[env] python      : {sys.executable}")
    print(f"[env] python ver  : {sys.version.split()[0]}")
    print(f"[env] repo root   : {REPO_ROOT}")
    print(f"[env] mode        : {'CI (可选依赖 WARN)' if args.ci else '本地完整环境'}")

    # 1. 必需依赖冒烟
    print("\n== 必需依赖 import 冒烟 ==")
    if check_deps(REQUIRED_DEPS, ci_mode=False):
        failed = True

    # 2. 可选依赖冒烟（--ci 降级 WARN）
    print("\n== 可选依赖 import 冒烟 ==")
    if check_deps(OPTIONAL_DEPS, ci_mode=args.ci):
        failed = True

    # 3. transformers 子模块专项（历史故障点；--ci 缺失时降级 WARN）
    print("\n== transformers 子模块专项 ==")
    if check_import("transformers"):
        for sub in ("configuration_utils", "tokenization_utils_base", "modeling_utils"):
            try:
                m = __import__(f"transformers.{sub}", fromlist=[sub])
                print(f"  [OK] transformers.{sub:<28} {getattr(m, '__file__', '?')}")
            except Exception as e:
                print(f"  [FAIL] transformers.{sub}: {type(e).__name__}: {e}")
                failed = True
    else:
        mark = "WARN(可选,SKILLS_OFFLINE 降级)" if args.ci else "FAIL"
        print(f"  [{mark}] transformers 不可用，跳过子模块专项")

    # 4. sys.path 污染检测（仓库内顶层同名目录）
    print("\n== sys.path 污染检测 ==")
    polluted = False
    for entry in sys.path:
        if not entry:
            continue
        p = Path(entry)
        if not p.is_dir() or not p.resolve().is_relative_to(REPO_ROOT.resolve()):
            continue
        for name in PATH_POLLUTION_HITS:
            if (p / name).exists():
                print(f"  [FAIL] 仓库路径 {p / name} 可能遮蔽同名包")
                polluted = True
    if not polluted:
        print("  [OK] 未发现仓库内同名目录污染")

    # 5. 数据目录可写性
    print("\n== 数据目录可写性 ==")
    for rel in DATA_DIRS:
        d = REPO_ROOT / rel
        try:
            d.mkdir(parents=True, exist_ok=True)
            probe = d / ".env_health_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            print(f"  [OK] {rel}")
        except Exception as e:
            print(f"  [FAIL] {rel}: {e}")
            failed = True

    # 6. venv 提示
    # 【变易】跨平台: POSIX venv 解释器为 venv/bin/python, Windows 为 venv/Scripts/python.exe
    # Why: 仓库曾误提交 Windows venv（venv/Scripts/python.exe），Linux CI checkout 后
    #   exists() 为 True 但执行报 Permission denied。venv 是本地开发概念，CI 无 venv。
    venv_py = (
        REPO_ROOT / "venv" / "bin" / "python"
        if sys.platform != "win32"
        else REPO_ROOT / "venv" / "Scripts" / "python.exe"
    )
    print("\n== venv 检查 ==")
    if venv_py.exists():
        try:
            r = subprocess.run(
                [str(venv_py), "-c",
                 "import transformers,sys;print(transformers.__version__);print(sys.executable)"],
                capture_output=True, text=True, timeout=30,
            )
            ver, exe = (r.stdout.strip().splitlines() + ["?", "?"])[:2]
            print(f"  [venv] transformers={ver} @ {exe}")
            if r.returncode != 0:
                # Why 分支处理: venv 是本地开发环境概念（CI 无 venv 跳过）。
                # --ci 模式下 venv 内可选依赖缺失仅 WARN（对齐可选依赖降级语义），
                # 本地模式 FAIL（提示修复本地 venv）。
                if args.ci:
                    print(f"  [WARN(可选,venv)] venv transformers import 失败: "
                          f"{r.stderr.strip()[:200]}")
                else:
                    print(f"  [FAIL] venv transformers import 失败: {r.stderr.strip()[:200]}")
                    failed = True
        except Exception as e:
            # Why CI 降级: 误提交的 Windows venv 二进制在 Linux 上执行 Permission denied,
            #   CI checkout 无本地 venv 概念, 降级 WARN 不阻断（本地仍 FAIL 提示修复）。
            if args.ci:
                print(f"  [WARN(可选,venv)] venv 检查异常（CI 降级）: {e}")
            else:
                print(f"  [FAIL] venv 检查异常: {e}")
                failed = True
    else:
        print(f"  [info] 仓库无 venv/{'bin/python' if sys.platform != 'win32' else 'Scripts/python.exe'}（使用全局解释器）")

    print(f"\n== 结果: {'HEALTHY' if not failed else 'FAILED'} ==")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
