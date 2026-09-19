#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""依赖双真相源漂移审计（TASK-03 第 2 步交付物）。

Why 需要这个脚本
----------------
本仓库有**两个**依赖真相源，而它们服务不同阶段：

* `pyproject.toml` 的 `[project].dependencies` —— CI 执行 `pip install -e .` 时
  唯一读到的声明（见 `.github/workflows/ci.yml:56`，以及 `pyproject.toml:51-53`
  注释自述"CI 单元测试只执行 pip install -e .，不读 requirements*.txt"）。
* `requirements.txt` —— pip-compile 生成的**锁定文件**，生产环境（Dockerfile /
  `start_yunshu.bat`）实际安装的版本。

两者一旦分叉，就会出现"**CI 测的依赖 ≠ 生产跑的依赖**"，而这是最昂贵的一类
假绿：测试全过、上线即炸，且根因不在测试里。历史上 `chromadb` 已经被这型问题
真实咬过一次（仓库里存有 `chromadb 0.5.x downgrade` 与
`chromadb_windows_compatibility_migration_guide.md` 文档）。

Why 输出三类而不是"冲突/不冲突"两类
------------------------------------
只报"冲突"会把**未声明包**（requirements 装了、pyproject 没写）漏掉——而这一类
恰恰会让 CI（`pip install -e .`）缺件、生产却装着，形成"只有 CI 会红"的假象。

> ⚠️ **2026-09-19 更正**：本脚本初版此处曾写"这一类恰恰解释了本仓
> `failures_baseline.txt` 里 10 条 `ModuleNotFoundError`" —— **该因果推断已被实测推翻**。
> 那 10 条 ERROR 的报错文本是
> `No module named 'transformers.configuration_utils'; 'transformers' **is not a package**`，
> 后半句是**名字被遮蔽**的症状（`sys.modules['transformers']` 被 `tests/unit/test_reranker.py`
> 的模块级 `MagicMock()` 覆盖），根因是**测试顺序污染**，不是缺声明；
> 且 `transformers` 本机实测 `5.13.1` 且 `import transformers.configuration_utils` 正常。
> 详细证据见 `docs/closeout/BASELINE_20260918.md` §7。
> `transformers` 的声明仍然补齐了（`agent/` 直接 import 它），但理由与那 10 条 ERROR 无关。

1. `ONLY_IN_PYPROJECT`   仅 pyproject 声明 —— 生产锁文件里没有该包，生产可能缺件。
2. `ONLY_IN_REQUIREMENTS` 仅 requirements 声明 —— CI（`pip install -e .`）不会装它。
3. `CONFLICT`            真冲突 —— requirements 的钉版本**不满足** pyproject 约束。
4. `SPEC_DIFF`           口径不同 —— 钉版本满足 pyproject 约束，但两者写法/边界不同。

用法
----
    python scripts/audit_dependency_drift.py                 # 人类可读报告
    python scripts/audit_dependency_drift.py --json          # 机器可读
    python scripts/audit_dependency_drift.py --fail-on-conflict   # CI 用：有真冲突即非零退出
    python scripts/audit_dependency_drift.py --installed     # 追加"本机实装"第三口径

Why 退出码默认 0
----------------
本脚本首先是**治理工具**（要能随时跑、随时看），其次才是门禁。默认不阻断，
只有显式 `--fail-on-conflict` 才以非零退出——避免"跑个审计把自己 CI 跑红"。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

try:  # packaging 是 pip / setuptools 的常驻依赖，实装必然存在
    from packaging.requirements import Requirement
    from packaging.specifiers import SpecifierSet
    from packaging.utils import canonicalize_name
    from packaging.version import Version
except ImportError as exc:  # pragma: no cover - 环境缺失时的显式降级
    print(f"[audit_dependency_drift] 缺少 packaging：{exc}", file=sys.stderr)
    print("请先 `pip install packaging`（通常随 pip/setuptools 自动存在）", file=sys.stderr)
    raise SystemExit(2)


# ── 解析层 ──────────────────────────────────────────────────────────────────

def _read_pyproject(path: Path) -> dict[str, Requirement]:
    """读取 pyproject.toml 的 [project].dependencies → {规范名: Requirement}。

    Why 用 tomllib：Python 3.11+ 标准库自带，避免为本脚本引入 tomli/tomlkit
    依赖（D3 不引入重依赖）。requires-python >=3.11 已由 pyproject 保证。
    """
    import tomllib

    with path.open("rb") as fh:
        data = tomllib.load(fh)
    deps = data.get("project", {}).get("dependencies", []) or []
    out: dict[str, Requirement] = {}
    for raw in deps:
        req = Requirement(raw)
        out[canonicalize_name(req.name)] = req
    return out


#: pip-compile 在 `# via` 块里用来标注"本包是项目**直接**依赖"的前缀。
#: Why 必须识别它：`requirements.txt` 里 100+ 个包是**传递依赖**（正常的锁定文件
#: 形态，不该报缺）；只有带本前缀却不在 pyproject 里的包，才是"**直接依赖被漏声明**"
#: ——那才是真缺口（CI 执行 `pip install -e .` 时不会装它）。
#: 实装形态有两种：`Yunshu (pyproject.toml)`（pip-compile 自动生成）
#: 与 `Yunshu (TLM L3 向量存储后端)`（人工补充的说明），故按前缀匹配。
VIA_PROJECT_PREFIX = "Yunshu"


def _read_requirements(path: Path) -> tuple[dict[str, Requirement], dict[str, list[str]]]:
    """读取 pip-compile 锁定文件 → ({规范名: Requirement}, {规范名: [# via 来源]})。

    Why 手工逐行解析而不是 `pip._internal`：锁定文件的"钉版本"语义由
    `==` 表达，且带 `    # via xxx` 缩进注释行；用 pip 内部 API 会引入
    私有接口依赖，版本升级即碎。
    """
    out: dict[str, Requirement] = {}
    vias: dict[str, list[str]] = {}
    current: str | None = None
    continuing = False
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            m = re.match(r"^#\s+via\b\s*(.*)$", stripped)
            if m and current:
                first = m.group(1).strip()
                if first:
                    vias.setdefault(current, []).append(first)
                    continuing = not first.endswith(",")
                else:
                    # `# via` 后紧跟换行，来源在后续 `#   xxx` 行
                    continuing = True
            elif current and continuing:
                vias.setdefault(current, []).append(stripped.lstrip("#").strip())
            continue
        try:
            # 去掉**行尾注释**（requirements-dev/test.txt 的风格是
            # `pytest>=7.0.0,<8.0.0   # 测试框架 - ...`；PEP 508 解析器不接受
            # 行尾注释，不剥离会整行解析失败并被静默跳过 ⇒ 该文件假通过）。
            # 只在 `#` 前面是空白时才截断，避免破坏 URL 里的 `#`（如 git+https://x#egg=y）。
            code = re.split(r"\s+#", stripped, maxsplit=1)[0].strip()
            if not code:
                continue
            req = Requirement(code)
        except Exception:
            # 少数行是 pip-compile 的自由文本（如 "The following packages..."），跳过
            current = None
            continue
        current = canonicalize_name(req.name)
        out[current] = req
        continuing = False
    return out, vias


def _read_installed(frozen: str | None = None) -> dict[str, str]:
    """读取本机实装版本（第三口径）。

    Why 需要第三口径：pyproject 与 requirements 都可能是"纸面声明"，只有
    `pip freeze` 是**这台机器上真正在跑的东西**。三者不一致时，必须知道
    生产实况站在哪一边，否则"以谁为准"就是拍脑袋。
    """
    if frozen is not None:
        text = frozen
    else:
        import subprocess

        proc = subprocess.run(
            [sys.executable, "-m", "pip", "freeze"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            return {}
        text = proc.stdout
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        m = re.match(r"^([A-Za-z0-9._-]+)==([^\s;]+)", line)
        if m:
            out[canonicalize_name(m.group(1))] = m.group(2)
    return out


# ── 比较层 ──────────────────────────────────────────────────────────────────

def _is_pinned(req: Requirement | None) -> bool:
    """判断约束是否为"钉死单版本"（`==x.y.z`，含 `===`）。"""
    if req is None:
        return False
    specs = list(req.specifier)
    return len(specs) == 1 and specs[0].operator in ("==", "===") and "*" not in specs[0].version


def _pin(req: Requirement) -> str | None:
    specs = list(req.specifier)
    if len(specs) == 1 and specs[0].operator in ("==", "==="):
        return specs[0].version
    return None


def _bounds(spec: SpecifierSet) -> tuple[Version | None, Version | None]:
    """从 SpecifierSet 中抽取 (下界, 上界)，只识别 >=/> 与 <=/<。

    Why 只做保守抽取：完整的 PEP 440 约束集相交判定是 NP 难级别的（`!=`、
    通配、`~=` 组合），而本仓 requirements.txt 是锁定文件（几乎全是 `==`），
    pyproject 是区间约束。保守抽取足以覆盖全部真实形态，且**宁可漏报
    也不误报**——漏报由 `pip check` 与 CI 一致性步骤兜底。
    """
    lo: Version | None = None
    hi: Version | None = None
    for s in spec:
        try:
            v = Version(s.version)
        except Exception:
            continue
        if s.operator in (">", ">="):
            if lo is None or v > lo:
                lo = v
        elif s.operator in ("<", "<="):
            if hi is None or v < hi:
                hi = v
    return lo, hi


def _specs_disjoint(a: SpecifierSet, b: SpecifierSet) -> bool:
    """保守判定两个约束集是否**必然不相交**（宁可返回 False）。"""
    a_lo, a_hi = _bounds(a)
    b_lo, b_hi = _bounds(b)
    if a_lo is not None and b_hi is not None and a_lo > b_hi:
        return True
    if b_lo is not None and a_hi is not None and b_lo > a_hi:
        return True
    # 用真实版本边界点做二次确认（只在边界上取点，不枚举版本空间）
    candidates: set[Version] = set()
    for v in (a_lo, a_hi, b_lo, b_hi):
        if v is not None:
            candidates.add(Version(f"{v.major}.{v.minor}.{v.micro}"))
    if not candidates:
        return False
    # 若存在一个点落在两者之内，则相交 ⇒ 不是真冲突
    for v in candidates:
        if v in a and v in b:
            return False
    return True


def _pip_check_scoped(project_names: set[str], text: str | None = None) -> dict[str, Any]:
    """运行 `pip check`，把冲突按"是否落在本项目依赖闭包内"分成两组。

    Why 必须分组：本机 `python` 是一个**全局共享解释器**（不是项目 venv），
    里面同时装着别的项目的包（mootdx / gtts / hermes-agent / langchain 等）。
    实测 `pip check` 报 10 条冲突，其中 **0 条**落在本项目闭包内
    ⇒ 直接拿 `pip check` 当门禁只会得到一条永远红、且与本项目无关的断言。
    这类"看起来在报警、其实与本仓无关"的门禁，最后一定会被改成 `|| true`
    ——本脚本的分组就是为了让它**不必**被放宽。
    """
    if text is None:
        import subprocess

        proc = subprocess.run(
            [sys.executable, "-m", "pip", "check"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        text = (proc.stdout or "") + (proc.stderr or "")

    holder_re = re.compile(r"^([A-Za-z0-9._-]+)\s+[\w.+-]+\s+has requirement\s+(.+)$")
    in_project: list[dict[str, str]] = []
    foreign: list[dict[str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        m = holder_re.match(line)
        if not m:
            continue
        holder = canonicalize_name(m.group(1))
        entry = {"holder": holder, "detail": m.group(2).strip()}
        (in_project if holder in project_names else foreign).append(entry)
    return {
        "total": len(in_project) + len(foreign),
        "in_project_closure": in_project,
        "foreign": foreign,
    }


def audit(pyproject_path: Path, requirements_path: Path, installed: dict[str, str] | None = None) -> dict[str, Any]:
    py = _read_pyproject(pyproject_path)
    req, vias = _read_requirements(requirements_path)
    inst = installed or {}

    conflicts: list[dict[str, Any]] = []
    only_py: list[dict[str, Any]] = []
    only_req: list[dict[str, Any]] = []
    missing_direct: list[dict[str, Any]] = []
    spec_diff: list[dict[str, Any]] = []
    agree: list[dict[str, Any]] = []
    py_rejects_installed: list[dict[str, Any]] = []

    # [4] 三方不一致：pyproject 的区间**排除**了本机实装版本。
    # Why 单列这一类：TASK-03 §2.1 只比了 pyproject vs requirements 两方，但本机实测
    # 出现"**三方全不一致**"（torch：pyproject `<2.5.0` / 锁文件 `2.12.0` / 实装
    # `2.13.0+cpu`）。若只看两方，会得出"改锁文件即可"的错误结论——而真正在跑的
    # 解释器版本被两边同时排除。判"哪个是生产实况"必须先看见这一类。
    for name, ver in sorted(inst.items()):
        p = py.get(name)
        if p is None or not str(p.specifier):
            continue
        try:
            if Version(ver) in p.specifier:
                continue
        except Exception:
            continue
        py_rejects_installed.append(
            {
                "name": name,
                "pyproject": str(p.specifier),
                "installed": ver,
                "requirements": (_pin(req[name]) if name in req else None),
            }
        )

    for name in sorted(set(py) | set(req)):
        p = py.get(name)
        r = req.get(name)
        py_txt = str(p.specifier) if p and str(p.specifier) else ("" if p else None)
        req_txt = (_pin(r) or str(r.specifier)) if r is not None else None
        inst_ver = inst.get(name)

        if p is None:
            entry = {
                "name": name,
                "requirements": req_txt,
                "installed": inst_ver,
                "via": ", ".join(vias.get(name, [])) or "-",
            }
            if any(v.startswith(VIA_PROJECT_PREFIX) for v in vias.get(name, [])):
                # 锁定文件标明"D 是项目直接依赖"，但 pyproject 里没有 ⇒ 漏声明。
                # CI 的 `pip install -e .` 不会装它，生产却装了 —— 这正是 CI≠生产的成因。
                missing_direct.append(entry)
            else:
                only_req.append(entry)
            continue
        if r is None:
            only_py.append({"name": name, "pyproject": py_txt, "installed": inst_ver})
            continue

        if _is_pinned(r):
            pin = _pin(r)
            assert pin is not None
            try:
                ok = Version(pin) in p.specifier
            except Exception:
                ok = False
            if not ok:
                conflicts.append(
                    {
                        "name": name,
                        "pyproject": py_txt,
                        "requirements": req_txt,
                        "installed": inst_ver,
                    }
                )
            elif py_txt != f"=={pin}":
                spec_diff.append(
                    {
                        "name": name,
                        "pyproject": py_txt,
                        "requirements": req_txt,
                        "installed": inst_ver,
                        "note": "钉版本落在 pyproject 区间内，写法与边界口径不同",
                    }
                )
            else:
                agree.append({"name": name, "version": pin})
        else:
            if _specs_disjoint(p.specifier, r.specifier):
                conflicts.append(
                    {
                        "name": name,
                        "pyproject": py_txt,
                        "requirements": req_txt,
                        "installed": inst_ver,
                    }
                )
            else:
                spec_diff.append(
                    {
                        "name": name,
                        "pyproject": py_txt,
                        "requirements": req_txt,
                        "installed": inst_ver,
                        "note": "两处均为区间约束，相交但口径不同",
                    }
                )

    return {
        "pyproject": str(pyproject_path),
        "requirements": str(requirements_path),
        "counts": {
            "pyproject_declared": len(py),
            "requirements_locked": len(req),
            "installed": len(inst),
            "ONLY_IN_PYPROJECT": len(only_py),
            "MISSING_DIRECT_IN_PYPROJECT": len(missing_direct),
            "ONLY_IN_REQUIREMENTS": len(only_req),
            "CONFLICT": len(conflicts),
            "SPEC_DIFF": len(spec_diff),
            "PYPROJECT_REJECTS_INSTALLED": len(py_rejects_installed),
            "AGREE": len(agree),
        },
        "ONLY_IN_PYPROJECT": only_py,
        "MISSING_DIRECT_IN_PYPROJECT": missing_direct,
        "ONLY_IN_REQUIREMENTS": only_req,
        "CONFLICT": conflicts,
        "SPEC_DIFF": spec_diff,
        "PYPROJECT_REJECTS_INSTALLED": py_rejects_installed,
        "AGREE": agree,
        #: 本项目的"依赖闭包"名称集合 = pyproject 声明 ∪ 锁定文件条目。
        #: Why 需要它：`pip check` 报的冲突必须按"是否落在本项目闭包内"分组，
        #: 否则全局共享解释器里其它项目的包会污染门禁（详见 _pip_check_scoped）。
        "closure_names": sorted(set(py) | set(req)),
    }


# ── 展示层 ──────────────────────────────────────────────────────────────────

def _fmt_row(items: list[dict[str, Any]], cols: list[str]) -> list[str]:
    if not items:
        return ["  （空）"]
    out = []
    for it in items:
        out.append("  " + " | ".join(str(it.get(c, "-")) for c in cols))
    return out


def render(report: dict[str, Any], limit: int = 15) -> str:
    c = report["counts"]
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("依赖双真相源漂移审计报告")
    lines.append("=" * 78)
    lines.append(f"pyproject   : {report['pyproject']}")
    lines.append(f"requirements: {report['requirements']}")
    lines.append(
        f"计数        : pyproject 声明 {c['pyproject_declared']} | "
        f"requirements 锁定 {c['requirements_locked']} | 本机实装 {c['installed']}"
    )
    lines.append("")

    lines.append(f"■ [1] 真冲突 CONFLICT = {c['CONFLICT']}（口径不相交，必须修）")
    lines.extend(_fmt_row(report["CONFLICT"], ["name", "pyproject", "requirements", "installed"]))
    lines.append("")

    lines.append(f"■ [2] 仅一处声明（单边缺失）")
    lines.append(
        f"    [2a] **直接依赖漏声明** pyproject 缺 = {c['MISSING_DIRECT_IN_PYPROJECT']}"
        f"（锁定文件标注 'via {VIA_PROJECT_PREFIX} …' 却不在 pyproject ⇒ CI 不装它）"
    )
    lines.extend(_fmt_row(report["MISSING_DIRECT_IN_PYPROJECT"], ["name", "requirements", "installed", "via"]))
    lines.append("")
    lines.append(f"    [2b] 仅 pyproject 有 = {c['ONLY_IN_PYPROJECT']}（生产锁文件缺件）")
    lines.extend(_fmt_row(report["ONLY_IN_PYPROJECT"], ["name", "pyproject", "installed"]))
    lines.append("")
    lines.append(f"    [2c] 仅 requirements 有（传递依赖，锁定文件正常形态）= {c['ONLY_IN_REQUIREMENTS']}")
    rows = _fmt_row(report["ONLY_IN_REQUIREMENTS"], ["name", "requirements", "installed", "via"])
    if not limit or len(rows) <= limit:
        lines.extend(rows)
    else:
        lines.extend(rows[:limit])
        lines.append(f"  …（共 {len(rows)} 项，此处只列前 {limit} 项；完整清单见 --json / --json-out）")
    lines.append("")

    lines.append(f"■ [3] 口径不同 SPEC_DIFF = {c['SPEC_DIFF']}（相交但写法/边界不同）")
    lines.extend(_fmt_row(report["SPEC_DIFF"], ["name", "pyproject", "requirements", "installed"]))
    lines.append("")

    lines.append(
        f"■ [4] pyproject **排除**了本机实装版本 = {c['PYPROJECT_REJECTS_INSTALLED']}"
        f"（三方不一致；判'生产实况'时以本机为准）"
    )
    lines.extend(
        _fmt_row(report["PYPROJECT_REJECTS_INSTALLED"], ["name", "pyproject", "requirements", "installed"])
    )
    lines.append("")
    lines.append(f"■ 完全一致 AGREE = {c['AGREE']}")
    pk = report.get("pip_check")
    if pk:
        lines.append("")
        lines.append(
            f"■ [5] pip check = {pk['total']} 条冲突"
            f"（其中**落在本项目依赖闭包内** = {len(pk['in_project_closure'])}）"
        )
        lines.append("    项目内冲突：")
        lines.extend(_fmt_row(pk["in_project_closure"], ["holder", "detail"]))
        lines.append(f"    闭包外冲突（本机全局解释器里别的项目的包，{len(pk['foreign'])} 条，门禁不阻断）：")
        lines.extend(_fmt_row(pk["foreign"][:5], ["holder", "detail"]))
        if len(pk["foreign"]) > 5:
            lines.append(f"    …（共 {len(pk['foreign'])} 条）")
    lines.append("=" * 78)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="审计 pyproject.toml 与 requirements.txt 的依赖漂移")
    ap.add_argument("--pyproject", type=Path, default=REPO_ROOT / "pyproject.toml")
    ap.add_argument("--requirements", type=Path, default=REPO_ROOT / "requirements.txt")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--json-out", type=Path, default=None, help="把 JSON 报告写入文件")
    ap.add_argument("--installed", action="store_true", help="追加本机实装（pip freeze）作为第三口径")
    ap.add_argument("--frozen", type=Path, default=None, help="用文件中的 pip freeze 文本代替实时调用")
    ap.add_argument(
        "--fail-on-conflict",
        action="store_true",
        help="存在真冲突时以非零退出（CI 门禁用）",
    )
    ap.add_argument(
        "--check-installed",
        action="store_true",
        help="pyproject 排除了本机实装版本时以非零退出（要求同时给 --installed）",
    )
    ap.add_argument(
        "--pip-check",
        action="store_true",
        help="运行 pip check，仅当冲突落在本项目依赖闭包内时以非零退出",
    )
    ap.add_argument(
        "--pip-check-out",
        type=Path,
        default=None,
        help="把 pip check 的原始输出归档到该文件（CI artifact）",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=15,
        help="文本报告中 [2c] 传递依赖清单的显示上限（0 = 全量；JSON 始终全量）",
    )
    args = ap.parse_args(argv)

    inst: dict[str, str] = {}
    if args.installed or args.frozen is not None:
        frozen_text = args.frozen.read_text(encoding="utf-8") if args.frozen else None
        inst = _read_installed(frozen_text)

    report = audit(args.pyproject, args.requirements, inst)

    pip_report: dict[str, Any] | None = None
    if args.pip_check or args.pip_check_out is not None:
        import subprocess as _sp

        _proc = _sp.run(
            [sys.executable, "-m", "pip", "check"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        _raw = (_proc.stdout or "") + (_proc.stderr or "")
        if args.pip_check_out:
            args.pip_check_out.parent.mkdir(parents=True, exist_ok=True)
            args.pip_check_out.write_text(_raw, encoding="utf-8")
        closure = set(report["closure_names"])
        pip_report = _pip_check_scoped(closure, _raw)
        report["pip_check"] = pip_report

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render(report, limit=args.limit))

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[audit_dependency_drift] JSON 报告已写入 {args.json_out}")

    if args.fail_on_conflict and report["counts"]["CONFLICT"] > 0:
        print(
            f"[audit_dependency_drift] ✗ 存在 {report['counts']['CONFLICT']} 项真冲突",
            file=sys.stderr,
        )
        return 1
    if args.check_installed:
        n = report["counts"]["PYPROJECT_REJECTS_INSTALLED"]
        if not inst:
            print(
                "[audit_dependency_drift] --check-installed 需要同时给 --installed",
                file=sys.stderr,
            )
            return 2
        if n > 0:
            print(
                f"[audit_dependency_drift] ✗ pyproject 排除了 {n} 个本机实装版本"
                f"（CI 装的 ≠ 生产跑的）",
                file=sys.stderr,
            )
            return 1
    if args.pip_check and pip_report is not None and pip_report["in_project_closure"]:
        print(
            f"[audit_dependency_drift] ✗ pip check 在本项目依赖闭包内有 "
            f"{len(pip_report['in_project_closure'])} 条冲突",
            file=sys.stderr,
        )
        return 1
    return 0


def _force_utf8_stdio() -> None:
    """把 stdout/stderr 强制成 UTF-8，避免在非 UTF-8 控制台上崩溃。

    【不易·2026-09-19 实测缺陷】本脚本的输出含 `⇒`(U+21D2) 等非 ASCII 符号
    （见 `grep -c '⇒\\|→\\|✓\\|✗' scripts/audit_dependency_drift.py` → 10 处）。
    Windows 的**默认控制台编码是 GBK(CP936)**，`print()` 遇到这类字符会抛
    `UnicodeEncodeError: 'gbk' codec can't encode character '\\u21d2'`，
    进程以 **exit 1** 结束 —— 与"发现依赖冲突"的退出码**完全相同**，
    于是**一个编码问题会被误读成一条依赖冲突**（实测踩到）。
    CI 上之所以没暴露：`.github/workflows/*.yml` 的 runner 环境是 `C.UTF-8`。

    Why 用 `reconfigure` 而不是包一层 `TextIOWrapper`：
      · `reconfigure` 是 Python 3.7+ 的原生出口重配置，不替换 `sys.stdout` 对象，
        因此不影响 `capsys`/重定向等其他消费者的身份判断；
      · 包 wrapper 会让 `sys.stdout` 在导入前后变身，容易踩到 `isatty()` 与
        pytest 捕获的边界问题。
    Why `errors="replace"` 而不是 `strict`：本脚本是**审计工具**，宁可把无法
    编码的字符降级成 `?` 也不该因为一个装饰性符号丢掉整份报告。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - 已分离/已关闭的流
            pass


if __name__ == "__main__":
    _force_utf8_stdio()
    raise SystemExit(main())
