/**
 * 实例管理 —— LLM 实例与搜索实例（9 条端点）
 * ------------------------------------------------------------------
 *   LLM    GET|POST /api/llm/instances，GET|PUT|DELETE /<id>，
 *          POST /<id>/default（设为默认），POST /<id>/test（连通性测试）
 *   搜索   GET|POST /api/search/instances，PUT|DELETE /<id>，
 *          POST /<id>/default，POST /<id>/test
 *
 * 【为什么两类实例同页】它们是同一种东西的两种形态：一条"外部服务"记录，
 * 有 name / 启用 / 默认 / 测试连通性 / 增删改。分开两页会让"设默认""测一下"
 * 这两件高频小动作出现在两个地方。
 *
 * 【删除为什么要点两次】删掉默认实例会让运行时无可用服务；后端不做兜底，
 * 前端必须把"这是默认实例"和"确认删除"两件事都说清楚。
 */
import { useCallback, useEffect, useState } from 'react'
import { CheckCircle2, Cpu, Loader2, Plus, RefreshCw, Search, Star, Trash2, Wifi } from 'lucide-react'
import { ErrorBox, hubDelete, hubGet, hubPost, hubPut } from '../components/ui'
import {
  LLM_INSTANCES,
  SEARCH_INSTANCES,
  llmInstance,
  llmInstanceDefault,
  llmInstanceTest,
  searchInstance,
  searchInstanceDefault,
  searchInstanceTest,
} from '@/api/endpoints'

const BTN = 'inline-flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-50'
const BTN_EM = BTN + ' border-cyan-700/70 text-cyan-300 hover:bg-cyan-500/10'
const BTN_RED = BTN + ' border-red-800/70 text-red-300 hover:bg-red-500/10'
const INPUT = 'rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200 outline-none focus:border-cyan-600'

interface Instance {
  id?: string
  name?: string
  provider?: string
  model?: string
  engine_type?: string
  base_url?: string
  api_key?: string
  timeout?: number
  enabled?: boolean
  is_default?: boolean
}

type Tab = 'llm' | 'search'

export default function InstancesPanel() {
  const [tab, setTab] = useState<Tab>('llm')
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')

  const [llm, setLlm] = useState<Instance[]>([])
  const [search, setSearch] = useState<Instance[]>([])
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null)
  const [detailEdit, setDetailEdit] = useState('')
  const [confirmDelete, setConfirmDelete] = useState('')

  // 新建表单（两类共用一套字段，按 Tab 取需要的）
  const [fName, setFName] = useState('')
  const [fKind, setFKind] = useState('')       // provider（LLM）/ engine_type（搜索）
  const [fModel, setFModel] = useState('')
  const [fBaseUrl, setFBaseUrl] = useState('')
  const [fApiKey, setFApiKey] = useState('')
  const [fTimeout, setFTimeout] = useState('30')

  const fail = (label: string, e: unknown) => setErr(label + '：' + (e instanceof Error ? e.message : String(e)))
  const bad = (label: string, r?: { error?: string; errors?: string[] }) =>
    setErr(label + '：' + (r?.error || (r?.errors?.length ? r.errors.join('；') : '后端返回 ok=false')))

  const loadLlm = useCallback(async () => {
    setBusy('llm'); setErr('')
    try {
      const r = await hubGet<{ ok?: boolean; instances?: Instance[]; error?: string }>(LLM_INSTANCES)
      if (r?.ok === false) { bad('读取 LLM 实例失败', r); return }
      setLlm(r?.instances ?? [])
    } catch (e) { fail('读取 LLM 实例失败', e) } finally { setBusy('') }
  }, [])

  const loadSearch = useCallback(async () => {
    setBusy('search'); setErr('')
    try {
      const r = await hubGet<{ ok?: boolean; instances?: Instance[]; error?: string }>(SEARCH_INSTANCES)
      if (r?.ok === false) { bad('读取搜索实例失败', r); return }
      setSearch(r?.instances ?? [])
    } catch (e) { fail('读取搜索实例失败', e) } finally { setBusy('') }
  }, [])

  useEffect(() => { void loadLlm(); void loadSearch() }, [loadLlm, loadSearch])

  const createLlm = async () => {
    setBusy('create'); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string; errors?: string[] }>(LLM_INSTANCES, {
        instance: {
          name: fName.trim(), provider: fKind.trim(), model: fModel.trim(),
          base_url: fBaseUrl.trim(), api_key: fApiKey.trim(),
          timeout: Number(fTimeout || 30), enabled: true,
        },
      })
      if (r?.ok === false) { bad('新建失败', r); return }
      setMsg('已创建 LLM 实例：' + fName.trim())
      setFName(''); setFApiKey('')
      await loadLlm()
    } catch (e) { fail('新建失败', e) } finally { setBusy('') }
  }

  const createSearch = async () => {
    setBusy('create'); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string; errors?: string[] }>(SEARCH_INSTANCES, {
        instance: {
          name: fName.trim(), engine_type: fKind.trim(), api_key: fApiKey.trim(),
          timeout: Number(fTimeout || 30), enabled: true,
        },
      })
      if (r?.ok === false) { bad('新建失败', r); return }
      setMsg('已创建搜索实例：' + fName.trim())
      setFName(''); setFApiKey('')
      await loadSearch()
    } catch (e) { fail('新建失败', e) } finally { setBusy('') }
  }

  const setDefault = async (kind: Tab, id: string) => {
    setBusy('default:' + id); setErr(''); setMsg('')
    try {
      const url = kind === 'llm' ? llmInstanceDefault(id) : searchInstanceDefault(id)
      const r = await hubPost<{ ok?: boolean; error?: string; errors?: string[] }>(url, {})
      if (r?.ok === false) { bad('设为默认失败', r); return }
      setMsg('已设为默认：' + id)
      await (kind === 'llm' ? loadLlm() : loadSearch())
    } catch (e) { fail('设为默认失败', e) } finally { setBusy('') }
  }

  const testOne = async (kind: Tab, id: string) => {
    setBusy('test:' + id); setErr(''); setMsg('')
    try {
      const url = kind === 'llm' ? llmInstanceTest(id) : searchInstanceTest(id)
      const r = await hubPost<{ ok?: boolean; error?: string; success?: boolean; message?: string }>(url, {})
      if (r?.ok === false) { bad('连通性测试失败', r); return }
      setMsg('测试结果（' + id + '）：' + (r?.message ?? (r?.success ? '成功' : '见返回')) + ' ' + JSON.stringify(r).slice(0, 160))
    } catch (e) { fail('连通性测试失败', e) } finally { setBusy('') }
  }

  const remove = async (kind: Tab, id: string) => {
    setBusy('del:' + id); setErr(''); setMsg('')
    try {
      const url = kind === 'llm' ? llmInstance(id) : searchInstance(id)
      const r = await hubDelete<{ ok?: boolean; error?: string }>(url)
      if (r?.ok === false) { bad('删除失败', r); return }
      setMsg('已删除：' + id)
      setConfirmDelete('')
      await (kind === 'llm' ? loadLlm() : loadSearch())
    } catch (e) { fail('删除失败', e) } finally { setBusy('') }
  }

  const openDetail = async (id: string) => {
    setBusy('detail'); setErr(''); setDetail(null)
    try {
      const r = await hubGet<Record<string, unknown>>(llmInstance(id))
      setDetail(r)
      setDetailEdit(JSON.stringify(r, null, 2))
    } catch (e) { fail('读取实例详情失败', e) } finally { setBusy('') }
  }

  /** 编辑：把详情里可改的字段整体 PUT 回去（PUT 语义就是"替换这一份"）。 */
  const saveDetail = async (id: string) => {
    if (!detail) return
    let payload: Record<string, unknown>
    try {
      payload = JSON.parse(detailEdit || '{}')
    } catch {
      setErr('详情不是合法 JSON —— 没有提交')
      return
    }
    setBusy('save'); setErr(''); setMsg('')
    try {
      const body = (payload.instance as Record<string, unknown>) ?? payload
      const r = await hubPut<{ ok?: boolean; error?: string; errors?: string[] }>(llmInstance(id), { instance: body })
      if (r?.ok === false) { bad('保存失败', r); return }
      setMsg('已保存：' + id)
      await loadLlm()
    } catch (e) { fail('保存失败', e) } finally { setBusy('') }
  }

  const loading = busy !== ''
  const rows = tab === 'llm' ? llm : search

  return (
    <div className="h-full overflow-y-auto p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Cpu size={14} className="text-cyan-400" />
        <h2 className="text-sm font-semibold text-slate-100">实例管理</h2>
        <span className="text-[11px] text-slate-500">LLM 实例与搜索实例：新建 / 设为默认 / 连通性测试 / 删除</span>
        <div className="ml-auto">
          <button type="button" className={BTN} onClick={() => { void loadLlm(); void loadSearch() }} disabled={loading} aria-label="刷新实例列表">
            {loading ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 刷新
          </button>
        </div>
      </div>

      {err && <ErrorBox message={err} />}
      {msg && <div className="mb-2 rounded border border-emerald-800/60 bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-300">{msg}</div>}

      <div className="mb-2 flex items-center gap-1">
        {([['llm', 'LLM 实例'], ['search', '搜索实例']] as const).map(([id, label]) => (
          <button
            key={id}
            type="button"
            onClick={() => setTab(id)}
            aria-selected={tab === id}
            className={'rounded-lg px-2.5 py-1 text-[11px] transition-colors ' + (
              tab === id ? 'bg-cyan-500/15 font-medium text-cyan-300' : 'text-slate-400 hover:bg-slate-800/60 hover:text-slate-200'
            )}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="mb-2 rounded-lg border border-slate-800 bg-slate-900/50 p-3">
        <div className="mb-2 flex items-center gap-2 text-[11px] text-slate-400">
          {tab === 'llm' ? <Cpu size={11} className="text-cyan-400" /> : <Search size={11} className="text-cyan-400" />}
          已配置（{rows.length}）
        </div>
        {rows.length === 0 && <div className="text-[11px] text-slate-500">还没有实例。用下面的表单建一个。</div>}
        <div className="flex flex-col gap-1">
          {rows.map((it) => {
            const id = it.id ?? ''
            const isOpen = confirmDelete === tab + ':' + id
            return (
              <div key={id} className="rounded border border-slate-800 bg-slate-950/60 px-2 py-1.5 text-[11px]">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-slate-200">{it.name ?? '(未命名)'}</span>
                  <span className="font-mono text-[10px] text-slate-500">{tab === 'llm' ? (it.provider ?? '') + '/' + (it.model ?? '') : (it.engine_type ?? '')}</span>
                  {it.is_default && <span className="flex items-center gap-0.5 text-[10px] text-amber-300"><Star size={9} /> 默认</span>}
                  <span className={it.enabled === false ? 'text-[10px] text-slate-500' : 'text-[10px] text-emerald-400'}>
                    {it.enabled === false ? '停用' : '启用'}
                  </span>
                  <span className="ml-auto flex flex-wrap items-center gap-1">
                    <button type="button" className={BTN} onClick={() => void setDefault(tab, id)} disabled={loading || it.is_default === true} aria-label={'设为默认 ' + id}>
                      <Star size={11} /> 设为默认
                    </button>
                    <button type="button" className={BTN} onClick={() => void testOne(tab, id)} disabled={loading} aria-label={'测试连通 ' + id}>
                      {busy === 'test:' + id ? <Loader2 size={11} className="animate-spin" /> : <Wifi size={11} />} 测试
                    </button>
                    {tab === 'llm' && (
                      <button type="button" className={BTN} onClick={() => void openDetail(id)} disabled={loading} aria-label={'实例详情 ' + id}>
                        详情
                      </button>
                    )}
                    <button type="button" className={BTN_RED} onClick={() => setConfirmDelete(isOpen ? '' : tab + ':' + id)} disabled={loading} aria-label={'删除实例 ' + id}>
                      <Trash2 size={11} /> 删除
                    </button>
                  </span>
                </div>
                {isOpen && (
                  <div className="mt-1.5 flex flex-wrap items-center gap-2 rounded border border-red-900/50 bg-red-500/5 px-2 py-1.5">
                    <span className="text-[10px] text-red-300">
                      删除后该实例立即不可用{it.is_default ? '，且它当前是默认实例' : ''}。确定？
                    </span>
                    <button type="button" className={BTN_RED} onClick={() => void remove(tab, id)} disabled={loading} aria-label={'确认删除 ' + id}>
                      {busy === 'del:' + id ? <Loader2 size={11} className="animate-spin" /> : null} 确认删除
                    </button>
                    <button type="button" className={BTN} onClick={() => setConfirmDelete('')}>取消</button>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>

      <div className="mb-2 rounded-lg border border-slate-800 bg-slate-900/50 p-3">
        <div className="mb-2 flex items-center gap-2 text-[11px] text-slate-400">
          <Plus size={11} className="text-cyan-400" /> 新建{tab === 'llm' ? ' LLM ' : '搜索'}实例
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          <label className="text-[11px] text-slate-400">名称
            <input className={INPUT + ' mt-1 w-full'} value={fName} onChange={(e) => setFName(e.target.value)} aria-label="实例名称" />
          </label>
          <label className="text-[11px] text-slate-400">
            {tab === 'llm' ? 'provider' : 'engine_type'}
            <input
              className={INPUT + ' mt-1 w-full'}
              value={fKind}
              onChange={(e) => setFKind(e.target.value)}
              placeholder={tab === 'llm' ? 'openai / deepseek / ollama' : 'duckduckgo / sogou / bing'}
              aria-label="实例类型"
            />
          </label>
          {tab === 'llm' && (
            <>
              <label className="text-[11px] text-slate-400">model
                <input className={INPUT + ' mt-1 w-full'} value={fModel} onChange={(e) => setFModel(e.target.value)} aria-label="模型名" />
              </label>
              <label className="text-[11px] text-slate-400">base_url
                <input className={INPUT + ' mt-1 w-full'} value={fBaseUrl} onChange={(e) => setFBaseUrl(e.target.value)} aria-label="接口基址" />
              </label>
            </>
          )}
          <label className="text-[11px] text-slate-400">api_key（可空）
            <input className={INPUT + ' mt-1 w-full'} value={fApiKey} onChange={(e) => setFApiKey(e.target.value)} aria-label="实例密钥" />
          </label>
          <label className="text-[11px] text-slate-400">timeout(s)
            <input className={INPUT + ' mt-1 w-full'} value={fTimeout} onChange={(e) => setFTimeout(e.target.value)} aria-label="实例超时秒" />
          </label>
        </div>
        <div className="mt-2">
          <button
            type="button"
            className={BTN_EM}
            onClick={() => void (tab === 'llm' ? createLlm() : createSearch())}
            disabled={loading || fName.trim() === '' || fKind.trim() === ''}
            aria-label="新建实例"
          >
            {busy === 'create' ? <Loader2 size={11} className="animate-spin" /> : <Plus size={11} />} 新建
          </button>
          <span className="ml-2 text-[10px] text-slate-600">名称与类型必填，其余按引擎要求填写</span>
        </div>
      </div>

      {detail && (
        <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
          <div className="mb-1 flex items-center gap-2 text-[11px] text-slate-400">
            <CheckCircle2 size={11} className="text-cyan-400" /> 实例详情（可直接编辑后保存，PUT 会整体替换）
            <span className="ml-auto flex items-center gap-1">
              <button
                type="button"
                className={BTN_EM}
                onClick={() => { const id = (detail.id as string) ?? ''; if (id) void saveDetail(id) }}
                disabled={loading}
                aria-label="保存实例详情"
              >
                {busy === 'save' ? <Loader2 size={11} className="animate-spin" /> : null} 保存
              </button>
              <button type="button" className={BTN} onClick={() => setDetail(null)}>关闭</button>
            </span>
          </div>
          <textarea
            className={INPUT + ' h-48 w-full font-mono'}
            value={detailEdit}
            onChange={(e) => setDetailEdit(e.target.value)}
            aria-label="实例详情 JSON"
          />
        </div>
      )}
    </div>
  )
}
