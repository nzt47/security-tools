/**
 * AboutSection —— 通用设置 · 关于（只读版本读数，2026-11-28）
 * ------------------------------------------------------------------
 * 数据全部来自既有 GET /api/version（agent/build_info.py），本组件不新增
 * 任何路由、环境变量或持久化键；取不到就如实显示错误，不假装"同版"。
 *
 * 【为什么不上「工作区有未提交改动」徽章】backend.dirty 把 data/ 运行期脏文件
 * 与未跟踪噪声一起算进去，本机恒为 true —— 常亮且误导（看起来像"代码没提交"）。
 * dirty_files 里混着运行期状态，无法在前端可靠区分，故整条不展示。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { Button, Notice } from '@/components/ui'
import {
  fetchServiceVersion,
  formatIso,
  isPageStale,
  pageBundle,
  shortCommit,
  type ServiceVersion,
} from './serviceVersion'

/** 单行「标签 + 值」；值可以是徽章/等宽文本等节点 */
function InfoRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-6 py-2">
      <span className="shrink-0 text-caption text-muted-foreground">{label}</span>
      <span className="min-w-0 break-all text-right text-caption text-foreground">{children}</span>
    </div>
  )
}

export default function AboutSection() {
  const [version, setVersion] = useState<ServiceVersion | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const page = useMemo(() => pageBundle(), [])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setVersion(await fetchServiceVersion())
      setError('')
    } catch (reason: unknown) {
      setVersion(null)
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const serverBundle = version?.frontend?.bundle ?? null
  const stale = isPageStale(page, serverBundle)
  const pageBuild = typeof __YUNSHU_BUILD__ === 'string' ? __YUNSHU_BUILD__ : ''

  return (
    <div className="space-y-3">
      {error ? (
        <Notice tone="warning" size="sm" icon={null}>
          未取到服务版本：{error}
        </Notice>
      ) : loading && !version ? (
        <Notice tone="info" size="sm" icon={null}>正在读取服务版本…</Notice>
      ) : stale ? (
        <Notice tone="warning" size="sm" icon={null}>
          当前页面落后于服务端（页面跑 {page}，服务端已是 {serverBundle}）——刷新后加载新版本。
        </Notice>
      ) : page && serverBundle ? (
        <Notice tone="success" size="sm" icon={null}>
          页面与服务端同版（{serverBundle}）。
        </Notice>
      ) : (
        <Notice tone="info" size="sm" icon={null}>
          开发模式不比对版本（页面没有 index-*.js 构建产物）。
        </Notice>
      )}

      <div className="divide-y divide-border rounded-md border border-border px-3">
        <InfoRow label="页面构建">{pageBuild ? formatIso(pageBuild) : '—'}</InfoRow>
        <InfoRow label="页面 bundle">
          <span className="font-mono">{page ?? '开发模式（无 bundle 戳）'}</span>
        </InfoRow>
        <InfoRow label="服务端 commit">
          <span className="font-mono">{shortCommit(version?.backend?.commit)}</span>
        </InfoRow>
        <InfoRow label="提交说明">{version?.backend?.commit_subject || '—'}</InfoRow>
        <InfoRow label="提交时间">{formatIso(version?.backend?.committed_at)}</InfoRow>
        <InfoRow label="服务端 bundle">
          <span className="font-mono">{serverBundle ?? '—'}</span>
        </InfoRow>
        <InfoRow label="前端构建于">{formatIso(version?.frontend?.built_at)}</InfoRow>
        <InfoRow label="前端构建 commit">
          <span className="font-mono">{shortCommit(version?.frontend?.build_commit)}</span>
        </InfoRow>
        <InfoRow label="服务进程">
          pid {version?.process?.pid ?? '—'} · Python {version?.process?.python ?? '—'}
        </InfoRow>
        <InfoRow label="服务启动于">{formatIso(version?.process?.started_at)}</InfoRow>
      </div>

      <div className="flex items-center justify-between gap-3">
        <span className="text-caption text-muted-foreground">
          版本读数只读，来自 GET /api/version；刷新页面用 Ctrl+F5。
        </span>
        <Button type="button" variant="ghost" size="sm" onClick={() => { void load() }} loading={loading}>
          <RefreshCw size={13} />
          检查更新
        </Button>
      </div>
    </div>
  )
}
