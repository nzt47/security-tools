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
| 2026-11-16 | `7c435ed` | 100.0% (14/14) | 0 | 0 | 62.86 | — | 0 | ↑ | docs(handoff): §11.135 第八十六轮（hub 蓝按钮清零 / 分片二 / 字号档位三个真缺陷 / ui/Textarea / 两条 CI 空 |
| 2026-11-16 | `45946ca` | 100.0% (14/14) | 0 | 0 | 32.77 | — | 0 | ↑ | docs(handoff): 4 条 Owner 拍板项按本轮结论落定（pyproject 记 done；token-levers / judge-cost / |
| 2026-11-16 | `4c823e6` | 100.0% (14/14) | 0 | 0 | 62.89 | — | 0 | ↑ | fix(css): 全站滚动条改用 index.css 承载（theme.css 根本没被 import，上一版规则没进产物） |
| 2026-11-16 | `e726d07` | 100.0% (14/14) | 0 | 0 | 64.07 | — | 0 | ↑ | docs(handoff): Electron 手工验收项补环境核实（本机无 electron 二进制 / Python Playwright 无 Electr |
| 2026-11-16 | `59c7e0d` | 100.0% (14/14) | 0 | 0 | 52.35 | — | 0 | ↑ | docs(handoff): 记 llm-monitor-persist-rolling（落盘改滚动快照 / 内存 200 条 / 现场验证两条路径） |
| 2026-11-16 | `728ee51` | 100.0% (14/14) | 0 | 0 | 64.46 | — | 0 | ↑ | refactor(ui): hub 的 BTN/INPUT 两个自造家族整批换原语 —— raw_button 604→362 / raw_input 227→ |
| 2026-11-16 | `ab01c77` | 100.0% (14/14) | 0 | 0 | 47.39 | — | 0 | ↑ | docs(handoff): §11.137 第八十七轮 —— hub 两个自造家族清零 / theme.css 删除 / 「窗口取值参考」三次返工成浮层 |
| 2026-11-17 | `1f4c199` | 100.0% (14/14) | 0 | 0 | 43.69 | — | 0 | ↑ | docs(handoff): §11.140 —— 通用按钮 codemod 扫掉 55 个手写按钮（raw_button 329 → 274） |
| 2026-11-17 | `64255da` | 100.0% (14/14) | 0 | 0 | 51.85 | — | 0 | ↑ | docs(handoff): §11.143 —— 三个搜索框改用 Input 的 leading/trailing 槽（raw_input 122 → 119 |
| 2026-11-17 | `3201d33` | 100.0% (14/14) | 0 | 0 | 41.83 | — | 0 | ↑ | docs(handoff): §11.144 —— 一次性收口三条 open 项（删死树 / CSS 颜色量具 / .kb 调色板）+ 再降一档 |
| 2026-11-17 | `9a72b05` | 100.0% (14/14) | 0 | 0 | 35.14 | — | 0 | ↑ | docs(handoff): §11.145 会话转移 —— 判断 · 成果 · 「为什么有封装还有差异」· 待办 · 独立提示词 |
| 2026-11-17 | `d6998a3` | 100.0% (14/14) | 0 | 0 | 52.36 | — | 0 | ↑ | refactor(ui): 原生 textarea 28 → 7（21 处换 ui/Textarea，其余 7 处有自造样式规则） |
| 2026-11-17 | `dff087e` | 100.0% (14/14) | 0 | 0 | 35.45 | — | 0 | ↑ | docs(handoff): §11.146 —— raw_textarea 28→7 · Electron 二进制落地 + ⋮ 三项/独立窗口真机验过 |
| 2026-11-17 | `3e91427` | 100.0% (14/14) | 0 | 0 | 43.92 | — | 0 | ↑ | docs(handoff): §11.148 —— 原语三处已决（Tabs / Button.surface=light / schema 字段）+ 封装补齐 |
| 2026-11-17 | `610f73e` | 100.0% (14/14) | 0 | 0 | 64.51 | — | 0 | ↑ | docs(handoff): §11.149 —— 徽章类 open 当场做完（ui/Badge 收口 35 处 + StatusBadge 委托），并记录「三 |
| 2026-11-18 | `42c0469` | 100.0% (14/14) | 0 | 0 | 64.20 | — | 0 | ↑ | docs(handoff): §11.155 —— 多选 pill 收口（TopicPicker / ChatPanel 显示开关）+ DevConsole 不 |
| 2026-11-18 | `c8f4e7c` | 100.0% (14/14) | 0 | 0 | 47.19 | — | 0 | ↑ | docs(handoff): §11.160 + 清单归零 —— batch2 完成（生产代码原生 button 604 → 0） |
| 2026-11-18 | `aaf180c` | 100.0% (14/14) | 0 | 0 | 48.38 | — | 0 | ↑ | docs(handoff): §11.161 封装缺口审计 —— 读数表 · ui/Notice · 剩余 5 类结构性缺口进 handoff_next |
| 2026-11-18 | `7954688` | 100.0% (14/14) | 0 | 0 | 64.77 | — | 0 | ↑ | docs(handoff): §11.164 —— ModalBase 再补三档形态 · 自造弹窗 7 → 1（剩 TransformQueueDialog） |
| 2026-11-18 | `3810000` | 100.0% (14/14) | 0 | 0 | 64.53 | — | 0 | ↑ | feat(ui): 带图标提示条 10 处收口 · Table 补 resizable/rowProps/columns.title（hub DataTable |
| 2026-11-18 | `a16ffad` | 100.0% (14/14) | 0 | 0 | 64.33 | — | 0 | ↑ | docs(handoff): §11.168 —— 带图标提示条 13 → 10（3 处误报）· Table 吸收 DataTable 能力 · 右坞真机读数 |
| 2026-11-19 | `ccea8f1` | 100.0% (14/14) | 0 | 0 | 55.91 | — | 0 | ↑ | feat(workbench): 导航 2/3 宽 · 分组单行 · 右坞默认打开（终端·实时活动）· 会话栏可拖 · 会话操作收成一个标志 · heading |
| 2026-11-19 | `9293d6d` | 100.0% (14/14) | 0 | 0 | 35.55 | — | 0 | ↑ | feat(workbench): 导航宽度改为刚好放得下所有叶子的像素宽（三档都是 120px） |
| 2026-11-19 | `1c3bfbe` | 100.0% (14/14) | 0 | 0 | 35.76 | — | 0 | ↑ | docs(handoff): §11.173 —— 满宽 Button 对齐根因 + 守卫；新增 windows-shard-env-flakes 待办 |
| 2026-11-19 | `06e9b5f` | 100.0% (14/14) | 0 | 0 | 66.60 | — | 0 | ↑ | fix(ui): 叶子页标题统一直到 Tab 上面；TAB 小标题字号收敛到阶梯 B |
| 2026-11-19 | `948eee4` | 100.0% (14/14) | 0 | 0 | 64.44 | — | 0 | ↑ | fix(ui): 最后 12 处 CSS 字号 heading 收口为显式 text-*（+ 守卫） |
| 2026-11-19 | `4a1cff3` | 100.0% (10/10) | 0 | 0 | 44.76 | — | 0 | ↑ | fix(ci): ui_guard_check 的派生连锁 5 处 + 守卫自身 3 处（2613fd951 的 10 分片红） |
| 2026-11-20 | `5f87f9f` | 100.0% (10/10) | 0 | 0 | 65.62 | — | 0 | ↑ | chore(derive): 重派生能力清单 + 盘点表（ui_guard_check 记录历史后调用点 12→55） |
| 2026-11-20 | `782ceef` | 100.0% (10/10) | 0 | 0 | 64.64 | — | 0 | ↑ | fix(test): tool pruning 受保护/误裁基线 8→9（ui_guard_fix risk=high） |
| 2026-11-20 | `7a88f90` | 100.0% (11/11) | 0 | 0 | 43.86 | — | 0 | ↑ | feat(llm-gate): 计费高峰拒绝 LLM 请求 + 顶栏开关/时段配置 |
| 2026-11-20 | `c07d389` | 100.0% (11/11) | 0 | 0 | 66.26 | — | 0 | ↑ | fix(ui): workbench 调色板改用 --wbp-* 前缀（避免覆盖 --wb-* 语义 token） |
| 2026-11-20 | `28438b1` | 100.0% (11/11) | 0 | 0 | 55.90 | — | 0 | ↑ | fix(ui): 恢复两处调色板漂移 —— ve-slate-950 真值 + dc 13.3333% 透明度 |
| 2026-11-20 | `c6cced8` | 100.0% (15/15) | 0 | 0 | 64.27 | — | 0 | ↑ | chore(manifest): 干净 worktree 重派生能力清单与盘点表（preview 免确认变更后） |
| 2026-11-21 | `3245f16` | 100.0% (15/15) | 0 | 0 | 45.33 | — | 0 | ↑ | fix(ui): 页头标题行固定 24px —— 消除动作控件高度带来的 1~4px 位移（真机 70/71/74） |
| 2026-11-21 | `f747ad8` | 100.0% (15/15) | 0 | 0 | 44.30 | — | 0 | ↑ | test(lines): 按 max_tools 27 更新三处生产口径断言（清单可见集 / 装配上限 / 不裁剪） |
| 2026-11-21 | `8d01681` | 100.0% (11/11) | 0 | 0 | 66.32 | — | 0 | ↑ | fix(memory): 合并摘要返回空时不得落盘（把上一份好摘要冲成 0 字节的真缺陷） |
| 2026-11-21 | `836707f` | 100.0% (11/11) | 0 | 0 | 64.69 | — | 0 | ↑ | fix(ui): 页内标题跟随新叶子名（系统总览 / 调度与开关） |
| 2026-11-21 | `229a73c` | 100.0% (11/11) | 0 | 0 | 36.28 | — | 0 | ↑ | docs(handoff): §11.193 真机复核收口（16 叶 + 17 旧深链）+ CI 偶发定因；条目按收尾部署 824cd7cd8 对齐 |
| 2026-11-21 | `d95a12a` | 100.0% (11/11) | 0 | 0 | 43.72 | — | 0 | ↑ | docs(handoff): ui-leaf-header-h1 / cognitive-reject-404 置 done（PageHeader 收口 + 补 |
| 2026-11-21 | `fc81493` | 100.0% (11/11) | 0 | 0 | 55.78 | — | 0 | ↑ | fix(settings): 合页子页不再自带 h1（PageContainer section 档）+ 账号表去掉重复「操作」列 |
| 2026-11-21 | `316ec20` | 100.0% (11/11) | 0 | 0 | 66.64 | — | 0 | ↑ | fix(nav): 首桶「会话与配置」恒高亮的真 bug + 桶名改「会话与能力」 |
| 2026-11-21 | `843dc11` | 100.0% (11/11) | 0 | 0 | 65.77 | — | 0 | ↑ | fix(prompt-lab): 分身角色方案取错载荷层级（信封 data.role）—— 整块静默不渲染 |
| 2026-11-21 | `f02b285` | 100.0% (11/11) | 0 | 0 | 64.82 | — | 0 | ↑ | docs(handoff): 登记 subagent-role-edit（已有分身的角色没有更新路由）+ §11.198 组装台方案模板入口 |
| 2026-11-21 | `2f73a8e` | 100.0% (11/11) | 0 | 0 | 31.91 | — | 0 | ↑ | docs(handoff): subagent-role-edit 的部署读数改成稳定口径（不写死 pid/bundle，补 1500 tests） |
| 2026-11-21 | `8477804` | 100.0% (11/11) | 0 | 0 | 48.26 | — | 0 | ↑ | docs(handoff): 登记 network-config-ui-dedupe（网络配置页去重 + 接回嵌套 schema，7501d500e） |
| 2026-11-21 | `3dbf9a9` | 100.0% (11/11) | 0 | 0 | 55.45 | — | 0 | ↑ | fix(instances): 新建 LLM 实例 400 —— 发 api_endpoint（旧发 base_url）+ 400 校验原因不再被吞 |
| 2026-11-22 | `abd47a3` | 100.0% (11/11) | 0 | 0 | 64.34 | — | 0 | ↑ | feat(instances): 点已有实例的「编辑」预填表单并原 id 保存（PUT） |
| 2026-11-22 | `4e22004` | 100.0% (10/10) | 0 | 0 | 50.15 | — | 0 | ↑ | fix(security): 清掉 code scanning 剩余 4 条告警（异常原文 / 路径注入 / TLS 版本） |
| 2026-11-22 | `de26acd` | 100.0% (10/10) | 0 | 0 | 97.78 | — | 0 | ↑ | chore(deps): opentelemetry 整组 1.45.1 -> 1.45.2（api/sdk/proto/exporters + semconv |
| 2026-11-22 | `17573c5` | 100.0% (10/10) | 0 | 0 | 98.10 | — | 0 | ↑ | fix(memory): _flush 读不到的键不再静默丢弃（CI shard6 偶发 10 个 .md 只有 2 个的根因） |
| 2026-11-22 | `acd66dd` | 100.0% (10/10) | 0 | 0 | 65.91 | — | 0 | ↑ | docs(handoff): 登记 instance-payload-contract 与 tlm-flush-requeue-unread（两个 done） |
| 2026-11-22 | `f6131d7` | 100.0% (10/10) | 0 | 0 | 78.26 | — | 0 | ↑ | docs(handoff): nav-panorama-flat 置 done（观测桶两个小节取消，叶子直接挂桶下） |
| 2026-11-22 | `8de3a3d` | 100.0% (10/10) | 0 | 0 | 76.41 | — | 0 | ↑ | docs(handoff): feishu-webhook-payload-dialect 置 done（飞书/钉钉 webhook 载荷方言 + 业务码） |
| 2026-11-22 | `f302876` | 100.0% (10/10) | 0 | 0 | 72.13 | — | 0 | ↑ | docs(handoff): settings-collapse-under-filter 置 done（开关中心筛选态折叠权还给用户） |
| 2026-11-22 | `111cfaa` | 100.0% (10/10) | 0 | 0 | 99.23 | — | 0 | ↑ | docs(handoff): feishu-webhook-sign-secret 置 done（加签字段 + env 键 + 登记 + 两家签名） |
| 2026-11-22 | `a118efe` | 100.0% (10/10) | 0 | 0 | 100.18 | — | 0 | ↑ | docs(handoff): §11.215 会话转移 + 两条 frozen（_sanitize 吞文案 / 钉钉加签无真机） |
| 2026-11-22 | `bd1bc2d` | 100.0% (10/10) | 0 | 0 | 101.44 | — | 0 | ↑ | chore(plugin-bulkhead): 去掉未使用的 snapshot 与 typing.Dict |
| 2026-11-22 | `549836a` | 100.0% (14/14) | 0 | 0 | 73.53 | — | 0 | ↑ | feat(plugin-isolation): 插件包改独立子进程 + 超时硬杀 + 最小 env（P2 第一批） |
| 2026-11-22 | `d1a3bd9` | 100.0% (10/10) | 0 | 0 | 70.32 | — | 0 | ↑ | test(contract): result_schema 扩面后改用 web_search 守 undeclared 语义 |
| 2026-11-23 | `9e609ba` | 100.0% (10/10) | 0 | 0 | 50.05 | — | 0 | ↑ | docs(plugin-security): P2 剩余五项优先级与签名/沙箱方案（待 Owner 拍板） |
| 2026-11-23 | `44cb1a9` | 100.0% (11/11) | 0 | 0 | 57.49 | — | 0 | ↑ | docs(handoff): 记录签名校验器落地与下一步（安装器未接线） |
| 2026-11-23 | `f981f65` | 100.0% (10/10) | 0 | 0 | 50.63 | — | 0 | ↑ | fix(plugin-installer): 发现列表跳过 .staging 内部目录 |
| 2026-11-23 | `c78fe2d` | 100.0% (10/10) | 0 | 0 | 38.79 | — | 0 | ↑ | docs(plugin-security): 修正插件测试计数并记录 CI 全绿 |
| 2026-11-23 | `be04ef9` | 100.0% (10/10) | 0 | 0 | 33.41 | — | 0 | ↑ | chore(capability): 重新派生能力清单（appcontainer 新增静态符号） |
| 2026-11-23 | `39d9ab0` | 100.0% (10/10) | 0 | 0 | 58.85 | — | 0 | ↑ | fix(audit): DB 兜底重试后 fail-closed，禁止内存链头分叉 |
| 2026-11-23 | `a732314` | 100.0% (14/14) | 0 | 0 | 58.31 | — | 0 | ↑ | chore(capability): 重派生能力清单（Job Object 静态符号） |
| 2026-11-23 | `51f437f` | 100.0% (14/14) | 0 | 0 | 58.58 | — | 0 | ↑ | feat(plugin-sandbox): Job Object 累计 CPU 时间上限 |
| 2026-11-23 | `c00e1e2` | 100.0% (14/14) | 0 | 0 | 55.17 | — | 0 | ↑ | feat(contract): 6 个只读工具补 result_schema（L2 扩面第一批，逐工具真调） |
| 2026-11-23 | `e1cc173` | 100.0% (10/10) | 0 | 0 | 52.06 | — | 0 | ↑ | fix(arch): 角色词表与身份方案库解耦，架构规则校验归零 |
| 2026-11-23 | `7795447` | 100.0% (10/10) | 0 | 0 | 50.02 | — | 0 | ↑ | feat(plugin): AppContainer 联网按插件 opt-in + L2 契约补齐 16 个 medium + look_at_screen 修 |
| 2026-11-24 | `1a2ded9` | 100.0% (15/15) | 0 | 0 | 69.20 | — | 0 | ↑ | test(fan_out): YAML 字段契约补 result_schema（L2 high 批次联动） |
| 2026-11-24 | `8be4ea4` | 100.0% (10/10) | 0 | 0 | 49.63 | — | 0 | ↑ | feat(contract): low 39 个工具补 result_schema（tracked 全量收口 96/101） |
| 2026-11-24 | `877efff` | 100.0% (10/10) | 0 | 0 | 102.76 | — | 0 | ↑ | test(sandbox): 用例跨平台修正（大小写仅 Windows / 不把 NUL 塞进环境变量） |
| 2026-11-24 | `24ff636` | 100.0% (12/12) | 0 | 0 | 70.92 | — | 0 | ↑ | test(capability): 追平 read_spill 带来的计数/基线哨兵（CI 12 处失败） |
| 2026-11-24 | `9881645` | 100.0% (11/11) | 0 | 0 | 53.03 | — | 0 | ↑ | fix(checkpoint): git 调用补 _proc.hidden_kwargs + 单测关闭快照 |
| 2026-11-24 | `687eecd` | 100.0% (10/10) | 0 | 0 | 51.17 | — | 0 | ↑ | fix(plan-mode): /plan rollback N + 能力清单重派生（修 CI 盘点表漂移） |
| 2026-11-24 | `aedf19a` | 100.0% (10/10) | 0 | 0 | 70.31 | — | 0 | ↑ | fix(plan-mode): 修 CodeQL 11 条（path-injection ×10 + weak hash ×1） |
| 2026-11-25 | `b0aead9` | 94.4% (17/18) | 0 | 1 | 103.43 | — | 0 | ↑ | feat(goal): E1-2a 目标事件溯源服务 + /goal 命令族（不含工具/路由） |
| 2026-11-25 | `d2719ff` | 92.9% (13/14) | 0 | 1 | 105.11 | — | 0 | ↑ | fix(goal): E1-2b 派生基线修正（S0 注释/τ/v2 下发层）+ goal.yaml 描述对齐 |
| 2026-11-25 | `b820a13` | 100.0% (11/11) | 0 | 0 | 37.18 | — | 0 | ↑ | fix(ask): ask 提出后本轮必须结束 + 流中提交不静默丢弃 |
| 2026-11-25 | `00a377f` | 100.0% (10/10) | 0 | 0 | 60.17 | — | 0 | ↑ | chore(derive): 主线 boost 变化后的能力清单重派生 + 可见集断言 28→30 |
| 2026-11-26 | `f395ec1` | 100.0% (17/17) | 0 | 0 | 71.02 | — | 0 | ↑ | fix(session-index): 同分命中让"新的在前"（回忆场景的新近优先） |
| 2026-11-26 | `5a90759` | 100.0% (17/17) | 0 | 0 | 62.96 | — | 0 | ↑ | fix(derive): 能力清单 location 分布 local 129→130（E2-1 subagent_control） |
| 2026-11-26 | `b6a7152` | 100.0% (13/13) | 0 | 0 | 49.02 | — | 0 | ↑ | feat(changes): E1-3 余项——变更卡按会话过滤 + 时序补偿 + 检查点保留策略 |
| 2026-11-26 | `18fee15` | 100.0% (13/13) | 0 | 0 | 36.55 | — | 0 | ↑ | fix(pty,proc): 僵尸态判定 + PTY spawn 走 hidden_kwargs（CI shard4/shard6 实测红） |
| 2026-11-26 | `95f9f55` | 100.0% (17/17) | 0 | 0 | 63.81 | — | 0 | ↑ | test(pruning): 装配层工具数 30 → 32（docx_read 进 boost 后 max_tools 提高，CI shard5 实测红） |
| 2026-11-26 | `6f1c739` | 100.0% (17/17) | 0 | 0 | 52.41 | — | 0 | ↑ | fix(tests): 归档时区改动带出的两处测试适配（CI shard3 实测红） |
| 2026-11-26 | `2a87aeb` | 100.0% (17/17) | 0 | 0 | 51.12 | — | 0 | ↑ | Merge origin/master |
| 2026-11-26 | `d714b6e` | 100.0% (14/14) | 0 | 0 | 73.56 | — | 0 | ↑ | Merge origin/master |
| 2026-11-26 | `ed18ca0` | 100.0% (13/13) | 0 | 0 | 74.05 | — | 0 | ↑ | fix(e3-2): 同步 S0 分位数契约（test_tool_router_hybrid_fusion_calibration） |
| 2026-11-27 | `b6e5a6e` | 100.0% (11/11) | 0 | 0 | 37.43 | — | 0 | ↑ | docs(handoff): 本轮推进 —— E4-1/E4-2 第一批/E4-1b/P5 B+C-shadow 与派生陷阱 |
| 2026-11-27 | `c5476de` | 100.0% (11/11) | 0 | 0 | 73.03 | — | 0 | ↑ | Merge origin/master |
| 2026-11-27 | `82473f0` | 100.0% (11/11) | 0 | 0 | 50.79 | — | 0 | ↑ | Merge origin/master |
| 2026-11-27 | `d789a50` | 100.0% (16/16) | 0 | 0 | 35.04 | — | 0 | ↑ | Merge origin/master |
| 2026-11-27 | `ad7f879` | 100.0% (17/17) | 0 | 0 | 38.23 | — | 0 | ↑ | docs(e4-4): 设计稿 + CHANGELOG + 交接 §10（索引 v1 / 抓取硬化 / 推荐位默认关） |
| 2026-11-27 | `5edc52e` | 100.0% (12/12) | 0 | 0 | 50.89 | — | 0 | ↑ | feat(e4-2): 跨轮次显式 state.json + 会话筛选 + run 详情（不新增路由/工具） |
| 2026-11-27 | `fc5d890` | 100.0% (11/11) | 0 | 0 | 51.18 | — | 0 | ↑ | Merge origin/master |
| 2026-11-27 | `4b1f412` | 100.0% (11/11) | 0 | 0 | 57.12 | — | 0 | ↑ | Merge origin/master |
| 2026-11-27 | `2171c1e` | 100.0% (11/11) | 0 | 0 | 54.92 | — | 0 | ↑ | Merge origin/master |
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
| `network_config.py` mypy 严格检查 | ✅ 阻塞 | 阻塞 | — | — | 已达成 (2026-08-28)：29 个历史类型错误清零，ci.yml 由 `\|\| true` 转阻塞 |
| `_mock_env_config_in_ci` fixture 复用 | ✅ 已提取至 conftest.py | — | — | — | 已完成 (2026-07-29) |

### network_config.py 类型错误清理记录（2026-08-28 已清零）

> 原 29 个历史类型错误（下表）已全部修复，`mypy agent/network_config.py --warn-no-return --warn-return-any --ignore-missing-imports --follow-imports=silent` 返回 Success，
> ci.yml 该模块检查已由非阻塞 `|| true` 转为阻塞。下表留档供回归参考。

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
