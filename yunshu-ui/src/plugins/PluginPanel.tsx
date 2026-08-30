/**
 * PluginPanel —— 插件中心（任务 T3.3 + T4.2：运行时发现）
 *
 * 行为：
 * 1. 挂载时 fetchPlugins()（GET /api/plugins）拉取全部插件 manifest；
 * 2. 左侧列表：插件名 + 版本 + 描述（自解释）；点击右侧渲染详情；
 * 3. 「刷新」按钮 → reloadPlugins()（POST /api/plugins/reload 后重新拉取）：
 *    成功 → 列表更新 + 成功 Toast；失败 → 错误 Toast + **保留旧列表**；
 *    刷新中 → 按钮禁用（加载态）；
 * 4. 详情：schema 非空 → <SchemaRenderer/> 自动生成表单；schema 为空 →
 *    「该插件暂无可配置界面」+ 其 routes 列表；
 * 5. 值预填：选中带 submitUrl 的插件时，先 GET submitUrl 读取当前生效值
 *    （仅取 schema 已声明的字段），失败静默回退 schema default；
 * 6. 提交：解析提交端点（插件声明 submit_url，兜底前端映射表）→ POST 表单值；
 *    成功/失败经全局 chat store 的 Toast 提示；成功后刷新当前值；
 * 7. 进阶（T4.2）：manifest 声明 clientSlot 的插件，列表项显示「加载 UI」按钮，
 *    点击后 loadClientUi() 动态 import 客户端模块并挂入插槽；失败 Toast 不影响
 *    其他功能。
 *
 * 提交端点解析（设计契约）：
 * - 插件声明 submit_url 优先（后端 Plugin 字段，写入 manifest）；
 * - 未声明 → SUBMIT_URL_FALLBACK 按插件名兜底（仅放「提交体与端点契约一致」的
 *   真实端点，不硬凑）；仍为空 → 不渲染提交按钮并提示「该插件暂不支持在线修改」。
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, request } from '../lib/apiClient';
import { useChatStore } from '../store/useChatStore';
import { SchemaRenderer } from './schema/SchemaRenderer';
import { fetchPlugins, loadClientUi, PluginInfo, reloadPlugins } from './pluginDiscovery';

/**
 * 提交端点兜底映射（插件未声明 submit_url 时按插件名兜底）。
 * 约束：仅收录「POST 提交体为扁平配置对象且端点契约一致」的真实端点；
 * 不确定的端点一律不收录（按风险项：submit_url 留空并提示，不硬凑）。
 */
export const SUBMIT_URL_FALLBACK: Record<string, string> = {
  memory: '/api/context/config',
  admin: '/api/config',
};

/** 解析插件提交端点：声明优先，其次前端映射表，最后空串（不支持在线修改） */
export function resolveSubmitUrl(plugin: PluginInfo): string {
  if (plugin.submitUrl && plugin.submitUrl.trim()) return plugin.submitUrl;
  return SUBMIT_URL_FALLBACK[plugin.name] ?? '';
}

/** 是否含可配置 schema（顶层 object + 至少一个属性） */
function hasConfigSchema(schema: Record<string, any> | null): boolean {
  return !!schema && typeof schema === 'object' && schema.type === 'object' && !!schema.properties;
}

/** 从后端当前值里挑选 schema 已声明字段（防止多余字段污染表单值） */
function pickSchemaFields(
  schema: Record<string, any> | null,
  current: Record<string, any>,
): Record<string, any> {
  if (!schema || typeof schema !== 'object' || !schema.properties) return {};
  const picked: Record<string, any> = {};
  for (const key of Object.keys(schema.properties)) {
    if (Object.prototype.hasOwnProperty.call(current, key)) picked[key] = current[key];
  }
  return picked;
}

export function PluginPanel() {
  const [plugins, setPlugins] = useState<PluginInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [values, setValues] = useState<Record<string, any>>({});
  const [loadingValues, setLoadingValues] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [loadingUi, setLoadingUi] = useState<string | null>(null);
  const addToast = useChatStore((s) => s.addToast);
  /** 当前选中插件名（同步读写，用于异步值加载的竞态守卫） */
  const selectedRef = useRef<string | null>(null);
  /** 用户已修改过的字段（预填合并时不被覆盖；避免「先编辑、后预填到达」丢失输入） */
  const touchedRef = useRef<Set<string>>(new Set());

  // ─── 挂载：拉取插件清单（首个插件的选中与值预填交给下方自动选中 effect） ───
  useEffect(() => {
    const ac = new AbortController();
    fetchPlugins(ac.signal)
      .then((list) => setPlugins(list))
      .catch((e: unknown) => {
        // 组件卸载导致的取消不视为错误
        if (e instanceof ApiError && e.code === 'API_REQUEST_ABORTED') return;
        setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => setLoading(false));
    return () => ac.abort();
  }, []);

  const selectedPlugin = plugins.find((p) => p.name === selected) ?? null;

  /** 拉取选中插件的当前生效值（仅 schema 声明字段；失败静默回退 default） */
  const loadCurrentValues = useCallback((plugin: PluginInfo) => {
    const url = resolveSubmitUrl(plugin);
    if (!url) return;
    const ac = new AbortController();
    setLoadingValues(true);
    request<Record<string, any>>(url, { signal: ac.signal })
      .then((current) => {
        if (selectedRef.current !== plugin.name) return; // 已切换插件，丢弃过期响应
        if (current && typeof current === 'object' && !Array.isArray(current)) {
          setValues((prev) => {
            const merged = { ...prev, ...pickSchemaFields(plugin.schema, current) };
            // 用户已改过的字段优先保留（预填只补未触碰的字段）
            for (const key of touchedRef.current) {
              if (Object.prototype.hasOwnProperty.call(prev, key)) merged[key] = prev[key];
            }
            return merged;
          });
        }
      })
      .catch(() => {
        /* 读值失败 → 保留 schema default 填充，不打扰用户 */
      })
      .finally(() => {
        if (selectedRef.current === plugin.name) setLoadingValues(false);
      });
  }, []);

  const selectPlugin = useCallback(
    (name: string) => {
      selectedRef.current = name;
      setSelected(name);
      setValues({});
      touchedRef.current = new Set();
      setLoadingValues(false);
      const plugin = plugins.find((p) => p.name === name);
      if (plugin) loadCurrentValues(plugin);
    },
    [plugins, loadCurrentValues],
  );

  /** SchemaRenderer 值变化：记录被修改的字段，供预填合并保护 */
  const handleValueChange = useCallback((next: Record<string, any>) => {
    setValues((prev) => {
      const touched = new Set(touchedRef.current);
      for (const [key, v] of Object.entries(next)) {
        if (!Object.prototype.hasOwnProperty.call(prev, key) || prev[key] !== v) {
          touched.add(key);
        }
      }
      touchedRef.current = touched;
      return next;
    });
  }, []);

  // 首次载入清单后自动选中第一个插件（若尚未选择）
  useEffect(() => {
    if (!loading && !selected && plugins.length > 0) {
      selectedRef.current = plugins[0].name;
      setSelected(plugins[0].name);
      loadCurrentValues(plugins[0]);
    }
  }, [loading, selected, plugins, loadCurrentValues]);

  /**
   * 刷新（T4.2）：POST /api/plugins/reload 后重新拉取。
   * 成功 → 列表替换 + 成功 Toast；失败 → 错误 Toast + **保留旧列表**（不 setPlugins）。
   */
  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const list = await reloadPlugins();
      setPlugins(list);
      addToast('success', `插件清单已刷新，共 ${list.length} 个插件`);
      // 选中项仍在 → 清空表单并重新预填当前值；已消失 → 交给自动选中 effect
      const keep = selectedRef.current && list.some((p) => p.name === selectedRef.current);
      if (keep) {
        const p = list.find((x) => x.name === selectedRef.current)!;
        setValues({});
        touchedRef.current = new Set();
        setLoadingValues(false);
        loadCurrentValues(p);
      } else {
        selectedRef.current = null;
        setSelected(null);
        setValues({});
        touchedRef.current = new Set();
      }
    } catch (e) {
      addToast('error', `刷新插件清单失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setRefreshing(false);
    }
  }, [addToast, loadCurrentValues]);

  /** 动态装载 clientSlot 插件 UI（进阶，T4.2）：失败 Toast，不影响其他功能 */
  const handleLoadUi = useCallback(
    async (plugin: PluginInfo) => {
      if (!plugin.clientSlot) return;
      setLoadingUi(plugin.name);
      try {
        await loadClientUi(plugin);
        addToast('success', `「${plugin.name}」UI 已加载并挂入 ${plugin.clientSlot.slotId} 插槽`);
      } catch (e) {
        addToast('error', `「${plugin.name}」UI 加载失败：${e instanceof Error ? e.message : String(e)}`);
      } finally {
        setLoadingUi(null);
      }
    },
    [addToast],
  );

  const submitUrl = selectedPlugin ? resolveSubmitUrl(selectedPlugin) : '';

  const handleSubmit = useCallback(
    async (formValues: Record<string, any>) => {
      if (!selectedPlugin || !submitUrl) return;
      setSubmitting(true);
      try {
        const res = await request<{ ok?: boolean; error?: string }>(submitUrl, {
          method: 'POST',
          body: formValues,
        });
        if (res && res.ok === false) {
          addToast('error', res.error || `「${selectedPlugin.name}」配置提交失败`);
        } else {
          addToast('success', `「${selectedPlugin.name}」配置已生效`);
          // 提交成功后刷新当前值（后端确认的生效状态可见）
          loadCurrentValues(selectedPlugin);
        }
      } catch (e) {
        addToast('error', e instanceof ApiError ? e.message : '配置提交失败');
      } finally {
        setSubmitting(false);
      }
    },
    [selectedPlugin, submitUrl, addToast, loadCurrentValues],
  );

  // ─── 渲染 ───
  if (loading) {
    return (
      <div className="flex h-full items-center justify-center" data-testid="plugin-panel-loading">
        <p className="text-sm text-[var(--text-muted)]">加载插件清单…</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3" data-testid="plugin-panel-error">
        <p className="text-sm text-[var(--mascot-error)]">插件清单加载失败：{error}</p>
        <button
          type="button"
          className="rounded-md border border-[var(--border-subtle)] px-3 py-1 text-sm text-[var(--text-secondary)] hover:bg-[var(--bg-hover)]"
          onClick={() => window.location.reload()}
        >
          重试
        </button>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0" data-testid="plugin-panel">
      {/* 左：插件列表（name + description，自解释；顶部刷新按钮） */}
      <aside className="w-64 shrink-0 overflow-auto border-r border-[var(--border-subtle)] p-3" data-plugin-list>
        <div className="mb-2 flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-[var(--text-primary)]">插件</h2>
          <button
            type="button"
            data-testid="plugin-refresh"
            aria-label="刷新插件清单"
            onClick={handleRefresh}
            disabled={refreshing}
            className="rounded-md border border-[var(--border-subtle)] px-2 py-1 text-xs text-[var(--text-secondary)] transition-colors hover:bg-[var(--bg-hover)] disabled:cursor-not-allowed disabled:opacity-60"
          >
            {refreshing ? '刷新中…' : '刷新'}
          </button>
        </div>
        {plugins.length === 0 && (
          <p className="text-xs text-[var(--text-muted)]">（暂无插件）</p>
        )}
        {plugins.map((p) => (
          <div
            key={p.name}
            className={`mb-1.5 flex items-stretch overflow-hidden rounded-md border transition-colors ${
              selected === p.name
                ? 'border-[var(--accent-primary)] bg-[var(--accent-primary)]/10'
                : 'border-[var(--border-subtle)] hover:bg-[var(--bg-hover)]'
            }`}
          >
            <button
              type="button"
              data-plugin-item={p.name}
              aria-label={p.name}
              onClick={() => selectPlugin(p.name)}
              className="min-w-0 flex-1 p-2.5 text-left"
            >
              <span className="flex items-center gap-1.5">
                <span className="text-sm font-medium text-[var(--text-primary)]">{p.name}</span>
                <span className="rounded bg-[var(--bg-hover)] px-1 text-[10px] text-[var(--text-muted)]">
                  v{p.version}
                </span>
                {hasConfigSchema(p.schema) && (
                  <span className="ml-auto rounded bg-[var(--accent-primary)]/15 px-1 text-[10px] text-[var(--accent-primary)]">
                    表单
                  </span>
                )}
              </span>
              <span className="mt-1 block text-xs leading-relaxed text-[var(--text-muted)]">
                {p.description || '（无描述）'}
              </span>
            </button>
            {p.clientSlot && (
              <button
                type="button"
                data-testid={`load-ui-${p.name}`}
                title="动态加载插件 UI 到插槽"
                disabled={loadingUi === p.name}
                onClick={() => handleLoadUi(p)}
                className="shrink-0 border-l border-[var(--border-subtle)] px-2 text-xs text-[var(--accent-primary)] transition-colors hover:bg-[var(--bg-hover)] disabled:cursor-not-allowed disabled:opacity-60"
              >
                {loadingUi === p.name ? '加载中…' : '加载 UI'}
              </button>
            )}
          </div>
        ))}
      </aside>

      {/* 右：详情 + SchemaRenderer */}
      <section className="min-w-0 flex-1 overflow-auto p-4" data-plugin-detail>
        {!selectedPlugin ? (
          <p className="text-sm text-[var(--text-muted)]">选择左侧插件查看详情与配置。</p>
        ) : hasConfigSchema(selectedPlugin.schema) ? (
          <div className="flex flex-col gap-3">
            <div className="flex items-center gap-2">
              <h2 className="text-base font-semibold text-[var(--text-primary)]">{selectedPlugin.name}</h2>
              <span className="rounded bg-[var(--bg-hover)] px-1.5 py-0.5 text-xs text-[var(--text-muted)]">
                v{selectedPlugin.version}
              </span>
              {selectedPlugin.submitUrl && (
                <span className="rounded bg-[var(--bg-hover)] px-1.5 py-0.5 text-xs text-[var(--text-muted)]">
                  提交端点：{selectedPlugin.submitUrl}
                </span>
              )}
            </div>
            {loadingValues && (
              <p className="text-xs text-[var(--text-muted)]" data-testid="plugin-values-loading">
                正在读取当前配置…
              </p>
            )}
            <SchemaRenderer
              schema={selectedPlugin.schema}
              value={values}
              onChange={handleValueChange}
              onSubmit={submitUrl ? handleSubmit : undefined}
            />
            {!submitUrl && (
              <p className="text-xs text-[var(--text-muted)]" data-testid="plugin-no-submit">
                该插件暂不支持在线修改（未声明提交端点）。
              </p>
            )}
            {submitting && (
              <p className="text-xs text-[var(--text-muted)]" data-testid="plugin-submitting">
                提交中…
              </p>
            )}
          </div>
        ) : (
          <div className="flex flex-col gap-3" data-testid="plugin-no-schema">
            <div className="flex items-center gap-2">
              <h2 className="text-base font-semibold text-[var(--text-primary)]">{selectedPlugin.name}</h2>
              <span className="rounded bg-[var(--bg-hover)] px-1.5 py-0.5 text-xs text-[var(--text-muted)]">
                v{selectedPlugin.version}
              </span>
            </div>
            <p className="text-sm text-[var(--text-muted)]">该插件暂无可配置界面。</p>
            <div>
              <p className="mb-1.5 text-xs font-medium text-[var(--text-secondary)]">暴露的路由：</p>
              <ul className="flex flex-wrap gap-1.5">
                {(selectedPlugin.routes ?? []).map((r) => (
                  <li
                    key={r}
                    className="rounded border border-[var(--border-subtle)] bg-[var(--bg-hover)] px-2 py-0.5 font-mono text-[11px] text-[var(--text-secondary)]"
                  >
                    {r}
                  </li>
                ))}
                {(selectedPlugin.routes ?? []).length === 0 && (
                  <li className="text-xs text-[var(--text-muted)]">（无路由声明）</li>
                )}
              </ul>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}

export default PluginPanel;
