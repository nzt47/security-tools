# CI pytest 插件缺失事故复盘与全局配置契约

> **文档目的**：记录 2026-08-02 ~ 08-03 排查并修复的「多个 CI job 缺 pytest 插件导致
> 整条 workflow 失败」问题全过程，以及 pytest.ini 全局配置 + `--strict-config`
> 下的插件装载契约，供团队后续新增/修改 CI job 时参考。
> **文档版本**：v1.0 | **更新日期**：2026-08-03

---

## 一、事件摘要

| 项 | 内容 |
|----|------|
| 影响范围 | 文档链接预检 job（ci.yml）、工具检索质量 CI、扩展系统健康检查 共 3 个工作流 |
| 根因 | `pytest.ini` 全局配置了 `asyncio_mode = auto` 与 `--timeout=60`，但多个 CI job 只装了 `pytest`，未装对应插件（`pytest-asyncio` / `pytest-timeout`）；pytest 9.1.x + `--strict-config` 把「未知配置项」从告警升级为**硬错误** |
| 直接后果 | ① `Unknown config option: asyncio_mode`（exit code 4）；② `unrecognized arguments: --timeout=60 --timeout-method=thread` |
| 连带后果 | 单元测试与文档链接预检 CI 失败、工具检索质量 CI 失败、扩展系统健康检查失败 |
| 修复 | 3 个 workflow 的安装依赖步骤补装 `pytest-asyncio` / `pytest-timeout`；文档链接预检测试加 win32 平台隔离 skipif |
| 验证 | 修复后各 workflow 独立 run 通过；后续 run 30775335221 中 9 个 workflow 的对应 job 全部 success |

---

## 二、全局配置契约（不易，先记住结论）

`pytest.ini` 是**所有 pytest 执行点的全局配置中心**（含 CI 里每个跑 pytest 的 job）。

```ini
# pytest.ini 关键全局项（与本次事故直接相关）
asyncio_mode = auto          # 依赖 pytest-asyncio 插件注册
addopts = --timeout=60 --timeout-method=thread   # 依赖 pytest-timeout 插件注册
          --strict-config     # 未知配置项 → 硬错误（不再仅告警）
```

**核心规则**：

1. **配置项 ≠ 插件**。`asyncio_mode`、`--timeout` 这些配置项本身没有任何作用，
   必须由对应插件（`pytest-asyncio`、`pytest-timeout`）注册后才会被 pytest 识别。
2. **任何跑 pytest 的 job，安装依赖必须覆盖 pytest.ini 用到的全部插件**。缺任一个，
   在 pytest 9.1.x + `--strict-config` 下直接硬失败。
3. **本地与 CI 的 pytest 版本差异会放大问题**：本地 9.0.x 对未知配置项仅告警不阻塞，
   CI 9.1.x 升级为硬错误——「本地能跑、CI 失败」通常是这类插件缺失。
4. **新增测试若依赖平台专属命令（如 Windows PowerShell），必须加平台 skipif**，
   否则在其它 OS 的 runner 上收集阶段就 `FileNotFoundError`。

### 最小插件覆盖清单（跑本项目 pytest 的 job 必须包含）

```bash
pip install pytest pytest-timeout pytest-asyncio
# 视 job 需要追加: pytest-cov pytest-mock pytest-xdist pytest-randomly psutil
```

---

## 三、事故时间线

### 事故 1：文档链接预检 job 缺 pytest-timeout

- **现象**：`unrecognized arguments: --timeout=60 --timeout-method=thread`
  （pytest 不识别 `--timeout` 参数，因为 `pytest-timeout` 未安装）
- **根因**：ci.yml 的「安装pytest」步骤只装了 `pytest`，而该 job 跑锚点回归测试
  时会继承 pytest.ini 的全局 addopts（含 `--timeout=60`）。
- **修复**：安装步骤补装 `pytest-timeout`。

### 事故 2：多个 job 缺 pytest-asyncio（`Unknown config option: asyncio_mode`）

- **现象**：`Unknown config option: asyncio_mode`，exit code 4
  （`--strict-config` 将未知配置项判为硬错误）
- **根因**：`pytest.ini` 全局 `asyncio_mode = auto` 需要 `pytest-asyncio` 注册，
  但以下 3 处 job 均未安装：
  1. ci.yml 文档链接预检 job（与事故 1 同步骤，一并补齐）
  2. tool-retrieval-ci.yml 的 retrieval-quality 与 negative-samples 两个 job
  3. extension-health-check.yml 的扩展系统单元测试 job
- **修复**：上述安装步骤统一补装 `pytest-asyncio`（同时补 `pytest-timeout` 防同类问题）。
- **提交**：`64af6583`（ci.yml）、`d8b8e98f`（tool-retrieval-ci）、`c5779c23`（extension-health-check）

### 事故 3：文档链接预检测试在 Linux runner 上找不到 PowerShell

- **现象**：`FileNotFoundError: [Errno 2] No such file or directory: 'powershell'`
- **根因**：`tests/unit/test_precheck_docs_anchor_links.py` 通过 subprocess 调用
  Windows PowerShell（`precheck_docs.ps1`），但 unit-tests job（ubuntu-latest）
  也会收集 `tests/unit/` 下的该文件。
- **修复**：与 `test_precommit_hook_blocking.py` 一致，加平台隔离 skipif：

  ```python
  pytestmark = pytest.mark.skipif(
      sys.platform != "win32",
      reason="依赖 Windows PowerShell（precheck_docs.ps1），仅 Windows 可运行",
  )
  ```

- **验证**：本地 Windows 4 个测试通过（12.50s）。

### 事故 4（遗留，非代码问题）：云枢单元测试被 GitHub runner 取消

- **现象**：`The runner has received a shutdown signal`，单元测试 3.10/3.11/3.12
  连续 4 次在测试执行中被取消（85% 附近），**无任何断言失败**，测试日志几乎全部丢失
  （日志文件仅几百字节）。
- **判断**：GitHub Actions runner 基础设施问题，非代码回归。
  证据：历史 run `30758722007` 中 3.11/3.12 曾完整跑通。
- **建议**：错峰 rerun；若频繁复现，可考虑调低 `-n auto` 并行度以降低 runner 资源压力。

---

## 四、预防措施 / 新增 CI job 检查清单

新增或修改任何跑 pytest 的 CI job 时，逐一核对：

- [ ] 安装步骤覆盖 pytest.ini 全部插件（至少 `pytest-timeout` + `pytest-asyncio`）
- [ ] 新增测试若调用平台专属命令，已加 `skipif(sys.platform != ...)` 平台隔离
- [ ] 改动 `.github/workflows/*.yml` 后，观察完整 workflow run 是否全绿（含所有 job）
- [ ] 修改 `pytest.ini` 新增配置项时，同步检查所有 pytest 执行点的插件覆盖

---

## 五、关联文档

- [Pre-commit Hook BOM 事故复盘与 Hook 逻辑说明](../ci_guidelines/precommit_hook_bom_incident_report.md)
- [Pre-commit Hook 复用指南](../ci_guidelines/precommit_hook_reuse_guide.md)
