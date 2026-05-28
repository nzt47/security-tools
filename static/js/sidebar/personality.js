// ════════════════════════════════════════════════════════════
// 人格配置模块
// ════════════════════════════════════════════════════════════

async function loadPersonality() {
  try {
    const data = await apiGet('/api/personality');
    renderPresets(data);
    renderSliders(data);
  } catch(e) {
    document.getElementById('personality-sliders').innerHTML = '<div class="sidebar-empty">加载人格配置失败</div>';
  }
}

function renderPresets(data) {
  const el = document.getElementById('personality-presets');
  const profiles = data.profiles || {};
  const current = data.current_profile;
  let html = '<div style="font-size:12px;color:#8b949e;margin-bottom:6px">★ 预设人格</div>';
  for (const [key, profile] of Object.entries(profiles)) {
    const active = key === current;
    html += `<div class="sidebar-card" style="cursor:pointer;${active ? 'border-color:#58a6ff' : ''}" onclick="applyProfile('${key}')">
      <div class="sidebar-card-header">
        <span class="sidebar-card-title">${active ? '▸ ' : ''}${escapeHtml(profile.name)}</span>
        ${active ? '<span class="badge info">当前</span>' : ''}
      </div>
      <div class="sidebar-card-sub">${escapeHtml(profile.description)}</div>
    </div>`;
  }
  el.innerHTML = html;
}

function renderSliders(data) {
  const el = document.getElementById('personality-sliders');
  const params = data.custom_params || {};
  const dimensions = data.dimensions || [];
  let html = '<div style="font-size:12px;color:#8b949e;margin:10px 0 6px">── 详细参数 ──</div>';
  for (const dim of dimensions) {
    const val = Math.round((params[dim.key] || 0.5) * 100);
    html += `<div class="slider-group">
      <label>
        <span>${dim.left}</span>
        <span>${dim.label}: ${val}%</span>
        <span>${dim.right}</span>
      </label>
      <input type="range" min="0" max="100" value="${val}" data-key="${dim.key}" oninput="updateSliderLabel(this)">
    </div>`;
  }
  el.innerHTML = html;
}

function updateSliderLabel(slider) {
  const label = slider.closest('.slider-group').querySelector('label span:nth-child(2)');
  const dimKey = slider.dataset.key;
  label.textContent = `${dimKey}: ${slider.value}%`;
}

async function applyProfile(profileKey) {
  try {
    const r = await apiPost('/api/personality/profile', { profile: profileKey });
    if (r.ok) {
      showToast(`已切换预设`);
      loadPersonality();
    } else {
      showToast(r.error || '切换失败', 'error');
    }
  } catch(e) {
    showToast('切换失败', 'error');
  }
}

async function savePersonality() {
  const params = {};
  document.querySelectorAll('#personality-sliders input[type="range"]').forEach(sl => {
    const key = sl.dataset.key;
    params[key] = parseInt(sl.value) / 100;
  });
  try {
    const r = await apiPost('/api/personality/params', { params });
    if (r.ok) {
      showToast('人格配置已保存');
      loadPersonality();
    }
  } catch(e) {
    showToast('保存失败', 'error');
  }
}

async function resetPersonality() {
  const confirmed = await showConfirm('确定恢复默认人格配置吗？');
  if (!confirmed) return;
  try {
    const r = await apiPost('/api/personality/reset');
    if (r.ok) {
      showToast('已恢复默认配置');
      loadPersonality();
    }
  } catch(e) {
    showToast('重置失败', 'error');
  }
}
