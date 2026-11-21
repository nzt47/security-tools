/**
 * 运行监控 —— 全景感知 + 系统拓扑 两个只读 Tab（2026-11-21 导航重组）
 * ------------------------------------------------------------------
 * 为什么合并：这两个页面都是「此刻这台机器/这套系统长什么样」的只读视图，原来是两个导航叶子，
 * 用户看一眼传感器再去看拓扑要来回切；合到一个叶子里，标题栏说明一次，Tab 切换即可。
 *
 * 深链：?panel=panorama/runtime&tab=sensors|topology；
 * 旧叶子 ?panel=panorama/sensors 与 ?panel=panorama/monitor 由 hubNav 的 LEGACY_NAV_KEY_MAP
 * 落到本页，并由 BOOT_PANEL 决定初始 Tab（沿用引擎页的做法）。
 */
import { useState } from 'react'
import { Activity, Network } from 'lucide-react'
import { PageTabsHeader } from '../components/ui'
import { Tabs } from '@/components/ui'
import { BOOT_PANEL } from '@/workbench/hubNav'
import { legacyTab, readUrlTab, writeUrlTab } from '@/lib/hubTab'
import PanoramaSensors from '@/pages/hub/panorama/sensors'
import PanoramaMonitor from '@/pages/hub/panorama/monitor'

type Tab = 'sensors' | 'topology'

const TABS: Array<{ id: Tab; label: string; icon: typeof Activity; hint: string }> = [
  { id: 'sensors', label: '全景感知', icon: Activity, hint: '传感器实时读数与健康指标（只读）' },
  { id: 'topology', label: '系统拓扑', icon: Network, hint: '模块状态、健康评分与按域明细（只读）' },
]

const LEGACY: Record<string, Tab> = {
  'panorama/sensors': 'sensors',
  'panorama/monitor': 'topology',
}

function isTab(v: string | null): v is Tab {
  return v === 'sensors' || v === 'topology'
}

export default function RuntimeMonitor() {
  const [tab, setTab] = useState<Tab>(
    () => readUrlTab(isTab) ?? legacyTab(BOOT_PANEL, LEGACY) ?? 'sensors',
  )
  const active = TABS.find((t) => t.id === tab) ?? TABS[0]

  return (
    <div className="flex h-full min-h-0 flex-col bg-slate-950">
      <PageTabsHeader
        title="运行监控"
        hint={active.hint}
        tabs={
          <Tabs
            variant="folder"
            className="-mb-px"
            aria-label="运行监控"
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
      <div className="min-h-0 flex-1 overflow-y-auto">
        {tab === 'sensors' ? <PanoramaSensors embedded /> : <PanoramaMonitor embedded />}
      </div>
    </div>
  )
}
