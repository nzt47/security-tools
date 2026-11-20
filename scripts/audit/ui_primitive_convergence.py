#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UI 原语收敛守卫：原生控件/任意值字号**只许降不许升**（2026-11-16）。

## 为什么要这个守卫

2026-11-16 审计前端：`components/ui/` 里其实有 Button/Input/Select/Card/ModalBase 等
统一封装，但**全仓只有 12 个文件 import 过它**（都在 pages/system/ + ConfirmDialog）。
其余页面是三种写法并存：
  · 页面级 CSS 类（`.pl-btn` / `.wb-send-btn` / `.skmgmt-btn` …，CSS 里 38 个 *btn* 选择器）
  · 原生 `<button>/<select>/<input>`（741 / 56 / 241 处）
  · 内联 style（179 处）与 Tailwind 任意值字号（1506 处 `text-[Npx]`）

统一不能靠"这次一起改完"（量太大、也没人守），所以把它变成**只降不升的基线**：
新增原生控件或新写 `text-[Npx]` 会让本守卫变红，逼新代码走 `components/ui` 原语与字号阶梯。

## 用法

    python scripts/audit/ui_primitive_convergence.py --check           # 只降不升（CI 用）
    python scripts/audit/ui_primitive_convergence.py --write-baseline  # 收敛后刷新基线

## 口径

- 统计范围：`yunshu-ui/src/**/*.tsx|ts`，**排除 `src/components/ui/`**（原语内部当然用原生元素）
  与 **`.test.` 文件**（合成反例/夹具会故意写原生控件，与 vitest 守卫、自动修复同口径；
  2026-11-20 起排除，raw_button 8→1、raw_select 1→0、raw_heading 80→78）。
- 指标：raw_button / raw_select / raw_input / raw_textarea / raw_range / arbitrary_text / raw_heading。
- 基线文件：`data/ui_primitive_baseline.json`（提交入库，随收敛一起减小）。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Dict

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_SRC = REPO_ROOT / "yunshu-ui" / "src"
BASELINE = REPO_ROOT / "data" / "ui_primitive_baseline.json"

#: 指标 → 正则。只统计「新增会加剧分裂」的写法。
METRICS: Dict[str, str] = {
    "raw_button": r"<button\b",
    "raw_select": r"<select\b",
    "raw_input": r"<input\b",
    # 2026-11-16：ui/Textarea 补齐后把 textarea 也纳入"只降不升"（此前没有原语，故未统计）
    "raw_textarea": r"<textarea\b",
    "raw_range": r"""type=["']range["']""",
    # 【2026-11-16 修正】原来只写 text-\[\d+px\]，**漏掉了半像素写法**（9.5/10.5/11.5/12.5/13.5），
    #   而半像素恰恰是最需要收敛的那批（17 个取值里 6 个是半像素）。补上小数后同一份代码的
    #   读数从 1451 变成 1519（+68），这是**量具修正**不是放宽门槛：基线随之重写，之后仍然只降不升。
    "arbitrary_text": r"text-\[\d+(?:\.\d+)?px\]",
    "raw_heading": r"<h[123]\b",
    # 【2026-11-17 新增】CSS 侧的硬编码颜色（hex 与 rgb()/rgba() 字面值）。
    #   为什么单列一个指标：这一轮发现 knowledge 的 .kb-* 里还有一支蓝（#4a9eff）与一批中性灰，
    #   而本脚本此前只量 .tsx/.ts —— CSS 字面值完全在量具之外，只能靠人工数构建产物才发现。
    #   统计范围：yunshu-ui/src/**/*.css，**排除 index.css**（那里是 token 定义本身，字面值合法）。
    #   与 arbitrary_text 是两个数，别混用。
    "css_hardcoded_color": "",
}

#: CSS 里"硬编码颜色"的两种写法。
_CSS_COLOR_PATTERNS = (r"#[0-9a-fA-F]{3,8}\b", r"\brgba?\(\s*\d")

#: 参与 CSS 指标的文件（token 定义文件本身豁免）。
_CSS_EXEMPT = {"index.css"}

#: 自定义属性**定义**行（`--name: <颜色>`）—— 这是色板/主题 token 的声明处，
#: 与 index.css 同性质：字面值在这一行是"定义"，不是"在规则里硬编码使用"。
#: 不计入 css_hardcoded_color；`var(--name)` 的使用处本来就没有字面值。
_CSS_VAR_DEF = re.compile(r"^\s*--[\w-]+\s*:")


def _strip_css_var_defs(text: str) -> str:
    """去掉自定义属性定义行，只留规则里的字面值（用于 CSS 颜色量具）。"""
    return "\n".join("" if _CSS_VAR_DEF.match(line) else line
                     for line in text.splitlines())


#: 扫描的文件后缀。
#:
#: 【2026-11-17 量具修正】原来只有 ".tsx"。而 `lib/tableActions.ts` 里有一个**任意值字号**写法
#: （三张表的行内按钮样式常量），这类 .ts 里的同写法一直不在量具范围内 —— 于是"任意值字号已清零"
#: 这句话只对 .tsx 成立。补上 ".ts" 后同一份代码 raw_button 334 → 335、arbitrary_text 0 → 1
#: （前者是 hooks/usePermission.ts 的**用法示例注释**里那个 <button>，量具不剥注释：
#: 剥 `//` 会误伤字符串里的 `http://`；后者已在同批改成 text-caption，故回到 0）。
#: 这是**量具修正**不是放宽门槛：基线随之一并重写，之后仍然只降不升。
#:
#: ★ 已知限制：量具按**文本**计数、不剥注释 —— 注释/文档里贴出原样写法也会被数进去。
#:   写注释时别复述原样类名（本文件与 lib/tableActions.ts 的注释都按这条改写）。
SCAN_SUFFIXES = (".tsx", ".ts")


def scan(root: Path = UI_SRC) -> Dict[str, int]:
    """统计当前写法数量（排除 components/ui 原语内部）。"""
    ui_dir = root / "components" / "ui"
    files = [p for p in root.rglob("*")
             if p.suffix in SCAN_SUFFIXES and ui_dir not in p.parents
             # 合成反例/夹具（.test.）里的原生控件不是产品代码，与 vitest 守卫同口径排除
             and ".test." not in p.name]
    counts = {k: 0 for k in METRICS}
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for key, pattern in METRICS.items():
            if not pattern:
                continue
            counts[key] += len(re.findall(pattern, text))
    # CSS 侧单独扫一遍（同一份代码里 .css 与 .tsx 是两套写法，指标也分开）
    for path in root.rglob("*.css"):
        if path.name in _CSS_EXEMPT:
            continue
        text = _strip_css_var_defs(path.read_text(encoding="utf-8", errors="replace"))
        for pattern in _CSS_COLOR_PATTERNS:
            counts["css_hardcoded_color"] += len(re.findall(pattern, text))
    counts["_files"] = len(files)
    return counts


#: `@import` 只有在「除 @charset/@layer 之外的所有语句之前」才有效（CSS 规范）。
#: 被任意规则挤到后面 ⇒ 浏览器**整条忽略**（不报错、不警告）。
#: 【2026-11-21 实测事故】调色板 `:root{}` 块被插到 workbench.css 最前面，把
#: `@import react-mosaic-component.css` / `highlight.js` 两条挤到规则之后 ⇒
#: Mosaic 基础样式与 hljs 主题全丢，工作台三列塌成 8px 条（导航看起来"没了"）。
_STYLE_IMPORT_RE = re.compile(r"@import\s+([^;]+);")
_STYLE_IMPORT_ALLOWED_PREFIX = re.compile(r"@charset[^;]*;|@layer[^;{]*;")


def style_import_violations(root: Path = UI_SRC) -> list:
    """返回 ``[(相对路径, 之前的残留文本, @import 内容)]``；空列表 = 全部合规。"""
    bad = []
    for path in sorted(root.rglob("*.css")):
        text = path.read_text(encoding="utf-8", errors="replace")
        # 注释要与原文等长替换，否则定位会偏；剥掉注释后只找**第一条** @import
        clean = re.sub(r"/\*.*?\*/", lambda m: " " * len(m.group(0)), text, flags=re.S)
        match = _STYLE_IMPORT_RE.search(clean)
        if not match:
            continue
        head = clean[:match.start()].strip()
        leftovers = _STYLE_IMPORT_ALLOWED_PREFIX.sub("", head).strip()
        if leftovers:
            bad.append((path.relative_to(root).as_posix(),
                        " ".join(leftovers.split())[:80],
                        match.group(1).strip()))
    return bad


def load_baseline(path: Path = BASELINE) -> Dict[str, int]:
    """读基线；缺失返回空（首次运行视为待建立）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: int(v) for k, v in data.items() if not k.startswith("_")}


def compare(current: Dict[str, int], baseline: Dict[str, int]) -> list:
    """只允许下降：返回**升高**的指标说明（空 = 通过）。"""
    if not baseline:
        return ["基线缺失：先跑 --write-baseline 建立一份"]
    problems = []
    for key in METRICS:
        now = int(current.get(key, 0))
        was = int(baseline.get(key, 0))
        if now > was:
            problems.append("%s: %d → %d（涨了 %d）" % (key, was, now, now - was))
    return problems


def write_baseline(current: Dict[str, int], path: Path = BASELINE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {k: int(current[k]) for k in METRICS}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="UI 原语收敛守卫（只降不升）")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="与基线比较，升高即失败")
    group.add_argument("--write-baseline", action="store_true", help="把当前计数写为基线")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出当前计数")
    args = parser.parse_args(argv)

    current = scan()
    if args.write_baseline:
        write_baseline(current)
        print("[ui-convergence] 基线已写入 %s" % BASELINE.relative_to(REPO_ROOT))
        print(json.dumps({k: current[k] for k in METRICS}, ensure_ascii=False))
        return 0

    imports_bad = style_import_violations()
    if args.json:
        payload = dict(current)
        payload["_style_import_violations"] = len(imports_bad)
        print(json.dumps(payload, ensure_ascii=False))
    else:
        for key in METRICS:
            print("  %-16s %5d" % (key, current[key]))
        print("  %-16s %5d" % ("css_import_order", len(imports_bad)))
    if imports_bad:
        print("[ui-convergence] 失败：@import 被规则挤到后面（浏览器会整条忽略）：")
        for rel, head, imp in imports_bad:
            print("   - %s：@import %s 之前出现了 %r" % (rel, imp, head))
        return 1
    problems = compare(current, load_baseline())
    if problems:
        print("[ui-convergence] 失败：新增了原生控件/任意值字号，请改用 components/ui 原语：")
        for item in problems:
            print("   - " + item)
        return 1
    print("[ui-convergence] [OK] 没有新增（只降不升）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
