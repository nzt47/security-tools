"""TASK-S6-01 页面截图 / E2E 快照（验收报告附件）

【做什么】
    用真实浏览器打开工作台的**治理面板**各栏目，等真实数据渲染完成后截图，
    并把"面板确实渲染了真实数据"的证据（关键 DOM 文本）一并落盘。

【口径】
    - 截图落到 `reports/s6_01/shots/`，命名 `<panel>.png`；
    - 每张图附 `panel_snapshots.json`：`{panel, url, title, evidence[], errors[]}`；
    - 证据取**真实渲染文本**（如"数据源（5）"、能力 id、事件条数），
      不用"页面打开成功"这类不能证明面板工作的断言。

用法::

    python scripts/dev/cp_panel_snapshots.py --base http://127.0.0.1:5757 \
        --out reports/s6_01/shots
"""

from __future__ import annotations

import argparse
import json
import os
import time

PANELS = [
    ("pipeline", "消化流水线", ["治理面板", "消化流水线", "轨迹采集", "灰度"]),
    ("approvals", "审批收件箱", ["审批收件箱", "一键批", "不出气泡"]),
    ("capabilities", "能力地图", ["能力地图", "cp.builtin.read_file", "数据源"]),
    ("roi", "成本 ROI", ["成本 ROI", "决策本体", "策略延迟"]),
    ("incidents", "自愈事故", ["自愈事故", "MTTD", "备份"]),
    ("memory", "记忆技能库", ["记忆", "召回优先级", "strategy"]),
    ("audit", "审计导出", ["审计导出", "验签"]),
]


def _click_nav(page, label: str, timeout: int = 8000) -> tuple[bool, str]:
    """点左侧导航（分组或叶子）

    三个坑（实测）：
      1. 叶子是 `<button>`，点它内部的 `<span>` **不会**更新导航状态（React onClick 在
         button 上）⇒ 必须点 button 本体；
      2. 导航在 Mosaic tile 内的滚动容器里，`scroll_into_view_if_needed` 会超时、
         Playwright 的可操作性检查也会偶发失败 ⇒ 兜底直接派发 MouseEvent；
      3. 分组是**可折叠**的：折叠时叶子根本不在 DOM 里（`has_text` 查不到），
         故必须显式展开并校验。

    Returns:
        (是否成功, 诊断信息)
    """
    err = ""
    try:
        page.locator("button").filter(has_text=label).first.click(timeout=timeout)
        return True, ""
    except Exception as e:  # noqa: BLE001 走兜底
        err = f"{type(e).__name__}"
    try:
        hit = page.evaluate(
            """(label) => {
              const btns = Array.from(document.querySelectorAll('button'));
              const hit = btns.find((b) => (b.textContent || '').trim() === label)
                       || btns.find((b) => (b.textContent || '').includes(label));
              if (!hit) return false;
              hit.dispatchEvent(new MouseEvent('click',
                { bubbles: true, cancelable: true, view: window }));
              return true;
            }""",
            label,
        )
        if hit:
            return True, f"{err}（dispatchEvent 兜底）"
        return False, f"{err}（未找到按钮且 dispatch 未命中）"
    except Exception as e2:  # noqa: BLE001
        return False, f"{err} + dispatch {type(e2).__name__}"


def main() -> int:
    parser = argparse.ArgumentParser(description="治理面板截图 / E2E 快照")
    parser.add_argument("--base", default="http://127.0.0.1:5757")
    parser.add_argument("--out", default=os.path.join("reports", "s6_01", "shots"))
    parser.add_argument("--width", type=int, default=1680)
    parser.add_argument("--height", type=int, default=1050)
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    from playwright.sync_api import sync_playwright

    snapshots = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": args.width, "height": args.height})
        page = ctx.new_page()
        console_errors: list[str] = []
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(f"pageerror: {e}"))

        for key, label, needles in PANELS:
            nav_error = ""
            url = f"{args.base}/chat#/workbench"
            page.goto(url, wait_until="load", timeout=60000)
            # 等导航树真正挂载（Mosaic 外壳 + zustand 水合；实测 1.5s 不够稳）
            try:
                page.wait_for_selector('button:has-text("治理面板")', timeout=20000)
            except Exception:  # noqa: BLE001 等不到就继续，后面会记录诊断
                pass
            page.wait_for_timeout(1800)
            # 先确保「治理面板」分组**展开**：分组可折叠 ⇒ 折叠时叶子不在 DOM 里。
            # ★ 必须先判断当前状态再决定是否点，否则会把已展开的分组点成折叠（实测踩过）。
            leaves_present = page.evaluate(
                """() => Array.from(document.querySelectorAll('button'))
                     .some((b) => (b.textContent || '').trim() === '消化流水线')"""
            )
            if not leaves_present:
                group_ok, group_err = _click_nav(page, "治理面板")
                page.wait_for_timeout(800)
            else:
                group_err = ""
            # 再点该栏目叶子
            leaf_ok, leaf_err = _click_nav(page, label)
            nav_error = f"group[{group_err}] leaf[{leaf_err}]"
            clicked = leaf_ok
            page.wait_for_timeout(3200)   # 等真实数据请求 + 渲染

            shot = os.path.join(args.out, f"{key}.png")
            page.screenshot(path=shot, full_page=False)
            body = page.inner_text("body")[:20000]
            evidence = [n for n in needles if n in body]
            snapshots.append({
                "panel": key,
                "label": label,
                "url": url,
                "nav_clicked": clicked,
                "nav_error": nav_error,
                "shot": shot.replace("\\", "/"),
                "evidence": evidence,
                "evidence_missing": [n for n in needles if n not in body],
                "body_len": len(body),
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            })
            print(f"[shot] {key}: evidence={evidence} missing="
                  f"{[n for n in needles if n not in body]} → {shot}")

        browser.close()

    out_json = os.path.join(args.out, "panel_snapshots.json")
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump({"snapshots": snapshots, "console_errors": console_errors[:50]},
                  fh, ensure_ascii=False, indent=2)
    print(f"[shot] 快照索引：{out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
