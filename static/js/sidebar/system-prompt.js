// ════════════════════════════════════════════════════════════
// 云枢 · 系统身份提示词配置视图
// 组件级开关管理、Token 统计、预览、应用
// UI 由注册表数据驱动，新增 SECTION_REGISTRY 条目自动渲染
// ════════════════════════════════════════════════════════════

// ── 全局状态 ──
let spConfig = null;
let spStats = null;
let spRegistry = [];

// ════════════════════════════════════════════════════════════
//  主加载入口
// ════════════════════════════════════════════════════════════

async function loadSystemPrompt() {
  const editor = document.getElementById('system-prompt-editor');
  if (editor) editor.innerHTML = '';

  try {
    const data = await app.get('/api/system-prompt/config');
    if (data.ok === false) throw new Error(data.error || '加载失败');
    spConfig = data;
    spStats = data.stats || {};
    spRegistry = data.registry || [];
    renderFullView(data);
  } catch (e) {
    console.error('加载提示词配置失败:', e);
    showError(e.message);
  }
}

// ════════════════════════════════════════════════════════════
//  动态渲染（数据驱动，遍历注册表）
// ════════════════════════════════════════════════════════════

function renderFullView(data) {
  const sections = data.sections || {};
  const stats = data.stats || {};
  const container = document.getElementById('sp-sections-container');
  if (!container) return;

  // 1. 动态生成所有 section 卡片
  let html = '';
  for (const entry of spRegistry) {
    html += renderSection(entry, sections);
  }
  container.innerHTML = html;

  // 2. 从配置中恢复各组件的状态
  for (const [key, sec] of Object.entries(sections)) {
    // Toggle 状态
    const toggle = document.querySelector(`.sp-section-toggle[data-key="${key}"]`);
    if (toggle) toggle.checked = sec.enabled !== false;

    // 可编辑组件的自定义内容
    loadCustomContent(key, sec);

    // Range slider 值
    const tokenLimit = sec.token_limit;
    if (tokenLimit) {
      const slider = document.querySelector(`.sp-range[data-key="${key}"]`);
      if (slider) {
        slider.value = tokenLimit;
        const valDisplay = slider.parentElement?.querySelector('.sp-range-value');
        if (valDisplay) valDisplay.textContent = formatTokenNumber(tokenLimit);
      }
    }
  }

  // 3. 模块可用性标记
  updateModuleBadges(sections);

  // 4. 更新 Token 统计
  updateTokenStats(stats);

  // 5. 展开所有 section body
  document.querySelectorAll('#view-system-prompt .skills-body').forEach(el => {
    el.style.display = 'block';
  });

  // 6. 生成预览
  spPreviewFull();
}

// ════════════════════════════════════════════════════════════
//  Section 卡片渲染工厂
//  根据 ui_type 分发到不同渲染器
// ════════════════════════════════════════════════════════════

function renderSection(entry, sections) {
  const key = entry.key;
  const uiType = entry.ui_type || 'toggle';
  const icon = entry.icon || '📋';
  const label = entry.label || key;
  const desc = entry.description || '';
  const tokens = entry.tokens || 0;
  const tokenRange = entry.range || '';

  const toggleHtml = `<label class="toggle-switch" onclick="event.stopPropagation()">
    <input type="checkbox" class="sp-section-toggle" data-key="${key}" onchange="spOnToggleChange(this)">
    <span class="toggle-slider"></span>
  </label>`;

  const tokenBadge = tokenRange
    ? `<span class="sp-token-badge" style="margin-left:auto;font-size:10px;color:var(--text-muted)">~${tokens} tokens</span>`
    : '';

  switch (uiType) {
    case 'editable':
      return renderEditable(key, icon, label, desc, toggleHtml, tokenBadge, entry, sections);
    case 'sub_toggles':
      return renderSubToggles(key, icon, label, desc, entry, sections);
    case 'configurable':
      return renderConfigurable(key, icon, label, desc, toggleHtml, tokenBadge, sections);
    case 'toggle':
    default:
      return renderSimpleToggle(key, icon, label, desc, toggleHtml, tokenBadge, entry, sections);
  }
}

// ── 简单开关 ──
function renderSimpleToggle(key, icon, label, desc, toggleHtml, tokenBadge, entry, sections) {
  const badgeKey = entry.badge_key;
  const badgeHtml = badgeKey
    ? `<span class="sp-badge-${badgeKey}" style="display:none;font-size:9px;padding:0 6px;border-radius:3px;background:var(--bg-primary);color:var(--text-muted)"></span>`
    : '';

  return `<div class="network-section">
    <div class="network-section-header skills-toggle-header" onclick="spToggleSectionBody(this)">
      <span class="section-icon">${icon}</span>
      <span class="section-title">${esc(label)}</span>
      ${toggleHtml}
      <span class="skills-toggle-icon" style="margin-left:8px">▼</span>
      ${badgeHtml}
      ${tokenBadge}
    </div>
    <div class="network-section-body skills-body">
      <p style="font-size:11px;color:var(--text-secondary);margin:0">${esc(desc)}</p>
    </div>
  </div>`;
}

// ── 可编辑（身份设定 / 核心原则） ──
function renderEditable(key, icon, label, desc, toggleHtml, tokenBadge, entry, sections) {
  const placeholder = key === 'identity'
    ? '在这里编写自定义身份描述...\n\n可使用占位符: {current_date} 将在运行时自动填充。'
    : '在这里编写自定义行为原则...\n\n每行一条，将直接插入到 system prompt 中。';

  // 默认显示内容
  const defaultText = key === 'identity'
    ? '你是「云枢」，一个生活在电脑里的数字生命体。你诞生于这台计算机中，拥有完整的感知-认知-行动能力。'
    : '9 条行为铁律，控制 LLM 的行为边界（可在下方逐条开关或切换到自定义模式自由编写）';

  return `<div class="network-section">
    <div class="network-section-header skills-toggle-header" onclick="spToggleSectionBody(this)">
      <span class="section-icon">${icon}</span>
      <span class="section-title">${esc(label)}</span>
      ${toggleHtml}
      <span class="skills-toggle-icon" style="margin-left:8px">▼</span>
      ${tokenBadge}
    </div>
    <div class="network-section-body skills-body">
      <p style="font-size:11px;color:var(--text-secondary);margin:0 0 8px">${esc(desc)}</p>
      <div class="form-row" style="margin-bottom:6px">
        <div style="display:flex;align-items:center;gap:8px">
          <button class="btn-sm sp-mode-btn" data-key="${key}" onclick="spToggleCustomMode(this)" style="font-size:9px;padding:2px 10px">✏ 自定义</button>
          <span class="sp-mode-indicator" data-key="${key}" style="font-size:9px;color:var(--text-muted)">• 默认</span>
        </div>
      </div>
      <div class="sp-default-content" data-key="${key}">
        <div class="sp-display-text" style="font-size:11px;color:var(--text-secondary);background:var(--bg-primary);padding:6px 10px;border-radius:4px;border:1px solid var(--border-color);line-height:1.5">
          ${esc(defaultText)}
        </div>
        ${key === 'principles' ? '<div id="sp-principles-list" style="margin-top:4px"></div>' : ''}
      </div>
      <div class="sp-custom-content" data-key="${key}" style="display:none">
        <textarea class="sp-content-editor" data-key="${key}" spellcheck="false"
          style="width:100%;min-height:${key === 'identity' ? '120' : '150'}px;padding:8px;font-family:Consolas,monospace;font-size:11px;line-height:1.5;background:var(--bg-primary);color:var(--text-primary);border:1px solid var(--border-color);border-radius:4px;resize:vertical;white-space:pre"
          placeholder="${esc(placeholder)}"></textarea>
      </div>
    </div>
  </div>`;
}

// ── 子开关组（感知层） ──
function renderSubToggles(key, icon, label, desc, entry, sections) {
  const children = entry.children || [];
  let childrenHtml = '';
  for (const ch of children) {
    const chKey = ch.key;
    const chLabel = ch.label || chKey;
    const chDesc = ch.description || '';
    const chRange = ch.range || '';
    const chTokens = ch.tokens || 0;
    const chType = ch.ui_type || 'toggle';
    const isConfigurable = ch.configurable || false;
    const defaultLimit = ch.default_token_limit || 0;

    // Child toggle + description
    childrenHtml += `<div class="form-row" style="margin-bottom:${isConfigurable ? '6' : '0'}px">
      <div style="display:flex;align-items:center;gap:10px;flex:1">
        <label class="toggle-switch small" style="flex-shrink:0">
          <input type="checkbox" class="sp-section-toggle" data-key="${chKey}" onchange="spOnToggleChange(this)">
          <span class="toggle-slider"></span>
        </label>
        <div style="flex:1">
          <div style="font-size:12px;color:var(--text-primary)">${esc(chLabel)}</div>
          <div style="font-size:10px;color:var(--text-muted)">${esc(chDesc)}</div>
        </div>
        <span class="sp-token-badge" style="font-size:10px;color:var(--text-muted);flex-shrink:0">~${chTokens} tokens</span>
      </div>
    </div>`;

    // Configurable range slider
    if (isConfigurable && defaultLimit) {
      childrenHtml += `<div class="form-row sp-section-detail" data-parent="${chKey}" style="padding-left:38px;margin-top:-4px;margin-bottom:8px">
        <label>最大长度</label>
        <div style="display:flex;align-items:center;gap:8px">
          <input type="range" class="sp-range" data-key="${chKey}" data-field="token_limit"
            min="100" max="1600" step="100" value="${defaultLimit}"
            oninput="spUpdateRange(this)" style="width:160px">
          <span class="sp-range-value" style="font-size:11px;color:var(--text-secondary);min-width:40px">${defaultLimit}</span>
        </div>
      </div>`;
    }
  }

  return `<div class="network-section">
    <div class="network-section-header skills-toggle-header" onclick="spToggleSectionBody(this)">
      <span class="section-icon">${icon}</span>
      <span class="section-title">${esc(label)}</span>
      <span style="font-size:10px;color:var(--text-muted);margin-left:4px">${esc(desc)}</span>
      <span class="skills-toggle-icon" style="margin-left:auto">▼</span>
    </div>
    <div class="network-section-body skills-body">
      ${childrenHtml}
    </div>
  </div>`;
}

// ── 可配置（带范围滑块） ──
function renderConfigurable(key, icon, label, desc, toggleHtml, tokenBadge, sections) {
  const sec = sections[key] || {};
  const currentLimit = sec.token_limit || 131072;

  return `<div class="network-section">
    <div class="network-section-header skills-toggle-header" onclick="spToggleSectionBody(this)">
      <span class="section-icon">${icon}</span>
      <span class="section-title">${esc(label)}</span>
      ${toggleHtml}
      <span class="skills-toggle-icon" style="margin-left:8px">▼</span>
      ${tokenBadge}
    </div>
    <div class="network-section-body skills-body">
      <p style="font-size:11px;color:var(--text-secondary);margin:0 0 8px">${esc(desc)}</p>
      <div class="form-row sp-section-detail" data-parent="${key}" style="margin-bottom:0">
        <label>历史消息 Token 预算 <span class="hint">越大→记忆越好→token 越多</span></label>
        <div style="display:flex;align-items:center;gap:8px">
          <input type="range" class="sp-range" data-key="${key}" data-field="token_limit"
            min="4096" max="131072" step="4096" value="${currentLimit}"
            oninput="spUpdateRange(this)" style="width:200px">
          <span class="sp-range-value" style="font-size:11px;color:var(--text-secondary);min-width:60px">${formatTokenNumber(currentLimit)}</span>
        </div>
        <div style="font-size:10px;color:var(--text-muted);margin-top:2px">
          建议值：8192（极省）· 16384（经济）· 32768（均衡）· 65536（充足）· 131072（完整）
        </div>
      </div>
    </div>
  </div>`;
}

// ════════════════════════════════════════════════════════════
//  自定义内容模式
// ════════════════════════════════════════════════════════════

function loadCustomContent(key, sec) {
  if (!sec) return;
  const editor = document.querySelector(`.sp-content-editor[data-key="${key}"]`);
  if (!editor) return;

  const customContent = sec.custom_content || '';
  editor.value = customContent;

  const indicator = document.querySelector(`.sp-mode-indicator[data-key="${key}"]`);
  const btn = document.querySelector(`.sp-mode-btn[data-key="${key}"]`);
  const hasCustom = Boolean(customContent.trim());

  if (hasCustom) {
    showCustomMode(key, true);
    if (indicator) { indicator.textContent = '• 自定义模式'; indicator.style.color = 'var(--accent)'; }
    if (btn) btn.textContent = '📋 默认';
  } else {
    showCustomMode(key, false);
    if (indicator) { indicator.textContent = key === 'principles' ? '• 默认（逐条开关）' : '• 默认'; indicator.style.color = 'var(--text-muted)'; }
    if (btn) btn.textContent = '✏ 自定义';
  }

  // 如果是 principles，渲染原则列表
  if (key === 'principles' && !hasCustom) {
    renderPrinciplesFromConfig();
  }
}

function renderPrinciplesFromConfig() {
  const container = document.getElementById('sp-principles-list');
  if (!container) return;
  const items = spConfig?.sections?.principles?.extra_params?.items || [];
  if (!items.length) {
    container.innerHTML = '<div style="font-size:11px;color:var(--text-muted);padding:6px">（无原则配置）</div>';
    return;
  }
  let html = '';
  for (let i = 0; i < items.length; i++) {
    const p = items[i];
    const num = i + 1;
    const isUrgent = num === 8 || num === 9;
    html += `<div class="form-row" style="margin-bottom:2px;padding:4px 8px;border-radius:4px;background:var(--bg-primary)">
      <div style="display:flex;align-items:center;gap:10px;flex:1">
        <label class="toggle-switch small" style="flex-shrink:0">
          <input type="checkbox" class="sp-principle-toggle" data-index="${i}"
                 ${p.enabled !== false ? 'checked' : ''}
                 ${isUrgent ? 'disabled' : ''}
                 onchange="spOnToggleChange(this)">
          <span class="toggle-slider"></span>
        </label>
        <div style="flex:1;font-size:11px;color:var(--text-primary);line-height:1.4">
          ${esc(p.text || '原则 ' + num)}
          ${isUrgent ? '<span style="color:var(--warning);font-size:9px">（建议保留）</span>' : ''}
        </div>
        <span style="font-size:9px;color:var(--text-muted);flex-shrink:0">~${num === 8 ? 80 : num === 9 ? 40 : 60} tokens</span>
      </div>
    </div>`;
  }
  container.innerHTML = html;
}

function spToggleCustomMode(btn) {
  const key = btn.dataset.key;
  const isCustom = document.querySelector(`.sp-custom-content[data-key="${key}"]`)?.style.display !== 'none';
  showCustomMode(key, !isCustom);

  const indicator = document.querySelector(`.sp-mode-indicator[data-key="${key}"]`);
  if (!isCustom) {
    btn.textContent = '📋 默认';
    if (indicator) { indicator.textContent = '• 自定义模式'; indicator.style.color = 'var(--accent)'; }
    const editor = document.querySelector(`.sp-content-editor[data-key="${key}"]`);
    if (editor) editor.focus();
  } else {
    btn.textContent = '✏ 自定义';
    if (indicator) { indicator.textContent = key === 'principles' ? '• 默认（逐条开关）' : '• 默认'; indicator.style.color = 'var(--text-muted)'; }
  }
}

function showCustomMode(key, isCustom) {
  const dc = document.querySelector(`.sp-default-content[data-key="${key}"]`);
  const cc = document.querySelector(`.sp-custom-content[data-key="${key}"]`);
  if (dc) dc.style.display = isCustom ? 'none' : 'block';
  if (cc) cc.style.display = isCustom ? 'block' : 'none';
}

// ════════════════════════════════════════════════════════════
//  模块可用性标记
// ════════════════════════════════════════════════════════════

function updateModuleBadges(sections) {
  // 遍历注册表，查找有 badge_key 的条目
  for (const entry of spRegistry) {
    const badgeKey = entry.badge_key;
    if (!badgeKey) continue;
    const badgeEl = document.querySelector(`.sp-badge-${badgeKey}`);
    if (!badgeEl) continue;
    const sec = sections[entry.key] || {};
    const available = sec.extra_params?.module_available;
    badgeEl.textContent = available ? '已安装' : '未安装';
    badgeEl.style.display = 'inline-block';
    badgeEl.style.background = available ? 'var(--success)' : 'var(--danger)';
    badgeEl.style.color = '#fff';
  }
}

// ════════════════════════════════════════════════════════════
//  Token 统计
// ════════════════════════════════════════════════════════════

function updateTokenStats(stats) {
  if (!stats) return;
  let total = 0, disabled = 0, savings = 0;
  for (const [key, s] of Object.entries(stats)) {
    if (s.enabled === false) { disabled++; savings += s.tokens || 0; }
    else { total += s.tokens || 0; }
  }
  const grandTotal = total + 150 + 3000; // base + tools params

  const el = document.getElementById('sp-total-tokens');
  if (el) el.textContent = `≈ ${formatTokenNumber(grandTotal)} tokens/次`;

  const estEl = document.getElementById('sp-estimated-tokens');
  if (estEl) estEl.textContent = formatTokenNumber(grandTotal);

  const bar = document.getElementById('sp-savings-bar');
  if (bar) {
    if (disabled > 0) {
      bar.style.display = 'block';
      document.getElementById('sp-disabled-count').textContent = disabled;
      document.getElementById('sp-savings-amount').textContent = formatTokenNumber(savings);
    } else {
      bar.style.display = 'none';
    }
  }
}

// ════════════════════════════════════════════════════════════
//  交互事件
// ════════════════════════════════════════════════════════════

function spOnToggleChange(el) {
  updateTokenStats(spStats || {});
}

function spOnPrincipleChange(el) {
  updateTokenStats(spStats || {});
}

function spUpdateRange(el) {
  const valDisplay = el.parentElement?.querySelector('.sp-range-value');
  if (valDisplay) valDisplay.textContent = formatTokenNumber(parseInt(el.value) || 0);
}

function spToggleSectionBody(headerEl) {
  const body = headerEl.parentElement?.querySelector('.skills-body');
  if (!body) return;
  const isOpen = body.style.display !== 'none';
  body.style.display = isOpen ? 'none' : 'block';
  const icon = headerEl.querySelector('.skills-toggle-icon');
  if (icon) icon.textContent = isOpen ? '▶' : '▼';
}

// ════════════════════════════════════════════════════════════
//  收集当前 UI 状态 → 配置对象
// ════════════════════════════════════════════════════════════

function collectConfigFromUI() {
  const config = JSON.parse(JSON.stringify(spConfig || { sections: {} }));
  const sections = config.sections || {};

  // Toggle 状态
  document.querySelectorAll('.sp-section-toggle').forEach(t => {
    const key = t.dataset.key;
    if (sections[key]) sections[key].enabled = t.checked;
  });

  // 原则开关状态
  if (sections.principles?.extra_params?.items) {
    document.querySelectorAll('.sp-principle-toggle').forEach(t => {
      const idx = parseInt(t.dataset.index);
      if (sections.principles.extra_params.items[idx])
        sections.principles.extra_params.items[idx].enabled = t.checked;
    });
  }

  // 自定义内容
  document.querySelectorAll('.sp-content-editor').forEach(el => {
    const key = el.dataset.key;
    if (sections[key]) sections[key].custom_content = el.value.trim() || '';
  });

  // Range slider
  document.querySelectorAll('.sp-range').forEach(s => {
    const key = s.dataset.key, field = s.dataset.field;
    if (sections[key] && field) sections[key][field] = parseInt(s.value) || 0;
  });

  return config;
}

// ════════════════════════════════════════════════════════════
//  核心操作
// ════════════════════════════════════════════════════════════

async function spApplyConfig() {
  try {
    const config = collectConfigFromUI();
    const saveResult = await app.post('/api/system-prompt/config', config);
    if (!saveResult.ok) { app.showToast(saveResult.error || '保存失败', 'error'); return; }

    const applyResult = await app.post('/api/system-prompt/config/apply', { config });
    if (!applyResult.ok) { app.showToast(applyResult.error || '应用失败', 'error'); return; }

    const previewEl = document.getElementById('sp-full-preview');
    if (previewEl && applyResult.template) previewEl.textContent = applyResult.template;
    app.showToast('✅ 配置已应用，下次对话生效');

    const fresh = await app.get('/api/system-prompt/config');
    if (fresh.ok !== false) { spConfig = fresh; spStats = fresh.stats || {}; updateTokenStats(spStats); }
  } catch (e) { app.showToast('应用失败: ' + e.message, 'error'); }
}

async function spPreviewFull() {
  try {
    const config = collectConfigFromUI();
    const result = await app.post('/api/system-prompt/config/preview', { config });
    const el = document.getElementById('sp-full-preview');
    if (!el) return;
    el.textContent = result.template || ('（预览生成失败）\n' + JSON.stringify(result, null, 2));
  } catch (e) { app.showToast('预览失败: ' + e.message, 'error'); }
}

async function spResetAll() {
  if (!await app.showConfirm('确定恢复全部默认配置吗？\n\n所有自定义内容将丢失。')) return;
  try {
    const r = await app.post('/api/system-prompt/config/reset');
    if (!r.ok) { app.showToast(r.error || '重置失败', 'error'); return; }
    await app.post('/api/system-prompt/reset');
    app.showToast('✅ 已恢复默认');
    loadSystemPrompt();
  } catch (e) { app.showToast('重置失败: ' + e.message, 'error'); }
}

async function spCheckSync() {
  try {
    const r = await app.get('/api/system-prompt/sync-status');
    app.showToast(r.is_synced ? '✅ 配置与运行模板已同步' : '⚠️ 配置已更改但未同步，点击「应用配置」生效', r.is_synced ? 'success' : 'warning');
  } catch (e) { app.showToast('检查同步状态失败', 'error'); }
}

// ════════════════════════════════════════════════════════════
//  辅助
// ════════════════════════════════════════════════════════════

function formatTokenNumber(n) {
  n = parseInt(n) || 0;
  if (n >= 10000) return (n / 1000).toFixed(0) + 'k';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'k';
  return n.toString();
}

function esc(s) {
  if (!s) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function showError(msg) {
  const el = document.getElementById('sp-full-preview');
  if (el) el.textContent = '❌ ' + msg + '\n\n请检查后端服务是否正常运行。';
}
