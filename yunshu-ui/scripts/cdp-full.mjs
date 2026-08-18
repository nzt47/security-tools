/**
 * CDP 综合验证 —— 安装版：detach chat 面板 + 流式对话 + 跨窗口同步
 * 用法：node scripts/cdp-full.mjs
 * 前提：云枢.exe 已带 --remote-debugging-port=9222 启动（干净状态）
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
    ws.onerror = (e) => { clearTimeout(timer); reject(new Error('WS 连接失败')); };
  });
}

async function main() {
  // 0. 目标列表
  let targets = await getTargets();
  const main = targets.find((t) => t.type === 'page' && !t.url.includes('#/detached/'));
  if (!main) { console.log('❌ 未找到主窗口'); process.exit(1); }
  console.log('✅ 主窗口:', main.url);

  // 1. 点击 chat 面板的"独立窗口"按钮（DOM 模拟）
  const det = await evalIn(main.webSocketDebuggerUrl, `(() => {
    const panels = [...document.querySelectorAll('.mosaic-window')];
    const chatPanel = panels.find(p => p.querySelector('.mosaic-window-title')?.textContent?.includes('对话'))
      ?? panels[0];
    const btn = chatPanel
      ? [...chatPanel.querySelectorAll('button')].find(b => b.textContent?.includes('独立窗口'))
      : null;
    if (!btn) return { ok:false, reason:'未找到 chat 面板的独立窗口按钮' };
    btn.click();
    return { ok:true, panelTitle: chatPanel.querySelector('.mosaic-window-title')?.textContent?.trim() };
  })()`);
  console.log('🎯 点击独立窗口:', JSON.stringify(det));

  // 2. 等待新窗口出现
  let detached = null;
  for (let i = 0; i < 10; i++) {
    await sleep(1000);
    targets = await getTargets();
    detached = targets.find((t) => t.type === 'page' && t.url.includes('#/detached/chat'));
    if (detached) break;
  }
  if (!detached) {
    console.log('❌ 独立窗口未创建。当前 targets:', targets.map((t) => t.url).join(' | '));
    process.exit(1);
  }
  console.log('✅ 独立窗口已创建（恰好 1 个）:', detached.url);
  console.log('   窗口总数:', targets.length, '（应=2：主 + 1 独立）');

  // 3. 主窗口发送消息（SSE 流式）
  const sent = await evalIn(main.webSocketDebuggerUrl, `(() => {
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
  console.log('📤 发送消息:', JSON.stringify(sent));

  // 4. 轮询主窗口直到流式完成（有 done 消息/字符数稳定）
  let mainDone = false;
  for (let i = 0; i < 15; i++) {
    await sleep(2000);
    const st = await evalIn(main.webSocketDebuggerUrl, `(() => {
      const msgs = [...document.querySelectorAll('.wb-msg')].map(e=>e.textContent?.trim()).filter(Boolean);
      return { count: msgs.length, lastLen: msgs[msgs.length-1]?.length ?? 0, last: msgs[msgs.length-1]?.slice(0,60) };
    })()`);
    console.log(`  [${(i+1)*2}s] 主窗口: count=${st.count} lastLen=${st.lastLen}`);
    if (st.count >= 2 && st.lastLen > 200) { mainDone = true; break; }
  }
  if (!mainDone) console.log('⚠ 主窗口流式可能未完成，继续验证同步');

  // 5. 对比独立窗口（应同步收到消息）
  await sleep(1000);
  const syncMain = await evalIn(main.webSocketDebuggerUrl, `(() => {
    const msgs = [...document.querySelectorAll('.wb-msg')].map(e=>e.textContent?.trim()).filter(Boolean);
    return { count: msgs.length, last: msgs[msgs.length-1]?.slice(0,100) };
  })()`);
  const syncDet = await evalIn(detached.webSocketDebuggerUrl, `(() => {
    const msgs = [...document.querySelectorAll('.wb-msg')].map(e=>e.textContent?.trim()).filter(Boolean);
    return { count: msgs.length, last: msgs[msgs.length-1]?.slice(0,100) };
  })()`);
  console.log('\n=== 跨窗口同步对比 ===');
  console.log('主窗口  :', JSON.stringify(syncMain));
  console.log('独立窗口:', JSON.stringify(syncDet));
  const same = syncMain.count === syncDet.count && syncMain.last === syncDet.last;
  console.log(same ? '✅ 主 ↔ 独立窗口消息同步一致' : '⚠ 不一致（独立窗口无消息或未同步）');

  // 6. 主窗口布局应已摘除 chat 面板
  const layout = await evalIn(main.webSocketDebuggerUrl, `(() => {
    const titles = [...document.querySelectorAll('.mosaic-window-title')].map(e=>e.textContent?.trim());
    return { panelTitles: titles };
  })()`);
  console.log('主窗口剩余面板:', JSON.stringify(layout));
  console.log(layout.panelTitles.some((t) => t?.includes('对话')) ? '⚠ chat 面板仍在主窗口（摘除失败）' : '✅ chat 面板已从主窗口摘除');

  process.exit(0);
}

main().catch((e) => { console.error('❌ 脚本失败:', e.message); process.exit(1); });
