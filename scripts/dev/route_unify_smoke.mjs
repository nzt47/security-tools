#!/usr/bin/env node
/**
 * 路由收敛浏览器冒烟（P2 · 摘除第二套管理后台外壳 验证）
 * ============================================================================
 * 用真实 Edge/Chrome 无头 + CDP 验证「第二套管理后台外壳摘除」后的验收项：
 *   - 旧管理后台路径（#/dashboard、#/demo、#/export、#/system/*、#/knowledge）不再
 *     渲染影子后台：经兜底重定向到 #/workbench（非白屏）
 *   - /login 保留：登录表单可渲染（AdminGuard / 401 拦截器依赖）
 *   - /403 保留：手输可访问
 *   - 统一工作台可用：导航含「系统管理」栏目，展开后含「数据导出」子项，
 *     点击后主内容区渲染导出页（页头 + 格式选择 + 导出按钮）
 *
 * 前置条件：
 *   - 已构建前端产物（yunshu-ui/dist，`npm run build`），经 vite preview 或任意
 *     静态服务器托起（HashRouter 无需 history fallback）。
 *   - 本机装有 Edge 或 Chrome（EDGE_PATH / CHROME_PATH 或 --edge 指定）。
 *
 * 用法：
 *   node scripts/dev/route_unify_smoke.mjs --url http://127.0.0.1:4321/#/workbench
 *   # 或指定浏览器：
 *   node scripts/dev/route_unify_smoke.mjs --edge "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"
 *
 * 退出码：全部 [PASS] → 0；任一 [FAIL] → 1（便于 CI 门禁）。
 */

import { spawn } from 'node:child_process';
import http from 'node:http';
import net from 'node:net';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

// ═══ 小工具 ═══
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

// ═══ 参数与浏览器探测 ═══
function parseArgs(argv) {
  const args = {
    url: 'http://127.0.0.1:4321/#/workbench',
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

// ═══ 主流程 ═══
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

  console.log('=== 路由收敛浏览器冒烟（摘除第二套管理后台外壳）===');
  console.log(`  URL   : ${args.url}`);
  console.log(`  浏览器: ${edgeBin ?? '(未找到，用 EDGE_PATH/CHROME_PATH 或 --edge 指定)'}`);

  check('找到 Edge/Chrome 可执行文件', Boolean(edgeBin));
  if (!edgeBin) return;

  const profile = mkdtempSync(join(tmpdir(), 'route-smoke-'));
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
    // ── 1. 启动浏览器并拿到页面 target ──
    let target = null;
    const bootDeadline = Date.now() + 60_000;
    while (Date.now() < bootDeadline && !target) {
      try {
        const list = await getJSON(`http://127.0.0.1:${cdpPort}/json/list`);
        target = list.find((t) => t.type === 'page');
      } catch { /* CDP 尚未就绪 */ }
      if (!target) await sleep(500);
    }
    check('无头浏览器启动且 CDP 可达', Boolean(target), '见上方浏览器探测结果');
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
    /** 当前页面 hash（不含 # 前缀） */
    const hashExpr = `(location.hash || '').replace(/^#/, '') || '/'`;
    /** 设置 hash 并等待其落在目标路径（精确匹配或为目标路径的子路径） */
    const setAndWaitHash = async (path, timeoutMs = 15_000) => {
      await ev(`location.hash = ${JSON.stringify('#' + path)}`);
      return waitFor(
        `(${hashExpr}) === ${JSON.stringify(path)} || (${hashExpr}).startsWith(${JSON.stringify(path + '/')})`,
        timeoutMs,
      );
    };
    /** 等待重定向落地统一工作台（#/workbench 或其子路径） */
    const waitWorkbench = (timeoutMs = 20_000) =>
      waitFor(
        `(${hashExpr}) === '/workbench' || (${hashExpr}).startsWith('/workbench/')`,
        timeoutMs,
      );
    const clickByExactText = (label) =>
      ev(`(()=>{const bs=[...document.querySelectorAll('button')];const b=bs.find(x=>x.textContent.trim()===${JSON.stringify(label)});if(b){b.click();return true}return false})()`);

    // ── 2. 统一工作台可启动（导航含「系统管理」栏目）──
    check('工作台 SPA 启动（导航含「系统管理」）',
      await waitFor(`document.body && document.body.innerText.includes('系统管理')`, 40_000));

    // ── 3. 旧管理后台路径 → 兜底重定向回统一工作台（非白屏）──
    for (const legacy of ['/dashboard', '/demo', '/export', '/system/user', '/system/log', '/knowledge']) {
      await ev(`location.hash = ${JSON.stringify('#' + legacy)}`);
      const landed = await waitWorkbench();
      const hashNow = await ev(hashExpr);
      const hasNav = await ev(`document.body.innerText.includes('系统管理')`);
      check(`旧路径 #${legacy} → 重定向 /workbench（非白屏）`,
        landed && hasNav,
        `landed=${landed} hash=${hashNow} hasNav=${hasNav}`);
    }

    // ── 4. /login 保留：登录表单可渲染 ──
    check('跳转 #/login', await setAndWaitHash('/login'));
    check('登录表单渲染（欢迎回来 / 用户名 / 密码）',
      await waitFor(
        `document.body.innerText.includes('欢迎回来') && !!document.querySelector('input[placeholder="请输入用户名"]') && !!document.querySelector('input[placeholder="请输入密码"]')`,
        15_000,
      ));

    // ── 5. /403 保留：手输可访问 ──
    check('跳转 #/403', await setAndWaitHash('/403'));
    check('403 页渲染（无权访问）',
      await waitFor(`document.body.innerText.includes('403') && document.body.innerText.includes('无权访问')`, 15_000));

    // ── 6. 工作台 admin 栏目：展开「系统管理」→ 点击「数据导出」→ 渲染导出页 ──
    check('回到 #/workbench', await setAndWaitHash('/workbench', 20_000));
    // 注意：NavPanel 的深度 0 分组默认展开；「系统管理」组若已展开则无需点击（点击会收起）
    const adminGroupOpen = await ev(`document.body.innerText.includes('数据导出')`);
    check('展开「系统管理」栏目（已展开则跳过）',
      adminGroupOpen ? true : (await clickByExactText('系统管理')));
    check('子项「数据导出」出现', await waitFor(`document.body.innerText.includes('数据导出')`, 10_000));
    check('点击「数据导出」', await clickByExactText('数据导出'));
    check('导出页在主内容区渲染（导出格式 / CSV / JSON / 刷新）',
      await waitFor(
        `document.body.innerText.includes('导出格式') && document.body.innerText.includes('CSV') && document.body.innerText.includes('JSON') && document.body.innerText.includes('刷新')`,
        20_000,
      ));
    // 系统管理栏目其余子项仍在导航（零损失收敛）
    const adminItems = await ev(
      `['用户列表','角色权限','菜单管理','操作审计','消息中心','系统日志'].filter(x=>document.body.innerText.includes(x)).join(',')`,
    );
    check('admin 栏目既有 6 子项仍在导航（零损失）',
      (adminItems || '').split(',').length === 6,
      `found=${adminItems}`);

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
