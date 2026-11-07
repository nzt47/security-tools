/**
 * 能力获取 · 联机帮助（使用手册 + 工作过程）
 * ------------------------------------------------------------------
 * 为什么要有它：这一页横跨「入口材料 → 判定 → 重写 → 草稿 → 评审 → 登记 → 启用」七站，
 * 但页面上只有按钮、没有路线图。用户最容易问的三件事恰好都在这里答：
 *   ① 我上传的东西最后会变成什么？（草稿，不是能力）
 *   ② 哪几步是自动的、哪几步必须我点？（自动 4 步 / 手动 3 步）
 *   ③ 怎么少点几下？（批量、直通、L2 自动重写）
 *
 * 内容与页面按钮**逐字对得上**（步骤里写的就是按钮文案），改按钮请回来同步这里。
 */
import { BookOpen, X } from 'lucide-react'

export type HelpStation = {
  no: number
  title: string
  auto: boolean
  where: string
  detail: string
}

/** 全链路 7 站：从入口材料到最终能力。auto=true 表示这一站不需要人点。 */
export const STATIONS: HelpStation[] = [
  {
    no: 1, title: '入口材料', auto: false,
    where: '「获取外来能力」→「1. 选择来源」',
    detail:
      '本机路径（目录 / SKILL.md / zip / 文档）或直接粘贴文本。首屏会**自动探测** claude / openclaw / trae 等配置根，' +
      '列在「本机可吞噬源」里勾选即可，不用手敲路径；探测失败也不挡手输。',
  },
  {
    no: 2, title: '预览计划（判定 + 去重 + 推荐形态）', auto: true,
    where: '点「预览计划」后自动跑',
    detail:
      '识别来源类型（适配器）、判 L0/L1/L2、跟既有技能与元生功能做去重、给推荐形态（技能 / 工具 / 工作流）。' +
      '这一步只读：不写任何文件、不调用任何来源脚本。',
  },
  {
    no: 3, title: '重写 + 校验（仅 L2 需要）', auto: true,
    where: '「2. 预览计划」里的「LLM 重写并预览」；批量时会自动重写',
    detail:
      'L2 来源（普通文档 / 提示词）不能直接成卡：自动做 1 次 LLM 重写并逐字校验引用，结果存档。' +
      '校验不过就**不落草稿**，原因直接写在页面上（不会静默成功）。',
  },
  {
    no: 4, title: '生成草稿', auto: true,
    where: '「3. 确认落库」→「生成技能草稿 / 工具草稿 / 工作流草稿」',
    detail:
      '写到 data/ingestion/drafts/<plan_id> 隔离区。**不注册、不启用**：三种形态都只是草稿，' +
      '所以这里点错也不会污染线上能力。',
  },
  {
    no: 5, title: '草稿评审', auto: false,
    where: '「草稿评审」Tab',
    detail: '看风险、去重结论与正文，决定转正或删除；生成过的草稿也列在这一页底部。',
  },
  {
    no: 6, title: '登记', auto: false,
    where: '「草稿评审」的草稿行（登记 / 登记并启用）',
    detail:
      '技能进文件轨（data/skills_repo + 主轨台账）、工具写 agent/tools/custom + YAML + 单测、' +
      '工作流写 data/learned_workflows.json。**登记 ≠ 启用**：这一步只是「存在于系统里」；' +
      '技能也可以在「转化队列」批量登记（同一条链的批量入口）。',
  },
  {
    no: 7, title: '启用（最终能力）', auto: false,
    where: '草稿行的「登记并启用」/ 技能中心 / 工具页',
    detail:
      '启用后才进模型上下文、才可能被调用。新工具默认 enabled:false（先停用观察），' +
      '工作流也要单独启用。启用即「最终能力」落地，回滚就是停用。',
  },
]

/** 少点几下的三条路径（页面上的按钮文案逐字对应） */
export const SHORTCUTS = [
  { title: '批量生成草稿', detail: '在「本机可吞噬源」里多选（≤20）→ 一次跑完逐条预览 + 落草稿；L2 会自动重写。' },
  { title: '粘贴文本直通', detail: '「粘贴文本」→「直通生成草稿」：跳过手动分级与确认，一次调用完成重写 + 校验 + 落草稿（仍不安装/不注册/不启用）。' },
  { title: 'L2 自动重写', detail: '单条也会在预览阶段给出「LLM 重写并预览」，不用自己改文档。' },
]

export const FAQ = [
  { q: '为什么这里不能直接安装/启用？', a: '获取页只负责「把外来材料变成可评审的草稿」。安装、登记、启用会改运行时台账与模型可见面，统一放在后两站，便于审计与回滚。' },
  { q: '草稿存在哪？会不会污染线上？', a: 'data/ingestion/drafts/<plan_id>（隔离区，gitignored）。不注册就不进技能/工具台账，模型看不到、也调不到。' },
  { q: 'L2 为什么非要重写？', a: 'L2 是「只有正文、没有结构」的文档。重写把它变成带触发条件/步骤/参数/约束的能力卡，并逐字校验引用，防止编造。校验不过不会落草稿。' },
  { q: '去重说「建议合并」怎么办？', a: '先按提示生成草稿，再到「转化队列」里合并——合并会记住目标技能的 id，不会覆盖成两条。' },
  { q: '失败了/想撤销？', a: '草稿阶段直接删草稿（「草稿评审」每行右侧）；已登记的去「技能中心 / 工具页」停用或回退；工具/工作流也可直接删掉 agent/tools/custom 与 learned_workflows 里的条目。' },
]

type Props = { open: boolean; onClose: () => void; onGo: (tab: 'review' | 'queue') => void }

export default function IngestionHelp({ open, onClose, onGo }: Props) {
  if (!open) return null
  const autoCount = STATIONS.filter((s) => s.auto).length
  const manualCount = STATIONS.length - autoCount
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/70 p-4"
      role="dialog" aria-modal="true" aria-label="能力获取使用手册">
      <div className="flex max-h-[86vh] w-full max-w-3xl flex-col overflow-hidden rounded-xl border border-slate-700 bg-slate-900 shadow-2xl">
        <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
          <div className="flex items-center gap-2">
            <BookOpen size={14} className="text-cyan-400" />
            <h2 className="text-sm font-semibold text-slate-100">能力获取 · 使用手册</h2>
            <span className="text-[11px] text-slate-500">从入口材料到最终能力</span>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭使用手册"
            className="rounded-md p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200">
            <X size={14} />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3 text-[11px] leading-relaxed text-slate-300">
          <p className="rounded-md border border-cyan-900/50 bg-cyan-950/20 px-2.5 py-2">
            <strong className="text-cyan-200">一句话：</strong>
            这一页把「外来材料」变成<strong className="text-slate-100">可评审的草稿</strong>；
            评审 → 登记 → 启用在后两站。全链路 {STATIONS.length} 站，其中
            <strong className="text-emerald-300"> {autoCount} 站自动</strong>、
            <strong className="text-amber-300">{manualCount} 站需要你点</strong>。
          </p>

          <h3 className="mt-3 text-[12px] font-medium text-slate-100">工作过程（7 站）</h3>
          <ol className="mt-1 space-y-1.5" data-cp-help-stations="true">
            {STATIONS.map((s) => (
              <li key={s.no} className="rounded-md border border-slate-800 bg-slate-950/40 px-2.5 py-1.5">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="rounded bg-slate-800 px-1 font-mono text-[10px] text-slate-400">{s.no}</span>
                  <span className="font-medium text-slate-100">{s.title}</span>
                  <span className={s.auto
                    ? 'rounded border border-emerald-800/60 px-1 text-[9px] text-emerald-300'
                    : 'rounded border border-amber-800/60 px-1 text-[9px] text-amber-300'}>
                    {s.auto ? '自动' : '需要你点'}
                  </span>
                  <span className="ml-auto text-[10px] text-slate-500">{s.where}</span>
                </div>
                <div className="mt-0.5 text-slate-400">{s.detail}</div>
              </li>
            ))}
          </ol>

          <h3 className="mt-3 text-[12px] font-medium text-slate-100">你要操作的步骤（照着点）</h3>
          <ol className="mt-1 list-decimal space-y-0.5 pl-5 text-slate-300" data-cp-help-steps="true">
            <li>贴路径或选「本机可吞噬源」里的候选（也可切「粘贴文本」）。</li>
            <li>点「预览计划」——看 L0/L1/L2、去重结论、推荐形态与警告。</li>
            <li>如果是 L2，点「LLM 重写并预览」（批量时会自动做）。</li>
            <li>在「3. 确认落库」选形态（默认技能）→ 勾「我已核对预览计划」→ 点「生成技能草稿」。</li>
            <li>去「草稿评审」确认风险与去重结论，转正或删除。</li>
            <li>在「草稿评审」点那一行的「登记」或「登记并启用」；需要模型马上能用就用后者。</li>
            <li>（可选）在「技能中心 / 工具页」确认启用状态与审批边界。</li>
          </ol>

          <h3 className="mt-3 text-[12px] font-medium text-slate-100">少点几下（自动化的三条路径）</h3>
          <ul className="mt-1 list-disc space-y-0.5 pl-5 text-slate-300">
            {SHORTCUTS.map((s) => (
              <li key={s.title}><strong className="text-slate-100">{s.title}：</strong>{s.detail}</li>
            ))}
          </ul>

          <h3 className="mt-3 text-[12px] font-medium text-slate-100">常见问题</h3>
          <dl className="mt-1 space-y-1">
            {FAQ.map((f) => (
              <div key={f.q}>
                <dt className="font-medium text-slate-200">· {f.q}</dt>
                <dd className="pl-3 text-slate-400">{f.a}</dd>
              </div>
            ))}
          </dl>

          <h3 className="mt-3 text-[12px] font-medium text-slate-100">术语</h3>
          <ul className="mt-1 space-y-0.5 text-slate-400">
            <li><strong className="text-slate-300">L0 / L1 / L2</strong>：合规等级。L0 结构完整可直接用；L1 结构可补齐；L2 只有正文，必须重写。</li>
            <li><strong className="text-slate-300">草稿</strong>：隔离区里的候选能力，未注册、未启用，模型看不见。</li>
            <li><strong className="text-slate-300">登记 / 启用</strong>：写入台账（存在）→ 下发模型（可用），是两件事。</li>
            <li><strong className="text-slate-300">原生重叠</strong>：技能正文与某个内置工具（如 read_file）语义重复，提示你改用工具而不是新技能。</li>
          </ul>
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-slate-800 px-4 py-2">
          <button type="button" onClick={() => { onGo('review'); onClose() }}
            className="rounded-md border border-slate-700 px-2.5 py-1 text-[11px] text-slate-300 hover:bg-slate-800">
            去草稿评审
          </button>
          <button type="button" onClick={() => { onGo('queue'); onClose() }}
            className="rounded-md border border-slate-700 px-2.5 py-1 text-[11px] text-slate-300 hover:bg-slate-800">
            去转化队列
          </button>
          <button type="button" onClick={onClose}
            className="rounded-md bg-cyan-800 px-2.5 py-1 text-[11px] text-cyan-50 hover:bg-cyan-700">
            知道了
          </button>
        </div>
      </div>
    </div>
  )
}