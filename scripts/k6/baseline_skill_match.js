/**
 * k6 基线压测脚本 — 5000 技能量级技能检索服务
 *
 * 对应 K8S_HPA_LOADTEST_PLAN.md 阶段 1（基线压测 — 稳态容量确认）
 * 目标: 100 QPS 持续 60s，验证不触发 HPA 扩容（每副本 QPS < 200 阈值）
 *
 * 【不易】对齐 HPA 阈值:
 *   - 每副本 QPS 阈值 200（本脚本 100 QPS / 2 副本 = 50 QPS/副本，远低于阈值）
 *   - P99 延迟阈值 40ms（基线应 < 40ms）
 * 【变易】参数化: ENDPOINT 环境变量
 * 【简易】单文件自包含，含 thresholds 自动判定通过/失败
 *
 * 运行:
 *   k6 run -e ENDPOINT=http://<SVC_IP>:8080/match scripts/k6/baseline_skill_match.js
 *
 * 输出:
 *   - 实时统计（p50/p95/p99 延迟、QPS、错误率）
 *   - JSON 报告文件（baseline_report.json）
 *   - thresholds 判定（通过/失败）
 */
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Trend } from 'k6/metrics';

// ═══════════════════════════════════════════════════════════════════
//  压测配置（对齐 K8S_HPA_LOADTEST_PLAN.md 阶段 1）
// ═══════════════════════════════════════════════════════════════════

export const options = {
  // 阶段 1: 20 VU × 5 QPS/VU = 100 QPS，持续 60s
  vus: 20,
  duration: '60s',

  // 【不易】thresholds 对齐 HPA 触发阈值，自动判定基线是否健康
  thresholds: {
    // P99 延迟必须 < 40ms（HPA 扩容阈值）
    'http_req_duration{type:match}': ['p(99)<40'],
    // P95 延迟应 < 25ms（HPA 缩容参考线）
    'http_req_duration{type:match}': ['p(95)<25'],
    // 错误率 < 1%
    'http_req_failed': ['rate<0.01'],
    // 总 QPS 应 ≥ 90（允许 10% 波动，目标 100）
    'http_reqs': ['count>5400'],  // 100 QPS × 60s × 90%
  },
};

// 自定义指标（补充 k6 默认指标）
const matchLatency = new Trend('match_latency_ms', true);
const matchSuccess = new Counter('match_success_total');
const matchFailure = new Counter('match_failure_total');

// 测试 query 集（覆盖多领域，模拟真实用户意图）
const TEST_QUERIES = [
  { query: '帮我解析PDF文件并提取表格数据', top_k: 5 },
  { query: '生成一份市场分析报告', top_k: 5 },
  { query: '创建一个Python脚本自动化任务', top_k: 5 },
  { query: '帮我反思刚才的回答质量', top_k: 3 },
  { query: '翻译这段英文到中文', top_k: 3 },
  { query: '总结会议记录要点', top_k: 5 },
  { query: '调试JavaScript运行时错误', top_k: 5 },
  { query: '部署应用到Kubernetes集群', top_k: 5 },
];

// ═══════════════════════════════════════════════════════════════════
//  主循环 — 每个 VU 每秒发起 5 次请求（100 QPS / 20 VU = 5 QPS/VU）
// ═══════════════════════════════════════════════════════════════════

export default function () {
  const endpoint = __ENV.ENDPOINT || 'http://localhost:8080/match';
  const queryItem = TEST_QUERIES[Math.floor(Math.random() * TEST_QUERIES.length)];

  const payload = JSON.stringify({
    query: queryItem.query,
    top_k: queryItem.top_k,
  });

  const params = {
    headers: {
      'Content-Type': 'application/json',
    },
    tags: { type: 'match' },  // 用于 thresholds 分组
  };

  const res = http.post(endpoint, payload, params);

  // 记录延迟（毫秒）
  matchLatency.add(res.timings.duration);

  // 结果检查
  const ok = check(res, {
    'status 200': (r) => r.status === 200,
    'has matches': (r) => {
      try {
        const body = r.json();
        return body && (body.matches !== undefined || body.match_count !== undefined);
      } catch (e) {
        return false;
      }
    },
  });

  if (ok) {
    matchSuccess.add(1);
  } else {
    matchFailure.add(1);
  }

  // 控制 QPS: 每个 VU 每秒 5 次请求（200ms 间隔）
  sleep(0.2);
}

// ═══════════════════════════════════════════════════════════════════
//  报告输出 — 生成 JSON 报告 + 控制台摘要
// ═══════════════════════════════════════════════════════════════════

export function handleSummary(data) {
  const summary = {
    test_name: 'baseline_skill_match',
    timestamp: new Date().toISOString(),
    config: {
      vus: options.vus,
      duration: options.duration,
      target_qps: 100,
    },
    results: {
      total_requests: data.metrics.http_reqs?.values.count || 0,
      actual_qps: data.metrics.http_reqs?.values.rate || 0,
      latency_p50: data.metrics.http_req_duration?.values['p(50)'] || 0,
      latency_p95: data.metrics.http_req_duration?.values['p(95)'] || 0,
      latency_p99: data.metrics.http_req_duration?.values['p(99)'] || 0,
      error_rate: data.metrics.http_req_failed?.values.rate || 0,
      match_success: data.metrics.match_success_total?.values.count || 0,
      match_failure: data.metrics.match_failure_total?.values.count || 0,
    },
    thresholds_passed: data.thresholds ? Object.entries(data.thresholds)
      .map(([k, v]) => `${k}: ${v.ok ? '✓' : '✗'}`)
      .join(', ') : 'N/A',
  };

  // 控制台摘要
  console.log('\n' + '='.repeat(70));
  console.log('  基线压测报告 — 5000 技能量级');
  console.log('='.repeat(70));
  console.log(`  总请求: ${summary.results.total_requests}`);
  console.log(`  实际 QPS: ${summary.results.actual_qps.toFixed(1)} (目标 100)`);
  console.log(`  延迟 p50: ${summary.results.latency_p50.toFixed(2)}ms`);
  console.log(`  延迟 p95: ${summary.results.latency_p95.toFixed(2)}ms (阈值 <25ms)`);
  console.log(`  延迟 p99: ${summary.results.latency_p99.toFixed(2)}ms (阈值 <40ms)`);
  console.log(`  错误率: ${(summary.results.error_rate * 100).toFixed(2)}% (阈值 <1%)`);
  console.log(`  成功: ${summary.results.match_success} / 失败: ${summary.results.match_failure}`);
  console.log(`  Thresholds: ${summary.thresholds_passed}`);
  console.log('='.repeat(70) + '\n');

  // 写入 JSON 报告
  return {
    'baseline_report.json': JSON.stringify(summary, null, 2),
    stdout: textSummary(data),
  };
}

// 简化文本摘要
function textSummary(data) {
  return '';
}
