# Cross-Encoder Reranker 集成 — TODO 任务列表

> **生成日期**:2026-07-20
> **关联文档**:
>   - [集成方案草稿](tool_router_reranker_integration_plan.md)
>   - [xfail Root Cause 分析](../reports/xfail_root_cause_analysis_20260720.md)
>   - [依赖检查报告](../reports/cross_encoder_dependency_check_20260720.md)
> **总目标**:12 个 xfail case 全部转 PASS,recall@5 保持 1.0000,单次 query < 100ms

---

## 进度总览

| Phase | 任务数 | 已完成 | 进行中 | 待启动 | 验收指标 |
|-------|--------|--------|--------|--------|---------|
| Phase 1:零样本集成 | 9 | 1 | 0 | 8 | ≥ 6/12 xfail 转 PASS |
| Phase 2:微调训练 | 5 | 0 | 0 | 5 | 12/12 xfail 转 PASS |
| Phase 3:生产上线 | 6 | 0 | 0 | 6 | 25/25 PASS + < 100ms |
| **合计** | **20** | **1** | **0** | **19** | |

---

## Phase 1:零样本集成(验证可行性)

> **目标**:实现 ToolReranker + HybridRetriever 改造,零样本验证 12 个 xfail case,目标 ≥ 6/12 转 PASS(50%)
> **前置条件**:依赖已就绪(零新增依赖,模型已缓存)
> **预估工时**:2-3 天

### ✅ P1.0 零样本评估脚本(已完成)

- **状态**:✅ 完成
- **产出物**:`scripts/eval_reranker_zero_shot.py`(513 行)
- **验收**:BM25 baseline 0/12 PASS(符合预期,与 xfail 报告一致)
- **说明**:子进程隔离加载 CrossEncoder,JSON Lines 通信协议。可直接运行 `python scripts/eval_reranker_zero_shot.py` 启动零样本评估。

### ⏳ P1.1 运行零样本评估(人工执行)

- **状态**:⏳ 待执行
- **任务**:运行 `python scripts/eval_reranker_zero_shot.py`,记录 12 个 case 的 rerank 结果
- **产出物**:`docs/reports/reranker_zero_shot_eval_result_20260720.md`(评估报告)
- **验收**:记录 rerank_pass 数量 + 改善明细 + 仍未解决明细
- **决策点**:
  - 若 ≥ 6/12 PASS → 进入 P1.2(实现 ToolReranker)
  - 若 < 6/12 PASS → 跳过 P1.2,直接进入 Phase 2(微调训练)
- **预估工时**:0.5 天(含模型加载 + 评估 + 报告)
- **依赖**:P1.0

### ⏳ P1.2 实现 ToolReranker 类

- **状态**:⏳ 待启动
- **任务**:创建 `agent/tool_router_reranker.py`,实现 `ToolReranker` 类
- **设计要点**:
  - 复用 `SkillReranker` 的延迟加载 + 子进程探测 + 失败降级模式
  - 适配工具检索场景(候选字段为 tool_name + description)
  - 读取环境变量 `AGENT_RERANKER_MODEL` / `AGENT_RERANKER_TOP_N` / `AGENT_RERANKER_MIN_SCORE`
  - 子进程隔离加载 CrossEncoder(复用 `scripts/eval_reranker_zero_shot.py` 的 `_WORKER_SCRIPT` 设计)
- **接口设计**:
  ```python
  class ToolReranker:
      def __init__(self, *, model_name=..., rerank_top_n=20, rerank_min_score=0.05): ...
      def rerank(self, query: str, candidates: list[tuple[str, float]],
                 *, tool_descriptions: dict[str, str], top_k: int = 5
      ) -> list[tuple[str, float, float]]: ...  # [(tool, hybrid_score, rerank_score)]
      def health(self) -> dict: ...
  ```
- **产出物**:
  - `agent/tool_router_reranker.py`(< 250 行)
  - `tests/unit/test_tool_router_reranker.py`(单元测试)
- **验收**:
  - 单元测试通过(模型加载 / 阈值过滤 / 失败降级 / 线程安全)
  - health() 返回正确状态
  - 与 P1.0 零样本评估脚本结果一致
- **【不易】约束**:不修改 `SkillReranker`,仅复用设计模式
- **预估工时**:1 天
- **依赖**:P1.1(若达标)

### ⏳ P1.3 HybridRetriever 改造

- **状态**:⏳ 待启动
- **任务**:在 `HybridRetriever` 中新增 `query_with_rerank` 方法
- **设计要点**:
  - **【不易】不破坏现有 `query` 接口签名**,仅新增方法
  - `query_with_rerank(text, top_k=5, candidate_k=20) → [(tool, hybrid_score, rerank_score)]`
  - 环境变量 `AGENT_HYBRID_RERANKER=1` 控制 Reranker 启用
  - Reranker 不可用时退化到 Stage 1 结果(返回 hybrid_score,rerank_score=0.0)
- **改造点**:
  - `HybridRetriever.__init__`:新增 `enable_reranker` 参数,初始化 `self._reranker`
  - `HybridRetriever.query_with_rerank`:两阶段检索逻辑
  - `HybridRetriever._tool_descriptions`:缓存 tool_name → description 映射
- **产出物**:
  - `agent/tool_router_hybrid.py`(新增 ~50 行)
  - `tests/unit/test_hybrid_retriever_rerank.py`(集成测试)
- **验收**:
  - `query_with_rerank` 在 Reranker 禁用时等价于 `query` + 填充 rerank_score=0.0
  - `query_with_rerank` 在 Reranker 启用时返回精排结果
  - 现有 `query` 接口行为不变(回归测试通过)
- **预估工时**:0.5 天
- **依赖**:P1.2

### ⏳ P1.4 环境变量配置(.env)

- **状态**:⏳ 待启动
- **任务**:在 `.env` 新增 4 个 Tool-Reranker 专属环境变量
- **配置项**:
  ```env
  # === Cross-Encoder Reranker (Phase 1 灰度) ===
  AGENT_HYBRID_RERANKER=0
  AGENT_RERANKER_MODEL=BAAI/bge-reranker-v2-m3
  AGENT_RERANKER_TOP_N=20
  AGENT_RERANKER_MIN_SCORE=0.05
  ```
- **【不易】约束**:所有配置修改必须落到 `.env`,代码通过 `os.environ.get` 读取
- **产出物**:`.env` 更新 + `.env.example` 同步更新(若有)
- **验收**:
  - `AGENT_HYBRID_RERANKER=0` 时 Reranker 禁用,行为与现状一致
  - `AGENT_HYBRID_RERANKER=1` 时 Reranker 启用
- **预估工时**:0.1 天
- **依赖**:P1.2

### ⏳ P1.5 子进程探测验证

- **状态**:⏳ 待启动
- **任务**:验证 `bge-reranker-v2-m3` 在主进程中加载是否触发原生崩溃
- **执行步骤**:
  1. 运行 `python scripts/test_cross_encoder_load.py`(已有脚本)
  2. 若崩溃 → 验证 ToolReranker 的子进程隔离是否生效
  3. 若子进程也崩溃 → 评估 ONNX Runtime 替代方案
- **产出物**:`docs/reports/reranker_native_crash_probe_20260720.md`(探测报告)
- **验收**:
  - 探测报告明确结论:崩溃 / 不崩溃
  - 若崩溃,ToolReranker 的子进程隔离能正常降级
- **预估工时**:0.5 天
- **依赖**:P1.2

### ⏳ P1.6 集成测试(端到端)

- **状态**:⏳ 待启动
- **任务**:端到端验证 `query_with_rerank` 在 12 个 xfail case 上的效果
- **测试场景**:
  1. `AGENT_HYBRID_RERANKER=0`:Reranker 禁用,12 个 xfail 仍失败
  2. `AGENT_HYBRID_RERANKER=1`:Reranker 启用,≥ 6 个 xfail 转 PASS
  3. 模拟 Reranker 崩溃:降级到 Stage 1 结果
- **产出物**:
  - `tests/integration/test_hybrid_reranker_e2e.py`(端到端测试)
  - 评估报告更新
- **验收**:
  - 场景 1:12 xfail(与现状一致)
  - 场景 2:≥ 6 PASS(达 Phase 1 目标)
  - 场景 3:降级正确,无异常
- **预估工时**:0.5 天
- **依赖**:P1.3, P1.4, P1.5

### ⏳ P1.7 性能基准测试

- **状态**:⏳ 待启动
- **任务**:测量 `query_with_rerank` 的延迟和内存占用
- **指标**:
  - 单次 query 延迟(P50/P95/P99)
  - 模型加载时间
  - 内存增量
- **产出物**:`docs/reports/reranker_perf_benchmark_20260720.md`
- **验收**:
  - 单次 query P95 < 100ms
  - 内存增量 < 1GB
- **预估工时**:0.3 天
- **依赖**:P1.6

### ⏳ P1.8 Phase 1 验收报告

- **状态**:⏳ 待启动
- **任务**:汇总 Phase 1 成果,决策是否进入 Phase 2
- **产出物**:`docs/reports/reranker_phase1_acceptance_20260720.md`
- **内容**:
  - 12 个 xfail case 的 PASS/FAIL 明细
  - 性能基准数据
  - 降级链验证结果
  - 决策:进入 Phase 2(微调)或 直接 Phase 3(上线)
- **决策点**:
  - 12/12 PASS + 性能达标 → 跳过 Phase 2,直接 Phase 3
  - 6-11/12 PASS + 性能达标 → 进入 Phase 2(微调补足剩余)
  - < 6/12 PASS → Phase 1 失败,重新评估方案
- **预估工时**:0.2 天
- **依赖**:P1.6, P1.7

---

## Phase 2:微调训练(若零样本不足)

> **目标**:通过数据增强 + 微调,让 12 个 xfail case 全部转 PASS
> **前置条件**:Phase 1 零样本评估 < 12/12 PASS
> **预估工时**:5-7 天

### ⏳ P2.1 数据增强(LLM 生成)

- **状态**:⏳ 待启动
- **任务**:用 LLM 把 25 query 扩充到 200+ query
- **增强策略**:
  - 同义改写:「在百度上搜索 Python 教程」→「用百度查 Python 教程」
  - 句式变换:「把 logs 文件夹压缩成 zip」→「将 logs 目录打包为 zip」
  - 方向反转:对每个方向性 case 生成反向 query
  - 长度变化:短 query / 中 query / 长 query 各占 1/3
- **产出物**:`data/tool_negative_samples_expanded.json`(200+ query)
- **验收**:
  - 每组工具族至少 20 个变体 query
  - 覆盖 12 个 xfail case 的失败类型(召回缺失 / 负样本泄漏 / 方向性混淆)
  - 人工校验通过(宁精勿多)
- **预估工时**:1.5 天
- **依赖**:Phase 1 完成

### ⏳ P2.2 训练数据格式转换

- **状态**:⏳ 待启动
- **任务**:把扩充后的负样本转为 Cross-Encoder 训练格式
- **格式**:
  ```json
  {"query": "...", "positive": "tool_name", "negatives": ["tool1", "tool2"]}
  ```
  → 转为 `(query, doc, label)` 三元组(label ∈ {0, 1})
- **产出物**:
  - `scripts/convert_negative_samples_to_trainset.py`(转换脚本)
  - `data/reranker_trainset.jsonl`(训练集)
  - `data/reranker_valset.jsonl`(验证集,20% 留出)
- **验收**:
  - 训练集 ≥ 800 条(200 query × 4 候选平均)
  - 正负样本比例 1:3(每个 positive 配 3 个 negatives)
- **预估工时**:0.5 天
- **依赖**:P2.1

### ⏳ P2.3 微调训练脚本

- **状态**:⏳ 待启动
- **任务**:编写 `bge-reranker-v2-m3` 微调脚本
- **技术方案**:
  - 使用 `sentence_transformers.cross_encoder.CrossEncoder.train`
  - 损失函数:`BinaryCrossEntropyLoss`(label ∈ {0, 1})
  - 学习率:`2e-5`(AdamW)
  - batch_size:`16`
  - epochs:`3-5`(早停)
- **产出物**:
  - `scripts/finetune_reranker.py`(训练脚本)
  - `data/reranker_finetuned/`(微调后模型)
- **验收**:
  - 训练损失收敛
  - 验证集准确率 ≥ 95%
- **预估工时**:1 天
- **依赖**:P2.2

### ⏳ P2.4 微调后评估

- **状态**:⏳ 待启动
- **任务**:用微调后的模型重跑 12 个 xfail case
- **执行**:
  ```bash
  python scripts/eval_reranker_zero_shot.py --model data/reranker_finetuned
  ```
- **产出物**:`docs/reports/reranker_finetuned_eval_result_20260720.md`
- **验收**:12/12 PASS(全部 xfail 转 PASS)
- **决策点**:
  - 12/12 PASS → 进入 Phase 3
  - < 12/12 PASS → 补充训练数据,重跑 P2.3
- **预估工时**:0.5 天
- **依赖**:P2.3

### ⏳ P2.5 微调模型上线

- **状态**:⏳ 待启动
- **任务**:把微调模型设为默认,更新环境变量
- **配置变更**:
  ```env
  AGENT_RERANKER_MODEL=data/reranker_finetuned
  ```
- **产出物**:`.env` 更新
- **验收**:HybridRetriever 加载微调模型成功
- **预估工时**:0.2 天
- **依赖**:P2.4

---

## Phase 3:生产上线

> **目标**:Reranker 灰度上线,移除 12 个 xfail 标记,25/25 PASS
> **前置条件**:Phase 1 或 Phase 2 达到 12/12 PASS
> **预估工时**:2-3 天

### ⏳ P3.1 性能优化(若延迟超标)

- **状态**:⏳ 待启动
- **任务**:若 P1.7 性能基准 P95 > 100ms,执行优化
- **优化方案**(按优先级):
  1. 减少候选数:`AGENT_RERANKER_TOP_N` 从 20 降到 10
  2. ONNX Runtime 加速:模型转 ONNX,用 `onnxruntime` 推理
  3. GPU 加速(若有 GPU):`device='cuda'`
  4. 批量推理缓存(对高频 query)
- **产出物**:
  - `scripts/convert_reranker_to_onnx.py`(ONNX 转换脚本)
  - `data/reranker_onnx/`(ONNX 模型)
- **验收**:P95 < 100ms
- **预估工时**:1 天(若需要)
- **依赖**:Phase 1 或 Phase 2 完成

### ⏳ P3.2 集成点改造

- **状态**:⏳ 待启动
- **任务**:把 `query_with_rerank` 接入 `hybrid_select_tools`
- **改造点**:
  - `agent/tool_router_hybrid.py` 的 `hybrid_select_tools` 函数
  - 当 `AGENT_HYBRID_RERANKER=1` 时调用 `query_with_rerank`,否则调用 `query`
  - 后续处理(TOOL_ALIASES 合并 + 优先级去重 + 25 上限)不变
- **产出物**:`agent/tool_router_hybrid.py` 更新
- **验收**:
  - `hybrid_select_tools` 在 Reranker 启用时返回精排结果
  - 在 Reranker 禁用时行为与现状一致(回归测试通过)
- **预估工时**:0.3 天
- **依赖**:P3.1(若需要)

### ⏳ P3.3 CI 集成

- **状态**:⏳ 待启动
- **任务**:在 CI 中加入 Reranker 模式的负样本测试
- **改造点**:
  - `.github/workflows/tool-retrieval-ci.yml` 新增 job:`test-negative-samples-reranker`
  - 环境变量:`AGENT_HYBRID_RERANKER=1`
  - 测试:`pytest tests/unit/test_tool_negative_samples.py -v`
- **产出物**:`.github/workflows/tool-retrieval-ci.yml` 更新
- **验收**:CI 在 Reranker 模式下 25/25 PASS
- **预估工时**:0.3 天
- **依赖**:P3.2

### ⏳ P3.4 移除 xfail 标记

- **状态**:⏳ 待启动
- **任务**:从 `tests/unit/test_tool_negative_samples.py` 移除 12 个 xfail 标记
- **改造点**:
  - `_XFAIL_CASES` 字典清空(或移除)
  - `test_xfail_cases_count_is_15` 断言改为 `0`(或移除)
  - 更新测试 docstring(Reranker 已修复)
- **产出物**:`tests/unit/test_tool_negative_samples.py` 更新
- **验收**:
  - 25/25 PASS(无 xfail)
  - xfail 标记移除后 CI 通过
- **预估工时**:0.2 天
- **依赖**:P3.3

### ⏳ P3.5 文档更新

- **状态**:⏳ 待启动
- **任务**:更新所有相关文档,标注 Reranker 已上线
- **更新清单**:
  - `docs/reports/xfail_root_cause_analysis_20260720.md`:标注"已修复"
  - `docs/reports/tool_retrieval_eval_report_20260719.md`:更新 §7 优化记录
  - `docs/proposals/tool_router_reranker_integration_plan.md`:状态改为"已实施"
  - `docs/releases/`:新建 Reranker 上线 release notes
- **产出物**:多份文档更新 + release notes
- **验收**:所有文档状态一致,无遗留"待实施"标注
- **预估工时**:0.3 天
- **依赖**:P3.4

### ⏳ P3.6 灰度上线 + 监控

- **状态**:⏳ 待启动
- **任务**:灰度启用 Reranker,监控生产指标
- **灰度策略**:
  - 第 1 周:`AGENT_HYBRID_RERANKER=1` 仅在测试环境
  - 第 2 周:生产环境 10% 流量
  - 第 3 周:全量上线
- **监控指标**:
  - 单次 query 延迟(P95 < 100ms)
  - Reranker 降级触发率(< 1%)
  - 工具召回准确率(用户反馈)
- **产出物**:`docs/ops/reranker_rollout_plan.md`(灰度计划)
- **验收**:全量上线后 1 周无回滚
- **预估工时**:0.5 天(计划)+ 3 周(灰度)
- **依赖**:P3.5

---

## 关键决策点汇总

| 决策点 | 触发条件 | 选项 A | 选项 B |
|--------|---------|--------|--------|
| D1: P1.1 后 | 零样本 ≥ 6/12 PASS? | 是 → P1.2(实现 ToolReranker) | 否 → Phase 2(直接微调) |
| D2: P1.8 后 | 零样本 12/12 PASS? | 是 → 跳过 Phase 2,直接 Phase 3 | 否 → Phase 2(微调补足) |
| D3: P2.4 后 | 微调 12/12 PASS? | 是 → Phase 3 | 否 → 补数据重训(P2.1) |
| D4: P3.1 后 | 性能 P95 < 100ms? | 是 → P3.2 | 否 → 优化(ONNX/GPU) |
| D5: P3.6 后 | 灰度 1 周无回滚? | 是 → 全量上线 | 否 → 回滚 + 排查 |

---

## 风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| bge-reranker-v2-m3 触发原生崩溃 | 中 | 高 | 子进程隔离 + ONNX 备选(P1.5 验证) |
| 零样本效果不足(< 6/12) | 中 | 中 | Phase 2 微调补足 |
| 微调后仍有 case 失败 | 低 | 中 | 补充训练数据,增加 epochs |
| 性能延迟 > 100ms | 中 | 中 | 减少候选数 + ONNX 加速(P3.1) |
| 生产环境回滚 | 低 | 高 | 灰度策略 + 环境变量快速禁用 |

---

## 附录:文件清单

| 类别 | 文件 | Phase |
|------|------|-------|
| 评估脚本(已就绪) | `scripts/eval_reranker_zero_shot.py` | P1.0 ✅ |
| ToolReranker 实现 | `agent/tool_router_reranker.py` | P1.2 |
| HybridRetriever 改造 | `agent/tool_router_hybrid.py` | P1.3 |
| 环境变量配置 | `.env` | P1.4 |
| 单元测试 | `tests/unit/test_tool_router_reranker.py` | P1.2 |
| 集成测试 | `tests/integration/test_hybrid_reranker_e2e.py` | P1.6 |
| 端到端测试 | `tests/unit/test_tool_negative_samples.py`(更新) | P3.4 |
| CI 配置 | `.github/workflows/tool-retrieval-ci.yml`(更新) | P3.3 |
| 数据增强 | `data/tool_negative_samples_expanded.json` | P2.1 |
| 训练脚本 | `scripts/finetune_reranker.py` | P2.3 |
| ONNX 转换 | `scripts/convert_reranker_to_onnx.py` | P3.1 |

---

*本 TODO 列表对齐集成方案 §7 实施步骤,每个任务含产出物、验收标准、依赖关系、预估工时。Phase 1 完成后决策是否进入 Phase 2。*
