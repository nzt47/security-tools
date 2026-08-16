// ════════════════════════════════════════════════════════════
// 云枢 · 六域模块拓扑视图（S3）
// 数据源：GET  /api/modules/topology           （六域树 + 实时状态）
//         GET  /api/modules/<id>/detail        （节点详情）
//         POST /api/modules/<id>/actions       （统一干预入口）
// 交互：节点点击 → 详情面板；干预按钮 → 高危二次确认 + reason 必填
//       5s 轮询刷新拓扑（不可见时暂停，省资源）
// 测试注入：所有函数挂 window.topologyAPI，jsdom 测试可直接调用
// ════════════════════════════════════════════════════════════
(function () {
  'use strict';

  const POLL_MS = 5000;
  const LOG_PREFIX = '[topology]';
  // 统一排查日志（DevTools 按 "[topology]" 过滤即可全览交互链路）
  function _log(event, data) {
    if (window.console && window.console.info) {
      window.console.info(LOG_PREFIX, event, data === undefined ? '' : data);
    }
  }
  const STATUS_META = {
    healthy:  { label: '健康', cls: 's-healthy' },
    warning:  { label: '警告', cls: 's-warning' },
    fault:    { label: '故障', cls: 's-fault' },
    offline:  { label: '离线', cls: 's-offline' },
    disabled: { label: '未启用', cls: 's-disabled' },
  };

  // 供测试覆盖：真实环境用全局 apiFetch（index.html 提供，自动带 token）
  function _request(url, options) {
    if (window.apiFetch) return window.apiFetch(url, options);
    return fetch(url, options);
  }

  // ── 拓扑渲染 ──
  function renderTopology(data) {
    const root = document.getElementById('topo-tree');
    if (!root) return;
    root.innerHTML = '';
    const domains = data.domains || [];

    domains.forEach(function (d) {
      const dom = document.createElement('div');
      dom.className = 'domain';
      dom.dataset.domain = d.domain_id;

      const fault = d.nodes.filter(function (n) { return n.status === 'fault'; }).length;
      const warning = d.nodes.filter(function (n) { return n.status === 'warning'; }).length;
      const statusSummary = (fault ? ' <span class="dom-sum dom-fault">● ' + fault + ' 故障</span>' : '')
        + (warning ? ' <span class="dom-sum dom-warn">● ' + warning + ' 警告</span>' : '');

      const header = document.createElement('div');
      header.className = 'domain-header';
      header.innerHTML = '<span class="arrow">▼</span>'
        + '<span class="icon">' + (d.icon || '📦') + '</span>'
        + '<span class="name">' + d.domain_name + '</span>'
        + '<span class="status-summary">' + statusSummary + '</span>';
      header.addEventListener('click', function () { dom.classList.toggle('collapsed'); });

      const body = document.createElement('div');
      body.className = 'domain-body';

      d.nodes.forEach(function (n) {
        const st = STATUS_META[n.status] || STATUS_META.offline;
        const node = document.createElement('div');
        node.className = 'node';
        node.dataset.moduleId = n.module_id;
        node.dataset.status = n.status;
        node.innerHTML = '<span class="dot ' + st.cls + '"></span>'
          + '<span class="node-name" title="' + n.name + '">' + n.name + '</span>'
          + '<span class="chips">' + (n.metrics || []).map(function (m) {
            return '<span class="chip">' + m.key + '=' + m.value + '</span>';
          }).join('') + '</span>';
        node.addEventListener('click', function () { selectNode(n); });
        body.appendChild(node);
      });

      dom.appendChild(header);
      dom.appendChild(body);
      root.appendChild(dom);
    });
    _log('render_topology.done', {
      domains: domains.length,
      nodes: domains.reduce(function (acc, d) { return acc + d.nodes.length; }, 0),
      at: Date.now(),
    });
  }

  // ── 节点详情 ──
  function selectNode(node) {
    document.querySelectorAll('#topo-tree .node.selected').forEach(function (el) {
      el.classList.remove('selected');
    });
    const el = document.querySelector('#topo-tree .node[data-module-id="' + node.module_id + '"]');
    if (el) el.classList.add('selected');

    const detail = document.getElementById('topo-detail');
    if (!detail) return;
    detail.dataset.moduleId = node.module_id;
    detail.innerHTML = '<div class="topo-loading">加载详情...</div>';
    _log('select_node.start', { moduleId: node.module_id, at: Date.now() });

    _request('/api/modules/' + encodeURIComponent(node.module_id) + '/detail')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        // 竞态保护：请求期间用户可能已切到别的节点，若面板归属变化则丢弃本次结果
        if (detail.dataset.moduleId !== node.module_id) {
          _log('select_node.race_discarded', {
            moduleId: node.module_id,
            currentPanelModule: detail.dataset.moduleId,
            at: Date.now(),
          });
          return;
        }
        renderDetail(detail, d);
        _log('select_node.done', { moduleId: node.module_id, at: Date.now() });
      })
      .catch(function (err) {
        if (detail.dataset.moduleId !== node.module_id) {
          _log('select_node.race_error_discarded', { moduleId: node.module_id, err: String(err) });
          return;
        }
        detail.innerHTML = '<div class="topo-loading">详情加载失败</div>';
        _log('select_node.error', { moduleId: node.module_id, err: String(err) });
      });
  }

  function renderDetail(el, d) {
    const st = STATUS_META[d.status] || STATUS_META.offline;
    const actionsHtml = (d.actions || []).map(function (a) {
      const danger = a.danger === 'high' ? 'danger' : '';
      const note = a.note ? ' title="' + a.note + '"' : '';
      return '<button class="action-btn ' + danger + '" data-action="' + a.action + '"' + note + '>' + a.action + '</button>';
    }).join('') || '<span class="topo-empty">该节点无可干预操作</span>';

    el.innerHTML =
      '<div class="detail-row"><span class="k">模块 ID</span><span class="v">' + d.module_id + '</span></div>'
      + '<div class="detail-row"><span class="k">所属域</span><span class="v">' + d.domain + '</span></div>'
      + '<div class="detail-row"><span class="k">状态</span><span class="v"><span class="dot ' + st.cls + '" style="width:8px;height:8px"></span> ' + st.label + ' · ' + d.status_detail + '</span></div>'
      + '<div class="detail-row"><span class="k">代码路径</span><span class="v">' + d.path + '</span></div>'
      + '<div class="detail-section"><div class="title">关键指标</div>'
      + (d.metrics && d.metrics.length
        ? d.metrics.map(function (m) { return '<div class="detail-row"><span class="k">' + m.key + '</span><span class="v">' + m.value + '</span></div>'; }).join('')
        : '<div class="detail-row"><span class="k">无实时指标</span><span class="v">—</span></div>')
      + '</div>'
      + '<div class="detail-section"><div class="title">干预操作</div><div class="action-group">' + actionsHtml + '</div></div>'
      + '<div class="note">' + (d.description || '') + '</div>';

    el.querySelectorAll('.action-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        performAction(d.module_id, btn.dataset.action, btn.classList.contains('danger'));
      });
    });
  }

  // ── 统一干预 ──
  function performAction(moduleId, action, isDanger) {
    if (isDanger) {
      const ok = window.confirm('⚠️ 高危操作确认\n\n模块: ' + moduleId + '\n动作: ' + action
        + '\n\n该操作不可轻率执行，是否继续？');
      _log('perform_action.confirm', { moduleId: moduleId, action: action, approved: ok });
      if (!ok) return;
    }
    const reason = window.prompt(isDanger ? '请输入高危操作原因（必填）:' : '操作原因（可留空）:', '');
    if (isDanger && !(reason && reason.trim())) {
      _log('perform_action.reason_rejected', { moduleId: moduleId, action: action, reason: reason });
      showToast('高危操作必须填写原因', 'warn');
      return;
    }

    const body = JSON.stringify({ action: action, reason: reason || '', params: {} });
    _log('perform_action.send', { moduleId: moduleId, action: action, reason: reason || '', body: body });
    _request('/api/modules/' + encodeURIComponent(moduleId) + '/actions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body,
    })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (res && res.ok) {
          _log('perform_action.done', { moduleId: moduleId, action: action, res: res });
          showToast('干预成功: ' + action, 'info');
        } else {
          _log('perform_action.rejected', { moduleId: moduleId, action: action, res: res });
          showToast('干预失败: ' + ((res && res.error) || '未知错误'), 'warn');
        }
      })
      .catch(function (err) {
        _log('perform_action.error', { moduleId: moduleId, action: action, err: String(err) });
        showToast('干预请求失败', 'warn');
      });
  }

  // ── 全局指标条 ──
  function renderGlobalMetrics(data) {
    const bar = document.getElementById('topo-metrics');
    if (!bar) return;
    const health = data.overall_health != null ? data.overall_health : '—';
    const total = data.domains.reduce(function (acc, d) { return acc + d.nodes.length; }, 0);
    const on = data.domains.reduce(function (acc, d) {
      return acc + d.nodes.filter(function (n) { return n.status !== 'offline'; }).length;
    }, 0);
    bar.innerHTML = '<div class="metric-card"><div class="label">整体健康分</div><div class="value">' + health + '</div></div>'
      + '<div class="metric-card"><div class="label">模块在线</div><div class="value">' + on + '<small> / ' + total + '</small></div></div>'
      + '<div class="metric-card"><div class="label">功能域</div><div class="value">' + data.domains.length + '</div></div>';
  }

  function showToast(msg, type) {
    if (window.showNotice) { window.showNotice(msg, type); return; }
    // 无全局通知条时降级 console
    if (window.console) console.log('[topology] ' + msg);
  }

  // ── 主入口：加载并启动轮询 ──
  function loadTopology() {
    _log('load_topology.start', { at: Date.now() });
    _request('/api/modules/topology')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        renderTopology(data);
        renderGlobalMetrics(data);
        _log('load_topology.done', {
          overall_health: data.overall_health,
          at: Date.now(),
        });
      })
      .catch(function (err) {
        const root = document.getElementById('topo-tree');
        if (root) root.innerHTML = '<div class="topo-empty">拓扑加载失败，请确认服务已启动</div>';
        _log('load_topology.error', { err: String(err) });
      });
  }

  let _timer = null;
  function startPolling() {
    if (_timer) {
      _log('polling.already_running', { at: Date.now() });
      return;
    }
    _timer = setInterval(function () {
      if (document.hidden) {
        _log('polling.skipped_hidden', { at: Date.now() });
        return; // 页面不可见时暂停轮询
      }
      _log('polling.tick', { at: Date.now() });
      loadTopology();
    }, POLL_MS);
    _log('polling.started', { interval_ms: POLL_MS, at: Date.now() });
  }
  function stopPolling() {
    if (_timer) {
      clearInterval(_timer);
      _timer = null;
      _log('polling.stopped', { at: Date.now() });
    }
  }

  // 暴露测试与外部调用接口
  window.topologyAPI = {
    loadTopology: loadTopology,
    startPolling: startPolling,
    stopPolling: stopPolling,
    selectNode: selectNode,
    performAction: performAction,
    renderTopology: renderTopology,
    renderGlobalMetrics: renderGlobalMetrics,
  };

  // 主控台 registerView 懒加载入口（app.js switchView 首次切到该视图时调用）
  window.loadTopologyView = function () {
    loadTopology();
    startPolling();
  };
})();
