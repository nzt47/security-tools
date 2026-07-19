# Cross-Encoder Reranker 集成方案草稿(tool_router_hybrid)

> **文档日期**:2026-07-20
> **版本**:草稿 v0.1
> **关联文档**:
>   - [负样本扩充规划](../reports/negative_samples_expansion_plan_20260719.md) §7.3 长期规划
>   - [评估报告](../reports/tool_retrieval_eval_report_20260719.md) §5.3 长期改进建议
>   - [xfail Root Cause 分析](../reports/xfail_root_cause_analysis_20260720.md)
> **状态**:**草稿**,待评审

---

## 1. 背景与目标

### 1.1 背景

tool_router_hybrid v1.1 在 BM25 单路降级模式下:
- **评估测试**:20 query recall@5 = 1.0000(达标)
- **负样本回归**:25 query 中 13 passed + 12 xfailed(通过率 52%)

12 个 xfail case 均为 BM25 固有缺陷(方向性混淆 / 召回缺失 / 负样本泄漏),无法通过工具描述优化解决(已在 todo2 验证)。

### 1.2 目标

引入 Cross-Encoder Reranker 实现两阶段检索:
- **Stage 1(召回)**:BM25 + Embedding 融合召回 top-20(高召回)
- **Stage 2(精排)**:Cross-Encoder 重排 top-5(高精度)

**验收指标**:
- 12 个 xfail case 全部转为 PASS(通过率 52% → 100%)
- 评估测试 recall@5 保持 1.0000(不退化)
- 单次 query 延迟 < 100ms(Stage 1 < 1ms + Stage 2 < 99ms)

---

## 2. 现状分析(已有基础设施)

### 2.1 已有 Cross-Encoder 实现(skills_mgmt 模块)

**关键发现**:项目已有 `agent/skills_mgmt/reranker.py`,实现了 `SkillReranker` 类,用于**技能检索**(skills_mgmt)的精排。本方案可复用其设计模式或直接复用类。

| 组件 | 位置 | 状态 | 复用价值 |
|------|------|------|---------|
| `SkillReranker` 类 | `agent/skills_mgmt/reranker.py` | ✅ 已实现(322 行) | **高**:设计模式可直接复用 |
| 模型下载脚本 | `scripts/download_reranker.py`、`scripts/download_reranker_modelscope.py` | ✅ 已有 | **高**:直接复用 |
| 模型加载测试 | `scripts/test_cross_encoder_load.py`、`scripts/test_skill_reranker.py` | ✅ 已有 | **高**:直接复用 |
| 模型对比评估 | `scripts/compare_reranker_models.py` | ✅ 已有 | **中**:参考评估方法 |

### 2.2 SkillReranker 类关键设计(可复用)

```python
# agent/skills_mgmt/reranker.py 关键接口
class SkillReranker:
    def __init__(self, *, model_name="BAAI/bge-reranker-v2-m3",
                 max_length=512, rerank_top_n=10, rerank_min_score=None):
        ...

    def rerank(self, query: str, candidates: List[Dict],
               *, top_k: int = None) -> List[Dict]:
        """对候选做 Cross-Encoder 精排,返回 rerank_score 降序列表"""
        ...

    def health(self) -> Dict:
        """健康检查"""
        ...
```

**设计亮点(本方案复用)**:
- **延迟加载**:首次 rerank 时才加载模型
- **本地缓存优先**:优先从 modelscope/HF 缓存加载,避免网络下载
- **失败降级**:模型不可用时返回原顺序(不抛异常)
- **阈值过滤**:`rerank_score < 0.05` 的候选剔除(基于 v4 评估数据)
- **线程安全**:模型加载由 `threading.Lock` 保护
- **HF 镜像**:`HF_ENDPOINT=https://hf-mirror.com`(国内下载稳定)

### 2.3 已有依赖(无需新增)

`requirements.txt` 已包含所有 Cross-Encoder 所需依赖:

| 依赖 | 版本 | 用途 |
|------|------|------|
| sentence-transformers | 5.5.1 | CrossEncoder 类 |
| transformers | 5.9.0 | 模型加载 |
| torch | 2.12.0 | 推理后端 |
| huggingface-hub | 1.17.0 | 模型下载 |
| onnxruntime | 1.26.0 | 可选推理加速 |

### 2.4 待解决的原生崩溃问题

tool_router_hybrid 的 EmbeddingIndex 因 Windows 0xC0000005 / Linux SIGILL 已禁用 SentenceTransformer(子进程探测 + 结果缓存机制)。Cross-Encoder 同样依赖 sentence-transformers,需验证:
- **bge-reranker-v2-m3 是否触发同样的原生崩溃?**
- 若触发,是否可用 ONNX Runtime 替代 torch 推理?

**验证方案**:复用 `scripts/test_cross_encoder_load.py` 在子进程中加载 bge-reranker-v2-m3,确认是否崩溃。

---

## 3. 架构设计

### 3.1 两阶段检索流程

```
用户 query
    │
    ▼
┌─────────────────────────────────┐
│  Stage 1: 召回(HybridRetriever) │
│  - BM25 倒排索引                │
│  - Embedding 语义索引(可选)    │
│  - 分数融合:alpha * bm25 +     │
│    (1-alpha) * embedding        │
│  - 输出:top-20 候选            │
└─────────────────────────────────┘
    │
    ▼ top-20 candidates
┌─────────────────────────────────┐
│  Stage 2: 精排(ToolReranker)   │
│  - Cross-Encoder: bge-reranker  │
│  - 输入:(query, description)   │
│  - 输出:rerank_score 降序      │
│  - 阈值过滤:score < 0.05 剔除  │
│  - 输出:top-5 精排结果         │
└─────────────────────────────────┘
    │
    ▼
TOOL_ALIASES 合并 + 优先级去重 + 25 上限
    │
    ▼
最终工具白名单
```

### 3.2 新增组件:ToolReranker

**位置**:`agent/tool_router_reranker.py`(新文件)

**设计原则**:
- **【不易】** 不修改 HybridRetriever 的 query 接口签名,仅新增 rerank 步骤
- **【变易】** 复用 SkillReranker 的设计模式,但针对工具检索场景适配
- **【简易】** 单一职责:`rerank(query, candidates) → sorted_candidates`

**与 SkillReranker 的差异**:

| 维度 | SkillReranker | ToolReranker(本方案) |
|------|---------------|----------------------|
| 检索对象 | 技能(skill) | 工具(tool) |
| 候选来源 | RRF 召回 | BM25+Embedding 融合召回 |
| 候选字段 | skill_id, metadata.description | tool_name, description |
| 文档内容 | description(避免长 body) | description + parameter_names |
| 阈值过滤 | rerank_min_score=0.05 | 相同(可配) |
| 降级策略 | 返回原顺序 | 返回原顺序(Stage 1 结果) |

**接口设计(草稿)**:

```python
class ToolReranker:
    """工具检索 Cross-Encoder 精排器

    复用 SkillReranker 的设计模式,针对工具检索场景适配。
    """

    def __init__(
        self,
        *,
        model_name: str = "BAAI/bge-reranker-v2-m3",
        max_length: int = 512,
        rerank_top_n: int = 20,  # Stage 1 召回数量
        rerank_min_score: float = 0.05,
    ):
        ...

    def rerank(
        self,
        query: str,
        candidates: list[tuple[str, float]],  # [(tool_name, hybrid_score)]
        *,
        tool_descriptions: dict[str, str],  # tool_name -> description
        top_k: int = 5,
    ) -> list[tuple[str, float, float]]:
        """对 Stage 1 候选做 Cross-Encoder 精排

        Returns:
            [(tool_name, hybrid_score, rerank_score)] 按 rerank_score 降序
            失败时返回原候选(保留 hybrid_score 顺序)
        """
        ...

    def health(self) -> dict:
        """健康检查"""
        ...
```

### 3.3 HybridRetriever 改造方案

**【不易】不破坏现有 query 接口**,新增 `query_with_rerank` 方法:

```python
class HybridRetriever:
    def __init__(self, ..., enable_reranker: bool = False):
        ...
        self._reranker: Optional[ToolReranker] = None
        if enable_reranker:
            self._reranker = ToolReranker()

    def query(self, text: str, top_k: int = 10) -> list[tuple[str, float]]:
        """现有接口不变(Stage 1 only)"""
        ...

    def query_with_rerank(
        self, text: str, top_k: int = 5, candidate_k: int = 20
    ) -> list[tuple[str, float, float]]:
        """两阶段检索:Stage 1 召回 + Stage 2 精排

        Returns:
            [(tool_name, hybrid_score, rerank_score)] 按 rerank_score 降序
            Reranker 不可用时退化到 Stage 1 结果
        """
        # Stage 1: 召回 top-20
        candidates = self._query_locked(text, top_k=candidate_k)

        # Stage 2: 精排 top-5
        if self._reranker is None:
            return [(t, s, 0.0) for t, s in candidates[:top_k]]

        return self._reranker.rerank(
            text, candidates,
            tool_descriptions=self._tool_descriptions,
            top_k=top_k,
        )
```

**集成点**(orchestrator/task_dispatcher):
- `hybrid_select_tools(...)` 内部调用 `query_with_rerank` 而非 `query`
- 环境变量 `AGENT_HYBRID_RERANKER=1` 控制启用(默认关闭,灰度上线)

---

## 4. 降级链设计

### 4.1 三级降级链

```
Hybrid + Reranker(两阶段)
    │ Reranker 模型不可用(崩溃/超时/未安装)
    ▼
Hybrid(Stage 1 only,BM25 + Embedding 融合)
    │ Embedding 不可用(0xC0000005 / SIGILL)
    ▼
纯 BM25(Stage 1 only,BM25 单路)
    │ BM25 索引为空
    ▼
None(调用方回退到 get_tools_for_input 关键词分类)
```

### 4.2 Reranker 降级触发条件

| 条件 | 降级动作 | 日志 |
|------|---------|------|
| 模型加载崩溃(0xC0000005) | 子进程探测,标记不可用 | WARNING + 缓存到 `.reranker_probe` |
| 模型加载超时(>30s) | 返回原顺序 | WARNING |
| 推理异常(Exception) | 返回原顺序 | WARNING |
| 环境变量 `AGENT_HYBRID_RERANKER=0` | 跳过 Reranker | INFO |

### 4.3 子进程探测(复用 Embedding 探测模式)

```python
# agent/tool_router_reranker.py
_PROBE_CACHE = os.path.join(_PROJECT_ROOT, "data", ".reranker_probe")

def _run_reranker_probe(model_name: str) -> bool:
    """子进程探测 Cross-Encoder 模型加载是否安全"""
    probe_script = (
        "from sentence_transformers import CrossEncoder; "
        f"m = CrossEncoder({model_name!r}); "
        "m.predict([('probe', 'test')]); "
        "print('PROBE_OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe_script],
        capture_output=True, text=True, timeout=60,
    )
    return result.returncode == 0 and "PROBE_OK" in result.stdout
```

---

## 5. 训练数据规划

### 5.1 当前数据(25 query,不足以训练)

`data/tool_negative_samples.json` v1.1 含 10 组 25 query,但 Cross-Encoder 训练需 200+ 条。

### 5.2 数据增强方案(LLM 生成 + 人工校验)

**Step 1**:基于现有 10 组工具族,用 LLM 生成变体 query:
- 同义改写:「在百度上搜索 Python 教程」→「用百度查 Python 教程」
- 句式变换:「把 logs 文件夹压缩成 zip」→「将 logs 目录打包为 zip」
- 方向反转:对每个方向性 case 生成反向 query

**Step 2**:人工校验,标注 `(query, positive_tool, negative_tools)` 三元组

**Step 3**:转换为训练格式:
```json
{
  "query": "在百度上搜索 Python 教程",
  "positive": "web_search",
  "negatives": ["web_get", "fetch_news"]
}
```

### 5.3 训练方案

| 方案 | 模型 | 数据量 | 预期效果 | 工作量 |
|------|------|--------|---------|--------|
| A:零样本(不训练) | bge-reranker-v2-m3 | 0 | 部分解决(70%?) | 低 |
| B:微调 | bge-reranker-v2-m3 | 200+ | 全部解决(95%+) | 中 |
| C:训练新模型 | distilbert-multilingual | 500+ | 全部解决(99%+) | 高 |

**推荐**:先方案 A(零样本)验证效果,若不足再方案 B(微调)。

### 5.4 数据转换脚本

```python
# scripts/convert_negative_samples_to_trainset.py(待编写)
def convert(samples_path: str, output_path: str):
    """将 tool_negative_samples.json 转为训练格式"""
    ...
```

---

## 6. 性能预算

### 6.1 延迟预估(80 工具)

| 阶段 | 操作 | 延迟 | 说明 |
|------|------|------|------|
| Stage 1 | BM25 + Embedding 融合 | < 1ms | 已验证 |
| Stage 2 | Cross-Encoder predict(20 候选) | 50-80ms | bge-reranker-v2-m3,CPU |
| 总计 | 两阶段 | < 100ms | 满足 50ms 阈值需优化 |

### 6.2 性能优化方案

若 Stage 2 延迟过高:
1. **减少候选数**:`rerank_top_n` 从 20 降到 10
2. **ONNX Runtime 加速**:将模型转为 ONNX 格式,用 onnxruntime 推理
3. **GPU 加速**(若有 GPU):torch.cuda
4. **批量推理**:多个 query 合并 batch(不适用于单次检索)

### 6.3 内存预估

| 组件 | 内存 | 说明 |
|------|------|------|
| bge-reranker-v2-m3 模型 | ~600MB | XLM-RoBERTa-base |
| 推理峰值 | +200MB | 20 候选 batch predict |
| 总计 | ~800MB | 可接受 |

---

## 7. 实施步骤

### Phase 1:基础集成(零样本,验证可行性)

- [ ] **Step 1.1**:创建 `agent/tool_router_reranker.py`,实现 `ToolReranker` 类
  - 复用 `SkillReranker` 的延迟加载 + 子进程探测 + 失败降级
  - 适配工具检索场景(候选字段为 tool_name + description)
- [ ] **Step 1.2**:在 `HybridRetriever` 中新增 `query_with_rerank` 方法
  - 环境变量 `AGENT_HYBRID_RERANKER=1` 控制
  - 默认关闭,灰度上线
- [ ] **Step 1.3**:子进程探测验证 bge-reranker-v2-m3 是否崩溃
  - 复用 `scripts/test_cross_encoder_load.py`
  - 若崩溃,改用 ONNX Runtime 方案
- [ ] **Step 1.4**:零样本评估 12 个 xfail case
  - 目标:至少 6 个 xfail 转 PASS(50%)
  - 若不足 6 个,进入 Phase 2 微调

### Phase 2:微调训练(若零样本不足)

- [ ] **Step 2.1**:LLM 数据增强,25 query → 200+ query
- [ ] **Step 2.2**:人工校验 + 转换为训练格式
- [ ] **Step 2.3**:微调 bge-reranker-v2-m3
- [ ] **Step 2.4**:评估 12 个 xfail case,目标全部转 PASS

### Phase 3:生产上线

- [ ] **Step 3.1**:性能优化(若延迟 > 100ms)
- [ ] **Step 3.2**:集成点改造(orchestrator/task_dispatcher)
- [ ] **Step 3.3**:CI 集成(test_tool_negative_samples.py 加入 Reranker 模式)
- [ ] **Step 3.4**:移除 12 个 xfail 标记,验证 25/25 PASS

---

## 8. 风险与权衡

### 8.1 原生崩溃风险(高)

**风险**:bge-reranker-v2-m3 可能触发与 SentenceTransformer 相同的 Windows 0xC0000005 / Linux SIGILL 崩溃。

**缓解**:
- 子进程探测 + 结果缓存(复用 Embedding 探测模式)
- 备选方案:ONNX Runtime 推理(避开 torch)
- 降级:崩溃时退化到 Stage 1 结果

### 8.2 性能退化风险(中)

**风险**:Stage 2 Cross-Encoder 推理延迟 50-80ms,可能超过 50ms 阈值。

**缓解**:
- 默认关闭,环境变量控制启用
- 性能预算放宽到 100ms(两阶段检索合理范围)
- ONNX Runtime 加速方案备选

### 8.3 零样本效果不足风险(中)

**风险**:bge-reranker-v2-m3 零样本可能无法解决全部 12 个 xfail(特别是方向性混淆 case)。

**缓解**:
- Phase 1 目标设为 50%(6 个),不要求全部解决
- Phase 2 微调作为后备

### 8.4 训练数据质量风险(低)

**风险**:LLM 生成的 query 可能有噪声,影响微调效果。

**缓解**:
- 人工校验(宁精勿多)
- 保留 20% 数据作为验证集

---

## 9. 环境变量配置

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `AGENT_HYBRID_RERANKER` | `0` | 1=启用 Reranker,0=禁用 |
| `AGENT_HYBRID_EMBEDDING` | `0` | 1=启用 Embedding,0=禁用(现有) |
| `AGENT_RERANKER_MODEL` | `BAAI/bge-reranker-v2-m3` | Reranker 模型名 |
| `AGENT_RERANKER_TOP_N` | `20` | Stage 1 召回数量 |
| `AGENT_RERANKER_MIN_SCORE` | `0.05` | rerank_score 阈值 |
| `HF_ENDPOINT` | `https://hf-mirror.com` | HF 镜像(国内) |

---

## 10. 验收标准

### 10.1 功能验收

| 指标 | 当前(BM25 only) | 目标(Hybrid + Reranker) |
|------|------------------|------------------------|
| 评估测试 recall@5 | 1.0000 | 1.0000(不退化) |
| 负样本通过率 | 52%(13/25) | 100%(25/25) |
| xfail 数量 | 12 | 0 |

### 10.2 性能验收

| 指标 | 阈值 | 验证方法 |
|------|------|---------|
| 单次 query 延迟 | < 100ms | 集成测试性能断言 |
| 模型加载时间 | < 5s | 启动日志 |
| 内存增量 | < 1GB | psutil 监控 |

### 10.3 降级验收

| 场景 | 预期行为 | 验证方法 |
|------|---------|---------|
| Reranker 崩溃 | 退化到 Stage 1 | 子进程探测 |
| Reranker 超时 | 返回原顺序 | 30s 超时断言 |
| 环境变量=0 | 跳过 Reranker | 日志确认 |

---

## 11. 附录

### 11.1 相关文件

- 已有 Reranker 实现:[agent/skills_mgmt/reranker.py](../../agent/skills_mgmt/reranker.py)
- 模型下载脚本:[scripts/download_reranker.py](../../scripts/download_reranker.py)、[scripts/download_reranker_modelscope.py](../../scripts/download_reranker_modelscope.py)
- 加载测试脚本:[scripts/test_cross_encoder_load.py](../../scripts/test_cross_encoder_load.py)
- Hybrid 检索器:[agent/tool_router_hybrid.py](../../agent/tool_router_hybrid.py)
- 负样本库:[data/tool_negative_samples.json](../../data/tool_negative_samples.json)
- xfail Root Cause:[xfail_root_cause_analysis_20260720.md](../reports/xfail_root_cause_analysis_20260720.md)

### 11.2 参考资料

- [bge-reranker-v2-m3 模型卡](https://huggingface.co/BAAI/bge-reranker-v2-m3)
- [Cross-Encoder vs Bi-Encoder](https://www.sbert.net/examples/applications/cross-encoder/README.html)
- [sentence-transformers 文档](https://www.sbert.net/)

---

*本方案为草稿 v0.1,基于现有 SkillReranker 基础设施和负样本库 v1.1 设计。待评审后进入 Phase 1 实施。*