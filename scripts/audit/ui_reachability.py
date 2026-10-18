#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""前端页面「可达性」审计 —— 导航/懒加载实际引用的组件 vs `src/pages/` 下的页面文件。

【为什么需要它（2026-10-18 的真实教训）】
上一轮把两个功能挂进了「**有引用、但没有任何导航指向**」的组件里：
  · `pages/hub/session/index.tsx` —— 「会话任务」叶子实际渲染的是 WorkbenchChatPage，
    那个页面组件不在任何路由上；
  · `pages/hub/memory/workflow.tsx` —— 被 skill-center 引用的只有 `MemoryWorkflowTable`
    **具名导出**，它的 `default` 无人使用。
扫描器（`scripts/audit/route_ui_coverage.py`）数的是 **API 引用**：它当时是绿的，
因为端点确实被源码里某处调用了 —— 但用户在界面上点不到。
⇒ 「引用 ≠ 可达」需要**另一条独立判据**：从真实入口出发做静态导入闭包，
   闭包外的页面 = 没进门；文件进了门但 default 无人消费的 = 半个门。

【口径（可证伪）】
  · 入口：`yunshu-ui/src/main.tsx`（唯一 HTML 入口，见 vite 的 index.html）；
  · 边：`import ... from`、`export ... from`、`import('...')`、副作用 `import '...'`，
    并展开 `import.meta.glob(...)`（`features/*/index.tsx` 的自动发现机制靠它）；
  · 解析：`@/x` → `yunshu-ui/src/x`；相对路径；省略扩展名（.tsx/.ts/.jsx/.js）；
    目录 → `index.*`；第三方包不参与（无本地文件）；
  · **先剥注释再解析**（复用 contract_diff.strip_comments）：本仓注释里大量写
    "见 pages/hub/x 的 import '@/y'"，不剥会造出假边，让真正没人路由的页面看起来可达；
  · 目标：`yunshu-ui/src/pages/**/*.tsx`（排除 `*.test.tsx`）；
  · 告警两类：
      1) `unreachable_file` —— 整个文件不在入口闭包里（页面没人路由）；
      2) `orphan_default`  —— 文件可达，但**没有任何导入方消费它的 default 导出**
         （典型形态：只被 `import { 具名 }` 使用 ⇒ default 是死代码）。

【边界（如实标注，别把"没看见"当成"不存在"）】
  · 纯静态：`import(变量)`、运行时拼路径、插件 manifest / `client_slot` 声明的模块不计入；
  · 不做 tree-shaking 级分析：具名导出有没有被用到不看，只看 default 有没有人 import；
  · 只扫 `src/pages/`：`src/workbench`、`src/components` 下的死组件不在本判据范围；
  · `import type X from`（纯类型）**不算**消费默认导出 —— 它不渲染任何东西。

用法：
    python scripts/audit/ui_reachability.py            # 重算并写 reports/ui_reachability.json
    python scripts/audit/ui_reachability.py --check     # 只校验提交产物是否与工作树一致（CI/收尾用）
    python scripts/audit/ui_reachability.py --json      # 打印完整报告
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit.contract_diff import strip_comments  # noqa: E402
UI_SRC = ROOT / "yunshu-ui" / "src"
REPORT = ROOT / "reports" / "ui_reachability.json"
#: 人工处置台账：新告警必须登记，修好后必须删掉（双向一致，见 --check）
TRIAGE = ROOT / "config" / "ui_reachability_triage.json"
TRIAGE_STATUSES = ("keep", "todo")
ENTRY = "yunshu-ui/src/main.tsx"
PAGES_DIR = UI_SRC / "pages"

#: 只看页面文件；测试与类型声明不是"门"
SKIP_NAME_SUFFIXES = (".test.tsx", ".test.ts", ".d.ts")
SOURCE_SUFFIXES = (".tsx", ".ts", ".jsx", ".js")

#: import 说明符（三种写法都收：静态 / 动态 / 副作用）
SPEC_PATTERNS = (
    re.compile(r"""\bfrom\s*['"]([^'"]+)['"]"""),
    re.compile(r"""\bimport\s*\(\s*['"]([^'"]+)['"]\s*\)"""),
    re.compile(r"""\bimport\s+['"]([^'"]+)['"]"""),
)
#: 默认导入：`import X from '...'` / `import X, { Y } from '...'`（`import type X` 不匹配）
DEFAULT_IMPORT = re.compile(
    r"""\bimport\s+([A-Za-z_$][\w$]*)\s*(?:,\s*\{[^}]*\})?\s*from\s*['"]([^'"]+)['"]"""
)
#: `lazy(() => import('...'))` 等价于消费默认导出；带 `.then(...)` 的是具名，不算
LAZY_DEFAULT_IMPORT = re.compile(
    r"""lazy\(\s*\(\s*\)\s*=>\s*import\(\s*['"]([^'"]+)['"]\s*\)\s*\)"""
)
#: import.meta.glob 的第一个参数（单串或数组）
GLOB_CALL = re.compile(r"""import\.meta\.glob(?:<[^>]*>)?\(\s*(\[[^\]]*\]|['"][^'"]+['"])""")
QUOTED = re.compile(r"""['"]([^'"]+)['"]""")


def _rel(path: Path) -> str:
    """repo 相对路径，统一正斜杠（Windows 上也能与 import 说明符对齐）。"""
    return path.resolve().relative_to(ROOT).as_posix()


def _strip_query(spec: str) -> str:
    return spec.split("?", 1)[0].split("#", 1)[0]


def _candidates(target: Path) -> Iterable[Path]:
    yield target
    for ext in SOURCE_SUFFIXES:
        yield Path(str(target) + ext)
    for ext in (".tsx", ".ts"):
        yield target / ("index" + ext)


def resolve_spec(spec: str, base_dir: Path) -> Optional[str]:
    """把 import 说明符解析成 repo 相对路径；第三方包 / 无对应文件返回 None。"""
    spec = _strip_query(spec.strip())
    if not spec or spec.startswith(("http:", "https:", "data:", "node:")):
        return None
    if spec.startswith("@/"):
        target = UI_SRC / spec[2:]
    elif spec.startswith("."):
        target = (base_dir / spec)
    else:
        return None  # 第三方包：没有本地文件，不参与可达性
    for cand in _candidates(target):
        if cand.is_file():
            return _rel(cand)
    return None


def expand_glob(pattern: str, base_dir: Path) -> List[str]:
    """展开 `import.meta.glob('...')` —— 构建期静态发现（features/*/index.tsx）。"""
    pattern = pattern.strip().strip("'\"")
    if pattern.startswith("@/"):
        base, pattern = UI_SRC, pattern[2:]
    else:
        base = base_dir
    try:
        return sorted(_rel(p) for p in base.glob(pattern) if p.is_file())
    except (OSError, ValueError):
        return []


def parse_imports(text: str, base_dir: Path) -> List[str]:
    """一个文件的全部本地依赖（去重保序）。"""
    found: List[str] = []
    for pat in SPEC_PATTERNS:
        for spec in pat.findall(text):
            resolved = resolve_spec(spec, base_dir)
            if resolved:
                found.append(resolved)
    for arg in GLOB_CALL.findall(text):
        for spec in QUOTED.findall(arg):
            found.extend(expand_glob(spec, base_dir))
    return list(dict.fromkeys(found))


def parse_default_consumers(text: str, base_dir: Path) -> List[str]:
    """该文件**消费了哪些模块的 default 导出**（`import X from` / 裸 lazy import）。"""
    consumed: List[str] = []
    for _name, spec in DEFAULT_IMPORT.findall(text):
        resolved = resolve_spec(spec, base_dir)
        if resolved:
            consumed.append(resolved)
    for spec in LAZY_DEFAULT_IMPORT.findall(text):
        resolved = resolve_spec(spec, base_dir)
        if resolved:
            consumed.append(resolved)
    return list(dict.fromkeys(consumed))


def _source_files() -> List[Path]:
    return sorted(
        p for p in UI_SRC.rglob("*")
        if p.is_file() and p.suffix in SOURCE_SUFFIXES and not p.name.endswith(SKIP_NAME_SUFFIXES)
    )


def _module_text(path: Path) -> str:
    """模块源码：**先剥注释**再解析。

    【为什么】本仓注释很密（大量 "见 pages/hub/x import '@/y'" 这类说明）。不剥的话，
    注释里提到的路径会被当成真实依赖 ⇒ 一条**假边**就能让真正没人路由的页面看起来可达，
    判据就从"漏报"这一侧坏掉。strip_comments 是 contract_diff 里的状态机实现
    （保留字符串与模板串、补回块注释的换行以保持行号），前端扫描共用同一套口径。
    """
    try:
        return strip_comments(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return ""


def build_graph() -> tuple:
    """返回 (deps, default_consumers, all_sources)。

    deps: rel -> [rel]（本地依赖）
    default_consumers: rel -> [rel]（它消费了谁的 default）
    """
    deps: dict = {}
    default_consumers: dict = {}
    files = _source_files()
    for p in files:
        rel = _rel(p)
        text = _module_text(p)
        base = p.parent
        deps[rel] = parse_imports(text, base)
        default_consumers[rel] = parse_default_consumers(text, base)
    return deps, default_consumers, [_rel(p) for p in files]


def reachable_from(entry: str, deps: dict) -> Set[str]:
    """入口闭包（含入口自身）。未解析到的入口 ⇒ 空集 + 由调用方报警。"""
    if entry not in deps:
        return set()
    seen: Set[str] = set()
    stack = [entry]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(d for d in deps.get(cur, []) if d not in seen)
    return seen


def page_files() -> List[str]:
    if not PAGES_DIR.is_dir():
        return []
    return sorted(
        _rel(p) for p in PAGES_DIR.rglob("*.tsx")
        if not p.name.endswith(SKIP_NAME_SUFFIXES)
    )


def has_default_export(path: Path) -> bool:
    return bool(re.search(r"""\bexport\s+default\b""", _module_text(path)))


def classify() -> dict:
    deps, default_consumers, all_sources = build_graph()
    reachable = reachable_from(ENTRY, deps)
    defaults_by_target: dict = {}
    for src, targets in default_consumers.items():
        for t in targets:
            defaults_by_target.setdefault(t, set()).add(src)

    unreachable = []
    orphan_default = []
    for rel in page_files():
        path = ROOT / rel
        if rel not in reachable:
            unreachable.append({"path": rel, "default_export": has_default_export(path)})
        elif has_default_export(path) and not defaults_by_target.get(rel):
            orphan_default.append({"path": rel, "importers": sorted(_importers_of(rel, deps))})

    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "entry": ENTRY,
        "scanned": {
            "source_modules": len(all_sources),
            "reachable_modules": len(reachable),
            "page_files": len(page_files()),
        },
        "unreachable": unreachable,
        "orphan_default": orphan_default,
    }
    payload["content_hash"] = content_hash(payload)
    return payload


def _importers_of(target: str, deps: dict) -> Set[str]:
    return {src for src, outs in deps.items() if target in outs}


def content_hash(payload: dict) -> str:
    """只把**判据**（可达性结论）算进哈希：说明文字/时间不参与。"""
    core = {
        "entry": payload["entry"],
        "unreachable": sorted(e["path"] for e in payload["unreachable"]),
        "orphan_default": sorted(e["path"] for e in payload["orphan_default"]),
        "page_files": sorted(payload.get("page_files", [])),
    }
    return hashlib.sha256(
        json.dumps(core, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


def load_triage() -> dict:
    """读处置台账；文件缺失 / 损坏 ⇒ 空表（所有告警按未登记处理，fail-closed 方向）。"""
    try:
        data = json.loads(TRIAGE.read_text(encoding="utf-8"))
        entries = data.get("entries")
        return entries if isinstance(entries, dict) else {}
    except (OSError, ValueError):
        return {}


def triage_problems(findings: Sequence[str], triage: dict) -> tuple:
    """双向核对：(未登记的告警, 台账里的陈旧项)。

    【为什么两个方向都要查】只查"新告警未登记"会让台账变成墓地：修好一条却忘了删，
    下次同一个文件再坏时就会被陈旧记录**静默放行**（路由覆盖的待办台账同款理由）。
    """
    untracked = sorted(p for p in findings if p not in triage)
    stale = sorted(p for p in triage if p not in set(findings))
    return untracked, stale


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="前端页面可达性审计（导航/懒加载 vs pages/）")
    ap.add_argument("--check", action="store_true",
                    help="校验产物与处置台账是否与工作树一致（收尾 / CI 用）")
    ap.add_argument("--json", action="store_true", help="打印完整报告 JSON")
    args = ap.parse_args(argv)

    payload = classify()
    # page_files 也放进产物：hash 里用它把「新增页面」这种漂移算进去
    payload["page_files"] = page_files()
    payload["content_hash"] = content_hash(payload)

    unreachable = payload["unreachable"]
    orphans = payload["orphan_default"]
    findings = [e["path"] for e in unreachable] + [e["path"] for e in orphans]

    triage = load_triage()
    bad_status = sorted(
        p for p, v in triage.items() if not isinstance(v, dict) or v.get("status") not in TRIAGE_STATUSES
    )
    untracked, stale = triage_problems(findings, triage)

    if args.check:
        failed = False
        if not REPORT.exists():
            print("[ERR] 缺少提交产物 %s（先运行本脚本生成）" % _rel(REPORT))
            failed = True
        else:
            old = json.loads(REPORT.read_text(encoding="utf-8"))
            if old.get("content_hash") != payload["content_hash"]:
                print("[ERR] 可达性结论已漂移：产物 hash=%s 重算 hash=%s"
                      % (old.get("content_hash"), payload["content_hash"]))
                print("      处理：运行 python scripts/audit/ui_reachability.py 重新派生并提交产物")
                failed = True
        if bad_status:
            print("[ERR] 处置台账里有非法 status（只允许 keep / todo）：" + repr(bad_status))
            failed = True
        if untracked:
            print("[ERR] 出现未登记的告警（新页面没人路由 / default 变死代码）：" + repr(untracked))
            print("      处理：接进导航，或在 config/ui_reachability_triage.json 登记并写明理由")
            failed = True
        if stale:
            print("[ERR] 台账里留有已消解的陈旧项（修好后请删除）：" + repr(stale))
            failed = True
        if failed:
            return 1
        print("[OK] 产物与台账均一致：%d 个页面文件 / %d 条不可达 / %d 条 default 无人用"
              % (len(payload["page_files"]), len(unreachable), len(orphans)))
        _print_warnings(unreachable, orphans, triage)
        return 0

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print("== 前端页面可达性 ==")
    print("  入口              : %s" % payload["entry"])
    print("  源码模块（可解析）: %d" % payload["scanned"]["source_modules"])
    print("  入口闭包          : %d" % payload["scanned"]["reachable_modules"])
    print("  pages/ 页面文件   : %d" % len(payload["page_files"]))
    print("  没有任何导航指向  : %d" % len(unreachable))
    print("  default 无人消费  : %d" % len(orphans))
    print("  content_hash      : %s" % payload["content_hash"])
    print("[OK] 已写入 %s" % REPORT)
    _print_warnings(unreachable, orphans, triage)

    if bad_status or untracked or stale:
        if bad_status:
            print("\n[ERR] 处置台账里有非法 status（只允许 keep / todo）：" + repr(bad_status))
        if untracked:
            print("\n[ERR] 未登记的告警：" + repr(untracked)
                  + "（接进导航，或在 config/ui_reachability_triage.json 登记）")
        if stale:
            print("\n[ERR] 台账里的陈旧项：" + repr(stale) + "（修好后请删除）")
        return 1
    return 0


def _print_warnings(unreachable: Sequence[dict], orphans: Sequence[dict], triage: dict) -> None:
    def _note(path: str) -> str:
        entry = triage.get(path) or {}
        status = entry.get("status") or "未登记"
        note = (entry.get("note") or "").strip()
        return status + (": " + note if note else "")

    if unreachable:
        print("\n[告警] 这些页面文件不在入口闭包里（没有任何导航/懒加载指向）：")
        for e in unreachable:
            print("  - %s%s  [%s]" % (
                e["path"], "" if e.get("default_export") else "（无 default 导出）", _note(e["path"])))
    if orphans:
        print("\n[告警] 这些页面可达，但 default 导出没有任何导入方消费（具名导出在用）：")
        for e in orphans:
            print("  - %s  <- 引用方：%s  [%s]" % (
                e["path"], ", ".join(e.get("importers") or []) or "（无）", _note(e["path"])))


if __name__ == "__main__":
    sys.exit(main())


if __name__ == "__main__":
    sys.exit(main())
