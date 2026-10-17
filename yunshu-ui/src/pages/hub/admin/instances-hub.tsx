/**
 * 系统管理 · 实例与搜索性能 —— 两个子页共用一个导航叶子
 * ------------------------------------------------------------------
 * 为什么并成一页两 Tab：搜索实例与搜索性能是同一件事的两面 ——
 * 一边配"用哪个引擎"，一边看"这个引擎跑得怎么样"。
 * 分开两条导航会让"配完引擎去哪看效果"变成一次迷路。
 */
import { useState } from 'react'
import { Activity, Cpu } from 'lucide-react'
import InstancesPanel from './instances'
import SearchPerformance from './search-perf'

type Tab = 'instances' | 'perf'

export default function InstancesHub() {
  const [tab, setTab] = useState<Tab>('instances')
  return (
    <div className="flex h-full min-h-0 flex-col bg-slate-950">
      <div className="flex items-center gap-1.5 border-b border-slate-800 px-4 py-2">
        {([
          ['instances', '实例管理', Cpu],
          ['perf', '搜索性能', Activity],
        ] as const).map(([id, label, Icon]) => (
          <button
            key={id}
            type="button"
            onClick={() => setTab(id)}
            aria-selected={tab === id}
            className={'flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs transition-colors ' + (
              tab === id
                ? 'bg-cyan-500/15 font-medium text-cyan-300'
                : 'text-slate-400 hover:bg-slate-800/60 hover:text-slate-200'
            )}
          >
            <Icon size={13} />{label}
          </button>
        ))}
        <span className="ml-auto font-mono text-[10px] text-slate-600">
          {tab === 'instances' ? 'LLM / 搜索实例：建、设默认、测连通、删' : '搜索性能：启停轮询、手动检测、成功率与历史'}
        </span>
      </div>
      <div className="min-h-0 flex-1">
        {tab === 'instances' ? <InstancesPanel /> : <SearchPerformance />}
      </div>
    </div>
  )
}
