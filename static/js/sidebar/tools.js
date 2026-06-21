// ════════════════════════════════════════════════════════════
// 工具集成模块 — 按分类展示 + 关键词管理
// ════════════════════════════════════════════════════════════

async function loadTools() {
  const list = document.getElementById('tools-list');
  const count = document.getElementById('tools-count');
  list.innerHTML = '<div class="view-loading">加载中...</div>';

  try {
    const [tools, categories, procData] = await Promise.all([
      app.get('/api/tools/config'),
      app.get('/api/tools/categories'),
      app.get('/api/process/whitelist'),
    ]);
    count.textContent = `已注册 ${tools.length} 个工具 · ${categories.categories.length} 个分类`;
    renderProcessWhitelist(procData);
    renderCategorizedTools(tools, categories);
  } catch (e) {
    list.innerHTML = '<div class="view-empty">加载工具列表失败</div>';
    count.textContent = '';
  }
}

// ════════════════════════════════════════════════════════════
//  按分类渲染工具
// ════════════════════════════════════════════════════════════

function renderCategorizedTools(tools, categories) {
  const container = document.getElementById('tools-list');
  if (!tools || !categories) {
    container.innerHTML = '<div class="view-empty">暂无数据</div>';
    return;
  }

  // 建立工具名 → 详情映射
  const toolMap = {};
  for (const t of tools) {
    toolMap[t.name] = t;
  }

  // 建立工具名 → 分类映射
  const toolCategoryMap = {};
  for (const cat of categories.categories) {
    for (const tName of cat.tools) {
      toolCategoryMap[tName] = cat.key;
    }
  }

  // 所有已注册工具（不在分类中的归为"其他"）
  const categorizedTools = {};
  for (const cat of categories.categories) {
    categorizedTools[cat.key] = { ...cat, tools: [] };
  }
  categorizedTools['_uncategorized'] = {
    key: '_uncategorized', label: '其他', icon: '🔧',
    description: '未分类的工具', always: false, tools: [],
  };

  for (const t of tools) {
    const catKey = toolCategoryMap[t.name] || '_uncategorized';
    if (categorizedTools[catKey]) {
      categorizedTools[catKey].tools.push(t);
    }
  }

  // 渲染
  let html = '';
  const keywords = categories.keywords || {};

  for (const [catKey, cat] of Object.entries(categorizedTools)) {
    if (cat.tools.length === 0) continue;
    const isAlways = cat.always;

    html += `<div class="network-section" style="margin-bottom:8px">
      <div class="network-section-header skills-toggle-header" onclick="tcToggleSection(this)">
        <span class="section-icon">${cat.icon}</span>
        <span class="section-title">${esc(cat.label)}</span>
        <span class="skills-section-count">${cat.tools.length}</span>
        ${isAlways ? '<span class="badge info" style="font-size:8px;margin-left:4px">始终发送</span>' : ''}
        <span class="skills-toggle-icon" style="margin-left:auto;font-size:10px;color:var(--text-muted)">▼</span>
        <span style="font-size:10px;color:var(--text-muted);margin-left:8px">
          ${cat.tools.filter(t => toolMap[t.name]?.enabled !== false).length} 已启用
        </span>
      </div>
      <div class="network-section-body skills-body" style="display:block">
        <p style="font-size:10px;color:var(--text-secondary);margin:0 0 6px">${esc(cat.description)}</p>
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:4px">
          ${cat.tools.map(t => {
            const info = toolMap[t.name] || {};
            const enabled = info.enabled !== false;
            return `<div class="view-card" style="margin:0;padding:6px 8px">
              <div class="view-card-header" style="margin-bottom:2px">
                <span class="view-card-title" style="font-size:11px">${esc(t.name)}</span>
                <label class="toggle-switch small" onclick="event.stopPropagation()">
                  <input type="checkbox" ${enabled ? 'checked' : ''} onchange="toggleTool('${esc(t.name)}')">
                  <span class="toggle-slider"></span>
                </label>
              </div>
              <div class="view-card-sub" style="font-size:10px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">${esc(t.description || '')}</div>
              <div style="font-size:9px;color:var(--text-muted);margin-top:2px">
                ${info.call_count || 0} 次调用${info.last_used ? ' · 上次 ' + (info.last_used || '') : ''}
              </div>
            </div>`;
          }).join('')}
        </div>
      </div>
    </div>`;
  }

  // ── 关键词管理 ──
  html += renderKeywordManager(categories.keywords);

  container.innerHTML = html;
}

// ════════════════════════════════════════════════════════════
//  关键词管理
// ════════════════════════════════════════════════════════════

function renderKeywordManager(keywords) {
  if (!keywords) return '';
  let html = '<div class="network-section" style="margin-top:12px">';
  html += `<div class="network-section-header skills-toggle-header" onclick="tcToggleSection(this)">
    <span class="section-icon">🔑</span>
    <span class="section-title">触发关键词管理</span>
    <span style="font-size:10px;color:var(--text-muted);margin-left:8px">修改后需重新启动对话生效</span>
    <span class="skills-toggle-icon" style="margin-left:auto;font-size:10px;color:var(--text-muted)">▼</span>
  </div>`;
  html += '<div class="network-section-body skills-body" style="display:block">';
  html += '<p style="font-size:10px;color:var(--text-secondary);margin:0 0 8px">' +
    '用户输入包含以下关键词时，自动发送对应分类的工具定义。可增删改，修改后实时生效。</p>';

  for (const [catKey, kwList] of Object.entries(keywords)) {
    if (!kwList || kwList.length === 0) continue;
    const catLabel = getCategoryLabel(catKey);

    html += `<div style="margin-bottom:6px;padding:6px 8px;background:var(--bg-primary);border-radius:4px;border:1px solid var(--border-color)">
      <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">
        <strong style="font-size:11px;color:var(--text-primary)">${esc(catLabel)}</strong>
        <span style="font-size:9px;color:var(--text-muted)">${kwList.length} 个关键词</span>
        <button class="btn-sm" style="font-size:9px;padding:1px 8px;margin-left:auto" onclick="kwShowAdd('${catKey}')">+ 添加</button>
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:4px">`;
    for (const kw of kwList) {
      html += `<span class="kw-tag" data-category="${esc(catKey)}" data-keyword="${esc(kw)}">
        <span class="kw-text">${esc(kw)}</span>
        <span class="kw-edit" onclick="kwEdit(this)" title="修改">✏</span>
        <span class="kw-del" onclick="kwDelete('${esc(catKey)}','${esc(kw)}')" title="删除">×</span>
      </span>`;
    }
    html += '</div></div>';
  }

  html += '<div style="margin-top:6px">';
  html += '<button class="btn-sm" onclick="kwReset()" style="font-size:10px;color:var(--danger)">↺ 恢复默认关键词</button>';
  html += '</div></div></div>';
  return html;
}

function getCategoryLabel(catKey) {
  const labels = {
    'core': '⚙ 核心', 'web': '🌐 网络搜索', 'file': '📁 文件',
    'code': '💻 代码Shell', 'system': '🖥 系统进程', 'extension': '🧩 扩展',
    'pdf': '📄 PDF', 'software': '📦 软件', 'async': '⏳ 异步任务',
    'schedule': '⏰ 定时任务', 'v2': '⚡ V2 特性',
  };
  return labels[catKey] || catKey;
}

// ════════════════════════════════════════════════════════════
//  关键词交互
// ════════════════════════════════════════════════════════════

function kwShowAdd(category) {
  const kw = prompt('输入新触发关键词（用户输入包含此词时将激活对应分类的工具）：');
  if (!kw || !kw.trim()) return;
  kwAdd(category, kw.trim());
}

async function kwAdd(category, keyword) {
  try {
    const r = await app.post('/api/tools/keywords', { category, keyword });
    if (r.ok) {
      app.showToast(`关键词「${keyword}」已添加`);
      loadTools();
    } else {
      app.showToast(r.error || '添加失败', 'error');
    }
  } catch (e) {
    app.showToast('添加失败', 'error');
  }
}

async function kwDelete(category, keyword) {
  if (!confirm(`确定删除关键词「${keyword}」？`)) return;
  try {
    const r = await app.fetch('/api/tools/keywords', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ category, keyword }),
    });
    const data = await r.json();
    if (data.ok) {
      app.showToast(`已删除「${keyword}」`);
      loadTools();
    } else {
      app.showToast(data.error || '删除失败', 'error');
    }
  } catch (e) {
    app.showToast('删除失败', 'error');
  }
}

function kwEdit(el) {
  const tag = el.closest('.kw-tag');
  if (!tag) return;
  const category = tag.dataset.category;
  const oldKw = tag.dataset.keyword;
  const newKw = prompt('修改关键词：', oldKw);
  if (!newKw || !newKw.trim() || newKw.trim() === oldKw) return;
  kwUpdate(category, oldKw, newKw.trim());
}

async function kwUpdate(category, oldKeyword, newKeyword) {
  try {
    const r = await app.post('/api/tools/keywords/update', {
      category, old_keyword: oldKeyword, new_keyword: newKeyword,
    });
    if (r.ok) {
      app.showToast(`已更新为「${newKeyword}」`);
      loadTools();
    } else {
      app.showToast(r.error || '更新失败', 'error');
    }
  } catch (e) {
    app.showToast('更新失败', 'error');
  }
}

async function kwReset() {
  if (!confirm('确定恢复默认关键词吗？自定义关键词将丢失。')) return;
  try {
    const r = await app.post('/api/tools/keywords/reset');
    if (r.ok) {
      app.showToast('已恢复默认关键词');
      loadTools();
    } else {
      app.showToast('重置失败', 'error');
    }
  } catch (e) {
    app.showToast('重置失败', 'error');
  }
}

// ════════════════════════════════════════════════════════════
//  工具交互
// ════════════════════════════════════════════════════════════

async function toggleTool(name) {
  try {
    const tools = await app.get('/api/tools/config');
    const tool = tools.find(t => t.name === name);
    if (!tool) return;
    const r = await app.post('/api/tools/toggle', { name, enabled: !tool.enabled });
    if (r.ok) {
      app.showToast(r.enabled ? `「${name}」已授权` : `「${name}」已禁用`);
      loadTools();
    }
  } catch (e) {
    app.showToast('操作失败', 'error');
  }
}

// ════════════════════════════════════════════════════════════
//  Section 折叠
// ════════════════════════════════════════════════════════════

function tcToggleSection(headerEl) {
  const body = headerEl.parentElement?.querySelector('.skills-body');
  if (!body) return;
  const isOpen = body.style.display !== 'none';
  body.style.display = isOpen ? 'none' : 'block';
  const icon = headerEl.querySelector('.skills-toggle-icon');
  if (icon) icon.textContent = isOpen ? '▶' : '▼';
}

// ════════════════════════════════════════════════════════════
//  进程白名单（继承原有逻辑）
// ════════════════════════════════════════════════════════════

const PROC_CATEGORIES = {
  '系统工具': ['notepad.exe', 'calc.exe', 'mspaint.exe', 'write.exe', 'explorer.exe', 'cmd.exe'],
  '脚本引擎': ['python.exe', 'python3.exe', 'pip.exe'],
  'Web 工具': ['node.exe', 'npm.cmd', 'npx.cmd'],
  '命令行':   ['git.exe', 'curl.exe', 'wget.exe'],
};

function renderProcessWhitelist(data) {
  const catsEl = document.getElementById('process-whitelist-categories');
  const summaryEl = document.getElementById('process-whitelist-summary');
  if (!data || !data.all || data.all.length === 0) {
    if (catsEl) catsEl.innerHTML = '<div style="color:#8b949e;font-size:11px">暂无白名单</div>';
    if (summaryEl) summaryEl.textContent = '0 个程序';
    return;
  }
  const { all, default: defList, custom } = data;
  const customSet = new Set(custom || []);
  if (summaryEl) summaryEl.textContent = `${all.length} 个程序（内置 ${defList.length} + 自定义 ${custom.length}）`;
  if (!catsEl) return;
  let html = '';
  for (const [catName, catProgs] of Object.entries(PROC_CATEGORIES)) {
    const matched = catProgs.filter(p => all.includes(p) || all.includes(p.replace('.exe','').replace('.cmd','') + '.exe'));
    if (matched.length === 0) continue;
    html += `<div class="proc-category"><div class="proc-cat-title">${catName}</div><div class="proc-cat-items">`;
    matched.forEach(p => {
      const isCustom = customSet.has(p);
      html += `<span class="proc-tag${isCustom ? ' custom' : ''}" title="${isCustom ? '自定义' : '内置'}">${esc(p)}` +
        (isCustom ? `<span class="proc-tag-del" data-prog="${esc(p)}" onclick="removeWhitelistProg('${esc(p)}')">×</span>` : '') + `</span>`;
    });
    html += `</div></div>`;
  }
  const allClassified = new Set(Object.values(PROC_CATEGORIES).flat());
  const unclassified = all.filter(p => !allClassified.has(p));
  if (unclassified.length > 0) {
    html += `<div class="proc-category"><div class="proc-cat-title">其他</div><div class="proc-cat-items">`;
    unclassified.forEach(p => {
      const isCustom = customSet.has(p);
      html += `<span class="proc-tag${isCustom ? ' custom' : ''}" title="${isCustom ? '自定义' : '内置'}">${esc(p)}` +
        (isCustom ? `<span class="proc-tag-del" data-prog="${esc(p)}" onclick="removeWhitelistProg('${esc(p)}')">×</span>` : '') + `</span>`;
    });
    html += `</div></div>`;
  }
  html += `<div class="proc-add-row">
    <input type="text" class="proc-add-input" id="proc-add-input" placeholder="输入程序名，如 chrome.exe" maxlength="60" onkeydown="if(event.key==='Enter') addWhitelistProg()">
    <button class="btn-sm primary" onclick="addWhitelistProg()">+ 添加</button>
  </div>`;
  catsEl.innerHTML = html;
}

async function addWhitelistProg() {
  const input = document.getElementById('proc-add-input');
  if (!input) return;
  const program = input.value.trim();
  if (!program) { app.showToast('请输入程序名', 'error'); return; }
  try {
    const r = await app.post('/api/process/whitelist/add', { program });
    if (r.ok) {
      app.showToast(`「${program}」已添加`);
      input.value = '';
      const procData = await app.get('/api/process/whitelist');
      renderProcessWhitelist(procData);
    } else { app.showToast(r.error || '添加失败', 'error'); }
  } catch(e) { app.showToast('添加失败', 'error'); }
}

async function removeWhitelistProg(program) {
  try {
    const r = await app.post('/api/process/whitelist/remove', { program });
    if (r.ok) {
      app.showToast(`「${program}」已移除`);
      const procData = await app.get('/api/process/whitelist');
      renderProcessWhitelist(procData);
    } else { app.showToast(r.error || '移除失败', 'error'); }
  } catch(e) { app.showToast('移除失败', 'error'); }
}

// ════════════════════════════════════════════════════════════
//  辅助
// ════════════════════════════════════════════════════════════

function esc(s) {
  if (!s) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
