/* 云枢 · 审批控制台（TASK-S4-01 / v7.2 §5.7⑦ 前端）
 *
 * 【DOM 隔离】整个审批按钮区挂载在 `#cp-approval-console-root` 的 **Shadow Root**
 * 内：宿主页 CSS 选择器无法命中审批按钮（避免被既有布局覆盖/遮挡），审批按钮的
 * 样式也不会外泄污染宿主页。宿主元素本身的固定 zIndex / 层叠上下文见
 * `static/css/approval_console.css`。
 *
 * 【权限纪律】本文件只做**渲染与提交**。「谁能审批」由后端 §7.0 Actor 矩阵单表判定
 * ——请求体里出现的任何 actor / actor_type 都会被后端忽略；前端隐藏按钮仅是 UX。
 *
 * 【CSRF】双重提交：从 cookie `cp_approval_csrf` 读取令牌，回填到请求头
 * `X-CSRF-Token`；后端把「Cookie 中的会话 + 头中的 CSRF」配对校验。
 *
 * 【链接】审批链接 token 在服务端与**会话**绑定：把链接复制给别人打开会得到
 * `session_mismatch`（禁分享式链接）。本控制台不展示可复制的公开 URL。
 */
(function () {
  'use strict';

  var API = '/api/approval';
  var CSRF_COOKIE = 'cp_approval_csrf';
  var CSRF_HEADER = 'X-CSRF-Token';
  var SESSION_COOKIE = 'cp_approval_session';

  var state = {
    identity: null,
    session: null,
    items: [],
    links: {},          // record_id -> link token（仅当前会话可用）
    secondFactor: {},   // record_id -> 一次性确认码
    collapsed: false,
    message: ''
  };

  // ── 工具 ────────────────────────────────────────────────

  function readCookie(name) {
    var parts = ('; ' + document.cookie).split('; ' + name + '=');
    return parts.length === 2 ? decodeURIComponent(parts.pop().split(';').shift()) : '';
  }

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      var value = attrs[k];
      // 布尔属性：null / undefined / false ⇒ 不写属性（否则 setAttribute(k, null)
      // 会写出字符串 "null"，使 `disabled` 等布尔属性恒为真）
      if (value === null || value === undefined || value === false) { return; }
      if (k === 'class') { node.className = value; }
      else if (k === 'text') { node.textContent = value; }
      else if (k.indexOf('on') === 0 && typeof value === 'function') {
        node.addEventListener(k.slice(2), value);
      } else { node.setAttribute(k, value); }
    });
    (children || []).forEach(function (child) {
      node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
    });
    return node;
  }

  function request(method, path, body) {
    var headers = { 'Accept': 'application/json' };
    if (body) { headers['Content-Type'] = 'application/json'; }
    // CSRF 双重提交：Cookie 里的令牌必须出现在请求头
    var csrf = readCookie(CSRF_COOKIE);
    if (csrf) { headers[CSRF_HEADER] = csrf; }
    return fetch(API + path, {
      method: method,
      headers: headers,
      credentials: 'same-origin',
      body: body ? JSON.stringify(body) : undefined
    }).then(function (res) {
      return res.json().catch(function () { return {}; })
        .then(function (data) { return { status: res.status, data: data }; });
    });
  }

  function setMessage(text) {
    state.message = text || '';
    render();
  }

  // ── 动作 ────────────────────────────────────────────────

  function loadIdentity() {
    return request('GET', '/whoami').then(function (r) {
      state.identity = r.data || null;
      state.session = (r.data && r.data.session) || null;
      render();
    });
  }

  function openSession() {
    return request('POST', '/session').then(function (r) {
      if (r.status >= 400 || !r.data.ok) {
        setMessage('开启审批会话失败：' + (r.data.message || r.status));
        return;
      }
      state.session = r.data.session;
      setMessage('审批会话已开启（' + r.data.session.expires_in + 's 内有效）');
      return loadPending();
    });
  }

  function loadPending() {
    return request('GET', '/pending').then(function (r) {
      if (r.status >= 400 || !r.data.ok) {
        setMessage('读取待审批失败：' + (r.data.message || r.status));
        return;
      }
      state.items = r.data.items || [];
      if (r.data.identity) { state.identity = Object.assign({}, state.identity, r.data.identity); }
      setMessage('待审批 ' + state.items.length + ' 条');
    });
  }

  function issueLink(recordId) {
    return request('POST', '/link', { record_id: recordId }).then(function (r) {
      if (r.status >= 400 || !r.data.ok) {
        setMessage('签发审批链接失败：' + (r.data.message || r.status));
        return;
      }
      state.links[recordId] = r.data.link.token;
      setMessage('审批链接已签发（' + r.data.link.expires_in + 's 内有效，绑定当前会话）');
    });
  }

  function issueSecondFactor(recordId) {
    return request('POST', '/second-factor', { record_id: recordId }).then(function (r) {
      if (r.status >= 400 || !r.data.ok) {
        setMessage('签发确认码失败：' + (r.data.message || r.status));
        return;
      }
      state.secondFactor[recordId] = r.data.code;
      setMessage('二次认证确认码：' + r.data.code + '（一次性，仅当前会话可用）');
    });
  }

  function decide(recordId, approve) {
    var link = state.links[recordId];
    if (!link) {
      setMessage('请先为该记录签发审批链接（会话绑定，禁分享）');
      return Promise.resolve();
    }
    var body = { link_token: link, note: '' };
    if (approve) {
      body.note = window.prompt('审批备注（可留空）：', '') || '';
    } else {
      body.note = window.prompt('驳回原因（必填，审计要求）：', '') || '';
      if (!body.note) { setMessage('驳回必须填写原因'); return Promise.resolve(); }
    }
    var code = state.secondFactor[recordId];
    if (code) { body.second_factor = code; }
    return request('POST', '/' + encodeURIComponent(recordId) + (approve ? '/approve' : '/reject'), body)
      .then(function (r) {
        if (r.status >= 400 || !r.data.ok) {
          var hint = r.data.requires_second_factor ? '（该审批为 destructive，请先取确认码）' : '';
          setMessage('审批未通过：' + (r.data.message || r.status) + hint);
          if (r.data.code === 'second_factor_required') { return issueSecondFactor(recordId); }
          return;
        }
        delete state.links[recordId];
        delete state.secondFactor[recordId];
        setMessage('审批已提交：' + r.data.record.state + '（actor=' + r.data.record.actor + '）');
        return loadPending();
      });
  }

  // ── 渲染 ────────────────────────────────────────────────

  var STYLE = [
    ':host { all: initial; }',
    '.panel { border: 1px solid #d0d7de; border-radius: 10px; overflow: hidden;',
    '  background: #ffffff; box-shadow: 0 8px 24px rgba(31,35,40,.18); }',
    '.head { display: flex; align-items: center; gap: 8px; padding: 10px 12px;',
    '  background: #24292f; color: #fff; }',
    '.head b { font-size: 13px; }',
    '.head .spacer { flex: 1; }',
    'button { border: 1px solid #d0d7de; background: #f6f8fa; border-radius: 6px;',
    '  padding: 3px 8px; font-size: 12px; cursor: pointer; }',
    'button:hover { background: #eaeef2; }',
    'button.primary { background: #1f883d; border-color: #1f883d; color: #fff; }',
    'button.danger { background: #cf222e; border-color: #cf222e; color: #fff; }',
    'button:disabled { opacity: .5; cursor: not-allowed; }',
    '.identity { padding: 8px 12px; background: #f6f8fa; font-size: 12px;',
    '  border-bottom: 1px solid #d0d7de; line-height: 1.7; }',
    '.badge { display: inline-block; padding: 1px 6px; border-radius: 999px;',
    '  font-size: 11px; margin-left: 4px; }',
    '.badge.ok { background: #dafbe1; color: #1a7f37; }',
    '.badge.warn { background: #fff8c5; color: #7d4e00; }',
    '.badge.taint { background: #ffebe9; color: #cf222e; }',
    '.list { max-height: 380px; overflow: auto; }',
    '.item { padding: 10px 12px; border-bottom: 1px solid #eaeef2; }',
    '.item .title { font-weight: 600; }',
    '.item .meta { color: #57606a; font-size: 11px; margin-top: 2px; word-break: break-all; }',
    '.item .desc { margin: 4px 0; }',
    '.actions { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }',
    '.msg { padding: 8px 12px; font-size: 12px; color: #57606a; background: #f6f8fa; }',
    '.empty { padding: 16px 12px; color: #57606a; text-align: center; font-size: 12px; }'
  ].join('\n');

  function buildRoot() {
    var host = document.getElementById('cp-approval-console-root');
    if (!host) {
      host = document.createElement('div');
      host.id = 'cp-approval-console-root';
      document.body.appendChild(host);
    }
    if (!host.shadowRoot) {
      // DOM 隔离：审批按钮区进入独立 Shadow Root
      host.attachShadow({ mode: 'open' });
      var style = document.createElement('style');
      style.textContent = STYLE;
      host.shadowRoot.appendChild(style);
      host.shadowRoot.appendChild(el('div', { class: 'panel', id: 'cp-panel' }));
    }
    return host.shadowRoot;
  }

  function identityBadges(identity) {
    var out = [];
    if (!identity) { return out; }
    if (identity.is_human) {
      out.push(el('span', { class: 'badge ok', text: 'human' }));
    } else {
      out.push(el('span', { class: 'badge warn', text: String(identity.actor_type || 'unknown') }));
    }
    if (identity.identity_degraded) {
      out.push(el('span', { class: 'badge warn', text: '身份降级（' + (identity.identity_source || '-') + '）' }));
    } else if (identity.identity_source) {
      out.push(el('span', { class: 'badge ok', text: identity.identity_source }));
    }
    return out;
  }

  function renderItem(item) {
    var children = [
      el('div', { class: 'title', text: item.object_type + ' · ' + item.object_id }),
      el('div', { class: 'meta', text: 'record=' + item.record_id + ' level=' + item.level +
        (item.risk ? (' risk=' + item.risk) : '') + ' actor=' + (item.actor || '-') }),
      el('div', { class: 'desc', text: item.description || '' })
    ];
    if (item.taint) {
      // 外来内容进审批上下文 → TaintBadge（§5.7⑦）
      var taintLine = el('div', {});
      taintLine.appendChild(el('span', { class: 'badge taint', text: 'TaintBadge：外来内容' }));
      taintLine.appendChild(el('span', { class: 'meta', text: ' ' + (item.taint_reason || '') }));
      children.push(taintLine);
    }
    if (item.undo_hint || item.compensating_action) {
      children.push(el('div', { class: 'meta', text: 'undo_hint=' + (item.undo_hint || '-') +
        ' | 补偿=' + (item.compensating_action || '-') }));
    } else if (item.undo_hint_status) {
      children.push(el('div', { class: 'meta', text: 'undo_hint 状态=' + item.undo_hint_status }));
    }
    var actions = el('div', { class: 'actions', 'data-cp-approval-actions': item.record_id });
    var link = state.links[item.record_id];
    actions.appendChild(el('button', {
      text: link ? '链接已签发' : '① 签发审批链接',
      onclick: function () { issueLink(item.record_id); },
      disabled: link ? 'disabled' : null
    }));
    if ((item.risk || '') === 'destructive') {
      actions.appendChild(el('button', {
        text: state.secondFactor[item.record_id] ? '② 确认码已取' : '② 取二次认证码',
        onclick: function () { issueSecondFactor(item.record_id); }
      }));
    }
    // 前端隐藏按钮只是 UX：真正的权限判定在后端 §7.0 矩阵（human 专属）
    var human = !state.identity || state.identity.is_human;
    actions.appendChild(el('button', {
      class: 'primary', text: '审批通过',
      onclick: function () { decide(item.record_id, true); },
      disabled: (!human || !link) ? 'disabled' : null
    }));
    actions.appendChild(el('button', {
      class: 'danger', text: '驳回',
      onclick: function () { decide(item.record_id, false); },
      disabled: (!human || !link) ? 'disabled' : null
    }));
    children.push(actions);
    return el('div', { class: 'item' }, children);
  }

  function render() {
    var root = buildRoot();
    var panel = root.getElementById('cp-panel');
    while (panel.firstChild) { panel.removeChild(panel.firstChild); }

    var body = [];
    if (!state.collapsed) {
      var actorLine = el('div', {}, [
        document.createTextNode('操作者：' + ((state.identity && state.identity.actor) || '未识别'))
      ]);
      identityBadges(state.identity).forEach(function (badge) { actorLine.appendChild(badge); });
      var sessionLine = el('div', {
        class: 'meta',
        text: state.session
          ? ('会话 ' + state.session.session_id.slice(0, 8) + '… · ' + state.session.expires_in + 's')
          : '未开启审批会话（审批链接需会话绑定）'
      });
      body.push(el('div', { class: 'identity' }, [actorLine, sessionLine]));

      var list = el('div', { class: 'list' });
      if (!state.items.length) {
        list.appendChild(el('div', { class: 'empty', text: '暂无待审批项' }));
      } else {
        state.items.forEach(function (item) { list.appendChild(renderItem(item)); });
      }
      body.push(list);
      if (state.message) { body.push(el('div', { class: 'msg', text: state.message })); }
    }

    var head = el('div', { class: 'head' }, [
      el('b', { text: '审批控制台（§7.0 / §5.7⑦）' }),
      el('span', { class: 'spacer' }),
      el('button', { text: state.session ? '刷新' : '开启会话',
        onclick: function () { (state.session ? loadPending() : openSession()); } }),
      el('button', { text: state.collapsed ? '展开' : '收起',
        onclick: function () { state.collapsed = !state.collapsed; render(); } })
    ]);
    panel.appendChild(head);
    body.forEach(function (node) { panel.appendChild(node); });
  }

  function boot() {
    buildRoot();
    render();
    loadIdentity().then(function () {
      if (state.session) { return loadPending(); }
      return null;
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  // 诊断/测试可见面（不含任何令牌）
  window.CPApprovalConsole = {
    state: state,
    render: render,
    loadPending: loadPending,
    openSession: openSession,
    issueLink: issueLink,
    issueSecondFactor: issueSecondFactor,
    decide: decide,
    CSRF_HEADER: CSRF_HEADER,
    CSRF_COOKIE: CSRF_COOKIE,
    SESSION_COOKIE: SESSION_COOKIE
  };
})();
