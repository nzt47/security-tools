/**
 * CDP 验证 —— 安装版 CodeEditor 面板：detach 独立窗口 + 内容同步 + localStorage 持久化
 * 用法：node scripts/cdp-editor.mjs
 * 前提：云枢.exe 已带 --remote-debugging-port=9222 启动
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
    const ui = await evalIn(main.webSocketDebuggerUrl, `(() => {
      const titles = [...document.querySelectorAll('.mosaic-window-title')].map(e=>e.textContent?.trim());
      return { titles, hasEditor: titles.some(t=>t?.includes('代码编辑器')) };
    })()`);
    console.log('[重置后]', JSON.stringify(ui));
    return;
  }

  if (step === 'ui') {
    const ui = await evalIn(main.webSocketDebuggerUrl, `(() => {
      const titles = [...document.querySelectorAll('.mosaic-window-title')].map(e=>e.textContent?.trim());
      const hasEditor = titles.some(t=>t?.includes('代码编辑器'));
      const editor = document.querySelector('.wb-editor-input');
      const preview = document.querySelector('.wb-editor-preview');
      const lang = document.querySelector('.wb-editor-lang');
      const ls = localStorage.getItem('yunshu:editor:code:v1');
      return { titles, hasEditor, hasTextarea: !!editor, hasPreview: !!preview, langOptions: lang ? [...lang.options].map(o=>o.textContent) : [], ls };
    })()`);
    console.log('[UI]', JSON.stringify(ui, null, 2));
    return;
  }

  if (step === 'write') {
    // 在编辑器输入自定义内容（写入 localStorage）
    const r = await evalIn(main.webSocketDebuggerUrl, `(() => {
      const input = document.querySelector('.wb-editor-input');
      if (!input) return { ok:false, reason:'未找到编辑器输入框' };
      const code = 'const hub = "yunshu-editor-sync-ok";\\nfunction ping() { return hub; }\\nconsole.log(ping());';
      const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value')?.set;
      setter.call(input, code);
      input.dispatchEvent(new Event('input', { bubbles: true }));
      return { ok:true, len: code.length };
    })()`);
    console.log('[写入]', JSON.stringify(r));
    await sleep(800);
    const ls = await evalIn(main.webSocketDebuggerUrl, `localStorage.getItem('yunshu:editor:code:v1')`);
    console.log('[localStorage]', ls?.slice(0, 140));
    return;
  }

  if (step === 'detach') {
    const before = (await getTargets()).length;
    const r = await evalIn(main.webSocketDebuggerUrl, `(() => {
      const panels = [...document.querySelectorAll('.mosaic-window')];
      const codePanel = panels.find(p => p.querySelector('.mosaic-window-title')?.textContent?.includes('代码编辑器'));
      const btn = codePanel ? [...codePanel.querySelectorAll('button')].find(b=>b.textContent?.includes('独立窗口')) : null;
      if (!btn) return { ok:false, reason:'未找到代码编辑器独立窗口按钮' };
      btn.click();
      return { ok:true };
    })()`);
    console.log('[点击独立窗口]', JSON.stringify(r));
    let detached = null;
    for (let i = 0; i < 10; i++) {
      await sleep(1000);
      const ts = await getTargets();
      detached = ts.find((t) => t.type === 'page' && t.url.includes('#/detached/code'));
      if (detached) break;
    }
    const after = (await getTargets()).length;
    console.log('[targets]', before, '→', after, '| 独立窗口:', detached?.url ?? '未创建');
    if (!detached) process.exit(1);

    // 验证独立窗口编辑器内容与主窗口一致（localStorage 同步）
    await sleep(1500);
    const detState = await evalIn(detached.webSocketDebuggerUrl, `(() => {
      const input = document.querySelector('.wb-editor-input');
      const ls = localStorage.getItem('yunshu:editor:code:v1');
      return { valueLen: input?.value?.length ?? -1, valueHead: input?.value?.slice(0,60), lsHead: ls?.slice(0,60) };
    })()`);
    console.log('[独立窗口编辑器]', JSON.stringify(detState, null, 2));
    return;
  }

  if (step === 'check') {
    const ts = await getTargets();
    for (const t of ts) console.log('TARGET:', t.type, '|', t.url);
    const det = ts.find((x) => x.type === 'page' && x.url.includes('#/detached/code'));
    if (!det) { console.log('未找到 #/detached/code 独立窗口'); process.exit(1); }
    const st = await evalIn(det.webSocketDebuggerUrl, `(()=>{
      const input = document.querySelector('.wb-editor-input');
      const ls = localStorage.getItem('yunshu:editor:code:v1');
      let parsed = null;
      try { parsed = ls ? JSON.parse(ls) : null; } catch {}
      return { valueHead: input?.value?.slice(0,60) ?? null, lsContentHead: parsed?.content?.slice(0,60) ?? null, match: input?.value === parsed?.content };
    })()`);
    console.log('[独立窗口]', JSON.stringify(st, null, 2));
    return;
  }

  if (step === 'close') {
    // 正常退出（触发主进程 app.quit，确保 localStorage 落盘）
    const r = await evalIn(main.webSocketDebuggerUrl, `window.close(); 'close-called'`);
    console.log('[关闭]', r);
    await sleep(3000);
    const ts = await getTargets().catch(() => null);
    console.log('[targets]', ts ? ts.length : 'CDP 已不可达（应用已退出）');
    return;
  }

  console.log('用法: node scripts/cdp-editor.mjs <reset|ui|write|detach|check|close>');
  process.exit(1);
}

main().catch((e) => { console.error('❌ 失败:', e.message); process.exit(1); });
