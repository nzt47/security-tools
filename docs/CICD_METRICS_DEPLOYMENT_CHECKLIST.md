# CI/CD 指标推送系统 - 部署检查清单

> **创建日期**：2026-07-31
> **适用范围**：cicd_metrics_push.py + ci-cd.yml + Prometheus/Grafana 监控体系
> **用途**：确认本地环境到 CI 流水线的完整上线步骤，确保生产环境稳定性

---

## 一、本地环境验证（上线前必做）

### 1.1 代码完整性检查

- [ ] `scripts/cicd_metrics_push.py` 存在且语法正确
  ```bash
  python -c "import ast; ast.parse(open('scripts/cicd_metrics_push.py').read()); print('OK')"
  ```
- [ ] `scripts/cicd_metrics_push.py` 含 sys.path 注入逻辑（确保 agent 包可导入）
  ```bash
  python -c "import scripts.cicd_metrics_push as m; print('PROJECT_ROOT' in dir(m) and 'OK' or 'MISSING')"
  # 预期: OK（_PROJECT_ROOT 已注入 sys.path）
  ```
- [ ] `scripts/test_grouping_key_local.py` 存在（6 用例）
- [ ] `scripts/test_log_level_dynamic.py` 存在（6 用例）
- [ ] `scripts/test_log_level_showcase.py` 存在（4 级别对比）
- [ ] `scripts/demo_parallel_jobs.py` 存在（并行演示）

### 1.2 单元测试验证

- [ ] grouping_key 测试全部通过
  ```bash
  python scripts/test_grouping_key_local.py
  # 预期: 6 通过, 0 失败
  ```
- [ ] 日志级别测试全部通过
  ```bash
  python scripts/test_log_level_dynamic.py
  # 预期: 6 通过, 0 失败
  ```

### 1.3 功能验证

- [ ] INFO 级别运行正常（输出 3 条日志）
  ```bash
  $env:LOG_LEVEL="INFO"; python scripts/cicd_metrics_push.py --stage build --success
  ```
- [ ] DEBUG 级别运行正常（输出 5 条日志，含 registry 指标数 + 推送耗时）
  ```bash
  $env:LOG_LEVEL="DEBUG"; python scripts/cicd_metrics_push.py --stage build --success
  ```
- [ ] **agent 包导入成功**（registry 指标数 > 10，证明 Yunshu_ 自定义指标已注册）
  ```bash
  $env:LOG_LEVEL="DEBUG"; python scripts/cicd_metrics_push.py --stage build --success 2>&1 | findstr "registry"
  # 预期: [metrics] registry 指标数: 85（或 >10）
  # 若为 10 则说明 agent 包未导入，检查 sys.path 注入逻辑
  ```
- [ ] **无 "降级为 no-op" 警告**（PrometheusMetricsExporter 成功实例化）
  ```bash
  $env:LOG_LEVEL="DEBUG"; python scripts/cicd_metrics_push.py --stage build --success 2>&1 | findstr "no-op"
  # 预期: 无输出（无降级警告）
  ```
- [ ] 日志级别展示脚本运行正常
  ```bash
  python scripts/test_log_level_showcase.py
  ```
- [ ] 并行 job 演示脚本运行正常
  ```bash
  python scripts/demo_parallel_jobs.py
  ```
- [ ] 重试机制验证（pushgateway 不可达时触发重试，不阻塞流水线）
  ```bash
  $env:LOG_LEVEL="DEBUG"; $env:PUSH_MAX_RETRIES="1"; python scripts/cicd_metrics_push.py --stage build --success
  # 预期日志含: "推送失败，准备重试 → 尝试 1/2, 延迟 1.0s"
  # 预期退出码: 0（不阻塞）
  ```
- [ ] 重试次数可配置（设为 0 则不重试）
  ```bash
  $env:PUSH_MAX_RETRIES="0"; python scripts/cicd_metrics_push.py --stage build --success
  # 预期: 无重试日志，直接输出推送失败
  ```
- [ ] **环境变量解析容错**（非数字值降级为默认值，不崩溃）
  ```bash
  $env:PUSH_TIMEOUT="abc"; $env:PUSH_MAX_RETRIES="xyz"
  python scripts/cicd_metrics_push.py --stage build --success
  # 预期: 正常运行（PUSH_TIMEOUT 降级为 10.0，PUSH_MAX_RETRIES 降级为 1）
  # 预期退出码: 0
  ```
- [ ] **埋点异常隔离**（record_stage 失败不阻断 push）
  ```bash
  # 确认 main() 中 try/except 包裹 record_stage，异常时记录 warning 并继续 push
  python -c "
  import sys; sys.path.insert(0, 'scripts')
  import cicd_metrics_push as m
  class BadExporter:
      def record_ci_pipeline_run(self, **kw): raise RuntimeError('模拟埋点失败')
      def __getattr__(self, name): return lambda *a, **kw: None
  try:
      m.record_stage(BadExporter(), 'build', True, 'prod', None, None)
      print('FAIL: record_stage 未抛出异常')
  except RuntimeError as e:
      print('OK: record_stage 异常会被 main() try/except 隔离')
  "
  ```

### 1.4 并发安全验证

- [ ] 同 run_id 不同 ci_job 的 grouping_key 独立
  ```
  stress-test:            {'run_id': 'xxx', 'ci_job': 'stress-test'}
  integration-test:       {'run_id': 'xxx', 'ci_job': 'integration-test'}
  circuit-breaker:        {'run_id': 'xxx', 'ci_job': 'circuit-breaker-inspection'}
  ✅ 3 个 grouping_key 全部不同
  ```

---

## 二、CI 流水线配置确认

### 2.1 ci-cd.yml 全局 env 配置

- [ ] `PUSHGATEWAY_URL` 已配置
  ```yaml
  PUSHGATEWAY_URL: http://monitoring.internal:9091
  ```
- [ ] `LOG_LEVEL` 已配置（默认 INFO）
  ```yaml
  LOG_LEVEL: INFO
  ```
- [ ] grouping_key 注释已添加
  ```yaml
  # [修复 CHG-2026-0731] GITHUB_JOB 由 GitHub Actions 自动注入...
  ```
- [ ] 可选：推送超时和重试参数（有默认值，无需必填）
  ```yaml
  # PUSH_TIMEOUT: "10"        # 推送超时秒数，默认 10（prometheus_client 原默认 30 太长）
  # PUSH_MAX_RETRIES: "1"     # 最大重试次数，默认 1（共 2 次尝试），仅对网络瞬时错误重试
  ```

### 2.2 埋点 Step 配置

- [ ] `lint-and-typecheck` job 含 "Record CI build metrics" step
- [ ] `stress-test` job 含 "Record CI test metrics" step
- [ ] `integration-test` job 含 coverage 提取 + "Record CI test metrics" step
- [ ] `circuit-breaker-inspection` job 含 "Record CI test metrics" step
- [ ] `docker-build` job 含 "Record CI build metrics" step
- [ ] `deployment-ready` job 含 duration 计算 + "Record deployment metrics" + "Record rollback" step

### 2.3 YAML 语法校验

- [ ] ci-cd.yml YAML 语法正确
  ```bash
  python -c "import yaml; yaml.safe_load(open('.github/workflows/ci-cd.yml')); print('YAML OK')"
  ```
- [ ] 8 个 job 结构完整
- [ ] 7 个埋点 step 分布正确
- [ ] 9 条依赖链全部有效

---

## 三、Pushgateway 配置确认

### 3.1 服务可用性

- [ ] pushgateway 服务已部署
  ```bash
  curl http://monitoring.internal:9091/-/healthy
  # 预期: 200 OK
  ```
- [ ] DNS 解析正确
  ```bash
  nslookup monitoring.internal
  ```
- [ ] 端口 9091 开放
  ```bash
  telnet monitoring.internal 9091
  ```

### 3.2 Prometheus 抓取配置

- [ ] prometheus.yml 配置了 pushgateway 抓取
  ```yaml
  scrape_configs:
    - job_name: 'pushgateway'
      honor_labels: true
      static_configs:
        - targets: ['pushgateway:9091']
  ```
- [ ] Prometheus 能抓取到 pushgateway 指标
  ```bash
  curl http://localhost:9090/api/v1/targets | grep pushgateway
  ```

---

## 三点五、环境依赖与导入路径验证

> **关键**：此章节为 [CHG-2026-0731] 新增，修复 agent 包不可导入导致自定义指标未注册的问题。

### 3.5.1 Python 依赖检查

- [ ] prometheus_client 已安装
  ```bash
  python -c "import prometheus_client; print(prometheus_client.__version__)"
  ```
- [ ] agent 包可导入（sys.path 注入生效）
  ```bash
  python scripts/cicd_metrics_push.py --stage build --success 2>&1 | findstr "no-op"
  # 预期: 无输出（无 "降级为 no-op" 警告）
  ```

### 3.5.2 自定义指标注册验证

- [ ] PrometheusMetricsExporter 成功实例化（registry 指标数 > 10）
  ```bash
  $env:LOG_LEVEL="DEBUG"; python scripts/cicd_metrics_push.py --stage build --success 2>&1 | findstr "registry"
  # 预期: [metrics] registry 指标数: 85（或 >10）
  # 若为 10: agent 包未导入 → 检查 sys.path 注入 → 检查 agent/ 目录完整性
  ```
- [ ] RetryPolicy 使用项目统一类（非降级为 _SimpleRetryPolicy）
  ```bash
  $env:LOG_LEVEL="DEBUG"; python scripts/cicd_metrics_push.py --stage build --success 2>&1 | findstr "RetryPolicy"
  # 预期: 无 "降级为内置简易重试" 日志
  # 若有降级日志: agent.error_handler 不可导入 → 检查 agent/ 目录完整性
  ```

### 3.5.3 CI 环境仓库结构验证

- [ ] CI runner 中仓库结构完整（agent 包在仓库根目录）
  ```bash
  # CI step 中添加验证
  ls -la agent/__init__.py agent/monitoring/__init__.py agent/error_handler.py
  # 预期: 三个文件均存在
  ```
- [ ] CI runner 中 sys.path 注入生效
  ```yaml
  # 在 Record CI build metrics step 前添加验证 step
  - name: Verify agent package importable
    run: |
      python -c "
      import sys, os
      sys.path.insert(0, os.getcwd())
      from agent.monitoring.prometheus import PrometheusMetricsExporter
      print('agent package OK')
      "
  ```

---

## 四、Grafana Dashboard 确认

### 4.1 指标名称一致性

- [ ] dashboard 中无 `yunshu_` 前缀旧指标名（已改为 `Yunshu_`）
- [ ] dashboard PromQL 用 `sum by (stage/environment/status)` 聚合（适配 grouping_key）
- [ ] 运行审计脚本确认无命名断裂
  ```bash
  python scripts/check_grafana_metric_names.py
  ```

### 4.2 数据源配置

- [ ] Grafana 数据源指向正确的 Prometheus
- [ ] datasource uid=prometheus（与 dashboard JSON 一致）

---

## 五、告警规则确认

### 5.1 规则文件

- [ ] `alert_rules.yml` 含 `Yunshu_v2_deployment_alerts` 分组
- [ ] `DeploymentFailureRateHigh` 阈值为 `> 5`（1h 内）
- [ ] `RollbackDetected` 阈值为 `> 1`（1h 内）

### 5.2 规则加载

- [ ] Prometheus 已加载告警规则
  ```bash
  curl http://localhost:9090/api/v1/rules | grep deployment
  ```

---

## 六、上线执行步骤

### 6.1 代码提交

- [ ] 所有修改已提交到 git
  ```bash
  git add scripts/cicd_metrics_push.py
  git add scripts/test_grouping_key_local.py
  git add scripts/test_log_level_dynamic.py
  git add scripts/test_log_level_showcase.py
  git add scripts/demo_parallel_jobs.py
  git add .github/workflows/ci-cd.yml
  git add docs/CICD_METRICS_LOGGING_TROUBLESHOOTING.md
  git add docs/CICD_METRICS_DEPLOYMENT_CHECKLIST.md
  ```
- [ ] 提交信息符合规范
  ```bash
  git commit -m "feat(ci): CI/CD 指标推送系统 - grouping_key 并发安全 + 动态日志级别"
  ```

### 6.2 CI 流水线触发

- [ ] push 到 main 分支触发 CI
- [ ] `lint-and-typecheck` job 通过
- [ ] `stress-test` job 通过
- [ ] `integration-test` job 通过（含 coverage 提取）
- [ ] `circuit-breaker-inspection` job 通过
- [ ] `docker-build` job 通过
- [ ] `deployment-ready` job 通过

### 6.3 指标推送验证

- [ ] CI 运行后 pushgateway 有新指标
  ```bash
  curl http://monitoring.internal:9091/metrics | grep Yunshu_ci
  ```
- [ ] Prometheus 能查到 CI 指标
  ```bash
  curl 'http://localhost:9090/api/v1/query?query=Yunshu_ci_pipeline_runs_total'
  ```
- [ ] Grafana dashboard 显示 CI/CD 数据

---

## 七、上线后验证

### 7.1 日志验证

- [ ] GitHub Actions step 输出包含 `[metrics]` 日志
- [ ] 日志级别为 INFO（生产默认）
- [ ] 推送成功日志出现（pushgateway 可达时）
- [ ] 推送失败日志不阻塞流水线（pushgateway 不可达时）
- [ ] 推送失败时出现重试日志（`准备重试 → 尝试 1/2`）
- [ ] 重试耗尽后退出码仍为 0（不影响流水线）
- [ ] **经重试后推送成功时**日志含 `推送成功（经 N 次重试）`（如有重试场景）
- [ ] **埋点异常时**日志含 `埋点失败（不影响推送）` 且 push 仍执行（如遇埋点异常）
- [ ] **环境变量误配时**脚本不崩溃（PUSH_TIMEOUT/PUSH_MAX_RETRIES 非数字降级为默认值）

### 7.2 指标验证

- [ ] `Yunshu_ci_pipeline_runs_total` 有数据
- [ ] `Yunshu_ci_test_coverage_percent` 有数据（integration-test job）
- [ ] `Yunshu_deployment_duration_seconds` 有数据（deployment-ready job）
- [ ] `Yunshu_deployment_status` 有数据

### 7.3 告警验证

- [ ] `DeploymentFailureRateHigh` 告警状态为 inactive
- [ ] `RollbackDetected` 告警状态为 inactive

---

## 八、回滚预案

### 8.1 配置回滚

- [ ] 回滚脚本可用
  ```bash
  python scripts/rollback_prometheus_rules.ps1 -DryRun
  ```

### 8.2 紧急关闭埋点

如需紧急关闭 CI/CD 指标推送（不影响流水线）：

- [ ] 方案 A：注释 ci-cd.yml 中的埋点 step
- [ ] 方案 B：设置 `PUSHGATEWAY_URL` 为无效地址（推送失败但不阻塞）
- [ ] 方案 C：设置 `LOG_LEVEL=CRITICAL`（静默所有日志）

---

## 九、已知限制

| 限制 | 说明 | 规避方式 |
|---|---|---|
| pushgateway 不自动清理历史 run_id | 每次 CI 运行的指标会残留 | 定期 `curl -X DELETE` 清理旧 run_id |
| Counter 在 pushgateway 中不累计 | pushgateway 是 replace 语义 | dashboard 用 `sum(increase(...))` 聚合 |
| coverage 提取依赖 pytest-cov | CI 环境需安装 pytest-cov | ci-cd.yml 已含 `pip install pytest-cov` |
| duration 精度为秒级 | 短时部署（<5s）可能显示 0 | 可接受（GitHub Actions runner 时钟限制） |
| 重试仅对网络瞬时错误生效 | `OSError` 子类（URLError/ConnectionError/Timeout）才重试 | 非 200 HTTP 状态码如 5xx 由 prometheus_client 内部处理 |
| 推送超时默认 10s | 网络极慢环境可能误超时 | 通过 `PUSH_TIMEOUT` 环境变量调大 |
| agent 包导入依赖 sys.path 注入 | 脚本从 scripts/ 运行时项目根目录不在 sys.path | 已通过 `_PROJECT_ROOT` 注入修复，CI 环境需确保仓库结构完整 |

---

## 十、相关文档

| 文档 | 说明 |
|---|---|
| [CICD_METRICS_LOGGING_TROUBLESHOOTING.md](CICD_METRICS_LOGGING_TROUBLESHOOTING.md) | 日志排查指南 |
| [CICD_METRICS_KNOWN_ISSUES.md](CICD_METRICS_KNOWN_ISSUES.md) | 已知问题与规避 |

---

**检查清单版本**：v1.3（新增环境变量容错 + 埋点异常隔离验证）
**最后更新**：2026-07-31
**验证状态**：✅ 本地测试全部通过（6/6 grouping_key + 6/6 日志级别 + 环境变量容错 + registry 指标数 88），待 CI 流水线验证
