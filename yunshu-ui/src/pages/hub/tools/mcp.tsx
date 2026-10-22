/**
 * MCP 系统 —— MCP 服务 CRUD / 启停 / 卸载 + 内置服务发现与安装
 * 数据源：/api/mcp/services（增删改查）、
 *         /api/extensions/toggle + /api/extensions/uninstall（启停/卸载，同一份 MCP 配置
 *         与扩展登记一起收敛；原先的 /api/mcp/enable 是**全局开关**，按行调用会把整个
 *         MCP 子系统关掉 —— 2026-10-20 修）、
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
  hubGet, hubPost,
} from '../components/ui'

import {
  EXTENSIONS_DISCOVER,
  EXTENSIONS_INSTALL,
  EXTENSIONS_TOGGLE,
  EXTENSIONS_UNINSTALL,
  MCP_SERVICES,
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
  // 卸载二次确认（不可逆动作不能一点就走；force 只认布尔 true）
  const [confirmUninstall, setConfirmUninstall] = useState('')
  // 行内错误（启停/卸载）：与页面级 error 分开，避免一次失败把所有内容顶掉
  const [rowError, setRowError] = useState('')

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

  /** 卸载：删服务配置 + 扩展登记（uninstall_mcp 两条一起收敛） */
  const uninstall = async (id: string) => {
    setRowError('')
    try {
      const r = await hubPost<{ ok?: boolean; message?: string; error?: string }>(
        EXTENSIONS_UNINSTALL, { type: 'mcp', id })
      if (r?.ok === false) { setRowError('卸载失败：' + (r.error || r.message || '未知错误')); return }
      setConfirmUninstall('')
      load()
    } catch (e) { setRowError('卸载失败：' + String(e)) }
  }

  /** 启停：按行切换该服务的 enabled（不是全局 MCP 开关） */
  const toggle = async (r: McpService) => {
    setRowError('')
    try {
      const res = await hubPost<{ ok?: boolean; message?: string; error?: string }>(
        EXTENSIONS_TOGGLE, { type: 'mcp', id: sid(r), enabled: !r.enabled })
      if (res?.ok === false) { setRowError('切换失败：' + (res.error || res.message || '未知错误')); return }
      load()
    } catch (e) { setRowError('切换失败：' + String(e)) }
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
      {rowError && <div className="mb-4"><ErrorBox message={rowError} /></div>}
      {showForm && (
        <Card className="mb-4">
          <div className="flex gap-2">
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="服务名称" className="flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-hidden" />
            <input value={cmd} onChange={(e) => setCmd(e.target.value)} placeholder="启动命令 (command)" className="flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-hidden" />
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
                render: (r) => {
                  const id = sid(r)
                  const armed = confirmUninstall === id
                  return (
                    <div className="flex flex-wrap items-center gap-2">
                      <button
                        onClick={() => void toggle(r)}
                        aria-label={'启停 ' + id}
                        className="flex items-center gap-1 rounded-md border border-slate-700 px-2.5 py-1.5 text-xs text-slate-300 hover:bg-slate-800"
                      >
                        <Power size={11} /> {r.enabled ? '停用' : '启用'}
                      </button>
                      <button
                        onClick={() => setConfirmUninstall(armed ? '' : id)}
                        aria-label={'卸载 ' + id}
                        className="flex items-center gap-1 rounded-md border border-red-900/60 px-2.5 py-1.5 text-xs text-red-400 hover:bg-red-950"
                      >
                        <Trash2 size={11} /> {armed ? '确认卸载' : '卸载'}
                      </button>
                      {armed && (
                        <span className="flex items-center gap-1 text-[10px] text-red-300">
                          不可逆（服务配置 + 扩展登记一并删除）
                          <button
                            onClick={() => void uninstall(id)}
                            aria-label={'确认卸载 MCP ' + id}
                            className="rounded-sm border border-red-800/70 px-2 py-1 text-[10px] text-red-200 hover:bg-red-500/10"
                          >
                            确定
                          </button>
                          <button onClick={() => setConfirmUninstall('')} className="rounded-sm border border-slate-700 px-2 py-1 text-[10px] text-slate-300 hover:bg-slate-800">取消</button>
                        </span>
                      )}
                    </div>
                  )
                },
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
              <div key={'builtin-mcp-' + id} className="flex flex-wrap items-center gap-2 rounded-sm border border-slate-800 bg-slate-950/60 px-2 py-1 text-[11px]">
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
