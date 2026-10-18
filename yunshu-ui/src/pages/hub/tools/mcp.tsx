/**
 * MCP 系统 —— MCP 服务 CRUD / 启停 + 内置服务发现与安装
 * 数据源：/api/mcp/services、/api/mcp/enable、
 *         /api/extensions/discover（内置清单）、/api/extensions/install（就地安装）
 *
 * 【2026-10-18 为什么把"内置 MCP 服务"搬进来】它原来在「扩展市场」里单独一组，
 * 与"发现—安装—管理"的完整动线割裂：在那边点安装，安装结果要回本页才看得到。
 * 现在合并到一处 —— 发现与安装就地完成，装完立即出现在服务表里。
 */
import { useCallback, useEffect, useState } from 'react'
import { Download, Loader2, Plus, Trash2, Power } from 'lucide-react'
import {
  Card, Loading, ErrorBox, DataTable, Badge, PageHeader,
  hubDelete, hubGet, hubPost,
} from '../components/ui'

import {
  EXTENSIONS_DISCOVER,
  EXTENSIONS_INSTALL,
  MCP_ENABLE,
  MCP_SERVICES,
  mcpServiceById,
} from '@/api/endpoints';
// 【P1-front 第三批】后端 GET 已带 X-Envelope: v2，改用显式信封解析。
// 注意：同路径的 POST（新建服务）是另一个端点，仍是普通 JSON。
import { getEnvelope } from '@/api/envelope';

interface McpService {
  service_id: string
  id?: string
  name?: string
  command?: string
  transport?: string
  enabled?: boolean
  status?: string
  [k: string]: unknown
}

/** `GET /api/extensions/discover` 里的内置 MCP 模板（builtin_mcp[]） */
interface BuiltinMcp {
  id?: string
  name?: string
  description?: string
  installed?: boolean
  enabled?: boolean
}

interface DiscoverPayload {
  ok?: boolean
  error?: string
  builtin_mcp?: BuiltinMcp[]
}

/** `GET /api/mcp/services` 的业务载荷（后端：`{ok, services}`）。 */
interface McpServiceList {
  ok?: boolean
  services?: McpService[]
}

export default function ToolsMcp() {
  const [services, setServices] = useState<McpService[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [showForm, setShowForm] = useState(false)
  const [name, setName] = useState('')
  const [cmd, setCmd] = useState('')
  const [builtin, setBuiltin] = useState<BuiltinMcp[]>([])
  const [installing, setInstalling] = useState('')

  const load = () => {
    setLoading(true)
    getEnvelope<McpServiceList>(MCP_SERVICES).then((d) => {
      setServices(d?.services ?? [])
      setLoading(false)
    }).catch((e) => { setError(String(e)); setLoading(false) })
  }

  useEffect(load, [])

  const loadBuiltin = useCallback(() => {
    hubGet<DiscoverPayload>(EXTENSIONS_DISCOVER)
      .then((d) => { if (d?.ok !== false) setBuiltin(d?.builtin_mcp ?? []) })
      .catch((e) => setError(String(e)))
  }, [])

  useEffect(loadBuiltin, [loadBuiltin])

  /** 就地安装内置模板：装完立刻重拉服务表（不必回扩展市场看结果） */
  const installBuiltin = async (id: string) => {
    setInstalling(id)
    setError('')
    try {
      await hubPost(EXTENSIONS_INSTALL, { type: 'mcp', source: id })
      load()
      loadBuiltin()
    } catch (e) { setError(String(e)) } finally { setInstalling('') }
  }

  const add = async () => {
    try {
      await hubPost(MCP_SERVICES, { name, command: cmd })
      setShowForm(false)
      setName('')
      setCmd('')
      load()
    } catch (e) { setError(String(e)) }
  }

  const del = async (id: string) => {
    try {
      await hubDelete(mcpServiceById(id))
      load()
    } catch (e) { setError(String(e)) }
  }

  const toggle = async (id: string) => {
    try {
      await hubPost(MCP_ENABLE, { service_id: id })
      load()
    } catch (e) { setError(String(e)) }
  }

  const sid = (s: McpService) => String(s.service_id ?? s.id ?? '')

  return (
    <div className="p-6">
      <PageHeader
        title="MCP 系统"
        description="MCP 服务管理与启停"
        actions={<button onClick={() => setShowForm(!showForm)} className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs text-white hover:bg-blue-500"><Plus size={12} /> 新增服务</button>}
      />
      {error && <div className="mb-4"><ErrorBox message={error} /></div>}
      {showForm && (
        <Card className="mb-4">
          <div className="flex gap-2">
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="服务名称" className="flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-none" />
            <input value={cmd} onChange={(e) => setCmd(e.target.value)} placeholder="启动命令 (command)" className="flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-none" />
            <button onClick={add} className="rounded-lg bg-emerald-600 px-4 py-2 text-sm text-white hover:bg-emerald-500">保存</button>
          </div>
        </Card>
      )}
      {loading ? <Loading /> : (
        <Card>
          <DataTable
            data={services}
            keyField="service_id"
            columns={[
              { key: 'name', title: '服务', render: (r) => <span className="text-slate-200">{String(r.name ?? sid(r))}</span> },
              { key: 'transport', title: '传输', render: (r) => (r.transport ? <Badge color="cyan">{String(r.transport)}</Badge> : '-') },
              { key: 'enabled', title: '状态', render: (r) => <Badge color={r.enabled ? 'green' : 'slate'}>{r.enabled ? '启用' : '停用'}</Badge> },
              {
                key: 'actions', title: '操作',
                render: (r) => (
                  <div className="flex gap-2">
                    <button onClick={() => toggle(sid(r))} className="flex items-center gap-1 rounded-md border border-slate-700 px-2.5 py-1.5 text-xs text-slate-300 hover:bg-slate-800">
                      <Power size={11} /> 切换
                    </button>
                    <button onClick={() => del(sid(r))} className="flex items-center gap-1 rounded-md border border-red-900/60 px-2.5 py-1.5 text-xs text-red-400 hover:bg-red-950">
                      <Trash2 size={11} /> 删除
                    </button>
                  </div>
                ),
              },
            ]}
          />
        </Card>
      )}

      {/* ── 内置 MCP 服务：发现 → 就地安装（原「扩展市场 · 内置 MCP 服务」组） ── */}
      <Card title={'内置 MCP 服务（可安装）' + (builtin.length ? '（' + builtin.length + '）' : '')} className="mt-4">
        <p className="mb-2 text-[10px] text-slate-500">
          来自内置模板；安装后立即出现在上方服务表（不必回扩展市场看结果）。
        </p>
        {builtin.length === 0 && <div className="text-[11px] text-slate-500">没有可安装的内置模板。</div>}
        <div className="flex flex-col gap-1">
          {builtin.map((b) => {
            const id = b.id ?? ''
            return (
              <div key={'builtin-mcp-' + id} className="flex flex-wrap items-center gap-2 rounded border border-slate-800 bg-slate-950/60 px-2 py-1 text-[11px]">
                <span className="font-mono text-[10px] text-cyan-300">{id}</span>
                <span className="text-slate-300">{b.name ?? ''}</span>
                <span className="truncate text-[10px] text-slate-500">{b.description ?? ''}</span>
                <span className="ml-auto flex items-center gap-1.5">
                  {b.enabled
                    ? <span className="text-[10px] text-emerald-400">已启用</span>
                    : b.installed
                      ? <span className="text-[10px] text-slate-400">已装（未启用）</span>
                      : (
                        <button
                          type="button"
                          onClick={() => void installBuiltin(id)}
                          disabled={installing !== ''}
                          aria-label={'安装内置 MCP ' + id}
                          className="flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {installing === id ? <Loader2 size={11} className="animate-spin" /> : <Download size={11} />} 安装
                        </button>
                      )}
                </span>
              </div>
            )
          })}
        </div>
      </Card>
    </div>
  )
}
