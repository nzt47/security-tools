"""评估集样本校验脚本（任务1 Step 2）— data/evals/ schema v1 强制校验

校验内容（0 非法 → 退出码 0；存在非法 → 退出码 1）:
    1. 逐条样本字段合法性:
       - id: 非空字符串，且全池唯一
       - category: 必须与所在目录一致，且 ∈ 已知类别（search/code/chat/tool/planning/general）
       - task: 非空字符串
       - expected_output: 若存在，type ∈ {exact, contains, json, validator} 且子字段合法
       - created_at: 非空字符串
       - metadata: 必含 input(dict) / difficulty / source / input_hash；difficulty ∈
         {TRIVIAL, SIMPLE, NORMAL, COMPLEX}；source ∈ {manual, reflection, feedback, novelty}
       - input_hash: 16 位 hex，且与 compute_input_hash(category, task, input) 一致
    2. 去重: input_hash 全池唯一（跨类别）
    3. manifest.json 一致性:
       - versions[version].categories 中登记的 id 必须存在于池中且类别正确
       - 池中每个样本 id 必须被 current 版本登记（防漏登）

用法:
    python scripts/eval_samples_validate.py                 # 校验默认目录 data/evals
    python scripts/eval_samples_validate.py --dir <dir>     # 校验指定目录
    python scripts/eval_samples_validate.py --quiet         # 仅输出摘要

不变式（不易）:
    - 绝不修改样本数据；只读校验。
    - 校验算法（compute_input_hash）与 eval_sample_ingest 保持一致，
      供回归门禁去重/审计复用。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 项目根目录 = 本文件上两级（scripts → 项目根）
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# 已知类别（与 evaluator._KNOWN_CATEGORIES + 扩展 tool/planning 对齐）
KNOWN_CATEGORIES = ("search", "code", "chat", "tool", "planning", "general")
# 难度（复用 enhanced_planner 复杂度语义，任务7 元数据）
KNOWN_DIFFICULTIES = ("TRIVIAL", "SIMPLE", "NORMAL", "COMPLEX")
# 来源（人工标注 / 反思 / 反馈 / 新颖事件）
KNOWN_SOURCES = ("manual", "reflection", "feedback", "novelty")
# expected_output 校验类型
KNOWN_CHECKER_TYPES = ("exact", "contains", "json", "validator")

# 非样本目录/文件（不入评估集）
EXCLUDED_DIRS = {"_pending"}
EXCLUDED_FILES = {"manifest.json", "baselines.json", "README.md"}


def compute_input_hash(category: str, task: str, input_meta: Any) -> str:
    """去重哈希：sha256(canonical_json({category, task, input}))[:16]

    Why canonical: 字典键排序 + ensure_ascii=False，保证同语义样本哈希一致
    （不随键顺序/编码变化漂移）。与 eval_sample_ingest.compute_input_hash 同算法。
    """
    payload = json.dumps(
        {"category": category, "task": task, "input": input_meta or {}},
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _is_valid_hash(h: str) -> bool:
    return bool(h) and len(h) == 16 and all(c in "0123456789abcdef" for c in h.lower())


@dataclass
class SampleIssue:
    """单条样本的非法项"""
    sample_id: str
    category: str
    message: str


@dataclass
class ValidationReport:
    """校验报告（全量通过 = 0 非法）"""
    base_dir: str = ""
    total: int = 0
    per_category: Dict[str, int] = field(default_factory=dict)
    issues: List[SampleIssue] = field(default_factory=list)
    manifest_issues: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues and not self.manifest_issues

    def summary(self) -> str:
        lines = [
            f"[eval_samples_validate] 目录: {self.base_dir}",
            f"[eval_samples_validate] 样本总数: {self.total}",
        ]
        for cat, n in sorted(self.per_category.items()):
            lines.append(f"  - {cat}: {n} 条")
        if self.issues:
            lines.append(f"[eval_samples_validate] 非法样本: {len(self.issues)} 条")
            for it in self.issues[:50]:
                lines.append(f"  ✗ [{it.category}] {it.sample_id}: {it.message}")
        if self.manifest_issues:
            lines.append(f"[eval_samples_validate] manifest 问题: {len(self.manifest_issues)} 条")
            for m in self.manifest_issues[:20]:
                lines.append(f"  ✗ {m}")
        return "\n".join(lines)


# ════════════════════════════════════════════════════════════
#  逐条样本校验
# ════════════════════════════════════════════════════════════


def _check_expected_output(expected: Any) -> Optional[str]:
    """校验 expected_output 结构；合法返回 None，非法返回原因"""
    if expected is None:
        return None
    if not isinstance(expected, dict):
        return "expected_output 必须是对象"
    etype = expected.get("type")
    if etype not in KNOWN_CHECKER_TYPES:
        return f"expected_output.type 非法: {etype!r}（允许 {KNOWN_CHECKER_TYPES}）"
    if etype == "exact" and "value" not in expected:
        return "exact 校验器缺少 value"
    if etype == "contains" and not expected.get("values"):
        return "contains 校验器缺少 values"
    if etype == "json" and ("key" not in expected or "value" not in expected):
        return "json 校验器缺少 key/value"
    if etype == "validator" and not expected.get("expression"):
        return "validator 校验器缺少 expression"
    return None


def _check_sample(item: Any, default_category: str, seen_ids: Dict[str, str],
                  seen_hashes: Dict[str, str]) -> Optional[SampleIssue]:
    """校验单条样本；返回首个非法项（None=合法）"""
    if not isinstance(item, dict):
        return SampleIssue(sample_id="?", category=default_category,
                           message="样本条目必须是对象")
    sid = item.get("id") or item.get("sample_id")
    if not sid or not isinstance(sid, str):
        return SampleIssue(sample_id=str(sid), category=default_category,
                           message="缺少 id 或 id 非字符串")
    sid = str(sid)
    category = item.get("category") or default_category
    if category != default_category:
        return SampleIssue(sample_id=sid, category=str(category),
                           message=f"category {category!r} 与目录 {default_category!r} 不一致")
    if category not in KNOWN_CATEGORIES:
        return SampleIssue(sample_id=sid, category=category,
                           message=f"未知类别 {category!r}")
    if sid in seen_ids:
        return SampleIssue(sample_id=sid, category=category,
                           message=f"id 重复（已见 {seen_ids[sid]}）")
    task = item.get("task")
    if not task or not isinstance(task, str):
        return SampleIssue(sample_id=sid, category=category,
                           message="缺少 task 或 task 非字符串")
    if item.get("created_at") is not None and not isinstance(item["created_at"], str):
        return SampleIssue(sample_id=sid, category=category,
                           message="created_at 必须为字符串")
    if item.get("created_at") == "":
        return SampleIssue(sample_id=sid, category=category,
                           message="created_at 为空")
    eo_err = _check_expected_output(item.get("expected_output"))
    if eo_err:
        return SampleIssue(sample_id=sid, category=category, message=eo_err)

    # metadata
    meta = item.get("metadata")
    if not isinstance(meta, dict):
        return SampleIssue(sample_id=sid, category=category,
                           message="缺少 metadata 或 metadata 非对象")
    if "input" not in meta or not isinstance(meta.get("input"), dict):
        return SampleIssue(sample_id=sid, category=category,
                           message="metadata.input 必须为对象")
    diff = meta.get("difficulty")
    if diff not in KNOWN_DIFFICULTIES:
        return SampleIssue(sample_id=sid, category=category,
                           message=f"difficulty 非法: {diff!r}（允许 {KNOWN_DIFFICULTIES}）")
    source = meta.get("source")
    if source not in KNOWN_SOURCES:
        return SampleIssue(sample_id=sid, category=category,
                           message=f"source 非法: {source!r}（允许 {KNOWN_SOURCES}）")
    ih = meta.get("input_hash")
    if not _is_valid_hash(ih):
        return SampleIssue(sample_id=sid, category=category,
                           message=f"input_hash 非法: {ih!r}（须 16 位 hex）")
    expected_hash = compute_input_hash(category, task, meta.get("input"))
    if ih.lower() != expected_hash:
        return SampleIssue(sample_id=sid, category=category,
                           message=f"input_hash 不匹配（期望 {expected_hash}）")
    if ih.lower() in seen_hashes:
        return SampleIssue(sample_id=sid, category=category,
                           message=f"input_hash 重复（与 {seen_hashes[ih.lower()]} 冲突，跨类别去重）")
    seen_ids[sid] = category
    seen_hashes[ih.lower()] = sid
    return None


# ════════════════════════════════════════════════════════════
#  manifest 校验
# ════════════════════════════════════════════════════════════


def _validate_manifest(manifest_path: Path, pool_ids: Dict[str, List[str]],
                       category_map: Dict[str, str]) -> List[str]:
    """校验 manifest 一致性；返回问题列表（空=通过）"""
    issues: List[str] = []
    if not manifest_path.exists():
        issues.append(f"manifest 缺失: {manifest_path}（门禁无法解析样本集版本）")
        return issues
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        issues.append(f"manifest 读取失败: {e}")
        return issues
    if not isinstance(manifest, dict) or "versions" not in manifest:
        issues.append("manifest 缺少 versions 字段")
        return issues
    current = manifest.get("current")
    versions = manifest.get("versions")
    if not isinstance(versions, dict) or current not in versions:
        issues.append(f"manifest current {current!r} 未在 versions 中登记")
        return issues
    all_ids = {sid for ids in pool_ids.values() for sid in ids}
    for version, spec in versions.items():
        if not isinstance(spec, dict) or "categories" not in spec:
            issues.append(f"版本 {version} 缺少 categories 字段")
            continue
        cats = spec["categories"]
        if not isinstance(cats, dict):
            issues.append(f"版本 {version} categories 非对象")
            continue
        for cat, ids in cats.items():
            if cat not in KNOWN_CATEGORIES:
                issues.append(f"版本 {version} 登记了未知类别 {cat!r}")
                continue
            pool_cat_ids = set(pool_ids.get(cat, []))
            for sid in ids or []:
                if sid not in pool_cat_ids:
                    issues.append(f"版本 {version}/{cat} 登记的 {sid!r} 不在池中")
                elif category_map.get(sid) != cat:
                    issues.append(f"版本 {version}/{cat} 的 {sid!r} 类别错配")
    # 反向：current 版本必须登记池中全部样本
    if current:
        cur_cats = versions[current].get("categories", {}) if isinstance(
            versions[current], dict) else {}
        registered = {sid for ids in (cur_cats or {}).values() for sid in (ids or [])}
        for sid in sorted(all_ids - registered):
            issues.append(f"池中样本 {sid!r} 未登记到 current 版本 {current}")
    return issues


# ════════════════════════════════════════════════════════════
#  入口
# ════════════════════════════════════════════════════════════


def validate_samples(base_dir: str) -> ValidationReport:
    """校验评估集目录；返回 ValidationReport（ok=True 表示 0 非法）

    兼容 EvalSamplePool 加载语义：目录下所有 .json 合并；坏文件按整文件跳过并记录问题。
    """
    base = Path(base_dir)
    report = ValidationReport(base_dir=str(base))
    seen_ids: Dict[str, str] = {}
    seen_hashes: Dict[str, str] = {}
    pool_ids: Dict[str, List[str]] = {}
    category_map: Dict[str, str] = {}

    if not base.is_dir():
        report.issues.append(SampleIssue("?", "-", f"目录不存在: {base}"))
        return report

    for cat_dir in sorted(p for p in base.iterdir()
                          if p.is_dir() and p.name not in EXCLUDED_DIRS):
        cat = cat_dir.name
        if cat not in KNOWN_CATEGORIES:
            report.issues.append(SampleIssue("?", cat, f"目录 {cat!r} 非已知类别"))
            continue
        for json_file in sorted(cat_dir.glob("*.json")):
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    raw = json.load(f)
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
                report.issues.append(SampleIssue("?", cat,
                                                 f"文件加载失败 {json_file.name}: {e}"))
                continue
            items = raw if isinstance(raw, list) else list(raw.values())
            for item in items:
                report.total += 1
                issue = _check_sample(item, cat, seen_ids, seen_hashes)
                if issue is not None:
                    report.issues.append(issue)
                    continue
                sid = str(item.get("id"))
                report.per_category[cat] = report.per_category.get(cat, 0) + 1
                pool_ids.setdefault(cat, []).append(sid)
                category_map[sid] = cat

    report.manifest_issues = _validate_manifest(
        base / "manifest.json", pool_ids, category_map)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="评估集样本校验（data/evals schema v1）")
    parser.add_argument("--dir", default=str(_ROOT / "data" / "evals"),
                        help="评估集根目录（默认 data/evals）")
    parser.add_argument("--quiet", action="store_true", help="仅输出摘要与退出码")
    args = parser.parse_args()

    report = validate_samples(args.dir)
    print(report.summary())
    print(f"[eval_samples_validate] 结果: {'PASS（0 非法）' if report.ok else 'FAIL'}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
