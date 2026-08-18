/**
 * CDP 验证 —— ThinkingPanel detach 独立窗口 + 思考链实时同步
 * 用法：node scripts/cdp-think.mjs
 * 前提：云枢.exe 已带 --remote-debugging-port=9222 启动（后端 5678 运行中）
 */
const CDP = 'http://127.0.0.1:9222';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function getTargets() {
  return (await fetch(`${CDP}/json/list`)).json();
}

function evalIn(wsUrl, expression, timeoutMs = 8000) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    let id = 0;
    const timer = setTimeout(() => { ws.close(); reject(new Error('evaluate 超时')); }, timeoutMs);
    ws.onopen = () => {
      const i = ++id;
      ws.send(JSON.stringify({ id: i, method: 'Runtime.evaluate', params: { expression, returnByValue: true, awaitPromise: true } }));
      ws.onmessage = (ev) => {
        const d = JSON.parse(ev.data);
        if (d.id === i) {
          clearTimeout(timer);
          ws.close();
          if (d.result?.exceptionDetails) reject(new Error(d.result.exceptionDetails.exception?.description ?? d.result.exceptionDetails.text));
          else resolve(d.result?.result?.value);
        }
      };
    };
    ws.onerror = () => { clearTimeout(timer); reject(new Error('WS 连接失败')); };
  });
}

async function main() {
  const step = process.argv[2] ?? 'all';
  const targets = await getTargets();
  const main = targets.find((t) => t.type === 'page' && !t.url.includes('#/detached/'));
  if (!main) { console.log('❌ 未找到主窗口'); process.exit(1); }
  console.log('✅ 主窗口:', main.url);

  if (step === 'reset') {
    const r = await evalIn(main.webSocketDebuggerUrl, `(() => {
      const btn = [...document.querySelectorAll('button')].find(b=>b.textContent?.includes('重置布局'));
      if (!btn) return { ok:false, reason:'未找到重置布局按钮' };
      btn.click();
      return { ok:true };
    })()`);
    console.log('[重置布局]', JSON.stringify(r));
    await sleep(1200);
    return;
  }

  if (step === 'detach-think') {
    const before = (await getTargets()).length;
    const r = await evalIn(main.webSocketDebuggerUrl, `(() => {
      const panels = [...document.querySelectorAll('.mosaic-window')];
      const thinkPanel = panels.find(p => p.querySelector('.mosaic-window-title')?.textContent?.includes('思考过程'));
      const btn = thinkPanel ? [...thinkPanel.querySelectorAll('button')].find(b=>b.textContent?.includes('独立窗口')) : null;
      if (!btn) return { ok:false, reason:'未找到思考过程独立窗口按钮' };
      btn.click();
      return { ok:true };
    })()`);
    console.log('[点击独立窗口(think)]', JSON.stringify(r));
    let detached = null;
    for (let i = 0; i < 10; i++) {
      await sleep(1000);
      const ts = await getTargets();
      detached = ts.find((t) => t.type === 'page' && t.url.includes('#/detached/think'));
      if (detached) break;
    }
    console.log('[targets]', before, '→', (await getTargets()).length, '| 独立窗口:', detached?.url ?? '未创建');
    return;
  }

  if (step === 'chat') {
    const r = await evalIn(main.webSocketDebuggerUrl, `(() => {
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
    console.log('[发送消息]', JSON.stringify(r));

    // 轮询主窗口 thinking 事件增长
    for (let i = 1; i <= 8; i++) {
      await sleep(2000);
      const st = await evalIn(main.webSocketDebuggerUrl, `(() => {
        const nodes = [...document.querySelectorAll('.wb-think-node')].map(e=>e.textContent?.trim());
        return { count: nodes.length, last: nodes[nodes.length-1]?.slice(0,60) };
      })()`);
      console.log(`  [${i*2}s] 主窗口思考链: count=${st.count} last=${st.last ?? ''}`);
      if (st.count >= 4) break;
    }
    return;
  }

  if (step === 'sync') {
    const ts = await getTargets();
    const det = ts.find((t) => t.type === 'page' && t.url.includes('#/detached/think'));
    if (!det) { console.log('❌ 未找到 #/detached/think 独立窗口'); process.exit(1); }
    const detState = await evalIn(det.webSocketDebuggerUrl, `(() => {
      const nodes = [...document.querySelectorAll('.wb-think-node')].map(e=>e.textContent?.trim());
      const titles = [...document.querySelectorAll('.mosaic-window-title')].map(e=>e.textContent?.trim());
      return { count: nodes.length, nodes: nodes.slice(0,4), topbar: document.querySelector('.wb-brand-title')?.textContent };
    })()`);
    console.log('[独立窗口思考链]', JSON.stringify(detState, null, 2));
    return;
  }

  console.log('用法: node scripts/cdp-think.mjs <reset|detach-think|chat|sync>');
  process.exit(1);
}

main().catch((e) => { console.error('❌ 失败:', e.message); process.exit(1); });
