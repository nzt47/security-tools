#!/usr/bin/env node
/**
 * 登录流程浏览器冒烟（P2 · 摘除第二套管理后台外壳 验证）
 * ============================================================================
 * 验证「/login 保留且登录流程可用」：
 *   - #/login 渲染登录表单
 *   - 输入 admin / 123456 点击登录（走 devMock /api/auth/login）
 *   - 成功后 token 双写（localStorage + store），落地页 = /（重定向 → /workbench）
 *   - 工作台导航正常（含「系统管理」）
 *
 * 前置条件：
 *   - dev server 需开启管理 API Mock：VITE_MOCK_API=true npm run dev
 *     （vite.config.ts：mockApiPlugin 在 VITE_MOCK_API=true 时启用，拦截 /api/auth/login、
 *     /api/user/info，不再转发后端 5678）
 *   - 本机装有 Edge 或 Chrome（EDGE_PATH / CHROME_PATH 或 --edge 指定）。
 *
 * 用法：
 *   node scripts/dev/login_flow_smoke.mjs --url http://127.0.0.1:5173/static/#/login
 *
 * 退出码：全部 [PASS] → 0；任一 [FAIL] → 1。
 */

import { spawn } from 'node:child_process';
import http from 'node:http';
import net from 'node:net';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function getJSON(url) {
  return new Promise((resolve, reject) => {
    http
      .get(url, (res) => {
        let data = '';
        res.on('data', (c) => (data += c));
        res.on('end', () => {
          try { resolve(JSON.parse(data)); } catch (e) { reject(e); }
        });
      })
      .on('error', reject);
  });
}

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.listen(0, '127.0.0.1', () => {
      const port = srv.address().port;
      srv.close(() => resolve(port));
    });
    srv.on('error', reject);
  });
}

function parseArgs(argv) {
  const args = {
    url: 'http://127.0.0.1:5173/static/#/login',
    edge: process.env.EDGE_PATH || process.env.CHROME_PATH || null,
    timeoutMs: 90_000,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const v = argv[i];
    if (v === '--url') args.url = argv[++i] ?? args.url;
    else if (v === '--edge') args.edge = argv[++i] ?? args.edge;
    else if (v === '--timeout') args.timeoutMs = Number(argv[++i]) * 1000 || args.timeoutMs;
  }
  return args;
}

function resolveBrowser(explicit) {
  const candidates = [
    explicit,
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  ].filter(Boolean);
  return candidates.find((p) => existsSync(p)) ?? null;
}

let PASS = 0;
let FAIL = 0;
let ws;

function check(name, cond, detail = '') {
  if (cond) {
    PASS += 1;
    console.log(`  [PASS] ${name}`);
  } else {
    FAIL += 1;
    console.log(`  [FAIL] ${name}${detail ? ` — ${detail}` : ''}`);
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const edgeBin = resolveBrowser(args.edge);

  console.log('=== 登录流程浏览器冒烟（/login 保留 + 登录落地工作台）===');
  console.log(`  URL   : ${args.url}`);
  console.log(`  浏览器: ${edgeBin ?? '(未找到，用 EDGE_PATH/CHROME_PATH 或 --edge 指定)'}`);

  check('找到 Edge/Chrome 可执行文件', Boolean(edgeBin));
  if (!edgeBin) return;

  const profile = mkdtempSync(join(tmpdir(), 'login-smoke-'));
  const cdpPort = await freePort();
  const proc = spawn(
    edgeBin,
    [
      '--headless=new', '--disable-gpu', '--no-sandbox',
      `--user-data-dir=${profile}`,
      `--remote-debugging-port=${cdpPort}`,
      '--remote-allow-origins=*',
      args.url,
    ],
    { stdio: 'ignore', windowsHide: true },
  );

  try {
    let target = null;
    const bootDeadline = Date.now() + 60_000;
    while (Date.now() < bootDeadline && !target) {
      try {
        const list = await getJSON(`http://127.0.0.1:${cdpPort}/json/list`);
        target = list.find((t) => t.type === 'page');
      } catch { /* CDP 尚未就绪 */ }
      if (!target) await sleep(500);
    }
    check('无头浏览器启动且 CDP 可达', Boolean(target));
    if (!target) return;

    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      ws.onopen = resolve;
      ws.onerror = () => reject(new Error('WebSocket 连接失败'));
    });

    let msgId = 0;
    const pending = new Map();
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.id && pending.has(m.id)) {
        pending.get(m.id)(m);
        pending.delete(m.id);
      }
    };
    const send = (method, params = {}) =>
      new Promise((resolve) => {
        const id = ++msgId;
        pending.set(id, resolve);
        ws.send(JSON.stringify({ id, method, params }));
      });
    const ev = async (expression) => {
      const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
      if (r.result && r.result.exceptionDetails) {
        throw new Error('页面脚本执行失败: ' + JSON.stringify(r.result.exceptionDetails).slice(0, 300));
      }
      return r.result.result.value;
    };
    const waitFor = async (expression, timeoutMs) => {
      const deadline = Date.now() + timeoutMs;
      while (Date.now() < deadline) {
        try {
          if (await ev(expression)) return true;
        } catch { /* 页面尚未就绪，重试 */ }
        await sleep(350);
      }
      return false;
    };
    const clickByExactText = (label) =>
      ev(`(()=>{const bs=[...document.querySelectorAll('button')];const b=bs.find(x=>x.textContent.trim()===${JSON.stringify(label)});if(b){b.click();return true}return false})()`);
    /** 以原生 setter 填值（React 受控组件需触发 input 事件） */
    const fillInput = (selector, value) =>
      ev(`(()=>{const el=document.querySelector(${JSON.stringify(selector)});if(!el)return false;const proto=el.tagName==='TEXTAREA'?window.HTMLTextAreaElement.prototype:window.HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(proto,'value').set.call(el,${JSON.stringify(value)});el.dispatchEvent(new Event('input',{bubbles:true}));return true})()`);

    const hashExpr = `(location.hash || '').replace(/^#/, '') || '/'`;

    // ── 1. 登录页渲染 ──
    check('登录页渲染（欢迎回来 / 用户名 / 密码）',
      await waitFor(
        `document.body.innerText.includes('欢迎回来') && !!document.querySelector('input[placeholder="请输入用户名"]')`,
        40_000,
      ));

    // ── 2. 填写 admin / 123456 并登录 ──
    check('填写用户名 admin', await fillInput('input[placeholder="请输入用户名"]', 'admin'));
    check('填写密码 123456', await fillInput('input[placeholder="请输入密码"]', '123456'));
    await sleep(200);
    check('点击「登录」按钮', await clickByExactText('登录'));

    // ── 3. 登录成功 → 落地 /workbench（token 已写入 localStorage）──
    const landed = await waitFor(
      `(${hashExpr}).startsWith('/workbench') && document.body.innerText.includes('系统管理')`,
      25_000,
    );
    const hashNow = await ev(hashExpr);
    const tokenOk = await ev(`(()=>{try{const t=localStorage.getItem('token');return !!t && t.startsWith('mock-token-')}catch{return false}})()`);
    check('登录成功：落地 #/workbench 且导航可用', landed, `hash=${hashNow}`);
    check('token 已写入 localStorage（mock-token-*）', Boolean(tokenOk));

    console.log('=== 冒烟结束 ===');
  } catch (e) {
    check('冒烟过程无异常', false, e.message);
  } finally {
    try { if (ws) ws.close(); } catch { /* ignore */ }
    try { proc.kill(); } catch { /* ignore */ }
    try { rmSync(profile, { recursive: true, force: true }); } catch { /* ignore */ }
  }

  console.log(`结果: [PASS] ${PASS}  [FAIL] ${FAIL}`);
}

await main();
process.exit(FAIL > 0 ? 1 : 0);
