# Jira Issue 草稿 — 架构违规修复 PR #5 遗留问题

**生成时间**：2026-07-02
**来源**：[架构违规指标修复完整复盘报告](file:///c:/Users/Administrator/agent/docs/observability/arch_metric_fix_retrospective_report.md) 第五章
**PR**：[#5 — feat(observability): 阶段2可见性收敛](https://github.com/nzt47/security-tools/pull/5)

> **使用说明**：以下为 4 个遗留问题的 Jira Issue 草稿，可直接复制到 Jira 创建。负责人字段（`@负责人`）需根据团队实际情况填写。

---

## Issue 1：升级废弃的 actions/upload-artifact@v3 到 v4

### 基本信息
- **Issue 类型**：Bug / 技术债务
- **优先级**：P1（阻塞 P0 安全回归测试 CI）
- **负责人**：@负责人（建议：DevOps / CI 维护人员）
- **模块**：`.github/workflows/p0-security-verify.yml` 及相关 workflow
- **关联**：复盘报告 5.2 节

### 标题
`[CI] 升级 actions/upload-artifact@v3 到 v4 — 修复 P0 安全回归测试硬性失败`

### 描述

#### 问题现象
P0 安全回归测试 job 在 CI 中硬性失败，错误信息：
```
This request has been automatically failed because it uses a deprecated version
of `actions/upload-artifact: v3`. Learn more: https://github.blog/changelog/2024-04-16-deprecation-notice-v3-of-the-artifact-actions/
```

#### 影响范围
- **受影响 job**：P0 安全回归测试、P0 安全验证总结
- **PR 影响**：PR #5 的 `mergeStateStatus` 为 UNSTABLE（因本 job 失败）
- **业务影响**：P0 安全回归测试无法在 CI 中运行，安全漏洞可能逃逸

#### 预先存在验证
已在 commit `ef3d5bcf` 上验证同样失败，确认非 PR #5 引入。

#### 修复方案
1. 扫描所有 `.github/workflows/*.yml` 文件，查找 `actions/upload-artifact@v3`
2. 替换为 `actions/upload-artifact@v4`
3. 验证 v4 的 artifact 命名和行为兼容性（v4 的 artifact 不可跨 run 下载，需检查是否有此依赖）
4. 本地验证 workflow 语法：`actionlint .github/workflows/*.yml`
5. 触发 CI 验证修复

#### 验收标准
- [ ] 所有 workflow 中无 `actions/upload-artifact@v3` 引用
- [ ] P0 安全回归测试 job 不再因 artifact 版本失败
- [ ] artifact 上传功能正常（可下载）

---

## Issue 2：修复 test_p0_security_fix.py CI collection error

### 基本信息
- **Issue 类型**：Bug
- **优先级**：P2（非阻塞，但影响补丁完整性验证）
- **负责人**：@负责人（建议：测试维护人员 / QA）
- **模块**：`tests/regression/test_p0_security_fix.py`
- **关联**：复盘报告 5.3 节

### 标题
`[Test] 修复 test_p0_security_fix.py 在 CI 环境的 collection error`

### 描述

#### 问题现象
补丁完整性验证 job 在 CI 中失败，pytest 收集阶段出错：
```
ERROR tests/regression/test_p0_security_fix.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
===================== no tests collected, 1 error in 0.35s =====================
```

#### 本地 vs CI 差异
- **本地**：68 tests passing（含 27 新增用例），0 失败
- **CI**：collection error，0 tests collected

#### 可能原因（需人工核实）
1. CI 环境依赖差异（缺少某个 import 的模块）
2. 路径问题（相对路径在 CI 中解析不同）
3. import 错误（具体错误信息未在日志中显示，需下载 artifact 查看完整堆栈）

#### 预先存在验证
已在 commit `ef3d5bcf` 上验证同样失败，确认非 PR #5 引入。

#### 修复方案
1. 在 CI workflow 中添加 `--tb=long` 参数获取完整 collection error 堆栈
2. 或在 collection 步骤后上传 `mock_server.log` 和完整日志 artifact
3. 对比本地与 CI 环境的依赖列表（`pip freeze`）
4. 如是 import 错误，修复 import 语句或添加 CI 专用 fixture
5. 如是路径问题，使用 `pathlib.Path` 或 `__file__` 相对路径

#### 验收标准
- [ ] CI 环境中 `test_p0_security_fix.py` 可正常收集
- [ ] 68 个测试在 CI 中全部通过
- [ ] 本地测试仍保持通过（无回归）

---

## Issue 3：修复可见性趋势报告 Mock 测试 query_range 失败

### 基本信息
- **Issue 类型**：Bug
- **优先级**：P2（不阻塞合并，job 非必需 check）
- **负责人**：@负责人（建议：可观测性模块维护人员）
- **模块**：`.github/workflows/observability-ci.yml` → `visibility-trend-mock-test` job
- **关联**：[GitHub Issue #6](https://github.com/nzt47/security-tools/issues/6)、复盘报告 5.1 节

### 标题
`[Observability] 修复可见性趋势报告 Mock 测试 query_range 返回非 matrix 数据`

### 描述

#### 问题现象
`visibility-trend-mock-test` job 退出码 3，Mock 服务 `query_range` API 验证失败：
```
##[error]Process completed with exit code 3.
```

#### 失败链路
1. Mock 服务启动成功（PID 已分配）
2. 测试通过 curl/脚本查询 `query_range` 端点
3. 期望返回 Prometheus matrix 数据格式
4. 实际返回非预期（非 matrix 格式或空），验证脚本退出码 3

#### 可能原因（需人工核实）
- Mock 服务的 `query_range` 响应数据格式与预期不符（如返回 vector 而非 matrix）
- Mock 服务的 fixture 数据缺失或时间范围配置错误
- 验证脚本的断言逻辑过严

#### 触发条件
- `workflow_dispatch` 手动触发 + `mock_test_enable == true`
- 默认不触发（`mock_test_enable` 默认 false），仅手动触发时失败

#### 修复方案
1. 下载 `visibility-trend-mock-test-*` artifact，检查 `mock_server.log`
2. 对比预期 matrix 格式与实际响应
3. 修复 Mock 服务 fixture 或验证脚本的断言逻辑
4. 本地复现：启动 Mock 服务 + 运行验证查询

#### 验收标准
- [ ] `visibility-trend-mock-test` job 退出码 0
- [ ] Mock 服务 `query_range` 返回正确 matrix 格式
- [ ] 验证脚本断言通过

---

## Issue 4：升级 Node 20 废弃的 actions 版本

### 基本信息
- **Issue 类型**：技术债务
- **优先级**：P3（当前不阻塞，但 GitHub 后续可能硬性失败）
- **负责人**：@负责人（建议：DevOps / CI 维护人员）
- **模块**：所有 `.github/workflows/*.yml`
- **关联**：复盘报告 5.4 节

### 标题
`[CI] 升级 Node 20 废弃的 actions 版本（checkout@v3, setup-python@v4）`

### 描述

#### 问题现象
部分 workflow 仍使用基于 Node 20 的 action 版本，CI 日志出现废弃警告：
```
Node 20 is being deprecated. This workflow is running with Node 24 by default.
The following actions target Node.js 20 but are being forced to run on Node.js 24:
actions/checkout@v3, actions/setup-python@v4
```

#### 影响范围
- **当前影响**：仅 warning，不阻塞 CI
- **未来风险**：GitHub 后续可能硬性失败（类似 `upload-artifact@v3` 的废弃路径）
- **受影响 actions**：
  - `actions/checkout@v3` → 应升级到 `@v4`
  - `actions/setup-python@v4` → 应升级到 `@v5`

#### 修复方案
1. 扫描所有 `.github/workflows/*.yml` 文件
2. 替换 `actions/checkout@v3` → `actions/checkout@v4`
3. 替换 `actions/setup-python@v4` → `actions/setup-python@v5`
4. 验证 v4/v5 的行为兼容性（checkout v4 默认不持久化凭证，需检查是否有此依赖）
5. 触发 CI 验证修复

#### 验收标准
- [ ] 所有 workflow 中无 `actions/checkout@v3` 和 `actions/setup-python@v4` 引用
- [ ] CI 日志中不再出现 `Node 20 is being deprecated` 警告
- [ ] CI 功能正常（checkout 和 Python 环境设置正常工作）

---

## 附加：3 个监控测试 6 小时超时取消问题（新发现）

### 基本信息
- **Issue 类型**：Bug / 性能问题
- **优先级**：P2（影响 CI 反馈时效）
- **负责人**：@负责人（建议：测试维护人员）
- **模块**：`.github/workflows/observability-ci.yml` → `observability-unit-tests` 和 `full-project-tests` job
- **关联**：复盘报告 4.3 节

### 标题
`[CI] 3 个可观测性测试 job 6 小时超时被取消 — 需优化测试性能或添加超时控制`

### 描述

#### 问题现象
PR #5 的 3 个监控测试 job 在 GitHub Actions 上运行 6 小时后被自动取消：
- 可观测性单元测试 (3.10)：18:13:38 开始 → 00:14:05 取消
- 可观测性单元测试 (3.11)：18:13:12 开始 → 00:14:05 取消
- 全项目测试覆盖率：18:12:51 开始 → 00:14:05 取消

#### 根因
GitHub Actions 最长运行 6 小时限制，3 个 job 均触发超时。

#### 影响范围
- PR 的 3 个 check 无法给出 pass/fail 结论（显示 cancelled）
- 无法验证 CI 环境下的测试通过情况
- 延迟 PR 合并决策

#### 修复方案
1. **分析耗时**：在 job 中添加 `--durations=10` 参数，输出最慢的 10 个测试
2. **优化测试**：
   - 检查是否有测试存在网络等待、sleep、或超时设置过长
   - 检查 `pip install -e .` 是否安装了不必要的重依赖（如 torch）
   - 考虑将覆盖率 html 报告生成改为仅在本地运行
3. **添加超时限制**：在 job 级别添加 `timeout-minutes: 30` 强制 30 分钟超时
4. **拆分测试**：如全项目测试确实需要长时间，考虑拆分为多个并行 job

#### 验收标准
- [ ] 3 个 job 在 30 分钟内完成（或添加合理的 `timeout-minutes`）
- [ ] 添加 `--durations=10` 输出最慢测试
- [ ] CI 环境下测试全部通过

---

## 创建建议

| Issue | 建议 Sprint | 建议标签 |
|---|---|---|
| Issue 1 (upload-artifact v3) | 当前 Sprint | `ci`, `tech-debt`, `p1` |
| Issue 2 (test collection error) | 当前 Sprint | `test`, `bug`, `p2` |
| Issue 3 (Mock 测试) | 下一 Sprint | `observability`, `test`, `p2` |
| Issue 4 (Node 20 废弃) | 下一 Sprint | `ci`, `tech-debt`, `p3` |
| 附加 (6 小时超时) | 当前 Sprint | `ci`, `performance`, `p2` |
