# BM25 检索质量失败分析报告（PR #499）

> **分析时间**:2026-08-09
> **PR**:#499 release/v1.2.0 → master
> **Head**:`2ec54b01` feat(scripts): Jira 脚本支持 .env 读取（项目约定）
> **数据来源**:CI run 31264078228（Shard 2, Python 3.10）日志 + git 分支对比

---

## 1. 结论（TL;DR）

【不易】PR #499 的 5 个单元测试失败**均为既有 BM25 检索缺陷，与 release 内容无关**，且 **root cause 是分支间修复未同步**：master/develop 已含全部 3 个检索质量修复，release/v1.2.0 分支缺失，导致本应在 master 上 xfail/通过 的用例在 PR head 上裸跑失败。

修复方式：将 3 个 master 修复同步进 release/v1.2.0（cherry-pick 或 merge）。

---

## 2. 失败用例清单（5 个）

| # | 用例 | 失败类型 | 失败详情 |
|---|------|---------|---------|
| 1 | `test_tool_negative_samples.py::test_query_distinction[G4_list_family_q08]` | 负样本泄漏 | query='查看系统中运行的进程'，负样本 `list_async_tasks` 进入 top-5 |
| 2 | `test_tool_retrieval_quality.py::test_single_query_recall[0]` | 召回缺失 | q01 '帮我搜索今天的天气'，`web_search` 未命中（recall@5=0.50） |
| 3 | `test_tool_retrieval_quality.py::test_single_query_recall[18]` | 召回缺失 | q19 '搜索网页内容并读取本地文件'，`web_search` 未命中（recall@5=0.50） |
| 4 | `test_tool_retrieval_quality.py` 其他失败（Shard 4 等） | 同类 | 与 2/3 同一机制 |
| 5 | 各平台重复（3.10/3.11/3.12 × ubuntu/windows） | 同类 | 全平台一致失败，非环境差异 |

---

## 3. 负样本泄漏具体数据（G4_list_family_q08）

**query**:`查看系统中运行的进程`
**预期正样本**:`list_processes`（进程列表）
**负样本**:`list_async_tasks`（后台任务列表）

**BM25 top-5 selected**:

```
1. list_processes       ← 正确命中（进程列表）
2. get_persona_info     ← 泄漏候选
3. get_preferences      ← 泄漏候选
4. get_sensor_summary   ← 泄漏候选
5. list_async_tasks     ← 负样本泄漏（应为进程/任务语义区分）
```

**泄漏原因**:`list_processes` 与 `list_async_tasks` 描述中共享「list」「task」等词素，BM25 词频匹配无法区分「进程」与「任务」语义。

---

## 4. 召回缺失具体数据（q01 / q19）

### q01: '帮我搜索今天的天气'

```
selected    = ['get_weather', 'search_memory', 'search_lifetrace', 'get_status', 'software_search']
ground_truth= ['web_search', 'get_weather']
missing     = {'web_search'}
recall@5    = 0.50
```

**原因**:`web_search` 与 `search_memory`/`search_lifetrace`/`software_search` 同属 search_* 工具族，BM25 词频在族内分散，`web_search` 跌出 top-5。

### q19: '搜索网页内容并读取本地文件'

```
selected    = ['read_pdf', 'read_file', 'web_get', 'write_file', 'fetch_news']
ground_truth= ['web_search', 'read_file']
missing     = {'web_search'}
recall@5    = 0.50
```

**原因**:query 含「搜索网页」，但 `web_get`/`fetch_news` 词频高于 `web_search`，语义别名缺失。

---

## 5. Root Cause：分支修复未同步

三个 master 修复 commit **均不在 release/v1.2.0**（`git branch --contains` 验证）：

| Commit | 内容 | 所在分支 | 修复的失败 |
|--------|------|---------|-----------|
| `14d0474e` | test: G4_q08 标记 xfail 消除噪音 B（list_async_tasks 泄漏） | master ✓ develop ✓ **release ✗** | 用例 #1 |
| `928ac16e` | fix(data): B2 负样本措辞优化（list_processes/list_async_tasks 描述区分进程/任务语义） | master ✓ develop ✓ **release ✗** | 用例 #1 |
| `b2337502` | feat(retrieval): web_search 描述补充语义别名消除 q01/q19 召回缺口（D1） | master ✓ develop ✓ **release ✗** | 用例 #2/#3 |

**证据链**:
1. 本地 `_XFAIL_CASES`（develop）含 `("G4_list_family", "查看系统中运行的进程")` → xfail
2. CI 日志（PR head）该用例为 `FAILED` 而非 `XFAIL` → release 分支无该标记
3. `git show origin/release/v1.2.0:tests/unit/test_tool_negative_samples.py` 无 q08 条目

---

## 6. 修复方案

【变易】同步 master 修复到 release/v1.2.0：

```bash
git checkout release/v1.2.0
git cherry-pick 14d0474e 928ac16e b2337502   # 若已 cherry-pick 过则跳过
git push origin release/v1.2.0
```

注意：`928ac16e` 若与 release 中负样本库版本有冲突需手工合并（data/tool_negative_samples.json）。

---

## 7. 附：同 run 中其他失败（与本报告无关）

- **性能测试全挂**:NumPy 2.0 `np.float_` 移除 + pytest-benchmark `run_test` API 不兼容（环境问题）
- **集成测试 3 个**:工具调用断言 / NoneType.strip / UTF-8 解码（另见集成测试分析）
- **边界扫描**:high 116 > baseline 115，新增 1 个硬编码值（**已单独修复**，见 `38defa95`）
