# CI 健康度看板

> **用途**：追踪 CI 流水线回归测试通过率与耗时趋势，识别长期退化项与技术债。
>
> **更新频率**：每次合入 main 分支后由 CI 自动追加一行（人工补填亦可）。
>
> **维护人**：研发团队 owner（轮值）。
>
> **数据来源**：GitHub Actions `单元测试 (Python 3.10)` job 的 `test-results-unit-py3.10` artifact + `junit.xml`。

---

## 一、关键指标定义

| 指标 | 定义 | 健康阈值 | 告警阈值 |
|------|------|----------|----------|
| 通过率 (Pass Rate) | `passed / (passed + failed + errors)` | ≥ 99% | < 95% |
| 单测总耗时 (Total Duration) | junit.xml `<testsuite time="...">` 累计 | ≤ 300s | > 600s |
| 失败用例数 (Failures) | `failed + errors` | 0 | ≥ 1 |
| 跳过用例数 (Skipped) | `skipped` | ≤ 总数 5% | > 总数 10% |
| mypy 阻塞模块数 | env_config_manager + network_config | 0（全绿） | ≥ 1 |
| 覆盖率 (Coverage) | `coverage.xml` line coverage | ≥ 70% | < 40% |

---

## 二、合入趋势记录

> 每次合入 main 追加一行。**Trend** 列：`↑` 改善 / `→` 持平 / `↓` 退化。

| 日期 | Commit (短) | 通过率 | 失败 | 跳过 | 耗时(s) | 覆盖率 | mypy阻塞 | Trend | 备注 |
|------|-------------|--------|------|------|---------|--------|----------|-------|------|
| 2026-07-29 | `v1.2.1-fix-secure-manager-return` | 100% (66/66) | 0 | 0 | 22.78 | — | 0 | ↑ | 修复 9 个 SecureManager CI 失败；fixture 提取至 conftest.py |
| 2026-08-03 | `8685bb5` | 99.0% (1489/1504) | 0 | 15 | 77.48 | — | 0 | ↑ | fix(ci): 分片排除 sandbox 测试文件并修复 model_cache_utils 导入路� |
| 2026-08-04 | `64f2257` | 99.0% (1489/1504) | 0 | 15 | 69.23 | — | 0 | ↑ | feat(memory): TLM-L3 Markdown 双向同步 + gitleaks CI 修复 (#163) |
| 2026-08-04 | `5c77ac1` | 99.0% (1489/1504) | 0 | 15 | 77.33 | — | 0 | ↑ | fix(ci): 修复 Web 模块测试依赖缺失 |
| 2026-08-04 | `2de5884` | 99.0% (1489/1504) | 0 | 15 | 77.81 | — | 0 | ↑ | docs(pypi): README 更新 PyPI 安装方式 + 新增 release-docs workflow |
| 2026-08-04 | `cd15739` | 99.0% (1489/1504) | 0 | 15 | 76.05 | — | 0 | ↑ | release(pypi): 升级 l2-p99-monitor 到 1.0.1 |
| 2026-08-04 | `65a186b` | 99.0% (1489/1504) | 0 | 15 | 75.25 | — | 0 | ↑ | fix(ci): 修复 core-invariants-guard 的 secrets-in-if 违规导致 workflow � |
| 2026-08-04 | `0056efd` | 99.0% (1489/1504) | 0 | 15 | 66.33 | — | 0 | ↑ | chore(hooks): 同步 packages 版 tlm-hook-failsafe 到源版(含编码检查/CI |
| 2026-08-04 | `ff83ff4` | 99.0% (1490/1505) | 0 | 15 | 76.04 | — | 0 | ↑ | fix(reranker): verify 脚本对齐真实实现(类名 SkillReranker/类属性默 |
| 2026-08-04 | `8b78511` | 98.7% (1494/1513) | 0 | 19 | 108.10 | — | 0 | ↑ | release(tlm-hook-failsafe): bump to 1.1.7 + license 迁移 + Release 修复 + AP |
| 2026-08-04 | `8ffe05c` | 98.7% (1494/1513) | 0 | 19 | 96.23 | — | 0 | ↑ | chore(hooks): 同步 WORKFLOW_SIM 段到发布包副本, 保持与 dev 版模板 |
| 2026-08-04 | `657daae` | 98.7% (1494/1513) | 0 | 19 | 106.81 | — | 0 | ↑ | fix(ci): docker-compose.yml 添加 build 段作为 CI fallback |
| 2026-08-05 | `26a9c07` | 98.7% (1505/1525) | 0 | 20 | 100.62 | — | 0 | ↑ | docs(observability): 补充 Shard3 实测监控数据与 -n 1 决策依据（不 |
| 2026-08-06 | `60182b1` | 98.5% (1493/1516) | 0 | 23 | 80.07 | — | 0 | ↑ | Merge pull request #308 from nzt47/fix/ci-validation-clean |
| 2026-08-06 | `b0b1a43` | 98.5% (1493/1516) | 0 | 23 | 94.26 | — | 0 | ↑ | docs(release): 预检工具包 v1.0.0-preflight 发布日志与回滚指南 |
| 2026-08-06 | `9871aa6` | 98.1% (1488/1517) | 0 | 29 | 92.81 | — | 0 | ↑ | ci(release): GitLab 版增强发布日志 + GitHub Release 失败重试与幂等 |
| 2026-08-06 | `4e28619` | 98.1% (1488/1517) | 0 | 29 | 77.77 | — | 0 | ↑ | ci(release): release-auto.yml 回填 GitHub Release 重试逻辑（3x10s/409-422 |
| 2026-08-06 | `cfde372` | 98.1% (1488/1517) | 0 | 29 | 98.38 | — | 0 | ↑ | ci(release): release-auto.yml 增强排查日志（时间戳/API 参数/token � |
| 2026-08-06 | `ac46383` | 98.1% (1488/1517) | 0 | 29 | 84.64 | — | 0 | ↑ | docs(release): v1.0.0 标签前移与分支同步操作日志归档 (#352) |
| 2026-08-07 | `18fbf93` | 96.8% (1499/1549) | 0 | 50 | 159.30 | — | 0 | ↑ | feat(release): 新增发布前自动检查工作流 release-precheck.yml + 新� |
| 2026-08-07 | `b08ae5f` | 98.7% (1628/1649) | 0 | 21 | 97.56 | — | 0 | ↑ | docs(release): v1.0.0 发布收尾最终执行归档报告（PR #354 合并 + � |
| 2026-08-07 | `896b7ba` | 98.7% (1628/1649) | 0 | 21 | 95.31 | — | 0 | ↑ | docs(release): 新成员 Release v1.0.0 操作手册 + 流程知识库 Wiki 页� |
| 2026-08-07 | `0456b43` | 98.8% (1595/1614) | 0 | 19 | 116.24 | — | 0 | ↑ | fix(tests): delete_many 顺序删除测试改用 slugify slug（Linux 大小写� |
| 2026-08-08 | `6dc9427` | 99.4% (1595/1604) | 0 | 9 | 83.80 | — | 0 | ↑ | fix(knowledge): HealthReport 新增 to_dict 序列化方法修复 lint 接口 50 |
| 2026-08-09 | `23f9b64` | 99.5% (1708/1717) | 0 | 9 | 106.44 | — | 0 | ↑ | test(perf): 7 个 filter 依赖测试加 @pytest.mark.serial（Shard 2） |
| 2026-08-10 | `33136c1` | 99.5% (1708/1717) | 0 | 9 | 112.73 | — | 0 | ↑ | test(knowledge): 4 个日志捕获测试加 @pytest.mark.serial（Shard 4 flake  |
| 2026-08-10 | `305282c` | 99.5% (1708/1717) | 0 | 9 | 116.13 | — | 0 | ↑ | fix(ci): 修复 performance 测试 import 副作用全局禁用日志致 Shard 4 |
| 2026-08-10 | `296c8e6` | 99.5% (1708/1717) | 0 | 9 | 113.68 | — | 0 | ↑ | fix(logging): safe_logger AuditLogger 补 makedirs 防 CI 全新 checkout 无 lo |
| 2026-08-10 | `2b6d51d` | 99.5% (1708/1717) | 0 | 9 | 113.75 | — | 0 | ↑ | fix(ci): observability-ci 触发 paths 纳入 agent/log_system 防修复静默� |
| 2026-08-10 | `6ada3dc` | 99.5% (1708/1717) | 0 | 9 | 104.13 | — | 0 | ↑ | test(integration): test_orchestrator logging.disable 包进 try/finally 防断� |
| 2026-08-10 | `dbf9d57` | 99.5% (1708/1717) | 0 | 9 | 108.56 | — | 0 | ↑ | ci(guard): 集成 logging.disable 泄漏扫描双防线（pre-commit + ci.yml） |
| 2026-08-10 | `363176d` | 99.5% (1708/1717) | 0 | 9 | 111.31 | — | 0 | ↑ | docs(troubleshooting): 归档 logging 防线全景 SVG 与 Mermaid 源文件 |
| 2026-08-10 | `09a3d81` | 99.5% (1708/1717) | 0 | 9 | 114.13 | — | 0 | ↑ | docs(troubleshooting): 修复失效链接 - 检出 develop 归档文档并删除 |
| 2026-08-10 | `57b6494` | 98.3% (1741/1772) | 0 | 31 | 138.32 | — | 0 | ↑ | Merge branch 'develop' into temp-fix |
| 2026-08-11 | `edbe8cd` | 98.3% (1741/1772) | 0 | 31 | 124.91 | — | 0 | ↑ | test(ci): env 锁 msvcrt 跨平台 mock + defect 看门狗加 xfail(strict=False |
| 2026-08-11 | `b07399a` | 98.3% (1741/1772) | 0 | 31 | 139.99 | — | 0 | ↑ | Merge commit '2340eaa9' into temp-fix |
| 2026-08-15 | `cac72f0` | 99.3% (1985/1998) | 0 | 13 | 163.56 | — | 0 | ↑ | Merge pull request #634 from nzt47/develop |
| 2026-08-15 | `86adcfe` | 99.3% (1985/1998) | 0 | 13 | 159.96 | — | 0 | ↑ | chore(git): master .gitignore 同步 wait_checkout_master.ps1 与 quality_gate_r |
| 2026-08-16 | `255ab9b` | 99.3% (1985/1998) | 0 | 13 | 157.78 | — | 0 | ↑ | style(workbench): 面板标题贴左，消除窄面板大片留白 |
| 2026-08-26 | `64ded0f` | 98.8% (1938/1962) | 0 | 24 | 164.29 | — | 0 | ↑ | Merge pull request #832 from nzt47/fix/health-probes-json-tolerance |
| 2026-08-26 | `d304ce5` | 98.8% (1938/1962) | 0 | 24 | 169.03 | — | 0 | ↑ | Merge pull request #836 from nzt47/docs/delivery-closeout-report |
| 2026-08-26 | `938f376` | 98.3% (2050/2085) | 0 | 35 | 107.25 | — | 0 | ↑ | feat(permission): RBAC+ABAC 三层权限架构升级(PermissionGateway/JSON日� |
| 2026-08-26 | `8b0d06a` | 98.3% (2050/2085) | 0 | 35 | 135.95 | — | 0 | ↑ | fix(docs): 交付报告策略配置链接修正为上级相对路径(../data/) |
| 2026-08-26 | `f7d23fa` | 98.3% (2050/2085) | 0 | 35 | 135.82 | — | 0 | ↑ | docs(delivery): 交付报告补充 CI 验证结果与遗留问题记录 |
| 2026-08-28 | `704a4ab` | 98.1% (1987/2026) | 0 | 39 | 166.30 | — | 0 | ↑ | docs(delivery): 健康度交付报告遗留问题处理记录（监控栈/性能 |
| YYYY-MM-DD | `<sha7>` | — | — | — | — | — | — | — | 模板占位行，请替换 |

---

## 三、测试文件明细

> 按文件粒度追踪通过率与耗时，定位退化热点。

| 测试文件 | 用例数 | 通过 | 失败 | 跳过 | 耗时(s) | 最近失败日期 | 备注 |
|----------|--------|------|------|------|---------|--------------|------|
| `tests/unit/test_network_config.py` | 28 | 28 | 0 | 0 | ~12 | 2026-07-29 前 | v1.2.1 修复后稳定 |
| `tests/unit/test_network_config_save_regression.py` | 22 | 22 | 0 | 0 | ~10 | — | 历史回归套件 |
| `tests/unit/test_env_hot_reload.py` | — | — | — | — | — | — | 待补填 |
| `tests/unit/test_env_config_manager.py` | 2 | 2 | 0 | 0 | <1 | — | 单例 return 回归测试 |
| `<其他文件>` | — | — | — | — | — | — | 请补充 |

---

## 四、阻塞项与技术债跟踪

> 记录已知非阻塞项，待条件成熟后转为阻塞。

| 项目 | 当前状态 | 目标状态 | 阻塞原因 | 跟踪 Issue | 计划转阻塞日期 |
|------|----------|----------|----------|------------|----------------|
| `env_config_manager.py` mypy 严格检查 | ✅ 阻塞 | 阻塞 | — | — | 已达成 (2026-07-29) |
| `network_config.py` mypy 严格检查 | ⚠️ 非阻塞 (`\|\| true`) | 阻塞 | 29 个历史类型错误（隐式 Optional / None 不可索引 / Returning Any） | 待创建 | 待债务清理 |
| `_mock_env_config_in_ci` fixture 复用 | ✅ 已提取至 conftest.py | — | — | — | 已完成 (2026-07-29) |

### network_config.py 29 个类型错误分类（待修复清单）

| 错误类型 | 错误码 | 数量 | 修复难度 | 备注 |
|----------|--------|------|----------|------|
| 隐式 Optional (`def f(x: str = None)`) | `[assignment]` | 4 | 低 | 改为 `str \| None = None` |
| 变量先 `= None` 后赋 dict | `[assignment]` | 6 | 中 | 需补类型标注或重构初始化 |
| None 不可索引/赋值 | `[index]` | 5 | 中 | 同上，根因相同 |
| 返回值类型不匹配 | `[return-value]` | 3 | 中 | 函数声明 `dict` 实际返回 `dict \| None` |
| Returning Any | `[no-any-return]` | 5 | 低 | 加 `cast()` 或修正返回类型 |
| 参数类型不匹配 | `[arg-type]` | 4 | 中 | `object` → `str` 收紧 |
| 缺少类型标注 | `[var-annotated]` | 1 | 低 | `updates: dict = {}` |
| 属性不存在 | `[attr-defined]` | 1 | 低 | `object.startswith` 需 type narrow |

---

## 五、告警规则

| 触发条件 | 动作 | 通知渠道 |
|----------|------|----------|
| 通过率 < 95% | 创建 GitHub Issue (`ci-regression` 标签) | GitHub Issues + 钉钉机器人 |
| 单测总耗时 > 600s | 在 PR 评论贴出耗时 Top 5 测试 | GitHub PR 评论 |
| mypy 阻塞模块数 ≥ 1 | CI 直接失败，阻塞合入 | GitHub Checks |
| 覆盖率 < 40% | CI 直接失败（`fail_under = 40`） | GitHub Checks |

> 钉钉/邮件通知配置详见 `.github/workflows/ci.yml` 与 project_memory 中 CI failure notifications 约定。

---

## 六、填充说明

### 6.1 从 junit.xml 提取数据

```bash
# 通过率
python -c "
import xml.etree.ElementTree as ET
tree = ET.parse('test-results/junit.xml')
ts = tree.getroot()
total = int(ts.get('tests'))
failed = int(ts.get('failures')) + int(ts.get('errors'))
skipped = int(ts.get('skipped'))
passed = total - failed - skipped
print(f'通过率: {passed}/{total} ({passed/total*100:.1f}%)')
print(f'耗时: {float(ts.get(\"time\")):.2f}s')
"
```

### 6.2 从 coverage.xml 提取覆盖率

```bash
python -c "
import xml.etree.ElementTree as ET
tree = ET.parse('coverage.xml')
root = tree.getroot()
print(f'覆盖率: {float(root.get(\"line-rate\"))*100:.1f}%')
"
```

### 6.3 追加趋势行模板

```markdown
| 2026-MM-DD | `<sha7>` | XX.X% (N/N) | N | N | N.NN | NN% | N | ↑/→/↓ | 简要说明 |
```

---

## 七、相关文档

- CI 流水线配置：[`.github/workflows/ci.yml`](file:///c:/Users/Administrator/agent/.github/workflows/ci.yml)
- 最新一次 CI 报告：[`docs/reports/ci_pipeline_report_20260729.md`](file:///c:/Users/Administrator/agent/docs/reports/ci_pipeline_report_20260729.md)
- v1.2.1 修复发布说明：[`docs/releases/`](file:///c:/Users/Administrator/agent/docs/releases/)
- 测试环境隔离最佳实践：[`docs/best_practices/test_env_isolation_pattern.md`](file:///c:/Users/Administrator/agent/docs/best_practices/test_env_isolation_pattern.md)
- 代码审查摘要：[`docs/reviews/env_config_manager_return_fix_review.md`](file:///c:/Users/Administrator/agent/docs/reviews/env_config_manager_return_fix_review.md)

---

## 八、变更日志

| 日期 | 变更 | 操作人 |
|------|------|--------|
| 2026-07-29 | 初始化看板模板；记录 v1.2.1 修复基线；登记 network_config mypy 技术债 | Yi-Jing Coding Agent |
| 2026-08-15 | 追加 §九 仓库维护记录（git gc 优化 + TASK-06 清理数据） | Yi-Jing Coding Agent |

---

## 九、仓库维护记录

> 记录仓库对象库优化与临时数据清理，追踪仓库健康度（与 CI 通过率看板互补）。

| 日期 | 类型 | 数据 | 影响 | 备注 |
|------|------|------|------|------|
| 2026-08-15 | git gc 优化 | loose objects 3.09 MiB(673 个)→**0**；packs 3→2；size-pack 57.57→56.37 MiB | HEAD/worktree(9)/并行会话**零影响** | TASK-06 收尾；默认参数（2 周 prune + reflog 90 天保护），空闲窗口执行；详见 [Git_Archive_Cleanup_SOP.md §三-E](../Git_Archive_Cleanup_SOP.md) |
| 2026-08-15 | 临时数据清理 | TASK-06 草稿 ×3 删除（ARCHIVED 状态，内容归档于验证报告）；临时分支/worktree 无 | 无 | 详见 [TASK-06_结案总结_20260815.md §十一](../zh/智能体学习机制重构计划/TASK-06_结案总结_20260815.md) |
