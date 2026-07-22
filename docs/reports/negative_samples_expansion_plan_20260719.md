# 负样本库扩充规划与执行记录

> **文档日期**:2026-07-19
> **关联报告**:[tool_retrieval_eval_report_20260719.md](./tool_retrieval_eval_report_20260719.md)
> **关联 commit**:扩充后待提交
> **状态**:**已完成**(10 组 25 query + 回归测试)

---

## 1. 背景与目标

### 1.1 背景

[评估报告](./tool_retrieval_eval_report_20260719.md) §3.2 识别了 3 个 BM25 单路检索的风险点:
- **q01**「帮我搜索今天的天气」:web_search 排第 5(search_* 词根混淆)
- **q07**「压缩文件夹到 zip」:decompress 排第 1(BM25 无法区分压缩/解压方向)
- **q08**「把 JSON 转换为 YAML 格式」:yaml_to_json 排第 1(BM25 无法区分转换方向)

原负样本库 v1.0 含 6 组 16 query,未覆盖这些风险点。

### 1.2 目标

**短期(本次扩充)**:6 组 16 query → 10 组 25 query,覆盖报告风险点 + 补充新工具族
**长期(与 Reranker 对接)**:25 query → 200+ query(通过 LLM 数据增强),训练 Cross-Encoder

---

## 2. 现状盘点(v1.0)

| 组 | 工具族 | query 数 | 区分维度 |
|----|--------|----------|----------|
| G1 | web_search / web_get / fetch_news | 3 | 搜索/抓取/新闻 |
| G2 | read_file / read_pdf / merge_pdf | 2 | 读取/合并 |
| G3 | shell_execute / run_program | 2 | 别名关系 |
| G4 | list_directory / list_processes / list_async_tasks | 3 | 目录/进程/任务 |
| G5 | ext_install / ext_list / software_install | 3 | 扩展/软件 |
| G6 | submit_task / schedule_task / cancel_task | 3 | 提交/调度/取消 |
| **合计** | | **16** | |

---

## 3. 扩充设计(v1.1)

### 3.1 新增 4 组(P0 + P1)

#### G7 compress / decompress(压缩/解压方向性)

**来源**:报告 q07 风险点
**区分维度**:动词方向(压缩 vs 解压)

| Query | expected_positive | negative | xfail |
|-------|-------------------|----------|-------|
| 把 logs 文件夹压缩成 zip | compress | decompress | 是(BM25 退化) |
| 解压 archive.tar.gz 到当前目录 | decompress | compress | 是(负样本泄漏) |

#### G8 json_to_yaml / yaml_to_json(格式转换方向性)

**来源**:报告 q08 风险点
**区分维度**:转换方向(JSON→YAML vs YAML→JSON)

| Query | expected_positive | negative | xfail |
|-------|-------------------|----------|-------|
| 把 config.json 转换成 yaml 格式 | json_to_yaml | yaml_to_json | 是(BM25 退化) |
| 读取 data.yaml 转成 JSON 对象 | yaml_to_json | json_to_yaml | 是(负样本泄漏) |

#### G9 web_search / search_memory / search_lifetrace(搜索语义)

**来源**:报告 q01 风险点
**区分维度**:搜索对象(网页 vs 记忆 vs LifeTrace)

| Query | expected_positive | negative | xfail |
|-------|-------------------|----------|-------|
| 在 Google 上搜索 Python 异步教程 | web_search | search_memory, search_lifetrace | 是(召回缺失) |
| 回忆之前讨论过的项目架构 | search_memory | web_search, search_lifetrace | 是(召回缺失) |
| 检索 lifetrace 中的历史对话 | search_lifetrace | web_search, search_memory | 否(关键词精确匹配) |

#### G10 read_file / write_file(读写方向)

**来源**:报告 q02 观察(write_file 误入 top-5)
**区分维度**:读写方向

| Query | expected_positive | negative | xfail |
|-------|-------------------|----------|-------|
| 读取 config.yaml 的内容 | read_file | write_file | 是(负样本泄漏) |
| 把处理结果写入 output.json | write_file | read_file | 否(动词精确匹配) |

### 3.2 扩充后汇总

| 组 | 工具族 | query 数 | xfail 数 |
|----|--------|----------|----------|
| G1 | web_search / web_get / fetch_news | 3 | 2 |
| G2 | read_file / read_pdf / merge_pdf | 2 | 0 |
| G3 | shell_execute / run_program | 2 | 0 |
| G4 | list_directory / list_processes / list_async_tasks | 3 | 2 |
| G5 | ext_install / ext_list / software_install | 3 | 1 |
| G6 | submit_task / schedule_task / cancel_task | 3 | 3 |
| G7 | compress / decompress(新) | 2 | 2 |
| G8 | json_to_yaml / yaml_to_json(新) | 2 | 2 |
| G9 | web_search / search_memory / search_lifetrace(新) | 3 | 2 |
| G10 | read_file / write_file(新) | 2 | 1 |
| **合计** | | **25** | **15** |

---

## 4. 实施步骤

### 步骤 1:扩充负样本库文件 ✅

**文件**:`data/tool_negative_samples.json`
**操作**:version 1.0 → 1.1,追加 G7/G8/G9/G10 共 9 query
**结果**:10 组 25 query,JSON 合法性验证通过

### 步骤 2:创建回归测试 ✅

**文件**:`tests/unit/test_tool_negative_samples.py`
**测试结构**:
- TestNegativeSamplesStructure(7 测试):JSON 结构合法性
- TestNegativeSamplesRetrieval(25 测试):parametrize 展开 25 query
- TestNegativeSamplesStatistics(4 测试):xfail 数量防漂移
- TestHybridRetrieverAvailable(3 测试):基础检索可用性

**xfail 策略**:
- 失败时调用 `pytest.xfail()` 标记,不报错
- xfail 原因分类:召回缺失 / 负样本泄漏 / 方向性混淆
- 未来 Reranker 上线后,xfail case 转为 PASS,届时移除标记

### 步骤 3:验证 ✅

| 验证项 | 命令 | 结果 |
|--------|------|------|
| JSON 合法性 | `python -c "import json; ..."` | 10 组 25 query ✓ |
| 负样本回归测试 | `pytest tests/unit/test_tool_negative_samples.py -v` | 24 passed + 15 xfailed ✓ |
| 评估测试不破坏 | `pytest tests/unit/test_tool_retrieval_quality.py` | 22/22 passed ✓ |
| 集成测试不破坏 | `pytest tests/unit/test_tool_router_hybrid_integration.py` | 14/14 passed ✓ |
| hybrid 单元不破坏 | `pytest tests/unit/test_tool_router_hybrid.py` | 46/46 passed ✓ |
| tool_router 不破坏 | `pytest agent/tests/test_tool_router.py` | 19/19 passed ✓ |
| **全量回归** | 上述 4 个测试文件合计 | **101/101 passed** |

---

## 5. 执行结果

### 5.1 测试结果汇总

```
24 passed, 15 xfailed in 1.72s
```

**通过 case(10 个)**:
- G2 q03/q04(pdf 读取/合并)
- G3 q05/q06(shell 执行/程序启动)
- G5 q11/q12(列扩展/装软件)
- G9 q22(lifetrace 检索,关键词精确匹配)
- G10 q24(写入文件,动词精确匹配)
- G1/G4/G6 各有部分通过

**xfail case(15 个)**,按失败类型分布:

| 失败类型 | 数量 | 典型 case |
|----------|------|-----------|
| 召回缺失(BM25 词频分散) | 8 | G1 q00, G4 q07, G6 q14, G9 q20 等 |
| 负样本泄漏(无法区分相似工具) | 5 | G6 q13/q15, G7 q17, G8 q19, G10 q23 |
| 方向性混淆(无法区分动词方向) | 2 | G7 q16, G8 q18(报告明确识别) |

### 5.2 关键发现

1. **BM25 单路检索能力边界**:25 个区分性 case 中仅 10 个通过(40% 通过率),说明 BM25 对相似工具的区分能力有限
2. **召回缺失比负样本泄漏更严重**:8 个召回缺失 case 表明 expected_positive 甚至不在 top-5,BM25 排序质量不足
3. **方向性混淆是 BM25 固有缺陷**:G7/G8 的 4 个 case 全部失败,验证了报告 §3.2 的判断
4. **G2/G3 全通过**:别名关系(G3)和 PDF 工具族(G2)区分度足够,无需 Reranker 介入

### 5.3 对 Reranker 的需求论证

15 个 xfail case 是引入 Cross-Encoder Reranker 的核心依据:
- **召回缺失型**:需 Reranker 重排,提升 expected_positive 排名
- **负样本泄漏型**:需 Reranker 精细化区分,降低 negative 排名
- **方向性混淆型**:需 Reranker 理解动词语义,区分转换/压缩方向

预期 Reranker 上线后:15 个 xfail → 15 个 PASS,通过率 40% → 100%

---

## 6. 风险与权衡

### 6.1 xfail 标记的双刃剑

**收益**:明确标记 BM25 已知缺陷,为 Reranker 上线提供回归基线
**风险**:xfail 可能被忽视,长期积累成技术债
**缓解**:
- 每个 xfail 必须含分类原因(召回缺失/负样本泄漏/方向性混淆)
- 统计测试 `test_xfail_cases_count_is_15` 防止 xfail 数量漂移
- Reranker 上线后必须移除 xfail 验证修复

### 6.2 负样本库膨胀控制

**原则**:宁精勿多,每组聚焦一个区分维度
**上限**:首版 10 组 25 query,后续每月评估是否新增,单次扩充不超过 3 组
**淘汰机制**:若某组所有 query 都被 BM25 完美区分(连续 3 次评估 PASS),可考虑退役该组

### 6.3 与 Reranker 训练的对接

**数据格式**:当前 JSON 结构需转换为 `(query, positive_tool, negative_tools)` 三元组
**转换脚本**:计划在 Reranker 立项时编写 `scripts/convert_negative_samples_to_trainset.py`
**数据量**:25 query 不足以训练 Cross-Encoder,需扩到 200+ 条(可通过 LLM 数据增强生成)

---

## 7. 未来工作

### 7.1 短期(当前架构内)

- [ ] CI 集成:将 `test_tool_negative_samples.py` 加入 CI 必跑套件
- [ ] 工具描述优化:在 `schedule_task` description 中前置「创建」动词,提升 G6 q14 通过率
- [ ] 监控 xfail 漂移:工具描述变更后重新评估,更新 xfail 标记

### 7.2 中期(引入 Embedding)

- [ ] 解决 SentenceTransformer 加载崩溃(Windows 0xC0000005 / Linux SIGILL)
- [ ] Embedding 上线后评估:对比纯 BM25 vs BM25+Embedding 的 xfail 数量
- [ ] 若 Embedding 能解决部分 xfail,更新标记

### 7.3 长期(Cross-Encoder Reranker)

- [ ] 数据增强:25 query → 200+ query(LLM 生成 + 人工校验)
- [ ] 训练 Cross-Encoder(基于 distilbert-multilingual 或类似轻量模型)
- [ ] 两阶段检索:BM25+Embedding 召回 top-20 → Cross-Encoder 重排 top-5
- [ ] 移除所有 xfail 标记,验证 25/25 PASS

---

## 8. 附录

### 8.1 相关文件

- 负样本库:[data/tool_negative_samples.json](../../data/tool_negative_samples.json)(v1.1, 10 组 25 query)
- 回归测试:[tests/unit/test_tool_negative_samples.py](../../tests/unit/test_tool_negative_samples.py)(39 测试)
- 评估报告:[docs/reports/tool_retrieval_eval_report_20260719.md](./tool_retrieval_eval_report_20260719.md)
- 检索器实现:[agent/tool_router_hybrid.py](../../agent/tool_router_hybrid.py)

### 8.2 复现命令

```bash
cd c:\Users\Administrator\agent
$env:PYTHONIOENCODING='utf-8'
$env:AGENT_HYBRID_EMBEDDING='0'
python -m pytest tests/unit/test_tool_negative_samples.py -v
```

预期输出:`24 passed, 15 xfailed`

### 8.3 xfail case 完整列表

| # | 组 | Query | 失败类型 |
|---|----|-------|----------|
| 1 | G1 | 在百度上搜索 Python 教程 | 召回缺失 |
| 2 | G1 | 抓取 https://example.com 的 HTML 内容 | 召回缺失 |
| 3 | G4 | 列出 /home/user 下的所有文件 | 召回缺失 |
| 4 | G4 | 查看提交的后台任务列表 | 召回缺失 |
| 5 | G5 | 安装 markdown 编辑器扩展 | 召回缺失 |
| 6 | G6 | 提交一个后台数据处理任务 | 负样本泄漏 |
| 7 | G6 | 创建每天凌晨 3 点执行的定时任务 | 召回缺失 |
| 8 | G6 | 取消任务 ID 为 abc123 的后台任务 | 负样本泄漏 |
| 9 | G7 | 把 logs 文件夹压缩成 zip | 方向性混淆 |
| 10 | G7 | 解压 archive.tar.gz 到当前目录 | 负样本泄漏 |
| 11 | G8 | 把 config.json 转换成 yaml 格式 | 方向性混淆 |
| 12 | G8 | 读取 data.yaml 转成 JSON 对象 | 负样本泄漏 |
| 13 | G9 | 在 Google 上搜索 Python 异步教程 | 召回缺失 |
| 14 | G9 | 回忆之前讨论过的项目架构 | 召回缺失 |
| 15 | G10 | 读取 config.yaml 的内容 | 负样本泄漏 |

---

*本文档记录负样本库 v1.0 → v1.1 扩充过程,作为 Reranker 立项的依据。*
