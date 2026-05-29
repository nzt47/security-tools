// ════════════════════════════════════════════════════════════
// 技能管理模块
// ════════════════════════════════════════════════════════════

async function loadSkills() {
  const list = document.getElementById('skills-list');
  list.innerHTML = '<div class="view-loading" style="padding:20px;text-align:center;color:#8b949e">加载中...</div>';
  try {
    const skills = await app.get('/api/skills');
    renderSkills(skills);
  } catch(e) {
    list.innerHTML = '<div class="view-empty">加载技能失败</div>';
  }
}

function renderSkills(skills) {
  const list = document.getElementById('skills-list');
  if (!skills || skills.length === 0) {
    list.innerHTML = '<div class="view-empty">暂无已配置的技能</div>';
    return;
  }
  list.innerHTML = skills.map(s => `
    <div class="view-card">
      <div class="view-card-header">
        <span class="view-card-title">${app.escapeHtml(s.name)}</span>
        <label class="toggle-switch">
          <input type="checkbox" ${s.enabled ? 'checked' : ''} onchange="toggleSkill('${s.id}')">
          <span class="slider"></span>
        </label>
      </div>
      <div class="view-card-sub">${app.escapeHtml(s.description)}</div>
      <div class="view-card-actions">
        <button onclick="showSkillParams('${s.id}')">⚙ 参数</button>
        <button onclick="deleteSkill('${s.id}')" style="color:var(--danger-color)">🗑 删除</button>
      </div>
    </div>
  `).join('');
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
  app.get('/api/skills').then(skills => {
    const skill = skills.find(s => s.id === id);
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
    // 尝试将数值字符串转换为数字
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
      <p style="font-size:13px;font-weight:600;color:#58a6ff;margin-bottom:8px">+ 添加技能</p>
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
