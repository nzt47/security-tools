// ════════════════════════════════════════════════════════════
// 人格配置模块
// ════════════════════════════════════════════════════════════

async function loadPersonality() {
  const isDetail = document.getElementById('detail-personality')?.classList.contains('active');
  const presetsId = isDetail ? 'detail-personality-presets' : 'personality-presets';
  const slidersId = isDetail ? 'detail-personality-sliders' : 'personality-sliders';

  try {
    const data = await apiGet('/api/personality');
    renderPersonalityPresets(data, presetsId, isDetail);
    renderPersonalitySliders(data, slidersId, isDetail);
  } catch(e) {
    const el = document.getElementById(slidersId);
    if (el) el.innerHTML = '<div class="sidebar-empty">加载人格配置失败</div>';
  }
}

function renderPersonalityPresets(data, containerId, isDetail) {
  const el = document.getElementById(containerId);
  if (!el) return;
  const profiles = data.profiles || {};
  const current = data.current_profile;
  let html = '';
  for (const [key, profile] of Object.entries(profiles)) {
    const active = key === current;
    const border = active ? 'border-color:#58a6ff' : '';
    html += `<div class="sidebar-card" style="cursor:pointer;${border}${isDetail ? ';padding:14px 18px' : ''}" onclick="applyProfile('${key}')">
      <div class="sidebar-card-header">
        <span class="sidebar-card-title${isDetail ? ';font-size:15px' : ''}">${active ? '▸ ' : ''}${escapeHtml(profile.name)}</span>
        ${active ? '<span class="badge info">当前</span>' : ''}
      </div>
      <div class="sidebar-card-sub${isDetail ? ';font-size:13px' : ''}">${escapeHtml(profile.description)}</div>
    </div>`;
  }
  el.innerHTML = html;
}

function renderPersonalitySliders(data, containerId, isDetail) {
  const el = document.getElementById(containerId);
  if (!el) return;
  const params = data.custom_params || {};
  const dimensions = data.dimensions || [];
  let html = '';
  for (const dim of dimensions) {
    const val = Math.round((params[dim.key] || 0.5) * 100);
    html += `<div class="slider-group">
      <label>
        <span>${dim.left}</span>
        <span>${dim.label}: ${val}%</span>
        <span>${dim.right}</span>
      </label>
      <input type="range" min="0" max="100" value="${val}" data-key="${dim.key}" oninput="updateSliderLabel(this)" style="width:100%">
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
  document.querySelectorAll('#personality-sliders input[type="range"], #detail-personality-sliders input[type="range"]').forEach(sl => {
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
