/**
 * 网络配置 —— 全局出口参数 + 搜索默认（**LLM 实例不在这里**）
 * ------------------------------------------------------------------
 * 数据源：GET|POST /api/network-config、POST /api/network-config/reset、GET /api/search/instances。
 *
 * 【2026-11-22 收口：删掉重复的「LLM 配置」卡】
 * 原页面有一张 provider/model/api_endpoint/api_key/timeout 卡，和「实例管理」的 LLM 实例表单
 * 是同一件事；更糟的是它读写的是**顶层**键（旧扁平 schema），而后端只认嵌套的
 * `llm.*` / `network.*` / `search.*`，并优先 `llm_instances` —— 真机 8 个输入全是空，
 * 保存只会往文件里写 `api_key:""` / `timeout:""` 两个畸形顶层键（其中 api_key 还是明文落盘）。
 * 现在：LLM 只在「实例管理」配；本页只做全局出口与搜索默认，且**只提交这两个分区**
 * （不再把整份 GET 响应回灌，避免把脱敏 key、实例列表、change_log 一起写回去）。
 *
 * 【默认引擎为什么是下拉】后端 `apply_search_instances()` 按**搜索实例 id** 校验
 * default_engine（指向不存在的实例会被清空），手填 id 没有意义 —— 下拉直接列实例。
 * engine_priority 由实例注册顺序自动重建，本页只显示数量、不提供编辑。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { RotateCcw, Save, Search } from 'lucide-react'
import ConfirmDialog from '@/components/ConfirmDialog'
import {
  Button, Card, ErrorBox, hubGet, hubPost, Input, Loading, Notice, SectionTitle, Select, Switch, unwrap,
} from '../components/ui'
import {
  NETWORK_CONFIG,
  NETWORK_CONFIG_RESET,
  SEARCH_INSTANCES,
} from '@/api/endpoints'
import {
  NetworkFormError,
  buildNetworkPayload,
  type NetworkDraft,
  type SearchDraft,
} from '@/lib/networkConfig'
import NetworkTransfer from './TransferPanel'

interface SearchInstance {
  id?: string
  name?: string
  engine_type?: string
  enabled?: boolean
}

const EMPTY_NET: NetworkDraft = {
  timeout: '30', max_retries: '3', backoff_factor: '0.5', proxy_enabled: false, proxy_url: '',
}
const EMPTY_SEARCH: SearchDraft = {
  enabled: true, default_engine: '', max_results: '10', timeout: '30', cache_ttl: '300',
}

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e))

export default function NetworkPage() {
  const [net, setNet] = useState<NetworkDraft>(EMPTY_NET)
  const [search, setSearch] = useState<SearchDraft>(EMPTY_SEARCH)
  const [instances, setInstances] = useState<SearchInstance[]>([])
  const [priorityCount, setPriorityCount] = useState(0)
  const [proxyMasked, setProxyMasked] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')
  const [saving, setSaving] = useState(false)
  const [confirmReset, setConfirmReset] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [raw, searchRaw] = await Promise.all([
        hubGet<Record<string, unknown>>(NETWORK_CONFIG),
        // 实例列表失败不影响本页主表单：下拉退化为"不指定"
        hubGet<Record<string, unknown>>(SEARCH_INSTANCES).catch(() => null),
      ])
      const data = unwrap<Record<string, unknown>>(raw) ?? {}
      const n = (data.network ?? {}) as Record<string, unknown>
      const s = (data.search ?? {}) as Record<string, unknown>
      setNet({
        timeout: String(n.timeout ?? '30'),
        max_retries: String(n.max_retries ?? '3'),
        backoff_factor: String(n.backoff_factor ?? '0.5'),
        proxy_enabled: n.proxy_enabled === true,
        proxy_url: String(n.proxy_url ?? ''),
      })
      setSearch({
        enabled: s.enabled !== false,
        default_engine: String(s.default_engine ?? ''),
        max_results: String(s.max_results ?? '10'),
        timeout: String(s.timeout ?? '30'),
        cache_ttl: String(s.cache_ttl ?? '300'),
      })
      setPriorityCount(Array.isArray(s.engine_priority) ? s.engine_priority.length : 0)
      const list = searchRaw ? (unwrap<{ instances?: SearchInstance[] }>(searchRaw)?.instances ?? []) : []
      setInstances(Array.isArray(list) ? list : [])
      setProxyMasked(String(n.proxy_url ?? '').includes('***'))
    } catch (e) {
      setError('读取网络配置失败：' + errText(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const save = async () => {
    setError('')
    setMsg('')
    let payload: Record<string, unknown>
    try {
      payload = buildNetworkPayload(net, search)
    } catch (e) {
      if (e instanceof NetworkFormError) { setError(e.message); return }
      throw e
    }
    setSaving(true)
    try {
      const r = await hubPost<{ ok?: boolean; error?: string; errors?: string[] }>(NETWORK_CONFIG, payload)
      if (r?.ok === false) {
        setError('保存失败：' + (r.error || (r.errors?.length ? r.errors.join('；') : '后端返回 ok=false')))
        return
      }
      setMsg('网络配置已保存并应用')
      await load()
    } catch (e) {
      setError('保存失败：' + errText(e))
    } finally {
      setSaving(false)
    }
  }

  const reset = async () => {
    setError('')
    setMsg('')
    setSaving(true)
    try {
      const r = await hubPost<{ ok?: boolean; error?: string }>(NETWORK_CONFIG_RESET, {})
      if (r?.ok === false) { setError('重置失败：' + (r.error || '后端返回 ok=false')); return }
      setMsg('已重置为默认网络配置')
      setConfirmReset(false)
      await load()
    } catch (e) {
      setError('重置失败：' + errText(e))
    } finally {
      setSaving(false)
    }
  }

  const engineOptions = useMemo(() => {
    const opts = instances.map((it) => ({
      label: (it.name || it.id || '') + (it.engine_type ? '（' + it.engine_type + '）' : ''),
      value: String(it.id || ''),
    }))
    // 配置里指向已删除/未拉到的实例时如实列出来，别让它"看起来没选"
    if (search.default_engine && !opts.some((o) => o.value === search.default_engine)) {
      opts.unshift({ label: search.default_engine + '（不在当前实例列表）', value: search.default_engine })
    }
    return opts
  }, [instances, search.default_engine])

  return (
    <div className="p-6">
      <SectionTitle
        title="网络配置"
        description="全局出口参数与搜索默认；LLM 的 provider / model / 密钥请在「实例管理」里配"
        actions={(
          <div className="flex items-center gap-2">
            <Button onClick={() => setConfirmReset(true)} variant="default" size="sm" data-testid="network-reset">
              <RotateCcw size={12} /> 重置
            </Button>
            <Button variant="primary" size="sm" onClick={() => void save()} disabled={saving} data-testid="network-save">
              <Save size={12} /> {saving ? '保存中…' : '保存并应用'}
            </Button>
          </div>
        )}
      />

      <div className="mb-4 rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2 text-caption text-slate-400">
        LLM 实例（provider / model / api_key / 接口地址）在「实例管理」Tab：多实例、可设默认、可测连通。
        本页只保存 <code>network</code> 与 <code>search</code> 两个分区，不会改动实例与密钥。
      </div>

      {error && <div className="mb-4"><ErrorBox message={error} /></div>}
      {msg && <div className="mb-4"><Notice tone="success" size="sm" data-testid="network-msg">{msg}</Notice></div>}

      {loading ? <Loading /> : (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card title="网络（出口）">
            <div className="space-y-3">
              <div className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2">
                <div>
                  <div className="text-xs text-slate-300">启用代理</div>
                  <div className="text-micro text-slate-500">关闭时 HTTP 客户端直连</div>
                </div>
                <Switch
                  checked={net.proxy_enabled}
                  onCheckedChange={(v) => setNet((cur) => ({ ...cur, proxy_enabled: v }))}
                  label="启用代理"
                  data-testid="network-proxy-enabled"
                />
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                <label className="text-caption text-slate-400">网络超时（秒）
                  <Input size="md" className="mt-1" inputMode="numeric" value={net.timeout}
                    onChange={(e) => setNet((cur) => ({ ...cur, timeout: e.target.value }))}
                    data-testid="network-timeout" aria-label="网络超时" />
                </label>
                <label className="text-caption text-slate-400">重试次数
                  <Input size="md" className="mt-1" inputMode="numeric" value={net.max_retries}
                    onChange={(e) => setNet((cur) => ({ ...cur, max_retries: e.target.value }))}
                    data-testid="network-retries" aria-label="重试次数" />
                </label>
                <label className="text-caption text-slate-400">退避系数
                  <Input size="md" className="mt-1" inputMode="decimal" value={net.backoff_factor}
                    onChange={(e) => setNet((cur) => ({ ...cur, backoff_factor: e.target.value }))}
                    data-testid="network-backoff" aria-label="退避系数" />
                </label>
              </div>

              <label className="block text-caption text-slate-400">代理地址（含认证时形如 http://user:pass@host:port）
                <Input size="md" className="mt-1" value={net.proxy_url}
                  onChange={(e) => setNet((cur) => ({ ...cur, proxy_url: e.target.value }))}
                  data-testid="network-proxy-url" aria-label="代理地址"
                  placeholder={proxyMasked ? '已配置（脱敏展示）；不改动 = 保留原值' : 'http://host:port'} />
              </label>
              {proxyMasked && (
                <p className="text-micro text-amber-400/80" data-testid="network-proxy-masked">
                  当前地址已脱敏（***）：不改动就保留原值；要替换请重填完整地址（含密码）。
                </p>
              )}
            </div>
          </Card>

          <Card title="搜索默认">
            <div className="space-y-3">
              <div className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2">
                <div>
                  <div className="text-xs text-slate-300">启用搜索</div>
                  <div className="text-micro text-slate-500">关闭后 web_search 不可用（仍保留实例登记）</div>
                </div>
                <Switch
                  checked={search.enabled}
                  onCheckedChange={(v) => setSearch((cur) => ({ ...cur, enabled: v }))}
                  label="启用搜索"
                  data-testid="search-enabled"
                />
              </div>

              <div>
                <div className="mb-1 flex items-center gap-1 text-caption text-slate-400">
                  <Search size={11} className="text-cyan-400" /> 默认搜索引擎
                </div>
                <Select
                  size="md"
                  value={search.default_engine}
                  onChange={(v) => setSearch((cur) => ({ ...cur, default_engine: v }))}
                  data-testid="search-default-engine"
                  aria-label="默认搜索引擎"
                  placeholder="（不指定：按实例优先级自动选）"
                  placeholderOptionDisabled={false}
                  options={engineOptions}
                />
                <p className="mt-1 text-micro text-slate-500">
                  下拉来自「实例管理 → 搜索实例」；引擎优先级由实例注册顺序自动维护
                  （当前 {priorityCount} 项），本页不提供编辑。
                </p>
              </div>

              <div className="grid gap-3 sm:grid-cols-3">
                <label className="text-caption text-slate-400">结果上限
                  <Input size="md" className="mt-1" inputMode="numeric" value={search.max_results}
                    onChange={(e) => setSearch((cur) => ({ ...cur, max_results: e.target.value }))}
                    data-testid="search-max-results" aria-label="搜索结果上限" />
                </label>
                <label className="text-caption text-slate-400">搜索超时（秒）
                  <Input size="md" className="mt-1" inputMode="numeric" value={search.timeout}
                    onChange={(e) => setSearch((cur) => ({ ...cur, timeout: e.target.value }))}
                    data-testid="search-timeout" aria-label="搜索超时" />
                </label>
                <label className="text-caption text-slate-400">缓存 TTL（秒）
                  <Input size="md" className="mt-1" inputMode="numeric" value={search.cache_ttl}
                    onChange={(e) => setSearch((cur) => ({ ...cur, cache_ttl: e.target.value }))}
                    data-testid="search-cache-ttl" aria-label="缓存 TTL" />
                </label>
              </div>
            </div>
          </Card>
        </div>
      )}

      <NetworkTransfer />

      <ConfirmDialog
        open={confirmReset}
        title="重置网络配置"
        message="会恢复默认的网络/搜索参数，并清空 LLM 实例、搜索实例与 MCP 服务登记；.env 里的密钥不受影响。确定重置？"
        details={['影响分区：network / search / llm / llm_instances / search_instances / mcp', '.env 中的密钥不会被删除']}
        confirmText="重置为默认"
        danger
        loading={saving}
        onConfirm={() => void reset()}
        onCancel={() => setConfirmReset(false)}
      />
    </div>
  )
}