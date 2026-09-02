#!/usr/bin/env node
/**
 * 统一工作台浏览器验收冒烟（覆盖人工清单 1/7 的可断言项）
 * ============================================================================
 * 检查项：
 *   A. DevConsole FAB（右上角）存在且可展开（网络/错误/性能/状态 Tab）
 *   B. 会话任务页可用（上下文管理器含滑块 + 机制说明）
 *   C. 栏目渲染抽查：可视化编辑 / 数据导出 / 插件管理 / 知识库系统
 *   D. 资产管理分类联动（点「提示词库」→ 主内容出现 prompts 分类内容）
 *   E. 历史问话面板（会话页内入口存在并可打开；空态也接受）
 *   F. 知识库 CRUD 冒烟：新建「冒烟测试」卡片 → 列表出现 → 删除 → 消失
 *      （写操作真实落库 knowledge/index.md，标题带时间戳便于辨认；结束即清理）
 *   G. 旧路径重定向抽查（#/dashboard → /workbench）
 *
 * 前置：后端 5678 运行（python app_server.py）+ 已 build:flask；本机 Edge/Chrome。
 * 用法：
 *   node scripts/dev/workbench_acceptance_smoke.mjs [--url http://127.0.0.1:5678/chat#/workbench] [--edge "..."]
 * 退出码：全 PASS → 0；任一 FAIL → 1。
 */
import { spawn } from 'node:child_process';
import http from 'node:http';
import net from 'node:net';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const getJSON = (url) => new Promise((resolve, reject) => {
  http.get(url, (res) => {
    let d = '';
    res.on('data', (c) => (d += c));
    res.on('end', () => { try { resolve(JSON.parse(d)); } catch (e) { reject(e); } });
  }).on('error', reject);
});
const freePort = () => new Promise((resolve, reject) => {
  const srv = net.createServer();
  srv.listen(0, '127.0.0.1', () => { const p = srv.address().port; srv.close(() => resolve(p)); });
  srv.on('error', reject);
});
function parseArgs(argv) {
  const a = { url: 'http://127.0.0.1:5678/chat#/workbench', edge: process.env.EDGE_PATH || null };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--url') a.url = argv[++i] ?? a.url;
    else if (argv[i] === '--edge') a.edge = argv[++i] ?? a.edge;
  }
  return a;
}
const resolveBrowser = (explicit) => [
  explicit,
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
].filter(Boolean).find((p) => existsSync(p)) ?? null;

let PASS = 0, FAIL = 0;
const check = (name, cond, detail = '') => {
  if (cond) { PASS += 1; console.log(`  [PASS] ${name}`); }
  else { FAIL += 1; console.log(`  [FAIL] ${name}${detail ? ` — ${detail}` : ''}`); }
};

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const edgeBin = resolveBrowser(args.edge);
  console.log('=== 统一工作台验收冒烟 ===');
  console.log(`  URL: ${args.url}\n  浏览器: ${edgeBin ?? 'NOT FOUND'}`);
  check('找到 Edge/Chrome', Boolean(edgeBin));
  if (!edgeBin) return;

  const stamp = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14); // 冒烟标题时间戳
  // 尾字母 x：slugify 会循环剥除尾部「-<数字>」后缀，字母结尾保证 slug 完整保留
  const smokeTitle = `Smoke Test ${stamp}x`;
  const slug = `smoke-test-${stamp}x`;
  const profile = mkdtempSync(join(tmpdir(), 'wb-accept-'));
  const cdpPort = await freePort();
  const proc = spawn(edgeBin, [
    '--headless=new', '--disable-gpu', '--no-sandbox',
    `--user-data-dir=${profile}`, `--remote-debugging-port=${cdpPort}`,
    '--remote-allow-origins=*', args.url,
  ], { stdio: 'ignore', windowsHide: true });

  let ws = null;
  try {
    let target = null;
    const bootDeadline = Date.now() + 60000;
    while (Date.now() < bootDeadline && !target) {
      try { const l = await getJSON(`http://127.0.0.1:${cdpPort}/json/list`); target = l.find((t) => t.type === 'page'); } catch {}
      if (!target) await sleep(400);
    }
    check('无头浏览器启动且 CDP 可达', Boolean(target));
    if (!target) return;

    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('ws 连接失败')); });
    let msgId = 0;
    const pending = new Map();
    const consoleErrors = [];
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
      if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') {
        consoleErrors.push((m.params.args || []).map((x) => x.value || x.description || '').join(' ').slice(0, 160));
      }
    };
    const send = (method, params = {}) => new Promise((res) => { const id = ++msgId; pending.set(id, res); ws.send(JSON.stringify({ id, method, params })); });
    await send('Runtime.enable');
    const ev = async (expression) => {
      const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
      if (r.result?.exceptionDetails) throw new Error('页面脚本异常: ' + JSON.stringify(r.result.exceptionDetails).slice(0, 200));
      return r.result?.result?.value;
    };
    const waitFor = async (expr, timeoutMs) => {
      const dl = Date.now() + timeoutMs;
      while (Date.now() < dl) {
        try { if (await ev(expr)) return true; } catch {}
        await sleep(400);
      }
      return false;
    };
    const clickText = (label) => ev(
      `(()=>{const bs=[...document.querySelectorAll('button')];const b=bs.find(x=>x.textContent.trim()===${JSON.stringify(label)});if(b){b.click();return true}return false})()`);
    const bodyText = () => ev(`document.body.innerText`);

    // ═══ A. DevConsole FAB ═══
    check('工作台启动（云枢工作台可见）', await waitFor(`document.body.innerText.includes('云枢工作台')`, 40000));
    check('DevConsole FAB 存在（右上角）', await waitFor(`!!document.querySelector('.devconsole-fab')`, 20000));
    // FAB 展开由 mousedown+mouseup（未移动=点击）驱动，纯 click() 不触发
    const fabOpen = await ev(`(()=>{const fab=document.querySelector('.devconsole-fab');if(!fab)return false;const r=fab.getBoundingClientRect();const x=r.x+r.width/2,y=r.y+r.height/2;fab.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,clientX:x,clientY:y}));window.dispatchEvent(new MouseEvent('mouseup',{bubbles:true,clientX:x,clientY:y}));return true})()`);
    check('点击 FAB 展开面板', fabOpen);
    await sleep(1200);
    let panelTxt = await ev(`(()=>{const p=document.querySelector('.devconsole-panel');if(!p)return '(panel 未出现)';return p.innerText.slice(0,200).replace(/\\s+/g,' ')})()`);
    if (panelTxt === '(panel 未出现)') {
      // 容错：再点一次 FAB（toggle 语义可能首点收起/次点展开）
      await ev(`(()=>{const b=document.querySelector('.devconsole-fab');if(b)b.click();return true})()`);
      await sleep(1000);
      panelTxt = await ev(`(()=>{const p=document.querySelector('.devconsole-panel');if(!p)return '(panel 仍未出现)';return p.innerText.slice(0,200).replace(/\\s+/g,' ')})()`);
    }
    check('面板含 网络/错误/性能 Tab', panelTxt.includes('网络') && panelTxt.includes('错误') && panelTxt.includes('性能'), `panel: ${panelTxt}`);
    await ev(`(()=>{const b=document.querySelector('.devconsole-fab');if(b)b.click();return true})()`); // 收起

    // ═══ B. 会话任务页（默认）：上下文管理器 ═══
    check('上下文管理器入口可见', await waitFor(`document.body.innerText.includes('上下文')`, 8000));
    check('展开上下文 → 三个滑块出现', await ev(`(()=>{const b=[...document.querySelectorAll('button')].find(x=>x.textContent.includes('上下文')&&x.textContent.includes('%'));if(b){b.click();return true}return false})()`).then(async (ok) => ok && (await waitFor(`document.querySelectorAll('input[type=range]').length>=3`, 8000))));

    // ═══ E. 历史问话面板入口（存在即可；空态也接受）═══
    const hasHistoryBtn = await ev(`(()=>{const bs=[...document.querySelectorAll('button')];const b=bs.find(x=>x.title&&String(x.title).includes('历史'));if(b){b.click();return true}return false})()`);
    if (hasHistoryBtn) {
      check('历史问话面板可打开', await waitFor(`document.body.innerText.includes('历史问话')`, 8000));
      await ev(`(()=>{const d=[...document.querySelectorAll('div')].find(x=>x.className&&String(x.className).includes('fixed')&&String(x.className).includes('right-'));if(d){d.click();return true}return false})()`);
    } else {
      console.log('      [SKIP] 未找到历史问话按钮（会话为空或无历史功能时跳过）');
    }

    // ═══ C. 栏目渲染抽查 ═══
    const navCases = [
      ['可视化编辑', '节点'],           // 画布区应有节点面板字样
      ['数据导出', '导出格式'],
      ['插件管理', '插件'],
      ['知识库系统', '卡片总数'],
    ];
    for (const [nav, expect] of navCases) {
      check(`点击导航「${nav}」`, await clickText(nav));
      check(`「${nav}」页渲染（含「${expect}」）`, await waitFor(`document.body.innerText.includes(${JSON.stringify(expect)})`, 20000));
      await ev(`(()=>{const s=document.querySelector('.devconsole-fab');return true})()`);
    }

    // ═══ D. 资产管理分类联动（点「提示词库」应显示 prompts 分类而非默认记忆）═══
    check('点击资产「提示词库」', await clickText('提示词库'));
    const promptsActive = await waitFor(`(()=>{const t=document.body.innerText;return t.includes('提示词')&&(t.includes('分类')||t.includes('创建')||t.includes('备份'))})()`, 12000);
    check('提示词库分类激活（非默认记忆分类）', promptsActive);

    // ═══ F. 知识库 CRUD 冒烟（新建→验证→删除）═══
    check('切到知识库系统', await clickText('知识库系统'));
    const kbReady = await waitFor(`document.body.innerText.includes('新建卡片')`, 20000);
    check('知识库页就绪（含新建卡片入口）', kbReady);
    if (kbReady) {
      check('点击新建卡片', await clickText('新建卡片'));
      const formOk = await waitFor(`!!document.querySelector('input[placeholder="唯一标识（创建后不可修改）"]')`, 10000);
      check('新建弹层出现', formOk);
      if (formOk) {
        const fillInput = (ph, val) => ev(`(()=>{const els=[...document.querySelectorAll('input,textarea')];const el=els.find(x=>x.placeholder&&x.placeholder.startsWith(${JSON.stringify(ph)}));if(!el)return false;const d=(el.tagName==='TEXTAREA')?Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value'):Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value');d.set.call(el,${JSON.stringify(val)});el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));return true})()`);
        const setSelect = (idx, val) => ev(`(()=>{const sels=[...document.querySelectorAll('.kb-form select, select')];const el=sels[${idx}];if(!el)return false;const opt=[...el.options].find(o=>o.value===${JSON.stringify(val)});if(!opt)return false;el.value=opt.value;el.dispatchEvent(new Event('change',{bubbles:true}));return true})()`);
        await fillInput('卡片标题', smokeTitle);
        await fillInput('唯一标识', slug);
        await fillInput('一句话核心洞见', `${smokeTitle} 核心洞见（验收冒烟自动创建）`);
        await fillInput('文章 / 播客', 'smoke-auto'); // source 必填
        await fillInput('正文 Markdown', `# ${smokeTitle}\n\n验收冒烟脚本自动创建的测试卡片，验证后即删除。`);
        await setSelect(0, 'concepts');
        await setSelect(1, 'draft');
        await sleep(400);
        // 提交：新建时按钮为「创建」
        const submit = await ev(`(()=>{const bs=[...document.querySelectorAll('button')];const b=bs.find(x=>['创建','保存'].includes(x.textContent.trim()));if(b){b.click();return true}return false})()`);
        check('提交新建卡片', submit);
        const created = await waitFor(`document.body.innerText.includes(${JSON.stringify(smokeTitle)})`, 15000);
        check('列表出现冒烟卡片', created);
        if (!created) {
          const formErr = await ev(`(()=>{const e=document.querySelector('.kb-form-error');return e?e.textContent:'(弹层已关闭或无错误)'})()`);
          console.log(`      formError: ${formErr}`);
        }
        // 删除：先注入 confirm/alert 覆盖（headless 默认拒绝），再点行内删除
        await ev(`(()=>{window.confirm=()=>true;window.alert=()=>{}})()`);
        const delClicked = await ev(`(()=>{const bs=[...document.querySelectorAll('button')];const b=bs.find(x=>x.title==='删除'&&x.closest('tr')&&x.closest('tr').textContent.includes(${JSON.stringify(smokeTitle)}));if(!b)return false;b.click();return true})()`);
        check('点击冒烟卡片删除', delClicked);
        check('冒烟卡片已从列表移除', await waitFor(`!document.body.innerText.includes(${JSON.stringify(smokeTitle)})`, 10000));
      } else {
        check('CRUD 冒烟（弹层缺失跳过）', true, 'skip');
      }
    } else {
      check('CRUD 冒烟（知识库未就绪跳过）', true, 'skip');
    }

    // ═══ G. 旧路径重定向抽查 ═══
    await send('Page.navigate', { url: args.url.replace('#/workbench', '#/dashboard') });
    await sleep(2500);
    check('旧路径 #/dashboard 重定向 /workbench', await waitFor(`location.hash.includes('workbench')`, 10000));

    check('无 console 错误（前 5 条）', consoleErrors.length === 0, consoleErrors.slice(0, 5).join(' | '));
    console.log('=== 验收结束 ===');
  } catch (e) {
    check('验收过程无异常', false, e.message);
  } finally {
    try { if (ws) ws.close(); } catch {}
    try { proc.kill(); } catch {}
    try { rmSync(profile, { recursive: true, force: true }); } catch {}
  }
  console.log(`结果: [PASS] ${PASS}  [FAIL] ${FAIL}`);
}

await main();
process.exit(FAIL > 0 ? 1 : 0);
