#!/usr/bin/env node
/**
 * 思考/工具内联显示「存活探针」（真实浏览器 + 真实 SSE）
 * ============================================================================
 * 背景：用户反馈"会话里的思考与工具先出现、一会就没了"。单元测试覆盖了 store 合并
 * 逻辑，但无法覆盖真实浏览器里的完整链路（SSE → store → React 渲染）。
 * 本脚本用无头 Edge（CDP）打真实页面：
 *   1. 打开 /chat#/workbench（新建会话保证空态）；
 *   2. 输入并发送一条消息（真实 LLM 调用，消耗 1 次）；
 *   3. 流式期间与结束后各轮询 DOM：
 *        .wb-thought-block / .wb-tool-step 数量、最后一条回复的文本长度
 *      记录时间线，判定"流结束后 N 秒内是否消失"。
 *   4. 全程收集 console 输出（含 [云枢·SSE] 日志）便于定位。
 *
 * 用法：node scripts/dev/thinking_inline_probe.mjs [--url ...] [--hold 30]
 * 退出码：稳定存活 → 0；消失 → 1；环境问题 → 2
 */
import { spawn } from 'node:child_process';
import http from 'node:http';
import net from 'node:net';
import { existsSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const getJSON = (url) => new Promise((resolve, reject) => {
  http.get(url, (res) => { let d = ''; res.on('data', (c) => (d += c)); res.on('end', () => { try { resolve(JSON.parse(d)); } catch (e) { reject(e); } }); }).on('error', reject);
});
const freePort = () => new Promise((resolve, reject) => {
  const srv = net.createServer();
  srv.listen(0, '127.0.0.1', () => { const p = srv.address().port; srv.close(() => resolve(p)); });
  srv.on('error', reject);
});

const args = { url: 'http://127.0.0.1:5678/chat#/workbench', hold: 30, question: '用一句话介绍你自己', turns: 1 };
for (let i = 2; i < process.argv.length; i += 1) {
  if (process.argv[i] === '--url') args.url = process.argv[++i];
  else if (process.argv[i] === '--hold') args.hold = Number(process.argv[++i]) || 30;
  else if (process.argv[i] === '--question') args.question = process.argv[++i];
  else if (process.argv[i] === '--turns') args.turns = Number(process.argv[++i]) || 1;
}

/** 多轮时的提问序列：第 2 轮刻意触发工具调用（系统提示词要求实操请求先发 tool_calls） */
const QUESTIONS = [args.question, '帮我搜索一下今天的新闻', '现在几点了？用工具查一下'];

const edgeBin = [
  process.env.EDGE_PATH,
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
].filter(Boolean).find((p) => existsSync(p));

if (!edgeBin) { console.error('未找到 Edge/Chrome'); process.exit(2); }

const SNAPSHOT_EXPR = `(()=>{
  const msgs=[...document.querySelectorAll('.wb-msg')];
  const last=msgs[msgs.length-1];
  return {
    msgs: msgs.length,
    thought: document.querySelectorAll('.wb-thought-block').length,
    tool: document.querySelectorAll('.wb-tool-step').length,
    hiddenChip: document.querySelectorAll('.wb-hidden-chip').length,
    streaming: document.body.innerText.includes('正在生成'),
    lastLen: last ? last.innerText.replace(/\\s+/g,' ').length : 0,
    lastHead: last ? last.innerText.replace(/\\s+/g,' ').slice(0,70) : '',
  };
})()`;

async function main() {
  const profile = mkdtempSync(join(tmpdir(), 'think-probe-'));
  const cdpPort = await freePort();
  const proc = spawn(edgeBin, [
    '--headless=new', '--disable-gpu', '--no-sandbox',
    `--user-data-dir=${profile}`, `--remote-debugging-port=${cdpPort}`,
    '--remote-allow-origins=*', args.url,
  ], { stdio: 'ignore', windowsHide: true });

  let ws = null;
  try {
    let target = null;
    const deadline = Date.now() + 60000;
    while (Date.now() < deadline && !target) {
      try { const l = await getJSON(`http://127.0.0.1:${cdpPort}/json/list`); target = l.find((t) => t.type === 'page'); } catch {}
      if (!target) await sleep(400);
    }
    if (!target) { console.error('CDP 不可达'); process.exit(2); }

    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('ws fail')); });
    let msgId = 0;
    const pending = new Map();
    const logs = [];
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
      if (m.method === 'Runtime.consoleAPICalled') {
        const text = (m.params.args || []).map((x) => x.value ?? x.description ?? '').join(' ');
        if (text.includes('云枢') || m.params.type === 'error') logs.push(`[${m.params.type}] ${text.slice(0, 200)}`);
      }
    };
    const send = (method, params = {}) => new Promise((res) => { const id = ++msgId; pending.set(id, res); ws.send(JSON.stringify({ id, method, params })); });
    await send('Runtime.enable');
    const ev = async (expr) => {
      const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
      if (r.result?.exceptionDetails) throw new Error('page exception: ' + JSON.stringify(r.result.exceptionDetails).slice(0, 200));
      return r.result?.result?.value;
    };
    const waitFor = async (expr, timeoutMs) => {
      const dl = Date.now() + timeoutMs;
      while (Date.now() < dl) { try { if (await ev(expr)) return true; } catch {} await sleep(400); }
      return false;
    };

    console.log('=== 思考/工具内联显示存活探针 ===');
    console.log(`URL: ${args.url}`);
    const ready = await waitFor(`!!document.querySelector('textarea.wb-input')`, 60000);
    console.log('工作台会话页就绪:', ready);
    if (!ready) { console.error('聊天输入框未出现（后端未就绪？）'); process.exit(2); }

    // 新建会话 → 空态 → 逐轮发送（走真实 SSE，多轮用于验证"上一轮的思考/工具块不会被下一轮抹掉"）
    await ev(`(()=>{const b=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()==='新建');if(b){b.click();return true}return false})()`);
    await sleep(1500);

    const sendQuestion = (q) => ev(`(()=>{
      const ta=document.querySelector('textarea.wb-input');
      if(!ta) return 'no-textarea';
      const setter=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;
      setter.call(ta, ${JSON.stringify(q)});
      ta.dispatchEvent(new Event('input',{bubbles:true}));
      ta.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
      return 'sent';
    })()`);

    const t0 = Date.now();
    const timeline = [];
    let sawThought = false;
    let doneAt = null;
    let turn = 0;
    let sent = await sendQuestion(QUESTIONS[0]);
    turn = 1;
    console.log(`第 ${turn} 轮发送:`, sent);

    for (let i = 0; i < 400; i += 1) {
      let snap = null;
      try { snap = await ev(SNAPSHOT_EXPR); } catch { /* 渲染中偶发异常忽略 */ }
      if (snap) {
        const t = ((Date.now() - t0) / 1000).toFixed(1);
        if (snap.thought > 0) sawThought = true;
        if (!snap.streaming && snap.msgs > 0 && sawThought && doneAt === null) {
          doneAt = Date.now();
          // 还有下一轮 → 立刻追问，验证前一轮区块是否被抹掉
          if (turn < args.turns) {
            const before = snap;
            sent = await sendQuestion(QUESTIONS[Math.min(turn, QUESTIONS.length - 1)]);
            turn += 1;
            doneAt = null;
            console.log(`第 ${turn} 轮发送: ${sent}（追上前一轮区块数：思考=${before.thought} 工具=${before.tool}）`);
          }
        }
        const isKey = timeline.length === 0
          || snap.thought !== timeline.at(-1).thought
          || snap.tool !== timeline.at(-1).tool
          || snap.streaming !== timeline.at(-1).streaming;
        if (isKey) {
          timeline.push(snap);
          console.log(`  t=${t}s 轮次=${turn} msgs=${snap.msgs} 思考块=${snap.thought} 工具块=${snap.tool} 隐藏条=${snap.hiddenChip} 生成中=${snap.streaming} 末条长度=${snap.lastLen}`);
        }
        if (doneAt && Date.now() - doneAt > args.hold * 1000) break;
      }
      await sleep(1000);
    }

    const final = await ev(SNAPSHOT_EXPR);
    console.log('\n--- 最终快照 ---');
    console.log(' ', JSON.stringify(final));
    console.log(`\n曾出现思考块: ${sawThought}；共 ${turn} 轮；流结束后保持 ${args.hold}s 后：思考块=${final.thought} 工具块=${final.tool}`);

    // ── 刷新页面：步骤已随 assistant 消息落盘，历史恢复后内联区块应重新出现 ──
    let afterReload = null;
    if (args.reload !== false) {
      console.log('\n--- 刷新页面（验证落盘步骤经历史恢复）---');
      await send('Page.enable');
      await send('Page.reload', { ignoreCache: true });
      const back = await waitFor(`document.querySelectorAll('.wb-msg').length > 0`, 60000);
      await sleep(1500);
      afterReload = await ev(SNAPSHOT_EXPR);
      console.log(`  页面恢复: ${back}；思考块=${afterReload.thought} 工具块=${afterReload.tool} msgs=${afterReload.msgs}`);
      const build = await ev(`window.__YUNSHU_BUILD__ || '(未注入)'`);
      console.log(`  当前页面构建戳: ${build}`);
    }

    if (logs.length) {
      console.log('\n--- 页面 console（含 SSE 日志，末尾 8 条）---');
      logs.slice(-8).forEach((l) => console.log('  ' + l));
    }

    // 判定：多轮场景下每轮都应留下自己的思考块；刷新后历史恢复同样要有
    const liveOk = sawThought && final.thought >= turn;
    const reloadOk = args.reload === false || (afterReload && afterReload.thought >= 1);
    if (liveOk && reloadOk) {
      console.log(`\n[PASS] ${turn} 轮回复分别保留思考块（${final.thought} 个），刷新后仍恢复 ${afterReload ? afterReload.thought : '-'} 个`);
    } else {
      console.log(`\n[FAIL] 轮次=${turn}；会话内思考块=${final.thought}（需 >= ${turn}）；刷新后思考块=${afterReload ? afterReload.thought : '未检查'}`);
    }
    process.exitCode = liveOk && reloadOk ? 0 : 1;
  } catch (e) {
    console.error('探针异常:', e);
    process.exitCode = 2;
  } finally {
    try { ws?.close(); } catch {}
    try { proc.kill(); } catch {}
  }
}

main();
