// ════════════════════════════════════════════════════════════
// 技能管理模块 — 分类展示已安装和可安装的技能
// ════════════════════════════════════════════════════════════

async function loadSkills() {
  const list = document.getElementById('skills-list');
  list.innerHTML = '<div class="view-loading" style="padding:20px;text-align:center;color:#8b949e">加载中...</div>';
  try {
    const data = await app.get('/api/skills');
    // data 格式: { installed: [...], available: [...] }
    renderSkills(data);
  } catch(e) {
    list.innerHTML = '<div class="view-empty">加载技能失败</div>';
  }
}

function renderSkills(data) {
  const list = document.getElementById('skills-list');

  // 兼容旧 API 格式（直接返回数组）和新格式（{installed, available}）
  if (Array.isArray(data)) {
    data = { installed: data, available: [] };
  }

  const installed = data && data.installed ? data.installed : [];
  const available = data && data.available ? data.available : [];

  const parts = [];

  // ── 已安装技能（可折叠） ──
  const collapsed = localStorage.getItem('skills_installed_collapsed') === 'true';
  parts.push('<div class="skills-section">');
  parts.push('<div class="skills-section-header skills-toggle-header" onclick="toggleInstalledSkills()">');
  parts.push('<span class="skills-toggle-icon">' + (collapsed ? '▶' : '▼') + '</span>');
  parts.push('<span class="skills-section-title">📦 已安装技能</span>');
  parts.push('<span class="skills-section-count">' + installed.length + '</span>');
  parts.push('</div>');
  parts.push('<div class="skills-body" id="skills-installed-body"' + (collapsed ? ' style="display:none"' : '') + '>');

  if (installed.length === 0) {
    parts.push('<div class="view-empty">暂无已配置的技能</div>');
  } else {
    for (const s of installed) {
      parts.push(renderInstalledSkillCard(s));
    }
  }
  parts.push('</div>');
  parts.push('</div>');

  // ── 可安装技能 ──
  const installable = available.filter(a => !a.installed);
  parts.push('<div class="skills-section" style="margin-top:16px">');
  parts.push('<div class="skills-section-header">');
  parts.push('<span class="skills-section-title">📥 可安装技能</span>');
  parts.push(`<span class="skills-section-count">${installable.length}</span>`);
  parts.push('</div>');

  if (installable.length === 0) {
    parts.push('<div class="view-empty">所有内置技能已安装</div>');
  } else {
    for (const s of installable) {
      parts.push(renderAvailableSkillCard(s));
    }
  }
  parts.push('</div>');

  list.innerHTML = parts.join('\n');
}

function renderInstalledSkillCard(s) {
  // 内置技能不可删除
  const isBuiltin = s.source !== 'extension_store';
  return `<div class="view-card">
    <div class="view-card-header">
      <span class="view-card-title">${app.escapeHtml(s.name)}${isBuiltin ? ' <span style="font-size:10px;color:#484f58;font-weight:400">·内置</span>' : ''}</span>
      <label class="toggle-switch">
        <input type="checkbox" ${s.enabled ? 'checked' : ''} onchange="toggleSkill('${s.id}')">
        <span class="toggle-slider"></span>
      </label>
    </div>
    <div class="view-card-sub">${app.escapeHtml(s.description)}</div>
    <div class="view-card-actions">
      <button onclick="showSkillParams('${s.id}')">⚙ 参数</button>
      ${isBuiltin ? '' : '<button onclick="deleteSkill(\'' + s.id + '\')" style="color:var(--danger-color)">🗑 删除</button>'}
    </div>
  </div>`;
}

function renderAvailableSkillCard(s) {
  return `<div class="view-card" style="opacity:0.85">
    <div class="view-card-header">
      <span class="view-card-title">${app.escapeHtml(s.name)}</span>
      <button class="btn-sm primary" onclick="installBuiltinSkill('${s.id}')" style="font-size:11px;padding:3px 12px">+ 安装</button>
    </div>
    <div class="view-card-sub">${app.escapeHtml(s.description)}</div>
    <div style="font-size:11px;color:#484f58;margin-top:4px">${s.builtin ? '内置' : ''}</div>
  </div>`;
}

async function installBuiltinSkill(id) {
  try {
    const r = await app.post('/api/skills/add', { id, name: id, description: '', enabled: true, params: {} });
    if (r.ok) {
      app.showToast('技能已安装');
      loadSkills();
    } else {
      app.showToast(r.error || '安装失败', 'error');
      loadSkills();
    }
  } catch(e) {
    app.showToast('安装失败', 'error');
  }
}

async function toggleSkill(id) {
  try {
    const result = await app.post('/api/skills/toggle', { id });
    if (result.ok) {
      app.showToast(result.enabled ? '已启用' : '已禁用');
    }
  } catch(e) {
    app.showToast('操作失败', 'error');
    loadSkills();
  }
}

function showSkillParams(id) {
  const overlay = document.createElement('div');
  overlay.className = 'confirm-overlay';
  overlay.innerHTML = `
    <div class="confirm-box" style="max-width:400px">
      <p style="font-size:13px;font-weight:600;color:#58a6ff;margin-bottom:8px">⚙ 技能参数配置</p>
      <div id="skill-params-form" style="font-size:13px;color:#8b949e">加载参数...</div>
      <div class="sidebar-confirm-actions" style="margin-top:12px">
        <button class="btn-sm" onclick="this.closest('.confirm-overlay').remove()">取消</button>
        <button class="btn-sm primary" onclick="saveSkillParams('${id}')">保存</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
  // 从已安装列表获取参数
  app.get('/api/skills').then(data => {
    const installed = data && data.installed ? data.installed : [];
    const skill = installed.find(s => s.id === id);
    if (!skill) return;
    const form = document.getElementById('skill-params-form');
    const params = skill.params || {};
    form.innerHTML = Object.entries(params).map(([key, val]) => `
      <div style="margin-bottom:8px">
        <label style="display:block;font-size:12px;color:#8b949e;margin-bottom:2px">${key}</label>
        <input class="view-search" id="sp-${key}" value="${val}" style="margin-bottom:0">
      </div>
    `).join('');
  });
}

async function saveSkillParams(id) {
  const params = {};
  document.querySelectorAll('#skill-params-form input').forEach(inp => {
    const key = inp.id.replace('sp-', '');
    const val = inp.value;
    params[key] = isNaN(val) || val === '' ? val : Number(val);
  });
  try {
    const r = await app.post('/api/skills/params', { id, params });
    if (r.ok) {
      app.showToast('参数已更新');
      document.querySelector('.confirm-overlay').remove();
      loadSkills();
    }
  } catch(e) {
    app.showToast('保存失败', 'error');
  }
}

function showAddSkill() {
  const overlay = document.createElement('div');
  overlay.className = 'confirm-overlay';
  overlay.innerHTML = `
    <div class="confirm-box" style="max-width:400px">
      <p style="font-size:13px;font-weight:600;color:#58a6ff;margin-bottom:8px">+ 添加自定义技能</p>
      <div style="margin-bottom:8px">
        <label style="display:block;font-size:12px;color:#8b949e;margin-bottom:2px">技能名称</label>
        <input class="view-search" id="new-skill-name" placeholder="如: 多语言翻译" style="margin-bottom:0">
      </div>
      <div style="margin-bottom:8px">
        <label style="display:block;font-size:12px;color:#8b949e;margin-bottom:2px">技能ID</label>
        <input class="view-search" id="new-skill-id" placeholder="如: multi_lang_translate" style="margin-bottom:0">
      </div>
      <div style="margin-bottom:8px">
        <label style="display:block;font-size:12px;color:#8b949e;margin-bottom:2px">描述</label>
        <input class="view-search" id="new-skill-desc" placeholder="简短描述技能功能" style="margin-bottom:0">
      </div>
      <div class="sidebar-confirm-actions" style="margin-top:12px">
        <button class="btn-sm" onclick="this.closest('.confirm-overlay').remove()">取消</button>
        <button class="btn-sm primary" onclick="confirmAddSkill()">添加</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
}

async function confirmAddSkill() {
  const name = document.getElementById('new-skill-name').value.trim();
  const id = document.getElementById('new-skill-id').value.trim();
  const desc = document.getElementById('new-skill-desc').value.trim();
  if (!name || !id) {
    app.showToast('请填写名称和ID', 'error');
    return;
  }
  try {
    const r = await app.post('/api/skills/add', { id, name, description: desc, enabled: true, params: {} });
    if (r.ok) {
      app.showToast('技能已添加');
      document.querySelector('.confirm-overlay').remove();
      loadSkills();
    } else {
      app.showToast(r.error || '添加失败', 'error');
    }
  } catch(e) {
    app.showToast('添加失败', 'error');
  }
}

async function deleteSkill(id) {
  const confirmed = await app.showConfirm('确定要删除这个技能吗？');
  if (!confirmed) return;
  try {
    const r = await app.post('/api/skills/delete', { id });
    if (r.ok) {
      app.showToast('已删除');
      loadSkills();
    }
  } catch(e) {
    app.showToast('删除失败', 'error');
  }
}

// ── 折叠/展开已安装技能 ──
function toggleInstalledSkills() {
  const body = document.getElementById('skills-installed-body');
  const icon = document.querySelector('.skills-toggle-header .skills-toggle-icon');
  if (!body) return;
  const isHidden = body.style.display === 'none';
  if (isHidden) {
    body.style.display = '';
    if (icon) icon.textContent = '▼';
    localStorage.setItem('skills_installed_collapsed', 'false');
  } else {
    body.style.display = 'none';
    if (icon) icon.textContent = '▶';
    localStorage.setItem('skills_installed_collapsed', 'true');
  }
}
