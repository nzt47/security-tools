/**
 * 页内 Tab 与 URL 的共用小工具（2026-11-21 导航重组）
 * ------------------------------------------------------------------
 * 重组后出现一批「一个叶子装多个 Tab」的页面（运行监控 / 日志与审计 / 治理看板 / 账号……），
 * 它们都要满足同一件事：**深链能直接打开某个 Tab**（?panel=<叶子>&tab=<tab>），
 * 并且旧深链（原来是独立叶子）落到合并页时要打开对应 Tab。
 *
 * 口径与 `pages/hub/tools/index.tsx` 的 readUrlTab/writeUrlTab 一致：
 *   · 读：先看 `window.location.search`，再看 hash 里的 query（本应用是 hash 路由，tab 在 hash 上）；
 *   · 写：replaceState 写回 hash 的 query，尽量保留 `#/workbench?panel=...` 的形状；
 *   · 非法 tab 返回 null，由调用方回退到默认 Tab（不渲染空白）。
 */

/** 从当前 URL 读 ?tab=；`isTab` 负责把字符串收窄成合法的 Tab 联合类型 */
export function readUrlTab<T extends string>(isTab: (value: string | null) => value is T): T | null {
  if (typeof window === 'undefined') return null
  const sources = [
    window.location.search.replace(/^\?/, ''),
    window.location.hash.split('?')[1] ?? '',
  ]
  for (const source of sources) {
    const value = new URLSearchParams(source).get('tab')
    if (isTab(value)) return value
  }
  return null
}

/** 把当前 Tab 写回 URL（replaceState；不产生历史记录） */
export function writeUrlTab(tab: string): void {
  if (typeof window === 'undefined') return
  try {
    const hash = window.location.hash || ''
    if (hash.includes('?')) {
      const [path, query = ''] = hash.split('?')
      const params = new URLSearchParams(query)
      if (params.get('tab') === tab) return
      params.set('tab', tab)
      window.history.replaceState({}, '', path + '?' + params.toString())
      return
    }
    const url = new URL(window.location.href)
    if (url.searchParams.get('tab') === tab) return
    url.searchParams.set('tab', tab)
    window.history.replaceState({}, '', url.toString())
  } catch {
    /* 无 history / 非法 URL 时只保留内存态 */
  }
}

/** 由「旧叶子 key」推到 Tab 的通用小工具：`map[BOOT_PANEL]` */
export function legacyTab<T extends string>(
  bootPanel: string,
  map: Record<string, T>,
): T | null {
  if (!bootPanel) return null
  const hit = map[bootPanel]
  return hit ?? null
}
