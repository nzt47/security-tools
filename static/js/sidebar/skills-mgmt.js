// ════════════════════════════════════════════════════════════
// 技能管理 v2 + 工作流学习 — 前端逻辑
// 调用后端 /api/skills-mgmt/* 与 /api/workflow-learning/*
// 遵循: 防抖搜索 / 防连点 / 错误显性化 / trackEvent 埋点
// ════════════════════════════════════════════════════════════

// ─── 状态 ───
const skmgmtState = {
  skills: [],
  selectedSkillId: null,
  searchReqId: 0,           // Request ID 防竞态
  searchAbort: null,        // AbortController 取消废弃请求
  listAbort: null,
  submitting: false,        // 防连点
  healthTimer: null,
  loaded: false,
};

// ─── trackEvent 埋点占位符（失败不影响主流程） ───
function skmgmtTrack(event, payload) {
  try { console.debug('[track]', event, payload); } catch (e) { /* 吞掉 */ }
}

// ─── 统一 API 请求（带错误码提取） ───
async function skmgmtFetch(path, opts = {}) {
  const t0 = performance.now();
  try {
    const res = await fetch(path, {
      method: opts.method || 'GET',
      headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
      body: opts.body ? JSON.stringify(opts.body) : undefined,
      signal: opts.signal,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      // 边界显性化：提取业务错误码
      const err = new Error(data.error || data.message || `HTTP ${res.status}`);
      err.code = data.code || `HTTP_${res.status}`;
      err.status = res.status;
      err.details = data.details;
      throw err;
    }
    return data;
  } catch (e) {
    if (e.name === 'AbortError') throw e;  // 取消不报错
    console.error(`[SkillsMgmt] ${path} 失败:`, e.code || '', e.message);
    throw e;
  } finally {
    const dur = (performance.now() - t0).toFixed(1);
    skmgmtTrack('api_call', { path, duration_ms: dur });
  }
}

// ════════════════════════════════════════════════════════════
//  Tab 切换
// ════════════════════════════════════════════════════════════
function skmgmtSwitchTab(tab) {
  document.querySelectorAll('.skmgmt-tabbtn').forEach(b => {
    b.classList.toggle('active', b.dataset.sktab === tab);
  });
  document.getElementById('sktab-basic').style.display = tab === 'basic' ? 'block' : 'none';
  const v2 = document.getElementById('sktab-v2');
  v2.style.display = tab === 'v2' ? 'flex' : 'none';
  const wf = document.getElementById('sktab-workflow');
  wf.style.display = tab === 'workflow' ? 'flex' : 'none';

  skmgmtTrack('tab_switch', { tab });

  // 懒加载
  if (tab === 'v2' && !skmgmtState.loaded) {
    skmgmtLoadCategories();
    skmgmtLoadSkills();
    skmgmtState.loaded = true;
  }
  if (tab === 'workflow') {
    loadWorkflows();
  }
}

// ════════════════════════════════════════════════════════════
//  技能列表（带 Request ID 防竞态 + AbortController 取消）
// ════════════════════════════════════════════════════════════
let _skmgmtSearchTimer = null;
function skmgmtDebouncedSearch() {
  clearTimeout(_skmgmtSearchTimer);
  _skmgmtSearchTimer = setTimeout(skmgmtLoadSkills, 300);  // 防抖 300ms
}

async function skmgmtLoadSkills() {
  const reqId = ++skmgmtState.searchReqId;
  // 取消上一个未完成请求（AbortController）
  if (skmgmtState.searchAbort) skmgmtState.searchAbort.abort();
  const ac = new AbortController();
  skmgmtState.searchAbort = ac;

  const listEl = document.getElementById('skmgmt-list');
  if (!listEl) return;
  listEl.innerHTML = '<div class="skmgmt-loading">加载中...</div>';

  const q = document.getElementById('skmgmt-search').value.trim();
  const cat = document.getElementById('skmgmt-cat-filter').value;
  const status = document.getElementById('skmgmt-status-filter').value;
  const enabledOnly = document.getElementById('skmgmt-enabled-only').checked;

  const params = new URLSearchParams();
  if (q) params.set('q', q);
  if (cat) params.append('categories', cat);
  if (status) params.append('statuses', status);
  if (enabledOnly) params.set('enabled_only', 'true');
  params.set('page_size', '100');

  try {
    const data = await skmgmtFetch(`/api/skills-mgmt/search?${params}`, { signal: ac.signal });
    // Request ID 校验：只有最新请求才更新 UI
    if (reqId !== skmgmtState.searchReqId) return;
    skmgmtState.skills = data.items || [];
    skmgmtRenderList(skmgmtState.skills);
    // 更新徽章
    const badge = document.getElementById('skmgmt-v2-count');
    if (skmgmtState.skills.length > 0) {
      badge.textContent = skmgmtState.skills.length;
      badge.style.display = 'inline-block';
    } else {
      badge.style.display = 'none';
    }
  } catch (e) {
    if (e.name === 'AbortError') return;
    listEl.innerHTML = `<div class="skmgmt-error">加载失败: ${e.code || ''} ${e.message}</div>`;
  }
}

function skmgmtRenderList(skills) {
  const listEl = document.getElementById('skmgmt-list');
  if (!skills.length) {
    listEl.innerHTML = '<div class="skmgmt-empty">暂无技能，点击「+ 新建技能」创建</div>';
    return;
  }
  listEl.innerHTML = skills.map(s => `
    <div class="skmgmt-item ${s.id === skmgmtState.selectedSkillId ? 'active' : ''}" onclick="skmgmtSelectSkill('${s.id}')">
      <div class="skmgmt-item-name">
        ${escapeHtml(s.name)}
        <span class="skmgmt-status ${s.status}">${skmgmtStatusText(s.status)}</span>
      </div>
      <div class="skmgmt-item-desc">${escapeHtml(s.description || '无描述')}</div>
      <div class="skmgmt-item-meta">
        <span>v${s.version || '0.1.0'}</span>
        <span>${s.category || ''}</span>
        ${s.enabled ? '' : '<span style="color:#ef5350">已禁用</span>'}
        ${s.metrics && s.metrics.usage_count ? `<span>用 ${s.metrics.usage_count} 次</span>` : ''}
      </div>
    </div>
  `).join('');
}

function skmgmtStatusText(s) {
  return { draft: '草稿', pending_review: '待审', approved: '已通过', published: '已发布', rejected: '已拒绝', deprecated: '已弃用', archived: '已归档' }[s] || s;
}

// ════════════════════════════════════════════════════════════
//  技能详情
// ════════════════════════════════════════════════════════════
async function skmgmtSelectSkill(id) {
  skmgmtState.selectedSkillId = id;
  document.querySelectorAll('.skmgmt-item').forEach(el => {
    el.classList.toggle('active', el.onclick && el.textContent.includes(id));
  });
  // 重新渲染列表高亮
  skmgmtRenderList(skmgmtState.skills);
  const detailEl = document.getElementById('skmgmt-detail');
  detailEl.innerHTML = '<div class="skmgmt-loading">加载详情...</div>';
  try {
    const data = await skmgmtFetch(`/api/skills-mgmt/${encodeURIComponent(id)}`);
    skmgmtRenderDetail(data.skill);
  } catch (e) {
    detailEl.innerHTML = `<div class="skmgmt-error">加载详情失败: ${e.code || ''} ${e.message}</div>`;
  }
}

function skmgmtRenderDetail(s) {
  const detailEl = document.getElementById('skmgmt-detail');
  if (!s) {
    detailEl.innerHTML = '<div class="skmgmt-empty">点击左侧技能查看详情</div>';
    return;
  }
  const m = s.metrics || {};
  const r = s.review_result;
  const versions = s.versions || [];
  const scoreClass = (v) => v >= 70 ? 'good' : v >= 40 ? 'warn' : 'bad';

  detailEl.innerHTML = `
    <div class="skmgmt-detail">
      <h3>
        ${escapeHtml(s.name)}
        <span class="skmgmt-status ${s.status}">${skmgmtStatusText(s.status)}</span>
        <span class="skmgmt-tag">v${s.version || '0.1.0'}</span>
        ${s.enabled ? '' : '<span class="skmgmt-status rejected">已禁用</span>'}
      </h3>
      <div class="skmgmt-detail-desc">${escapeHtml(s.description || '无描述')}</div>
      <div class="skmgmt-actions">
        <button class="btn-sm ${s.enabled ? '' : 'primary'}" onclick="skmgmtToggle('${s.id}', ${!s.enabled})" ${skmgmtState.submitting ? 'disabled' : ''}>${s.enabled ? '禁用' : '启用'}</button>
        <button class="btn-sm" onclick="skmgmtReview('${s.id}')" ${skmgmtState.submitting ? 'disabled' : ''}>审核</button>
        <button class="btn-sm" onclick="skmgmtBump('${s.id}')" ${skmgmtState.submitting ? 'disabled' : ''}>升级版本</button>
        <button class="btn-sm" onclick="skmgmtOptimize('${s.id}')">参数优化</button>
        <button class="btn-sm" onclick="skmgmtRecordExec('${s.id}', true)">记录成功</button>
        <button class="btn-sm" onclick="skmgmtRecordExec('${s.id}', false)">记录失败</button>
        <button class="btn-sm" onclick="skmgmtDelete('${s.id}')" style="color:#ef5350" ${skmgmtState.submitting ? 'disabled' : ''}>删除</button>
      </div>
      <div class="skmgmt-meta-grid">
        <div class="skmgmt-meta-item"><div class="skmgmt-meta-label">ID</div><div class="skmgmt-meta-value">${escapeHtml(s.id)}</div></div>
        <div class="skmgmt-meta-item"><div class="skmgmt-meta-label">分类</div><div class="skmgmt-meta-value">${escapeHtml(s.category || '-')}</div></div>
        <div class="skmgmt-meta-item"><div class="skmgmt-meta-label">来源</div><div class="skmgmt-meta-value">${escapeHtml(s.source || '-')}</div></div>
        <div class="skmgmt-meta-item"><div class="skmgmt-meta-label">作者</div><div class="skmgmt-meta-value">${escapeHtml(s.author || '-')}</div></div>
        <div class="skmgmt-meta-item"><div class="skmgmt-meta-label">内容类型</div><div class="skmgmt-meta-value">${escapeHtml(s.content_type || '-')}</div></div>
        <div class="skmgmt-meta-item"><div class="skmgmt-meta-label">更新时间</div><div class="skmgmt-meta-value">${escapeHtml(s.updated_at || '-')}</div></div>
      </div>
      ${(s.tags && s.tags.length) ? `<div class="skmgmt-section"><div class="skmgmt-section-title">标签</div>${s.tags.map(t => `<span class="skmgmt-tag">${escapeHtml(t)}</span> `).join('')}</div>` : ''}
      ${r ? `
      <div class="skmgmt-section">
        <div class="skmgmt-section-title">审核结果 (${r.status})</div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px">
          <span class="skmgmt-score ${scoreClass(r.score)}">综合 ${r.score}</span>
          <span class="skmgmt-score ${scoreClass(r.security_score)}">安全 ${r.security_score}</span>
          <span class="skmgmt-score ${scoreClass(r.quality_score)}">质量 ${r.quality_score}</span>
          <span class="skmgmt-score ${r.duplicate_score < 70 ? 'good' : 'bad'}">重复 ${r.duplicate_score}</span>
        </div>
        ${r.summary ? `<div style="font-size:12px;color:#8b949e;margin-bottom:6px">${escapeHtml(r.summary)}</div>` : ''}
        ${(r.findings && r.findings.length) ? r.findings.map(f => `<div class="skmgmt-finding ${f.severity}">[${f.code}] ${escapeHtml(f.message)}</div>`).join('') : '<div style="font-size:12px;color:#8b949e">无审核发现</div>'}
      </div>` : ''}
      <div class="skmgmt-section">
        <div class="skmgmt-section-title">内容</div>
        <div class="skmgmt-code">${escapeHtml(s.content || '(空)')}</div>
      </div>
      <div class="skmgmt-section">
        <div class="skmgmt-section-title">使用统计</div>
        <div class="skmgmt-meta-grid">
          <div class="skmgmt-meta-item"><div class="skmgmt-meta-label">总使用</div><div class="skmgmt-meta-value">${m.usage_count || 0}</div></div>
          <div class="skmgmt-meta-item"><div class="skmgmt-meta-label">成功</div><div class="skmgmt-meta-value" style="color:#66bb6a">${m.success_count || 0}</div></div>
          <div class="skmgmt-meta-item"><div class="skmgmt-meta-label">失败</div><div class="skmgmt-meta-value" style="color:#ef5350">${m.failure_count || 0}</div></div>
          <div class="skmgmt-meta-item"><div class="skmgmt-meta-label">平均延迟</div><div class="skmgmt-meta-value">${m.avg_latency_ms ? m.avg_latency_ms.toFixed(0) + 'ms' : '-'}</div></div>
        </div>
      </div>
      ${versions.length ? `
      <div class="skmgmt-section">
        <div class="skmgmt-section-title">版本历史 (${versions.length})</div>
        ${versions.map(v => `<div class="skmgmt-version ${v.version === s.version ? 'skmgmt-version-cur' : ''}"><span><b>v${v.version}</b> ${escapeHtml(v.changelog || '')}</span>${v.version !== s.version ? `<button class="btn-sm" onclick="skmgmtRollback('${s.id}','${v.version}')">回滚</button>` : '<span style="color:#4a9eff;font-size:11px">当前</span>'}</div>`).join('')}
      </div>` : ''}
    </div>
  `;
}

// ════════════════════════════════════════════════════════════
//  技能操作（防连点 + 乐观更新由后端权威刷新）
// ════════════════════════════════════════════════════════════
async function skmgmtToggle(id, enabled) {
  if (skmgmtState.submitting) return;
  skmgmtState.submitting = true;
  try {
    await skmgmtFetch(`/api/skills-mgmt/${id}/toggle`, { method: 'POST', body: { enabled } });
    skmgmtTrack('skill_toggle', { id, enabled });
    await skmgmtSelectSkill(id);
    await skmgmtLoadSkills();
  } catch (e) {
    alert(`操作失败: ${e.code || ''} ${e.message}`);
  } finally {
    skmgmtState.submitting = false;
  }
}

async function skmgmtReview(id) {
  if (skmgmtState.submitting) return;
  skmgmtState.submitting = true;
  try {
    await skmgmtFetch(`/api/skills-mgmt/${id}/review`, { method: 'POST', body: {} });
    skmgmtTrack('skill_review', { id });
    await skmgmtSelectSkill(id);
    await skmgmtLoadSkills();
  } catch (e) {
    alert(`审核失败: ${e.code || ''} ${e.message}`);
  } finally {
    skmgmtState.submitting = false;
  }
}

async function skmgmtDelete(id) {
  if (!confirm(`确认删除技能「${id}」？此操作不可撤销。`)) return;
  if (skmgmtState.submitting) return;
  skmgmtState.submitting = true;
  try {
    await skmgmtFetch(`/api/skills-mgmt/${id}`, { method: 'DELETE' });
    skmgmtTrack('skill_delete', { id });
    skmgmtState.selectedSkillId = null;
    document.getElementById('skmgmt-detail').innerHTML = '<div class="skmgmt-empty">点击左侧技能查看详情</div>';
    await skmgmtLoadSkills();
  } catch (e) {
    alert(`删除失败: ${e.code || ''} ${e.message}`);
  } finally {
    skmgmtState.submitting = false;
  }
}

async function skmgmtBump(id) {
  const kind = prompt('升级类型: patch / minor / major', 'patch');
  if (!kind) return;
  const changelog = prompt('变更说明（可选）', '') || '';
  if (skmgmtState.submitting) return;
  skmgmtState.submitting = true;
  try {
    await skmgmtFetch(`/api/skills-mgmt/${id}/versions/bump`, { method: 'POST', body: { kind, changelog } });
    skmgmtTrack('skill_bump', { id, kind });
    await skmgmtSelectSkill(id);
  } catch (e) {
    alert(`升级失败: ${e.code || ''} ${e.message}`);
  } finally {
    skmgmtState.submitting = false;
  }
}

async function skmgmtRollback(id, ver) {
  if (!confirm(`回滚到版本 ${ver}？`)) return;
  try {
    await skmgmtFetch(`/api/skills-mgmt/${id}/versions/rollback`, { method: 'POST', body: { target_version: ver } });
    skmgmtTrack('skill_rollback', { id, ver });
    await skmgmtSelectSkill(id);
  } catch (e) {
    alert(`回滚失败: ${e.code || ''} ${e.message}`);
  }
}

async function skmgmtOptimize(id) {
  try {
    const data = await skmgmtFetch(`/api/skills-mgmt/${id}/optimize`, { method: 'POST', body: {} });
    const recs = data.recommendations || [];
    const acts = data.actions_taken || {};
    let msg = '参数优化建议:\n' + (recs.length ? recs.map(r => '• ' + r).join('\n') : '暂无建议');
    if (Object.keys(acts).length) msg += '\n\n已执行动作:\n' + Object.entries(acts).map(([k,v]) => `• ${k}: ${v}`).join('\n');
    alert(msg);
    await skmgmtSelectSkill(id);
  } catch (e) {
    alert(`优化失败: ${e.code || ''} ${e.message}`);
  }
}

async function skmgmtRecordExec(id, success) {
  try {
    await skmgmtFetch(`/api/skills-mgmt/${id}/execution`, { method: 'POST', body: { success, latency_ms: Math.random() * 2000 + 100 } });
    skmgmtTrack('skill_exec_record', { id, success });
    await skmgmtSelectSkill(id);
  } catch (e) {
    alert(`记录失败: ${e.code || ''} ${e.message}`);
  }
}

// ════════════════════════════════════════════════════════════
//  分类下拉
// ════════════════════════════════════════════════════════════
async function skmgmtLoadCategories() {
  try {
    const data = await skmgmtFetch('/api/skills-mgmt/meta/categories');
    const sel = document.getElementById('skmgmt-cat-filter');
    (data.categories || data.items || []).forEach(c => {
      const opt = document.createElement('option');
      opt.value = c.value || c;
      opt.textContent = c.label || c;
      sel.appendChild(opt);
    });
  } catch (e) { /* 忽略，下拉保持默认 */ }
}

// ════════════════════════════════════════════════════════════
//  批量审核
// ════════════════════════════════════════════════════════════
async function skmgmtBatchReview() {
  if (skmgmtState.submitting) return;
  skmgmtState.submitting = true;
  try {
    const data = await skmgmtFetch('/api/skills-mgmt/review/batch', { method: 'POST', body: {} });
    skmgmtTrack('skill_batch_review', { count: (data.results || []).length });
    const reviewed = data.results || [];
    alert(`批量审核完成: ${reviewed.length} 个技能\n` + reviewed.map(r => `• ${r.skill_id}: ${r.status} (${r.score})`).join('\n'));
    await skmgmtLoadSkills();
  } catch (e) {
    alert(`批量审核失败: ${e.code || ''} ${e.message}`);
  } finally {
    skmgmtState.submitting = false;
  }
}

// ════════════════════════════════════════════════════════════
//  新建技能（模态框：AI 辅助 / 手动 / 安装）
// ════════════════════════════════════════════════════════════
function skmgmtOpenCreator() {
  const overlay = document.createElement('div');
  overlay.className = 'skmgmt-modal-overlay';
  overlay.id = 'skmgmt-creator';
  overlay.innerHTML = `
    <div class="skmgmt-modal">
      <div class="skmgmt-modal-header">
        <span class="skmgmt-modal-title">新建技能</span>
        <button class="btn-sm" onclick="skmgmtCloseCreator()">×</button>
      </div>
      <div class="skmgmt-modal-body">
        <div class="skmgmt-mode-tabs">
          <button class="skmgmt-mode-tab active" onclick="skmgmtCreatorMode('ai')">AI 辅助生成</button>
          <button class="skmgmt-mode-tab" onclick="skmgmtCreatorMode('manual')">手动编写</button>
          <button class="skmgmt-mode-tab" onclick="skmgmtCreatorMode('install')">外部安装</button>
        </div>
        <div id="skmgmt-creator-body"></div>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
  overlay.addEventListener('click', e => { if (e.target === overlay) skmgmtCloseCreator(); });
  skmgmtCreatorMode('ai');
}

function skmgmtCloseCreator() {
  const el = document.getElementById('skmgmt-creator');
  if (el) el.remove();
}

function skmgmtCreatorMode(mode) {
  document.querySelectorAll('.skmgmt-mode-tab').forEach((b, i) => {
    b.classList.toggle('active', ['ai', 'manual', 'install'].indexOf(mode) === i);
  });
  const body = document.getElementById('skmgmt-creator-body');
  if (mode === 'ai') {
    body.innerHTML = `
      <div class="skmgmt-form-row"><label class="skmgmt-form-label">技能名称 *</label><input class="skmgmt-form-input" id="sk-ai-name" placeholder="如：代码审查助手"></div>
      <div class="skmgmt-form-row"><label class="skmgmt-form-label">意图描述 *</label><textarea class="skmgmt-form-textarea" id="sk-ai-intent" placeholder="描述这个技能应该做什么..."></textarea></div>
      <div class="skmgmt-form-row"><label class="skmgmt-form-label">标签（逗号分隔）</label><input class="skmgmt-form-input" id="sk-ai-tags" placeholder="review,code"></div>
      <button class="btn-sm primary" onclick="skmgmtSubmitCreator('ai')" style="width:100%">AI 生成技能骨架</button>
      <div style="font-size:11px;color:#8b949e;margin-top:8px">LLM 不可用时自动降级为模板生成</div>
    `;
  } else if (mode === 'manual') {
    body.innerHTML = `
      <div class="skmgmt-form-row"><label class="skmgmt-form-label">技能 ID *</label><input class="skmgmt-form-input" id="sk-m-id" placeholder="kebab-case，如 code-reviewer"></div>
      <div class="skmgmt-form-row"><label class="skmgmt-form-label">名称 *</label><input class="skmgmt-form-input" id="sk-m-name" placeholder="技能显示名"></div>
      <div class="skmgmt-form-row"><label class="skmgmt-form-label">描述</label><textarea class="skmgmt-form-textarea" id="sk-m-desc" style="min-height:60px"></textarea></div>
      <div class="skmgmt-form-row"><label class="skmgmt-form-label">分类</label><select class="skmgmt-form-select" id="sk-m-cat"><option value="custom">custom</option><option value="builtin">builtin</option><option value="ai_generated">ai_generated</option><option value="community">community</option></select></div>
      <div class="skmgmt-form-row"><label class="skmgmt-form-label">内容类型</label><select class="skmgmt-form-select" id="sk-m-ctype"><option value="markdown">markdown</option><option value="python">python</option><option value="javascript">javascript</option><option value="text">text</option></select></div>
      <div class="skmgmt-form-row"><label class="skmgmt-form-label">内容 *</label><textarea class="skmgmt-form-textarea" id="sk-m-content" placeholder="技能内容（文档/代码/提示词）"></textarea></div>
      <button class="btn-sm primary" onclick="skmgmtSubmitCreator('manual')" style="width:100%">创建技能</button>
    `;
  } else {
    body.innerHTML = `
      <div style="font-size:12px;color:#8b949e;margin-bottom:10px">支持格式:<br>• github:user/repo[/path]<br>• url:https://...skill.json<br>• local:/path/to/skill.json<br>• registry:openclaw/foo</div>
      <div class="skmgmt-form-row"><label class="skmgmt-form-label">来源 *</label><input class="skmgmt-form-input" id="sk-i-source" placeholder="github:user/repo"></div>
      <div class="skmgmt-form-row"><label class="skmgmt-check"><input type="checkbox" id="sk-i-force"> 覆盖已存在的同 ID 技能</label></div>
      <button class="btn-sm primary" onclick="skmgmtSubmitCreator('install')" style="width:100%">安装</button>
    `;
  }
}

async function skmgmtSubmitCreator(mode) {
  if (skmgmtState.submitting) return;
  skmgmtState.submitting = true;
  try {
    let data, path;
    if (mode === 'ai') {
      const name = document.getElementById('sk-ai-name').value.trim();
      const intent = document.getElementById('sk-ai-intent').value.trim();
      if (!name || !intent) { alert('名称和意图必填'); skmgmtState.submitting = false; return; }
      const tags = document.getElementById('sk-ai-tags').value.split(',').map(t => t.trim()).filter(Boolean);
      path = '/api/skills-mgmt/create/ai';
      data = { name, intent, category: 'custom', tags };
    } else if (mode === 'manual') {
      const id = document.getElementById('sk-m-id').value.trim();
      const name = document.getElementById('sk-m-name').value.trim();
      if (!id || !name) { alert('ID 和名称必填'); skmgmtState.submitting = false; return; }
      path = '/api/skills-mgmt/create/manual';
      data = {
        id, name,
        description: document.getElementById('sk-m-desc').value.trim(),
        category: document.getElementById('sk-m-cat').value,
        content_type: document.getElementById('sk-m-ctype').value,
        content: document.getElementById('sk-m-content').value,
        tags: [],
      };
    } else {
      const source = document.getElementById('sk-i-source').value.trim();
      if (!source) { alert('来源必填'); skmgmtState.submitting = false; return; }
      path = '/api/skills-mgmt/install';
      data = { source, force: document.getElementById('sk-i-force').checked };
    }
    const res = await skmgmtFetch(path, { method: 'POST', body: data });
    skmgmtTrack('skill_create', { mode, id: res.skill && res.skill.id });
    skmgmtCloseCreator();
    await skmgmtLoadSkills();
    if (res.skill) skmgmtSelectSkill(res.skill.id);
  } catch (e) {
    alert(`创建失败: ${e.code || ''} ${e.message}`);
  } finally {
    skmgmtState.submitting = false;
  }
}

// ════════════════════════════════════════════════════════════
//  工作流学习
// ════════════════════════════════════════════════════════════
async function loadWorkflows() {
  const listEl = document.getElementById('wf-list');
  if (!listEl) return;
  listEl.innerHTML = '<div class="skmgmt-loading">加载工作流...</div>';
  try {
    const data = await skmgmtFetch('/api/workflow-learning/workflows?enabled_only=false');
    const items = data.items || [];
    const badge = document.getElementById('skmgmt-wf-count');
    if (items.length > 0) { badge.textContent = items.length; badge.style.display = 'inline-block'; }
    else { badge.style.display = 'none'; }
    if (!items.length) {
      listEl.innerHTML = '<div class="skmgmt-empty">暂无学习到的工作流。智能体与大模型成功交互后会自动学习方法。</div>';
      return;
    }
    listEl.innerHTML = items.map(wf => `
      <div class="skmgmt-wf-card">
        <div class="skmgmt-wf-card-head">
          <span class="skmgmt-wf-name">${escapeHtml(wf.name)}</span>
          <span class="skmgmt-tag">优先级 ${wf.priority}</span>
        </div>
        <div class="skmgmt-wf-desc">${escapeHtml(wf.description || '无描述')}</div>
        <div class="skmgmt-wf-meta">
          <span>置信度 ${(wf.confidence * 100).toFixed(0)}%</span>
          <span style="color:#66bb6a">成功 ${wf.success_count}</span>
          <span style="color:#ef5350">失败 ${wf.failure_count}</span>
          <span>${wf.steps ? wf.steps.length : 0} 步</span>
          <span>${wf.enabled ? '✓ 启用' : '✗ 禁用'}</span>
        </div>
        ${wf.steps && wf.steps.length ? `
        <div class="skmgmt-wf-steps">
          ${wf.steps.map((s, i) => `<div class="skmgmt-wf-step"><span class="skmgmt-wf-step-num">${i + 1}</span><span>${escapeHtml(s.tool_name)}</span></div>`).join('')}
        </div>` : ''}
        <div style="margin-top:8px;display:flex;gap:6px">
          <button class="btn-sm" onclick="wfToggle('${wf.id}', ${!wf.enabled})">${wf.enabled ? '禁用' : '启用'}</button>
          <input type="number" value="${wf.priority}" min="0" max="100" style="width:60px" onchange="wfSetPriority('${wf.id}', this.value)" title="优先级 0-100">
          <button class="btn-sm" onclick="wfDelete('${wf.id}')" style="color:#ef5350">删除</button>
        </div>
      </div>
    `).join('');
  } catch (e) {
    listEl.innerHTML = `<div class="skmgmt-error">加载失败: ${e.code || ''} ${e.message}</div>`;
  }
}

async function wfToggle(id, enabled) {
  try {
    await skmgmtFetch(`/api/workflow-learning/workflows/${id}/toggle`, { method: 'POST', body: { enabled } });
    await loadWorkflows();
  } catch (e) { alert(`操作失败: ${e.message}`); }
}

async function wfSetPriority(id, val) {
  try {
    await skmgmtFetch(`/api/workflow-learning/workflows/${id}/priority`, { method: 'POST', body: { priority: parseInt(val) } });
  } catch (e) { alert(`设置失败: ${e.message}`); }
}

async function wfDelete(id) {
  if (!confirm('确认删除此工作流？')) return;
  try {
    await skmgmtFetch(`/api/workflow-learning/workflows/${id}`, { method: 'DELETE' });
    await loadWorkflows();
  } catch (e) { alert(`删除失败: ${e.message}`); }
}

async function wfMatchOnly() {
  const text = document.getElementById('wf-match-input').value.trim();
  if (!text) { alert('请输入任务描述'); return; }
  const resultEl = document.getElementById('wf-match-result');
  resultEl.style.display = 'block';
  resultEl.className = 'skmgmt-wf-result-box';
  resultEl.innerHTML = '匹配中...';
  try {
    const data = await skmgmtFetch(`/api/workflow-learning/match`, { method: 'POST', body: { task_text: text, top_k: 5 } });
    const cands = data.candidates || data.items || [];
    if (!cands.length) {
      resultEl.className = 'skmgmt-wf-result-box';
      resultEl.innerHTML = '<b>未匹配到工作流</b>，将走正常 LLM 调用路径';
    } else {
      resultEl.className = 'skmgmt-wf-result-box success';
      resultEl.innerHTML = `<b>匹配到 ${cands.length} 个候选工作流:</b>` + cands.map(c => `
        <div class="skmgmt-cand"><span>${escapeHtml(c.workflow_name)} (相似度 ${(c.similarity * 100).toFixed(0)}%, 置信度 ${(c.confidence * 100).toFixed(0)}%)</span><span class="skmgmt-tag">优先级 ${c.priority}</span></div>
      `).join('');
    }
  } catch (e) {
    resultEl.className = 'skmgmt-wf-result-box fail';
    resultEl.innerHTML = `匹配失败: ${e.message}`;
  }
}

async function wfMatchAndExecute() {
  const text = document.getElementById('wf-match-input').value.trim();
  if (!text) { alert('请输入任务描述'); return; }
  const resultEl = document.getElementById('wf-match-result');
  resultEl.style.display = 'block';
  resultEl.className = 'skmgmt-wf-result-box';
  resultEl.innerHTML = '匹配并执行中...';
  try {
    const data = await skmgmtFetch(`/api/workflow-learning/try-execute`, { method: 'POST', body: { task_text: text, params: {} } });
    if (!data.matched) {
      resultEl.className = 'skmgmt-wf-result-box';
      resultEl.innerHTML = '<b>未匹配到工作流</b>，已跳过本地执行';
    } else if (data.success) {
      resultEl.className = 'skmgmt-wf-result-box success';
      resultEl.innerHTML = `<b>✓ 执行成功</b> (${data.workflow_name})<br>相似度 ${(data.similarity * 100).toFixed(0)}% | 跳过 LLM: ${data.skipped_llm ? '是' : '否'} | 耗时 ${data.execution_time_ms.toFixed(0)}ms<br><b>输出:</b><br><pre style="white-space:pre-wrap;margin:6px 0;font-size:11px">${escapeHtml(typeof data.output === 'string' ? data.output : JSON.stringify(data.output, null, 2))}</pre>`;
    } else {
      resultEl.className = 'skmgmt-wf-result-box fail';
      resultEl.innerHTML = `<b>✗ 执行失败</b> (${data.workflow_name})<br>${escapeHtml(data.error || '未知错误')}`;
    }
    await loadWorkflows();
  } catch (e) {
    resultEl.className = 'skmgmt-wf-result-box fail';
    resultEl.innerHTML = `执行失败: ${e.message}`;
  }
}

// ════════════════════════════════════════════════════════════
//  健康检查（轮询）
// ════════════════════════════════════════════════════════════
async function skmgmtCheckHealth() {
  const el = document.getElementById('skmgmt-health');
  if (!el) return;
  let ok = false;
  try {
    const d = await skmgmtFetch('/api/skills-mgmt/health');
    ok = d.ok === true;
  } catch (e) { ok = false; }
  el.className = 'skmgmt-health ' + (ok ? 'online' : 'offline');
  el.title = ok ? '技能服务在线' : '技能服务离线';
}

// ════════════════════════════════════════════════════════════
//  工具
// ════════════════════════════════════════════════════════════
function escapeHtml(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// 切换到技能视图时初始化（由 nav.js 的 switchView 调用 loadSkills，这里补充 v2 初始化）
// 通过监听 view-skills 的显示来触发健康检查
const _origLoadSkills = typeof loadSkills === 'function' ? loadSkills : null;
window.loadSkills = function() {
  if (_origLoadSkills) _origLoadSkills();
  // 同时初始化 v2 健康检查
  if (!skmgmtState.healthTimer) {
    skmgmtCheckHealth();
    skmgmtState.healthTimer = setInterval(skmgmtCheckHealth, 30000);
  }
};
