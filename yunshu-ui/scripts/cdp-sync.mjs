/**
 * CDP 验证 —— 独立窗口同步（安装版实测）
 * 用法：node scripts/cdp-sync.mjs
 * 前提：主窗口已 detach 出 #/detached/<panelId>，且主窗口已发过消息
 */
const CDP = 'http://127.0.0.1:9222';
const res = await fetch(`${CDP}/json/list`);
const targets = await res.json();

console.log('=== 当前窗口列表 ===');
for (const t of targets) console.log(`  - ${t.type}: ${t.url}`);

const main = targets.find((t) => t.type === 'page' && !t.url.includes('#/detached/'));
const detached = targets.find((t) => t.type === 'page' && t.url.includes('#/detached/'));

if (!main || !detached) {
  console.log('缺少主窗口或独立窗口，请先执行 detach 验证');
  process.exit(1);
}

async function evalIn(wsUrl, expression) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    let id = 0;
    ws.onopen = () => {
      const i = ++id;
      ws.send(JSON.stringify({ id: i, method: 'Runtime.evaluate', params: { expression, returnByValue: true, awaitPromise: true } }));
      ws.onmessage = (ev) => {
        const d = JSON.parse(ev.data);
        if (d.id === i) {
          ws.close();
          d.result?.exceptionDetails ? reject(new Error(d.result.exceptionDetails.text)) : resolve(d.result?.result?.value);
        }
      };
    };
    ws.onerror = (e) => reject(e);
  });
}

const Q = `(()=>{const msgs=[...document.querySelectorAll('.wb-msg')].map(e=>e.textContent?.trim()).filter(Boolean); return {count:msgs.length, last:msgs[msgs.length-1]?.slice(0,120), role: msgs.length>=2 ? msgs[msgs.length-2]?.slice(0,60) : null}})()`;

const mainState = await evalIn(main.webSocketDebuggerUrl, Q);
const detState = await evalIn(detached.webSocketDebuggerUrl, Q);

console.log('\n=== 同步对比 ===');
console.log('主窗口    :', JSON.stringify(mainState));
console.log('独立窗口  :', JSON.stringify(detState));
console.log('\n结论:', JSON.stringify(mainState) === JSON.stringify(detState) ? '✅ 双向一致（同步正常）' : '⚠ 不一致，需人工确认');
