/**
 * 治理看板 —— 五个只读治理视图合成一页（2026-11-21 导航重组）
 * ------------------------------------------------------------------
 * 合并前是「治理与运营」桶里的五个独立叶子：消化流水线 / 能力地图 / 成本 ROI / 自愈事故 / 路由覆盖。
 * 它们全部只读、共用同一套治理口径（MetricValue / 缺位渲染"—"），分开挂只会让人在桶里滚。
 *
 * 为什么没有把「业务看板」也并进来：它自带两个页签（业务指标 / 系统仪表盘）且体量大，
 * 并进来会变成「页签里的页签」（本仓明确避免的形态）⇒ 业务看板保持独立叶子，同放在观测·治理小节。
 *
 * 深链：?panel=governance/board&tab=pipeline|capabilities|roi|incidents|route-coverage；
 * 五个旧叶子 key 由 hubNav 的 LEGACY_NAV_KEY_MAP 落到本页，并由 BOOT_PANEL 选初始 Tab。
 */
import { useState } from 'react'
import { GitBranch, Layers, Route, Siren, TrendingUp } from 'lucide-react'
import { PageTabsHeader } from '../components/ui'
import { Tabs } from '@/components/ui'
import { BOOT_PANEL } from '@/workbench/hubNav'
import { legacyTab, readUrlTab, writeUrlTab } from '@/lib/hubTab'
import {
  CapabilityMapPanel,
  DigestionPipelinePanel,
  IncidentsPanel,
  RoiPanel,
} from './index'
import RouteCoveragePanel from './route-coverage'

type Tab = 'pipeline' | 'capabilities' | 'roi' | 'incidents' | 'route-coverage'

const TABS: Array<{ id: Tab; label: string; icon: typeof Layers; hint: string }> = [
  { id: 'pipeline', label: '消化流水线', icon: GitBranch, hint: '摄入 → 蒸馏 → 能力：各阶段卡在哪（只读）' },
  { id: 'capabilities', label: '能力地图', icon: Layers, hint: '能力分布与阶段/风险切片（只读）' },
  { id: 'roi', label: '成本 ROI', icon: TrendingUp, hint: 'LLM 成本与产出的 30 日对比（只读）' },
  { id: 'incidents', label: '自愈事故', icon: Siren, hint: '自愈事件与六要素完整性（只读）' },
  { id: 'route-coverage', label: '路由覆盖', icon: Route, hint: '构建期审计：端点 ↔ UI 覆盖与棘轮（只读）' },
]

const LEGACY: Record<string, Tab> = {
  'governance/pipeline': 'pipeline',
  'governance/capabilities': 'capabilities',
  'governance/roi': 'roi',
  'governance/incidents': 'incidents',
  'governance/route-coverage': 'route-coverage',
}

function isTab(v: string | null): v is Tab {
  return v === 'pipeline' || v === 'capabilities' || v === 'roi' || v === 'incidents'
    || v === 'route-coverage'
}

export default function GovernanceBoard() {
  const [tab, setTab] = useState<Tab>(
    () => readUrlTab(isTab) ?? legacyTab(BOOT_PANEL, LEGACY) ?? 'pipeline',
  )
  const active = TABS.find((t) => t.id === tab) ?? TABS[0]

  return (
    <div className="flex h-full min-h-0 flex-col bg-slate-950">
      <PageTabsHeader
        title="治理看板"
        hint={active.hint}
        tabs={
          <Tabs
            variant="folder"
            className="-mb-px"
            aria-label="治理看板"
            value={tab}
            onChange={(v) => {
              setTab(v as Tab)
              writeUrlTab(v)
            }}
            items={TABS.map((t) => {
              const Icon = t.icon
              return { value: t.id, label: <><Icon size={13} />{t.label}</> }
            })}
          />
        }
      />
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
        {tab === 'pipeline' && <DigestionPipelinePanel />}
        {tab === 'capabilities' && <CapabilityMapPanel />}
        {tab === 'roi' && <RoiPanel />}
        {tab === 'incidents' && <IncidentsPanel />}
        {tab === 'route-coverage' && <RouteCoveragePanel />}
      </div>
    </div>
  )
}
