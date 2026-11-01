/**
 * 技能中心 —— 只保留 LLM 技能（2026-10-24 IA 调整）
 * ------------------------------------------------------------------
 * 工作流技能 / 可视化编辑已拆成独立叶子「工作流」（pages/hub/memory/workflow-hub.tsx）：
 * 两者执行模型不同（模型执行 vs 本地 DAG），页面不再用 Tab 混装。
 * 本页因此只留「统一 LLM 表」——运行时技能 + 技能资产按 id 合并，符合既有契约「单一 LLM 表」。
 */
import { useState } from 'react'
import { BookOpen, GitBranch, Hammer, Sparkles } from 'lucide-react'
import SkillAssetTable from './SkillAssetTable'
import SkillIntakeDialog from './SkillIntakeDialog'
import SkillCardsDialog from '../../../components/SkillCardsDialog'

export default function SkillCenter() {
  const [intakeOpen, setIntakeOpen] = useState(false)
  const [cardsOpen, setCardsOpen] = useState(false)
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
              LLM 技能（提示/行为注入，模型执行）—— 统一表 = 运行时技能（/api/skills）+ 技能资产（/api/skills-mgmt）按 id 合并
              <span className="ml-2 text-slate-600">· 本地确定性执行的工作流在「工作流」叶子</span>
            </p>
          </div>
          <button
            type="button"
            onClick={() => setCardsOpen(true)}
            title="按能力卡（L1）列出现在到底有哪些能力：触发 / 步骤 / 参数 / 示例 / 约束"
            className="ml-auto flex shrink-0 items-center gap-1.5 rounded-md border border-slate-700 px-2.5 py-1.5 text-[11px] text-slate-300 transition-colors hover:border-cyan-700 hover:bg-cyan-500/10 hover:text-cyan-300"
          >
            <BookOpen size={12} /> 能力卡
          </button>
          <button
            type="button"
            onClick={() => setIntakeOpen(true)}
            title="为什么要把技能提交进 git、新技能怎么处理、一键入轨"
            className="flex shrink-0 items-center gap-1.5 rounded-md border border-slate-700 px-2.5 py-1.5 text-[11px] text-slate-300 transition-colors hover:border-cyan-700 hover:bg-cyan-500/10 hover:text-cyan-300"
          >
            <GitBranch size={12} /> 入轨助手
          </button>
        </div>
      </div>

      {/* ─── 内容 ─── */}
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <div className="mb-3 flex items-start gap-2 rounded-lg border border-slate-800 bg-slate-900/60 px-3.5 py-2.5 text-xs text-slate-400">
          <Sparkles size={13} className="mt-0.5 shrink-0 text-cyan-400" />
          <span>
            这里管理<strong className="text-slate-200"> LLM 技能</strong>（提示/行为/扩展类：情感表达、主动建议、安全守护、
            email-helper 等），其内容会注入每次 LLM 调用。表格把运行时技能与技能资产按 id 合并：
            只有运行时注册的技能标「仅运行时·无资产记录」（无评审/版本/发布记录，仍可查看内容与启停）。
            确定性、可本地执行的重复任务请到「工作流」叶子；外来技能/文档的获取入口在「吞噬」。
          </span>
        </div>
        {/* 统一表已包含「扩展系统登记为 skill」的运行时行（按 ext_id 与运行时 id 同源），
            SkillAssetTable 内部只列「表里没有」的游离登记 —— 不在本页再画一份重复清单。 */}
        <SkillAssetTable />
      </div>
      <SkillCardsDialog open={cardsOpen} onClose={() => setCardsOpen(false)} />
      <SkillIntakeDialog open={intakeOpen} onClose={() => setIntakeOpen(false)} />
    </div>
  )
}
