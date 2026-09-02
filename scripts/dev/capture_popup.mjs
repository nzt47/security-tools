#!/usr/bin/env node
/**
 * 弹窗捕获器：MutationObserver 记录任何新插入到页面的浮层元素（fixed/高 z/常见弹窗类），
 * 即使自动秒关也能抓取其 class 与文本。用户正常操作页面 N 秒后导出捕获列表。
 */
import { spawn } from 'node:child_process';
import http from 'node:http';
import net from 'node:net';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const URL = process.argv[2] || 'http://127.0.0.1:5678/chat#/workbench';
const WATCH_MS = Number(process.argv[3]) || 180000; // 观察时长
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const getJSON = (u) => new Promise((res, rej) => {
  http.get(u, (r) => { let d = ''; r.on('data', (c) => (d += c)); r.on('end', () => { try { res(JSON.parse(d)); } catch (e) { rej(e); } }); }).on('error', rej);
});
const freePort = () => new Promise((res, rej) => {
  const srv = net.createServer();
  srv.listen(0, '127.0.0.1', () => { const p = srv.address().port; srv.close(() => res(p)); });
  srv.on('error', rej);
});

async function main() {
  const edgeBin = ['C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'].find((p) => existsSync(p));
  console.log('URL:', URL, '| watch(ms):', WATCH_MS);
  if (!edgeBin) return;
  const profile = mkdtempSync(join(tmpdir(), 'capture-popup-'));
  const port = await freePort();
  const proc = spawn(edgeBin, ['--disable-gpu', '--no-sandbox', `--user-data-dir=${profile}`, `--remote-debugging-port=${port}`, '--remote-allow-origins=*', URL], { stdio: 'ignore', windowsHide: false });
  try {
    let target = null;
    const dl = Date.now() + 60000;
    while (Date.now() < dl && !target) {
      try { const l = await getJSON(`http://127.0.0.1:${port}/json/list`); target = l.find((t) => t.type === 'page'); } catch {}
      if (!target) await sleep(400);
    }
    if (!target) { console.log('CDP FAIL'); return; }
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((r, j) => { ws.onopen = r; ws.onerror = () => j(new Error('ws')); });
    let id = 0;
    const pending = new Map();
    const consoleLogs = [];
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
      if (m.method === 'Runtime.consoleAPICalled') {
        const txt = (m.params.args || []).map((a) => a.value ?? a.description ?? '').join(' ').slice(0, 160);
        if (txt) consoleLogs.push(`[${m.params.type}] ${txt}`);
      }
    };
    const send = (method, params = {}) => new Promise((r) => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
    await send('Runtime.enable');
    const ev = async (expr) => (await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true })).result?.result?.value;
    await sleep(4000);
    // 注入捕获器：记录插入到 body 的 fixed / zIndex 高 / 典型弹窗类元素
    const installed = await ev(`(()=>{
      window.__popupCap = [];
      const seen = new Set();
      const sniff = (el, why) => {
        try {
          const cs = window.getComputedStyle(el);
          const cls = String(el.className || '').slice(0, 100);
          const txt = (el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 200);
          const r = el.getBoundingClientRect();
          const rec = { t: new Date().toLocaleTimeString(), why, cls, txt, pos: cs.position, z: cs.zIndex, w: Math.round(r.width), h: Math.round(r.height) };
          window.__popupCap.push(rec);
        } catch {}
      };
      const mo = new MutationObserver((muts) => {
        for (const m of muts) {
          for (const n of m.addedNodes) {
            if (n.nodeType !== 1) continue;
            const el = n;
            const cls = String(el.className || '');
            const cs = window.getComputedStyle(el);
            const z = Number(cs.zIndex || 0);
            const pos = cs.position;
            if (pos === 'fixed' || pos === 'absolute') {
              if (el.offsetWidth > 20 && el.offsetHeight > 20) sniff(el, pos + '/z' + cs.zIndex);
            } else if (/toast|notif|popup|modal|dialog|overlay|alert|tip|badge|hint/i.test(cls)) {
              sniff(el, 'class:' + cls.slice(0, 40));
            }
            // 也捕获子树中的关键层（部分实现挂在已有容器内）
            if (el.querySelectorAll) {
              el.querySelectorAll('div[style*="fixed"], [class*="toast" i], [class*="notif" i], [class*="popup" i], [class*="modal" i], [class*="dialog" i]').forEach((sub) => {
                if (!seen.has(sub)) { seen.add(sub); sniff(sub, 'sub'); }
              });
            }
          }
        }
      });
      mo.observe(document.body, { childList: true, subtree: true });
      window.__popupMo = mo;
      return true;
    })()`);
    console.log('capture installed:', installed);
    console.log(`请在此窗口对应页面正常操作 ${Math.round(WATCH_MS / 1000)} 秒（触发弹窗的场景）...`);
    await sleep(WATCH_MS);
    const cap = await ev(`JSON.stringify(window.__popupCap || [])`);
    console.log('=== 捕获的浮层元素 ===');
    const list = JSON.parse(cap || '[]');
    if (list.length === 0) console.log('(无捕获——观察期内未出现浮层)');
    list.forEach((r, i) => console.log(`[${i}] ${r.t} why=${r.why} pos=${r.pos} z=${r.z} ${r.w}x${r.h}\n    class: ${r.cls}\n    text: ${r.txt}`));
    console.log('=== 页面 console 输出（尾部 25 条）===');
    consoleLogs.slice(-25).forEach((c) => console.log(c));
  } finally {
    try { proc.kill(); } catch {}
    try { rmSync(profile, { recursive: true, force: true }); } catch {}
  }
}
main().catch((e) => { console.error(e); process.exit(1); });
