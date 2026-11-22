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
import { CheckCircle2, Cpu, Loader2, Pencil, Plus, RefreshCw, Save, Search, Star, Trash2, Wifi } from 'lucide-react'
import { Button, ErrorBox, hubDelete, hubGet, hubPost, hubPut, Input, Notice, Select, Tabs, Textarea } from '../components/ui'
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

interface Instance {
  id?: string
  name?: string
  provider?: string
  model?: string
  engine_type?: string
  /** 后端实际字段（validate_llm_instance / apply_to_app 都读它） */
  api_endpoint?: string
  /** 旧字段名（历史数据可能残留）；只读兼容，不再写入 */
  base_url?: string
  api_key?: string
  timeout?: number
  enabled?: boolean
  is_default?: boolean
}

type Tab = 'llm' | 'search'

/**
 * 搜索实例的引擎类型（与 agent/config_validation.BUILTIN_ENGINES 对齐，全小写）。
 * 用下拉而不是自由文本：后端按精确词表校验，「Tavily」「Bing」这种首字母大写
 * 会被当成"未知的内置引擎类型"（2026-11-22 Owner 实测）。
 * custom = 自定义端点，必须同时填 api_endpoint。
 */
const ENGINE_OPTIONS = [
  { value: 'duckduckgo', label: 'DuckDuckGo' },
  { value: 'sogou', label: '搜狗搜索' },
  { value: 'so360', label: '360 搜索' },
  { value: 'bing', label: 'Bing' },
  { value: 'google', label: 'Google' },
  { value: 'brave', label: 'Brave' },
  { value: 'baidu', label: '百度' },
  { value: 'tavily', label: 'Tavily（需 key）' },
  { value: 'firecrawl', label: 'Firecrawl（需 key）' },
  { value: 'custom', label: 'custom（自定义端点）' },
]

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
  // 正在编辑的实例 id（空 = 新建模式）。点行的「编辑」把已配置内容填进下面的表单。
  const [editingId, setEditingId] = useState('')

  const resetForm = () => {
    setEditingId('')
    setFName(''); setFKind(''); setFModel(''); setFBaseUrl(''); setFApiKey(''); setFTimeout('30')
  }

  /**
   * 把一条已有实例载入表单（编辑模式）。
   *
   * 【api_key 为什么不回填】列表返回的是脱敏值（***xxxx）；回填再保存会把脱敏值写回去。
   * 留空 = 保留原密钥；要换密钥就重填（只有非空且非脱敏值才会被后端 _save_secure 写入）。
   */
  const beginEdit = (it: Instance) => {
    const id = String(it.id || '')
    if (!id) return
    setEditingId(id)
    setFName(String(it.name || ''))
    setFKind(String((tab === 'llm' ? it.provider : it.engine_type) || ''))
    setFModel(String(it.model || ''))
    setFBaseUrl(String(it.api_endpoint || it.base_url || ''))
    setFApiKey('')
    setFTimeout(String(it.timeout ?? 30))
    setErr('')
    setMsg('')
  }

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
          // 后端校验与运行时都读 api_endpoint；base_url 是历史字段名（写它会被 400 拒绝）
          api_endpoint: fBaseUrl.trim(), api_key: fApiKey.trim(),
          timeout: Number(fTimeout || 30), enabled: true,
        },
      })
      if (r?.ok === false) { bad('新建失败', r); return }
      setMsg('已创建 LLM 实例：' + fName.trim())
      resetForm()
      await loadLlm()
    } catch (e) { fail('新建失败', e) } finally { setBusy('') }
  }

  const createSearch = async () => {
    setBusy('create'); setErr(''); setMsg('')
    try {
      const r = await hubPost<{ ok?: boolean; error?: string; errors?: string[] }>(SEARCH_INSTANCES, {
        instance: {
          name: fName.trim(), engine_type: fKind.trim(), api_key: fApiKey.trim(),
          // custom 引擎后端要求 api_endpoint；内置引擎不必填，只有填了才发
          ...(fBaseUrl.trim() ? { api_endpoint: fBaseUrl.trim() } : {}),
          timeout: Number(fTimeout || 30), enabled: true,
        },
      })
      if (r?.ok === false) { bad('新建失败', r); return }
      setMsg('已创建搜索实例：' + fName.trim())
      resetForm()
      await loadSearch()
    } catch (e) { fail('新建失败', e) } finally { setBusy('') }
  }

  /**
   * 保存对已有实例的修改（PUT）。
   *
   * 【为什么按钮/文案与新建共用一套表单】Owner 要"点已有实例就能看到已配置内容并改"；
   * 新建与编辑字段完全相同，共用表单避免两套输入互相漂移。
   * 【api_key 留空 = 保留】只有非空且非脱敏值才进 updates。
   */
  const saveEdit = async () => {
    if (!editingId) return
    setBusy('edit'); setErr(''); setMsg('')
    const key = fApiKey.trim()
    const updates: Record<string, unknown> = tab === 'llm'
      ? {
          name: fName.trim(), provider: fKind.trim(), model: fModel.trim(),
          api_endpoint: fBaseUrl.trim(), timeout: Number(fTimeout || 30),
        }
      : {
          name: fName.trim(), engine_type: fKind.trim(), timeout: Number(fTimeout || 30),
          ...(fBaseUrl.trim() ? { api_endpoint: fBaseUrl.trim() } : {}),
        }
    if (key && !key.startsWith('***')) updates.api_key = key
    try {
      const url = tab === 'llm' ? llmInstance(editingId) : searchInstance(editingId)
      const r = await hubPut<{ ok?: boolean; error?: string; errors?: string[] }>(url, { updates })
      if (r?.ok === false) { bad('保存修改失败', r); return }
      setMsg('已保存修改：' + fName.trim())
      resetForm()
      await (tab === 'llm' ? loadLlm() : loadSearch())
    } catch (e) { fail('保存修改失败', e) } finally { setBusy('') }
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
        <h2 className="text-ui font-semibold text-slate-100">实例管理</h2>
        <span className="text-caption text-slate-500">LLM 实例与搜索实例：新建 / 设为默认 / 连通性测试 / 删除</span>
        <div className="ml-auto">
          <Button type="button" variant="default" size="xs" onClick={() => { void loadLlm(); void loadSearch() }} disabled={loading} aria-label="刷新实例列表">
            {loading ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} 刷新
          </Button>
        </div>
      </div>

      {err && <ErrorBox message={err} />}
      {msg && <Notice tone="success" size="sm" className="mb-2">{msg}</Notice>}

      <div className="mb-2 flex items-center gap-1">
        <Tabs
          variant="segmented"
          size="sm"
          value={tab}
          onChange={(v) => { setTab(v as typeof tab); resetForm() }}
          items={([['llm', 'LLM 实例'], ['search', '搜索实例']] as const).map(([id, label]) => ({ value: id, label }))}
        />
      </div>

      <div className="mb-2 rounded-lg border border-slate-800 bg-slate-900/50 p-3">
        <div className="mb-2 flex items-center gap-2 text-caption text-slate-400">
          {tab === 'llm' ? <Cpu size={11} className="text-cyan-400" /> : <Search size={11} className="text-cyan-400" />}
          已配置（{rows.length}）
        </div>
        {rows.length === 0 && <div className="text-caption text-slate-500">还没有实例。用下面的表单建一个。</div>}
        <div className="flex flex-col gap-1">
          {rows.map((it) => {
            const id = it.id ?? ''
            const isOpen = confirmDelete === tab + ':' + id
            return (
              <div key={id} className="rounded-sm border border-slate-800 bg-slate-950/60 px-2 py-1.5 text-caption">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-slate-200">{it.name ?? '(未命名)'}</span>
                  <span className="font-mono text-micro text-slate-500">{tab === 'llm' ? (it.provider ?? '') + '/' + (it.model ?? '') : (it.engine_type ?? '')}</span>
                  {it.is_default && <span className="flex items-center gap-0.5 text-micro text-amber-300"><Star size={9} /> 默认</span>}
                  <span className={it.enabled === false ? 'text-micro text-slate-500' : 'text-micro text-emerald-400'}>
                    {it.enabled === false ? '停用' : '启用'}
                  </span>
                  <span className="ml-auto flex flex-wrap items-center gap-1">
                    <Button
                      type="button" variant="default" size="xs"
                      onClick={() => beginEdit(it)}
                      disabled={loading}
                      aria-label={'编辑实例 ' + id}
                      data-testid={'instance-edit-' + id}
                    >
                      <Pencil size={11} /> 编辑
                    </Button>
                    <Button type="button" variant="default" size="xs" onClick={() => void setDefault(tab, id)} disabled={loading || it.is_default === true} aria-label={'设为默认 ' + id}>
                      <Star size={11} /> 设为默认
                    </Button>
                    <Button type="button" variant="default" size="xs" onClick={() => void testOne(tab, id)} disabled={loading} aria-label={'测试连通 ' + id}>
                      {busy === 'test:' + id ? <Loader2 size={11} className="animate-spin" /> : <Wifi size={11} />} 测试
                    </Button>
                    {tab === 'llm' && (
                      <Button type="button" variant="default" size="xs" onClick={() => void openDetail(id)} disabled={loading} aria-label={'实例详情 ' + id}>
                        详情
                      </Button>
                    )}
                    <Button type="button" variant="danger-outline" size="xs" onClick={() => setConfirmDelete(isOpen ? '' : tab + ':' + id)} disabled={loading} aria-label={'删除实例 ' + id}>
                      <Trash2 size={11} /> 删除
                    </Button>
                  </span>
                </div>
                {isOpen && (
                  <div className="mt-1.5 flex flex-wrap items-center gap-2 rounded-sm border border-red-900/50 bg-red-500/5 px-2 py-1.5">
                    <span className="text-micro text-red-300">
                      删除后该实例立即不可用{it.is_default ? '，且它当前是默认实例' : ''}。确定？
                    </span>
                    <Button type="button" variant="danger-outline" size="xs" onClick={() => void remove(tab, id)} disabled={loading} aria-label={'确认删除 ' + id}>
                      {busy === 'del:' + id ? <Loader2 size={11} className="animate-spin" /> : null} 确认删除
                    </Button>
                    <Button type="button" variant="default" size="xs" onClick={() => setConfirmDelete('')}>取消</Button>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>

      <div className="mb-2 rounded-lg border border-slate-800 bg-slate-900/50 p-3">
        <div className="mb-2 flex items-center gap-2 text-caption text-slate-400">
          {editingId ? <Pencil size={11} className="text-amber-400" /> : <Plus size={11} className="text-cyan-400" />}
          {editingId
            ? '编辑' + (tab === 'llm' ? ' LLM ' : '搜索') + '实例（id: ' + editingId + '）'
            : '新建' + (tab === 'llm' ? ' LLM ' : '搜索') + '实例'}
          <span className="text-micro text-slate-500">
            {editingId ? '已填入当前配置；api_key 留空 = 保留原密钥' : '点任一行的「编辑」可载入已配置内容'}
          </span>
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          <label className="text-caption text-slate-400">名称
            <Input size="sm" className="mt-1 w-full" value={fName} onChange={(e) => setFName(e.target.value)} aria-label="实例名称" />
          </label>
          <label className="text-caption text-slate-400">
            {tab === 'llm' ? 'provider' : 'engine_type'}
            {tab === 'llm' ? (
              <Input
                size="sm" className="mt-1 w-full"
                value={fKind}
                onChange={(e) => setFKind(e.target.value)}
                placeholder="openai / deepseek / ollama"
                aria-label="实例类型"
              />
            ) : (
              <Select
                size="sm" className="mt-1 w-full"
                value={fKind}
                onChange={setFKind}
                placeholder="选择引擎类型"
                aria-label="实例类型"
                data-testid="search-engine-type"
                options={ENGINE_OPTIONS}
              />
            )}
          </label>
          {tab === 'search' && (
            <label className="text-caption text-slate-400">api_endpoint（engine_type=custom 时必填）
              <Input size="sm" className="mt-1 w-full" value={fBaseUrl}
                onChange={(e) => setFBaseUrl(e.target.value)}
                placeholder="https://example.com/search?q={query}"
                aria-label="搜索接口端点" data-testid="search-endpoint" />
            </label>
          )}
          {tab === 'llm' && (
            <>
              <label className="text-caption text-slate-400">model
                <Input size="sm" className="mt-1 w-full" value={fModel} onChange={(e) => setFModel(e.target.value)} aria-label="模型名" />
              </label>
              <label className="text-caption text-slate-400">api_endpoint *（必填）
                <Input size="sm" className="mt-1 w-full" value={fBaseUrl} onChange={(e) => setFBaseUrl(e.target.value)}
                  placeholder="https://api.openai.com/v1" aria-label="接口端点" data-testid="llm-endpoint" />
              </label>
            </>
          )}
          <label className="text-caption text-slate-400">api_key（可空）
            <Input size="sm" className="mt-1 w-full" value={fApiKey} onChange={(e) => setFApiKey(e.target.value)} aria-label="实例密钥" />
          </label>
          <label className="text-caption text-slate-400">timeout(s)
            <Input size="sm" className="mt-1 w-full" value={fTimeout} onChange={(e) => setFTimeout(e.target.value)} aria-label="实例超时秒" />
          </label>
        </div>
        <div className="mt-2">
          <Button
            type="button"
            variant="accent" size="xs"
            onClick={() => void (editingId ? saveEdit() : (tab === 'llm' ? createLlm() : createSearch()))}
            disabled={loading || fName.trim() === '' || fKind.trim() === ''
              || (tab === 'llm' && fBaseUrl.trim() === '')
              || (tab === 'search' && fKind.trim() === 'custom' && fBaseUrl.trim() === '')}
            aria-label={editingId ? '保存实例修改' : '新建实例'}
            data-testid={editingId ? 'instance-save' : 'instance-create'}
          >
            {busy === 'create' || busy === 'edit'
              ? <Loader2 size={11} className="animate-spin" />
              : (editingId ? <Save size={11} /> : <Plus size={11} />)}
            {editingId ? '保存修改' : '新建'}
          </Button>
          {editingId && (
            <Button type="button" variant="default" size="xs" onClick={() => { resetForm(); setMsg('已取消编辑') }} aria-label="取消编辑">
              取消编辑
            </Button>
          )}
          <span className="ml-2 text-micro text-slate-600">
            {tab === 'llm'
              ? '名称、provider、api_endpoint 必填（后端校验，缺了会 400）'
              : '名称与引擎类型必填；custom 还需 api_endpoint'}
          </span>
        </div>
      </div>

      {detail && (
        <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
          <div className="mb-1 flex items-center gap-2 text-caption text-slate-400">
            <CheckCircle2 size={11} className="text-cyan-400" /> 实例详情（可直接编辑后保存，PUT 会整体替换）
            <span className="ml-auto flex items-center gap-1">
              <Button
                type="button"
                variant="accent" size="xs"
                onClick={() => { const id = (detail.id as string) ?? ''; if (id) void saveDetail(id) }}
                disabled={loading}
                aria-label="保存实例详情"
              >
                {busy === 'save' ? <Loader2 size={11} className="animate-spin" /> : null} 保存
              </Button>
              <Button type="button" variant="default" size="xs" onClick={() => setDetail(null)}>关闭</Button>
            </span>
          </div>
          <Textarea
            size="sm"
            className="h-48 w-full font-mono"
            value={detailEdit}
            onChange={(e) => setDetailEdit(e.target.value)}
            aria-label="实例详情 JSON"
          />
        </div>
      )}
    </div>
  )
}