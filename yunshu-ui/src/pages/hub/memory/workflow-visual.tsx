/**
 * 可视化编辑 —— 工作台 hubNav「记忆管理 → 可视化编辑」页
 *
 * 能力与说明（保存/加载按后端实际能力实现）：
 * - 画布 = VisualEditor（@xyflow/react 节点编排 + undo/redo + YAML 预览）
 * - 保存：把当前画布（nodes/edges + 生成的 YAML）POST 到
 *   /api/visual-workflows（后端新增的最小草稿存储，与 workflow-learning
 *   的学习工作流隔离；见 agent/server_routes/routes_visual_workflows.py）
 * - 加载：从 /api/visual-workflows 列表选择草稿 → GET 完整图 → 重建画布
 * - 后端不可达 / 未配置 API 令牌（401）时给出明确错误提示，仍可使用
 *   VisualEditor 自带「导出 YAML」离线导出
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  AlertTriangle, CheckCircle2, ChevronDown, FolderOpen, Loader2,
  PenTool, Plus, Save, Trash2, X,
} from 'lucide-react'
import { VisualEditor } from '@/components/VisualEditor'
import { useFlowStore } from '@/components/VisualEditor/stores/useFlowStore'
import { generateYaml } from '@/components/VisualEditor/generator/CodeGenerator'
import {
  deleteVisualWorkflow,
  deserializeEdges,
  deserializeNodes,
  getVisualWorkflow,
  listVisualWorkflows,
  saveVisualWorkflow,
  serializeGraph,
  type VisualWorkflowSummary,
} from '@/lib/visualWorkflowApi'

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

export default function MemoryWorkflowVisual() {
  const [drafts, setDrafts] = useState<VisualWorkflowSummary[]>([])
  const [name, setName] = useState('未命名工作流')
  const [currentId, setCurrentId] = useState<string | null>(null)
  const [loadingList, setLoadingList] = useState(false)
  const [busy, setBusy] = useState<'save' | 'delete' | null>(null)
  const [listOpen, setListOpen] = useState(false)
  const [status, setStatus] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)
  const dropRef = useRef<HTMLDivElement>(null)

  const nodes = useFlowStore((s) => s.nodes)
  const edges = useFlowStore((s) => s.edges)
  const loadGraph = useFlowStore((s) => s.loadGraph)
  const clearCanvas = useFlowStore((s) => s.clearCanvas)

  const refresh = useCallback(async (silent = false) => {
    if (!silent) setLoadingList(true)
    try {
      setDrafts(await listVisualWorkflows())
    } catch (e) {
      setStatus({
        kind: 'err',
        text: `读取后端草稿列表失败：${errText(e)}。后端不可用时可先用「导出 YAML」离线保存。`,
      })
    } finally {
      if (!silent) setLoadingList(false)
    }
  }, [])

  // 首次进入拉取已保存草稿列表
  useEffect(() => {
    void refresh()
  }, [refresh])

  // 点击下拉外部时收起
  useEffect(() => {
    if (!listOpen) return
    const onDocClick = (e: MouseEvent) => {
      if (dropRef.current && !dropRef.current.contains(e.target as Node)) setListOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [listOpen])

  const handleSave = async () => {
    const trimmed = name.trim() || '未命名工作流'
    if (nodes.length === 0) {
      setStatus({ kind: 'err', text: '画布为空：请先从左侧拖入节点并连线后再保存' })
      return
    }
    const graph = serializeGraph(nodes, edges)
    setBusy('save')
    try {
      const res = await saveVisualWorkflow({
        id: currentId ?? undefined,
        name: trimmed,
        description: `${graph.nodes.length} 节点 · ${graph.edges.length} 连线 · 可视化编排`,
        nodes: graph.nodes,
        edges: graph.edges,
        yaml: generateYaml(nodes, edges),
      })
      setCurrentId(res.id)
      setStatus({
        kind: 'ok',
        text: res.action === 'created'
          ? `已保存到后端（ID: ${res.id}，下次保存将覆盖更新）`
          : `已更新（ID: ${res.id}）`,
      })
      void refresh(true)
    } catch (e) {
      setStatus({
        kind: 'err',
        text: `保存失败：${errText(e)}。若返回 401/未授权，请在界面 API Token 输入框填入后端 FLASK_API_TOKEN；也可先用「导出 YAML」离线保存。`,
      })
    } finally {
      setBusy(null)
    }
  }

  const handleLoad = async (id: string) => {
    try {
      const detail = await getVisualWorkflow(id)
      const nextNodes = deserializeNodes(detail.nodes)
      const nextEdges = deserializeEdges(detail.edges)
      loadGraph(nextNodes, nextEdges)
      setName(detail.name || '未命名工作流')
      setCurrentId(detail.id)
      setListOpen(false)
      setStatus({
        kind: 'ok',
        text: `已加载「${detail.name || detail.id}」（${nextNodes.length} 节点 / ${nextEdges.length} 连线）`,
      })
    } catch (e) {
      setStatus({ kind: 'err', text: `加载失败：${errText(e)}` })
    }
  }

  const handleDelete = async (id: string) => {
    const label = drafts.find((d) => d.id === id)?.name ?? id
    if (!window.confirm(`确定从后端删除草稿「${label}」？此操作不可撤销。`)) return
    setBusy('delete')
    try {
      await deleteVisualWorkflow(id)
      if (currentId === id) setCurrentId(null)
      void refresh(true)
      setStatus({ kind: 'ok', text: `已删除「${label}」` })
    } catch (e) {
      setStatus({ kind: 'err', text: `删除失败：${errText(e)}` })
    } finally {
      setBusy(null)
    }
  }

  const handleNew = () => {
    if (nodes.length > 0 && !window.confirm('清空当前画布并新建空白工作流？')) return
    clearCanvas()
    setCurrentId(null)
    setName('未命名工作流')
    setStatus({ kind: 'ok', text: '已新建空白画布（点击保存写入后端）' })
  }

  const toggleList = () => {
    const next = !listOpen
    setListOpen(next)
    if (next && drafts.length === 0) void refresh(false)
  }

  return (
    <div className="ve-page flex h-full min-h-0 flex-col gap-2.5 p-3">
      {/* ─── 顶部操作条：保存 / 加载 / 新建 ─── */}
      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-slate-800 bg-slate-900/60 px-4 py-2.5">
        <div className="flex items-center gap-2 text-sm text-slate-200">
          <PenTool size={14} className="text-cyan-400" />
          <span className="font-medium">可视化编辑</span>
        </div>
        <span className="hidden text-[11px] text-slate-500 lg:inline">
          拖拽编排 → 保存到后端 /api/visual-workflows，随时重新加载
        </span>

        <div className="ml-auto flex flex-wrap items-center gap-2">
          <input
            className="w-44 rounded-md border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-200 outline-none transition-colors placeholder:text-slate-600 focus:border-cyan-600"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="工作流名称（保存时使用）"
            title="工作流名称（保存时使用）"
            data-testid="ve-save-name"
          />
          <button
            type="button"
            onClick={handleSave}
            disabled={busy !== null || nodes.length === 0}
            className="flex items-center gap-1.5 rounded-md bg-cyan-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-cyan-500 disabled:cursor-not-allowed disabled:opacity-50"
            data-testid="ve-save-btn"
          >
            {busy === 'save' ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
            {currentId ? '更新' : '保存'}
          </button>

          {/* 已保存草稿列表（加载入口） */}
          <div className="relative" ref={dropRef}>
            <button
              type="button"
              onClick={toggleList}
              className="flex items-center gap-1.5 rounded-md border border-slate-700 px-3 py-1.5 text-xs text-slate-300 transition-colors hover:bg-slate-800"
              data-testid="ve-open-list-btn"
            >
              {loadingList ? <Loader2 size={12} className="animate-spin" /> : <FolderOpen size={12} />}
              加载
              <ChevronDown size={11} className={`transition-transform ${listOpen ? 'rotate-180' : ''}`} />
              {drafts.length > 0 && (
                <span className="rounded-full bg-cyan-500/15 px-1.5 text-[10px] text-cyan-300">
                  {drafts.length}
                </span>
              )}
            </button>

            {listOpen && (
              <div
                className="absolute right-0 top-full z-50 mt-1.5 max-h-80 w-[21rem] overflow-y-auto rounded-lg border border-slate-700 bg-slate-900 p-1.5 shadow-2xl"
                data-testid="ve-save-list"
              >
                <div className="flex items-center justify-between px-2 pb-1.5 pt-1">
                  <span className="text-[11px] uppercase tracking-wider text-slate-500">后端已保存草稿</span>
                  <button
                    type="button"
                    onClick={() => void refresh()}
                    className="text-[11px] text-cyan-400 hover:text-cyan-300"
                    title="刷新列表"
                  >
                    刷新
                  </button>
                </div>
                {drafts.length === 0 ? (
                  <div className="px-2 py-6 text-center text-xs text-slate-500">
                    {loadingList ? '加载中…' : '暂无草稿。编排完成后点「保存」写入后端。'}
                  </div>
                ) : (
                  <ul className="space-y-1">
                    {drafts.map((d) => (
                      <li
                        key={d.id}
                        className={`flex items-center gap-2 rounded-md px-2 py-1.5 text-xs transition-colors ${
                          currentId === d.id ? 'bg-cyan-500/10' : 'hover:bg-slate-800/70'
                        }`}
                      >
                        <button
                          type="button"
                          className="min-w-0 flex-1 text-left"
                          onClick={() => void handleLoad(d.id)}
                          title={`${d.description ?? ''} · 更新于 ${d.updated_at ?? ''}`}
                          data-testid={`ve-load-${d.id}`}
                        >
                          <span className="block truncate text-slate-200">
                            {d.name || d.id}
                            {currentId === d.id && <span className="ml-1 text-[10px] text-cyan-400">(当前)</span>}
                          </span>
                          <span className="block text-[10px] text-slate-500">
                            {d.node_count ?? 0} 节点 / {d.edge_count ?? 0} 连线 · {d.updated_at ?? d.id}
                          </span>
                        </button>
                        <button
                          type="button"
                          className="shrink-0 rounded p-1 text-slate-500 transition-colors hover:bg-red-500/10 hover:text-red-400"
                          onClick={() => void handleDelete(d.id)}
                          title="从后端删除"
                          data-testid={`ve-delete-${d.id}`}
                        >
                          <Trash2 size={12} />
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>

          <button
            type="button"
            onClick={handleNew}
            className="flex items-center gap-1.5 rounded-md border border-slate-700 px-3 py-1.5 text-xs text-slate-300 transition-colors hover:bg-slate-800"
            title="清空画布并新建"
            data-testid="ve-new-btn"
          >
            <Plus size={12} /> 新建
          </button>
        </div>
      </div>

      {/* ─── 状态提示 ─── */}
      {status && (
        <div
          className={`flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs ${
            status.kind === 'ok'
              ? 'border-emerald-800/70 bg-emerald-950/40 text-emerald-400'
              : 'border-amber-800/70 bg-amber-950/40 text-amber-400'
          }`}
          data-testid="ve-status"
        >
          {status.kind === 'ok' ? <CheckCircle2 size={13} /> : <AlertTriangle size={13} />}
          <span className="min-w-0 flex-1">{status.text}</span>
          <button type="button" onClick={() => setStatus(null)} className="shrink-0 text-slate-500 hover:text-slate-300">
            <X size={12} />
          </button>
        </div>
      )}

      {/* ─── 可视化编辑器（深色主题宿主）─── */}
      <div className="ve-dark min-h-0 flex-1 overflow-hidden rounded-xl border border-slate-800">
        <VisualEditor />
      </div>
    </div>
  )
}
