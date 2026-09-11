"""L0 锚冻结与校验（TASK-S5-02）——**唯一**被许可写入锚目录的入口

用法::

    # 1) 校验（默认行为，只读；不改任何文件）
    python scripts/freeze_eval_anchor.py --verify-only
    python scripts/freeze_eval_anchor.py --verify-only --json

    # 2) 人工冻结（必须署名 + 显式 --confirm-freeze）
    python scripts/freeze_eval_anchor.py --confirm-freeze \
        --reviewer Owner --note "L0 锚首次冻结：20 条，6 类场景各 ≥2"

    # 3) 冻结后写入 release manifest（把锚哈希锚定入发布清单）
    python scripts/freeze_eval_anchor.py --write-release-manifest

    # 4) 发布包硬化（可选，默认不做：仓库工作区需要 git 可写）
    python scripts/freeze_eval_anchor.py --mark-readonly

**为什么写入必须经本脚本**：`agent.eval.anchor.freeze_anchor()` 需要显式传入
``allow_write=True`` 与人工署名 ``frozen_by``，而 ``allow_write`` 只由本脚本的
``--confirm-freeze`` 分支提供。自动化流程（消化流水线 / shadow / 自愈 / 评测执行器）
调用的高层 API 都不传该参数，因此它们**在结构上**无法改写锚。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.eval import anchor as A  # noqa: E402
from agent.eval import cases as C  # noqa: E402
from agent.eval import runner as R  # noqa: E402

#: 发布清单落点（把锚用例集哈希"锚定入 release manifest"）
RELEASE_MANIFEST = os.path.join(A._REPO_ROOT, "release", "eval_anchor_manifest.json")

#: 其他层的用例集（一并登记哈希：一次发布即锁定全套评测数据）
DATASET_LAYERS = (C.LAYER_L0, C.LAYER_L1, C.LAYER_L2, C.LAYER_L3)


def _dataset_path(layer: str, anchor_dir: str) -> str:
    if layer == C.LAYER_L0:
        return os.path.join(anchor_dir, A.CASES_FILENAME)
    return R.LAYER_CASESET_PATHS.get(layer, "")


def collect_layer_hashes(anchor_dir: str = "") -> dict:
    """汇总各层用例集哈希与条数（缺失层如实标注 missing，不伪造）"""
    root = A.resolve_anchor_dir(anchor_dir)
    out: dict = {}
    for layer in DATASET_LAYERS:
        path = _dataset_path(layer, root)
        entry = {"path": os.path.relpath(path, A._REPO_ROOT) if path else "",
                 "exists": bool(path) and os.path.exists(path)}
        if entry["exists"]:
            try:
                case_set = C.load_case_set(path, validate=False)
                entry.update({"count": len(case_set),
                              "caseset_sha256": case_set.caseset_sha256,
                              "scenario_counts": case_set.scenario_counts(),
                              "verdict_counts": case_set.verdict_counts()})
            except C.CaseError as e:
                entry.update({"error": str(e)})
        out[layer] = entry
    return out


def write_release_manifest(anchor_dir: str = "", path: str = "") -> dict:
    """写出 `release/eval_anchor_manifest.json`（锚哈希 + 全层数据哈希 + 工具版本）"""
    root = A.resolve_anchor_dir(anchor_dir)
    manifest = A.load_manifest(os.path.join(root, A.MANIFEST_FILENAME))
    payload = {
        "schema": "eval.release_anchor.v1",
        "anchor_dir": os.path.relpath(root, A._REPO_ROOT),
        "anchor_caseset_sha256": manifest.caseset_sha256,
        "anchor_manifest_sha256": A.text_sha256(os.path.join(root, A.MANIFEST_FILENAME)),
        "anchor_count": manifest.count,
        "anchor_frozen_at": manifest.frozen_at,
        "anchor_frozen_by": manifest.frozen_by,
        "reference_sha256": manifest.reference_sha256,
        "freeze_tool_version": manifest.tool_version,
        "layers": collect_layer_hashes(root),
        "note": ("L0 锚哈希锚定入发布清单：发布制品与评测数据一一对应；"
                 "锚为系统不可写，任何改动都会使本清单失效（fail-closed）"),
    }
    target = path or RELEASE_MANIFEST
    parent = os.path.dirname(os.path.abspath(target))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="L0 锚冻结与校验（TASK-S5-02）")
    parser.add_argument("--anchor-dir", default="", help="锚目录（默认 eval/l0_anchor）")
    parser.add_argument("--cases", default="", help="待冻结用例集（默认 <anchor-dir>/cases.json）")
    parser.add_argument("--reference", default="", help="参考解（默认 <anchor-dir>/reference.json）")
    parser.add_argument("--reviewer", default="", help="冻结人（人工署名，必填）")
    parser.add_argument("--note", default="", help="冻结说明")
    parser.add_argument("--confirm-freeze", action="store_true",
                        help="显式确认写入（唯一提供写能力的开关）")
    parser.add_argument("--verify-only", action="store_true", help="只校验（默认行为）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出校验报告")
    parser.add_argument("--write-release-manifest", action="store_true",
                        help="写出 release/eval_anchor_manifest.json")
    parser.add_argument("--mark-readonly", action="store_true",
                        help="施加 OS 级只读属性（发布包硬化；仓库工作区不建议）")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    root = A.resolve_anchor_dir(args.anchor_dir)

    if args.confirm_freeze:
        cases_path = args.cases or os.path.join(root, A.CASES_FILENAME)
        reference_path = args.reference or os.path.join(root, A.REFERENCE_FILENAME)
        if not os.path.exists(cases_path) or not os.path.exists(reference_path):
            print(f"[FAIL] 待冻结材料缺失: cases={cases_path} reference={reference_path}")
            return 2
        case_set = C.load_case_set(cases_path, validate=False)
        with open(reference_path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        answers = raw.get("answers") if isinstance(raw.get("answers"), dict) else raw
        manifest = A.freeze_anchor(case_set=case_set, reference=answers,
                                   frozen_by=args.reviewer, review_note=args.note,
                                   root=root, allow_write=True)
        print("[OK] 已冻结 L0 锚")
        print(json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2))
        if args.write_release_manifest:
            payload = write_release_manifest(root)
            print(f"[OK] 已写出 release manifest: {RELEASE_MANIFEST}"
                  f"（锚哈希 {payload['anchor_caseset_sha256'][:12]}）")
        return 0

    report = A.verify_anchor(root)
    if args.write_release_manifest:
        if not report["ok"]:
            print("[FAIL] 锚校验未通过，拒绝写出 release manifest")
            return 1
        payload = write_release_manifest(root)
        print(f"[OK] 已写出 release manifest: {RELEASE_MANIFEST}")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.mark_readonly:
        print(json.dumps(A.set_os_readonly(root, enable=True), ensure_ascii=False, indent=2))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"锚目录: {report['anchor_dir']}")
        print(f"条数: {report['count']}（manifest={report['manifest_count']}）")
        print(f"用例集哈希: {report['caseset_sha256']}")
        print(f"manifest 哈希: {report['manifest_caseset_sha256']}")
        print(f"参考解: exists={report['reference'].get('exists')} "
              f"ok={report['reference'].get('ok')}")
        print(f"位置独立: {report['independent'].get('independent')}")
        print(f"结论: {'OK（系统不可写校验通过）' if report['ok'] else 'FAIL'}")
        for problem in report.get("problems", []):
            print(f"  - {problem}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
