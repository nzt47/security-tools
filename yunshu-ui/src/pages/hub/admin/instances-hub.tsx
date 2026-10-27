/**
 * 系统管理 · 实例与搜索性能 · 网络配置 —— 三个子页共用一个导航叶子
 * ------------------------------------------------------------------
 * 为什么并成一页多 Tab：搜索实例与搜索性能是同一件事的两面 ——
 * 一边配"用哪个引擎"，一边看"这个引擎跑得怎么样"。
 * 分开两条导航会让"配完引擎去哪看效果"变成一次迷路。
 *
 * 2026-10-27 IA：网络配置（LLM / 搜索 / 代理 / 出网）并入本页第三 Tab ——
 * 它与「实例管理」是同一组系统级配置（谁能出网、走哪个端点），独立叶子造成
 * 侧栏重复与"配完实例还要再找一个入口配网络"。旧深链 `?panel=network` 仍有效：
 * 经 LEGACY_NAV_KEY_MAP 落到本页，并由下面的 initialTab 识别 `panel=network`。
 */
import { useState } from 'react'
import { Activity, Cpu, Globe } from 'lucide-react'
import InstancesPanel from './instances'
import SearchPerformance from './search-perf'
import NetworkPage from '@/pages/hub/network'

export type InstancesTab = 'instances' | 'perf' | 'network'

const TABS = [
  ['instances', '实例管理', Cpu, 'LLM / 搜索实例：建、设默认、测连通、删'],
  ['perf', '搜索性能', Activity, '搜索性能：启停轮询、手动检测、成功率与历史'],
  ['network', '网络配置', Globe, '出网与代理：LLM / 搜索实例、网络开关、连通性'],
] as const

/** 读取初始 Tab：优先 ?tab=，其次兼容旧深链 ?panel=network */
export function initialTab(search: string = typeof window !== 'undefined' ? window.location.search : ''): InstancesTab {
  const params = new URLSearchParams(search)
  const tab = params.get('tab')
  if (tab === 'instances' || tab === 'perf' || tab === 'network') return tab
  if (params.get('panel') === 'network') return 'network'
  return 'instances'
}

export default function InstancesHub() {
  const [tab, setTab] = useState<InstancesTab>(() => initialTab())
  const active = TABS.find(([id]) => id === tab) ?? TABS[0]
  return (
    <div className="flex h-full min-h-0 flex-col bg-slate-950">
      <div className="flex items-center gap-1.5 border-b border-slate-800 px-4 py-2">
        {TABS.map(([id, label, Icon]) => (
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
        <span className="ml-auto font-mono text-[10px] text-slate-600">{active[3]}</span>
      </div>
      <div className="min-h-0 flex-1">
        {tab === 'instances' && <InstancesPanel />}
        {tab === 'perf' && <SearchPerformance />}
        {tab === 'network' && <NetworkPage />}
      </div>
    </div>
  )
}
