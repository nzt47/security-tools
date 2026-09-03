#!/usr/bin/env node
/** Sentry E2E：打开 5678 → 触发未捕获错误 → Sentry(Sentry.init) 应自动捕获并上报 */
import { spawn } from 'node:child_process';
import http from 'node:http';
import net from 'node:net';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const URL = process.argv[2] || 'http://127.0.0.1:5678/chat#/workbench';
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
    'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe'].find((p) => existsSync(p));
  console.log('URL:', URL);
  const profile = mkdtempSync(join(tmpdir(), 'sentry-e2e-'));
  const port = await freePort();
  const proc = spawn(edgeBin, ['--headless=new', '--disable-gpu', '--no-sandbox', `--user-data-dir=${profile}`, `--remote-debugging-port=${port}`, '--remote-allow-origins=*', URL], { stdio: 'ignore', windowsHide: true });
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
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
    };
    const send = (method, params = {}) => new Promise((r) => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
    const ev = async (expr) => (await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true })).result?.result?.value;
    // 等待工作台加载（Sentry.init 在 main 执行）
    let loaded = false;
    for (let i = 0; i < 30; i++) { loaded = await ev(`!!document.querySelector('.wb-topbar')`); if (loaded) break; await sleep(1000); }
    console.log('workbench loaded:', loaded);
    // 检查 Sentry 是否初始化（_initialized → captureMessage 不抛且 DSN 配置）
    const sentryOk = await ev(`(typeof __SENTRY__ !== 'undefined') || !!window.Sentry`);
    console.log('sentry global present:', sentryOk);
    await sleep(1500);
    // 触发未捕获错误（setTimeout 抛 → window.onerror → Sentry captureException）
    const thrown = await ev(`(()=>{setTimeout(()=>{throw new Error('SENTRY_E2E_TEST_' + Date.now())}, 100); return true})()`);
    console.log('error thrown:', thrown);
    // 触发一次 unhandledrejection（Promise.reject 无处理 → Sentry 捕获）
    await ev(`(()=>{setTimeout(()=>{Promise.reject(new Error('SENTRY_E2E_REJECT_' + Date.now()))}, 300); return true})()`);
    console.log('waiting 12s for sendBeacon/fetch upload...');
    await sleep(12000);
    console.log('done; check glitchtip Issue/IssueEvent count delta');
  } finally {
    try { proc.kill(); } catch {}
    try { rmSync(profile, { recursive: true, force: true }); } catch {}
  }
}
main().catch((e) => { console.error(e); process.exit(1); });
