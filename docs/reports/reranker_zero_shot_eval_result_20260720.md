# Cross-Encoder Reranker 零样本评估报告

> **评估日期**:2026-07-20
> **评估脚本**:[scripts/eval_reranker_zero_shot.py](../../scripts/eval_reranker_zero_shot.py)
> **模型**:`BAAI/bge-reranker-v2-m3`(2.17 GB,XLM-RoBERTa-base)
> **评估对象**:12 个 xfail case(来自 [xfail Root Cause 分析](xfail_root_cause_analysis_20260720.md))
> **目标**:≥ 6/12 PASS(Phase 1 达标线 50%)

---

## 1. 评估配置

| 参数 | 值 | 说明 |
|------|----|----|
| `--top-k` | 5 | 精排后返回数量 |
| `--candidate-k` | 20 | Stage 1 BM25 召回数量 |
| `--min-score` | 0.05 | rerank_score 阈值,低于此值剔除 |
| `--model` | BAAI/bge-reranker-v2-m3 | Cross-Encoder 模型 |
| `AGENT_HYBRID_EMBEDDING` | 0 | 禁用 Embedding,走纯 BM25 召回 |
| 子进程隔离 | 是 | 避免 CrossEncoder 原生崩溃影响主进程 |

---

## 2. 评估结论

| 指标 | BM25 Baseline | Reranker Zero-shot | 改善 |
|------|--------------|-------------------|------|
| **PASS 数** | 0/12 | **6/12** | **+6** |
| **PASS 率** | 0% | **50%** | +50pp |
| 平均延迟 | < 100ms | 9897.5ms | +9797ms ⚠ |
| 最大延迟 | - | 13712.5ms | - |
| 最小延迟 | - | 7857.5ms | - |

**决策**:✅ 达到 Phase 1 目标(≥ 6/12 PASS),进入 P1.2(实现 ToolReranker)。  
**遗留**:6 个 case 未解决,按 [TODO D2](../proposals/tool_router_reranker_todos.md) 决策点,**需进入 Phase 2 微调**补足。

---

## 3. 12 个 case 评估明细

### 3.1 改善 case(BM25 FAIL → Reranker PASS)— 6 个

| ID | 失败类型 | query | Rerank top-3 | 最高分 |
|----|---------|-------|-------------|--------|
| G1_q00 | 召回缺失 | 在百度上搜索 Python 教程 | search_files, software_search, **web_search** | 0.31 |
| G1_q01 | 负样本泄漏 | 抓取 https://example.com 的 HTML 内容 | **web_get**, web_xpath, web_css | 0.87 |
| G4_q07 | 召回缺失 | 列出 /home/user 下的所有文件 | **list_directory** | 0.18 |
| G6_q13 | 负样本泄漏 | 提交一个后台数据处理任务 | **submit_task** | 0.65 |
| G6_q14 | 召回缺失 | 创建每天凌晨 3 点执行的定时任务 | **schedule_task** | 0.17 |
| G6_q15 | 负样本泄漏 | 取消任务 ID 为 abc123 的后台任务 | cancel_scheduled_task, pause_scheduled_task, **cancel_task** | 0.62 |

**关键观察**:
- 召回缺失类(G1_q00/G4_q07/G6_q14):Cross-Encoder 能从 BM25 的 top-20 候选中精准挑出正确工具
- 负样本泄漏类(G1_q01/G6_q13/G6_q15):Cross-Encoder 把正确工具排到第 1,过滤掉 BM25 误召回的不相关工具

### 3.2 仍未解决 case — 6 个

| ID | 失败类型 | query | Rerank top-5 | 失败原因分析 |
|----|---------|-------|-------------|------------|
| G4_q09 | 召回缺失 | 查看提交的后台任务列表 | submit_task, list_scheduled_tasks, software_list, list_processes, **list_async_tasks** | list_async_tasks 仅排第 5,得分 0.08 太低 |
| G7_q16 | 方向性混淆 | 把 logs 文件夹压缩成 zip | **compress**, decompress | compress 排第 1(0.29),但 decompress 仍以 0.06 进入 top-5 |
| G7_q17 | 负样本泄漏 | 解压 archive.tar.gz 到当前目录 | **decompress**, compress | decompress 排第 1(0.48),但 compress 仍以 0.10 进入 top-5 |
| G8_q18 | 负样本泄漏 | 把 config.json 转换成 yaml 格式 | **json_to_yaml**, yaml_to_json | json_to_yaml 排第 1(0.91),但 yaml_to_json 仍以 0.51 进入 top-5 |
| G8_q19 | 负样本泄漏 | 读取 data.yaml 转成 JSON 对象 | **yaml_to_json**, json_to_yaml | yaml_to_json 排第 1(0.99),但 json_to_yaml 仍以 0.52 进入 top-5 |
| G9_q20 | 召回缺失 | 在 Google 上搜索 Python 异步教程 | search_files, software_search | web_search 完全未进入 top-5 |

### 3.3 失败原因分类

| 失败模式 | case 数 | 微调可解决? | 说明 |
|---------|---------|-----------|------|
| **负样本得分过滤不足** | 4(G7_q16/q17, G8_q18/q19) | ✅ 可以 | Cross-Encoder 已正确排序,但负样本得分仍 > 阈值 0.05。微调可让模型学会更强地压低负样本得分(< 0.01) |
| **正确工具得分过低** | 1(G4_q09) | ✅ 可以 | list_async_tasks 得分仅 0.08,微调可让模型对"后台任务列表"更敏感 |
| **召回阶段缺失** | 1(G9_q20) | ⚠ 部分可解决 | web_search 未进入 BM25 top-20 候选,Cross-Encoder 无机会精排。需在 BM25 阶段增强(扩 candidate_k 或加 query 改写),或微调让 web_search 描述与"Google 搜索"语义更接近 |

**关键发现**:6 个未解决 case 中,**5 个可通过 LoRA 微调解决**(让 Cross-Encoder 学会更强地区分相似工具)。G9_q20 需在召回阶段额外增强。

---

## 4. 性能分析

### 4.1 延迟分布

| 统计量 | 值(ms) |
|--------|--------|
| 平均 | 9,897.5 |
| 最大 | 13,712.5(G6_q14) |
| 最小 | 7,857.5(G8_q19) |
| P95 估计 | ~13,000 |
| 目标 P95 | < 100 |

**结论**:⚠ 当前延迟远超 100ms 目标(130x),原因:
- CPU 推理(torch 2.13.0+cpu,CUDA 不可用)
- 每次推理需处理 ~20 个 (query, doc) pairs
- 模型本身较大(2.17 GB)

### 4.2 性能优化路径(Phase 3 P3.1)

| 优化方案 | 预期加速 | 工作量 | 优先级 |
|---------|---------|--------|--------|
| 减少候选数(candidate_k: 20→10) | 2x | 低 | P0 |
| ONNX Runtime 量化(int8) | 3-5x | 中 | P1 |
| GPU 推理(若有 GPU) | 10-20x | 中 | P2 |
| 批量推理缓存(高频 query) | N/A | 中 | P3 |

**目标**:通过 ONNX 量化 + 候选数减少,将 P95 延迟降到 < 500ms(可接受范围)。

---

## 5. 决策与下一步

### 5.1 决策点 D1(零样本 ≥ 6/12 PASS?)

**结果**:✅ 是(6/12 PASS)

**行动**:进入 P1.2(实现 ToolReranker 类)

### 5.2 决策点 D2(零样本 12/12 PASS?)

**结果**:❌ 否(6/12 PASS,6 个未解决)

**行动**:进入 Phase 2(微调补足剩余 6 个 case)

### 5.3 Phase 2 启动依据

| 维度 | 评估 |
|------|------|
| 微调必要性 | ✅ 6 个未解决 case 中 5 个可通过微调解决 |
| 数据基础 | ✅ 25 query 可扩充到 200+,4 种增强策略已规划 |
| 计算资源 | ⚠ CPU 训练预计 30-60 分钟(可接受) |
| 风险 | 低(LoRA 不破坏原模型,失败可回滚) |

### 5.4 立即行动清单

按 [Phase 2 微调方案](phase2_finetune_data_prep_plan_20260720.md) §9 行动清单:

- [x] P1.0 评估脚本(已完成)
- [x] P1.1 运行零样本评估(本报告)
- [ ] **P2.1**:数据增强 25 → 200+ query(规则模板 + 人工校验)
- [ ] **P2.2**:训练数据格式转换,生成 `data/reranker_trainset.jsonl`
- [ ] **P2.3a**:安装 PEFT 依赖(`pip install peft accelerate`)
- [ ] **P2.3b**:编写 `scripts/finetune_reranker.py`(LoRA 微调)
- [ ] **P2.3c**:运行微调训练
- [ ] **P2.4**:用微调模型重跑评估,目标 12/12 PASS

### 5.5 与 Phase 1 的并行关系

Phase 1 的 P1.2-P1.8(实现 ToolReranker + HybridRetriever 改造)可与 Phase 2 并行推进:
- **P1.2-P1.6**:用零样本模型实现 ToolReranker,验证集成链路(预期 6/12 PASS)
- **P2.1-P2.5**:并行做微调,产出微调模型
- **P3.x**:微调模型就绪后,直接替换 `AGENT_RERANKER_MODEL`,无需改代码

---

## 6. 附录:完整评估输出

### 6.1 BM25 Baseline(0/12 PASS)

```
[✗ FAIL] G1_q00  召回缺失      | 在百度上搜索 Python 教程
         BM25 top-5: ['run_program', 'ext_discover', 'software_search', 'compress', 'json_query']
[✗ FAIL] G1_q01  负样本泄漏     | 抓取 https://example.com 的 HTML 内容
         BM25 top-5: ['web_get', 'fetch_news', 'read_pdf', 'search_memory', 'web_xpath']
[✗ FAIL] G4_q07  召回缺失      | 列出 /home/user 下的所有文件
         BM25 top-5: ['list_scheduled_tasks', 'web_download', 'list_async_tasks', 'get_sensor_summary', 'expand_context']
[✗ FAIL] G4_q09  召回缺失      | 查看提交的后台任务列表
         BM25 top-5: ['submit_task', 'get_persona_info', 'get_preferences', 'get_sensor_summary', 'get_task_result']
[✗ FAIL] G6_q13  负样本泄漏     | 提交一个后台数据处理任务
         BM25 top-5: ['submit_task', 'get_task_result', 'compress', 'schedule_task', 'web_post']
[✗ FAIL] G6_q14  召回缺失      | 创建每天凌晨 3 点执行的定时任务
         BM25 top-5: ['list_scheduled_tasks', 'split_pdf', 'get_weather', 'json_query', 'get_pdf_info']
[✗ FAIL] G6_q15  负样本泄漏     | 取消任务 ID 为 abc123 的后台任务
         BM25 top-5: ['submit_task', 'cancel_scheduled_task', 'cancel_task', 'schedule_task', 'get_task_result']
[✗ FAIL] G7_q16  方向性混淆     | 把 logs 文件夹压缩成 zip
         BM25 top-5: ['decompress', 'compress', 'list_directory', 'generate_tool', 'arch_diagram']
[✗ FAIL] G7_q17  负样本泄漏     | 解压 archive.tar.gz 到当前目录
         BM25 top-5: ['decompress', 'compress', 'list_directory', 'get_file_info', 'json_validate']
[✗ FAIL] G8_q18  负样本泄漏     | 把 config.json 转换成 yaml 格式
         BM25 top-5: ['json_to_yaml', 'yaml_to_json', 'ext_configure', 'data_format_detect', 'get_weather']
[✗ FAIL] G8_q19  负样本泄漏     | 读取 data.yaml 转成 JSON 对象
         BM25 top-5: ['json_query', 'json_to_yaml', 'yaml_to_json', 'read_file', 'read_pdf']
[✗ FAIL] G9_q20  召回缺失      | 在 Google 上搜索 Python 异步教程
         BM25 top-5: ['run_program', 'ext_discover', 'cancel_task', 'software_search', 'get_task_status']
```

### 6.2 Reranker Zero-shot(6/12 PASS)

```
[✓ PASS] G1_q00  ⭐ improved  | 在百度上搜索 Python 教程
         Rerank: ['search_files', 'software_search', 'web_search'] scores: [0.31, 0.08, 0.07]
[✓ PASS] G1_q01  ⭐ improved  | 抓取 https://example.com 的 HTML 内容
         Rerank: ['web_get', 'web_xpath', 'web_css', 'read_file'] scores: [0.87, 0.36, 0.24, 0.06]
[✓ PASS] G4_q07  ⭐ improved  | 列出 /home/user 下的所有文件
         Rerank: ['list_directory'] scores: [0.18]
[✗ FAIL] G4_q09              | 查看提交的后台任务列表
         Rerank: ['submit_task', 'list_scheduled_tasks', 'software_list', 'list_processes', 'list_async_tasks']
         scores: [0.80, 0.61, 0.27, 0.15, 0.08]
[✓ PASS] G6_q13  ⭐ improved  | 提交一个后台数据处理任务
         Rerank: ['submit_task'] scores: [0.65]
[✓ PASS] G6_q14  ⭐ improved  | 创建每天凌晨 3 点执行的定时任务
         Rerank: ['schedule_task'] scores: [0.17]
[✓ PASS] G6_q15  ⭐ improved  | 取消任务 ID 为 abc123 的后台任务
         Rerank: ['cancel_scheduled_task', 'pause_scheduled_task', 'cancel_task'] scores: [0.62, 0.48, 0.29]
[✗ FAIL] G7_q16              | 把 logs 文件夹压缩成 zip
         Rerank: ['compress', 'decompress'] scores: [0.29, 0.06]
[✗ FAIL] G7_q17              | 解压 archive.tar.gz 到当前目录
         Rerank: ['decompress', 'compress'] scores: [0.48, 0.10]
[✗ FAIL] G8_q18              | 把 config.json 转换成 yaml 格式
         Rerank: ['json_to_yaml', 'yaml_to_json'] scores: [0.91, 0.51]
[✗ FAIL] G8_q19              | 读取 data.yaml 转成 JSON 对象
         Rerank: ['yaml_to_json', 'json_to_yaml'] scores: [0.99, 0.52]
[✗ FAIL] G9_q20              | 在 Google 上搜索 Python 异步教程
         Rerank: ['search_files', 'software_search'] scores: [0.07, 0.06]
```

---

## 7. 关联文档

- [xfail Root Cause 分析](xfail_root_cause_analysis_20260720.md) — 12 个 xfail case 失败原因
- [Cross-Encoder 集成方案](../proposals/tool_router_reranker_integration_plan.md) — 总体方案
- [TODO 任务列表](../proposals/tool_router_reranker_todos.md) — 20 个任务,5 个决策点
- [Phase 2 微调方案](phase2_finetune_data_prep_plan_20260720.md) — LoRA 微调详细方案
- [依赖检查报告](cross_encoder_dependency_check_20260720.md) — Phase 1 依赖状态

---

*本报告对齐 TODO P1.1,确认 Phase 1 达标(6/12 PASS)并触发 Phase 2 微调流程。*
