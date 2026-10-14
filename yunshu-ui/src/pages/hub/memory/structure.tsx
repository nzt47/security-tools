/**
 * 记忆结构 —— 记忆管理的「结构 / 功能 / 组成部分」说明面板
 * ------------------------------------------------------------------
 * 需求（2026-10-14）：在记忆管理里新增一个 TAB，显示记忆的**结构、功能与组成部分**。
 *
 * 【取数纪律】层次与职责是本仓既有事实（写死在下方常量，随代码变更同步）；
 * 数量则一律来自**真实端点**，取不到就显示 —，不拿占位数字冒充（本仓纪律）。
 *   向量记忆 / 会话：/api/memory/overview
 *   知识库        ：/api/knowledge/index
 *   经验库        ：/api/experience/stats
 */
import { useEffect, useState } from 'react'
import { BookOpen, Brain, Database, Layers, Lightbulb, RefreshCw } from 'lucide-react'
import { Card, PageHeader, StatCard } from '../components/ui'
import { getEnvelope } from '@/api/envelope'
import { EXPERIENCE_STATS, KNOWLEDGE_INDEX, MEMORY_OVERVIEW } from '@/api/endpoints'

type AnyObj = Record<string, unknown>

/** 从任意形状的对象里按候选键取一个数字（取不到返回 null —— 由调用方显示 —） */
function pickNum(obj: unknown, keys: string[]): number | null {
  if (!obj || typeof obj !== 'object') return null
  const rec = obj as AnyObj
  for (const k of keys) {
    const v = rec[k]
    if (typeof v === 'number' && Number.isFinite(v)) return v
  }
  // 数组型响应（如 /api/knowledge/index 返回卡片列表）按长度计
  if (Array.isArray(obj)) return obj.length
  for (const k of ['items', 'cards', 'list', 'data']) {
    const v = rec[k]
    if (Array.isArray(v)) return v.length
  }
  return null
}

const fmt = (n: number | null) => (n == null ? '—' : n.toLocaleString())

/** 三层记忆：层次 / 承载物 / 生命周期 / 机制 */
const LAYERS: { name: string; holds: string; life: string; how: string }[] = [
  {
    name: '① 工作记忆（当前窗口）',
    holds: '会话消息 + 系统提示词 + 工具定义',
    life: '一轮对话',
    how: '由 /api/context/status 实时计量；占用超阈值触发压缩（见「会话与配置 · 上下文」面板）',
  },
  {
    name: '② 短期记忆（对话摘要）',
    holds: 'memory 摘要（compress_rounds = 摘要版本号）',
    life: '会话内',
    how: '后台异步把旧对话压缩为摘要，保留关键决策与结论；压缩过多会触发「摘要退化」提示',
  },
  {
    name: '③ 长期记忆（持久）',
    holds: '向量记忆 + 知识库 + 经验库',
    life: '持久（跨会话）',
    how: '写入后按语义检索召回：对话时自动注入、或经「全库检索」按需查询',
  },
]

/** 长期记忆的三个组成部分：职责 / 写入方 / 读取方 */
const PARTS: { name: string; role: string; write: string; read: string; icon: typeof Brain }[] = [
  {
    name: '向量记忆',
    role: '对话与手动录入的原文切片及其向量索引，按语义相似度召回',
    write: '手动录入（本页「向量记忆 · 手动录入」）/ 对话中自动沉淀 / 压缩前的原文',
    read: '/api/vector/search；工作台对话时的记忆注入',
    icon: Brain,
  },
  {
    name: '知识库',
    role: '结构化知识卡片 + 双链图谱，适合沉淀可复用的概念与结论',
    write: '知识卡片编辑 / 消化流水线（治理面板）',
    read: '/api/knowledge/query；融合检索',
    icon: BookOpen,
  },
  {
    name: '经验库',
    role: '从历史会话提炼的任务经验（做法 + 适用条件），供相似任务复用',
    write: '会话蒸馏 / 经验审查台',
    read: '/api/experience/search；委派子代理时的经验注入',
    icon: Lightbulb,
  },
]

export default function MemoryStructure() {
  const [overview, setOverview] = useState<unknown>(null)
  const [knowledge, setKnowledge] = useState<unknown>(null)
  const [experience, setExperience] = useState<unknown>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  const load = () => {
    setLoading(true)
    setError('')
    // 三个端点各自独立：任一失败不影响其余读数（缺失项显示 —）
    Promise.allSettled([
      getEnvelope<unknown>(MEMORY_OVERVIEW),
      getEnvelope<unknown>(KNOWLEDGE_INDEX),
      getEnvelope<unknown>(EXPERIENCE_STATS),
    ]).then(([o, k, e]) => {
      if (o.status === 'fulfilled') setOverview(o.value)
      else setError(String(o.reason))
      if (k.status === 'fulfilled') setKnowledge(k.value)
      if (e.status === 'fulfilled') setExperience(e.value)
      setLoading(false)
    })
  }

  useEffect(load, [])

  const vectorDocs = pickNum(overview, ['vector_docs', 'vector_count', 'docs'])
  const messages = pickNum(overview, ['message_count', 'messages'])
  const windowEvents = pickNum(overview, ['window_events', 'events'])
  const cards = pickNum(knowledge, ['total', 'count', 'cards'])
  const expTotal = pickNum(experience, ['total', 'count', 'samples'])

  return (
    <div className="mx-auto max-w-5xl px-5 py-5">
      <PageHeader
        title="记忆结构"
        description="记忆的层次、组成部分、数据来源与职责边界（数量取自真实端点，取不到显示 —）"
      />

      <div className="mb-5 flex items-center gap-2">
        <button
          type="button"
          onClick={load}
          className="flex items-center gap-1.5 rounded-md border border-slate-700 px-2.5 py-1.5 text-[11px] text-slate-400 hover:bg-slate-800"
        >
          <RefreshCw size={12} /> 刷新
        </button>
        {loading && <span className="text-[11px] text-slate-500">加载中…</span>}
        {error && <span className="text-[11px] text-amber-400">部分读数不可用：{error.slice(0, 120)}</span>}
      </div>

      {/* ── 组成总览（真实数量）──────────────────────────────────── */}
      <div className="mb-5 grid grid-cols-2 gap-3 lg:grid-cols-5">
        <StatCard label="向量记忆文档" value={fmt(vectorDocs)} unit="条" icon={<Brain size={14} />} />
        <StatCard label="知识卡片" value={fmt(cards)} unit="张" icon={<BookOpen size={14} />} color="text-amber-400" />
        <StatCard label="经验条目" value={fmt(expTotal)} unit="条" icon={<Lightbulb size={14} />} color="text-emerald-400" />
        <StatCard label="会话消息" value={fmt(messages)} unit="条" icon={<Database size={14} />} color="text-slate-400" />
        <StatCard label="窗口事件" value={fmt(windowEvents)} unit="次" icon={<Layers size={14} />} color="text-violet-400" />
      </div>

      {/* ── 三层结构 ────────────────────────────────────────────── */}
      <Card title="记忆的层次（从易失到持久）" className="mb-5">
        <div className="space-y-3">
          {LAYERS.map((l) => (
            <div key={l.name} className="rounded-lg border border-slate-800 bg-slate-950/50 px-3.5 py-2.5">
              <div className="text-[13px] font-medium text-slate-100">{l.name}</div>
              <dl className="mt-1.5 grid gap-1 text-[11.5px] sm:grid-cols-[7rem_1fr]">
                <dt className="text-slate-500">承载物</dt>
                <dd className="text-slate-300">{l.holds}</dd>
                <dt className="text-slate-500">生命周期</dt>
                <dd className="text-slate-300">{l.life}</dd>
                <dt className="text-slate-500">机制</dt>
                <dd className="text-slate-300">{l.how}</dd>
              </dl>
            </div>
          ))}
        </div>
      </Card>

      {/* ── 长期记忆的三个组成部分 ──────────────────────────────── */}
      <Card title="长期记忆的组成部分（各自职责 / 谁写 / 谁读）">
        <div className="space-y-3">
          {PARTS.map((p) => {
            const Icon = p.icon
            return (
              <div key={p.name} className="rounded-lg border border-slate-800 bg-slate-950/50 px-3.5 py-2.5">
                <div className="flex items-center gap-1.5 text-[13px] font-medium text-cyan-300">
                  <Icon size={13} /> {p.name}
                </div>
                <div className="mt-1 text-[11.5px] text-slate-300">{p.role}</div>
                <div className="mt-1 text-[11px] text-slate-500">写入方：{p.write}</div>
                <div className="text-[11px] text-slate-500">读取方：{p.read}</div>
              </div>
            )
          })}
        </div>
        <p className="mt-3 text-[11px] text-slate-500">
          提示：三者的统一入口在「全库检索」；「向量记忆」提供手动录入与自动沉淀两种视图。
          压缩与窗口水位见「会话与配置 · 会话任务」底部的上下文面板。
        </p>
      </Card>
    </div>
  )
}
