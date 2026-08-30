/**
 * PluginPanel —— 插件中心（任务 T3.3，协议见 PLAN-3 §4/§5）
 *
 * 行为：
 * 1. 挂载时 GET /api/plugins 拉取全部插件 manifest；
 * 2. 左侧列表：插件名 + 版本 + 描述（自解释）；点击右侧渲染详情；
 * 3. 详情：schema 非空 → <SchemaRenderer/> 自动生成表单；schema 为空 →
 *    「该插件暂无可配置界面」+ 其 routes 列表；
 * 4. 值预填：选中带 submitUrl 的插件时，先 GET submitUrl 读取当前生效值
 *    （仅取 schema 已声明的字段），失败静默回退 schema default —— 让
 *    「查看 → 修改 → 提交 → 生效」闭环的第一步真实可见；
 * 5. 提交：解析提交端点（插件声明 submit_url，兜底前端映射表）→ POST 表单值；
 *    成功/失败经全局 chat store 的 Toast 提示；成功后刷新当前值（生效可见）。
 *
 * 提交端点解析（设计契约）：
 * - 插件声明 submit_url 优先（后端 Plugin 新增可选字段，写入 manifest）；
 * - 未声明 → SUBMIT_URL_FALLBACK 按插件名兜底（仅放「提交体与端点契约一致」的
 *   真实端点，不硬凑）；仍为空 → 不渲染提交按钮并提示「该插件暂不支持在线修改」。
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, request } from '../lib/apiClient';
import { useChatStore } from '../store/useChatStore';
import { SchemaRenderer } from './schema/SchemaRenderer';

/** /api/plugins manifest 中单个插件的条目（wire 形状，submit_url 为后端字段） */
export interface PluginManifestEntry {
  name: string;
  version: string;
  description: string;
  schema: Record<string, any>;
  submit_url?: string;
  routes: string[];
}

export interface PluginsManifest {
  plugins: PluginManifestEntry[];
  host?: { python?: string; flask?: string };
}

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
export function resolveSubmitUrl(plugin: PluginManifestEntry): string {
  if (plugin.submit_url && plugin.submit_url.trim()) return plugin.submit_url;
  return SUBMIT_URL_FALLBACK[plugin.name] ?? '';
}

/** 是否含可配置 schema（顶层 object + 至少一个属性） */
function hasConfigSchema(schema: Record<string, any> | undefined): boolean {
  return !!schema && typeof schema === 'object' && schema.type === 'object' && !!schema.properties;
}

/** 从后端当前值里挑选 schema 已声明字段（防止多余字段污染表单值） */
function pickSchemaFields(
  schema: Record<string, any> | undefined,
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
  const [plugins, setPlugins] = useState<PluginManifestEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [values, setValues] = useState<Record<string, any>>({});
  const [loadingValues, setLoadingValues] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const addToast = useChatStore((s) => s.addToast);
  /** 当前选中插件名（同步读写，用于异步值加载的竞态守卫） */
  const selectedRef = useRef<string | null>(null);
  /** 用户已修改过的字段（预填合并时不被覆盖；避免「先编辑、后预填到达」丢失输入） */
  const touchedRef = useRef<Set<string>>(new Set());

  // ─── 挂载：拉取插件清单（首个插件的选中与值预填交给下方自动选中 effect） ───
  useEffect(() => {
    const ac = new AbortController();
    request<PluginsManifest>('/api/plugins', { signal: ac.signal })
      .then((m) => {
        const list = Array.isArray(m?.plugins) ? m.plugins : [];
        setPlugins(list);
      })
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
  const loadCurrentValues = useCallback((plugin: PluginManifestEntry) => {
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
      {/* 左：插件列表（name + description，自解释） */}
      <aside className="w-64 shrink-0 overflow-auto border-r border-[var(--border-subtle)] p-3" data-plugin-list>
        {plugins.length === 0 && (
          <p className="text-xs text-[var(--text-muted)]">（暂无插件）</p>
        )}
        {plugins.map((p) => (
          <button
            key={p.name}
            type="button"
            data-plugin-item={p.name}
            aria-label={p.name}
            onClick={() => selectPlugin(p.name)}
            className={`mb-1.5 w-full rounded-md border p-2.5 text-left transition-colors ${
              selected === p.name
                ? 'border-[var(--accent-primary)] bg-[var(--accent-primary)]/10'
                : 'border-[var(--border-subtle)] hover:bg-[var(--bg-hover)]'
            }`}
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
              {selectedPlugin.submit_url && (
                <span className="rounded bg-[var(--bg-hover)] px-1.5 py-0.5 text-xs text-[var(--text-muted)]">
                  提交端点：{selectedPlugin.submit_url}
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
