# -*- coding: utf-8 -*-
"""ui_guard 规则引擎：fast 模式（纯 Python）与 full 模式（可选走 npm/vitest）。"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from agent.ui_guard.models import Finding, Report
from agent.utils import proc as _proc

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_SRC = REPO_ROOT / "yunshu-ui" / "src"

#: 规则名（对外稳定；前端按它分组）
RULE_BUTTON = "button.alignment"
RULE_HEADING = "heading.scale"
RULE_TABS = "tabs.order"
RULE_RAW = "audit.raw_control"
RULE_ARBITRARY_TEXT = "audit.arbitrary_text"
RULE_RAW_HEADING = "audit.raw_heading"
RULE_CSS_COLOR = "audit.css_hardcoded_color"

FIXABLE_RULES = (RULE_BUTTON, RULE_HEADING)

#: 页面里的标题标记（与 pageHeaderOrder.guard.test.ts 同一判据）
_TITLE_MARKERS = ("<PageTabsHeader", "<PageHeader", "<h1", "<h2", "<SectionTitle")

#: 非叶子页、由父页提供标题的子面板（与守卫的 allowlist 一致）
_TABS_ALLOW = {
    "pages/hub/memory/skill-detail-drawer.tsx",
    "pages/hub/panorama/log-center-panel.tsx",
    "pages/hub/panorama/ops-diagnostics.tsx",
}

_TAG_BUTTON = re.compile(r"<Button\b[^>]*>")
_TAG_HEADING = re.compile(r"<h[1-6]\b[^>]*>")
_CLASS_ATTR = re.compile(r"""className=(?:"([^"]*)"|'([^']*)')""")

_AUDIT_CACHE: Dict[str, Any] = {}
_LAST_REPORT: Optional[Report] = None
_LAST_AT: float = 0.0
_LAST_LOCK = threading.RLock()
_HEALTH_TTL_SEC = 30.0


def _audit_module():
    """加载量具模块（复用它的 METRICS / CSS 正则 / 基线读取，不抄第二份）。

    按**包路径**导入（scripts.audit 是命名空间包）；不用 spec_from_file_location ——
    detect_dynamic_loads 把「无包路径的外部脚本加载」判 HIGH。
    """
    if "mod" in _AUDIT_CACHE:
        return _AUDIT_CACHE["mod"]
    from scripts.audit import ui_primitive_convergence as mod

    _AUDIT_CACHE["mod"] = mod
    return mod


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, max(0, index)) + 1


def _rel(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:  # pragma: no cover - 正常都在 root 下
        return path.as_posix()


def _ui_files(root: Path) -> Iterable[Path]:
    ui = root / "yunshu-ui" / "src"
    ui_dir = ui / "components" / "ui"
    for p in ui.rglob("*"):
        if p.suffix in (".tsx", ".ts") and ui_dir not in p.parents:
            yield p


def _class_of(tag: str) -> str:
    m = _CLASS_ATTR.search(tag)
    if not m:
        return ""
    return m.group(1) if m.group(1) is not None else (m.group(2) or "")


def _check_button(text: str, rel: str, out: List[Finding]) -> None:
    for m in _TAG_BUTTON.finditer(text):
        tag = m.group(0)
        cls = _class_of(tag)
        if not cls:
            continue
        line = _line_of(text, m.start())
        wide = ("w-full" in cls) or ("flex-1" in cls)
        has_justify = "justify-" in cls
        if wide and not has_justify:
            out.append(Finding(
                rule=RULE_BUTTON, severity="error", file=rel, line=line,
                message="满宽 Button 未显式声明对齐（基类是 justify-center，会被当行用）",
                fix_hint="在 className 加 justify-start（行/列表）或 justify-center（动作）",
                snippet=tag[:160], fixable=True))
        elif "text-left" in cls and "justify-start" not in cls:
            out.append(Finding(
                rule=RULE_BUTTON, severity="error", file=rel, line=line,
                message="text-left 只作用于行内文本，管不到 flex 子项",
                fix_hint="补 justify-start（或去掉 text-left）",
                snippet=tag[:160], fixable=True))


def _check_heading(text: str, rel: str, out: List[Finding]) -> None:
    for m in _TAG_HEADING.finditer(text):
        tag = m.group(0)
        if "text-" in tag:
            continue
        level = tag[2]
        token = {"1": "text-title", "2": "text-ui"}.get(level, "text-body")
        literal = _CLASS_ATTR.search(tag) is not None or "className=" not in tag
        out.append(Finding(
            rule=RULE_HEADING, severity="error", file=rel, line=_line_of(text, m.start()),
            message="标题字号没有写在标签上（由 CSS 类/父选择器决定，改阶梯时会漏）",
            fix_hint=(f"在标签 className 加 {token}" if literal
                      else "className 是表达式（cn/变量）：手动补 text-*"),
            snippet=tag[:160], fixable=literal))


def _check_tabs_order(root: Path, out: List[Finding]) -> None:
    pages = root / "yunshu-ui" / "src" / "pages"
    for p in pages.rglob("*.tsx"):
        rel_to_pages = p.relative_to(pages).as_posix()
        rel = "pages/" + rel_to_pages
        if rel in _TABS_ALLOW or ".test." in p.name:
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if "export default" not in text or "<Tabs" not in text:
            continue
        tabs_at = text.index("<Tabs")
        marks = [text.index(m) for m in _TITLE_MARKERS if m in text]
        if not any(i < tabs_at for i in marks):
            out.append(Finding(
                rule=RULE_TABS, severity="error", file=rel,
                line=_line_of(text, tabs_at),
                message="<Tabs> 出现在任何标题之前（标题会落到 Tab 下面）",
                fix_hint="用 PageTabsHeader（标题恒在 Tab 上面）",
                snippet="<Tabs"))


def _audit_findings(root: Path, out: List[Finding]) -> Dict[str, int]:
    """逐文件量具（file:line 可定位）；返回各指标总数（与基线比较用）。"""
    mod = _audit_module()
    metrics = {k: v for k, v in mod.METRICS.items() if v}
    counts = {k: 0 for k in mod.METRICS}
    ui = root / "yunshu-ui" / "src"
    ui_dir = ui / "components" / "ui"
    for p in ui.rglob("*"):
        if p.suffix not in mod.SCAN_SUFFIXES or ui_dir in p.parents:
            continue
        # 【2026-11-20】与 scripts/audit 同口径：.test. 是合成反例，不计入产品量具
        if ".test." in p.name:
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        rel = _rel(root, p)
        for key, pattern in metrics.items():
            hits = list(re.finditer(pattern, text))
            counts[key] += len(hits)
            for m in hits:
                if key == "raw_heading":
                    continue  # 由 heading.scale 逐标签检查，避免同一处报两遍
                rule = RULE_ARBITRARY_TEXT if key == "arbitrary_text" else RULE_RAW
                sev = "error" if key == "arbitrary_text" else "warning"
                out.append(Finding(
                    rule=rule, severity=sev, file=rel, line=_line_of(text, m.start()),
                    message={
                        "raw_button": "原生 <button>：改用 ui/Button",
                        "raw_select": "原生 <select>：改用 ui/Select",
                        "raw_input": "原生 <input>：改用 ui/Input",
                        "raw_textarea": "原生 <textarea>：改用 ui/Textarea",
                        "raw_range": "原生 range：改用 ui/Slider",
                        "arbitrary_text": "任意值字号：改用 9 档 token",
                    }.get(key, f"量具命中 {key}"),
                    fix_hint="见 docs/ui/封装规范.md 的选型表",
                    snippet=m.group(0)[:80]))
    for p in ui.rglob("*.css"):
        if p.name in mod._CSS_EXEMPT:
            continue
        # 自定义属性定义行是色板声明，不算"规则里的硬编码"（与 scripts/audit 同口径）
        text = mod._strip_css_var_defs(p.read_text(encoding="utf-8", errors="replace"))
        rel = _rel(root, p)
        found = []
        for pattern in mod._CSS_COLOR_PATTERNS:
            found.extend(re.finditer(pattern, text))
        counts["css_hardcoded_color"] += len(found)
        for m in found:
            out.append(Finding(
                rule=RULE_CSS_COLOR, severity="warning", file=rel,
                line=_line_of(text, m.start()),
                message="CSS 硬编码颜色：改用语义 token / 调色板类",
                fix_hint="检查 __color-* token 或 Tailwind 语义色",
                snippet=m.group(0)[:40]))
    # 计数以 scripts/audit 的单一实现为准，避免引擎与量具两份口径漂移
    canonical = mod.scan(ui)
    canonical["_files"] = len([1 for _ in _ui_files(root)])
    return canonical


def _commit(root: Path) -> str:
    try:
        p = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                           **_proc.hidden_kwargs(), cwd=str(root),
                           capture_output=True, text=True, timeout=5)
        return p.stdout.strip() if p.returncode == 0 else ""
    except Exception:  # noqa: BLE001 无 git 环境不应阻断检查
        return ""


def check(*, root: Optional[Path] = None, mode: str = "fast",
          rules: Optional[Sequence[str]] = None, limit: int = 500) -> Report:
    """执行一次检查（fast=纯 Python；full=额外跑 npm/vitest 作为对拍）。"""
    global _LAST_REPORT, _LAST_AT
    t0 = time.perf_counter()
    root = Path(root) if root else REPO_ROOT
    wanted = set(rules) if rules else None
    findings: List[Finding] = []
    errors: List[str] = []
    try:
        counts = _audit_findings(root, findings)
        for p in _ui_files(root):
            # 【与 vitest 守卫一致】测试文件里会贴"反面写法"（如 title markers 数组），
            # 不排除会把守卫自己的样例当成违规。
            if ".test." in p.name:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            rel = _rel(root, p)
            _check_button(text, rel, findings)
            _check_heading(text, rel, findings)
        _check_tabs_order(root, findings)
    except Exception as exc:  # noqa: BLE001 规则单条失败不阻断其余（如实报告）
        logger.warning("[ui_guard] 扫描失败: %s", exc, exc_info=True)
        errors.append(f"{type(exc).__name__}: {exc}")
        counts = {}

    if wanted:
        findings = [f for f in findings if f.rule in wanted]
    findings.sort(key=lambda f: (f.severity != "error", f.file, f.line, f.rule))

    baseline_mod = _audit_module()
    baseline = baseline_mod.load_baseline()
    current = {k: int(v) for k, v in counts.items() if not k.startswith("_")}
    regressions = []
    for k, was in baseline.items():
        now = int(current.get(k, 0))
        if now > int(was):
            regressions.append({"metric": k, "baseline": int(was), "current": now,
                                "delta": now - int(was)})
    if regressions:
        errors.append("量具升高：" + ", ".join(
            f"{r['metric']} {r['baseline']}→{r['current']}" for r in regressions))

    if mode == "full":
        for cmd, name in (
            ([sys.executable, "scripts/audit/ui_primitive_convergence.py", "--check"], "audit"),
            (["npx", "vitest", "run", "guard"], "vitest-guard"),
        ):
            try:
                p = subprocess.run(cmd, **_proc.hidden_kwargs(),
                                   cwd=str(root / "yunshu-ui" if name == "vitest-guard" else root),
                                   capture_output=True, text=True, timeout=180)
                if p.returncode != 0:
                    errors.append(f"full/{name} 退出码 {p.returncode}: {(p.stderr or p.stdout)[-300:]}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"full/{name} 不可用: {type(exc).__name__}: {exc}")

    by_rule: Dict[str, int] = {}
    for f in findings:
        by_rule[f.rule] = by_rule.get(f.rule, 0) + 1
    sev = {"error": 0, "warning": 0, "info": 0}
    for f in findings:
        sev[f.severity] = sev.get(f.severity, 0) + 1
    report = Report(
        mode=mode, generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        commit=_commit(root), findings=findings,
        counts={"total": len(findings), **sev}, by_rule=by_rule,
        scanned_files=int(counts.get("_files", 0)),
        baseline={"path": "data/ui_primitive_baseline.json", "current": current,
                  "regressions": regressions, "passed": not regressions},
        errors=errors, duration_ms=round((time.perf_counter() - t0) * 1000, 1))
    with _LAST_LOCK:
        _LAST_REPORT = report
        _LAST_AT = time.time()
    return report


def last_report() -> Optional[Report]:
    return _LAST_REPORT


def health() -> Dict[str, Any]:
    """状态源：拓扑采集用。30s 内复用上一次结果，避免每次轮询全量扫盘。"""
    global _LAST_REPORT, _LAST_AT
    with _LAST_LOCK:
        fresh = _LAST_REPORT is not None and (time.time() - _LAST_AT) < _HEALTH_TTL_SEC
    report = _LAST_REPORT if fresh else check(limit=0)
    regressions = report.baseline.get("regressions") or []
    overall = 1.0 if not regressions else (0.6 if report.counts.get("error", 0) < 20 else 0.3)
    return {
        "ok": not regressions,
        "module": "ui_guard",
        "version": "1.0.0",
        "overall": overall,
        "status": "healthy" if not regressions else "warning",
        "counts": dict(report.counts),
        "by_rule": dict(report.by_rule),
        "baseline_regressions": regressions,
        "last_checked_at": report.generated_at,
        "last_duration_ms": report.duration_ms,
        "mode": report.mode,
    }
