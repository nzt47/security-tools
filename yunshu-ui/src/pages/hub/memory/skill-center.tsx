/**
 * 技能中心 —— LLM 技能 / 工作流技能 / 可视化编辑 统一管理页（分 Tab）
 * ------------------------------------------------------------------
 * 原工作台「记忆管理」下的 技能库管理 / 工作流管理 / 可视化编辑 三个入口
 * 收拢为单页三个 Tab，并明确两类技能的定位差异：
 *   - LLM 技能：提示/行为/扩展类技能（内容注入每次 LLM 调用 → 模型执行）
 *   - 工作流技能：学习/编排得到的确定性工具步骤（命中即本地执行，
 *     skipped_llm=true，省 LLM 调用）——管理维度（版本/审核/启停）本就不同，
 *     故分开呈现。
 * 可视化编辑 = 工作流技能的画布编排入口（第三 Tab，与后端 /api/visual-workflows 联动）。
 */
import { useState } from 'react'
import { Hammer, PenTool, Sparkles, Workflow } from 'lucide-react'
import { MemorySkillsTable } from './skills'
import { MemoryWorkflowTable } from './workflow'
import MemoryWorkflowVisual from './workflow-visual'
import SkillDigestManager from './skill-digest-manager'

type CenterTab = 'llm' | 'workflow' | 'visual'

const TABS: { key: CenterTab; label: string; icon: typeof Hammer; desc: string }[] = [
  { key: 'llm', label: 'LLM 技能', icon: Sparkles, desc: '提示/行为/扩展类技能 · 每次调用由 LLM 执行' },
  { key: 'workflow', label: '工作流技能', icon: Workflow, desc: '本地确定性执行 · 命中即跳过 LLM（skipped_llm）' },
  { key: 'visual', label: '可视化编辑', icon: PenTool, desc: '拖拽编排工作流（保存到 /api/visual-workflows）' },
]

export default function SkillCenter() {
  const [tab, setTab] = useState<CenterTab>('llm')
  const active = TABS.find((t) => t.key === tab)!

  return (
    <div className="flex h-full min-h-0 flex-col bg-slate-950">
      {/* ─── 页头 ─── */}
      <div className="border-b border-slate-800 px-5 pb-3 pt-4">
        <div className="flex items-center gap-2.5">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg border border-slate-700 bg-slate-900">
            <Hammer size={14} className="text-cyan-400" />
          </div>
          <div>
            <h1 className="text-[15px] font-semibold text-slate-100">技能中心</h1>
            <p className="text-[11px] text-slate-500">
              LLM 技能（模型执行）与工作流技能（本地执行）分开管理 —— 工作流命中时跳过 LLM，省调用省延迟
            </p>
          </div>
        </div>
      </div>

      {/* ─── Tab 栏 ─── */}
      <div className="flex items-center gap-1.5 border-b border-slate-800 px-5 py-2">
        {TABS.map((t) => {
          const Icon = t.icon
          return (
            <button
              key={t.key}
              type="button"
              onClick={() => setTab(t.key)}
              aria-selected={tab === t.key}
              title={t.desc}
              className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs transition-colors ${
                tab === t.key
                  ? 'bg-cyan-500/15 font-medium text-cyan-300'
                  : 'text-slate-400 hover:bg-slate-800/60 hover:text-slate-200'
              }`}
            >
              <Icon size={13} />
              {t.label}
            </button>
          )
        })}
        <span className="ml-auto hidden font-mono text-[10px] text-slate-600 md:inline">{active.desc}</span>
      </div>

      {/* ─── Tab 内容 ─── */}
      <div className="min-h-0 flex-1">
        {tab === 'llm' && (
          <div className="h-full overflow-y-auto p-4">
            <div className="mb-3 flex items-start gap-2 rounded-lg border border-slate-800 bg-slate-900/60 px-3.5 py-2.5 text-xs text-slate-400">
              <Sparkles size={13} className="mt-0.5 shrink-0 text-cyan-400" />
              <span>
                这里管理<strong className="text-slate-200"> LLM 技能</strong>（提示/行为/扩展类：情感表达、主动建议、安全守护、
                email-helper 等），其内容会注入每次 LLM 调用。确定性、可本地执行的重复任务请到
                「工作流技能」Tab（命中即本地执行、不消耗 LLM）。
              </span>
            </div>
            <MemorySkillsTable />
            {/* 技能资产库 · 评审-消化管线：新建/外来安装/评估/发布 全生命周期 */}
            <SkillDigestManager />
          </div>
        )}

        {tab === 'workflow' && (
          <div className="h-full overflow-y-auto p-4">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-800 bg-slate-900/60 px-3.5 py-2.5">
              <div className="flex items-start gap-2 text-xs text-slate-400">
                <Workflow size={13} className="mt-0.5 shrink-0 text-emerald-400" />
                <span>
                  工作流技能由历史交互自动学习（或经可视化编排），命中任务描述即
                  <strong className="text-slate-200"> 本地执行并跳过 LLM</strong>（skipped_llm）。可启停 / 执行 / 测试匹配。
                </span>
              </div>
              <button
                type="button"
                onClick={() => setTab('visual')}
                className="flex items-center gap-1.5 rounded-md bg-cyan-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-cyan-500"
              >
                <PenTool size={12} /> 可视化编辑 / 新建
              </button>
            </div>
            <MemoryWorkflowTable />
          </div>
        )}

        {tab === 'visual' && (
          <div className="h-full min-h-0 overflow-hidden">
            <MemoryWorkflowVisual />
          </div>
        )}
      </div>
    </div>
  )
}
