#!/usr/bin/env node
/**
 * 拓扑视图前端交互自动化测试（S3）
 *
 * 用 jsdom（yunshu-ui 依赖）模拟主控台 DOM + mock fetch，加载 static/js/topology.js，
 * 验证前端交互逻辑：
 *   1. loadTopology 渲染六域树、5 种状态色标、指标 chip
 *   2. 点击不同状态节点 → 详情面板更新（fetch detail、状态色标正确）
 *   3. 干预接口调用：
 *      - 低危动作：直接 POST /api/modules/<id>/actions，body 契约正确
 *      - 高危动作：confirm 取消不发请求；confirm 通过但 reason 为空不发请求；
 *                  通过且有 reason 时 POST 且携带 reason
 *
 * 运行: node scripts/test_topology_interactions.mjs
 * 说明: 仅模拟前端交互，不访问真实后端（fetch 全部 mock），无副作用。
 */
import { createRequire } from 'module';
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, resolve } from 'path';

const require = createRequire(import.meta.url);
const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, '..');

const { JSDOM } = require(resolve(ROOT, 'yunshu-ui/node_modules/jsdom'));

let passed = 0;
let failed = 0;

function check(name, cond, detail = '') {
  if (cond) {
    passed++;
    console.log(`  [PASS] ${name}${detail ? ' ' + detail : ''}`);
  } else {
    failed++;
    console.log(`  [FAIL] ${name}${detail ? ' ' + detail : ''}`);
  }
}

// ── mock 数据（覆盖 5 种状态节点，对齐 modules_api 契约）──
const TOPOLOGY = {
  generated_at: '2026-08-16T00:00:00',
  overall_health: 0.92,
  domains: [
    { domain_id: 'perception', domain_name: '感知层', icon: '👁',
      nodes: [
        { module_id: 'sensor.body', name: '感知聚合器', path: 'sensor/body_sensor.py', type: 'sensor',
          status: 'healthy', status_detail: '运行中', metrics: [{ key: 'sensor_on', value: 18 }], actions: [], danger: 'low' },
        { module_id: 'sensor.env', name: '环境传感器', path: 'sensor/env.py', type: 'sensor',
          status: 'warning', status_detail: '健康分 0.66', metrics: [{ key: 'battery', value: '12%' }], actions: [], danger: 'low' },
        { module_id: 'sensor.ext', name: '扩展感知', path: 'sensor/ext.py', type: 'sensor',
          status: 'disabled', status_detail: '未启用', metrics: [], actions: [], danger: 'low' },
      ] },
    { domain_id: 'action', domain_name: '行动层', icon: '🤖',
      nodes: [
        { module_id: 'action.tools', name: '工具集', path: 'agent/tools/', type: 'service',
          status: 'healthy', status_detail: '运行中', metrics: [{ key: 'tool_count', value: 42 }],
          actions: ['toggle_tool'], danger: 'medium' },
        { module_id: 'action.process', name: '进程管理', path: 'agent/system_tools.py', type: 'service',
          status: 'fault', status_detail: '健康分 0.30', metrics: [],
          actions: ['start_process', 'stop_process'], danger: 'high' },
        { module_id: 'action.llm', name: 'LLM 实例', path: 'app_server.py', type: 'service',
          status: 'healthy', status_detail: '运行中', metrics: [],
          actions: ['reconfigure_llm'], danger: 'high' },
      ] },
    { domain_id: 'memory', domain_name: '记忆层', icon: '💾',
      nodes: [
        { module_id: 'memory.manager', name: '记忆管理器', path: 'memory/', type: 'service',
          status: 'offline', status_detail: '无数据', metrics: [],
          actions: ['compress_memory'], danger: 'low' },
      ] },
  ],
};

const DETAILS = {
  'sensor.body': { module_id: 'sensor.body', name: '感知聚合器', domain: '感知层', path: 'sensor/body_sensor.py',
    type: 'sensor', description: '聚合传感器', status: 'healthy', status_detail: '运行中',
    metrics: [{ key: 'sensor_on', value: 18 }], actions: [], recent_actions: [] },
  'sensor.env': { module_id: 'sensor.env', name: '环境传感器', domain: '感知层', path: 'sensor/env.py',
    type: 'sensor', description: '环境感知', status: 'warning', status_detail: '健康分 0.66',
    metrics: [{ key: 'battery', value: '12%' }], actions: [], recent_actions: [] },
  'sensor.ext': { module_id: 'sensor.ext', name: '扩展感知', domain: '感知层', path: 'sensor/ext.py',
    type: 'sensor', description: '扩展感知', status: 'disabled', status_detail: '未启用',
    metrics: [], actions: [], recent_actions: [] },
  'action.tools': { module_id: 'action.tools', name: '工具集', domain: '行动层', path: 'agent/tools/',
    type: 'service', description: '工具注册表', status: 'healthy', status_detail: '运行中',
    metrics: [{ key: 'tool_count', value: 42 }],
    actions: [{ action: 'toggle_tool', method: 'POST', url: '/api/tools/toggle', params: {}, danger: 'medium', note: '' }],
    recent_actions: [] },
  'action.process': { module_id: 'action.process', name: '进程管理', domain: '行动层', path: 'agent/system_tools.py',
    type: 'service', description: '进程管理', status: 'fault', status_detail: '健康分 0.30',
    metrics: [],
    actions: [{ action: 'start_process', method: 'POST', url: '/api/process/start', params: {}, danger: 'medium', note: '' },
              { action: 'stop_process', method: 'POST', url: '/api/process/stop', params: {}, danger: 'high', note: '' }],
    recent_actions: [] },
  'action.llm': { module_id: 'action.llm', name: 'LLM 实例', domain: '行动层', path: 'app_server.py',
    type: 'service', description: 'LLM 管理', status: 'healthy', status_detail: '运行中',
    metrics: [],
    actions: [{ action: 'reconfigure_llm', method: 'POST', url: '/api/config', params: {}, danger: 'high', note: '' }],
    recent_actions: [] },
  'memory.manager': { module_id: 'memory.manager', name: '记忆管理器', domain: '记忆层', path: 'memory/',
    type: 'service', description: '记忆管理', status: 'offline', status_detail: '无数据',
    metrics: [], actions: [], recent_actions: [] },
};

function okBody(data, status = 200) {
  return { ok: status < 400, status, json: async () => data };
}

async function main() {
  const html = '<div id="topo-metrics"></div><div id="topo-tree"></div><div id="topo-detail"></div>';
  const dom = new JSDOM(html, { runScripts: 'outside-only', url: 'http://127.0.0.1:5678/' });
  const { window } = dom;

  // ── mock fetch（记录调用历史）──
  const calls = [];
  window.fetch = async (url, opts = {}) => {
    calls.push({ url: String(url), opts });
    const u = String(url);
    if (u.endsWith('/api/modules/topology')) return okBody(TOPOLOGY);
    if (u.includes('/actions')) {
      return okBody({ ok: true, module_id: 'x', action: 'x', forwarded: '', result: {}, status_code: 200 });
    }
    if (u.includes('/detail')) {
      const m = u.match(/\/api\/modules\/([^/]+)\/detail/);
      const id = m ? decodeURIComponent(m[1]) : '';
      return DETAILS[id] ? okBody(DETAILS[id]) : okBody({ error: 'not found' }, 404);
    }
    return okBody({});
  };

  // 加载 topology.js 到 jsdom window
  const script = readFileSync(resolve(ROOT, 'static/js/topology.js'), 'utf-8');
  window.eval(script);
  const API = window.topologyAPI;

  const wait = (ms = 20) => new Promise((r) => setTimeout(r, ms));

  console.log('== 1. 拓扑渲染（loadTopology）==');
  await API.loadTopology();
  await wait();

  const tree = window.document.getElementById('topo-tree');
  check('六域渲染', tree.querySelectorAll('.domain').length === 3, `domains=${tree.querySelectorAll('.domain').length}`);
  check('5 种状态色标存在', ['s-healthy', 's-warning', 's-fault', 's-offline', 's-disabled']
    .every((cls) => tree.querySelector('.dot.' + cls)), 'healthy/warning/fault/offline/disabled');
  check('指标 chip 渲染', tree.querySelectorAll('.chip').length >= 2, `chips=${tree.querySelectorAll('.chip').length}`);
  const metrics = window.document.getElementById('topo-metrics');
  check('全局指标条渲染', metrics.querySelectorAll('.metric-card').length === 3);

  console.log('== 2. 点击各状态节点 → 详情面板 ==');
  const clickNode = (id) => {
    const el = tree.querySelector('.node[data-module-id="' + id + '"]');
    if (!el) throw new Error('节点不存在: ' + id);
    el.click();
  };

  // 健康节点
  clickNode('action.tools');
  await wait();
  const detailEl = window.document.getElementById('topo-detail');
  check('点击健康节点加载详情', detailEl.textContent.includes('工具注册表')
    && detailEl.textContent.includes('action.tools'), 'detail 面板含模块信息');
  check('健康节点渲染低危按钮', detailEl.querySelector('.action-btn:not(.danger)') !== null);

  // 故障节点
  clickNode('action.process');
  await wait();
  check('点击故障节点加载详情', detailEl.textContent.includes('进程管理')
    && detailEl.querySelector('.dot.s-fault') !== null, '故障状态色标正确');
  check('高危动作按钮渲染 danger 类', detailEl.querySelector('.action-btn.danger') !== null,
    `danger 按钮=${detailEl.querySelectorAll('.action-btn.danger').length}`);

  // 离线节点
  clickNode('memory.manager');
  await wait();
  check('点击离线节点加载详情', detailEl.textContent.includes('记忆管理')
    && detailEl.querySelector('.dot.s-offline') !== null, '离线状态色标正确');

  // 未启用节点
  clickNode('sensor.ext');
  await wait();
  check('点击未启用节点加载详情', detailEl.textContent.includes('扩展感知')
    && detailEl.querySelector('.dot.s-disabled') !== null, '未启用状态色标正确');

  // 详情请求 URL 正确性
  const detailCalls = calls.filter((c) => c.url.includes('/detail'));
  check('detail 请求按模块 ID 发起', detailCalls.length >= 4
    && detailCalls.some((c) => c.url.includes('action.process')));

  console.log('== 3. 干预接口调用 ==');
  const postCalls = () => calls.filter((c) => c.url.includes('/actions'));

  // 3.1 低危/中危动作：无 confirm 直接 POST
  window.confirm = () => { throw new Error('低危动作不应弹确认'); };
  window.prompt = () => '自动操作';
  clickNode('action.tools');
  await wait();
  const btnToggle = window.document.querySelector('#topo-detail .action-btn[data-action="toggle_tool"]');
  check('低危动作按钮存在', btnToggle !== null);
  btnToggle.click();
  await wait();
  let posted = postCalls();
  check('低危动作 POST 发出', posted.length === 1, `calls=${posted.length}`);
  if (posted[0]) {
    const body = JSON.parse(posted[0].opts.body);
    check('POST body 契约', body.action === 'toggle_tool' && typeof body.reason === 'string'
      && typeof body.params === 'object', JSON.stringify(body));
  }

  // 3.2 高危动作：confirm 取消 → 不发请求
  window.confirm = () => false;
  window.prompt = () => '不应调用';
  clickNode('action.process');
  await wait();
  const btnStop = window.document.querySelector('#topo-detail .action-btn[data-action="stop_process"]');
  check('高危按钮存在', btnStop !== null && btnStop.classList.contains('danger'));
  btnStop.click();
  await wait();
  check('confirm 取消不发请求', postCalls().length === 1, `calls=${postCalls().length}（仍为 1）`);

  // 3.3 高危动作：confirm 通过但 reason 为空 → 不发请求
  window.confirm = () => true;
  window.prompt = () => '   ';
  btnStop.click();
  await wait();
  check('reason 为空不发请求', postCalls().length === 1, `calls=${postCalls().length}`);

  // 3.4 高危动作：confirm 通过 + reason → POST 携带 reason
  window.prompt = () => '故障排查需要';
  btnStop.click();
  await wait();
  posted = postCalls();
  check('高危带 reason 发出请求', posted.length === 2, `calls=${posted.length}`);
  if (posted[1]) {
    const body = JSON.parse(posted[1].opts.body);
    check('高危 body 含 reason', body.action === 'stop_process' && body.reason === '故障排查需要',
      JSON.stringify(body));
  }

  // 3.5 未声明动作不应出现按钮（感知层节点无 actions）
  clickNode('sensor.body');
  await wait();
  const detailBody = window.document.getElementById('topo-detail');
  check('无动作节点显示空态', detailBody.querySelector('.action-btn') === null
    || detailBody.textContent.includes('无'));

  console.log('== 4. 轮询生命周期 ==');
  // 快照必须在启动轮询前记录（前面 3 区的调用不计入轮询请求数）
  const beforePoll = calls.length;
  API.startPolling();
  await wait(30);  // 30ms << 5s 轮询周期，断言期间无轮询触发
  await API.stopPolling();
  check('轮询启停无异常', true);

  const extra = calls.length - beforePoll;
  console.log(`  (轮询启停期间新增请求 ${extra} 次，周期 5s 内应极少)`);
  check('请求总数有限（无失控轮询）', extra === 0, `extra=${extra}`);

  console.log(`\n结果: ${passed} passed / ${failed} failed`);
  process.exit(failed ? 1 : 0);
}

main().catch((e) => {
  console.error('测试执行异常:', e);
  process.exit(2);
});
