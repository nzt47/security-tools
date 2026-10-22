/**
 * 新增知识文档 —— POST /api/knowledge/add（{content, source?, tags?}）
 * ------------------------------------------------------------------
 * 【为什么"内容"是唯一必填】后端 400 的判据只有 content 空；source 缺省 manual、
 * tags 缺省空数组。表单就按这个来：不造前端自己的一套"必填"。
 * 【为什么成功之后清空输入、但不自动跳走】知识库的写入是**异步索引**（向量库 upsert），
 * 立刻刷新列表反而可能看不到刚写入的条目；这里只回显"已提交"，让人自己点刷新。
 */
import { useState } from 'react'
import { CheckCircle2, Loader2, Plus } from 'lucide-react'
import { hubPost } from '../components/ui'
import { KNOWLEDGE_ADD } from '@/api/endpoints'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-cyan-700/70 px-2 py-1 text-[11px] text-cyan-300 transition-colors hover:bg-cyan-500/10 disabled:cursor-not-allowed disabled:opacity-50'
const INPUT = 'w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 outline-hidden focus:border-cyan-600'

export default function KnowledgeAddPanel() {
  const [content, setContent] = useState('')
  const [source, setSource] = useState('manual')
  const [tags, setTags] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [ok, setOk] = useState('')

  const submit = async () => {
    if (!content.trim()) { setErr('内容不能为空'); return }
    setBusy(true); setErr(''); setOk('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string }>(KNOWLEDGE_ADD, {
        content: content.trim(),
        source: source.trim() || 'manual',
        tags: tags.split(/[,，\s]+/).map((s) => s.trim()).filter(Boolean),
      })
      if (r?.ok === false) { setErr('写入被拒绝：' + (r.error || 'ok=false')); return }
      setOk('已提交（向量索引异步写入，稍后刷新列表可见）')
      setContent(''); setTags('')
    } catch (e) {
      setErr('提交失败：' + (e instanceof Error ? e.message : String(e)))
    } finally { setBusy(false) }
  }

  return (
    <div className="mt-4 rounded-xl border border-slate-800 bg-slate-900/40 p-3">
      <div className="mb-2 flex items-center gap-2">
        <Plus size={13} className="text-cyan-400" />
        <h3 className="text-xs font-semibold text-slate-200">新增知识文档</h3>
        <span className="text-[10px] text-slate-500">直接写入向量知识库（内容必填）</span>
      </div>
      {err && <div className="mb-2 rounded-sm border border-red-900/60 bg-red-500/10 px-2 py-1 text-[11px] text-red-300">{err}</div>}
      {ok && (
        <div className="mb-2 flex items-center gap-1 rounded-sm border border-emerald-800/60 bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-300">
          <CheckCircle2 size={11} /> {ok}
        </div>
      )}
      <textarea
        className={INPUT + ' h-24'}
        value={content}
        onChange={(e) => setContent(e.target.value)}
        placeholder="要写入知识库的正文"
        aria-label="知识文档内容"
      />
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <input
          className={INPUT + ' w-40'}
          value={source}
          onChange={(e) => setSource(e.target.value)}
          placeholder="source（缺省 manual）"
          aria-label="知识来源"
        />
        <input
          className={INPUT + ' w-64'}
          value={tags}
          onChange={(e) => setTags(e.target.value)}
          placeholder="标签（逗号分隔，可空）"
          aria-label="知识标签"
        />
        <button type="button" className={BTN} onClick={() => void submit()} disabled={busy} aria-label="提交知识文档">
          {busy ? <Loader2 size={11} className="animate-spin" /> : <Plus size={11} />} 写入知识库
        </button>
      </div>
    </div>
  )
}
