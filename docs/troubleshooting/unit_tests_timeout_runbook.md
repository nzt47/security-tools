# unit-tests 超时排查清单

> **触发场景**：CI 中 unit-tests job 被 `timeout-minutes` 强制终止，
> `update-ci-dashboard` job 因 `needs: unit-tests` 未成功而 skipped，
> 看板趋势行未追加。
> **最近一次**：2026-07-29 run 30426327728 (commit c9f89218)
> **前置文档**：[`ci_dashboard_update_failure_runbook.md`](file:///c:/Users/Administrator/agent/docs/troubleshooting/ci_dashboard_update_failure_runbook.md)

---

## 一、诊断结论（2026-07-29 run 30426327728）

### 1.1 现象

| 项 | 值 |
|----|-----|
| run-id | 30426327728 |
| commit | c9f89218 |
| job 持续时间 | 45m15s（05:51:52 → 06:37:07） |
| job 状态 | cancelled（timeout-minutes: 45 触发） |
| pytest 进度 | **94%**（最后一个 PASSED: test_visibility_report.py） |
| FAILED/ERROR 测试 | **0 个** |
| INTERNALERROR | **无** |
| kill 证据 | `Terminate orphan process: pid (2474) (pytest)` |

### 1.2 根因分类

**本次属于"场景 B：测试总量超时"**，非"场景 A：测试卡住"。

| 场景 | 特征 | 本次是否符合 |
|------|------|--------------|
| A. 测试卡住（hang） | 进度停在某个测试不动，时间无限增长 | ❌ 不符合（进度连续推进到 94%） |
| B. 测试总量超时 | 进度正常推进，但总量大，timeout 内跑不完 | ✅ 符合（42 分钟跑 94%，剩 6%） |

### 1.3 时间线

```
05:51:52  job 启动
05:52:04  安装 pytest 依赖
05:54:41  第一个测试 PASSED (0%)
06:36:50  test_visibility_report.py 开始 (93%)
06:37:04  最后一个 PASSED (94%)
06:37:05  timeout-minutes: 45 触发，runner kill pytest 进程
06:37:07  job 标记 cancelled
```

**估算**：8661 个测试 × 94% ≈ 8141 个完成，剩余约 520 个（6%）未跑完。
平均每测试 0.31 秒（42min / 8141），无异常慢测试。

---

## 二、场景 A 排查：测试卡住（hang）

> 适用于：进度停在某个测试长时间不动（9c53ae88 的历史场景）

### 2.1 定位卡住的测试

```bash
# 获取 job 日志，过滤 pytest 进度行的最后 10 个
gh api "repos/nzt47/security-tools/actions/jobs/<job-id>/logs" --paginate 2>&1 \
  | Select-String "PASSED|FAILED|ERROR" | Select-Object -Last 10

# 卡住的测试 = 最后一个 PASSED 之后的那个测试
# 它没有输出 PASSED/FAILED，说明它还在跑或 hang 了
```

### 2.2 确认 hang 类型

```bash
# 检查是否有 signal 超时记录
gh api "repos/nzt47/security-tools/actions/jobs/<job-id>/logs" --paginate 2>&1 \
  | Select-String "SIGALRM|signal|timeout|TimeoutError"

# 检查是否有 C 扩展相关的 hang
gh api "repos/nzt47/security-tools/actions/jobs/<job-id>/logs" --paginate 2>&1 \
  | Select-String "sentence_transformers|chromadb|sqlite-vec|torch"
```

### 2.3 常见 hang 根因（来自 project_memory 经验）

| 根因 | 表现 | 解决方案 |
|------|------|----------|
| C 扩展阻塞 join | sentence_transformers/chromadb 导入时扫描文件系统 | SKILLS_OFFLINE=1 patch 重量级模块 |
| signal 无法中断 C 扩展 | --timeout-method=signal 对 C 扩展无效 | timeout-minutes 兜底 + 子进程隔离 |
| 网络请求无超时 | 测试中 HTTP 请求 hang | 添加 requests timeout 参数 |
| 文件锁竞争 | SQLite busy_timeout 不足 | PRAGMA busy_timeout=5000 |

### 2.4 本地复现

```bash
# 用相同参数本地跑疑似卡住的测试
SKILLS_OFFLINE=1 pytest tests/unit/<suspect-file>::<suspect-test> \
  -v --timeout=60 --timeout-method=signal

# 如果本地也 hang，用 Ctrl+C 中断看堆栈
# 堆栈会显示卡在哪个 C 扩展调用
```

---

## 三、场景 B 排查：测试总量超时

> 适用于：进度正常推进，但 timeout 内跑不完（本次 30426327728 的场景）

### 3.1 确认测试总量

```bash
# 本地统计测试数量
SKILLS_OFFLINE=1 pytest tests/unit/ --collect-only -q 2>&1 | Select-Object -Last 3
# 输出示例: "8661 tests collected"
```

### 3.2 估算所需时间

```bash
# 本地跑完整测试，测量时间
SKILLS_OFFLINE=1 pytest tests/unit/ \
  -v --tb=short -q \
  -p no:randomly \
  -m "not slow and not skip_ci" \
  --ignore=tests/unit/test_sandbox_multiprocess_boundary.py \
  --timeout=60 --timeout-method=signal

# 记录总时间，对比 timeout-minutes 是否够用
```

### 3.3 找最慢的测试

```bash
# 用 --durations 输出最慢的 20 个测试
SKILLS_OFFLINE=1 pytest tests/unit/ \
  --durations=20 \
  -p no:randomly \
  -m "not slow and not skip_ci" \
  --ignore=tests/unit/test_sandbox_multiprocess_boundary.py \
  --timeout=60 --timeout-method=signal \
  -q
```

### 3.4 解决方案（按优先级）

#### 方案 1：增加 timeout-minutes（最快，治标）

```yaml
# .github/workflows/ci.yml
unit-tests:
  timeout-minutes: 60  # 45 → 60，给足时间跑完 8661 个测试
```

- **优点**：1 行修改，立即生效
- **缺点**：CI 时间延长 15 分钟；若测试数量继续增长，60 分钟也不够
- **适用**：临时修复，争取时间做长期优化

#### 方案 2：启用 pytest-xdist 并行（治本，推荐）

```yaml
# .github/workflows/ci.yml 的 pytest 命令添加 -n auto
pytest tests/unit/ \
  -n auto \  # 新增：按 CPU 核数并行
  -v --tb=short \
  --cov=agent \
  ...
```

- **优点**：2-4 倍加速（取决于 runner CPU 核数）
- **缺点**：可能引入测试间依赖问题（共享状态/全局变量/文件系统）
- **风险**：需验证所有测试无共享状态依赖
- **验证**：本地先跑 `pytest -n auto` 确认无失败

#### 方案 3：分离慢测试（长期）

```python
# 在慢测试上加 marker
import pytest
@pytest.mark.slow
def test_large_dataset_processing():
    ...

# ci.yml 只跑非 slow 测试
pytest tests/unit/ -m "not slow"
```

- **优点**：CI 只跑快测试，慢测试单独 schedule
- **缺点**：需逐个标记慢测试
- **适用**：测试数量持续增长的长期方案

#### 方案 4：测试分片（matrix 并行）

```yaml
# ci.yml 用 matrix 分片
strategy:
  matrix:
    shard: [1, 2, 3, 4]
steps:
  - name: 运行分片测试
    run: |
      pytest tests/unit/ \
        --shard-id=${{ matrix.shard }} \
        --num-shards=4 \
        ...
```

- **优点**：4 个 shard 并行，理论 4 倍加速
- **缺点**：需安装 pytest-shard 插件；shard 间负载不均
- **适用**：测试数量极大（>10000）且无法用 -n auto 时

---

## 四、本次推荐方案

### 4.1 立即修复（让看板更新闭环）

**方案 1：增加 timeout-minutes 到 60**

```yaml
unit-tests:
  timeout-minutes: 60  # 45 → 60
```

理由：
- 本次跑到 94% 用了 42 分钟，剩余 6% 约需 3 分钟
- 60 分钟留有 18 分钟余量，足够跑完
- 1 行修改，立即让 CI 通过，看板更新闭环

### 4.2 长期优化（后续单独推进）

**方案 2：启用 pytest-xdist**

需先验证：
```bash
# 本地验证 -n auto 是否引入失败
SKILLS_OFFLINE=1 pytest tests/unit/ -n auto -p no:randomly \
  -m "not slow and not skip_ci" \
  --ignore=tests/unit/test_sandbox_multiprocess_boundary.py \
  --timeout=60 -q
```

若本地通过，CI 中启用 `-n auto`，预期 42min → 10-15min。

---

## 五、预防措施

### 5.1 监控测试总量增长

```bash
# 在 CI 中添加测试数量统计 step
- name: 统计测试数量
  run: |
    COUNT=$(SKILLS_OFFLINE=1 pytest tests/unit/ --collect-only -q 2>&1 | tail -1)
    echo "测试总数: $COUNT"
    # 若超过阈值告警
```

### 5.2 定期审查慢测试

```bash
# 每周跑一次，找最慢的 50 个测试
pytest tests/unit/ --durations=50 -q > test_reports/slowest_tests.txt
```

### 5.3 timeout-minutes 动态调整

根据测试总量动态设置：
- < 5000 测试：30 分钟
- 5000-10000 测试：60 分钟
- > 10000 测试：考虑分片

---

## 六、诊断命令速查

```bash
# 1. 查看 run 状态
gh run view <run-id> --json status,conclusion,jobs --jq '{status, conclusion, jobs: [.jobs[] | {name, status, conclusion}]}'

# 2. 获取 unit-tests job 日志关键部分
gh api "repos/nzt47/security-tools/actions/jobs/<job-id>/logs" --paginate 2>&1 \
  | Select-String "PASSED|FAILED|ERROR|timeout|SIGALRM|INTERNALERROR" | Select-Object -Last 20

# 3. 本地统计测试数量
SKILLS_OFFLINE=1 pytest tests/unit/ --collect-only -q 2>&1 | Select-Object -Last 3

# 4. 本地找最慢测试
SKILLS_OFFLINE=1 pytest tests/unit/ --durations=20 -q

# 5. 本地复现 CI 超时
SKILLS_OFFLINE=1 pytest tests/unit/ -v --tb=short -p no:randomly \
  -m "not slow and not skip_ci" \
  --ignore=tests/unit/test_sandbox_multiprocess_boundary.py \
  --timeout=60 --timeout-method=signal
```

---

## 七、相关文档

- 看板更新失败排查：[`ci_dashboard_update_failure_runbook.md`](file:///c:/Users/Administrator/agent/docs/troubleshooting/ci_dashboard_update_failure_runbook.md)
- run 未创建排查：[`ci_run_not_created_runbook.md`](file:///c:/Users/Administrator/agent/docs/troubleshooting/ci_run_not_created_runbook.md)
- CI 健康度看板：[`docs/dashboards/ci_health_dashboard.md`](file:///c:/Users/Administrator/agent/docs/dashboards/ci_health_dashboard.md)
- 解除阻塞脚本：[`scripts/unblock_ci_and_trigger_dashboard.ps1`](file:///c:/Users/Administrator/agent/scripts/unblock_ci_and_trigger_dashboard.ps1)
- 看板监控脚本：[`scripts/monitor_dashboard_update.ps1`](file:///c:/Users/Administrator/agent/scripts/monitor_dashboard_update.ps1)
