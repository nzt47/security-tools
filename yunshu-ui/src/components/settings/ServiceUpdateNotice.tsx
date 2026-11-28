/**
 * ServiceUpdateNotice —— 「服务已更新，刷新以加载新版本」提示（2026-11-28）
 * ------------------------------------------------------------------
 * 触发条件只有一条：**页面正在跑的 bundle 名 ≠ 服务端当前 bundle 名**。
 * 云枢一天会部署多次，旧标签页会一直执行旧代码却打新后端，症状是
 * 「改了没用 / 行为诡异」；此前没有任何提示（构建戳只写时间，不比对）。
 *
 * 边界：
 *  · 开发模式（无 index-*.js）不轮询、不提示；
 *  · 探测失败静默（版本接口 401/网络断都不该打断使用）；
 *  · 同一服务端版本只提示一次：点「稍后」后，仅当服务端又换成新 bundle 才再次出现。
 */
import { useEffect, useMemo, useState } from 'react'
import { Button, ModalBase, Notice } from '@/components/ui'
import { fetchServiceVersion, isPageStale, pageBundle, VERSION_POLL_MS } from './serviceVersion'

export default function ServiceUpdateNotice() {
  const page = useMemo(() => pageBundle(), [])
  const [serverBundle, setServerBundle] = useState<string | null>(null)
  const [dismissed, setDismissed] = useState<string | null>(null)

  useEffect(() => {
    if (!page) return
    let alive = true
    const check = async () => {
      try {
        const data = await fetchServiceVersion()
        if (alive) setServerBundle(data.frontend?.bundle ?? null)
      } catch {
        // 版本探测失败不影响使用：保留上一次读数，下一轮再试
      }
    }
    void check()
    const timer = window.setInterval(() => { void check() }, VERSION_POLL_MS)
    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible') void check()
    }
    document.addEventListener('visibilitychange', onVisibilityChange)
    return () => {
      alive = false
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [page])

  const stale = isPageStale(page, serverBundle)
  const open = stale && dismissed !== serverBundle

  const dismiss = () => setDismissed(serverBundle)

  return (
    <ModalBase
      open={open}
      onClose={dismiss}
      title="服务已更新"
      width="max-w-lg"
      closeButton
      testId="service-update-notice"
      footer={(
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={dismiss}>稍后</Button>
          <Button type="button" variant="primary" onClick={() => { window.location.reload() }}>
            立即刷新
          </Button>
        </div>
      )}
    >
      <div className="space-y-3">
        <Notice tone="warning" size="sm" icon={null}>
          后端已发布新版本，当前页面仍在执行旧代码。刷新后才会加载新版本。
        </Notice>
        <div className="space-y-1.5 text-caption text-muted-foreground">
          <div className="flex items-center justify-between gap-4">
            <span>当前页面</span>
            <span className="font-mono text-foreground">{page ?? '—'}</span>
          </div>
          <div className="flex items-center justify-between gap-4">
            <span>服务端</span>
            <span className="font-mono text-foreground">{serverBundle ?? '—'}</span>
          </div>
        </div>
        <p className="text-caption text-muted-foreground">
          正在进行的输入/滚动会丢失；点「稍后」只影响本标签页本次会话，服务端再次更新时会重新提示。
        </p>
      </div>
    </ModalBase>
  )
}
