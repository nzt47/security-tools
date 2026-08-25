# CI pre-existing 失败项修复计划（第二轮：25 项遗留失败）

> **文档版本**：v2.0（已完成） | **更新日期**：2026-08-25
> **背景**：PR #795（fix(ci): 修复 docs 失效链接误判第一批，含 Fix 1+2）CI 共 99 checks：
> 61 通过 / 13 跳过 / **25 失败**。25 项全部为 pre-existing（与上一 run 失败集一致），非 PR #795 diff 引入。
> 本计划针对这 25 项按优先级排序给出修复方案。
>
> **✅ 修复完成（2026-08-25）**：25 项失败全部转绿，最终验证 run `32868259253`
> （develop @ a861f8ac）29 个 job 全部 success（含覆盖率检查、全部 18 个单测 shard、
> 代码质量、文档链接、安全扫描、集成/E2E/性能）。修复提交：
> - `b2abd209`：docs 绝对路径链接批量转相对 + bandit 配置 + safety `--save-json` + 变更清单权限
> - `a2d70333`：`TenantManager.list_tenants` / `AuditLogger.log(tenant_id)` / gateway 租户上下文注入 / handoff 测试导入修复
> - `c1decc5e`：health_probes JSON 容忍 + llm_error 隔离 config 第一读取点
> - `a861f8ac`：shard3 collection error（semantic-config 测试改用 register_routes）+ shard6 config 第二读取点 + docs 链接大小写（Yunshu-ui→yunshu-ui）

---

## 一、失败清单总览（25 项）

| 类别 | 数量 | 优先级 | 根因状态 |
|------|------|--------|----------|
| 单元测试（多 shard / 多版本） | 13 | P0 | ✅ 已确认 |
| 全项目测试覆盖率（Shard 1/2/4/5/6） | 5 | P0 | ✅ 与单测同源 |
| 文档链接预检与锚点回归测试 | 1 | P1 | ✅ 已确认 |
| 安全扫描 | 1 | P2 | ✅ 已确认 |
| 变更清单报告 | 1 | P2 | ✅ 已确认 |
| 架构影响可见性检查 | 1 | P2 | ⚠️ 部分确认 |
| 代码质量检查 | 0 | - | 本 run 已通过，不计入 |

---

## 二、P0：单元测试 / 覆盖率失败（18 项）

### 根因（已确认）

覆盖率 Shard 4 日志显示核心失败模式：

```
E   TypeError: RateLimiter.check() got an unexpected keyword argument 'tenant_id'
    tests/unit/test_api_gateway_tenant_quota.py:68:
        assert limiter.check(endpoint="/test/quota", user_id="u1", tenant_id="A") is True
```

**根因**：`tests/unit/test_api_gateway_tenant_quota.py` 调用 `RateLimiter.check(...)` 传入了
`tenant_id` 参数，但实现（`agent/.../rate_limiter.py` 的 `check()` 方法）不接受该参数。
属**测试与实现签名不同步**（疑似限流器重构时未同步更新测试），同源失败扩散到
单测 13 shard + 覆盖率 5 shard。

### 修复方案

1. 拉取全部失败 shard 的 job 日志，确认各 shard 失败是否仅此一个根因，还是存在其他独立失败项（防漏）。
2. 对照实现确认 `RateLimiter.check()` 当前签名；决定修哪一侧：
   - 若 `tenant_id` 是**已删除/未实现**的隔离能力 → 更新测试用例（删掉 tenant_id 断言或改为当前签名等价断言）。
   - 若 `tenant_id` 隔离是**应有能力但实现缺失** → 在 `check()` 签名补 `tenant_id` 参数并实现租户级令牌回滚逻辑（改动较大，需评估，不得仅改签名糊弄）。
3. 本地先跑该测试文件全量通过，再跑关联限流器测试族。

### 风险边界（不易）

- 仅允许修测试-实现不一致；**禁止**为通过 CI 而删除/放宽真实断言逻辑。
- 涉及限流器核心逻辑改动时，必须先输出三义分析并征求确认。

### 验收标准

- `test_api_gateway_tenant_quota.py` 本地 pytest 全绿；
- 对应单测 shard 与覆盖率 shard 在 CI 转绿（预计 18 项 → 0）。

---

## 三、P1：文档链接预检与锚点回归（1 项）

### 根因（已确认）

job 日志显示 `[BROKEN]` 链接指向**本地绝对路径**：

```
[BROKEN] SSE流式断流乱序排查指南.md: c:\Users\Administrator\agent\app_server.py
[BROKEN] v2瘦身可行性分析.md: c:\Users\Administrator\agent\yunshu-ui\scripts\afterPack.mjs
[BROKEN] 云枢Electron多窗口架构设计.md: c:\Users\Administrator\agent\yunshu-ui\src\WorkbenchApp.tsx
```

**根因**：`docs/zh/` 下多篇文档（SSE流式断流乱序排查指南.md、v2瘦身可行性分析.md、
云枢Electron多窗口架构设计.md 等）存在指向开发者本机路径的
`c:\Users\Administrator\agent\...` 链接，CI 环境（D:\a\...）下必然失效。
本 PR 已修复的 `incident_report_template.md` 不再报错，剩余为本批文档问题（pre-existing）。

### 修复方案

1. 定位全部含 `c:\Users\Administrator\agent`（或 `C:\Users\`）绝对路径链接的文档：
   `Grep "c:\\Users\\Administrator\\agent" docs/zh/`
2. 逐个判定链接意图，改写为：
   - 仓库内文件 → 相对链接；
   - 仓库外/开发机专属文件 → 反引号代码引用（与 incident_report_template.md 同款修法）或删除。
3. 本地运行 `check_docs_broken_links.ps1` 校验 0 失效，再跑锚点回归。

### 验收标准

- 预检脚本本地 0 失效；CI「文档链接预检与锚点回归测试」转绿。

---

## 四、P2：安全扫描（1 项）

### 根因（已确认）

bandit 检出并输出大量 `[B101:assert_used]`（测试代码中的 assert），
`Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.`
导致 job 以非 0 退出。

### 修复方案

- 方案 A（推荐）：bandit 配置对 `tests/` 目录跳过 B101 或在配置文件中加入
  `skips: ['B101']` 作用域限定测试路径；
- 方案 B：测试代码中 assert 改为显式 `if not ...: raise`（改动面大，不推荐）；
- 先确认 bandit 是否还有其他高严重性 issue 一并暴露。

### 验收标准

- 安全扫描 job 转绿（bandit 0 high-severity 阻断项）。

---

## 五、P2：变更清单报告（1 项）

### 根因（已确认）

```
##[error]Unhandled error: HttpError: Resource not accessible by integration
```

**根因**：该 job 调用 GitHub API 生成变更清单，但 Actions `GITHUB_TOKEN` 默认权限
不含所需 scope（如 `pull-requests: read` / `contents: read`），或 workflow 顶层未声明
`permissions`。

### 修复方案

- 在对应 workflow 声明 job/step 级 `permissions: { pull-requests: read, contents: read }`（按实际调用收窄）；
- 或改用带 `repo` scope 的自定义 PAT secret（安全风险高，不推荐，优先收窄 GITHUB_TOKEN 权限）。

### 验收标准

- 变更清单报告 job 转绿且生成清单内容正确。

---

## 六、P2：架构影响可见性检查（1 项）

### 根因（⚠️ 部分确认）

job 退出码 1，日志未抓到明确 error 行（疑似分析工具输出/超时/依赖缺失）。

### 修复方案（待办）

1. 拉取该 job 完整日志定位首个失败 step；
2. 按定位结果补修复方案（工具版本、依赖、或分析断言）。

### 验收标准

- 架构影响可见性检查 job 转绿。

---

## 七、实施顺序建议

| 批次 | 内容 | 前置条件 |
|------|------|----------|
| 批次 A | P0 单元测试/覆盖率（拉全日志 → 修签名 → 本地验证） | 需确认修测试还是修实现 |
| 批次 B | P1 文档链接（批量改写本地绝对路径链接） | 需确认各链接意图 |
| 批次 C | P2 安全扫描 + 变更清单（CI 配置） | 需确认 workflow 文件 |
| 批次 D | P2 架构影响可见性（待归因后补方案） | 需完整日志 |

## 八、总体验收标准

- 25 项失败全部转绿（或逐项显式标注"暂不修 + 理由"并经确认）；
- 不引入新失败（对照上一 run 失败集）。

---

## 附：相关文件

- 第一批计划（Fix 1-4 框架）：`docs/planning/ci_pre_existing_fix_pr_plan_20260824.md`
- PR #795（第一批实施）：https://github.com/nzt47/security-tools/pull/795
