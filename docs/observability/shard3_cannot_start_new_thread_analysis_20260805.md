# 云枢测试 Shard 3 "can't start new thread" 根因分析与资源监控方案

> 归档日期: 2026-08-05 ｜ 关联 CI run: Shard 3 (py3.12) unit-tests job
> 关联代码: `scripts/ci_thread_monitor.py`、`.github/workflows/ci.yml`（单元测试步骤）

## 1. 现象

云枢系统测试流程（ci.yml）单元测试矩阵的 Shard 3（Python 3.12）出现
`pytest INTERNALERROR: RuntimeError: can't start new thread`，pytest 主进程在
运行中无法创建新线程，测试中止且无具体失败用例。

Traceback 中出现的 error_handler 重试测试自身异常为**伴随现象**（该测试因
环境/时序失败），非根因——根因是**线程创建失败本身**。

## 2. 根因分析

### 2.1 直接原因：pytest-timeout 的 thread 方法降级

ci.yml 单元测试使用 `--timeout=60 --timeout-method=signal`。但 pytest-xdist
（`-n 2`）的 worker 进程**无法注册 SIGALRM 信号处理器**，pytest-timeout 在
worker 侧自动降级为 `thread` 方法：

- thread 方法对**每个测试**创建一个 `threading.Timer` 守护线程用于超时检测；
- 测试结束后 `cancel()`，但已触发/调度延迟的 timer 线程瞬时残留；
- 测试自身创建的线程（error_handler 重试、异步任务、后台守护线程）未及时
  join 时同样残留。

### 2.2 原假设证伪

ci.yml 原注释声称：

> 【xdist 交互】-n 2 下 worker 进程自动降级为 thread 方法，但每 worker 仅
> ~4330 测试（8661/2），远低于线程耗尽阈值，安全。

该假设已被实跑证伪：

1. **计算口径错误**：8661 是全量 8 个 shard 时代的总数；6-shard 拆分后
   单 shard 约 1500 测试，但 2 个 worker 并发放大瞬时线程峰值；
2. **低估测试自身线程**：timer 线程不是唯一来源，测试代码泄漏的线程
   （未 join 的 daemon 线程、重复创建的线程池）跨测试累积；
3. **容器 pids 限制**：GitHub Actions 容器 cgroup `pids.max` 同时限制
   线程与进程数，线程/进程逼近该值时 `pthread_create` 失败即抛
   `can't start new thread`。

### 2.3 根因模型

```
-xdist(-n 2) 每 worker 一个进程
  └─ pytest-timeout 降级 thread 方法
       └─ 每测试 1 个 Timer 线程（瞬时创建/取消）
            └─ 叠加测试自身线程泄漏
                 └─ 瞬时线程+进程数 → 逼近 cgroup pids.max
                      └─ pthread_create 失败 → INTERNALERROR
```

## 3. 资源监控方案（本次落地）

### 3.1 监控脚本 `scripts/ci_thread_monitor.py`

- 后台采样线程数（`ps -eLf`）/ 进程数（`ps -e`）/ 容器 `pids.max`；
- 输出 JSON Lines 采样日志，`--report` 模式输出峰值摘要：
  线程 min/avg/max、进程 min/avg/max、`pids.max` 占用率（>80% 标记逼近）；
- 零第三方依赖，采样失败自动降级为 0，不中断 pytest（【不易】只读不改）。

### 3.2 ci.yml 集成（运行单元测试步骤）

1. pytest 前后台启动监控（`--interval 5`，输出到 `test-results/thread-monitor-shardN.log`）；
2. 捕获 pytest 退出码（`set +e` + `PYTEST_RC=$?`），不因 pytest 失败跳过报告；
3. kill 监控进程后立即 `--report` 输出峰值（CI 日志可检索 `threads=` / `pids.max=`）；
4. 监控日志随 `test-results/` artifact 上传，可下载二次分析；
5. 失败诊断步骤补充 `ps` 线程/进程快照 + `pids.max` 读取，失败时直接归因。

### 3.3 本地手动验证

```bash
# 采样 60s（Linux 上 ps 路径生效；Windows 降级 threading.active_count）
python scripts/ci_thread_monitor.py --duration 60 --interval 2 --output m.log
# 输出峰值报告
python scripts/ci_thread_monitor.py --report m.log
```

## 4. 线程池/并发配置优化方向（数据驱动，待监控数据落地后选择）

监控报告落地后按峰值线程数与 `pids.max` 比值决策：

| 方案 | 改动 | 适用条件（峰值占用率） | 权衡 |
|---|---|---|---|
| A: 降为单 worker | `-n 2` → `-n 1` | > 80%（逼近限制） | 无 xdist → signal 方法生效，无 timer 线程累积；单 shard 运行时间 ~2x（预计 10-16min），仍在 timeout-minutes 90 内 |
| B: 收紧超时 | `--timeout 60` → 30 | 30%-80% | 减少挂起 timer 线程滞留；误报风险略升 |
| C: 修测试线程泄漏 | 定位并 join 测试自身线程（error_handler 重试等） | 30%-80% 且线程峰值集中在特定文件 | 根治但需逐个文件排查 |

**优先推荐方案 A**：一次性消除 thread 方法降级路径（signal 方法零线程创建），
且不损失超时保护；代价仅是运行时间翻倍，对 90min 超时窗口余量充足。

## 5. 后续行动

- [ ] 下一次 CI 实跑后下载 `thread-monitor-shard*.log`，核对峰值线程数与 `pids.max` 比值；
- [ ] 按 §4 决策表选择优化方案并修改 ci.yml；
- [ ] 若选方案 C，需新增"线程数回归守卫"测试（测试结束断言
      `threading.active_count()` 不超过基线）。
