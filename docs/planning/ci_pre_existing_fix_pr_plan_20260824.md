# CI pre-existing 失败项修复 PR 计划（2026-08-24）

> 分支: `fix/ci-pre-existing-20260824`
> 基线: develop @ `cb1ff3b8`
> 目标: 修复 run 32651566274 暴露的 4 类 pre-existing CI 失败（非 34ea94de 引入，
>       已对比上一 run 同样失败），使 develop 主链 CI 全绿。

---

## 失败项总览（run 32651566274）

| # | Job | 失败步骤 | 根因判定 |
|---|---|---|---|
| 1 | 文档链接预检与锚点回归测试 | 运行文档链接预检 + 锚点回归测试 | 仓库既有失效链接/锚点 |
| 2 | 代码质量检查 | docs 链接预检诊断（失效链接阻断） | 同上（check_docs_broken_links.ps1 检出 0 阈值阻塞） |
| 3 | 安全扫描 | 敏感数据正则静态扫描 | `agent/skills_mgmt/eval_sample_ingest.py#304` GREEDY_REGEX 误报（训练样本含 api_key/secret_key/password 示例） |
| 4 | 单元测试（6 shard 失败） | 运行单元测试 | 系统性问题（3.10/3.11/3.12 多个 shard），疑为 flaky 或环境/基线漂移 |

---

## 修复方案

### Fix 1 + Fix 2：失效链接/锚点（合并处理，共用脚本）

- **入口脚本**（单一事实源）：
  - `scripts/dev/check_docs_broken_links.ps1`（代码质量 job，0 失效才通过）
  - `scripts/dev/git_precommit_check.ps1`（docs-precheck job，含锚点回归 pytest）
- **步骤**：
  1. 本地跑 `pwsh -NoProfile -File scripts/dev/check_docs_broken_links.ps1` 收集失效链接清单
  2. 逐一修复（改路径 / 补文件 / 删孤儿引用），**不得加白名单绕过**
  3. 跑锚点回归：`powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dev/git_precommit_check.ps1 -TargetRepo . -JsonOutput`
  4. 验证：两脚本本地 exit 0

### Fix 3：敏感数据 GREEDY_REGEX 误报

- **根因**：`eval_sample_ingest.py` 训练样本为教学示例（api_key/secret_key/password 字段名），被通用正则误判为真实凭据
- **方案**（择一，倾向 A）：
  - A. 在 `scan_sensitive_regex.py` 对该文件路径加入精确保白（白名单必须带文件行号锚定，禁全局通配）
  - B. 改写样本中字段名（如 `api_key` → `api_key_placeholder`）——改动面大，不推荐
- **验证**：`python scripts/scan_sensitive_regex.py` exit 0

### Fix 4：单测多 shard 失败（系统性问题）

- **步骤**：
  1. 从 run 32651566274 下载 `junit-shard*.xml` artifact，按失败测试聚合归因
  2. 判定类型：
     - 环境类（runner 回收/超时）→ 重跑验证 + 调 timeout-minutes
     - 代码类（断言失败）→ 修复测试或实现
     - 基线漂移（边界值/性能阈值）→ 更新基线（走 PR，记录 Why）
  3. 本地复现：`pytest <失败文件> -n 2 --timeout=60 -p no:randomly`
- **验证**：`scripts/split_unit_tests.py --shard N --shards 6` 各 shard 本地通过

---

## 实施顺序（小步可回滚，变易）

1. Fix 1+2（链接/锚点，纯 docs，低风险）→ 单独 commit
2. Fix 3（安全扫描白名单）→ 单独 commit
3. Fix 4（单测归因）→ 视根因单独 commit
4. push 分支 → 创建 PR（base=develop）→ 观察 CI
5. CI 全绿后 squash 合入 develop → 同步 gitee

## 风险与边界（不易）

- 禁改 `guard_bom_pollution.py` / BOM 契约（受保护清单）
- 禁全局放宽扫描阈值掩盖真实问题（白名单必须最小精确）
- 合入必须走 PR（master/develop 纪律，R1/R2）
- 失败 run 32650665214（fix/ci-skills-check-403）为独立工作线，不并入本 PR

## 验收标准

- [ ] 本地：4 类检查脚本全部 exit 0
- [ ] CI：develop 下一次 push 全绿（含单测全 shard）
- [ ] 无新增白名单绕过，无受保护文件变更
