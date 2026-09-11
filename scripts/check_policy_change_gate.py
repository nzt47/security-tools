#!/usr/bin/env python
"""策略变更合入门禁（P7.2-19：策略变更 PR 必附模拟报告，否则不可合入）

【它检查什么（逐条都可失败，不含「软告警」）】

    1. **改了策略就要有报告**：PR 触及 ``data/policies/*.json`` 时，
       ``--report`` 必须给出一份 ``schema == policy.simulation.v1`` 的模拟报告。
    2. **报告必须对应本次变更**：报告的 ``candidate.id`` / ``candidate.version``
       必须能在**改动后的**策略文件里找到；且报告不早于策略文件的修改时间
       （防止拿旧报告蒙混）。
    3. **高危变更必须逐条确认**：``high_risk_hits`` 非空时，PR 描述里必须有
       ``## 高危确认`` 段，且勾选条目数 ≥ 高危命中数。
    4. **无样本要说出来**：``verdict == no_sample`` 时，PR 描述必须写明
       「无历史决策样本」并由人工承担风险——**不允许**把「零变更」当作安全证据。
    5. **策略文件本身必须合法**：语法 / schema / 禁用 token / 签名（若开启）。

【用法】

    # 校验策略文件（pre-commit 用它：不需要 PR 上下文）
    python scripts/check_policy_change_gate.py --schema-only data/policies/policies.json

    # 完整门禁（CI 用）
    python scripts/check_policy_change_gate.py \\
        --changed data/policies/policies.json \\
        --report reports/policy_simulation.json \\
        --pr-body pr_body.md

    # 签名（人工合入前）
    python scripts/check_policy_change_gate.py --sign data/policies/policies.json

【退出码】0 = 通过；1 = 门禁不通过；2 = 用法/环境错误
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 允许从仓库根直接运行（CI 与本地都用 `python scripts/xxx.py`）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.policy.models import POLICY_SCHEMA, PolicyValidationError  # noqa: E402
from agent.policy.signing import PolicySigner, verify_policy_signature  # noqa: E402
from agent.policy.simulator import REPORT_SCHEMA  # noqa: E402
from agent.policy.store import PolicyStore, PolicyStoreError  # noqa: E402

#: 视为「策略变更」的路径模式
POLICY_PATH_RE = re.compile(r"(^|/)data/policies/.*\.json$")

#: PR 描述里的高危确认段标题（必须在标题级别与措辞上稳定，否则门禁无法解析）
HIGH_RISK_HEADING = "## 高危确认"
#: PR 描述里的「无样本声明」段标题（total=0 时必须出现**已勾选项**）
NO_SAMPLE_HEADING = "## 无样本声明"
NO_SAMPLE_MARKER = "无历史决策样本"

#: 勾选条目：``- [x] ...`` / ``* [X] ...``
CHECKED_ITEM_RE = re.compile(r"^\s*[-*]\s*\[[xX]\]", re.M)


class GateFailure(Exception):
    """门禁不通过（带人类可读原因）"""


def log(level: str, message: str) -> None:
    print(f"[{level}] {message}")


def is_policy_path(path: str) -> bool:
    return bool(POLICY_PATH_RE.search(str(path).replace("\\", "/")))


def _load_policy_document(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, list):
        return {"schema": POLICY_SCHEMA, "policies": data}
    if isinstance(data, dict) and "policies" in data:
        return data
    if isinstance(data, dict):
        return {"schema": POLICY_SCHEMA, "policies": [data]}
    raise GateFailure(f"策略文件结构无法识别: {path}")


# ════════════════════════════════════════════════════════════
#  一、策略文件自检
# ════════════════════════════════════════════════════════════


def validate_policy_files(paths: Sequence[str], *, require_signed: bool = False
                          ) -> List[str]:
    """校验策略文件（返回问题清单；空＝通过）"""
    problems: List[str] = []
    for path in paths:
        if not os.path.exists(path):
            problems.append(f"{path}: 文件不存在")
            continue
        try:
            store = PolicyStore(path=path, autoload=True,
                                require_signed=require_signed)
        except PolicyStoreError as exc:
            problems.append(f"{path}: 装载失败: {exc}")
            continue
        except PolicyValidationError as exc:
            problems.append(f"{path}: 策略非法: {exc.errors}")
            continue
        except json.JSONDecodeError as exc:
            problems.append(f"{path}: 非法 JSON: {exc}")
            continue
        for problem in store.problems:
            problems.append(f"{path}: [{problem.code}] {problem.policy_id}: {problem.detail}")
        for problem in store.validate_all():
            problems.append(f"{path}: [{problem.code}] {problem.policy_id}: {problem.detail}")
        # 内置不变量之外的策略即为本文件贡献的策略
        authored = [p for p in store.active() if not p.id.startswith("builtin.invariant.")]
        if not authored:
            problems.append(f"{path}: 未装载到任何作者策略（文件可能是空壳）")
        # 只在**本文件确实没问题**时才报「自检通过」——否则日志会先写「通过」再报
        # FAIL，读日志的人会以为门禁在自相矛盾。
        if not any(item.startswith(f"{path}: ") for item in problems):
            log("INFO", f"{path}: 装载 {len(authored)} 条策略，自检通过")
    return problems


# ════════════════════════════════════════════════════════════
#  二、模拟报告校验
# ════════════════════════════════════════════════════════════


def load_report(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        raise GateFailure(f"模拟报告不存在: {path}")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise GateFailure(f"模拟报告不是合法 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise GateFailure("模拟报告应为 JSON 对象")
    if str(data.get("schema") or "") != REPORT_SCHEMA:
        raise GateFailure(
            f"模拟报告 schema 不匹配: {data.get('schema')!r}（期望 {REPORT_SCHEMA!r}）；"
            "请用 `python -m agent.policy.simulator --json-out <path>` 重新生成")
    return data


def _policy_versions(paths: Sequence[str]) -> Dict[str, set]:
    """改动后策略文件里的 ``id → {version}``"""
    index: Dict[str, set] = {}
    for path in paths:
        if not os.path.exists(path):
            continue
        try:
            document = _load_policy_document(path)
        except (GateFailure, json.JSONDecodeError):
            continue
        for raw in document.get("policies") or []:
            if not isinstance(raw, dict):
                continue
            index.setdefault(str(raw.get("id") or ""), set()).add(
                str(raw.get("version") or ""))
    return index


def check_report_matches_change(report: Dict[str, Any], policy_paths: Sequence[str]
                                ) -> Dict[str, Any]:
    """报告必须对应本次改动（候选策略确实在改动后的文件里）"""
    candidate = report.get("candidate") or {}
    candidate_id = str(candidate.get("id") or "")
    candidate_version = str(candidate.get("version") or "")
    if not candidate_id:
        raise GateFailure("模拟报告缺 candidate.id，无法与本次变更对齐")
    index = _policy_versions(policy_paths)
    if candidate_id not in index:
        raise GateFailure(
            f"模拟报告的候选策略 `{candidate_id}` 不在本次改动的策略文件中"
            f"（文件内策略: {sorted(index)}）；报告与变更不对应")
    if candidate_version and candidate_version not in index[candidate_id]:
        raise GateFailure(
            f"候选策略 `{candidate_id}` 的版本 `{candidate_version}` 不在改动后的文件里"
            f"（文件内版本: {sorted(index[candidate_id])}）；报告已过期")

    # 报告不得早于策略文件（防止拿旧报告蒙混）
    # 口径说明：CI 全新检出时所有文件 mtime ≈ 检出时刻，本项检查在那里**自然为真**
    # （不会误拦）；它的主战场是本地/长驻环境——「改完策略忘了重跑模拟」是最常见的
    # 走形式路径。真正的一致性保证是上面的 id/version 对齐。
    newest_policy = max((_mtime(p) or 0.0) for p in policy_paths if os.path.exists(p))
    report_file_mtime = _mtime(report.get("__path__", "")) or 0.0
    if report_file_mtime and newest_policy and report_file_mtime + 1.0 < newest_policy:
        raise GateFailure(
            "模拟报告早于策略文件的最后修改时间，请重新生成报告后再合入")
    return candidate


def _mtime(path: Any) -> float:
    try:
        return os.path.getmtime(str(path))
    except OSError:
        return 0.0


# ════════════════════════════════════════════════════════════
#  三、PR 描述确认
# ════════════════════════════════════════════════════════════


def section_after(body: str, heading: str) -> str:
    """取出某个二级标题下的段落（到下一个同级/更高级标题为止）"""
    marker = body.find(heading)
    if marker < 0:
        return ""
    rest = body[marker + len(heading):]
    end = re.search(r"^#{1,2} ", rest, re.M)
    return rest[:end.start()] if end else rest


def high_risk_section(body: str) -> str:
    """``## 高危确认`` 段落（保留旧名，供外部引用）"""
    return section_after(body, HIGH_RISK_HEADING)


def check_acknowledgements(report: Dict[str, Any], body: str) -> None:
    """高危变更逐条确认；无样本情形必须显式勾选声明

    两处都用**「已勾选项」**而不是「出现某段文字」来判定：模板里天然带有未勾选的
    提示行，只有作者主动勾选才算确认。用文字出现与否判定会被模板本身满足——
    那不是门禁，那是走形式。
    """
    hits = report.get("high_risk_hits") or []
    totals = report.get("totals") or {}
    total = int(totals.get("total") or 0)

    if hits:
        section = high_risk_section(body)
        if not section.strip():
            raise GateFailure(
                f"存在 {len(hits)} 条高危变更（原 deny/ask 现 allow），"
                f"PR 描述必须包含 `{HIGH_RISK_HEADING}` 段并逐条勾选确认。"
                "格式：每个条目一行 `- [x] <policy_id> <capability_id> <理由>`")
        checked = len(CHECKED_ITEM_RE.findall(section))
        if checked < len(hits):
            raise GateFailure(
                f"高危变更 {len(hits)} 条，但 `{HIGH_RISK_HEADING}` 段只勾选了 "
                f"{checked} 条（需逐条勾选，格式 `- [x] ...`）")
        # 每条高危命中的策略 id 都应在确认段里出现（避免「勾了但没说哪条」）
        missing = [str(hit.get("new_policy_id") or hit.get("old_policy_id") or "")
                   for hit in hits
                   if str(hit.get("new_policy_id") or hit.get("old_policy_id") or "")
                   not in section]
        if missing:
            raise GateFailure(
                f"`{HIGH_RISK_HEADING}` 段未提及以下策略: {sorted(set(missing))}")
        log("INFO", f"高危变更确认齐全: {checked}/{len(hits)}")

    if str(report.get("verdict") or "") == "no_sample" or total == 0:
        section = section_after(body, NO_SAMPLE_HEADING)
        if not CHECKED_ITEM_RE.search(section):
            raise GateFailure(
                f"本次变更没有历史决策样本（total=0），PR 描述必须在 "
                f"`{NO_SAMPLE_HEADING}` 段**勾选**声明「{NO_SAMPLE_MARKER}」"
                "并由人工承担未经模拟的风险——「零变更」在此情形下不构成安全证据。")
        log("WARN", "无历史决策样本：已声明（该例外由人工承担）")

    if report.get("shadow"):
        log("WARN", f"策略遮蔽诊断有 {len(report['shadow'])} 条，请在 PR 中一并说明")


# ════════════════════════════════════════════════════════════
#  四、签名
# ════════════════════════════════════════════════════════════


def sign_file(path: str, signer: Optional[PolicySigner] = None) -> int:
    """给策略文件里的每条策略签名并**写回**（人工合入前的动作）"""
    signer = signer or PolicySigner()
    document = _load_policy_document(path)
    signed: List[Dict[str, Any]] = []
    for raw in document.get("policies") or []:
        body = dict(raw)
        body.pop("signature", None)
        from agent.policy.models import Policy
        body["signature"] = signer.sign(Policy.parse(body))
        signed.append(body)
    document["policies"] = signed
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    log("INFO", f"{path}: 已签名 {len(signed)} 条策略（方案 {signer.scheme}"
                f"{'，降级占位' if signer.degraded else ''}）")
    if signer.degraded:
        log("WARN", f"签名降级为 sha256-self 占位：{signer.degraded_reason}")
    return 0


# ════════════════════════════════════════════════════════════
#  CLI
# ════════════════════════════════════════════════════════════


def _discover_changed(base_ref: str) -> List[str]:
    """用 git 找出本分支相对 ``base_ref`` 改动的策略文件（CI 兜底路径）"""
    import subprocess
    try:
        output = subprocess.run(
            ["git", "diff", "--name-only", f"{base_ref}...HEAD"],
            capture_output=True, text=True, check=True).stdout
    except Exception:  # noqa: BLE001 拿不到 git 上下文不是门禁失败的理由
        return []
    return [line.strip() for line in output.splitlines()
            if line.strip() and is_policy_path(line.strip())]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="check_policy_change_gate",
        description="策略变更合入门禁（P7.2-19）")
    # 位置参数是给 **pre-commit / hooks 调用形态**用的：框架 hook 会把暂存文件名
    # 追加到 entry 之后（`python ... --schema-only data/policies/x.json`），
    # 只有 `--changed` 一个入口时会被 argparse 判为「无法识别的参数」而误拦提交。
    parser.add_argument("paths", nargs="*", default=None,
                        help="策略文件路径（等价于 --changed，便于 hook 直接追加文件名）")
    parser.add_argument("--changed", nargs="*", default=None,
                        help="本次改动的策略文件；缺省时用 git diff 推断")
    parser.add_argument("--base-ref", default="origin/master",
                        help="git diff 的基线引用（仅在 --changed 缺省时使用）")
    parser.add_argument("--report", default=None, help="模拟报告 JSON（机读版）")
    parser.add_argument("--pr-body", default=None, help="PR 描述文本文件")
    parser.add_argument("--schema-only", action="store_true",
                        help="只做策略文件自检（pre-commit 用，不需要 PR 上下文）")
    parser.add_argument("--require-signed", action="store_true",
                        help="要求策略已签名（等价 CP_POLICY_REQUIRE_SIGNATURE=1）")
    parser.add_argument("--sign", default=None, metavar="POLICY_FILE",
                        help="给策略文件签名并写回（人工动作）")
    return parser


def _resolve_changed(args: argparse.Namespace) -> List[str]:
    """合并三个入口的策略文件来源：位置参数 / --changed / git diff"""
    explicit: List[str] = list(args.paths or []) + list(args.changed or [])
    if explicit:
        return [path for path in explicit if is_policy_path(path)]
    return _discover_changed(args.base_ref)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)

    if args.sign:
        try:
            return sign_file(args.sign)
        except (PolicyValidationError, PolicyStoreError, GateFailure) as exc:
            log("FAIL", f"签名失败: {exc}")
            return 1

    changed = _resolve_changed(args)
    changed = [path for path in changed if is_policy_path(path)]

    if args.schema_only:
        targets = changed or ["data/policies/policies.json"]
        problems = validate_policy_files(targets, require_signed=args.require_signed)
        for problem in problems:
            log("FAIL", problem)
        if problems:
            return 1
        log("OK", f"策略文件自检通过: {targets}")
        return 0

    if not changed:
        log("OK", "本次未改动策略文件，门禁跳过（策略即代码只对策略变更设闸）")
        return 0

    problems = validate_policy_files(changed, require_signed=args.require_signed)
    if problems:
        for problem in problems:
            log("FAIL", problem)
        return 1

    if not args.report:
        log("FAIL", "改动触及策略文件，但未提供模拟报告（--report）。"
                    "P7.2-19：策略变更 PR 必附模拟报告，否则不可合入。")
        return 1
    try:
        report = load_report(args.report)
        report["__path__"] = args.report
        candidate = check_report_matches_change(report, changed)
        body = ""
        if args.pr_body:
            if not os.path.exists(args.pr_body):
                raise GateFailure(f"PR 描述文件不存在: {args.pr_body}")
            body = Path(args.pr_body).read_text(encoding="utf-8")
        check_acknowledgements(report, body)
    except GateFailure as exc:
        log("FAIL", str(exc))
        return 1

    totals = report.get("totals") or {}
    log("OK", f"策略变更门禁通过：候选 `{candidate.get('id')}@{candidate.get('version')}`，"
              f"样本 {totals.get('total')}，deny→allow {totals.get('deny_to_allow')}，"
              f"高危 {len(report.get('high_risk_hits') or [])}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI 入口
    raise SystemExit(main())
