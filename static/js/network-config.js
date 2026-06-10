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
  // LLM 服务
  set('nc-llm-enabled', config.llm.enabled);
  set('nc-llm-provider', config.llm.provider);
  set('nc-llm-apikey', config.llm.api_key);
  set('nc-llm-model', config.llm.model);
  set('nc-llm-endpoint', config.llm.api_endpoint || '');
  set('nc-llm-timeout', config.llm.timeout);
  set('nc-llm-retries', config.llm.max_retries);

  // 网络基础设置
  set('nc-network-timeout', config.network.timeout);
  set('nc-network-retries', config.network.max_retries);
  set('nc-network-backoff', config.network.backoff_factor);
  set('nc-proxy-enabled', config.network.proxy_enabled);
  set('nc-proxy-url', config.network.proxy_url || '');
  toggleProxyUrlRow();

  // 搜索服务
  set('nc-search-enabled', config.search.enabled);
  set('nc-search-engine', config.search.default_engine);
  set('nc-search-max', config.search.max_results);
  set('nc-search-timeout', config.search.timeout || 30);

  // 渲染搜索引擎优先级
  renderSearchEnginePriority(config.search.engine_priority || ['duckduckgo', 'tavily', 'bing', 'brave', 'google']);
  
  // 渲染搜索引擎启用状态
  const engineEnabled = config.search.engine_enabled || {};
  set('nc-engine-duckduckgo', engineEnabled.duckduckgo !== false);
  set('nc-engine-tavily', engineEnabled.tavily !== false);
  set('nc-engine-bing', engineEnabled.bing !== false);
  set('nc-engine-brave', engineEnabled.brave !== false);
  set('nc-engine-google', engineEnabled.google !== false);

  // API Key 配置
  const apiKeys = config.search_api_keys || {};
  set('nc-api-tavily', apiKeys.tavily || '');
  set('nc-api-bing', apiKeys.bing || '');
  set('nc-api-google', apiKeys.google || '');
  set('nc-api-google-cx', apiKeys.google_cx || '');
  set('nc-api-brave', apiKeys.brave || '');

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
function renderSearchEnginePriority(priority) {
  const list = document.getElementById('search-engine-priority-list');
  if (!list) return;

  const engineNames = {
    duckduckgo: 'DuckDuckGo',
    tavily: 'Tavily',
    bing: 'Bing',
    brave: 'Brave',
    google: 'Google'
  };

  list.innerHTML = '';

  priority.forEach(engine => {
    if (engineNames[engine]) {
      const item = document.createElement('div');
      item.className = 'priority-item';
      item.dataset.engine = engine;
      item.innerHTML = `
        <span class="priority-handle">⋮⋮</span>
        <span class="priority-label">${engineNames[engine]}</span>
        <label class="toggle-switch small">
          <input type="checkbox" id="nc-engine-${engine}" checked onchange="onEngineToggle('${engine}', this.checked)">
          <span class="toggle-slider"></span>
        </label>
      `;
      list.appendChild(item);
    }
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

  const config = {
    llm: {
      enabled: get('nc-llm-enabled'),
      provider: get('nc-llm-provider'),
      api_key: get('nc-llm-apikey'),
      model: get('nc-llm-model'),
      api_endpoint: get('nc-llm-endpoint'),
      timeout: num('nc-llm-timeout'),
      max_retries: num('nc-llm-retries'),
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
      default_engine: get('nc-search-engine'),
      max_results: num('nc-search-max'),
      timeout: num('nc-search-timeout'),
      engine_priority: enginePriority,
      engine_enabled: engineEnabled,
    },
    search_api_keys: {
      tavily: get('nc-api-tavily'),
      bing: get('nc-api-bing'),
      google: get('nc-api-google'),
      google_cx: get('nc-api-google-cx'),
      brave: get('nc-api-brave'),
    },
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

  if (config.network.proxy_enabled && config.network.proxy_url) {
    try {
      new URL(config.network.proxy_url);
    } catch {
      errors.push('代理 URL 格式无效');
    }
  }

  if (config.external_services.error_reporting.enabled && config.external_services.error_reporting.webhook_url) {
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
  if (!container) return;

  // 更新默认实例选择下拉框
  updateDefaultLlmInstanceSelect(instances);

  if (instances.length === 0) {
    container.innerHTML = '<div class="empty-state">暂无 LLM 实例，点击上方按钮添加</div>';
    return;
  }

  container.innerHTML = instances.map(instance => `
    <div class="llm-instance-card ${instance.enabled ? '' : 'disabled'}">
      <div class="llm-instance-header">
        <div style="flex:1">
          <div class="llm-instance-name">${escapeHtml(instance.name)}</div>
          <div class="llm-instance-meta">${escapeHtml(instance.provider)} · ${escapeHtml(instance.model)}</div>
        </div>
        <div class="llm-instance-actions">
          ${instance.is_default ? '<span class="default-badge">默认</span>' : ''}
          <label class="toggle-switch small" title="${instance.enabled ? '禁用' : '启用'}">
            <input type="checkbox" ${instance.enabled ? 'checked' : ''} onchange="toggleLlmInstance('${instance.id}', this.checked)">
            <span class="toggle-slider"></span>
          </label>
          <button class="btn-xs" onclick="editLlmInstance('${instance.id}')" title="编辑">✏️</button>
          <button class="btn-xs danger" onclick="deleteLlmInstance('${instance.id}', '${escapeHtml(instance.name)}')" title="删除">🗑</button>
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
      <button class="llm-set-default" onclick="setDefaultLlmInstance('${instance.id}')" ${instance.is_default ? 'disabled' : ''}>
        ${instance.is_default ? '✓ 已设为默认' : '设为默认'}
      </button>
    </div>
  `).join('');
}

/**
 * 更新默认实例选择下拉框
 */
function updateDefaultLlmInstanceSelect(instances) {
  const select = document.getElementById('nc-default-llm-instance');
  if (!select) return;

  // 获取当前选中的默认实例
  const currentDefault = __networkConfigCache?.default_llm_instance;

  // 清空并重新填充选项
  select.innerHTML = '<option value="">自动选择</option>';
  
  instances.forEach(instance => {
    const option = document.createElement('option');
    option.value = instance.id || instance.name;
    option.textContent = instance.name;
    if ((instance.id || instance.name) === currentDefault) {
      option.selected = true;
    }
    select.appendChild(option);
  });
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
    const instance = __llmInstances.find(i => i.id === instanceId);
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
  try {
    await apiFetch(`/api/llm/instances/${instanceId}`, {
      method: 'PUT',
      body: JSON.stringify({ updates: { enabled } }),
    });
    await loadLlmInstances();
  } catch (e) {
    console.error('切换 LLM 实例状态失败:', e);
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

/**
 * HTML 转义
 */
function escapeHtml(text) {
  if (!text) return '';
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}