/**
 * CDP 验证脚本 —— 云枢 Electron 安装版功能验证（临时）
 * ------------------------------------------------
 * 用法：node scripts/cdp-verify.mjs <action>
 *   action 可选：
 *     ui         → 快照主窗口 UI 状态（面板/按钮/输入框）
 *     detach     → 点击"独立窗口"按钮，验证 IPC detach → 新窗口 target 出现
 *     chat       → 发送消息，观察流式对话是否可用（含 file:// 下 API 地址问题）
 *     tabs       → 列出所有 CDP target（主窗口/独立窗口）
 * 前提：云枢.exe 已带 --remote-debugging-port=9222 启动
 */
const CDP_HTTP = 'http://127.0.0.1:9222';

async function getTargets() {
  const res = await fetch(`${CDP_HTTP}/json/list`);
  return res.json();
}

function connectWs(wsUrl) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    let id = 0;
    const pending = new Map();
    ws.onopen = () => {
      const call = (method, params = {}) =>
        new Promise((res, rej) => {
          const msgId = ++id;
          pending.set(msgId, { res, rej });
          ws.send(JSON.stringify({ id: msgId, method, params }));
        });
      resolve({ ws, call });
    };
    ws.onerror = (e) => reject(new Error('WS error: ' + (e?.message ?? 'unknown')));
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && pending.has(msg.id)) {
        const { res, rej } = pending.get(msg.id);
        pending.delete(msg.id);
        msg.error ? rej(new Error(msg.error.message)) : res(msg.result);
      }
    };
  });
}

async function evaluate(client, expression) {
  const r = await client.call('Runtime.evaluate', {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  if (r.exceptionDetails) {
    return { __error: r.exceptionDetails.exception?.description ?? r.exceptionDetails.text };
  }
  return r.result?.value;
}

async function main() {
  const action = process.argv[2] ?? 'ui';
  const targets = await getTargets();
  // 主窗口 = 非 detached 路由的 page target（detached 为独立窗口视图，无输入框）
  const mainPage = targets.find((t) => t.type === 'page' && !t.url.includes('#/detached/'))
    ?? targets.find((t) => t.type === 'page');
  if (!mainPage) {
    console.log('未找到 page 类型 target，当前 targets:', JSON.stringify(targets, null, 2));
    return;
  }
  // chat 动作：优先连接带输入框的窗口（chat 面板可能已在独立窗口中）
  const chatTarget = action === 'chat'
    ? (targets.find((t) => t.type === 'page' && t.url.includes('#/detached/chat')) ?? mainPage)
    : mainPage;
  console.log(`[target] 主窗口: ${mainPage.title} | ${mainPage.url}`);
  const client = await connectWs(chatTarget.webSocketDebuggerUrl);
  if (action === 'chat') console.log(`[chat] 使用窗口: ${chatTarget.url}`);

  if (action === 'ui') {
    const ui = await evaluate(client, `(() => {
      const q = (s) => document.querySelector(s);
      const title = document.title;
      const appMode = [...document.querySelectorAll('span,div')]
        .map(e=>e.textContent?.trim()).find(t=>t==='桌面模式'||t==='Web 模式') ?? null;
      const panels = [...document.querySelectorAll('.mosaic-window-title')].map(e=>e.textContent?.trim());
      const detachBtns = [...document.querySelectorAll('button')].filter(b=>b.textContent?.includes('独立窗口')).length;
      const input = q('textarea, input[type="text"]');
      const hasElectronAPI = !!window.electronAPI;
      const apiKeys = hasElectronAPI ? Object.keys(window.electronAPI) : [];
      const bodySnippet = document.body?.innerText?.slice(0, 300);
      return { title, appMode, panels, detachBtns, hasElectronAPI, apiKeys, bodySnippet };
    })()`);
    console.log('[UI 快照]', JSON.stringify(ui, null, 2));
    return;
  }

  if (action === 'detach') {
    const before = (await getTargets()).length;
    // 默认分离 chat 面板（可传参数指定 panelId，如 nav/think）
    const want = process.argv[3] ?? 'chat';
    const clickResult = await evaluate(client, `(() => {
      // 点击对应面板标题栏内/附近的"独立窗口"按钮
      const titles = [...document.querySelectorAll('.mosaic-window-title')];
      const idx = titles.findIndex(e => e.textContent?.includes(${JSON.stringify('对话')}));
      const btn = [...document.querySelectorAll('button')].find((b, i) =>
        b.textContent?.includes('独立窗口') && (!idx || i > 0));
      // 通用：找标题含"对话"的面板所在容器内的按钮
      const panels = [...document.querySelectorAll('.mosaic-window')];
      const target = panels.find(p => p.querySelector('.mosaic-window-title')?.textContent?.includes(${JSON.stringify('对话')}))
        ?? panels.find(p => p.querySelector('button')?.textContent?.includes('独立窗口'));
      const inner = target
        ? [...target.querySelectorAll('button')].find(b => b.textContent?.includes('独立窗口'))
        : [...document.querySelectorAll('button')].find(b => b.textContent?.includes('独立窗口'));
      if (!inner) return { ok:false, reason:'未找到独立窗口按钮' };
      inner.click();
      return { ok:true, label: inner.textContent?.trim(), detachedPanel: ${JSON.stringify(want)} };
    })()`);
    console.log('[点击]', JSON.stringify(clickResult));
    // 等待主进程建窗 + renderer 加载
    await new Promise((r) => setTimeout(r, 4000));
    const after = await getTargets();
    console.log(`[targets] 点击前 ${before} 个 → 点击后 ${after.length} 个`);
    for (const t of after) console.log(`  - ${t.type}: ${t.title} | ${t.url}`);
    return;
  }

  if (action === 'chat') {
    // 输入消息并发送（发送按钮为图标按钮，用 Enter 触发），随后轮询 UI 状态
    const sent = await evaluate(client, `(() => {
      const input = document.querySelector('textarea');
      if (!input) return { ok:false, reason:'未找到输入框' };
      const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value')?.set;
      setter.call(input, '你好，请介绍一下你自己');
      input.dispatchEvent(new Event('input', { bubbles: true }));
      const sendBtn = [...document.querySelectorAll('button')].find(b=>b.getAttribute('title')==='发送');
      if (!sendBtn) return { ok:false, reason:'未找到发送按钮' };
      sendBtn.click();
      return { ok:true };
    })()`);
    console.log('[发送]', JSON.stringify(sent));

    for (let i = 1; i <= 6; i++) {
      await new Promise((r) => setTimeout(r, 2000));
      const state = await evaluate(client, `(() => {
        const msgs = [...document.querySelectorAll('.wb-msg')].map(e=>e.textContent?.trim()).filter(Boolean);
        const errs = [...document.querySelectorAll('*')].map(e=>e.textContent).filter(t=>t && (t.includes('❌')||t.includes('SSE 请求失败')||t.includes('Failed to fetch')||t.includes('error'))).slice(0,3);
        const thinking = [...document.querySelectorAll('*')].map(e=>e.textContent).filter(t=>t&&t.includes('意图识别')).slice(0,1);
        return { msgCount: msgs.length, lastMsg: msgs[msgs.length-1]?.slice(0,120), errs: errs.map(x=>x.slice(0,120)), thinking: thinking.map(x=>x.slice(0,80)) };
      })()`);
      console.log(`[${i*2}s]`, JSON.stringify(state));
    }
    return;
  }

  console.log('未知 action:', action);
}

main().catch((e) => {
  console.error('脚本失败:', e);
  process.exit(1);
});
