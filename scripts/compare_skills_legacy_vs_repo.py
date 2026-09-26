"""对比新旧格式下技能元数据一致性

数据源:
    旧格式: data/skills.json (扁平 JSON)
    新格式: data/skills_repo/<skill_id>/skill.md (YAML front matter)

对比字段 (从 legacy skills.json 字段映射到新格式 front matter):
    id          → id
    name        → name
    enabled     → enabled
    description → description

可作为模块导入:  from scripts.compare_skills_legacy_vs_repo import check, CheckResult
也可作为 CLI:    python scripts/compare_skills_legacy_vs_repo.py

退出码（【G1-B / H-5】双口径）:
    0 = 完全一致 **或** CI 口径下 legacy 缺失的 PASS-SKIP（显式 NOT_APPLICABLE）
    1 = 有差异
    2 = 迁移校验口径下 legacy 缺失（FAIL；这才是不再"假绿"的那一档）
"""
from __future__ import annotations
import os
import sys
import json
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.skills_mgmt.file_store import SkillFileStore

logger = logging.getLogger(__name__)

LEGACY_JSON = ROOT / "data" / "skills.json"
REPO_PATH = ROOT / "data" / "skills_repo"

# 参与对比的字段 (legacy skills.json → 新 front matter)
COMPARE_FIELDS: List[Tuple[str, str]] = [
    ("id", "id"),
    ("name", "name"),
    ("enabled", "enabled"),
    ("description", "description"),
]


@dataclass
class CheckResult:
    """对比结果"""
    all_match: bool
    legacy_count: int
    repo_count: int
    only_legacy: List[str] = field(default_factory=list)
    only_repo: List[str] = field(default_factory=list)
    diffs: List[Dict[str, Any]] = field(default_factory=list)


#: 【G1-B / H-5】双口径：CI 环境允许 PASS-SKIP；迁移校验环境必须 FAIL（可 review）
#:
#: 为什么必须分成两个口径（这是"假绿"的根治点，G1-A §9.6 第 1 条）：
#:     改造前 `main()` 在 legacy 文件缺失时**无条件**打印
#:         "SKIP (无 legacy 文件, 视为 ALL_MATCH)" 并 `return 0`
#:     ⇒ 「检查不通过」被当成「通过」。本地实测该脚本退出码 1（15 处 description
#:     差异 + 7 个 only_legacy），而 CI 全绿 —— 因为 CI 里 `data/skills.json` 被
#:     .gitignore 排除、永远走那条 SKIP 分支。
#:
#: 两个口径的语义：
#:     --ci      （CI 默认）：文件缺失 ⇒ **PASS-SKIP**（显式结论 + 退出码 0）。
#:                CI 里没有 legacy 快照可比，这是**不适用**，不是通过。
#:     --verify  （迁移校验/本地默认）：文件缺失 ⇒ **FAIL** 且退出码非零 ——
#:                迁移校验的前提就是两份数据都在，缺一份说明环境不完整，
#:                此时的"没有差异"是假的。
#: 环境变量口径：`CP_LEGACY_COMPARE_REQUIRE=1` 等价于 --verify（供 shell 集成）。
DEFAULT_MODE_ENV = "CP_LEGACY_COMPARE_REQUIRE"


def _required_mode(explicit: Optional[bool] = None) -> bool:
    """迁移校验口径？（True=legacy 必须存在，缺失即 FAIL）"""
    if explicit is not None:
        return explicit
    raw = os.environ.get(DEFAULT_MODE_ENV)
    if raw is None or str(raw).strip() == "":
        # 未显式指定、也没有环境变量 ⇒ 本地默认按"迁移校验"处理
        # （宁可报红让人看见，也不要静默通过）
        return True
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def load_legacy() -> Dict[str, Dict[str, Any]]:
    """读取旧格式 skills.json → {skill_id: skill_dict}

    [变易] data/skills.json 被 .gitignore 排除 (运行时数据, 不入库),
    CI 环境没有此文件. 缺失时的行为由 `_required_mode()` 决定：
        · 迁移校验口径 → 抛 FileNotFoundError（由 main() 转成 FAIL + 非零退出）
        · CI 口径       → 返回空字典，由 main() 打印 PASS-SKIP
    """
    with open(LEGACY_JSON, encoding="utf-8") as f:
        data = json.load(f)
    return {s["id"]: s for s in data.get("skills", [])}


def load_repo_meta() -> Dict[str, Dict[str, Any]]:
    """读取新格式 skill.md front matter → {skill_id: meta_dict}"""
    store = SkillFileStore(repo_path=str(REPO_PATH))
    return store.load_metadata_index(refresh=True)


def normalize(value: Any) -> Any:
    """归一化: None / "" 视为等价空值"""
    if value is None:
        return ""
    return value


def check(*, verbose: bool = False) -> CheckResult:
    """执行对比，返回 CheckResult

    Args:
        verbose: True 时打印详细对比表
    """
    legacy = load_legacy()
    repo = load_repo_meta()

    legacy_ids = set(legacy.keys())
    repo_ids = set(repo.keys())
    only_legacy = sorted(legacy_ids - repo_ids)
    only_repo = sorted(repo_ids - legacy_ids)

    diffs: List[Dict[str, Any]] = []
    common = legacy_ids & repo_ids

    for sid in sorted(common):
        l = legacy[sid]
        r = repo[sid]
        for lkey, rkey in COMPARE_FIELDS:
            lv = normalize(l.get(lkey))
            rv = normalize(r.get(rkey))
            if lv != rv:
                diffs.append({
                    "skill_id": sid, "field": lkey,
                    "legacy": lv, "repo": rv,
                })

    all_match = not only_legacy and not only_repo and not diffs
    result = CheckResult(
        all_match=all_match,
        legacy_count=len(legacy),
        repo_count=len(repo),
        only_legacy=only_legacy,
        only_repo=only_repo,
        diffs=diffs,
    )

    if verbose:
        _print_result(result)
    return result


def _print_result(result: CheckResult) -> None:
    """打印详细对比结果"""
    print(f"[compare] legacy_count={result.legacy_count} repo_count={result.repo_count}")
    print(f"[compare] legacy_json={LEGACY_JSON}")
    print(f"[compare] repo_path={REPO_PATH}")
    print()

    if result.only_legacy:
        print(f"[SET] 仅在旧格式: {result.only_legacy}")
    if result.only_repo:
        print(f"[SET] 仅在新格式: {result.only_repo}")
    if not result.only_legacy and not result.only_repo:
        print(f"[SET] ID 集合一致")
    print()

    header = f"{'skill_id':<22} {'field':<12} {'legacy':<30} {'repo':<30} {'result'}"
    print(header)
    print("-" * len(header))

    # 构建 diff 索引便于查找
    diff_keys = {(d["skill_id"], d["field"]) for d in result.diffs}
    common_ids = set()
    for sid in result.only_legacy + result.only_repo:
        common_ids.add(sid)
    # 加载 common 用于打印（已知 ID 一致才有字段对比）
    legacy = load_legacy()
    repo = load_repo_meta()
    all_ids = sorted(set(legacy.keys()) & set(repo.keys()))

    for sid in all_ids:
        l = legacy[sid]
        r = repo[sid]
        for lkey, _ in COMPARE_FIELDS:
            lv = normalize(l.get(lkey))
            rv = normalize(r.get(lkey))
            ok = (sid, lkey) not in diff_keys
            mark = "OK" if ok else "DIFF"
            print(f"{sid:<22} {lkey:<12} {str(lv)[:30]:<30} {str(rv)[:30]:<30} {mark}")

    print()
    print("=" * 60)
    print(f"字段对比结果: {'ALL_MATCH' if result.all_match else 'HAS_DIFF'}")


def main(argv: Optional[List[str]] = None) -> int:
    """CLI 入口

    【G1-B / H-5 双口径】
        --ci      文件缺失 ⇒ PASS-SKIP（显式打印，退出码 0）
        --verify  文件缺失 ⇒ FAIL + 退出码 2（迁移校验口径）
        缺省：`CP_LEGACY_COMPARE_REQUIRE` 未设时按 --verify（偏保守，不静默通过）
        --legacy PATH  覆盖 legacy 文件路径（测试/对拍用；CI 不用）
    """
    global LEGACY_JSON
    import argparse
    ap = argparse.ArgumentParser(description="新旧格式技能元数据一致性对比")
    ap.add_argument("--ci", action="store_true",
                    help="CI 口径：legacy 缺失 ⇒ PASS-SKIP（退出码 0），并显式打印")
    ap.add_argument("--verify", action="store_true",
                    help="迁移校验口径：legacy 缺失 ⇒ FAIL（退出码 2）")
    ap.add_argument("--legacy", default=None, help="覆盖 legacy 快照路径")
    args = ap.parse_args(argv)

    if args.legacy:
        LEGACY_JSON = Path(args.legacy)
    require = _required_mode(True if args.verify else (False if args.ci else None))

    try:
        legacy = load_legacy()
    except FileNotFoundError:
        if require:
            print(f"[compare] FAIL: legacy 快照不存在 ({LEGACY_JSON})")
            print("[compare]       迁移校验口径要求两份数据同时在（CI 口径请加 --ci）")
            print("[compare] RESULT: FAIL(legacy_missing) —— 不是 ALL_MATCH")
            return 2
        print(f"[compare] PASS-SKIP: legacy 快照不存在 ({LEGACY_JSON})")
        print("[compare]            data/skills.json 被 .gitignore 排除 ⇒ CI 无此文件，")
        print("[compare]            该断言在 CI 环境**不适用**（NOT_APPLICABLE），")
        print("[compare]            不等于通过；迁移校验请用 --verify。")
        print("[compare] RESULT: PASS-SKIP(not_applicable)")
        return 0
    if not legacy:
        print("[compare] PASS-SKIP: legacy 快照为空（0 条技能）—— 无内容可比")
        print("[compare] RESULT: PASS-SKIP(empty)")
        return 0
    result = check(verbose=True)
    return 0 if result.all_match else 1


if __name__ == "__main__":
    sys.exit(main())
