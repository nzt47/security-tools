// ════════════════════════════════════════════════════════════
// 网络配置管理
// ════════════════════════════════════════════════════════════

let __networkConfigCache = null;
let __networkConfigDirty = false;

// LLM 实例数据
let __llmInstances = [];
// MCP 服务数据
let __mcpServices = [];

/**
 * 加载网络配置
 */
async function loadNetworkConfig() {
  console.log('[网络配置] loadNetworkConfig 被调用');
  try {
    console.log('[网络配置] 正在请求 /api/network-config...');
    const res = await apiFetch('/api/network-config');
    const config = await res.json();
    console.log('[网络配置] 配置加载成功:', config);
    __networkConfigCache = config;
    renderNetworkConfig(config);
    
    // 加载 LLM 实例
    await loadLlmInstances();
    // 加载 MCP 服务
    await loadMcpServices();
    // 加载搜索引擎实例
    await loadSearchInstances();

    // 将自定义引擎也加入优先级列表
    if (__searchInstances && __searchInstances.length > 0) {
      refreshPriorityWithInstances();
    }

    console.log('[网络配置] 配置渲染完成');
  } catch (e) {
    console.error('[网络配置] 加载失败:', e);
    showNetworkStatus('加载失败: ' + e.message, 'err');
  }
}

/**
 * 加载 LLM 实例
 */
async function loadLlmInstances() {
  try {
    const res = await apiFetch('/api/llm/instances');
    const result = await res.json();
    if (result.ok) {
      __llmInstances = result.instances || [];
      renderLlmInstances(__llmInstances);
    }
  } catch (e) {
    console.error('[网络配置] 加载 LLM 实例失败:', e);
  }
}

/**
 * 加载 MCP 服务
 */
async function loadMcpServices() {
  try {
    const res = await apiFetch('/api/mcp/services');
    const result = await res.json();
    if (result.ok) {
      __mcpServices = result.services || [];
      renderMcpServices(__mcpServices);
    }
  } catch (e) {
    console.error('[网络配置] 加载 MCP 服务失败:', e);
  }
}

/**
 * 渲染网络配置到表单
 */
function renderNetworkConfig(config) {
  // LLM 服务（仅保留启用开关）
  set('nc-llm-enabled', config.llm.enabled);

  // 网络基础设置
  set('nc-network-timeout', config.network.timeout);
  set('nc-network-retries', config.network.max_retries);
  set('nc-network-backoff', config.network.backoff_factor);
  set('nc-proxy-enabled', config.network.proxy_enabled);
  set('nc-proxy-url', config.network.proxy_url || '');
  toggleProxyUrlRow();

  // 搜索服务
  set('nc-search-enabled', config.search.enabled);
  set('nc-search-max', config.search.max_results);
  set('nc-search-timeout', config.search.timeout || 30);

  // 渲染搜索引擎优先级（实例数据稍后在 loadSearchInstances 中补充）
  renderSearchEnginePriority(config.search.engine_priority || [], config.search_api_keys, __searchInstances);

  // Web 抓取服务
  set('nc-scraping-enabled', config.web_scraping.enabled);
  set('nc-scraping-robots', config.web_scraping.respect_robots_txt);
  set('nc-scraping-delay', config.web_scraping.delay_between_requests);

  // 浏览器自动化
  set('nc-browser-enabled', config.browser.enabled);
  set('nc-browser-headless', config.browser.headless);
  set('nc-browser-timeout', config.browser.timeout);

  // 数据同步
  set('nc-sync-enabled', config.sync.enabled);
  set('nc-sync-interval', config.sync.interval_minutes);
  set('nc-sync-autostart', config.sync.auto_sync_on_start);

  // 外部服务
  set('nc-error-enabled', config.external_services.error_reporting.enabled);
  set('nc-error-webhook', config.external_services.error_reporting.webhook_url || '');
  set('nc-monitoring-enabled', config.external_services.monitoring.enabled);
  set('nc-monitoring-endpoint', config.external_services.monitoring.endpoint || '');

  __networkConfigDirty = false;
}

/**
 * 渲染搜索引擎优先级列表
 */
function renderSearchEnginePriority(priority, apiKeys, searchInstances) {
  const list = document.getElementById('search-engine-priority-list');
  if (!list) return;

  apiKeys = apiKeys || {};
  searchInstances = searchInstances || __searchInstances || [];
  // 建立实例 ID/名称/类型 -> 实例数据 映射（兼容新旧数据格式）
  const instanceMap = {};
  searchInstances.forEach(inst => {
    const eid = inst.id || inst.name;
    if (eid) instanceMap[eid] = inst;
    if (inst.name && inst.name !== eid) instanceMap[inst.name] = inst;
    if (inst.engine_type && inst.engine_type !== inst.name) instanceMap[inst.engine_type] = inst;
  });

  list.innerHTML = '';

  priority.forEach(engine => {
    const inst = instanceMap[engine];
    if (!inst) return;

    const label = `⚡ ${inst.name || engine}`;
    const item = document.createElement('div');
    item.className = 'priority-item' + (inst && !inst.enabled ? ' disabled' : '');
    item.dataset.engine = engine;

    let html = `
      <span class="priority-handle">⋮⋮</span>
      <span class="priority-label">${label}</span>
      <label class="toggle-switch small">
        <input type="checkbox" id="nc-engine-${engine}" ${inst ? (inst.enabled !== false ? 'checked' : '') : 'checked'} onchange="onEngineToggle('${engine}', this.checked)">
        <span class="toggle-slider"></span>
      </label>`;

    const et = inst.engine_type || '';
    const isDefault = inst.is_default || (engine === __networkConfigCache?.search?.default_engine);

    // 操作按钮
    html += `<span style="display:inline-flex;align-items:center;gap:2px;margin-left:auto">`;
    if (isDefault) html += `<span class="default-badge" style="font-size:10px;padding:1px 6px">默认</span>`;
    html += `<span style="font-size:11px;color:#8b949e;margin-right:4px">${et}</span>`;
    html += `<button class="btn-xs" onclick="testSearchInstance('${engine}')" title="测试">▶</button>`;
    html += `<button class="btn-xs" onclick="editSearchInstance('${engine}')" title="编辑">✏️</button>`;
    html += `<button class="btn-xs danger" onclick="deleteSearchInstance('${engine}', '${escapeHtml(inst.name || '')}')" title="删除">🗑</button>`;
    html += `</span>`;
    // 下一行显示端点/类型信息
    html += `<div style="flex-basis:100%;font-size:11px;color:#8b949e;margin-top:2px;padding-left:24px">📍 ${escapeHtml(inst.api_endpoint || et)} · ⏱ ${inst.timeout || 30}s</div>`;

    item.innerHTML = html;
    list.appendChild(item);
  });

  initPriorityDragAndDrop();
}

/**
 * 初始化优先级拖拽功能
 */
function initPriorityDragAndDrop() {
  const list = document.getElementById('search-engine-priority-list');
  if (!list) return;

  const items = list.querySelectorAll('.priority-item');
  
  items.forEach(item => {
    const handle = item.querySelector('.priority-handle');
    if (handle) {
      handle.style.cursor = 'grab';
      handle.addEventListener('mousedown', (e) => {
        startDrag(e, item);
      });
    }
  });
}

let draggedItem = null;
function startDrag(e, item) {
  draggedItem = item;
  item.style.opacity = '0.5';
  item.style.position = 'relative';
  item.style.zIndex = '1000';
  
  document.addEventListener('mousemove', onDrag);
  document.addEventListener('mouseup', stopDrag);
}

function onDrag(e) {
  if (!draggedItem) return;
  
  const list = document.getElementById('search-engine-priority-list');
  if (!list) return;

  const items = list.querySelectorAll('.priority-item');
  let targetIndex = -1;
  
  items.forEach((item, index) => {
    const rect = item.getBoundingClientRect();
    const midY = rect.top + rect.height / 2;
    if (e.clientY >= rect.top && e.clientY <= rect.bottom) {
      if (e.clientY < midY) {
        targetIndex = index;
      } else {
        targetIndex = index + 1;
      }
    }
  });

  if (targetIndex >= 0 && targetIndex <= items.length) {
    const currentIndex = Array.from(items).indexOf(draggedItem);
    if (targetIndex !== currentIndex) {
      list.removeChild(draggedItem);
      const referenceNode = items[targetIndex] || null;
      list.insertBefore(draggedItem, referenceNode);
    }
  }
  
  __networkConfigDirty = true;
}

function stopDrag() {
  if (draggedItem) {
    draggedItem.style.opacity = '1';
    draggedItem.style.position = '';
    draggedItem.style.zIndex = '';
    draggedItem = null;
  }
  document.removeEventListener('mousemove', onDrag);
  document.removeEventListener('mouseup', stopDrag);
  __networkConfigDirty = true;
}

/**
 * 收集表单数据
 */
function collectNetworkConfig() {
  const priorityList = document.getElementById('search-engine-priority-list');
  const enginePriority = [];
  const engineEnabled = {};
  
  if (priorityList) {
    priorityList.querySelectorAll('.priority-item').forEach(item => {
      const engine = item.dataset.engine;
      enginePriority.push(engine);
      
      const checkbox = item.querySelector('input[type="checkbox"]');
      engineEnabled[engine] = checkbox ? checkbox.checked : true;
    });
  }

  // 获取 LLM 配置（从缓存中获取，因为已删除默认配置表单）
  const llmConfig = __networkConfigCache?.llm || {
    enabled: true,
    provider: '',
    api_key: '',
    model: '',
    api_endpoint: '',
    timeout: 30,
    max_retries: 3,
  };

  const config = {
    llm: {
      enabled: get('nc-llm-enabled'),
      provider: llmConfig.provider,
      api_key: llmConfig.api_key,
      model: llmConfig.model,
      api_endpoint: llmConfig.api_endpoint,
      timeout: llmConfig.timeout,
      max_retries: llmConfig.max_retries,
    },
    network: {
      timeout: num('nc-network-timeout'),
      max_retries: num('nc-network-retries'),
      backoff_factor: num('nc-network-backoff'),
      proxy_enabled: get('nc-proxy-enabled'),
      proxy_url: get('nc-proxy-url'),
    },
    search: {
      enabled: get('nc-search-enabled'),
      default_engine: enginePriority.length > 0 ? enginePriority[0] : '',
      max_results: num('nc-search-max'),
      timeout: num('nc-search-timeout'),
      engine_priority: enginePriority,
      engine_enabled: engineEnabled,
    },
    search_instances: __searchInstances,
    web_scraping: {
      enabled: get('nc-scraping-enabled'),
      respect_robots_txt: get('nc-scraping-robots'),
      delay_between_requests: num('nc-scraping-delay'),
    },
    browser: {
      enabled: get('nc-browser-enabled'),
      headless: get('nc-browser-headless'),
      timeout: num('nc-browser-timeout'),
    },
    sync: {
      enabled: get('nc-sync-enabled'),
      interval_minutes: num('nc-sync-interval'),
      auto_sync_on_start: get('nc-sync-autostart'),
    },
    external_services: {
      error_reporting: {
        enabled: get('nc-error-enabled'),
        webhook_url: get('nc-error-webhook'),
      },
      monitoring: {
        enabled: get('nc-monitoring-enabled'),
        endpoint: get('nc-monitoring-endpoint'),
      },
    },
  };
  return config;
}

/**
 * 配置变更时标记脏数据
 */
function onNetworkConfigChange() {
  __networkConfigDirty = true;
  toggleProxyUrlRow();
}

/**
 * 搜索引擎启用/禁用切换
 */
function onEngineToggle(engine, enabled) {
  __networkConfigDirty = true;
  console.log(`[网络配置] 搜索引擎 ${engine} ${enabled ? '已启用' : '已禁用'}`);
  // 如果是自定义引擎实例，同时更新其后端状态
  if (__searchInstances && __searchInstances.some(i => (i.id || i.name) === engine)) {
    toggleSearchInstance(engine, enabled);
  }
}

/**
 * 更新单个配置项（切换开关）
 */
function updateNetworkConfig(path, value) {
  const parts = path.split('.');
  const config = collectNetworkConfig();
  let current = config;
  for (let i = 0; i < parts.length - 1; i++) {
    current = current[parts[i]];
  }
  current[parts[parts.length - 1]] = value;
  __networkConfigDirty = true;
}

/**
 * 切换代理 URL 显示
 */
function toggleProxyUrlRow() {
  const enabled = get('nc-proxy-enabled');
  const row = document.getElementById('nc-proxy-url-row');
  if (row) {
    row.style.display = enabled ? 'block' : 'none';
  }
}

/**
 * 保存网络配置
 */
async function saveNetworkConfig() {
  console.log('[网络配置] saveNetworkConfig 被调用');
  const config = collectNetworkConfig();
  console.log('[网络配置] 收集到的配置:', JSON.stringify(config, null, 2).substring(0, 500));

  const validationErrors = validateNetworkConfig(config);
  if (validationErrors.length > 0) {
    console.warn('[网络配置] 验证失败:', validationErrors);
    showNetworkStatus('验证失败: ' + validationErrors.join('; '), 'err');
    return;
  }

  try {
    console.log('[网络配置] 正在发送保存请求...');
    showNetworkStatus('保存中...', 'info');
    const res = await apiFetch('/api/network-config', {
      method: 'POST',
      body: JSON.stringify(config),
    });
    const result = await res.json();
    console.log('[网络配置] 保存响应:', result);

    if (result.ok) {
      __networkConfigCache = result.config;
      __networkConfigDirty = false;
      showNetworkStatus('✓ 配置已保存', 'ok');
    } else {
      showNetworkStatus('✗ 保存失败: ' + (result.error || '未知错误'), 'err');
    }
  } catch (e) {
    console.error('[网络配置] 保存异常:', e);
    showNetworkStatus('✗ 保存失败: ' + e.message, 'err');
  }
}

/**
 * 带超时的 API 请求
 */
async function apiFetchWithTimeout(path, opt = {}, timeoutMs = 30000) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  const startTime = Date.now();

  console.log(`[网络配置] [请求] ${opt.method || 'GET'} ${path} (超时: ${timeoutMs}ms)`);

  try {
    const res = await apiFetch(path, {
      ...opt,
      signal: controller.signal,
    });
    const duration = Date.now() - startTime;
    console.log(`[网络配置] [响应] ${path} 状态: ${res.status} 耗时: ${duration}ms`);
    return res;
  } catch (e) {
    const duration = Date.now() - startTime;
    console.error(`[网络配置] [错误] ${path} 失败 (耗时: ${duration}ms)`, e);

    if (e.name === 'AbortError') {
      console.error(`[网络配置] [超时] ${path} 请求超时，超过 ${timeoutMs}ms 限制`);
      throw new Error(`请求超时（${Math.round(timeoutMs / 1000)}秒）`);
    }

    const errorDetails = {
      name: e.name,
      message: e.message,
      type: e.type,
      stack: e.stack ? e.stack.substring(0, 300) : undefined,
    };
    console.error(`[网络配置] [详细错误]`, errorDetails);
    throw new Error(`${e.name}: ${e.message}`);
  } finally {
    clearTimeout(timeoutId);
  }
}

/**
 * 应用配置并即时生效
 */
async function applyNetworkConfig() {
  console.log('[网络配置] ====== 开始应用配置 ======');
  console.log('[网络配置] applyNetworkConfig 被调用');

  const config = collectNetworkConfig();
  console.log('[网络配置] 收集到的配置:', JSON.stringify(config, (k, v) => k.includes('api_key') || k === 'webhook_url' ? '***' : v, 2));

  const validationErrors = validateNetworkConfig(config);
  if (validationErrors.length > 0) {
    console.warn('[网络配置] 验证失败:', validationErrors);
    showNetworkStatus('验证失败: ' + validationErrors.join('; '), 'err');
    return;
  }

  try {
    showNetworkStatus('保存并应用中...', 'info');

    const res = await apiFetchWithTimeout('/api/network-config', {
      method: 'POST',
      body: JSON.stringify(config),
    }, 30000);

    const result = await res.json();

    console.log('[网络配置] [步骤1] 响应状态码:', res.status);
    console.log('[网络配置] [步骤1] 完整响应体:', JSON.stringify(result, null, 2));

    if (!result.ok) {
      console.error('[网络配置] [步骤1] 保存失败:', result.error);
      showNetworkStatus('✗ 保存失败: ' + (result.error || '未知错误'), 'err');
      return;
    }

    console.log('[网络配置] [步骤1] ✓ 保存成功');

    const applyRes = await apiFetchWithTimeout('/api/apply-network-config', {
      method: 'POST',
      body: JSON.stringify({}),
    }, 15000);

    const applyResult = await applyRes.json();
    console.log('[网络配置] [步骤2] 应用响应:', applyResult);

    if (applyResult.ok) {
      console.log('[网络配置] [步骤2] ✓ 配置已即时生效');
    } else {
      console.warn('[网络配置] [步骤2] 配置应用警告:', applyResult.error);
    }

    const isMaskedApiKey = config.llm.api_key.startsWith('***');
    const shouldConfigureLlm = config.llm.enabled && config.llm.provider && config.llm.api_key && !isMaskedApiKey;

    console.log('[网络配置] [判断] isMaskedApiKey=', isMaskedApiKey, 'shouldConfigureLlm=', shouldConfigureLlm);

    if (shouldConfigureLlm) {
      showNetworkStatus('正在配置 LLM...', 'info');

      const llmPayload = {
        provider: config.llm.provider,
        api_key: config.llm.api_key,
        model: config.llm.model || undefined,
      };

      console.log('[网络配置] [步骤3] 正在调用 /api/config 配置 LLM...');
      console.log('[网络配置] [步骤3] 请求体:', JSON.stringify(llmPayload, (k, v) => k === 'api_key' ? '***' : v, 2));

      const llmRes = await apiFetchWithTimeout('/api/config', {
        method: 'POST',
        body: JSON.stringify(llmPayload),
      }, 60000);

      const rawText = await llmRes.text();
      console.log('[网络配置] [步骤3] 响应状态码:', llmRes.status);
      console.log('[网络配置] [步骤3] 原始响应文本:', rawText);

      let llmResult;
      try {
        llmResult = JSON.parse(rawText);
        console.log('[网络配置] [步骤3] ✓ LLM 配置成功!');
      } catch (parseErr) {
        console.error('[网络配置] [步骤3] JSON 解析失败:', parseErr);
        llmResult = { ok: false, error: '响应非 JSON: ' + rawText.substring(0, 200) };
      }

      if (llmResult.ok) {
        console.log('[网络配置] [步骤3] ✓ LLM 已连接并生效');
        __networkConfigCache = result.config;
        __networkConfigDirty = false;
        showNetworkStatus('✓ 配置已保存，LLM 已连接并生效', 'ok');
      } else {
        console.error('[网络配置] [步骤3] ✗ LLM 配置失败:', llmResult);
        showNetworkStatus('⚠ 配置已保存，但 LLM 连接失败: ' + (llmResult.error || '未知错误'), 'err');
      }
    } else if (isMaskedApiKey) {
      console.log('[网络配置] [步骤3] 跳过 LLM 配置（API Key 是脱敏值，请重新输入完整 API Key）');
      __networkConfigCache = result.config;
      __networkConfigDirty = false;
      showNetworkStatus('✓ 配置已保存（如需配置 LLM，请重新输入完整 API Key）', 'info');
    } else {
      console.log('[网络配置] [步骤3] 跳过 LLM 配置');
      __networkConfigCache = result.config;
      __networkConfigDirty = false;
      showNetworkStatus('✓ 配置已保存并即时生效', 'ok');
    }

    console.log('[网络配置] ====== 配置应用完成 ======');
  } catch (e) {
    console.error('[网络配置] ====== applyNetworkConfig 异常 ======');
    console.error('[网络配置] 异常类型:', e.name);
    console.error('[网络配置] 异常消息:', e.message);
    console.error('[网络配置] 异常堆栈:', e.stack ? e.stack.substring(0, 500) : 'N/A');
    showNetworkStatus('✗ 应用失败: ' + e.message, 'err');
  }
}

/**
 * 重置配置为默认值
 */
async function resetNetworkConfig() {
  if (!confirm('确定要重置所有网络配置为默认值吗？此操作不可撤销。')) {
    return;
  }

  try {
    showNetworkStatus('重置中...', 'info');
    const res = await apiFetch('/api/network-config/reset', { method: 'POST' });
    const result = await res.json();

    if (result.ok) {
      renderNetworkConfig(result.config);
      showNetworkStatus('✓ 已重置为默认配置', 'ok');
    } else {
      showNetworkStatus('✗ 重置失败: ' + (result.error || '未知错误'), 'err');
    }
  } catch (e) {
    showNetworkStatus('✗ 重置失败: ' + e.message, 'err');
  }
}

/**
 * 导出网络配置
 */
async function exportNetworkConfig() {
  try {
    const res = await apiFetch('/api/network-config/export');
    const result = await res.json();

    if (result.ok) {
      const blob = new Blob([result.config_json], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `yunshu-network-config-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
      showNetworkStatus('✓ 配置已导出', 'ok');
    } else {
      showNetworkStatus('✗ 导出失败: ' + (result.error || '未知错误'), 'err');
    }
  } catch (e) {
    showNetworkStatus('✗ 导出失败: ' + e.message, 'err');
  }
}

/**
 * 导入网络配置
 */
async function importNetworkConfig(event) {
  const file = event.target.files[0];
  if (!file) return;

  try {
    const text = await file.text();
    showNetworkStatus('导入中...', 'info');

    const res = await apiFetch('/api/network-config/import', {
      method: 'POST',
      body: JSON.stringify({ config_json: text }),
    });
    const result = await res.json();

    if (result.ok) {
      renderNetworkConfig(result.config);
      await loadLlmInstances();
      await loadMcpServices();
      showNetworkStatus('✓ 配置已导入', 'ok');
    } else {
      showNetworkStatus('✗ 导入失败: ' + (result.error || '未知错误'), 'err');
    }
  } catch (e) {
    showNetworkStatus('✗ 导入失败: ' + e.message, 'err');
  } finally {
    event.target.value = '';
  }
}

/**
 * 输入验证
 */
function validateNetworkConfig(config) {
  const errors = [];

  if (config.llm.timeout < 1 || config.llm.timeout > 300) {
    errors.push('LLM 超时应在 1-300 秒之间');
  }

  if (config.network.timeout < 1 || config.network.timeout > 300) {
    errors.push('网络超时应在 1-300 秒之间');
  }

  if (config.network.proxy_enabled && config.network.proxy_url && !config.network.proxy_url.startsWith('***')) {
    try {
      new URL(config.network.proxy_url);
    } catch {
      errors.push('代理 URL 格式无效');
    }
  }

  if (config.external_services.error_reporting.enabled && config.external_services.error_reporting.webhook_url && !config.external_services.error_reporting.webhook_url.startsWith('***')) {
    try {
      new URL(config.external_services.error_reporting.webhook_url);
    } catch {
      errors.push('Webhook URL 格式无效');
    }
  }

  const enabledEngines = Object.values(config.search.engine_enabled || {}).filter(Boolean);
  if (enabledEngines.length === 0) {
    errors.push('至少需要启用一个搜索引擎');
  }

  return errors;
}

/**
 * 显示状态信息
 */
function showNetworkStatus(msg, type) {
  const el = document.getElementById('nc-status');
  if (!el) return;

  el.textContent = msg;
  el.style.display = 'block';
  el.className = 'cfg-status ' + (type === 'ok' ? 'ok' : type === 'err' ? 'err' : 'info');

  if (type === 'info') {
    el.style.background = '#1f6feb20';
    el.style.color = '#58a6ff';
    el.style.border = '1px solid #1f6feb80';
  }

  if (type === 'ok') {
    setTimeout(() => {
      el.style.display = 'none';
    }, 3000);
  }
}

/**
 * 辅助函数：设置表单值
 */
function set(id, value) {
  const el = document.getElementById(id);
  if (!el) return;

  if (el.type === 'checkbox') {
    el.checked = Boolean(value);
  } else {
    el.value = value ?? '';
  }
}

/**
 * 辅助函数：获取表单值
 */
function get(id) {
  const el = document.getElementById(id);
  if (!el) return null;

  if (el.type === 'checkbox') {
    return el.checked;
  }
  return el.value;
}

/**
 * 辅助函数：获取数字值
 */
function num(id) {
  const val = get(id);
  return val !== null && val !== '' ? Number(val) : 0;
}

// ════════════════════════════════════════════════════════════
// LLM 实例管理
// ════════════════════════════════════════════════════════════

let __editingLlmInstanceId = null;

/**
 * 渲染 LLM 实例列表
 */
function renderLlmInstances(instances) {
  const container = document.getElementById('llm-instances-list');
  if (!container) {
    console.error('[网络配置] llm-instances-list 容器不存在');
    return;
  }

  if (instances.length === 0) {
    container.innerHTML = '<div class="empty-state">暂无 LLM 实例，点击上方按钮添加</div>';
    return;
  }

  // 获取当前默认实例 ID
  const defaultInstanceId = __networkConfigCache?.default_llm_instance || '';

  container.innerHTML = instances.map(instance => {
    // 确保 instance.id 存在
    const id = instance.id || instance.name;
    const isDefault = instance.is_default || (id === defaultInstanceId);
    
    return `
    <div class="llm-instance-card ${instance.enabled ? '' : 'disabled'}">
      <div class="llm-instance-header">
        <div style="flex:1">
          <div class="llm-instance-name">${escapeHtml(instance.name)}</div>
          <div class="llm-instance-meta">${escapeHtml(instance.provider)} · ${escapeHtml(instance.model)}</div>
        </div>
        <div class="llm-instance-actions">
          ${isDefault ? '<span class="default-badge">默认</span>' : ''}
          <label class="toggle-switch small" title="${instance.enabled ? '禁用' : '启用'}">
            <input type="checkbox" ${instance.enabled ? 'checked' : ''} onchange="toggleLlmInstance('${id}', this.checked)">
            <span class="toggle-slider"></span>
          </label>
          <button class="btn-xs" onclick="editLlmInstance('${id}')" title="编辑">✏️</button>
          <button class="btn-xs" onclick="testLlmInstance('${id}')" title="测试连接">▶</button>
          <button class="btn-xs danger" onclick="deleteLlmInstance('${id}', '${escapeHtml(instance.name)}')" title="删除">🗑</button>
        </div>
      </div>
      <div class="llm-instance-body">
        <div class="llm-instance-endpoint">📍 ${escapeHtml(instance.api_endpoint)}</div>
        <div class="llm-instance-stats">
          <span>⏱ ${instance.timeout}s</span>
          <span>🔄 ${instance.max_retries}次重试</span>
          <span>👥 ${instance.max_concurrent_requests}并发</span>
        </div>
        ${instance.description ? `<div class="llm-instance-desc">📝 ${escapeHtml(instance.description)}</div>` : ''}
      </div>
      <button class="llm-set-default" onclick="setDefaultLlmInstance('${id}')" ${isDefault ? 'disabled' : ''}>
        ${isDefault ? '✓ 已设为默认' : '设为默认'}
      </button>
    </div>
  `;
  }).join('');
  
  console.log('[网络配置] LLM 实例列表已渲染，共 ' + instances.length + ' 个实例');
}



/**
 * 编辑 LLM 实例
 */
function editLlmInstance(instanceId) {
  // 支持 UUID 和名称匹配
  const instance = __llmInstances.find(i => (i.id || i.name) === instanceId);
  if (instance) {
    showLlmInstanceModal(instance.id || instance.name);
  } else {
    console.error('[网络配置] 未找到 LLM 实例:', instanceId);
    alert('未找到该实例');
  }
}

/**
 * 显示添加/编辑 LLM 实例模态框
 */
function showLlmInstanceModal(instanceId = null) {
  const modal = document.getElementById('llm-instance-modal');
  const title = document.getElementById('llm-modal-title');
  const form = document.getElementById('llm-instance-form');

  if (instanceId) {
    title.textContent = '编辑 LLM 实例';
    __editingLlmInstanceId = instanceId;
    // 支持 UUID 和名称匹配
    const instance = __llmInstances.find(i => (i.id || i.name) === instanceId);
    if (instance) {
      set('llm-form-name', instance.name);
      set('llm-form-provider', instance.provider);
      set('llm-form-api-key', instance.api_key);
      set('llm-form-model', instance.model);
      set('llm-form-endpoint', instance.api_endpoint);
      set('llm-form-auth-method', instance.auth_method);
      set('llm-form-max-concurrent', instance.max_concurrent_requests);
      set('llm-form-timeout', instance.timeout);
      set('llm-form-retries', instance.max_retries);
      set('llm-form-description', instance.description);
      set('llm-form-enabled', instance.enabled);
    }
  } else {
    title.textContent = '添加 LLM 实例';
    __editingLlmInstanceId = null;
    form.reset();
    set('llm-form-timeout', 30);
    set('llm-form-retries', 3);
    set('llm-form-max-concurrent', 5);
    set('llm-form-auth-method', 'api_key');
    set('llm-form-enabled', true);
  }

  modal.style.display = 'block';
}

/**
 * 隐藏 LLM 实例模态框
 */
function hideLlmInstanceModal() {
  const modal = document.getElementById('llm-instance-modal');
  modal.style.display = 'none';
  __editingLlmInstanceId = null;
}

/**
 * 保存 LLM 实例
 */
async function saveLlmInstance() {
  const instance = {
    name: get('llm-form-name'),
    provider: get('llm-form-provider'),
    api_key: get('llm-form-api-key'),
    model: get('llm-form-model'),
    api_endpoint: get('llm-form-endpoint'),
    auth_method: get('llm-form-auth-method'),
    max_concurrent_requests: num('llm-form-max-concurrent'),
    timeout: num('llm-form-timeout'),
    max_retries: num('llm-form-retries'),
    description: get('llm-form-description'),
    enabled: get('llm-form-enabled'),
  };

  // 验证
  const errors = validateLlmInstance(instance);
  if (errors.length > 0) {
    alert('验证失败:\n' + errors.join('\n'));
    return;
  }

  try {
    let result;
    if (__editingLlmInstanceId) {
      result = await apiFetch(`/api/llm/instances/${__editingLlmInstanceId}`, {
        method: 'PUT',
        body: JSON.stringify({ updates: instance }),
      });
    } else {
      result = await apiFetch('/api/llm/instances', {
        method: 'POST',
        body: JSON.stringify({ instance }),
      });
    }

    const data = await result.json();
    if (data.ok) {
      await loadLlmInstances();
      hideLlmInstanceModal();
      showNetworkStatus(__editingLlmInstanceId ? '✓ LLM 实例已更新' : '✓ LLM 实例已添加', 'ok');
    } else {
      alert('操作失败: ' + (data.error || '未知错误'));
    }
  } catch (e) {
    alert('操作失败: ' + e.message);
  }
}

/**
 * 验证 LLM 实例配置
 */
function validateLlmInstance(instance) {
  const errors = [];

  if (!instance.name.trim()) {
    errors.push('服务名称不能为空');
  }

  if (!instance.provider) {
    errors.push('请选择提供商');
  }

  if (!instance.api_endpoint.trim()) {
    errors.push('API 端点 URL 不能为空');
  } else {
    try {
      new URL(instance.api_endpoint);
    } catch {
      errors.push('API 端点 URL 格式无效');
    }
  }

  if (!instance.model.trim()) {
    errors.push('模型名称不能为空');
  }

  if (instance.max_concurrent_requests < 1) {
    errors.push('最大并发请求数必须大于 0');
  }

  if (instance.timeout < 1 || instance.timeout > 300) {
    errors.push('超时时间必须在 1-300 秒之间');
  }

  if (instance.max_retries < 0 || instance.max_retries > 10) {
    errors.push('最大重试次数必须在 0-10 之间');
  }

  return errors;
}

/**
 * 删除 LLM 实例
 */
async function deleteLlmInstance(instanceId, name) {
  if (!confirm(`确定要删除 LLM 实例 "${name}" 吗？此操作不可撤销。`)) {
    return;
  }

  try {
    const result = await apiFetch(`/api/llm/instances/${instanceId}`, {
      method: 'DELETE',
    });
    const data = await result.json();
    if (data.ok) {
      await loadLlmInstances();
      showNetworkStatus('✓ LLM 实例已删除', 'ok');
    } else {
      alert('删除失败: ' + (data.error || '未知错误'));
    }
  } catch (e) {
    alert('删除失败: ' + e.message);
  }
}

/**
 * 切换 LLM 实例启用状态
 */
async function toggleLlmInstance(instanceId, enabled) {
  console.log('[网络配置] 切换 LLM 实例状态:', instanceId, enabled);
  try {
    const result = await apiFetch(`/api/llm/instances/${instanceId}`, {
      method: 'PUT',
      body: JSON.stringify({ updates: { enabled } }),
    });
    const data = await result.json();
    if (data.ok) {
      await loadLlmInstances();
      showNetworkStatus(`✓ LLM 实例已${enabled ? '启用' : '禁用'}`, 'ok');
    } else {
      console.error('[网络配置] 切换状态失败:', data.error);
      showNetworkStatus('✗ 切换失败: ' + (data.error || '未知错误'), 'err');
    }
  } catch (e) {
    console.error('[网络配置] 切换 LLM 实例状态失败:', e);
    showNetworkStatus('✗ 切换失败: ' + e.message, 'err');
  }
}

/**
 * 设置默认 LLM 实例
 */
async function setDefaultLlmInstance(instanceId) {
  try {
    const result = await apiFetch(`/api/llm/instances/${instanceId}/default`, {
      method: 'POST',
    });
    const data = await result.json();
    if (data.ok) {
      await loadNetworkConfig();
      showNetworkStatus('✓ 已设置为默认实例', 'ok');
    } else {
      alert('操作失败: ' + (data.error || '未知错误'));
    }
  } catch (e) {
    alert('操作失败: ' + e.message);
  }
}

async function testLlmInstance(instanceId) {
  const btn = event.target;
  const origText = btn.textContent;
  btn.textContent = '⏳';
  btn.disabled = true;
  try {
    const result = await apiFetch(`/api/llm/instances/${instanceId}/test`, {
      method: 'POST',
    });
    const data = await result.json();
    if (data.ok) {
      alert(`✓ 连接成功！\n\n提供商: ${data.provider}\n模型: ${data.model}\n响应时间: ${data.elapsed}秒\n响应: ${data.response || ''}`);
    } else {
      alert('✗ 连接失败: ' + (data.error || '未知错误'));
    }
  } catch (e) {
    alert('✗ 请求失败: ' + e.message);
  } finally {
    btn.textContent = origText;
    btn.disabled = false;
  }
}

// ════════════════════════════════════════════════════════════
// MCP 服务管理
// ════════════════════════════════════════════════════════════

let __editingMcpServiceId = null;

/**
 * 渲染 MCP 服务列表
 */
function renderMcpServices(services) {
  const container = document.getElementById('mcp-services-list');
  if (!container) return;

  if (services.length === 0) {
    container.innerHTML = '<div class="empty-state">暂无 MCP 服务，点击上方按钮添加</div>';
    return;
  }

  container.innerHTML = services.map(service => `
    <div class="mcp-service-card ${service.enabled ? '' : 'disabled'}">
      <div class="mcp-service-header">
        <div style="flex:1">
          <div class="mcp-service-name">${escapeHtml(service.name)}</div>
          <div class="mcp-service-meta">${service.protocol.toUpperCase()}://${escapeHtml(service.address)}:${service.port}</div>
        </div>
        <div class="mcp-service-actions">
          <label class="toggle-switch small" title="${service.enabled ? '禁用' : '启用'}">
            <input type="checkbox" ${service.enabled ? 'checked' : ''} onchange="toggleMcpService('${service.id}', this.checked)">
            <span class="toggle-slider"></span>
          </label>
          <button class="btn-xs" onclick="editMcpService('${service.id}')" title="编辑">✏️</button>
          <button class="btn-xs danger" onclick="deleteMcpService('${service.id}', '${escapeHtml(service.name)}')" title="删除">🗑</button>
        </div>
      </div>
      <div class="mcp-service-body">
        <div class="mcp-service-stats">
          <span>⏱ ${service.timeout}s 超时</span>
          <span>🔄 ${service.max_retries}次重试</span>
          <span>📶 ${service.retry_strategy === 'fixed' ? '固定间隔' : service.retry_strategy === 'exponential' ? '指数退避' : '无重试'}</span>
        </div>
        ${service.security_methods && service.security_methods.length > 0 ? 
          `<div class="mcp-security-methods">🔐 ${service.security_methods.join(', ')}</div>` : ''}
        ${service.description ? `<div class="mcp-service-desc">📝 ${escapeHtml(service.description)}</div>` : ''}
      </div>
    </div>
  `).join('');
}

/**
 * 编辑 MCP 服务（入口，调用模态框）
 */
function editMcpService(serviceId) {
  const service = __mcpServices.find(s => s.id === serviceId);
  if (service) {
    showMcpServiceModal(serviceId);
  } else {
    console.error('[网络配置] 未找到 MCP 服务:', serviceId);
    alert('未找到该 MCP 服务');
  }
}

/**
 * 显示添加/编辑 MCP 服务模态框
 */
function showMcpServiceModal(serviceId = null) {
  const modal = document.getElementById('mcp-service-modal');
  const title = document.getElementById('mcp-modal-title');
  const form = document.getElementById('mcp-service-form');

  if (serviceId) {
    title.textContent = '编辑 MCP 服务';
    __editingMcpServiceId = serviceId;
    const service = __mcpServices.find(s => s.id === serviceId);
    if (service) {
      set('mcp-form-name', service.name);
      set('mcp-form-address', service.address);
      set('mcp-form-port', service.port);
      set('mcp-form-protocol', service.protocol);
      set('mcp-form-timeout', service.timeout);
      set('mcp-form-retry-strategy', service.retry_strategy);
      set('mcp-form-max-retries', service.max_retries);
      set('mcp-form-cert-path', service.certificate_path);
      set('mcp-form-description', service.description);
      set('mcp-form-enabled', service.enabled);
      
      // 设置安全认证方式复选框
      ['tls', 'token', 'certificate'].forEach(method => {
        set(`mcp-form-security-${method}`, service.security_methods?.includes(method) || false);
      });
    }
  } else {
    title.textContent = '添加 MCP 服务';
    __editingMcpServiceId = null;
    form.reset();
    set('mcp-form-port', 8080);
    set('mcp-form-protocol', 'http');
    set('mcp-form-timeout', 30);
    set('mcp-form-retry-strategy', 'fixed');
    set('mcp-form-max-retries', 3);
    set('mcp-form-enabled', true);
  }

  modal.style.display = 'block';
}

/**
 * 隐藏 MCP 服务模态框
 */
function hideMcpServiceModal() {
  const modal = document.getElementById('mcp-service-modal');
  modal.style.display = 'none';
  __editingMcpServiceId = null;
}

/**
 * 保存 MCP 服务
 */
async function saveMcpService() {
  const securityMethods = [];
  if (get('mcp-form-security-tls')) securityMethods.push('tls');
  if (get('mcp-form-security-token')) securityMethods.push('token');
  if (get('mcp-form-security-certificate')) securityMethods.push('certificate');

  const service = {
    name: get('mcp-form-name'),
    address: get('mcp-form-address'),
    port: num('mcp-form-port'),
    protocol: get('mcp-form-protocol'),
    timeout: num('mcp-form-timeout'),
    retry_strategy: get('mcp-form-retry-strategy'),
    max_retries: num('mcp-form-max-retries'),
    security_methods: securityMethods,
    certificate_path: get('mcp-form-cert-path'),
    description: get('mcp-form-description'),
    enabled: get('mcp-form-enabled'),
  };

  // 验证
  const errors = validateMcpService(service);
  if (errors.length > 0) {
    alert('验证失败:\n' + errors.join('\n'));
    return;
  }

  try {
    let result;
    if (__editingMcpServiceId) {
      result = await apiFetch(`/api/mcp/services/${__editingMcpServiceId}`, {
        method: 'PUT',
        body: JSON.stringify({ updates: service }),
      });
    } else {
      result = await apiFetch('/api/mcp/services', {
        method: 'POST',
        body: JSON.stringify({ service }),
      });
    }

    const data = await result.json();
    if (data.ok) {
      await loadMcpServices();
      hideMcpServiceModal();
      showNetworkStatus(__editingMcpServiceId ? '✓ MCP 服务已更新' : '✓ MCP 服务已添加', 'ok');
    } else {
      alert('操作失败: ' + (data.error || '未知错误'));
    }
  } catch (e) {
    alert('操作失败: ' + e.message);
  }
}

/**
 * 验证 MCP 服务配置
 */
function validateMcpService(service) {
  const errors = [];

  if (!service.name.trim()) {
    errors.push('服务名称不能为空');
  }

  if (!service.address.trim()) {
    errors.push('MCP 服务地址不能为空');
  }

  if (!service.port || service.port < 1 || service.port > 65535) {
    errors.push('通信端口必须在 1-65535 之间');
  }

  if (!['http', 'https'].includes(service.protocol)) {
    errors.push('协议类型必须是 HTTP 或 HTTPS');
  }

  if (service.timeout < 1 || service.timeout > 300) {
    errors.push('超时时间必须在 1-300 秒之间');
  }

  if (!['fixed', 'exponential', 'none'].includes(service.retry_strategy)) {
    errors.push('重试策略必须是固定间隔/指数退避/无重试');
  }

  if (service.max_retries < 0 || service.max_retries > 10) {
    errors.push('重试次数必须在 0-10 之间');
  }

  return errors;
}

/**
 * 删除 MCP 服务
 */
async function deleteMcpService(serviceId, name) {
  if (!confirm(`确定要删除 MCP 服务 "${name}" 吗？此操作不可撤销。`)) {
    return;
  }

  try {
    const result = await apiFetch(`/api/mcp/services/${serviceId}`, {
      method: 'DELETE',
    });
    const data = await result.json();
    if (data.ok) {
      await loadMcpServices();
      showNetworkStatus('✓ MCP 服务已删除', 'ok');
    } else {
      alert('删除失败: ' + (data.error || '未知错误'));
    }
  } catch (e) {
    alert('删除失败: ' + e.message);
  }
}

/**
 * 切换 MCP 服务启用状态
 */
async function toggleMcpService(serviceId, enabled) {
  try {
    await apiFetch(`/api/mcp/services/${serviceId}`, {
      method: 'PUT',
      body: JSON.stringify({ updates: { enabled } }),
    });
    await loadMcpServices();
  } catch (e) {
    console.error('切换 MCP 服务状态失败:', e);
  }
}

/**
 * 切换 MCP 全局启用状态
 */
async function toggleMcpEnabled(enabled) {
  try {
    const result = await apiFetch('/api/mcp/enable', {
      method: 'POST',
      body: JSON.stringify({ enabled }),
    });
    const data = await result.json();
    if (!data.ok) {
      alert('操作失败: ' + (data.error || '未知错误'));
    }
  } catch (e) {
    alert('操作失败: ' + e.message);
  }
}

// ════════════════════════════════════════════════════════════
// 搜索引擎实例管理
// ════════════════════════════════════════════════════════════

let __searchInstances = [];
let __editingSearchId = null;

async function loadSearchInstances() {
  try {
    const res = await apiFetch('/api/search/instances');
    const result = await res.json();
    if (result.ok) {
      __searchInstances = result.instances || [];
      renderSearchInstances(__searchInstances);
    }
  } catch (e) {
    console.error('[网络配置] 加载搜索引擎实例失败:', e);
  }
}

/** 刷新优先级列表（加载自定义引擎后或增删后调用） */
function refreshPriorityWithInstances() {
  if (!__networkConfigCache) return;
  const priority = __networkConfigCache.search.engine_priority || [];

  // 收集已在 priority 中的所有标识（ID、名称、类型）
  const existingIds = new Set(priority);
  const seen = new Set(priority);

  // 将名称和类型也计入已存在
  (__searchInstances || []).forEach(inst => {
    if (inst.name && seen.has(inst.name)) existingIds.add(inst.id);
    if (inst.engine_type && seen.has(inst.engine_type)) existingIds.add(inst.id);
  });

  let changed = false;
  (__searchInstances || []).forEach(inst => {
    const eid = inst.id || inst.name;
    if (eid && !existingIds.has(eid)) {
      priority.push(eid);
      existingIds.add(eid);
      changed = true;
    }
  });

  renderSearchEnginePriority(
    priority,
    {},
    __searchInstances
  );
}

function renderSearchInstances(instances) {
  const container = document.getElementById('search-instances-list');
  if (!container) return;

  if (instances.length === 0) {
    container.innerHTML = '<div class="empty-state">暂无搜索引擎实例，点击上方添加</div>';
    return;
  }

  // 查找哪个是默认
  const defaultId = __networkConfigCache?.search?.default_engine || '';

  container.innerHTML = instances.map(inst => {
    const id = inst.id || inst.name;
    const isDefault = inst.is_default || (id === defaultId);
    const isCustom = inst.engine_type === 'custom';
    const endpoint = isCustom ? inst.api_endpoint : `内置引擎: ${inst.engine_type}`;

    return `
    <div class="llm-instance-card ${inst.enabled ? '' : 'disabled'}">
      <div class="llm-instance-header">
        <div style="flex:1">
          <div class="llm-instance-name">${escapeHtml(inst.name)}</div>
          <div class="llm-instance-meta">${inst.engine_type} · ${isCustom ? '自定义' : '内置'}</div>
        </div>
        <div class="llm-instance-actions">
          ${isDefault ? '<span class="default-badge">默认</span>' : ''}
          <label class="toggle-switch small">
            <input type="checkbox" ${inst.enabled ? 'checked' : ''} onchange="toggleSearchInstance('${id}', this.checked)">
            <span class="toggle-slider"></span>
          </label>
          <button class="btn-xs" onclick="testSearchInstance('${id}')" title="测试">▶</button>
          <button class="btn-xs" onclick="editSearchInstance('${id}')" title="编辑">✏️</button>
          <button class="btn-xs danger" onclick="deleteSearchInstance('${id}', '${escapeHtml(inst.name)}')" title="删除">🗑</button>
        </div>
      </div>
      <div class="llm-instance-body">
        <div class="llm-instance-endpoint">📍 ${escapeHtml(endpoint)}</div>
        <div class="llm-instance-stats">
          <span>⏱ ${inst.timeout}s</span>
          <span>🔑 ${inst.api_key ? 'Key已配置' : '无Key'}</span>
        </div>
      </div>
      <button class="llm-set-default" onclick="setDefaultSearchInstance('${id}')" ${isDefault ? 'disabled' : ''}>
        ${isDefault ? '✓ 已设为默认' : '设为默认'}
      </button>
    </div>`;
  }).join('');
}

function showSearchInstanceModal(instanceId) {
  const modal = document.getElementById('search-instance-modal');
  const title = document.getElementById('search-modal-title');
  const form = document.getElementById('search-instance-form');

  if (instanceId) {
    title.textContent = '编辑搜索引擎';
    __editingSearchId = instanceId;
    const inst = __searchInstances.find(i => (i.id || i.name) === instanceId);
    if (inst) {
      set('si-form-name', inst.name);
      set('si-form-engine-type', inst.engine_type);
      set('si-form-endpoint', inst.api_endpoint || '');
      set('si-form-api-key', inst.api_key || '');
      set('si-form-auth-header', inst.auth_header || 'Authorization: Bearer {key}');
      set('si-form-http-method', inst.http_method || 'GET');
      set('si-form-timeout', inst.timeout || 30);
      set('si-form-query-param', inst.query_param || 'q');
      set('si-form-results-path', inst.results_path || 'data');
      set('si-form-title-field', inst.title_field || 'title');
      set('si-form-url-field', inst.url_field || 'url');
      set('si-form-snippet-field', inst.snippet_field || 'snippet');
    }
  } else {
    title.textContent = '添加搜索引擎';
    __editingSearchId = null;
    form.reset();
    set('si-form-auth-header', 'Authorization: Bearer {key}');
    set('si-form-http-method', 'GET');
    set('si-form-timeout', 30);
    set('si-form-query-param', 'q');
    set('si-form-results-path', 'data');
    set('si-form-title-field', 'title');
    set('si-form-url-field', 'url');
    set('si-form-snippet-field', 'snippet');
  }

  onSearchEngineTypeChange(); // 切换 custom 字段显示
  modal.style.display = 'block';
}

function hideSearchInstanceModal() {
  document.getElementById('search-instance-modal').style.display = 'none';
  __editingSearchId = null;
}

function switchSearchTab(tabId) {
  document.querySelectorAll('#search-instance-modal .tab-panel').forEach(p => p.style.display = 'none');
  document.querySelectorAll('#search-instance-modal .tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + tabId).style.display = 'block';
  document.querySelector(`#search-instance-modal .tab-btn[data-tab="${tabId}"]`).classList.add('active');
}

function onSearchEngineTypeChange() {
  const isCustom = get('si-form-engine-type') === 'custom';
  document.querySelectorAll('.si-custom-field').forEach(el => {
    el.style.display = isCustom ? '' : 'none';
  });
}

async function saveSearchInstance() {
  const instance = {
    name: get('si-form-name'),
    engine_type: get('si-form-engine-type'),
    api_endpoint: get('si-form-endpoint'),
    api_key: get('si-form-api-key'),
    auth_header: get('si-form-auth-header'),
    http_method: get('si-form-http-method'),
    timeout: num('si-form-timeout'),
    query_param: get('si-form-query-param'),
    results_path: get('si-form-results-path'),
    title_field: get('si-form-title-field'),
    url_field: get('si-form-url-field'),
    snippet_field: get('si-form-snippet-field'),
    enabled: true,
  };

  if (!instance.name.trim()) { alert('名称不能为空'); return; }

  try {
    let result;
    if (__editingSearchId) {
      result = await apiFetch(`/api/search/instances/${__editingSearchId}`, {
        method: 'PUT',
        body: JSON.stringify({ updates: instance }),
      });
    } else {
      result = await apiFetch('/api/search/instances', {
        method: 'POST',
        body: JSON.stringify({ instance }),
      });
    }
    const data = await result.json();
    if (data.ok) {
      await loadSearchInstances();
      refreshPriorityWithInstances();
      hideSearchInstanceModal();
      showNetworkStatus(__editingSearchId ? '✓ 搜索引擎已更新' : '✓ 搜索引擎已添加', 'ok');
    } else {
      alert('操作失败: ' + (data.error || '未知错误'));
    }
  } catch (e) {
    alert('操作失败: ' + e.message);
  }
}

async function deleteSearchInstance(instanceId, name) {
  if (!confirm(`确定要删除搜索引擎 "${name}" 吗？`)) return;
  try {
    const result = await apiFetch(`/api/search/instances/${instanceId}`, { method: 'DELETE' });
    const data = await result.json();
    if (data.ok) {
      await loadSearchInstances();
      refreshPriorityWithInstances();
      await loadSearchInstances();
      showNetworkStatus('✓ 搜索引擎已删除', 'ok');
    } else {
      alert('删除失败: ' + (data.error || '未知错误'));
    }
  } catch (e) {
    alert('删除失败: ' + e.message);
  }
}

async function toggleSearchInstance(instanceId, enabled) {
  try {
    await apiFetch(`/api/search/instances/${instanceId}`, {
      method: 'PUT',
      body: JSON.stringify({ updates: { enabled } }),
    });
    await loadSearchInstances();
  } catch (e) {
    console.error('切换状态失败:', e);
  }
}

async function setDefaultSearchInstance(instanceId) {
  try {
    const result = await apiFetch(`/api/search/instances/${instanceId}/default`, { method: 'POST' });
    const data = await result.json();
    if (data.ok) {
      await loadSearchInstances();
      showNetworkStatus('✓ 已设为默认搜索引擎', 'ok');
    } else {
      alert('操作失败: ' + (data.error || '未知错误'));
    }
  } catch (e) {
    alert('操作失败: ' + e.message);
  }
}

async function editSearchInstance(instanceId) {
  const inst = __searchInstances.find(i => (i.id || i.name) === instanceId);
  if (inst) showSearchInstanceModal(inst.id || inst.name);
  else alert('未找到该实例');
}

async function testSearchInstance(instanceId) {
  const btn = event.target;
  btn.textContent = '⏳';
  btn.disabled = true;
  try {
    const result = await apiFetch(`/api/search/instances/${instanceId}/test`, { method: 'POST' });
    const data = await result.json();
    if (data.ok && data.results && data.results.length > 0) {
      const preview = data.results.slice(0, 2).map(r => `• ${r.title || '(无标题)'}`).join('\n');
      alert('✓ 测试成功！\n\n返回 ' + data.total + ' 条结果，前 2 条：\n' + preview);
    } else {
      alert('✗ 测试失败: ' + (data.error || '无返回结果'));
    }
  } catch (e) {
    alert('✗ 测试异常: ' + e.message);
  } finally {
    btn.textContent = '▶';
    btn.disabled = false;
  }
}

/**
 * HTML 转义
 */
function escapeHtml(text) {
  if (!text) return '';
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}