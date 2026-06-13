// ════════════════════════════════════════════════════════════
// 工具集成模块
// ════════════════════════════════════════════════════════════

async function loadTools() {
  const list = document.getElementById('tools-list');
  const count = document.getElementById('tools-count');
  list.innerHTML = '<div class="view-loading" style="padding:20px;text-align:center;color:#8b949e">加载中...</div>';
  try {
    const [tools, procData] = await Promise.all([
      app.get('/api/tools/config'),
      app.get('/api/process/whitelist'),
    ]);
    count.textContent = `已注册 ${tools.length} 个工具`;
    renderProcessWhitelist(procData);
    renderTools(tools);
  } catch(e) {
    list.innerHTML = '<div class="view-empty">加载工具列表失败</div>';
    count.textContent = '';
    const catsEl = document.getElementById('process-whitelist-categories');
    if (catsEl) catsEl.innerHTML = '<div style="color:#f85149;font-size:11px">加载失败</div>';
    const sumEl = document.getElementById('process-whitelist-summary');
    if (sumEl) sumEl.textContent = '加载失败';
  }
}

// ═══ 程序白名单分类 ═══
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
    catsEl.innerHTML = '<div style="color:#8b949e;font-size:11px">暂无白名单</div>';
    summaryEl.textContent = '0 个程序';
    return;
  }
  const { all, default: defList, custom } = data;
  const customSet = new Set(custom || []);
  summaryEl.textContent = `${all.length} 个程序（内置 ${defList.length} + 自定义 ${custom.length}）`;

  // 按分类渲染
  let html = '';
  for (const [catName, catProgs] of Object.entries(PROC_CATEGORIES)) {
    const matched = catProgs.filter(p =>
      all.includes(p) || all.includes(p.replace('.exe','').replace('.cmd','') + '.exe')
    );
    if (matched.length === 0) continue;
    html += `<div class="proc-category">
      <div class="proc-cat-title">${catName}</div>
      <div class="proc-cat-items">`;
    matched.forEach(p => {
      const isCustom = customSet.has(p);
      html += `<span class="proc-tag${isCustom ? ' custom' : ''}" title="${isCustom ? '自定义' : '内置'}">` +
        app.escapeHtml(p) +
        (isCustom ? `<span class="proc-tag-del" data-prog="${app.escapeHtml(p)}" onclick="removeWhitelistProg('${app.escapeHtml(p)}')">×</span>` : '') +
        `</span>`;
    });
    html += `</div></div>`;
  }

  // 未分类的（自定义条目）
  const allClassified = new Set(Object.values(PROC_CATEGORIES).flat());
  const unclassified = all.filter(p => !allClassified.has(p));
  if (unclassified.length > 0) {
    html += `<div class="proc-category">
      <div class="proc-cat-title">其他</div>
      <div class="proc-cat-items">`;
    unclassified.forEach(p => {
      const isCustom = customSet.has(p);
      html += `<span class="proc-tag${isCustom ? ' custom' : ''}" title="${isCustom ? '自定义' : '内置'}">` +
        app.escapeHtml(p) +
        (isCustom ? `<span class="proc-tag-del" data-prog="${app.escapeHtml(p)}" onclick="removeWhitelistProg('${app.escapeHtml(p)}')">×</span>` : '') +
        `</span>`;
    });
    html += `</div></div>`;
  }

  // 添加按钮区域
  html += `<div class="proc-add-row">
    <input type="text" class="proc-add-input" id="proc-add-input"
      placeholder="输入程序名，如 chrome.exe" maxlength="60"
      onkeydown="if(event.key==='Enter') addWhitelistProg()">
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
      // 重新加载白名单
      const procData = await app.get('/api/process/whitelist');
      renderProcessWhitelist(procData);
    } else {
      app.showToast(r.error || '添加失败', 'error');
    }
  } catch(e) {
    app.showToast('添加失败', 'error');
  }
}

async function removeWhitelistProg(program) {
  try {
    const r = await app.post('/api/process/whitelist/remove', { program });
    if (r.ok) {
      app.showToast(`「${program}」已移除`);
      const procData = await app.get('/api/process/whitelist');
      renderProcessWhitelist(procData);
    } else {
      app.showToast(r.error || '移除失败', 'error');
    }
  } catch(e) {
    app.showToast('移除失败', 'error');
  }
}

function renderTools(tools) {
  const list = document.getElementById('tools-list');
  if (!tools || tools.length === 0) {
    list.innerHTML = '<div class="view-empty">暂无已注册的工具</div>';
    return;
  }
  list.innerHTML = tools.map(t => `
    <div class="view-card">
      <div class="view-card-header">
        <span class="view-card-title">🔧 ${app.escapeHtml(t.name)}</span>
        <label class="toggle-switch">
          <input type="checkbox" ${t.enabled !== false ? 'checked' : ''} onchange="toggleTool('${t.name}')">
          <span class="toggle-slider"></span>
        </label>
      </div>
      <div class="view-card-sub">${app.escapeHtml(t.description)}</div>
      <div style="font-size:11px;color:#484f58;margin-top:4px">
        调用 ${t.call_count || 0} 次${t.last_used ? ' | 上次: ' + t.last_used : ''}
      </div>
    </div>
  `).join('');
}

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
  } catch(e) {
    app.showToast('操作失败', 'error');
  }
}
