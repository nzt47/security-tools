# performance/stress 测试串行隔离修复方案（2026-08-10）

> 背景：预提交护栏 WARN「未发现分片脚本将 performance/stress 纳入串行段 → 可能混入并行矩阵产生 flake」。
> 本次 Shard 6 flaky（test_knowledge_link_perf.py 模块顶层 `logging.disable` 污染同进程日志捕获测试）
> 即 performance 测试混入并行分片的实证——问题虽已通过 fixture 修复，但**结构性风险未除**。

---

## 1. 现状分析

### 分片逻辑（scripts/split_unit_tests.py）

| 模式 | 扫描范围 | 排除 |
|------|----------|------|
| tests/unit（ci.yml） | `tests/unit/*.py` 非递归 | EXCLUDED（1 文件） |
| 全 tests/（observability-ci.yml） | `tests/**/test_*.py` 递归 | EXCLUDED + OBSERVABILITY_CI_ONLY |

**OBSERVABILITY_CI_ONLY（8 文件）**排除理由均为"环境依赖"（chromadb 不兼容 / fixture 未定义 / 需后端服务），
**没有目录级隔离 performance/stress**。当前 `tests/performance/`、`tests/stress/` 下未被排除的文件
（如 test_knowledge_link_perf.py、test_link_perf_*.py 等）**全部进入并行分片**。

### 结构性风险（3 类）

1. **全局副作用污染**：performance/stress 文件常有模块顶层副作用（计时屏蔽 → `logging.disable`、
   环境变量、路径注入），随机分片下污染同进程其他测试（本次实证）
2. **计时不可靠**：性能测试在并行矩阵中受其他 worker 争抢 CPU，阈值断言（如 1000us）易误报
3. **flake 传播**：performance 测试自身 1 次失败 → 所在 shard job 失败 → CI 非绿（与业务代码无关）

## 2. 修复方案（两阶段）

### 阶段 A：目录级排除（立即，消除风险）

`split_unit_tests.py` 全项目模式新增目录级排除集合：

```python
# 【不易】performance/stress 不进并行分片：计时敏感 + 顶层副作用风险，
# 混入并行矩阵产生 flake（2026-08-10 Shard 6 实证）。由独立串行 job 覆盖。
SERIAL_DIRS = (
    "tests/performance/",
    "tests/stress/",
)

# collect_test_files() 内，全项目模式过滤：
if test_root == "tests":
    files = [p for p in files if not any(d in p.as_posix() for d in SERIAL_DIRS)]
```

**影响**：`tests/performance/`、`tests/stress/` 全部移出 6-shard 并行矩阵。
副作用：这些测试暂不在 CI 运行（现状无独立 job）→ 需要阶段 B 补覆盖。

### 阶段 B：独立串行 job（补覆盖）

`observability-ci.yml` 新增 `performance-stress-serial-tests` job：

```yaml
performance-stress-serial-tests:
  name: 性能/压力测试（串行）
  runs-on: ubuntu-latest
  needs: []  # 与并行分片无依赖，独立执行
  steps:
    - uses: actions/checkout@v6
    - uses: actions/setup-python@v6
      with: { python-version: '3.11' }
    - run: pip install pytest pytest-timeout && pip install -e .
    - name: 串行运行 performance/stress
      run: |
        python -m pytest tests/performance tests/stress -v --tb=short \
          --timeout=300 -p no:randomly   # 固定顺序，杜绝顺序型 flake
```

> 【变易】job 级 `continue-on-error` 可配置为告警期 true；阈值断言稳定后改 false 硬门禁。
> 与路线图「阶段 2 门禁收紧」策略对齐（先告警后收紧）。

### 方案对比

| 方案 | 效果 | 代价 | 建议 |
|------|------|------|------|
| A 仅目录排除 | 立即消除并行 flake 风险 | performance/stress 暂时无 CI 覆盖 | 先落地 |
| A + B 串行 job | 隔离 + 保覆盖 | 1 个新 job（约 5-10min） | **推荐** |
| 仅修复污染文件 | 治标（本次已做） | 其他文件隐患仍在 | 已做，不替代 A/B |

## 3. 验证方式

- 本地：`python scripts/split_unit_tests.py --root tests --shard 6 --shards 6` 输出不含任何 `tests/performance|stress/` 路径
- CI：push 后观察 6 个分片均无 performance/stress 文件；新串行 job 独立运行
- 护栏：`pre_commit_ci_guard.py` 的 `serial_dirs` 检查转 PASS

## 4. 决策点

| 决策项 | 选项 | 待定 |
|--------|------|------|
| 阶段 B 是否本次一并实施 | A 先行 / A+B 一次落地 | 待确认 |
| 串行 job 是否阻断 | 告警期 true（默认）→ 硬门禁 | 待确认 |
| test_knowledge_link_perf.py 修复是否保留 | 保留（自包含是正确形态，与隔离正交） | 保留 |
