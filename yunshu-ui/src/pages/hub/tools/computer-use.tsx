/**
 * Computer Use —— 浏览器/屏幕自动化
 * 数据源：/api/browser/*（navigate/screenshot/close）
 */
import { useState } from 'react'
import { Monitor, Navigation } from 'lucide-react'
import { Card, ErrorBox, PageHeader, hubPost } from '../components/ui'

export default function ToolsComputerUse() {
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState('')

  const navigate = async () => {
    if (!url.trim() || busy) return
    setBusy(true)
    setResult('')
    try {
      const r = await hubPost('/api/browser/navigate', { url: url.trim() })
      setResult(`导航结果：${JSON.stringify(r).slice(0, 200)}`)
    } catch (e) {
      setResult(`失败：${e instanceof Error ? e.message : e}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="p-6">
      <PageHeader title="Computer Use" description="浏览器自动化与屏幕操作" />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="浏览器导航">
          <div className="flex gap-2">
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && navigate()}
              placeholder="输入 URL（如 https://example.com）"
              className="flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-none focus:border-cyan-600"
            />
            <button onClick={navigate} disabled={busy} className="flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-500 disabled:opacity-40">
              <Navigation size={14} /> {busy ? '执行中…' : '导航'}
            </button>
          </div>
          {result && <div className="mt-3 rounded-lg border border-slate-800 bg-slate-900 px-3 py-2 text-sm text-cyan-400">{result}</div>}
          <div className="mt-4 flex items-center gap-2 rounded-lg border border-slate-800 bg-slate-900/60 px-4 py-3 text-sm text-slate-500">
            <Monitor size={16} className="text-cyan-500" />
            后端 /api/browser/* 由 system_tools 插件提供；Computer Use 专用接口（/api/computer-use/*）为占位。
          </div>
        </Card>

        <Card title="功能规划">
          <ul className="space-y-2 text-sm text-slate-400">
            <li className="flex items-center gap-2"><span className="h-1.5 w-1.5 rounded-full bg-cyan-500" /> 浏览器导航与页面截图</li>
            <li className="flex items-center gap-2"><span className="h-1.5 w-1.5 rounded-full bg-cyan-500" /> 屏幕区域识别与点击模拟</li>
            <li className="flex items-center gap-2"><span className="h-1.5 w-1.5 rounded-full bg-cyan-500" /> 表单填写与交互自动化</li>
            <li className="flex items-center gap-2"><span className="h-1.5 w-1.5 rounded-full bg-cyan-500" /> 多步任务编排（规划 → 执行 → 校验）</li>
          </ul>
        </Card>
      </div>
    </div>
  )
}
