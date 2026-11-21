/**
 * 账号与权限 —— 用户 / 角色 / 菜单 三个管理页合成一个叶子（2026-11-21 导航重组）
 * ------------------------------------------------------------------
 * 三者是同一件事的三层：谁能登录（用户）→ 他有什么权限（角色）→ 菜单怎么下发（菜单）。
 * 原来是三条并列导航，改权限要来回跳；合到一页按「用户 → 角色 → 菜单」排列。
 *
 * 深链：?panel=admin/accounts&tab=users|roles|menus；三个旧 key 由 LEGACY_NAV_KEY_MAP 落到本页。
 * 三个子页都来自管理后台（pages/system/*），靠 AdminGuard 处理未登录提示。
 */
import { useState } from 'react'
import { ListTree, Shield, Users } from 'lucide-react'
import { PageTabsHeader } from '../components/ui'
import { Tabs } from '@/components/ui'
import { BOOT_PANEL } from '@/workbench/hubNav'
import { legacyTab, readUrlTab, writeUrlTab } from '@/lib/hubTab'
import AdminGuard from './AdminGuard'
import UserList from '@/pages/system/UserList'
import RoleList from '@/pages/system/RoleList'
import MenuList from '@/pages/system/MenuList'

type Tab = 'users' | 'roles' | 'menus'

const TABS: Array<{ id: Tab; label: string; icon: typeof Users; hint: string }> = [
  { id: 'users', label: '用户', icon: Users, hint: '账号、启用状态与密码重置' },
  { id: 'roles', label: '角色', icon: Shield, hint: '角色与权限码分配' },
  { id: 'menus', label: '菜单', icon: ListTree, hint: '菜单树与可见性（后端 /api/auth/menus 下发）' },
]

/** 旧叶子 key → Tab（单测按它验收深链落点） */
export const LEGACY: Record<string, Tab> = {
  'admin/users': 'users',
  'admin/roles': 'roles',
  'admin/menus': 'menus',
}

function isTab(v: string | null): v is Tab {
  return v === 'users' || v === 'roles' || v === 'menus'
}

export default function AccountsPage() {
  const [tab, setTab] = useState<Tab>(
    () => readUrlTab(isTab) ?? legacyTab(BOOT_PANEL, LEGACY) ?? 'users',
  )
  const active = TABS.find((t) => t.id === tab) ?? TABS[0]

  return (
    <div className="flex h-full min-h-0 flex-col bg-slate-950">
      <PageTabsHeader
        title="账号与权限"
        hint={active.hint}
        tabs={
          <Tabs
            variant="folder"
            className="-mb-px"
            aria-label="账号与权限"
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
            {tab === 'users' && <UserList />}
            {tab === 'roles' && <RoleList />}
            {tab === 'menus' && <MenuList />}
          </div>
        </AdminGuard>
      </div>
    </div>
  )
}
