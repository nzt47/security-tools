/**
 * 日志与审计 —— 五处「谁做了什么」收敛成一个叶子（2026-11-21 导航重组）
 * ------------------------------------------------------------------
 * 合并前分散在三个桶、五个叶子与面板：
 *   · 日志查看（全景看板）      → 实时日志（运行日志，只读）
 *   · 操作审计（系统管理）      → 操作审计（UI 审计中间件台账，只读）
 *   · 系统日志（系统管理）      → 系统日志（应用日志，只读）
 *   · 审计导出（全景看板）      → 审计链（不可篡改链 + CSV 导出）
 *   · 数据导出（系统管理）      → 数据导出（业务表 CSV/JSON 分片）
 * 五个都在回答「发生过什么 / 谁改的」，只是数据源不同 ⇒ 同一页 Tab，不再跨桶找。
 *
 * 深链：?panel=observability/logs&tab=live|ops-audit|syslog|chain|export；
 * 五个旧叶子 key 由 hubNav 的 LEGACY_NAV_KEY_MAP 落到本页，并由 BOOT_PANEL 选初始 Tab。
 * 需要管理后台登录态的 Tab（操作审计 / 系统日志 / 数据导出）各自套 AdminGuard，
 * 只读的实时日志与审计链不套（无 login 提示噪音）。
 */
import { useState } from 'react'
import { FileText, History, ScrollText, ShieldCheck, FileDown } from 'lucide-react'
import { PageTabsHeader } from '../components/ui'
import { Tabs } from '@/components/ui'
import { BOOT_PANEL } from '@/workbench/hubNav'
import { legacyTab, readUrlTab, writeUrlTab } from '@/lib/hubTab'
import AdminGuard from '@/pages/hub/admin/AdminGuard'
import PanoramaLogs from '@/pages/hub/panorama/logs'
import AuditList from '@/pages/system/AuditList'
import SystemLog from '@/pages/system/SystemLog'
import DataExport from '@/pages/Export'
import { AuditExportPanel } from '@/pages/hub/governance'

type Tab = 'live' | 'ops-audit' | 'syslog' | 'chain' | 'export'

const TABS: Array<{ id: Tab; label: string; icon: typeof FileText; hint: string }> = [
  { id: 'live', label: '实时日志', icon: FileText, hint: '结构化运行日志（/api/observability/logs，只读）' },
  { id: 'ops-audit', label: '操作审计', icon: History, hint: 'UI 审计中间件台账：谁在什么时候点了什么（只读）' },
  { id: 'syslog', label: '系统日志', icon: ScrollText, hint: '应用日志与导出（只读）' },
  { id: 'chain', label: '审计链', icon: ShieldCheck, hint: '不可篡改链式台账 + 校验与 CSV 导出（只读）' },
  { id: 'export', label: '数据导出', icon: FileDown, hint: '业务表分片导出（CSV / JSON）' },
]

const LEGACY: Record<string, Tab> = {
  'panorama/logs': 'live',
  'admin/audit': 'ops-audit',
  'admin/logs': 'syslog',
  'governance/audit': 'chain',
  'admin/export': 'export',
}

function isTab(v: string | null): v is Tab {
  return v === 'live' || v === 'ops-audit' || v === 'syslog' || v === 'chain' || v === 'export'
}

export default function LogsAndAudit() {
  const [tab, setTab] = useState<Tab>(
    () => readUrlTab(isTab) ?? legacyTab(BOOT_PANEL, LEGACY) ?? 'live',
  )
  const active = TABS.find((t) => t.id === tab) ?? TABS[0]

  return (
    <div className="flex h-full min-h-0 flex-col bg-slate-950">
      <PageTabsHeader
        title="日志与审计"
        hint={active.hint}
        tabs={
          <Tabs
            variant="folder"
            className="-mb-px"
            aria-label="日志与审计"
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
        {tab === 'live' && <PanoramaLogs embedded />}
        {tab === 'ops-audit' && (
          <AdminGuard>
            <div className="p-6"><AuditList /></div>
          </AdminGuard>
        )}
        {tab === 'syslog' && (
          <AdminGuard>
            <div className="p-6"><SystemLog embedded /></div>
          </AdminGuard>
        )}
        {tab === 'chain' && <AuditExportPanel />}
        {tab === 'export' && (
          <AdminGuard>
            <div className="p-6"><DataExport embedded /></div>
          </AdminGuard>
        )}
      </div>
    </div>
  )
}
