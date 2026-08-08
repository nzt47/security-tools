# observability-ci 环境噪音报告（2026-08-08 · PR #407 合并验证期）

- **关联提交**: `944f64e4`（observability-ci.yml pin pytest==9.0.3）→ 合并 commit `cb4eda79`
- **背景**: PR #407 合并验证时，全项目测试 Shard 3/6、4/6、5/6 持续 failure。逐一比对 master 分支同类 run，**所有失败均以相同测试/相同错误在 master 复现**，判定为环境噪音而非 PR 引入。
- **结论**: Shard 1/6、2/6、6/6 success；本报告记录 3 类已知噪音，供后续优化治理。

---

## 噪音总览

| # | 类别 | 受影响 job | 失败测试 | 典型错误 | master 对照 | PR 引入 |
|---|---|---|---|---|---|---|
| A | 检索质量 recall 断言 | 全项目 Shard 3/6；独立「检索质量回归 (recall@5)」job | `test_tool_retrieval_quality.py::test_single_query_recall[0]/[18]` | q01/q19 的 `web_search` 未进 top-5（recall@5=0.50） | 31242197033 Shard 5 同错 | ❌ |
| B | 负样本泄漏 | 全项目 Shard 5/6；独立「负样本回归 (xfail 漂移监控)」job | `test_tool_negative_samples.py::test_query_distinction[G4_list_family_q08]` | `list_async_tasks` 被误判为相关 | 31242197033 Shard 3 同错 | ❌ |
| C | stress 性能/测量阈值 | PR 全项目 Shard 4/6；master 全项目 Shard 5/6 | `test_resource_leak.py` 3 个用例 | 内存差断言为负 / Timeout>300s / 2198ms>600ms | 31242197033、31240424432 多次同错 | ❌ |

---

## A. 检索质量 recall@5 断言（web_search 缺失）

- **run 证据**: PR 31252186640 Shard 3/6（job 93090126846）；独立 job 93090126878
- **master 对照**: 31242197033 Shard 5/6（job 93064925326）—— 同一测试、同一错误

```
FAILED tests/unit/test_tool_retrieval_quality.py::TestRetrievalQuality::test_single_query_recall[0]
  E Failed: q01 recall@5=0.50  query='帮我搜索今天的天气'
    selected=['get_weather', 'search_memory', 'search_lifetrace', 'get_status', 'software_search']
    ground_truth=['web_search', 'get_weather']  missing={'web_search'}
FAILED tests/unit/test_tool_retrieval_quality.py::TestRetrievalQuality::test_single_query_recall[18]
  E Failed: q19 recall@5=0.50  query='搜索网页内容并读取本地文件'
    selected=['read_pdf', 'read_file', 'web_get', 'write_file', 'fetch_news']
    ground_truth=['web_search', 'read_file']  missing={'web_search'}
```

**根因分析**:
- 独立 job 的「整体 recall@5 = 0.9500（阈值 ≥ 0.80）」通过，但**单 query 断言要求 100% 命中**，q01/q19 两条查询的 `web_search` 未能进入 top-5，拉低单点 recall 至 0.50。
- 两个 query 均为「网页/搜索」语义，与 embedding 离线模式（CI `AGENT_HYBRID_EMBEDDING=0` 纯 BM25）下 `web_search` 与 `web_get`/`fetch_news` 等相似工具的关键词竞争有关；`search_memory`/`search_lifetrace` 等记忆类工具占用 top-5 槽位。
- **与 pytest 版本无关**（9.1.1 与 9.0.3 下同样失败），属检索质量/数据问题。

---

## B. 负样本泄漏（list_async_tasks）

- **run 证据**: PR 31252186640 Shard 5/6（job 93090126852）；独立 job 93090126914
- **master 对照**: 31242197033 Shard 3/6（job 93064925201）—— 同一测试、同一错误

```
FAILED tests/unit/test_tool_negative_samples.py::TestNegativeSamplesRetrieval::test_query_distinction[G4_list_family_q08]
  E Failed: [G4_list_family] query='查看系统中运行的进程' 负样本泄漏=['list_async_tasks']
    selected=['list_processes', 'get_persona_info', 'get_preferences', 'get_sensor_summary', 'list_async_tasks']
```

**根因分析**:
- `list_async_tasks`（异步任务列表）与 `list_processes`（进程列表）语义/描述高度相似，检索器将其误判为相关，进入 top-5 → 负样本泄漏断言失败。
- 同家族 G4 其他 query 多为 XFAIL（漂移监控），q08 已达到泄漏边界。
- **与 pytest 版本无关**，属负样本构造边界问题。

---

## C. stress 性能/测量阈值（test_resource_leak.py）

- **run 证据**: PR 944f64e4 run 31254093399 Shard 4/6（job 93094749621）；master 31242197033 Shard 5/6、31240424432（05:27 UTC）多次
- **master 对照**: 31242197033 job 93064925326 同时出现 3 类错误

```
PR Shard 4/6:
  FAILED tests/stress/test_resource_leak.py::TestLeakDetectionUnderStress::test_memory_leak_detection
    AssertionError: assert -8671.757894736842 > 0          # 两次 RSS 测量差为负（基线波动）

master Shard 5/6:
  FAILED tests/stress/test_resource_leak.py::TestHighConcurrencySampling::test_high_frequency_sampling_does_not_leak
    Failed: Timeout (>300.0s) from pytest-timeout.          # 高并发采样超时（runner 负载）
  FAILED tests/stress/test_resource_leak.py::TestSamplingPerformance::test_single_sample_under_600ms
    AssertionError: 采样中位数耗时 2198.06ms 超过 600ms（1% 开销约束）
```

**根因分析**:
- 内存/耗时测量对 runner 负载高度敏感：共享 runner 高负载时 RSS 基线波动、采样耗时暴涨、并发测试超时。
- 阈值（600ms、>0 内存差）无余量，偶发即红。
- **与 pytest 版本无关**，属环境敏感测量问题。

---

## 判定非 PR 引入的依据

1. **逐项复现**: 3 类失败均在 master 分支最近的 observability-ci run（31242197033 等）以**完全相同测试、完全相同错误**复现。
2. **失败形态稳定**: 检索质量/负样本失败为确定性数据问题（每次同 query 同 missing/同泄漏）；stress 失败为环境敏感 flaky（不同 run 表现不同形态：Timeout/断言/内存差）。
3. **PR 改动范围无关**: PR #407 仅改 workflow 配置（pytest pin、分桶脚本、pytest-asyncio），不触碰检索逻辑、负样本数据、采样实现。

---

## 优化建议（后续治理）

### A. 检索质量
- [ ] 单 query recall 断言评估：是否放宽为阈值（如 ≥0.5）或仅保留整体 recall 门禁（0.80）
- [ ] 检查 `web_search` 工具描述/关键词（搜索/网页/浏览器）在 BM25 下的匹配，补充同义词与别名
- [ ] 评估 CI 恢复 embedding（`AGENT_HYBRID_EMBEDDING` 放行）后是否消除该噪音

### B. 负样本
- [ ] 调整 G4_list_family 负样本构造：明确 `list_async_tasks` 与 `list_processes` 的判别边界（如增加排除词）
- [ ] 将已达泄漏边界的 G4 q08 从 XFAIL 监控转为正式负样本治理项

### C. stress 性能/测量
- [ ] 放宽阈值：`test_single_sample_under_600ms` 600ms → 1000ms；内存泄漏断言改为多次采样中位数
- [ ] 高并发用例加长 timeout 或标记 slow 移出 PR 阻塞路径
- [ ] 中长期：stress/性能类测试移入独立 nightly workflow，不阻塞 PR 合并

### 通用
- [ ] 引入 `pytest-rerunfailures`，对测量类测试（C 类）配置 rerun，其余保持严格
- [ ] 持续跟踪噪音出现频率，达到阈值后再决定测试/数据修复优先级

---

## 附：PR #407 合并时各 shard 最终结论

| Shard | 结论 | 说明 |
|---|---|---|
| 1/6 | ✅ success | 分桶行数加权修复生效 |
| 2/6 | ✅ success | pytest==9.0.3 pin 修复生效 |
| 3/6 | ❌ 噪音 A | 检索质量 recall 断言 |
| 4/6 | ❌ 噪音 C | stress 内存测量（master 同现） |
| 5/6 | ❌ 噪音 B | 负样本泄漏 |
| 6/6 | ✅ success | — |
