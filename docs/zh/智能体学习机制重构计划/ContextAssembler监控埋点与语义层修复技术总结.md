# ContextAssembler 监控埋点与语义层短路修复技术总结

- 日期：2026-08-12
- 状态：已完成并验证通过（commit `fe95ef7b` + 本次语义层修复提交）
- 前置依据：
  - docs/zh/智能体学习机制重构计划/ContextAssembler集成验证总结报告.md（性能基线）
  - docs/zh/智能体学习机制重构计划/D2D3_API架构替代方案设计.md（CEL 框架）

---

## 1. 工作范围

本轮围绕 ContextAssembler 集成做三件事：

1. **DEBUG 日志可观测性**：开启组装过程的实时拉取明细
2. **Prometheus 监控埋点**：4 个指标 + 3 个记录函数，持续观察组装行为
3. **语义层短路修复**：中文输入不再被误命中元技能拦截，使组装流程在真实 HTTP 链路可触发

## 2. 问题链与根因分析

### 2.1 现象

- 指标 `context_assembler_injected_total` 在真实 HTTP 请求下恒为 0
- 任何中文输入（含"费马小定理证明"）都被语义层命中并短路返回元技能 instruction
- 证据：`yunshu_intent_layer_total{layer="semantic"} 1.0`，命中 `proactive_suggestion` score=1.000

### 2.2 根因（三层）

| # | 层 | 根因 | 证据 |
|---|---|---|---|
| 1 | TF-IDF 分词 | `loader._tokenize` 中文按**单字**切分，命中率 `hits/len(query_tokens)` 对 `min_score=0.3` 形同虚设——元技能元数据覆盖大量常用字 | 单字命中率虚高，任何输入都 ≥0.3 |
| 2 | BM25 路 | `bm25_searcher._tokenize` 同样是单字分词，RRF 融合中 bm25 路 `bm25_rank=1` 命中元技能 | `bm25_score=1.79, rrf_normalized=1.0` |
| 3 | 拒识层 | 规则层+语义层双未命中 → "未知意图软拒识"（不调 LLM） | `yunshu_intent_layer_total{layer="reject"} 1.0` |

三层叠加：修复 1、2 后请求到达拒识层被拦截，组装仍不触发；需按用户决策关闭未知意图拒识。

## 3. 修复方案

### 3.1 中文分词 单字 → 相邻二元组（bigram）

**改动文件**：
- `agent/skills_mgmt/loader.py`：`_tokenize` 中文按相邻二元组切分（英文行为零变化）
- `agent/skills_mgmt/bm25_searcher.py`：同步 bigram（RRF 三路同尺度）

**效果对比**（真实技能库 data/skills_repo，RRF 参数 = orchestrator 实际参数）：

| 输入 | 修复前 top1 | 修复后 matches |
|---|---|---|
| 费马小定理证明 | proactive_suggestion (score=1.0) | `[]`（三路 miss → 降级 tfidf 单路亦空）|
| 帮我写一首关于春天的诗 | 元技能命中 | `[]` |
| 解析PDF文件并提取表格 | 元技能命中 | `[]`（真实库无任务技能，miss 正确）|

**三义约束**：
- 【不易】TF-IDF 命中率公式、min_score 阈值、RRF 融合、MatchResult 契约全部不变；英文分词零变化
- 【变易】中文 bigram 保留相邻字序，任务词（解析/总结/证明）具备区分度；向量/BM25 路独立分词不受影响
- 【简易】每个文件只改一个 `_tokenize` 函数，TF-IDF 单路与 RRF 融合的 tfidf 路一处生效

### 3.2 拒识层调整（用户确认）

- `config.yaml` 新增 `orchestrator.reject.enabled: false`（业务配置主源）
- `.env` 同步 `ORCHESTRATOR_REJECT_ENABLED=false`（运行时覆盖，用户配置走 .env 规范）
- **保留**输入过短拒识（`input_too_short`，process() 内独立判定，不受开关影响）

## 4. 监控埋点体系

### 4.1 Prometheus 指标（agent/monitoring/prometheus.py）

| 指标 | 类型 | 用途 | buckets |
|---|---|---|---|
| `context_assembler_injected_total` | counter | 注入成功次数（观察模式注入量）| — |
| `context_assembler_degraded_total` | counter | 组装异常降级（告警源）| — |
| `context_assembler_duration_ms` | histogram | 组装耗时（P95/P99 监控）| [1, 2.5, 5, 7.5, 10, 25, 50, 100] |
| `context_assembler_injected_tokens` | gauge | 最近一次注入 token 数（budget 占用）| — |

buckets 对齐集成验证性能分布（均值 4.10ms / P95 4.40ms / max 4.65ms）。

### 4.2 埋点路径

- `_call_llm_v2`（V2/Persona 路径）三处：empty / injected / degraded
- `_call_llm`（标准路径）旁路注入点：注入成功 → injected + duration + tokens
- 安全降级：prometheus_client 不可用时 noop 降级；埋点异常 `except Exception: pass` 不影响主链路

### 4.3 DEBUG 明细日志（agent/context/assembler.py）

`CONTEXT_ASSEMBLER_LOG_LEVEL=DEBUG` 时挂载专用 StreamHandler（绕过 root INFO 二次过滤），输出：

```
[context_assembler] 工作记忆拉取: 消息 0 条 → 0 字符
[context_assembler] 长期检索拉取: 片段 0 条
[context_assembler] 程序性记忆拉取: 技能 0 条, 工作流 None
[context_assembler] 组装完成 task='费马小定理证明' 耗时=12.4ms layer_tokens={} total=17/budget=3000 truncated=False
```

## 5. 性能对比

### 5.1 组装开销（ContextAssembler 核心）

| 场景 | 均值 | 说明 |
|---|---|---|
| 集成验证基准（n=20，真实组件）| **4.10 ms** | 有数据：工作记忆 + 长期检索 + 程序性记忆 |
| HTTP 链路实测（本次）| **12.4 ms** | 空数据：三层拉取 0/0/0 条 + 组装 |
| HTTP 链路埋点 total | **35.58 ms** | 含 orchestrator 包装/空降级路径开销 |

空数据组装（12.4ms）高于有数据基准（4.10ms）的主要原因是拉取函数（get_context / 检索 / 技能加载）在 HTTP 进程内的真实调用链更长，与组装核心逻辑无关；组装核心始终为毫秒级，对主链路延迟无感知影响。

### 5.2 链路行为对比（修复前 → 修复后）

| 环节 | 修复前 | 修复后 |
|---|---|---|
| 语义层判定 | 误命中元技能（错误短路）| 正确 miss → 放行 |
| 拒识层 | 被元技能命中绕过（不可达）| 按配置关闭 → 放行 |
| LLM 路径 | 不可达 | 可达（`layer="llm"` 埋点 1.0）|
| ContextAssembler 组装 | 不触发 | 触发（DEBUG 明细可见，duration 指标记录）|

## 6. 验证证据

- 单测：`test_bm25_skill_searcher.py`（26）+ `test_context_assembler.py`（14）+ 三层路由 e2e（11）= **51/51 通过**
- 回归：bigram 分词 4 个新用例（中文 bigram 切分 / 英文零变化 / 无关中文不命中元技能 / 任务中文仍命中任务技能）+ bm25 同步用例
- 指标回归：injected 递增 + degraded 递增（本次修复 token gauge 污染：测试内清零避免跨测试假阴性）
- HTTP 实测：`/api/chat "费马小定理证明"` → HTTP 200 → `intent_layer{layer="llm"}` + `duration_ms_count=1 sum=35.58` + DEBUG 三层明细

## 7. 后续优化建议

1. **任务技能库建设**：当前 skills_repo 为 persona 元技能（self_reflection/主动建议等），无语义层应召回的**任务技能**。建议沉淀真实任务技能（PDF 解析、知识检索等）并补充技能描述，使语义层短路重新发挥"任务技能直达"价值。
2. **拒识层回归**：`reject.enabled=false` 后未知意图全部走 LLM。若需恢复防护，建议增加**内容安全判定**（毒性/敏感词）替代"未知意图"软拒识，而非一刀切开关。
3. **向量路启用**：当前 vector fast-exit（`_st_backend=None` 跳过），RRF 实为 tfidf+bm25 两路。部署 BGE 模型后可提升语义召回质量，届时需复测 bigram 与向量相似度的融合权重。
4. **指标告警**：`degraded_total` 增长率可作为组装健康告警源；`injected_tokens` 结合 `learning.context_assembler.token_budget` 设置 budget 占用告警。
5. **测试稳定性**：`test_metrics_recorded_on_injection` 已修复跨测试污染；建议后续指标类测试统一在 fixture 中清零全局 Prometheus 状态，避免随机顺序假阴性。

## 8. 提交记录

- `fe95ef7b`：DEBUG 明细日志 + 4 指标 + 双路径注入 + app_server 加载 .env + 指标回归测试
- 本次提交：loader/bm25 bigram 分词修复 + config.yaml reject 调整 + bigram/bm25 回归测试 + 指标测试污染修复
