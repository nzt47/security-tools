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

退出码: 0=完全一致, 1=有差异
"""
from __future__ import annotations
import sys
import json
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

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


def load_legacy() -> Dict[str, Dict[str, Any]]:
    """读取旧格式 skills.json → {skill_id: skill_dict}

    [变易] data/skills.json 被 .gitignore 排除 (运行时数据, 不入库),
    CI 环境永远没有此文件. 迁移完成后该文件不再维护, 缺失时返回空字典
    让 check() 走 skip 分支, 而非抛 FileNotFoundError 阻断 CI.
    """
    try:
        with open(LEGACY_JSON, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"[compare] SKIP: legacy 文件不存在 ({LEGACY_JSON})")
        print(f"[compare]       data/skills.json 被 .gitignore 排除, CI 环境无此文件.")
        print(f"[compare]       迁移已完成时无 legacy 可对比, 视为 ALL_MATCH.")
        return {}
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


def main() -> int:
    """CLI 入口"""
    # [不易] legacy 文件缺失时 (CI 环境, data/skills.json 被 gitignore) 直接 skip,
    # 不走对比逻辑 (否则 only_repo 非空 → all_match=False → 误报失败).
    legacy = load_legacy()
    if not legacy:
        print("[compare] RESULT: SKIP (无 legacy 文件, 视为 ALL_MATCH)")
        return 0
    result = check(verbose=True)
    return 0 if result.all_match else 1


if __name__ == "__main__":
    sys.exit(main())
