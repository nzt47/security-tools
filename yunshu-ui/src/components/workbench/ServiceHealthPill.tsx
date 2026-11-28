/**
 * ServiceHealthPill —— 顶栏「服务连接」指示器（2026-11-28，借鉴 DSH 连接恢复控件）
 * ------------------------------------------------------------------
 * 解决什么：后端重启 / 断网时，页面只会表现为"点了没反应 / 请求报错"，没有任何
 * 全局提示；部署或容器重启期间用户不知道是"服务在重启"还是"功能坏了"。
 *
 * 口径（与 DSH 外壳对齐，按云枢单用户场景收窄）：
 *   · 健康时**静默**（不占位、不闪）；只有「不可达」才出现黄的药丸；
 *   · 点击药丸 = 立即重试；重试期间显示「重新连接中…」，且**至少可见 800ms**（防闪烁）；
 *   · 恢复后显示绿「连接已恢复」**2 秒**再消失；
 *   · 浏览器离线（navigator.onLine=false）优先显示「网络已断开」，不发请求。
 *
 * 判据：唯一探针是免令牌的 `GET /api/health`（server_auth 白名单内），
 * 不新增路由/环境变量；探测失败只落到本地状态，不进日志、不弹窗。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, RefreshCw } from 'lucide-react'
import { Badge, Button } from '@/components/ui'
import { HEALTH } from '@/api/endpoints'
import { request } from '@/lib/apiClient'

/** 轮询间隔：30s（/api/health 是采集型探针，别打太勤；部署重启窗口内最迟 30s 内可见） */
const HEALTH_POLL_MS = 30_000
/** 「重新连接中…」最短可见时间（防闪烁） */
const MIN_CHECKING_MS = 800
/** 「连接已恢复」驻留时间 */
const RECOVERY_MS = 2_000

type HealthStatus = 'ok' | 'down' | 'checking' | 'recovered'

export interface ServiceHealthPillProps {
  /** 轮询间隔（测试可调） */
  pollMs?: number
  /** 「重新连接中」最短可见（测试可调） */
  minCheckingMs?: number
  /** 「已恢复」驻留（测试可调） */
  recoveryMs?: number
}

/**
 * 顶栏服务连接指示：正常时什么都不渲染。
 * @param props - 可选的时序参数（仅测试用）
 */
export default function ServiceHealthPill({
  pollMs = HEALTH_POLL_MS,
  minCheckingMs = MIN_CHECKING_MS,
  recoveryMs = RECOVERY_MS,
}: ServiceHealthPillProps) {
  const [status, setStatus] = useState<HealthStatus>('ok')
  const [offline, setOffline] = useState(() => typeof navigator !== 'undefined' && navigator.onLine === false)
  const statusRef = useRef<HealthStatus>('ok')
  statusRef.current = status
  /** 一次重试的起点（保证「重新连接中」最短可见） */
  const checkingSinceRef = useRef(0)
  /** 防卸载后 setState */
  const aliveRef = useRef(true)
  /** 同一时刻只允许一个探针（轮询与"回到前台"可能撞车） */
  const inflightRef = useRef(false)

  const probe = useCallback(async () => {
    if (inflightRef.current) return
    if (typeof navigator !== 'undefined' && navigator.onLine === false) return
    inflightRef.current = true
    checkingSinceRef.current = Date.now()
    try {
      await request(HEALTH)
      const elapsed = Date.now() - checkingSinceRef.current
      if (elapsed < minCheckingMs) {
        await new Promise((resolve) => window.setTimeout(resolve, minCheckingMs - elapsed))
      }
      if (!aliveRef.current) return
      // 只有"从不可达/重试中恢复"才需要绿色确认；一直健康就保持静默
      setStatus(statusRef.current === 'ok' ? 'ok' : 'recovered')
    } catch {
      if (!aliveRef.current) return
      setStatus('down')
    } finally {
      inflightRef.current = false
    }
  }, [minCheckingMs])

  // 轮询 + 页面回到前台补查
  useEffect(() => {
    aliveRef.current = true
    void probe()
    const timer = window.setInterval(() => { void probe() }, pollMs)
    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible') void probe()
    }
    document.addEventListener('visibilitychange', onVisibilityChange)
    return () => {
      aliveRef.current = false
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [probe, pollMs])

  // 浏览器在线状态
  useEffect(() => {
    const onOffline = () => setOffline(true)
    const onOnline = () => {
      setOffline(false)
      void probe()
    }
    window.addEventListener('offline', onOffline)
    window.addEventListener('online', onOnline)
    return () => {
      window.removeEventListener('offline', onOffline)
      window.removeEventListener('online', onOnline)
    }
  }, [probe])

  // 恢复提示驻留 2s 后回到静默
  useEffect(() => {
    if (status !== 'recovered') return
    const timer = window.setTimeout(() => setStatus('ok'), recoveryMs)
    return () => window.clearTimeout(timer)
  }, [status, recoveryMs])

  const retry = () => {
    setStatus('checking')
    void probe()
  }

  if (offline) {
    return (
      <span role="status" aria-live="polite">
        <Badge tone="warning" size="sm" dot>网络已断开</Badge>
      </span>
    )
  }

  if (status === 'down') {
    return (
      <span role="status" aria-live="polite">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={retry}
          className="text-warning"
          title="探测 GET /api/health 失败；点一下立即重试"
        >
          <AlertTriangle size={13} />
          服务不可达 · 重试
        </Button>
      </span>
    )
  }

  if (status === 'checking') {
    return (
      <span role="status" aria-live="polite">
        <Button type="button" variant="ghost" size="sm" loading title="正在重新探测服务健康">
          <RefreshCw size={13} />
          重新连接中…
        </Button>
      </span>
    )
  }

  if (status === 'recovered') {
    return (
      <span role="status" aria-live="polite">
        <Badge tone="success" size="sm">
          <CheckCircle2 size={11} className="mr-1 inline" aria-hidden />
          连接已恢复
        </Badge>
      </span>
    )
  }

  return null
}
