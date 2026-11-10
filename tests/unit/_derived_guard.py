# -*- coding: utf-8 -*-
"""派生面指纹守卫的**纯函数**（2026-11-10，P2 derive-fingerprint-guard）。

【为什么单独一个模块，而不是写在 tests/conftest.py 里】
守卫本体（session autouse fixture）在 conftest；但**单测**要验它的判据。
若单测用 importlib 再"加载一次 conftest"，会把 conftest 的**导入期副作用**跑第二遍
（网络配置地板 / .env 地板 / sys.path 钉扎…）—— 实测后果：`CP_NETWORK_CONFIG_FILE`
被第二次改写，`test_network_config_test_floor` 在 CI 里红（地板目录落进仓库的 .pytest_tmp）。
故纯函数下沉到这里，conftest 与单测各自 `from _derived_guard import ...`
（`tests/unit` 已在 pytest.ini 的 `pythonpath` 里）。
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Dict, List

#: 关闸开关（只在确认是合法派生时用）
ENV_DISABLED = "CP_TEST_GUARD_DERIVED"

#: 要守的目标（相对仓库根）。判据：这些文件本该由**脚本 + 干净 worktree**改，
#: 任何测试改动都是污染。刻意**不含** data/tool_definitions（吞噬/登记会合法新增 YAML）、
#: data/logs、data/*.db（运行期台账）。
TARGETS = (
    "data/skills_repo",                      # 技能文件轨（*.md 存在性 + 内容）
    "data/skills_mgmt.json",                 # 主轨登记（运行态，但测试绝不该写）
    "data/capability_manifest.json",         # 能力清单（派生物）
    "docs/rfc/云枢能力清单盘点表.md",          # 盘点表（派生物）
    "tests/unit/test_capability_spec.py",    # 三个派生面基线哨兵
    "tests/unit/test_capregistry_core.py",
    "tests/unit/test_confirm_level.py",
)

#: 跳过：字节码（点开头的内部库统一按前缀跳过）
SKIP_PARTS = ("__pycache__",)

#: 报告最多列多少条（避免一次污染把日志刷爆）
MAX_REPORT = 12

#: "新增"里**允许**出现的目录前缀（运行态副作用）：见 conftest 的分流注释
ADDED_HARD_FAIL_PREFIX = "data/skills_repo/"


def disabled() -> bool:
    """关闸：`CP_TEST_GUARD_DERIVED=0|false|no|off` ⇒ True"""
    return os.environ.get(ENV_DISABLED, "1").strip().lower() in ("0", "false", "no", "off")


def snapshot(root: Path) -> Dict[str, str]:
    """目标集合的 {相对路径: sha256[:16]} 快照（**缺失即跳过**，不替测试造文件）"""
    out: Dict[str, str] = {}
    for rel in TARGETS:
        p = Path(root) / rel
        try:
            if p.is_file():
                files = [p]
            elif p.is_dir():
                files = [f for f in p.rglob("*") if f.is_file()
                         and not any(part.startswith(".") or part in SKIP_PARTS
                                     for part in f.parts)]
            else:
                continue
        except OSError:
            continue
        for f in files:
            if f.suffix == ".pyc":
                continue
            try:
                digest = hashlib.sha256(f.read_bytes()).hexdigest()[:16]
            except OSError:
                continue
            out[str(f.relative_to(root)).replace("\\", "/")] = digest
    return out


def diff(before: Dict[str, str], after: Dict[str, str]) -> Dict[str, List[str]]:
    """三类差异：deleted（少了）/ modified（内容变了）/ added（多出来）"""
    return {
        "deleted": sorted(set(before) - set(after)),
        "modified": sorted(k for k in (set(before) & set(after)) if before[k] != after[k]),
        "added": sorted(set(after) - set(before)),
    }


def split_added(diff_result: Dict[str, List[str]]) -> tuple[List[str], List[str]]:
    """把 added 拆成（硬失败, 仅告警）。

    干净检出里 `data/skills_mgmt.json` 本来不存在（.gitignore 的运行态），
    任何 `SkillsMgmtService()` 一构造就会把它创建出来 ⇒ 这类"新增"不是污染；
    而 `data/skills_repo/` 下凭空多出 .md = 多一条技能，必须硬失败。
    """
    added = diff_result.get("added") or []
    hard = [x for x in added if x.startswith(ADDED_HARD_FAIL_PREFIX)]
    soft = [x for x in added if not x.startswith(ADDED_HARD_FAIL_PREFIX)]
    return hard, soft
