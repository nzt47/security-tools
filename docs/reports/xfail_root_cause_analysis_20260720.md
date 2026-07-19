# 12 个 xfail case Root Cause 分析报告(BM25 固有缺陷)

> **分析日期**:2026-07-20
> **测试环境**:BM25 单路降级模式(`AGENT_HYBRID_EMBEDDING=0`)
> **数据来源**:`pytest tests/unit/test_tool_negative_samples.py -v` 实测输出
> **关联文档**:
>   - [评估报告](tool_retrieval_eval_report_20260719.md) §7.5 剩余 xfail 分布
>   - [负样本扩充规划](negative_samples_expansion_plan_20260719.md) §8.3 xfail 完整列表
>   - [Reranker 集成方案](../proposals/tool_router_reranker_integration_plan.md)

---

## 1. 概述

### 1.1 xfail 总览

经 todo2 优化(5 个工具 description 优化)后,负样本回归测试从 15 个 xfail 降至 **12 个 xfail**(27 passed + 12 xfailed)。

**实际失败类型分布**(基于实测 selected 列表重新分类,修正评估报告 §7.5 的分类):

| 失败类型 | 数量 | case 列表 |
|---------|------|----------|
| 召回缺失(expected_positive 不在 top-5) | 5 | G1_q00, G4_q07, G4_q09, G6_q14, G9_q20 |
| 负样本泄漏(negative 进入 top-5) | 6 | G1_q01, G6_q13, G6_q15, G7_q17, G8_q18, G8_q19 |
| 方向性混淆(negative 排在 expected 前面) | 1 | G7_q16 |
| **合计** | **12** | |

> **修正说明**:评估报告 §7.5 原标注"方向性混淆 2 个(G7_q17、G8_q18)",经实测核对:
> - G7_q17 的 expected_positive=decompress 在 selected 第 1 位,negative=compress 在第 2 位 → 实际是**负样本泄漏**(非方向性混淆)
> - G8_q18 的 expected_positive=json_to_yaml 在 selected 第 1 位,negative=yaml_to_json 在第 2 位 → 实际是**负样本泄漏**(非方向性混淆)
> - 仅 G7_q16 是真正的方向性混淆(decompress 排第 1 > compress 排第 2,但 expected 是 compress)

### 1.2 分析方法

对每个 case 收集:
- **query**:用户输入
- **expected_positive**:期望召回的工具(人工标注)
- **negative**:不应召回的同族工具
- **selected**:BM25 实际返回的 top-5(实测)
- **失败类型**:基于 selected 与 expected/negative 的关系判定

**BM25 固有缺陷判定标准**:
- 通过工具描述优化(todo2 已尝试)无法解决
- 根因在于 BM25 算法本身的局限性(词袋模型 / 无语义理解 / 无方向感知)

---

## 2. BM25 算法原理与固有缺陷

### 2.1 BM25 评分公式

```
score(q, d) = Σ_t IDF(t) * [tf(t,d) * (k1+1)] / [tf(t,d) + k1*(1-b+b*|d|/avgdl)]
```

- `t`:query 中的词
- `IDF(t)`:词的逆文档频率(稀有词权重高)
- `tf(t,d)`:词在文档中的词频
- `|d|`:文档长度,`avgdl`:平均文档长度
- `k1=1.5`:词频饱和参数,`b=0.75`:文档长度归一化参数

### 2.2 BM25 的 5 个固有缺陷(导致 xfail)

| 缺陷 | 算法根因 | 影响 case |
|------|---------|----------|
| **D1:词袋模型,无词序** | BM25 只统计词频,不考虑词的顺序 | G7_q16(压缩/解压方向) |
| **D2:字面匹配,无语义** | "回忆"不匹配 search_memory 的 description | G9_q20(web_search 召回缺失) |
| **D3:词根混淆** | "search" 词根同时出现在 web_search/search_memory/search_lifetrace | G1_q00, G9_q20 |
| **D4:词频分散(长 query)** | 长 query 的关键词被多个工具分散匹配 | G6_q14(schedule_task 召回缺失) |
| **D5:无方向感知** | "转换"同时匹配 json_to_yaml 和 yaml_to_json | G7_q16, G8_q18, G8_q19 |

### 2.3 为什么工具描述优化无法解决

todo2 已尝试优化 5 个工具 description(前置用户常用动词/关键词),但存在边界:
- **优化边界 1**:增加目标词会同时提升同族工具的得分(如增加"搜索"提升 web_search,但也提升 search_memory)
- **优化边界 2**:方向性词汇("压缩"vs"解压")在 BM25 中无法区分,因为 compress/decompress 的 description 都含 "compress" 词根
- **优化边界 3**:长 query 的词频分散是 BM25 固有特性,描述优化无法改变 query 的词频分布

---

## 3. 12 个 xfail case 详解

### 3.1 召回缺失型(5 个)

#### Case 1: G1_q00 「在百度上搜索 Python 教程」

| 字段 | 值 |
|------|-----|
| expected_positive | web_search |
| negative | web_get, fetch_news |
| selected(top-5) | run_program, ext_discover, software_search, compress, json_query |
| 失败 | web_search 不在 top-5 |

**Root Cause**:
- query 分词后:`[在, 百, 度, 上, 搜, 索, python, 教, 程]`
- web_search description:「网页搜索,搜索互联网信息。默认单引擎搜索...」
- **缺陷 D3(词根混淆)**:`search` 词根同时出现在 web_search/search_memory/search_lifetrace,三者 IDF 相近,BM25 无法区分
- **缺陷 D2(无语义)**:query 含「百度」(搜索引擎名),但 BM25 不理解"百度→搜索引擎→web_search"的语义链
- `software_search` 排第 4(含 `search` 词根 + `软件` 词根匹配「教程」)

**为何 todo2 优化未解决**:
- todo2 已在 web_search description 前置「网页搜索」,但 query 用「百度搜索」而非「网页搜索」,字面不匹配
- 增加「百度」到 description 不合理(特定品牌名不应写入通用工具描述)

**Reranker 预期**:
- Cross-Encoder 理解「百度搜索」与「网页搜索,搜索互联网信息」的语义相关性
- 预期 rerank_score:web_search > 0.5,software_search < 0.1

---

#### Case 2: G4_q07 「列出 /home/user 下的所有文件」

| 字段 | 值 |
|------|-----|
| expected_positive | list_directory |
| negative | list_processes, list_async_tasks |
| selected(top-5) | list_scheduled_tasks, web_download, list_async_tasks, get_sensor_summary, expand_context |
| 失败 | list_directory 不在 top-5 + list_async_tasks 泄漏 |

**Root Cause**:
- query 分词后:`[列, 出, home, user, 下, 的, 所, 有, 文, 件]`
- list_directory description:「列出目录(文件夹)中的文件和子目录,支持指定路径和显示隐藏文件」
- **缺陷 D4(词频分散)**:query 是长句,「列出」「文件」等词被多个工具分散匹配
- `list_scheduled_tasks` 排第 1(含 `list` + `任务` 词根,但 query 无"任务")
- 实测 list_directory 得分被 list_scheduled_tasks 挤出 top-5

**为何 todo2 优化未解决**:
- todo2 已在 list_directory description 增加「文件夹」,但 query 用「/home/user」(路径)而非「文件夹」
- 路径 `/home/user` 在 BM25 中被分词为 `home`/`user`,与 list_directory description 无匹配

**Reranker 预期**:
- Cross-Encoder 理解「列出 /home/user 下的文件」与「列出目录中的文件和子目录」的语义相关性
- 预期 rerank_score:list_directory > 0.6,list_scheduled_tasks < 0.1

---

#### Case 3: G4_q09 「查看提交的后台任务列表」

| 字段 | 值 |
|------|-----|
| expected_positive | list_async_tasks |
| negative | list_directory, list_processes |
| selected(top-5) | submit_task, get_persona_info, get_preferences, get_sensor_summary, get_task_result |
| 失败 | list_async_tasks 不在 top-5 |

**Root Cause**:
- query 分词后:`[查, 看, 提, 交, 的, 后, 台, 任, 务, 列, 表]`
- list_async_tasks description:「列出所有异步任务(含已完成、运行中、等待中)」
- **缺陷 D4(词频分散)**:「任务」匹配 submit_task/get_task_result,「列表」匹配 list_*,分散导致 list_async_tasks 得分不足
- `submit_task` 排第 1(含 `task` + `提交` 词根)
- list_async_tasks 含 `list` + `async` + `tasks`,但 query 无 `async` 字面

**为何 todo2 优化未解决**:
- list_async_tasks description 已含「异步任务」,但 query 用「后台任务」(非「异步任务」),字面不匹配
- 增加「后台」到 description 会导致 G4_q08 退化(已验证回滚)

**Reranker 预期**:
- Cross-Encoder 理解「后台任务列表」与「异步任务」的语义等价性
- 预期 rerank_score:list_async_tasks > 0.5,submit_task < 0.2

---

#### Case 4: G6_q14 「创建每天凌晨 3 点执行的定时任务」

| 字段 | 值 |
|------|-----|
| expected_positive | schedule_task |
| negative | submit_task, cancel_task |
| selected(top-5) | list_scheduled_tasks, split_pdf, get_weather, json_query, get_pdf_info |
| 失败 | schedule_task 不在 top-5 |

**Root Cause**:
- query 分词后:`[创, 建, 每, 天, 凌, 晨, 3, 点, 执, 行, 的, 定, 时, 任, 务]`(15 词,长 query)
- schedule_task description:「创建定时任务,支持每天/每周/每月...」
- **缺陷 D4(词频分散)**:15 词长 query,关键词被分散:`创建`匹配多个工具,`定时`匹配 list_scheduled_tasks,`任务`匹配 submit_task
- `list_scheduled_tasks` 排第 1(含 `scheduled` + `tasks`,匹配「定时任务」)
- schedule_task 含 `schedule` + `task`,但 query 用「定时」(中文)而非 `schedule`(英文)

**为何 todo1 优化未解决**:
- todo1 已在 schedule_task description 前置「创建定时任务」,但导致 q01 退化(已回滚)
- 这是 BM25 长查询词频分散的**固有缺陷**,描述优化无法解决

**Reranker 预期**:
- Cross-Encoder 理解「创建...定时任务」与「创建定时任务,支持每天/每周/每月」的语义匹配
- 预期 rerank_score:schedule_task > 0.7,list_scheduled_tasks < 0.3(列任务 vs 创建任务)

---

#### Case 5: G9_q20 「在 Google 上搜索 Python 异步教程」

| 字段 | 值 |
|------|-----|
| expected_positive | web_search |
| negative | search_memory, search_lifetrace |
| selected(top-5) | run_program, ext_discover, cancel_task, software_search, get_task_status |
| 失败 | web_search 不在 top-5 |

**Root Cause**:
- query 分词后:`[在, google, 上, 搜, 索, python, 异, 步, 教, 程]`
- web_search description:「网页搜索,搜索互联网信息...」
- **缺陷 D3(词根混淆)**:`search` 词根同时出现在 web_search/search_memory/search_lifetrace,三者 IDF 相近
- **缺陷 D2(无语义)**:query 含「Google」(搜索引擎名),但 BM25 不理解"Google→搜索引擎→web_search"
- `software_search` 排第 4(含 `search` + `软件` 词根匹配「教程」)
- web_search 得分被 `search` 词根的多个工具分散

**为何 todo2 优化未解决**:
- todo2 已在 web_search description 前置「网页搜索」,但 query 用「Google 搜索」而非「网页搜索」
- 增加「Google」到 description 不合理(特定品牌名)

**Reranker 预期**:
- Cross-Encoder 理解「Google 搜索」与「网页搜索,搜索互联网信息」的语义相关性
- 预期 rerank_score:web_search > 0.6,software_search < 0.1

---

### 3.2 负样本泄漏型(6 个)

#### Case 6: G1_q01 「抓取 https://example.com 的 HTML 内容」

| 字段 | 值 |
|------|-----|
| expected_positive | web_get |
| negative | web_search, fetch_news |
| selected(top-5) | web_get, fetch_news, read_pdf, search_memory, web_xpath |
| 失败 | fetch_news 泄漏(web_get 在第 1 位 ✓) |

**Root Cause**:
- query 分词后:`[抓, 取, https, example, com, 的, html, 内, 容]`
- web_get description:「发送 HTTP GET 请求抓取网页内容,获取 HTML...」(todo2 优化后)
- fetch_news description 含「抓取」词根(新闻抓取)
- **缺陷 D2(无语义)**:BM25 不理解"抓取 URL 内容"与"抓取新闻"的语义差异
- todo2 优化已让 web_get 排第 1(✓),但 fetch_news 仍泄漏(排第 2)

**Reranker 预期**:
- Cross-Encoder 区分「抓取指定 URL」(web_get)与「抓取新闻」(fetch_news)
- 预期 rerank_score:web_get > 0.7,fetch_news < 0.2

---

#### Case 7: G6_q13 「提交一个后台数据处理任务」

| 字段 | 值 |
|------|-----|
| expected_positive | submit_task |
| negative | schedule_task, cancel_task |
| selected(top-5) | submit_task, get_task_result, compress, schedule_task, web_post |
| 失败 | schedule_task 泄漏(submit_task 在第 1 位 ✓) |

**Root Cause**:
- query 分词后:`[提, 交, 一, 个, 后, 台, 数, 据, 处, 理, 任, 务]`
- submit_task description 含 `提交` + `任务`,排第 1(✓)
- schedule_task description 含 `任务` 词根,排第 4(泄漏)
- **缺陷 D5(无方向感知)**:BM25 不理解"提交一次性任务"与"创建定时任务"的方向差异
- `任务` 是高频词,IDF 低,但被多个 task_* 工具共享

**Reranker 预期**:
- Cross-Encoder 区分「提交后台任务」(submit_task)与「定时任务」(schedule_task)
- 预期 rerank_score:submit_task > 0.6,schedule_task < 0.2

---

#### Case 8: G6_q15 「取消任务 ID 为 abc123 的后台任务」

| 字段 | 值 |
|------|-----|
| expected_positive | cancel_task |
| negative | submit_task, schedule_task |
| selected(top-5) | submit_task, cancel_scheduled_task, cancel_task, schedule_task, get_task_result |
| 失败 | submit_task + schedule_task 泄漏(cancel_task 在第 3 位) |

**Root Cause**:
- query 分词后:`[取, 消, 任, 务, id, 为, abc123, 的, 后, 台, 任, 务]`
- cancel_task description 含 `取消` + `任务`,但在 selected 排第 3
- `submit_task` 排第 1(含 `任务` 词根,词频更高)
- `cancel_scheduled_task` 排第 2(含 `取消` + `scheduled` + `task`)
- **缺陷 D4(词频分散)**:`任务` 出现 2 次(query 中重复),但被多个 task_* 工具共享
- **缺陷 D2(无语义)**:BM25 不理解"取消已提交任务"的精确语义

**Reranker 预期**:
- Cross-Encoder 理解「取消任务 ID」的精确语义,优先 cancel_task
- 预期 rerank_score:cancel_task > 0.7,submit_task < 0.2,schedule_task < 0.2

---

#### Case 9: G7_q17 「解压 archive.tar.gz 到当前目录」

| 字段 | 值 |
|------|-----|
| expected_positive | decompress |
| negative | compress |
| selected(top-5) | decompress, compress, list_directory, get_file_info, json_validate |
| 失败 | compress 泄漏(decompress 在第 1 位 ✓) |

**Root Cause**:
- query 分词后:`[解, 压, archive, tar, gz, 到, 当, 前, 目, 录]`
- decompress description 含 `解压`,排第 1(✓)
- compress description 含 `压缩` + `compress` 词根,排第 2(泄漏)
- **缺陷 D1(词袋模型)**:`archive`/`tar`/`gz` 同时匹配 compress 和 decompress(两者 description 都含压缩格式)
- **缺陷 D5(无方向感知)**:BM25 不理解"解压"与"压缩"的方向差异

**Reranker 预期**:
- Cross-Encoder 区分「解压」(decompress)与「压缩」(compress)
- 预期 rerank_score:decompress > 0.8,compress < 0.2

---

#### Case 10: G8_q18 「把 config.json 转换成 yaml 格式」

| 字段 | 值 |
|------|-----|
| expected_positive | json_to_yaml |
| negative | yaml_to_json |
| selected(top-5) | json_to_yaml, yaml_to_json, ext_configure, data_format_detect, get_weather |
| 失败 | yaml_to_json 泄漏(json_to_yaml 在第 1 位 ✓) |

**Root Cause**:
- query 分词后:`[把, config, json, 转, 换, 成, yaml, 格, 式]`
- json_to_yaml description:「将 JSON 字符串转换为 YAML 格式字符串」
- yaml_to_json description:「将 YAML 字符串转换为 JSON 格式字符串」
- 两者 description 词频**完全相同**(json/yaml/转换/格式/字符串),BM25 得分接近
- **缺陷 D1(词袋模型)**:BM25 不考虑词序,无法区分"JSON→YAML"和"YAML→JSON"
- **缺陷 D5(无方向感知)**:`json` 和 `yaml` 在两者 description 中都出现,BM25 无法区分转换方向

**Reranker 预期**:
- Cross-Encoder 理解「JSON 转换成 YAML」的方向(json_to_yaml)
- 预期 rerank_score:json_to_yaml > 0.8,yaml_to_json < 0.2

---

#### Case 11: G8_q19 「读取 data.yaml 转成 JSON 对象」

| 字段 | 值 |
|------|-----|
| expected_positive | yaml_to_json |
| negative | json_to_yaml |
| selected(top-5) | json_query, json_to_yaml, yaml_to_json, read_file, read_pdf |
| 失败 | json_to_yaml 泄漏(yaml_to_json 在第 3 位) |

**Root Cause**:
- query 分词后:`[读, 取, data, yaml, 转, 成, json, 对, 象]`
- yaml_to_json description:「将 YAML 字符串转换为 JSON 格式字符串」
- json_to_yaml description:「将 JSON 字符串转换为 YAML 格式字符串」
- 两者词频相同(同 Case 10),BM25 得分接近
- `json_query` 排第 1(含 `json` 词根),`json_to_yaml` 排第 2,`yaml_to_json` 排第 3
- **缺陷 D1+D5**:词袋模型 + 无方向感知,yaml_to_json 被 json_query 和 json_to_yaml 挤到第 3

**Reranker 预期**:
- Cross-Encoder 理解「YAML 转成 JSON」的方向(yaml_to_json)
- 预期 rerank_score:yaml_to_json > 0.8,json_to_yaml < 0.2,json_query < 0.1

---

### 3.3 方向性混淆型(1 个)

#### Case 12: G7_q16 「把 logs 文件夹压缩成 zip」

| 字段 | 值 |
|------|-----|
| expected_positive | compress |
| negative | decompress |
| selected(top-5) | decompress, compress, list_directory, generate_tool, arch_diagram |
| 失败 | decompress 排第 1 > compress 排第 2(方向反了) |

**Root Cause**:
- query 分词后:`[把, logs, 文, 件, 夹, 压, 缩, 成, zip]`
- compress description:「将文件或目录压缩为 zip 或 tar.gz 格式...」
- decompress description 含 `解压` + `compress` 词根
- **缺陷 D1(词袋模型)**:`压缩` 匹配 compress,但 `zip` 同时匹配 compress 和 decompress(两者 description 都含 zip)
- **缺陷 D5(无方向感知)**:BM25 不理解"压缩成 zip"与"解压 zip"的方向差异
- decompress 排第 1 的原因:其 description 可能含更多 query 词根(如 `解压` + `archive` + `zip`)

**为何 todo2 优化未解决**:
- todo2 未优化 compress/decompress description(方向性混淆无法通过描述优化解决)
- 增加「压缩」到 compress description 也会提升 decompress 得分(两者共享 `compress` 词根)

**Reranker 预期**:
- Cross-Encoder 理解「压缩成 zip」的方向(compress)
- 预期 rerank_score:compress > 0.8,decompress < 0.2

---

## 4. 修复方案对比

### 4.1 BM25 描述优化(已验证,效果有限)

| 方案 | 解决的 case | 引入的退化 | 结论 |
|------|------------|-----------|------|
| todo2:5 个工具 description 优化 | 3 个(G1_q01 部分解决) | list_async_tasks 优化导致 G4_q08 退化(已回滚) | 边界已达 |

**边界分析**:todo2 已触及 BM25 描述优化的边界:
- 增加目标词会同时提升同族工具得分(词根共享)
- 方向性词汇无法通过描述优化区分
- 长 query 词频分散是 BM25 固有特性

### 4.2 Embedding 语义检索(待验证)

| 预期解决的 case | 预期效果 | 风险 |
|----------------|---------|------|
| G1_q00, G9_q20(web_search 召回缺失) | Embedding 理解"百度/Google 搜索"语义 | SentenceTransformer 崩溃 |
| G6_q14(schedule_task 召回缺失) | Embedding 理解"创建定时任务"语义 | 同上 |
| G7_q16, G8_q18/G19(方向性) | Embedding 可能部分解决方向性 | Bi-Encoder 对方向性仍较弱 |

**局限**:Embedding(Bi-Encoder)对方向性混淆的区分能力弱于 Cross-Encoder,因 Bi-Encoder 分别编码 query 和 doc,不捕捉交互特征。

### 4.3 Cross-Encoder Reranker(推荐)

| 预期解决的 case | 预期效果 | 依据 |
|----------------|---------|------|
| 全部 12 个 | rerank_score 精确区分 query-doc 相关性 | Cross-Encoder 拼接输入,捕捉交互特征 |

**优势**:
- Cross-Encoder 把 (query, doc) 拼接输入模型,输出相关性分数,精度高于 Bi-Encoder
- 理解方向性词汇(压缩/解压、JSON→YAML/YAML→JSON)
- 理解语义等价性(后台任务≈异步任务,Google 搜索≈网页搜索)
- 阈值过滤(rerank_score < 0.05 剔除)可解决负样本泄漏

**风险**:见 [集成方案 §8](../proposals/tool_router_reranker_integration_plan.md#8-风险与权衡)

---

## 5. 总结与建议

### 5.1 12 个 xfail 的 BM25 固有缺陷归属

| 缺陷 | 影响的 case | 数量 | BM25 可解 | Embedding 可解 | Reranker 可解 |
|------|------------|------|----------|---------------|--------------|
| D1:词袋模型,无词序 | G7_q16, G7_q17, G8_q18, G8_q19 | 4 | ❌ | △(部分) | ✅ |
| D2:字面匹配,无语义 | G1_q00, G4_q09, G9_q20 | 3 | ❌ | ✅ | ✅ |
| D3:词根混淆 | G1_q00, G9_q20 | 2 | ❌ | ✅ | ✅ |
| D4:词频分散(长 query) | G4_q07, G4_q09, G6_q14 | 3 | ❌ | △(部分) | ✅ |
| D5:无方向感知 | G6_q13, G6_q15, G7_q16, G7_q17, G8_q18, G8_q19 | 6 | ❌ | △(部分) | ✅ |

> 注:部分 case 同时受多个缺陷影响(如 G7_q16 同时受 D1+D5 影响),故总数 > 12

### 5.2 建议

1. **短期**:维持 12 个 xfail 标记,CI 监控漂移(已完成,todo3+todo4)
2. **中期**:引入 Cross-Encoder Reranker(见 [集成方案](../proposals/tool_router_reranker_integration_plan.md))
   - Phase 1:零样本验证,目标 6/12 转 PASS(50%)
   - Phase 2:微调训练,目标 12/12 转 PASS(100%)
3. **长期**:Reranker 上线后移除全部 xfail 标记,验证 25/25 PASS

### 5.3 xfail 标记维护

Reranker 上线后:
- 12 个 xfail case 预期全部转为 PASS
- 需同步更新 `tests/unit/test_tool_negative_samples.py` 的 `_XFAIL_CASES` 字典(清空或移除)
- 需同步更新 `test_xfail_cases_count_is_15` 断言(改为 0)
- 需同步更新本报告(标注"已修复")

---

## 6. 附录

### 6.1 复现命令

```bash
cd c:\Users\Administrator\agent
$env:PYTHONIOENCODING='utf-8'
$env:AGENT_HYBRID_EMBEDDING='0'
python -m pytest tests/unit/test_tool_negative_samples.py -v --tb=no
```

预期输出:`27 passed, 12 xfailed`

### 6.2 相关文件

- 负样本库:[data/tool_negative_samples.json](../../data/tool_negative_samples.json)(v1.1, 10 组 25 query)
- 回归测试:[tests/unit/test_tool_negative_samples.py](../../tests/unit/test_tool_negative_samples.py)(39 测试)
- Hybrid 检索器:[agent/tool_router_hybrid.py](../../agent/tool_router_hybrid.py)
- Reranker 集成方案:[docs/proposals/tool_router_reranker_integration_plan.md](../proposals/tool_router_reranker_integration_plan.md)

### 6.3 测试输出原文(12 个 xfail)

```
XFAIL G4_q07 - 召回缺失:list_directory 不在 top-5,list_async_tasks 泄漏 | selected=['list_scheduled_tasks', 'web_download', 'list_async_tasks', 'get_sensor_summary', 'expand_context']
XFAIL G6_q13 - 负样本泄漏:schedule_task 进入 top-5 | selected=['submit_task', 'get_task_result', 'compress', 'schedule_task', 'web_post']
XFAIL G9_q20 - 召回缺失:web_search 不在 top-5 | selected=['run_program', 'ext_discover', 'cancel_task', 'software_search', 'get_task_status']
XFAIL G8_q19 - 负样本泄漏:json_to_yaml 进入 top-5 | selected=['json_query', 'json_to_yaml', 'yaml_to_json', 'read_file', 'read_pdf']
XFAIL G1_q01 - 召回缺失:web_get 不在 top-5,fetch_news 泄漏 | selected=['web_get', 'fetch_news', 'read_pdf', 'search_memory', 'web_xpath']
XFAIL G1_q00 - 召回缺失:web_search 不在 top-5 | selected=['run_program', 'ext_discover', 'software_search', 'compress', 'json_query']
XFAIL G4_q09 - 召回缺失:list_async_tasks 不在 top-5 | selected=['submit_task', 'get_persona_info', 'get_preferences', 'get_sensor_summary', 'get_task_result']
XFAIL G8_q18 - 方向性混淆:BM25 无法区分转换方向 | selected=['json_to_yaml', 'yaml_to_json', 'ext_configure', 'data_format_detect', 'get_weather']
XFAIL G6_q14 - 召回缺失:schedule_task 不在 top-5 | selected=['list_scheduled_tasks', 'split_pdf', 'get_weather', 'json_query', 'get_pdf_info']
XFAIL G6_q15 - 负样本泄漏:schedule_task + submit_task 进入 top-5 | selected=['submit_task', 'cancel_scheduled_task', 'cancel_task', 'schedule_task', 'get_task_result']
XFAIL G7_q16 - 方向性混淆:BM25 无法区分压缩/解压方向 | selected=['decompress', 'compress', 'list_directory', 'generate_tool', 'arch_diagram']
XFAIL G7_q17 - 负样本泄漏:compress 进入 top-5 | selected=['decompress', 'compress', 'list_directory', 'get_file_info', 'json_validate']
```

---

*本报告基于 2026-07-20 实测数据生成,Reranker 上线后需更新。*
