/**
 * 记忆治理视图（只读）—— 原「治理面板 · 记忆技能库」，2026-10-18 并入记忆管理。
 * ------------------------------------------------------------------
 * 【为什么并进来】这块面板早已不含"技能"：技能中心已上提为顶层导航项，它剩下的只是
 *   **四层记忆（策略/事实/偏好/工作）的只读清单 + U5 召回优先级契约**，而「记忆管理 · 记忆结构」
 *   讲的正是同一件事（层次 / 组成部分 / 数据来源 / 召回）。分在两个导航桶里只会让人找两次。
 * 【语义不变】仍是**只读 + 内容脱敏 + 走治理面板的审计口径**
 *   （GET /api/cp/memory/skills，object_type=panel.memory_skills）。
 *   ⚠️ 不要把它并进可写的记忆列表：那会让人以为治理视图能改，也容易把脱敏内容当原文。
 * 【入口兼容】旧深链 #/workbench?panel=governance/memory 由 hubNav 的 LEGACY_NAV_KEY_MAP
 *   落到「记忆管理」（页内 Tab 记忆接续上次选择）。
 */
import {
  AbsentBox,
  Collapsible,
  MetricValue,
  PanelHeader,
  PanelShell,
  StatusBadge,
  VirtualList,
} from '../governance/components'
import { usePanel } from '../governance/usePanel'
import { fetchMemorySkills } from '@/lib/cpPanelsApi'
import type { MemoryCardData, MemorySkillsView } from '@/lib/cpPanelsTypes'

export function MemoryGovernancePanel() {
  const { data, loading, error, reload } = usePanel<MemorySkillsView>(
    () => fetchMemorySkills({ limit: 100 }),
    [],
  )
  const layers = data?.layers as {
    by_layer?: Record<string, number>
    entries?: MemoryCardData[]
    available?: boolean
    reason?: string
    total?: unknown
  } | undefined
  const rp = data?.recall_priority
  return (
    <PanelShell loading={loading} error={error} reload={reload}>
      {data && (
        <>
          <PanelHeader
            title="记忆治理视图（只读）"
            description="四层记忆（策略/事实/偏好/工作）的跨层清单（内容脱敏）· U5 召回优先级契约；此处只呈现，不改变召回结果"
            priority={data.panel?.priority}
            datasources={data.panel?.datasources}
          />
          {/* ★ U5：召回优先级契约（单一来源＝后端 taxonomy） */}
          <Collapsible title="召回优先级契约（U5：策略 > 事实 > 偏好）" defaultOpen>
            {rp && (rp as { available?: boolean }).available === false ? (
              <AbsentBox label="优先级口径不可用" reason={(rp as { reason?: string }).reason} />
            ) : (
              <div className="space-y-1 text-xs text-slate-300">
                <div className="flex flex-wrap items-center gap-2">
                  {(rp as { order?: string[] })?.order?.map((o, i) => (
                    <span key={o} className="flex items-center gap-1">
                      <StatusBadge tone={i === 0 ? 'green' : i === 1 ? 'blue' : 'gray'}>{o}</StatusBadge>
                      {i < ((rp as { order?: string[] })?.order?.length ?? 1) - 1 && (
                        <span className="text-slate-600">›</span>
                      )}
                    </span>
                  ))}
                </div>
                <div className="font-mono text-[10px] text-slate-500">来源：{(rp as { source?: string })?.source}</div>
                <div className="text-[11px] text-slate-500">公式：{(rp as { formula?: string })?.formula}</div>
                <div className="text-[11px] text-amber-400/80">{(rp as { contract?: string })?.contract}</div>
              </div>
            )}
          </Collapsible>

          <div className="mt-3">
            {layers?.available === false ? (
              <AbsentBox label="记忆存储不可用" reason={layers.reason} />
            ) : (
              <>
                <div className="mb-2 flex flex-wrap items-center gap-3 text-xs text-slate-400">
                  <MetricValue metric={layers?.total as never} label="召回条目" />
                  {Object.entries(layers?.by_layer || {}).map(([k, v]) => (
                    <span key={k} className="text-slate-500">{k}:{v}</span>
                  ))}
                </div>
                <VirtualList
                  items={(layers?.entries || []) as MemoryCardData[]}
                  itemHeight={56}
                  height={480}
                  className="rounded-lg border border-slate-800"
                  keyOf={(m) => m.memory_id}
                  renderItem={(m) => (
                    <div className="flex items-center gap-2 border-b border-slate-800/60 px-3 text-xs">
                      <StatusBadge tone={m.layer === 'strategy' ? 'green' : m.layer === 'fact' ? 'blue' : 'gray'}>
                        {m.layer}
                      </StatusBadge>
                      <span className="w-24 shrink-0 truncate font-mono text-[10px] text-slate-500" title={m.memory_id}>
                        {m.memory_id}
                      </span>
                      {/* content_redacted 已是脱敏文本（§3.4 脱敏先于哈希） */}
                      <span className="min-w-0 flex-1 truncate text-slate-300">{m.content_redacted}</span>
                      {m.org_level && <StatusBadge tone="blue">org 级</StatusBadge>}
                      {m.degraded && <StatusBadge tone="yellow">降级</StatusBadge>}
                      {m.forget_candidate && <StatusBadge tone="red">遗忘候选</StatusBadge>}
                      <span className="shrink-0 text-[10px] text-slate-600">{m.scope}</span>
                    </div>
                  )}
                />
              </>
            )}
          </div>
        </>
      )}
    </PanelShell>
  )
}
