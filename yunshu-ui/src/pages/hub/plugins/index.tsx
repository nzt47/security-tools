/**
 * 插件与扩展 —— 工作台「会话与配置」桶下的单一页面
 * ------------------------------------------------------------------
 * 原为「清单与配置 / 扩展呈现」两个 Tab（UI IA 整合阶段 1.2）。2026-10-15 合并为一页：
 *   · 两者同读 /api/plugins，「扩展呈现」的 schema 配置部分与「清单与配置」完全重复；
 *   · 「扩展呈现」唯一独有能力是自动挂载插件自带 UI（client_slot），已并入清单页的
 *     插件卡片（见 ../plugin-manage.tsx），extensions.tsx 随之删除；
 *   · 仍只有一个导航入口（hubNav 的 components/plugins），禁止双入口。
 */
import { useState } from 'react'
import { Package, Puzzle } from 'lucide-react'
import PluginManage from '../plugin-manage'
import ExtensionsMarket from '../extensions/market'

type PlugTab = 'plugins' | 'market'

const TABS: { key: PlugTab; label: string; icon: typeof Puzzle; desc: string }[] = [
  { key: 'plugins', label: '插件清单', icon: Puzzle, desc: '/api/plugins 清单 + schema 配置 + 插件自带界面' },
  { key: 'market', label: '扩展市场', icon: Package, desc: '/api/extensions：技能 / MCP / 通道 / 插件包 —— 内置包与社区索引' },
]

/**
 * 同一导航项下的两个 Tab（保持「禁止双入口」）。
 *
 * 【为什么不是第二个导航项】两者都属于"装东西进来"的一面，但数据源完全不同：
 * 插件清单读 /api/plugins（我们自己的插件 manifest），扩展市场读 /api/extensions
 * （可安装扩展 + 社区索引）。合在一页、分开 Tab，用户找"装扩展"时不会迷路。
 */
export default function PluginsPage() {
  const [tab, setTab] = useState<PlugTab>('plugins')
  return (
    <div className="flex h-full min-h-0 flex-col bg-slate-950">
      <div className="flex items-center gap-1.5 border-b border-slate-800 px-4 py-2">
        {TABS.map((t) => {
          const Icon = t.icon
          return (
            <button
              key={t.key}
              type="button"
              onClick={() => setTab(t.key)}
              aria-selected={tab === t.key}
              title={t.desc}
              className={'flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs transition-colors ' + (
                tab === t.key
                  ? 'bg-cyan-500/15 font-medium text-cyan-300'
                  : 'text-slate-400 hover:bg-slate-800/60 hover:text-slate-200'
              )}
            >
              <Icon size={13} />{t.label}
            </button>
          )
        })}
        <span className="ml-auto hidden font-mono text-[10px] text-slate-600 md:inline">
          {TABS.find((t) => t.key === tab)?.desc}
        </span>
      </div>
      <div className="min-h-0 flex-1">
        {tab === 'plugins' ? <PluginManage /> : <ExtensionsMarket />}
      </div>
    </div>
  )
}
