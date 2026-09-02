#!/usr/bin/env node
/**
 * 工作台「知识库系统」页浏览器冒烟（P1-1 迁移验证 · 真实 Edge/Chrome 无头 + CDP 点击）
 * ============================================================================
 * 用真实浏览器走通统一工作台的完整知识库链路，验证「完整版迁入工作台」的验收项：
 *   - 工作台导航：记忆管理 → 知识库系统（ContentPanel 渲染）
 *   - 只读统计行：卡片总数 / 健康分 / 图谱节点 / 图谱连线（/cards + /lint + /graph）
 *   - 融合检索 tab：输入问题 → POST /api/knowledge/query → 命中（含状态角标/来源/score）或空态
 *   - 健康巡检 tab：/api/knowledge/lint 健康分报告渲染
 *   - 列表与统计 tab：行点击 → 详情抽屉（入链/出链，复用 legacy CardDetail）
 *   - 新建卡片弹层（CardForm 字段齐全，不实际提交，不污染知识库）
 *
 * 前置条件：
 *   - 前端已可用：生产部署（Flask 5678，`/chat#/workbench`，需先 build + 发布静态并重启后端）
 *     或 dev server（Vite 5173）。默认 URL 指向 Flask 生产入口。
 *   - 后端 /api/knowledge/* 可访问（脚本只读 + 打开弹层即关闭，不创建/删除卡片）。
 *   - 本机装有 Edge 或 Chrome（可用 EDGE_PATH / CHROME_PATH 环境变量或 --edge 指定）。
 *
 * 用法：
 *   # 生产构建冒烟（默认）：
 *   node scripts/dev/kb_workbench_smoke.mjs
 *   # dev server 冒烟：
 *   node scripts/dev/kb_workbench_smoke.mjs --url http://127.0.0.1:5173/static/#/workbench
 *   # 自定义检索词 / 浏览器 / 超时（秒）：
 *   node scripts/dev/kb_workbench_smoke.mjs --query 双链 --edge "C:\\...\\msedge.exe" --timeout 180
 *   # 或在 yunshu-ui 下：
 *   npm run smoke:kb
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
    url: 'http://127.0.0.1:5678/chat#/workbench',
    query: '双链',
    edge: process.env.EDGE_PATH || process.env.CHROME_PATH || null,
    timeoutMs: 180_000,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const v = argv[i];
    if (v === '--url') args.url = argv[++i] ?? args.url;
    else if (v === '--query') args.query = argv[++i] ?? args.query;
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

  console.log('=== 工作台知识库页浏览器冒烟 ===');
  console.log(`  URL  : ${args.url}`);
  console.log(`  检索词: ${args.query}`);
  console.log(`  浏览器: ${edgeBin ?? '(未找到，用 EDGE_PATH/CHROME_PATH 或 --edge 指定)'}`);

  check('找到 Edge/Chrome 可执行文件', Boolean(edgeBin));
  if (!edgeBin) return;

  const profile = mkdtempSync(join(tmpdir(), 'kb-smoke-'));
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
        await sleep(400);
      }
      return false;
    };
    const clickByExactText = (label) =>
      ev(`(()=>{const bs=[...document.querySelectorAll('button')];const b=bs.find(x=>x.textContent.trim()===${JSON.stringify(label)});if(b){b.click();return true}return false})()`);

    // ── 2. 工作台导航 → 知识库系统 ──
    check('工作台 SPA 启动（导航含「知识库系统」）',
      await waitFor(`document.body && document.body.innerText.includes('知识库系统')`, 40_000));

    check('点击导航「知识库系统」', await clickByExactText('知识库系统'));

    check('知识库页渲染（统计行 + 三 tab + 卡片区）',
      await waitFor(
        `document.body.innerText.includes('卡片总数') && document.body.innerText.includes('融合检索') && document.body.innerText.includes('健康巡检') && document.body.innerText.includes('知识卡片（')`,
        30_000,
      ));

    const stats = await ev(
      `(()=>{const t=document.body.innerText;const m=t.match(/卡片总数\\s*\\d+/);return JSON.stringify({cards:m?m[0]:null,health:t.includes('健康分'),newBtn:t.includes('新建卡片'),refresh:t.includes('刷新')})})()`,
    );
    check('只读统计行就绪（卡片总数/健康分/新建/刷新）', Boolean(stats) && stats.includes('"newBtn":true') && stats.includes('"health":true') && stats.includes('"cards":"卡片总数'));
    console.log(`      stats: ${stats}`);

    // ── 3. 融合检索 tab ──
    check('切换「融合检索」tab', await clickByExactText('融合检索'));
    const typed = await ev(
      `(()=>{const el=document.querySelector('input[placeholder="输入问题，检索知识库（RRF 融合）"]');if(!el)return false;Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set.call(el,${JSON.stringify(args.query)});el.dispatchEvent(new Event('input',{bubbles:true}));return true})()`,
    );
    check('输入检索词', typed);
    await sleep(400);
    check('点击「检索」', await ev(
      `(()=>{const bs=[...document.querySelectorAll('button')];const b=bs.find(x=>x.textContent.trim()==='检索');if(b){b.click();return true}return false})()`,
    ));
    // 命中（含 [来源: 或 score）或明确的空态（未命中）都算请求链路走通
    const searched = await waitFor(
      `document.body.innerText.includes('[来源:') || document.body.innerText.includes('score ') || document.body.innerText.includes('未命中任何卡片')`,
      25_000,
    );
    check('检索请求完成并渲染结果（命中或空态）', searched);
    const searchOutcome = await ev(
      `(()=>{const t=document.body.innerText;const i=t.indexOf('RRF 融合 + 双链扩展 + 精排');return i>=0?t.slice(i,i+200).replace(/\\s+/g,' '):'(未见结果区)'})()`,
    );
    console.log(`      search: ${searchOutcome}`);

    // ── 4. 健康巡检 tab ──
    check('切换「健康巡检」tab', await clickByExactText('健康巡检'));
    check('健康报告渲染（巡检于 …）',
      await waitFor(`document.body.innerText.includes('巡检于')`, 25_000));

    // ── 5. 列表与统计 tab：行点击 → 详情抽屉 ──
    check('切回「列表与统计」tab', await clickByExactText('列表与统计'));
    const hasRow = await waitFor(
      `[...document.querySelectorAll('button')].some(x=>x.title && x.title.startsWith('打开卡片:'))`,
      25_000,
    );
    if (hasRow) {
      check('点击卡片行打开详情抽屉', await ev(
        `(()=>{const b=[...document.querySelectorAll('button')].find(x=>x.title&&x.title.startsWith('打开卡片:'));if(b){b.click();return true}return false})()`,
      ));
      check('详情抽屉渲染（出链/入链）',
        await waitFor(`document.body.innerText.includes('出链') && document.body.innerText.includes('入链')`, 15_000));
      await ev(`(()=>{const o=document.querySelector('.kb-detail-overlay');if(o)o.click();return !!o})()`);
      await sleep(400);
    } else {
      console.log('      [SKIP] 知识库为空：跳过详情抽屉冒烟');
    }

    // ── 6. 新建卡片弹层（只打开验证字段，不提交）──
    check('点击「新建卡片」', await clickByExactText('新建卡片'));
    const modalOk = await waitFor(
      `!!document.querySelector('input[placeholder="唯一标识（创建后不可修改）"]')`,
      15_000,
    );
    check('新建卡片弹层打开（CardForm 字段齐全）', modalOk);
    const fieldCount = await ev(
      `[...document.querySelectorAll('input,textarea')].filter(e=>e.placeholder).length`,
    );
    console.log(`      modal fields: ${fieldCount}`);
    check('弹层字段数 ≥ 5', Number(fieldCount) >= 5);
    await ev(
      `(()=>{const d=[...document.querySelectorAll('div')].find(x=>x.className&&String(x.className).includes('bg-black/50'));if(d)d.click();return !!d})()`,
    );

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
