# CI 覆盖率数据文件上传修复提示词（实战验证版）

> 本提示词基于 GitHub Actions `actions/upload-artifact@v4` 隐藏文件上传问题的一次真实修复，已在 CI run `29143680490` 验证通过（覆盖率 51% > 阈值 40%）。

---

## 一、什么时候使用本提示词

当你的 GitHub Actions CI 出现以下**任一**症状时：

| 症状 | 表现 |
|------|------|
| `coverage report` 报 `No data to report` | 覆盖率检查 job 找不到数据文件 |
| `coverage combine` 报 `No data to combine` | 合并步骤找不到 `.coverage` 文件 |
| `coverage report --fail-under=N` 静默退出 | 没有输出覆盖率表格就失败 |
| 覆盖率检查 job 被跳过 | `needs: [unit-tests]` 依赖的测试 job 失败 |
| artifact 下载后 `ls .coverage` 找不到文件 | 隐藏文件被 upload-artifact@v4 丢失 |

**根因**：`actions/upload-artifact@v4` 对以 `.` 开头的隐藏文件（如 `.coverage`）上传不稳定。`coverage report`/`combine` 只认 `.coverage` 这个 SQLite 数据文件，不认 HTML 或 XML 报告。

---

## 二、修复策略（改名中转四步法）

```
单元测试 Job                          覆盖率检查 Job
─────────────                         ─────────────
pytest --cov=<pkg>                    download-artifact (path: .)
  ↓                                     ↓
生成 .coverage (SQLite)               coverage_raw.data 落在根目录
  ↓                                     ↓
cp .coverage coverage_raw.data        cp coverage_raw.data .coverage  ← 还原
  ↓                                     ↓
upload-artifact:                      coverage combine || true
  - coverage_raw.data                   ↓
  - coverage.xml                      coverage report                  ← 输出表格
  - htmlcov/                            ↓
                                      coverage report --fail-under=N   ← 阈值断言
```

**四步**：
1. **改名**：`cp .coverage coverage_raw.data`（避免隐藏文件上传丢失）
2. **上传**：三件套（data + xml + html）
3. **下载到 `.`**：让文件直接落在工作根目录
4. **还原 + 执行**：`cp coverage_raw.data .coverage` → `combine` → `report`

---

## 三、完整 CI 配置模板

### 3.1 顶层环境变量

```yaml
env:
  PYTHON_VERSION: '3.10'        # 覆盖率检查 job 使用的 Python 版本
  COVERAGE_THRESHOLD: 40        # 覆盖率阈值（按项目实际调整）
```

### 3.2 单元测试 Job（上传覆盖率数据）

```yaml
  unit-tests:
    name: 单元测试 (Python ${{ matrix.python-version }})
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        python-version: ['3.10', '3.11', '3.12']
    steps:
      - name: 检出代码
        uses: actions/checkout@v4

      - name: 设置Python环境
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
          cache: 'pip'

      - name: 安装依赖
        run: |
          python -m pip install --upgrade pip
          pip install pytest pytest-cov pytest-xdist pytest-mock pytest-timeout pytest-asyncio pytest-randomly
          pip install -e .

      - name: 运行单元测试
        run: |
          pytest tests/unit/ \
            -v \
            --tb=short \
            --cov=agent \
            --cov-report=xml \
            --cov-report=html \
            --cov-report=term-missing \
            -m "not slow and not skip_ci" \
            --timeout=300

      # 关键修复 1：把隐藏文件 .coverage 改名为非隐藏文件
      # Why: actions/upload-artifact@v4 对 . 开头的隐藏文件上传不稳定
      - name: 准备覆盖率数据
        run: |
          cp .coverage coverage_raw.data
          ls -la coverage_raw.data coverage.xml

      # 关键修复 2：上传三个产物（原始 SQLite 数据 + XML 报告 + HTML 报告）
      - name: 上传覆盖率报告
        uses: actions/upload-artifact@v4
        with:
          name: coverage-report-py${{ matrix.python-version }}
          path: |
            htmlcov/
            coverage.xml
            coverage_raw.data
          retention-days: 30

      - name: 上传测试结果
        uses: actions/upload-artifact@v4
        if: always()
        with:
          name: test-results-unit-py${{ matrix.python-version }}
          path: test-results/
          retention-days: 30
```

### 3.3 覆盖率检查 Job（下载 + 还原 + 报告）

```yaml
  coverage-check:
    name: 覆盖率检查
    runs-on: ubuntu-latest
    needs: [unit-tests, integration-tests]
    steps:
      - name: 检出代码
        uses: actions/checkout@v4

      - name: 设置Python环境
        uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}
          cache: 'pip'

      - name: 安装依赖
        run: |
          python -m pip install --upgrade pip
          pip install pytest pytest-cov pytest-timeout
          pip install -e .

      # 关键修复 3：下载到 path: .（当前目录），让文件直接落在根目录
      - name: 下载覆盖率报告
        uses: actions/download-artifact@v4
        with:
          name: coverage-report-py${{ env.PYTHON_VERSION }}
          path: .

      - name: 检查覆盖率
        run: |
          echo "=== 检查覆盖率 ==="
          ls -la coverage_raw.data coverage.xml || true

          # 关键修复 4：把非隐藏名还原成 .coverage，coverage 命令才能识别
          cp coverage_raw.data .coverage

          # 合并覆盖率数据（多 Python 版本矩阵场景）
          # Why: 单版本时 "No data to combine" 是正常的，|| true 容错
          coverage combine || true

          # 生成覆盖率报告（输出表格供人看）
          coverage report

          # 检查是否达标（阈值断言，低于 N% 则退出码非 0）
          coverage report --fail-under=${{ env.COVERAGE_THRESHOLD }}

      - name: 生成覆盖率报告
        run: |
          coverage html -d test_reports/htmlcov
          coverage xml -o test_reports/coverage.xml

      - name: 上传完整覆盖率报告
        uses: actions/upload-artifact@v4
        with:
          name: full-coverage-report
          path: test_reports/
```

---

## 四、验证清单

修复后逐项确认：

- [ ] 单元测试 job 的 "准备覆盖率数据" 步骤输出 `coverage_raw.data` 存在（`ls` 显示文件大小 > 0）
- [ ] 单元测试 job 的 "上传覆盖率报告" 步骤 artifact 包含 `coverage_raw.data`
- [ ] 覆盖率检查 job 的 "下载覆盖率报告" 步骤后 `ls` 能看到 `coverage_raw.data`
- [ ] 覆盖率检查 job 的 `coverage report` 输出真实覆盖率表格（有 `TOTAL` 行和百分比）
- [ ] `coverage report --fail-under=N` 通过（退出码 0，覆盖率 >= 阈值）

---

## 五、排错指南

| 症状 | 可能原因 | 处理 |
|------|---------|------|
| `No data to combine` | `.coverage` 未还原 | 确认 `cp coverage_raw.data .coverage` 在 `coverage combine` 之前执行 |
| `No data to report` | `.coverage` 文件为空或不存在 | 确认 pytest 命令带 `--cov=<package>` 且包名与代码目录一致 |
| `coverage_raw.data` 不存在 | upload-artifact 未上传 | 检查 `path:` 是否包含 `coverage_raw.data`，确认 `cp .coverage coverage_raw.data` 成功 |
| `coverage report` 输出 0% | `--cov=<pkg>` 未生效 | 确认 `<pkg>` 与 `pip install -e .` 安装的包名一致 |
| `--fail-under` 通过但覆盖率低 | 阈值设置过低 | 调高 `COVERAGE_THRESHOLD` 到合理值 |
| 覆盖率检查 job 被跳过 | `needs` 依赖的 job 失败 | 先修复单元测试 job 的失败，覆盖率检查才会执行 |
| `No data to combine`（单版本） | 正常现象 | 单版本矩阵没有 `.coverage.*` 文件可合并，`|| true` 容错即可 |
| artifact 下载后文件消失 | 下载到子目录而非根目录 | 确认 `download-artifact` 的 `path: .`（当前目录） |

---

## 六、关键不变量（易踩坑点）

1. **`.coverage` 是 SQLite 二进制数据库文件**，不是文本报告。`coverage report`/`combine`/`xml`/`html` 命令都只认这个文件。
2. **不能只上传 `htmlcov/` 或 `coverage.xml`**：HTML 是渲染产物，XML 是 Cobertura 格式报告，`coverage report` 无法从中反推数据。
3. **隐藏文件上传风险**：任何以 `.` 开头的文件（`.coverage`、`.pytest_cache` 等）在 `upload-artifact@v4` 上都需谨慎，建议统一改名中转。
4. **`coverage combine` 默认读取当前目录 `.coverage`**：所以必须先 `cp coverage_raw.data .coverage` 还原文件名。
5. **`--cov=<package>` 必须与 `pip install -e .` 安装的包名一致**：否则 `.coverage` 里没有数据，`report` 输出 0%。
6. **`actions/upload-artifact@v4` 与 v3 的行为差异**：v4 对隐藏文件处理更严格，v3 能正常上传 `.coverage` 但 v4 不行。

---

## 七、实战验证证据

本方案在以下环境验证通过：

- **CI run**: `29143680490`（GitHub Actions, ubuntu-latest）
- **Python 版本**: 3.10 / 3.11 / 3.12 矩阵
- **coverage 版本**: 7.15.0
- **artifact 大小**: `coverage_raw.data` = 139,264 bytes（SQLite 数据文件）
- **覆盖率结果**: TOTAL 56129 Stmts, 27320 Miss, **51% Cover** > 阈值 40%
- **关键日志**:
  ```
  -rw-r--r-- 1 runner runner 2073905 Jul 11 08:24 coverage.xml
  -rw-r--r-- 1 runner runner  139264 Jul 11 08:24 coverage_raw.data
  No data to combine                                          ← 单版本正常
  TOTAL                                              56129  27320    51%      ← 通过
  ```

---

## 八、复用说明

将本文档作为模板，按项目实际情况调整：

| 参数 | 示例值 | 说明 |
|------|--------|------|
| `PYTHON_VERSION` | `'3.10'` | 覆盖率检查 job 使用的 Python 版本 |
| `COVERAGE_THRESHOLD` | `40` | 覆盖率阈值（按项目实际调整） |
| `python-version` 矩阵 | `['3.10', '3.11', '3.12']` | 单元测试的 Python 版本矩阵 |
| `--cov=<package>` | `--cov=agent` | 被测包名，须与 `pip install -e .` 一致 |
| `-m` 标记 | `"not slow and not skip_ci"` | pytest 标记过滤 |
| `--timeout` | `300` | 单测超时秒数 |
| `retention-days` | `30` | artifact 保留天数 |

**核心模式（改名上传 → 改名还原 → combine → report）保持不变**，只调整参数。

---

## 九、扩展：多版本矩阵合并

如果需要合并多个 Python 版本的覆盖率数据（而非只取单版本）：

```yaml
# 覆盖率检查 job 中，下载所有版本的 artifact
- name: 下载所有版本的覆盖率数据
  uses: actions/download-artifact@v4
  with:
    pattern: coverage-report-py*
    path: coverage-data/
    merge-multiple: false

- name: 合并多版本覆盖率
  run: |
    # 把每个版本的数据还原成 .coverage.<version> 格式
    # coverage combine 会合并所有 .coverage.* 文件
    for dir in coverage-data/coverage-report-py*; do
      version=$(basename "$dir" | sed 's/coverage-report-py//')
      cp "$dir/coverage_raw.data" ".coverage.$version"
    done
    coverage combine
    coverage report --fail-under=${{ env.COVERAGE_THRESHOLD }}
```

> 注意：单版本场景不需要这个扩展，`coverage combine || true` 即可。
