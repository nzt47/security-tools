/**
 * Computer Use —— 浏览器/屏幕自动化 + 剪贴板
 * 数据源：/api/browser/*（navigate/screenshot/close）、/api/clipboard（GET/POST）
 *
 * 【分批接入】方案 docs/proposals/ui_information_architecture_review_20261011.md §7：
 * 把「该进 UI 却未进」的路由接进来 —— 本页落地 browser/screenshot、browser/close、
 * clipboard 三条（接入后它们自动从「该进未进」待办消失）。
 */
import { useState } from 'react'
import { Camera, Clipboard, ClipboardPaste, Loader2, Monitor, Navigation, X } from 'lucide-react'
import { Card, PageHeader, hubGet, hubPost } from '../components/ui'

import {
  BROWSER_NAVIGATE,
  BROWSER_SCREENSHOT,
  BROWSER_CLOSE,
  CLIPBOARD,
} from '@/api/endpoints';

export default function ToolsComputerUse() {
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState('')

  const [shot, setShot] = useState('')
  const [busyShot, setBusyShot] = useState(false)

  const [clip, setClip] = useState('')
  const [clipDraft, setClipDraft] = useState('')
  const [busyClip, setBusyClip] = useState(false)

  const navigate = async () => {
    if (!url.trim() || busy) return
    setBusy(true)
    setResult('')
    try {
      const r = await hubPost(BROWSER_NAVIGATE, { url: url.trim() })
      setResult(`导航结果：${JSON.stringify(r).slice(0, 200)}`)
    } catch (e) {
      setResult(`失败：${e instanceof Error ? e.message : e}`)
    } finally {
      setBusy(false)
    }
  }

  const screenshot = async () => {
    if (busyShot) return
    setBusyShot(true)
    setResult('')
    try {
      const r = await hubGet<{ ok?: boolean; screenshot_base64?: string; error?: string }>(BROWSER_SCREENSHOT)
      if (r && r.ok === false) {
        setShot('')
        setResult(`截图失败：${r.error ?? '未知错误'}`)
        return
      }
      const b64 = r?.screenshot_base64 ?? ''
      setShot(b64)
      setResult(b64 ? `截图成功（${b64.length} 字符 base64 PNG）` : '后端未返回截图数据')
    } catch (e) {
      setResult(`截图失败：${e instanceof Error ? e.message : e}`)
    } finally {
      setBusyShot(false)
    }
  }

  const closeBrowser = async () => {
    setResult('')
    try {
      await hubPost(BROWSER_CLOSE, {})
      setShot('')
      setResult('浏览器已关闭')
    } catch (e) {
      setResult(`关闭失败：${e instanceof Error ? e.message : e}`)
    }
  }

  const readClipboard = async () => {
    if (busyClip) return
    setBusyClip(true)
    setResult('')
    try {
      const r = await hubGet<{ ok?: boolean; content?: string; error?: string }>(CLIPBOARD)
      if (r && r.ok === false) {
        setResult(`剪贴板读取失败：${r.error ?? '未知错误'}`)
        return
      }
      setClip(r?.content ?? '')
    } catch (e) {
      setResult(`剪贴板读取失败：${e instanceof Error ? e.message : e}`)
    } finally {
      setBusyClip(false)
    }
  }

  const writeClipboard = async () => {
    if (busyClip) return
    setBusyClip(true)
    setResult('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string }>(CLIPBOARD, { text: clipDraft })
      if (r && r.ok === false) {
        setResult(`剪贴板写入失败：${r.error ?? '未知错误'}`)
        return
      }
      setResult('已写入剪贴板')
    } catch (e) {
      setResult(`剪贴板写入失败：${e instanceof Error ? e.message : e}`)
    } finally {
      setBusyClip(false)
    }
  }

  return (
    <div className="p-6">
      <PageHeader title="Computer Use" description="浏览器自动化与屏幕操作 / 剪贴板" />

      {result && <div className="mb-3 rounded-lg border border-slate-800 bg-slate-900 px-3 py-2 text-sm text-cyan-400">{result}</div>}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="浏览器导航 / 截图">
          <div className="flex gap-2">
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && navigate()}
              placeholder="输入 URL（如 https://example.com）"
              className="flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-hidden focus:border-cyan-600"
            />
            <button onClick={navigate} disabled={busy}
              className="flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-500 disabled:opacity-40">
              {busy ? <Loader2 size={14} className="animate-spin" /> : <Navigation size={14} />} {busy ? '执行中…' : '导航'}
            </button>
          </div>
          <div className="mt-2 flex gap-2">
            <button onClick={screenshot} disabled={busyShot} title="GET /api/browser/screenshot"
              className="flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800 disabled:opacity-40">
              {busyShot ? <Loader2 size={12} className="animate-spin" /> : <Camera size={12} />} 截取页面
            </button>
            <button onClick={closeBrowser} title="POST /api/browser/close"
              className="flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800">
              <X size={12} /> 关闭浏览器
            </button>
          </div>
          {shot && (
            <div className="mt-3" data-testid="browser-screenshot">
              <img alt="页面截图" src={`data:image/png;base64,${shot}`} className="max-h-64 w-full rounded-lg border border-slate-800 object-contain" />
            </div>
          )}
          <div className="mt-4 flex items-center gap-2 rounded-lg border border-slate-800 bg-slate-900/60 px-4 py-3 text-sm text-slate-500">
            <Monitor size={16} className="text-cyan-500" />
            后端 /api/browser/* 由 system_tools 提供（导航/截图/关闭）；截图需先导航。
          </div>
        </Card>

        <Card title="剪贴板">
          <div className="flex gap-2">
            <button onClick={readClipboard} disabled={busyClip}
              className="flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-300 hover:bg-slate-800 disabled:opacity-40">
              {busyClip ? <Loader2 size={14} className="animate-spin" /> : <Clipboard size={14} />} 读取剪贴板
            </button>
          </div>
          <div className="mt-3" data-testid="clipboard-content">
            {clip ? (
              <pre className="max-h-40 overflow-auto rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-xs text-slate-300 whitespace-pre-wrap">{clip}</pre>
            ) : (
              <div className="text-xs text-slate-500">（未读取 / 剪贴板为空）</div>
            )}
          </div>
          <div className="mt-3 flex flex-col gap-2">
            <textarea
              value={clipDraft}
              onChange={(e) => setClipDraft(e.target.value)}
              placeholder="要写入剪贴板的文本"
              aria-label="剪贴板写入内容"
              className="h-20 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-hidden focus:border-cyan-600"
            />
            <button onClick={writeClipboard} disabled={busyClip || !clipDraft}
              className="flex items-center justify-center gap-1.5 rounded-lg bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-500 disabled:opacity-40">
              <ClipboardPaste size={14} /> 写入剪贴板
            </button>
          </div>
        </Card>
      </div>
    </div>
  )
}
