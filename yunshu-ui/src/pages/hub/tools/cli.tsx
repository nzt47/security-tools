/**
 * CLI 软件 —— CLI 工具管理
 * 后端 /api/cli/* 为占位路由，页面展示可用系统工具（进程管理）与占位说明。
 */
import { useState } from 'react'
import { Terminal } from 'lucide-react'
import { Card, PageHeader, hubGet, unwrap } from '../components/ui'

interface ProcessInfo {
  pid: number
  name: string
  [k: string]: unknown
}

export default function ToolsCli() {
  const [procs, setProcs] = useState<ProcessInfo[]>([])
  const [loaded, setLoaded] = useState(false)

  const loadProcs = () => {
    hubGet('/api/process/list').then((r) => {
      const d = unwrap<ProcessInfo[]>(r as Record<string, unknown>) ?? []
      setProcs(d)
      setLoaded(true)
    }).catch(() => { setProcs([]); setLoaded(true) })
  }

  return (
    <div className="p-6">
      <PageHeader title="CLI 软件" description="命令行工具与系统进程管理" />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="系统进程">
          <div className="mb-3 flex items-center justify-between">
            <span className="text-xs text-slate-500">当前运行进程</span>
            <button onClick={loadProcs} className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800">
              {loaded ? '重新加载' : '加载进程'}
            </button>
          </div>
          {procs.length > 0 ? (
            <div className="max-h-96 space-y-1 overflow-y-auto font-mono text-xs">
              {procs.map((p, i) => (
                <div key={i} className="flex gap-3 rounded px-2 py-1 hover:bg-slate-800/40">
                  <span className="w-16 text-slate-500">{p.pid}</span>
                  <span className="text-slate-300">{String(p.name)}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="py-8 text-center text-sm text-slate-600">
              {loaded ? '未获取到进程信息' : '点击"加载进程"查看系统进程'}
            </div>
          )}
        </Card>
        <Card title="CLI 扩展">
          <p className="text-sm leading-6 text-slate-400">
            CLI 软件管理后端接口（<code className="rounded bg-slate-800 px-1.5 py-0.5 text-cyan-400">/api/cli/*</code>）当前为占位实现。
            后续可在此接入软件安装、版本管理与命令行工具注册能力。
          </p>
          <div className="mt-4 flex items-center gap-2 rounded-lg border border-slate-800 bg-slate-900/60 px-4 py-3 text-sm text-slate-500">
            <Terminal size={16} className="text-cyan-500" />
            规划能力：Chocolatey / pip / npm / GitHub Releases 集成
          </div>
        </Card>
      </div>
    </div>
  )
}
