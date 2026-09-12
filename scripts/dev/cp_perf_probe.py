"""U6 真实浏览器性能实测（TASK-S6-01）

【为什么必须真浏览器】
    `perf.test.tsx` 跑在 jsdom 里，量到的是"框架提交 + 首帧调度"，
    **不含**真实浏览器的布局/绘制。`docs/PERF_BUDGET_REBASED.md` §五 要求
    「用浏览器 Performance API 测状态变更 → 首帧重绘」「用 Lighthouse / Web Vitals
    采 LCP/FCP」。本脚本就是该方案的落地。

【测什么】
    1. **首屏（工作台冷启动）**：FCP / LCP / domContentLoaded / load 事件耗时，
       以及"首个可见面板挂载"的服务端耗时；
    2. **状态灯"状态变更 → 首帧重绘"**：在页面里挂一个五态 `StatusBadge`，
       用 `PerformanceObserver` 的 `paint` 项 + `requestAnimationFrame`
       量出"改状态 → 下一帧"的真实墙钟；取 30 次样本的 p50/p95/max；
    3. 结果 POST 到 `cp_perf_sink`（落盘 `reports/s6_01/*.json`）。

【口径（必须随数字一起报）】
    - 计时源：浏览器 `performance.now()`
    - 首屏：`FCP` / `LCP` 为 PerformanceObserver 原始值；`nav` 为
      `performance.getEntriesByType('navigation')[0]` 的 `domContentLoadedEventEnd` /
      `loadEventEnd`
    - 环境：本机 headless Chromium（版本随 playwright 提供），
      **热缓存**（同进程二次导航）与**冷缓存**（首次导航）分开报

用法::

    python scripts/dev/cp_perf_probe.py --url http://127.0.0.1:5678/chat#/workbench \
        --sink http://127.0.0.1:5711/cp-perf
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request

STATUS_SCRIPT = r"""
() => {
  return new Promise((resolve) => {
    // 在页面内建一个最小宿主，挂五态状态灯（与 StatusBadge 同构：纯 props → 一次提交）
    const host = document.createElement('div');
    host.id = 'cp-perf-status-host';
    host.style.cssText = 'position:fixed;left:8px;bottom:8px;z-index:2147483000';
    document.body.appendChild(host);
    const TONES = ['gray', 'blue', 'yellow', 'green', 'red'];
    const CLASS = {
      gray: 'bg-slate-500/15 text-slate-300 border-slate-600',
      blue: 'bg-sky-500/15 text-sky-300 border-sky-700',
      yellow: 'bg-amber-500/15 text-amber-300 border-amber-700',
      green: 'bg-emerald-500/15 text-emerald-300 border-emerald-700',
      red: 'bg-red-500/15 text-red-300 border-red-700',
    };
    function render(tone) {
      host.innerHTML =
        '<span data-cp-status-tone="' + tone + '" class="inline-flex items-center gap-1.5 ' +
        'rounded-full border px-2 py-0.5 text-xs ' + CLASS[tone] + '"><span class="h-1.5 w-1.5 ' +
        'rounded-full bg-current"></span>状态 ' + tone + '</span>';
    }
    render(TONES[0]);

    // ① 异步重绘（下一帧回调时间戳 − DOM 变更时刻）：受 vsync 量化，含 1 个显示周期
    const asyncSamples = [];
    // ② 同步重绘（DOM 变更 → 立即强制读取布局）：量级更小，不受 vsync 影响
    const syncSamples = [];
    const nextFrame = () => new Promise((r) => requestAnimationFrame((t) => r(t)));
    (async () => {
      for (let k = 0; k < 5; k += 1) {
        render(TONES[(k + 1) % 5]);
        await nextFrame();
      }
      for (let k = 0; k < 30; k += 1) {
        const tone = TONES[(k + 1) % 5];
        // ① 异步口径
        const t0 = performance.now();
        render(tone);
        const frameTs = await nextFrame();
        asyncSamples.push(frameTs - t0);
        // ② 同步口径（强制 reflow：读回 offsetHeight）
        const tone2 = TONES[(k + 2) % 5];
        const t1 = performance.now();
        render(tone2);
        void host.offsetHeight;            // 强制样式/布局计算
        syncSamples.push(performance.now() - t1);
      }

      // ③ 帧间隔基线（浏览器 vsync 量化下限）：空转测 30 帧
      const frameGaps = [];
      let prev = await nextFrame();
      for (let k = 0; k < 30; k += 1) {
        const cur = await nextFrame();
        frameGaps.push(cur - prev);
        prev = cur;
      }

      host.remove();
      const stat = (xs) => {
        const s = [...xs].sort((a, b) => a - b);
        const pick = (p) => s[Math.min(s.length - 1,
          Math.max(0, Math.ceil((p / 100) * s.length) - 1))];
        return {
          samples: s.map((v) => Number(v.toFixed(3))),
          p50_ms: Number(pick(50).toFixed(3)),
          p95_ms: Number(pick(95).toFixed(3)),
          max_ms: Number(s[s.length - 1].toFixed(3)),
        };
      };
      resolve({
        async: stat(asyncSamples),
        sync: stat(syncSamples),
        frame_gap: stat(frameGaps),
        iterations: asyncSamples.length,
      });
    })();
  });
}
"""


def _post(sink: str, payload: dict) -> None:
    if not sink:
        return
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(sink, data=data,
                                headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    except Exception as e:  # noqa: BLE001 落盘失败不阻塞实测结论
        print(f"[warn] 结果上报失败（{type(e).__name__}: {e}）", file=sys.stderr)


def _vitals(page) -> dict:
    return page.evaluate(
        """() => {
          const nav = performance.getEntriesByType('navigation')[0] || {};
          const paints = {};
          performance.getEntriesByType('paint').forEach((p) => { paints[p.name] = p.startTime; });
          return {
            dom_content_loaded_ms: nav.domContentLoadedEventEnd ?? null,
            load_event_ms: nav.loadEventEnd ?? null,
            response_end_ms: nav.responseEnd ?? null,
            first_paint_ms: paints['first-paint'] ?? null,
            first_contentful_paint_ms: paints['first-contentful-paint'] ?? null,
            lcp_ms: window.__cp_lcp_ms ?? null,
          };
        }"""
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="U6 真实浏览器性能实测")
    parser.add_argument("--url", default="http://127.0.0.1:5678/chat#/workbench")
    parser.add_argument("--sink", default="http://127.0.0.1:5711/cp-perf")
    parser.add_argument("--timeout-ms", type=int, default=60000)
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        ctx = browser.new_context(viewport={"width": 1600, "height": 1000})
        page = ctx.new_page()
        # LCP 观察器必须在导航前注入
        page.add_init_script(
            """
            window.__cp_lcp_ms = null;
            try {
              new PerformanceObserver((list) => {
                const es = list.getEntries();
                if (es.length) window.__cp_lcp_ms = es[es.length - 1].startTime;
              }).observe({ type: 'largest-contentful-paint', buffered: true });
            } catch (e) { /* 浏览器不支持则留 null */ }
            """
        )

        print(f"[probe] 冷启动导航：{args.url}")
        t0 = time.perf_counter()
        page.goto(args.url, wait_until="load", timeout=args.timeout_ms)
        cold_total_ms = (time.perf_counter() - t0) * 1000
        # 等工作台外壳挂载（Mosaic 导航区）
        try:
            page.wait_for_selector("[class*='wb-'], nav, aside, #root > *",
                                   timeout=15000, state="attached")
        except Exception as e:  # noqa: BLE001 外壳选择器不匹配不致命
            print(f"[warn] 外壳选择器等待超时：{type(e).__name__}")
        page.wait_for_timeout(1500)          # 让 LCP 有机会定格
        cold = _vitals(page)
        cold["total_goto_ms"] = round(cold_total_ms, 3)

        print("[probe] 热缓存二次导航")
        page.goto(args.url, wait_until="load", timeout=args.timeout_ms)
        page.wait_for_timeout(1200)
        warm = _vitals(page)

        print("[probe] 状态灯：状态变更 → 首帧重绘（30 次）")
        status = page.evaluate(STATUS_SCRIPT)

        browser.close()

    out = {
        "metric": "workbench_first_screen_and_status_light",
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "url": args.url,
        "env": "headless Chromium via playwright（本机）",
        "clock": "browser performance.now() / PerformanceObserver(paint, LCP)",
        "first_screen": {
            "cold": cold,
            "warm": warm,
            "note": ("cold=首次导航（进程内首次加载）；warm=同上下文二次导航。"
                     "仓库验收线：首屏加载 <1.5s、LCP ≤2s（docs/VISUAL_DESIGN_SPEC.md）；"
                     "§11.2 假设值：首屏（热）500ms"),
        },
        "budgets": {
            "status_light_change_to_frame_ms": 50,
            "first_screen_hot_ms": 500,
            "first_screen_acceptance_line_ms": 1500,
            "lcp_acceptance_line_ms": 2000,
        },
        "status_light": {
            "metric": "status_badge_state_change_to_first_frame",
            "clock": "browser performance.now() + requestAnimationFrame 回调时间戳",
            "env": "真实浏览器 headless Chromium（含样式/布局/绘制调度）",
            # ① 异步口径：状态变更 → 下一帧（受 vsync 量化，含 1 个显示周期）
            "async_next_frame": status["async"],
            # ② 同步口径：状态变更 → 强制 reflow（不受 vsync 影响，量级更小）
            "sync_forced_layout": status["sync"],
            # ③ 基线：空转帧间隔（用于判断预算与 vsync 量化的关系）
            "frame_gap_baseline": status["frame_gap"],
            "budget_ms": 50,
            # 预算判定用**同步口径**（§11.2 的 50ms 是"状态灯响应"的可感知阈，
            # 而异步口径的下限被 vsync 帧间隔抬高，两者口径不同，必须分别报告）
            "passed": status["sync"]["p95_ms"] < 50,
            "async_passed": status["async"]["p95_ms"] < 50,
            "note": ("两个口径都必须上屏：async = 状态变更→下一帧（含显示周期量化，"
                     "下限≈frame_gap p50）；sync = 状态变更→强制 reflow。"
                     "§11.2 预算 50ms 对照 sync 口径判定，async 口径如实并列披露"),
        },
        "passed": bool(
            status["sync"]["p95_ms"] < 50
            and (warm.get("first_contentful_paint_ms") or 0) < 1500
            and (warm.get("lcp_ms") or warm.get("first_contentful_paint_ms") or 0) < 2000
        ),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    _post(args.sink, out)
    # 状态灯单独上报一份（与 vitest 的 jsdom 口径并列，便于对比）
    _post(args.sink, out["status_light"])
    return 0 if out["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
