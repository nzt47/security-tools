/**
 * 消息与通道 —— 通知列表 + 通道配置 两个 Tab（2026-11-21 导航重组）
 * ------------------------------------------------------------------
 * 原「消息中心」把通知列表（只读）与通道配置（webhook / SMTP / Slack / Discord / Telegram，
 * 可写）上下堆在一页 980 行里：找通知要滚过配置，配通道要滚过通知。
 * 现在按「看 / 改」拆成两个 Tab，叶子仍是一个（不增加侧栏项）。
 *
 * 深链：?panel=admin/notifications&tab=notify|channels；旧 key 同名，无需迁移。
 */
import { useState } from 'react'
import { Bell, Send } from 'lucide-react'
import { PageTabsHeader } from '../components/ui'
import { Tabs } from '@/components/ui'
import { BOOT_PANEL } from '@/workbench/hubNav'
import { legacyTab, readUrlTab, writeUrlTab } from '@/lib/hubTab'
import AdminGuard from './AdminGuard'
import NotificationCenter from '@/pages/system/NotificationCenter'
import HubChannelsPanel from './messages-channels'

type Tab = 'notify' | 'channels'

const TABS: Array<{ id: Tab; label: string; icon: typeof Bell; hint: string }> = [
  { id: 'notify', label: '通知', icon: Bell, hint: '通知列表与已读状态（只读）' },
  { id: 'channels', label: '通道', icon: Send, hint: 'webhook / SMTP / Slack / Discord / Telegram：配置与启停（可改）' },
]

const LEGACY: Record<string, Tab> = {}

function isTab(v: string | null): v is Tab {
  return v === 'notify' || v === 'channels'
}

export default function NotificationsPage() {
  const [tab, setTab] = useState<Tab>(
    () => readUrlTab(isTab) ?? legacyTab(BOOT_PANEL, LEGACY) ?? 'notify',
  )
  const active = TABS.find((t) => t.id === tab) ?? TABS[0]

  return (
    <div className="flex h-full min-h-0 flex-col bg-slate-950">
      <PageTabsHeader
        title="消息与通道"
        hint={active.hint}
        tabs={
          <Tabs
            variant="folder"
            className="-mb-px"
            aria-label="消息与通道"
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
        <AdminGuard>
          <div className="p-6">
            {tab === 'notify' && <NotificationCenter />}
            {tab === 'channels' && <HubChannelsPanel />}
          </div>
        </AdminGuard>
      </div>
    </div>
  )
}
