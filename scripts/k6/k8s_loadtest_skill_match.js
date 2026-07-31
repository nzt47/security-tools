/**
 * K8s 真实集群压测脚本 — 5000 技能量级技能检索服务
 *
 * 基于本地基线报告（baseline_report.json）配置：
 *   - 20 VU × 60s × 5 QPS/VU = 100 QPS（baseline 场景）
 *   - thresholds: p99<40ms / p95<25ms / 错误率<1% / 总请求>5400
 *   - 8 个测试查询（复用 baseline_skill_match.js）
 *
 * 【不易】对齐 HPA 触发阈值:
 *   - 每副本 QPS 阈值 200（baseline 100 QPS / 2 副本 = 50 QPS/副本，远低于阈值）
 *   - P99 延迟阈值 40ms（HPA 扩容触发线）
 * 【变易】多场景支持（SCENARIO 环境变量）:
 *   - baseline (默认): 20 VU × 60s 稳态，验证不触发 HPA 扩容
 *   - burst: ramp-up 2→40 VU，验证 HPA 30s 内 2→6 扩容
 *   - stress: 50 VU × 120s，验证 candidate_limit=200 降级方案
 * 【简易】单文件自包含，环境变量参数化
 *
 * ═══════════════════════════════════════════════════════════════════
 *  Prometheus 指标采集配置（Remote Write）
 * ═══════════════════════════════════════════════════════════════════
 * k6 通过 --out experimental-prometheus-rw 把指标推送到 Prometheus。
 * 运行命令（在能访问 Prometheus 的机器上执行）:
 *
 *   # baseline 场景（默认）
 *   k6 run \
 *     --out experimental-prometheus-rw=http://prometheus-server.monitoring.svc.cluster.local:9090/api/v1/write \
 *     -e ENDPOINT=http://skill-retrieval-service.production.svc.cluster.local:8080/match \
 *     -e NAMESPACE=production \
 *     scripts/k6/k8s_loadtest_skill_match.js
 *
 *   # burst 场景（验证 HPA 扩容）
 *   k6 run \
 *     --out experimental-prometheus-rw=http://prometheus-server.monitoring.svc.cluster.local:9090/api/v1/write \
 *     -e ENDPOINT=http://skill-retrieval-service.production.svc.cluster.local:8080/match \
 *     -e SCENARIO=burst \
 *     scripts/k6/k8s_loadtest_skill_match.js
 *
 *   # 从集群内 Pod 运行（避免本地网络限制，最贴近真实流量）
 *   kubectl run k6-runner --rm -it --restart=Never --image=grafana/k6:latest \
 *     -- run --out experimental-prometheus-rw=http://prometheus-server.monitoring.svc.cluster.local:9090/api/v1/write \
 *        -e ENDPOINT=http://skill-retrieval-service.production.svc.cluster.local:8080/match \
 *        /scripts/k8s_loadtest_skill_match.js
 *
 * Prometheus 端需启用 Remote Write Receiver（启动参数 --web.enable-remote-write-receiver）
 * 或部署 prometheus-adapter + Remote Write sidecar。
 *
 * 推送到 Prometheus 的指标（带 k6_ 前缀）:
 *   - k6_match_latency_ms (Trend)         — 检索延迟分布，对应 HPA skill_match_latency_p99
 *   - k6_match_success_total (Counter)    — 成功请求数
 *   - k6_match_failure_total (Counter)    — 失败请求数
 *   - k6_hpa_threshold_exceeded (Rate)    — 延迟超 40ms 的请求比例（HPA 触发率）
 *   - k6_http_reqs / k6_http_req_duration — k6 内置指标（QPS、延迟）
 *
 * Grafana 关联面板:
 *   - QPS:        rate(k6_http_reqs_total[1m])
 *   - P99 延迟:   histogram_quantile(0.99, rate(k6_http_req_duration_bucket[1m]))
 *   - 错误率:     rate(k6_match_failure_total[1m]) / rate(k6_http_reqs_total[1m])
 *   - HPA 触发率: rate(k6_hpa_threshold_exceeded_total[1m])  (超 40ms 的请求占比)
 */
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Trend, Rate } from 'k6/metrics';
import { textSummary } from 'https://jslib.k6.io/k6-summary/0.0.1/index.js';

// ═══════════════════════════════════════════════════════════════════
//  配置（环境变量参数化）
// ═══════════════════════════════════════════════════════════════════

const ENDPOINT = __ENV.ENDPOINT || 'http://skill-retrieval-service.production.svc.cluster.local:8080/match';
const SCENARIO = __ENV.SCENARIO || 'baseline';
const NAMESPACE = __ENV.NAMESPACE || 'production';

// ═══════════════════════════════════════════════════════════════════
//  自定义指标（推送到 Prometheus，带 k6_ 前缀）
// ═══════════════════════════════════════════════════════════════════

const matchLatency = new Trend('match_latency_ms', true);       // 检索延迟（ms）
const matchSuccess = new Counter('match_success_total');         // 成功请求
const matchFailure = new Counter('match_failure_total');         // 失败请求
const hpaThresholdExceeded = new Rate('hpa_threshold_exceeded'); // 延迟超 40ms 占比

// ═══════════════════════════════════════════════════════════════════
//  测试查询集（复用 baseline，覆盖多领域）
// ═══════════════════════════════════════════════════════════════════

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
//  场景配置（对齐 K8S_HPA_LOADTEST_PLAN.md）
// ═══════════════════════════════════════════════════════════════════

const SCENARIOS = {
  // 阶段 1: 基线压测 — 稳态容量确认（对齐本地 baseline_report.json）
  baseline: {
    vus: 20,
    duration: '60s',
    thresholds: {
      'http_req_duration{type:match}': ['p(99)<40', 'p(95)<25'],  // 对齐 HPA 阈值
      'http_req_failed': ['rate<0.01'],                             // 错误率 < 1%
      'http_reqs': ['count>5400'],                                  // 100 QPS × 60s × 90%
      'hpa_threshold_exceeded': ['rate<0.05'],                      // <5% 请求超 40ms
    },
  },

  // 阶段 2: 突发流量 — HPA 扩容验证（2→6 副本，30s 内）
  // ramp-up 模拟突发流量，触发 HPA scaleUp（Pods +4/30s + Percent +100%/60s）
  burst: {
    stages: [
      { duration: '5s', target: 10 },    // 初始 2 副本稳态
      { duration: '10s', target: 40 },   // 突发到 40 VU（200 QPS），触发 HPA
      { duration: '30s', target: 40 },   // 保持，观察 30s 内扩容到 6 副本
      { duration: '10s', target: 20 },   // 回落
      { duration: '5s', target: 0 },     // 收尾
    ],
    thresholds: {
      'http_req_duration{type:match}': ['p(99)<80'],  // 突发期放宽（扩容中延迟会升高）
      'http_req_failed': ['rate<0.05'],                // 错误率 < 5%
      'hpa_threshold_exceeded': ['rate<0.30'],         // <30% 请求超 40ms（扩容期允许）
    },
  },

  // 阶段 3: 压力测试 — candidate_limit=200 降级方案验证
  // 50 VU 持续压测，验证降级后服务不崩溃
  stress: {
    vus: 50,
    duration: '120s',
    thresholds: {
      'http_req_duration{type:match}': ['p(99)<150'],  // 降级后 P99 < 150ms
      'http_req_failed': ['rate<0.10'],                 // 错误率 < 10%
      'hpa_threshold_exceeded': ['rate<0.50'],          // <50% 请求超 40ms（降级期允许）
    },
  },
};

export const options = SCENARIOS[SCENARIO] || SCENARIOS.baseline;

// 全局标签（便于 Prometheus 按 namespace/scenario 维度聚合）
const commonTags = { namespace: NAMESPACE, scenario: SCENARIO };

// ═══════════════════════════════════════════════════════════════════
//  主循环 — 每个 VU 每秒发起 5 次请求（200ms 间隔）
// ═══════════════════════════════════════════════════════════════════

export default function () {
  const queryItem = TEST_QUERIES[Math.floor(Math.random() * TEST_QUERIES.length)];
  const payload = JSON.stringify({
    query: queryItem.query,
    top_k: queryItem.top_k,
  });

  const params = {
    headers: { 'Content-Type': 'application/json' },
    tags: { ...commonTags, type: 'match' },  // type=match 用于 thresholds 分组
  };

  const res = http.post(ENDPOINT, payload, params);

  // 记录延迟（毫秒，推送到 Prometheus）
  matchLatency.add(res.timings.duration, commonTags);

  // 标记是否超过 HPA 40ms 阈值（用于 hpa_threshold_exceeded Rate）
  hpaThresholdExceeded.add(res.timings.duration > 40, commonTags);

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
    matchSuccess.add(1, commonTags);
  } else {
    matchFailure.add(1, commonTags);
  }

  // 控制 QPS: 每个 VU 每秒 5 次请求（200ms 间隔）
  sleep(0.2);
}

// ═══════════════════════════════════════════════════════════════════
//  报告输出 — JSON 报告 + 控制台摘要
// ═══════════════════════════════════════════════════════════════════

export function handleSummary(data) {
  const sc = SCENARIOS[SCENARIO] || {};

  // ═══════════════════════════════════════════════════════════════════
  //  【变易】容错提取指标 — k6 v2.1.0 在 thresholds 失败时部分 p(NN) 为 undefined
  //  旧实现直接 data.metrics.xxx?.values.yyy，某指标缺失会导致 toFixed 抛异常
  //  新实现先解构到局部变量，统一用 || 0 兜底
  // ═══════════════════════════════════════════════════════════════════
  const m = (data && data.metrics) || {};
  const dur = (m.http_req_duration && m.http_req_duration.values) || {};
  const reqs = (m.http_reqs && m.http_reqs.values) || {};
  const failed = (m.http_req_failed && m.http_req_failed.values) || {};
  const hpa = (m.hpa_threshold_exceeded && m.hpa_threshold_exceeded.values) || {};
  const succ = (m.match_success_total && m.match_success_total.values) || {};
  const fail = (m.match_failure_total && m.match_failure_total.values) || {};

  const totalRequests = reqs.count || 0;
  const actualQps = reqs.rate || 0;
  const p50 = dur['p(50)'] || 0;
  const p90 = dur['p(90)'] || 0;
  const p95 = dur['p(95)'] || 0;
  const p99raw = dur['p(99)'] || 0;
  const latencyAvg = dur.avg || 0;
  const latencyMax = dur.max || 0;
  const errorRate = failed.rate || 0;
  const hpaRate = hpa.rate || 0;
  // 【不易】Rate 指标的 passes = 超 40ms 请求数（用于故障排查报告）
  const hpaExceededCount = hpa.passes || 0;
  const matchSuccess = succ.count || 0;
  const matchFailure = fail.count || 0;

  // ═══════════════════════════════════════════════════════════════════
  //  【不易】P99 兜底计算 — k6 v2.1.0 thresholds 失败时 p(99) 可能为 0
  //  反推依据: hpa_threshold_exceeded 记录延迟 >40ms 的请求占比
  //    若 exceededRate > 0 → P(100 - exceededRate*100) = 40ms → P99 > 40ms
  //    保守估计 P99 = max(p95 * 1.5, 40)（重尾分布 P99 ≈ 1.2-2× P95）
  // ═══════════════════════════════════════════════════════════════════
  let latencyP99 = p99raw;
  let p99Source = 'k6_direct';
  if (!p99raw || p99raw === 0) {
    if (hpaRate > 0) {
      // 存在超 40ms 请求，P99 必然 > 40ms
      latencyP99 = Math.max(p95 * 1.5, 40);
      p99Source = 'estimated_from_hpa_rate';
    } else if (p95 > 0) {
      // 正常情况 P99 ≈ 1.2 × P95
      latencyP99 = p95 * 1.2;
      p99Source = 'estimated_from_p95';
    } else {
      latencyP99 = 0;
      p99Source = 'unavailable';
    }
  }

  const round2 = (v) => Math.round(v * 100) / 100;

  const summary = {
    test_name: `k8s_loadtest_${SCENARIO}`,
    timestamp: new Date().toISOString(),
    config: {
      scenario: SCENARIO,
      endpoint: ENDPOINT,
      namespace: NAMESPACE,
      vus: sc.vus || 'stages',
      duration: sc.duration || (sc.stages ? JSON.stringify(sc.stages) : 'n/a'),
      target_qps: SCENARIO === 'baseline' ? 100 : (SCENARIO === 'stress' ? 250 : 'variable'),
    },
    results: {
      total_requests: totalRequests,
      actual_qps: round2(actualQps),
      latency_p50: round2(p50),
      latency_p90: round2(p90),
      latency_p95: round2(p95),
      latency_p99: round2(latencyP99),
      latency_p99_source: p99Source,
      latency_avg: round2(latencyAvg),
      latency_max: round2(latencyMax),
      error_rate: errorRate,
      hpa_threshold_exceeded_rate: hpaRate,
      hpa_threshold_exceeded_count: hpaExceededCount,
      match_success: matchSuccess,
      match_failure: matchFailure,
    },
    thresholds: data.thresholds
      ? Object.fromEntries(
          Object.entries(data.thresholds).map(([k, v]) => [k, v.ok])
        )
      : {},
    thresholds_all_passed: data.thresholds
      ? Object.values(data.thresholds).every((v) => v.ok)
      : false,
  };

  // 控制台摘要
  console.log('\n' + '='.repeat(70));
  console.log(`  K8s 集群压测报告 — 场景: ${SCENARIO}`);
  console.log('='.repeat(70));
  console.log(`  Endpoint:        ${ENDPOINT}`);
  console.log(`  Namespace:       ${NAMESPACE}`);
  console.log(`  总请求:          ${summary.results.total_requests}`);
  console.log(`  实际 QPS:        ${summary.results.actual_qps.toFixed(1)}`);
  console.log(`  延迟 avg:        ${summary.results.latency_avg.toFixed(2)}ms`);
  console.log(`  延迟 p50:        ${summary.results.latency_p50.toFixed(2)}ms`);
  console.log(`  延迟 p90:        ${summary.results.latency_p90.toFixed(2)}ms`);
  console.log(`  延迟 p95:        ${summary.results.latency_p95.toFixed(2)}ms (阈值 <25ms baseline)`);
  console.log(`  延迟 p99:        ${summary.results.latency_p99.toFixed(2)}ms [${p99Source}] (阈值 <40ms HPA)`);
  console.log(`  延迟 max:        ${summary.results.latency_max.toFixed(2)}ms`);
  console.log(`  错误率:          ${(summary.results.error_rate * 100).toFixed(2)}%`);
  console.log(`  HPA 触发率:      ${(summary.results.hpa_threshold_exceeded_rate * 100).toFixed(2)}% (${hpaExceededCount} 请求超 40ms)`);
  console.log(`  成功/失败:       ${summary.results.match_success} / ${summary.results.match_failure}`);
  console.log('');
  console.log('  Thresholds:');
  if (Object.keys(summary.thresholds).length > 0) {
    for (const [k, passed] of Object.entries(summary.thresholds)) {
      console.log(`    [${passed ? '✓' : '✗'}] ${k}`);
    }
  } else {
    console.log('    (无 thresholds 配置)');
  }
  console.log(`  总判定:          ${summary.thresholds_all_passed ? 'PASS ✓' : 'FAIL ✗'}`);
  console.log('='.repeat(70));
  console.log('  Prometheus 指标已推送（如配置 --out experimental-prometheus-rw）');
  console.log('  Grafana 查询示例:');
  console.log('    QPS:     rate(k6_http_reqs_total[1m])');
  console.log('    P99:     histogram_quantile(0.99, rate(k6_http_req_duration_bucket[1m]))');
  console.log('    错误率:  rate(k6_match_failure_total[1m]) / rate(k6_http_reqs_total[1m])');
  console.log('='.repeat(70) + '\n');

  // 【简易】兜底输出完整 JSON 到 stderr — 即使文件写入失败，数据也不丢
  // run_full_loadtest.ps1 可用正则 ___K6_REPORT_JSON_BEGIN_(.*?)___END___ 从日志提取
  console.error(`___K6_REPORT_JSON_BEGIN___${JSON.stringify(summary)}___K6_REPORT_JSON_END___`);

  // 写入 JSON 报告（按场景命名）
  // 【变易】k6 thresholds 失败时退出码非 0，但 handleSummary 仍会被调用
  // 只要 return 对象包含文件 key，k6 就会写入文件
  const reportFile = `k8s_${SCENARIO}_report.json`;
  return {
    [reportFile]: JSON.stringify(summary, null, 2),
    stdout: textSummary(data),
  };
}
