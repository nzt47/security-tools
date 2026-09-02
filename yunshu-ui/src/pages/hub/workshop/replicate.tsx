/**
 * 装配车间 —— 系统自我复制 / 独立开发模式
 * 说明页：展示复制与热更新能力规划。
 */
import { Copy, RefreshCw, FlaskConical } from 'lucide-react'
import { Card, PageHeader } from '../components/ui'

export default function WorkshopReplicate() {
  return (
    <div className="p-6">
      <PageHeader title="系统自我复制" description="装配车间 —— 独立开发模式与热更新" />
      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="系统自我复制">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-cyan-500/15 text-cyan-400"><Copy size={18} /></div>
            <p className="text-sm leading-6 text-slate-400">
              复制当前系统配置、记忆与能力集到新的分身实例，实现"系统克隆"。
            </p>
          </div>
        </Card>

        <Card title="独立开发工具热更新">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-emerald-500/15 text-emerald-400"><RefreshCw size={18} /></div>
            <p className="text-sm leading-6 text-slate-400">
              单独开发工具/技能时，经插件目录热加载（<code className="rounded bg-slate-800 px-1 text-xs text-cyan-400">/api/plugins/reload</code>）
              无需重启进程。
            </p>
          </div>
        </Card>

        <Card title="独立开发模式">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-blue-500/15 text-blue-400"><FlaskConical size={18} /></div>
            <p className="text-sm leading-6 text-slate-400">
              为单独工具/技能提供隔离开发环境（沙盒），支持独立测试与调试后并入主系统。
            </p>
          </div>
        </Card>
      </div>

      <Card className="mt-4" title="能力对照">
        <ul className="space-y-2 text-sm text-slate-400">
          <li className="flex items-center gap-2"><span className="h-1.5 w-1.5 rounded-full bg-cyan-500" /> 分身创建与组装：<code className="text-cyan-400">/api/subagent/create</code>（已实现，见"分身创建与组装"页）</li>
          <li className="flex items-center gap-2"><span className="h-1.5 w-1.5 rounded-full bg-cyan-500" /> 内存管理与 MCP 规划记忆：<code className="text-cyan-400">/api/memory/*</code>、<code className="text-cyan-400">/api/mcp/services</code></li>
          <li className="flex items-center gap-2"><span className="h-1.5 w-1.5 rounded-full bg-cyan-500" /> 热更新机制：<code className="text-cyan-400">/api/plugins/reload</code>（插件目录扫描）</li>
          <li className="flex items-center gap-2"><span className="h-1.5 w-1.5 rounded-full bg-cyan-500" /> 系统自我复制：复用分身能力 + 资产管理备份/恢复</li>
        </ul>
      </Card>
    </div>
  )
}
