/**
 * ApiTokenField —— API 令牌输入（遗留修复：T4.2 结案报告 FLASK_API_TOKEN 401）
 *
 * 用途：.env 启用 FLASK_API_TOKEN 时，/api/plugins/reload 与 schema 提交端点
 * 需要 `Authorization: Bearer <token>`。用户在此输入令牌后经 localStorage
 * 持久化，apiClient.request() 自动注入（见 lib/apiToken.ts），无需重复输入。
 *
 * 交互：
 * - 折叠开关「API 令牌」；已保存令牌时显示 ✓；
 * - 展开后：password 输入框 + 保存 / 清除；
 * - 提示文案说明何时需要令牌（刷新插件清单 / 提交配置返回 401 时）。
 */
import React, { useEffect, useState } from 'react';
import { clearApiToken, getApiToken, setApiToken, subscribeApiToken } from '../lib/apiToken';

export function ApiTokenField() {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState('');
  const [saved, setSaved] = useState(false);
  const [savedToken, setSavedToken] = useState(false);

  useEffect(() => {
    const t = getApiToken();
    setValue(t);
    setSavedToken(Boolean(t));
    return subscribeApiToken((next) => {
      setValue(next);
      setSavedToken(Boolean(next));
    });
  }, []);

  const handleSave = () => {
    setApiToken(value);
    setSaved(true);
    window.setTimeout(() => setSaved(false), 1500);
  };

  const handleClear = () => {
    clearApiToken();
    setValue('');
  };

  return (
    <div className="api-token-field" data-testid="api-token-field">
      <button
        type="button"
        data-testid="api-token-toggle"
        className="flex w-full items-center justify-between rounded-md border border-[var(--border-subtle)] px-2 py-1.5 text-xs text-[var(--text-secondary)] transition-colors hover:bg-[var(--bg-hover)]"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span>API 令牌{savedToken ? ' ✓' : ''}</span>
        <span>{open ? '▾' : '▸'}</span>
      </button>

      {open && (
        <div className="mt-1.5 rounded-md border border-[var(--border-subtle)] p-2" data-testid="api-token-body">
          <input
            type="password"
            data-testid="api-token-input"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="输入 FLASK_API_TOKEN"
            className="w-full rounded-md border border-[var(--border-subtle)] bg-[var(--bg-input, transparent)] px-2 py-1 text-xs text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
          />
          <div className="mt-1.5 flex items-center gap-2">
            <button
              type="button"
              data-testid="api-token-save"
              onClick={handleSave}
              className="rounded-md bg-[var(--accent-primary)] px-2 py-1 text-xs text-white transition-opacity hover:opacity-90"
            >
              {saved ? '已保存 ✓' : '保存'}
            </button>
            <button
              type="button"
              data-testid="api-token-clear"
              onClick={handleClear}
              disabled={!getApiToken()}
              className="rounded-md border border-[var(--border-subtle)] px-2 py-1 text-xs text-[var(--text-secondary)] transition-colors hover:bg-[var(--bg-hover)] disabled:cursor-not-allowed disabled:opacity-50"
            >
              清除
            </button>
          </div>
          <p className="mt-1.5 text-[11px] leading-relaxed text-[var(--text-muted)]">
            启用 FLASK_API_TOKEN 时，刷新插件清单 / 提交配置需携带令牌；令牌仅保存在本机浏览器。
          </p>
        </div>
      )}
    </div>
  );
}

export default ApiTokenField;
