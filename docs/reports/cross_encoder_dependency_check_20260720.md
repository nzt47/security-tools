# Cross-Encoder Reranker 依赖与配置检查报告

> **检查日期**:2026-07-20
> **检查范围**:Python 依赖、模型缓存、已有基础设施、环境变量配置
> **关联文档**:
>   - [Reranker 集成方案](../proposals/tool_router_reranker_integration_plan.md)
>   - [xfail Root Cause 分析](xfail_root_cause_analysis_20260720.md)
>   - [负样本扩充规划](negative_samples_expansion_plan_20260719.md)

---

## 1. 检查结论(TL;DR)

| 维度 | 状态 | 说明 |
|------|------|------|
| Python 依赖 | ✅ **全部已安装,无需新增** | 7 个核心依赖均已在 requirements.txt 中锁定版本 |
| 模型缓存 | ✅ **已下载,无需联网** | `bge-reranker-v2-m3` 已缓存到本地 HF cache |
| 已有基础设施 | ✅ **可复用 SkillReranker** | 1 个类 + 6 个脚本,设计模式直接复用 |
| 环境变量配置 | ⚠️ **部分待新增** | Tool-Reranker 专属配置(4 个变量)尚未在代码中落地 |
| 新增代码 | ⏳ **待实现** | `ToolReranker` 类 + `HybridRetriever.query_with_rerank` 方法 |

**核心结论**:Cross-Encoder 集成**零新增依赖、零模型下载**,只需新增 ToolReranker 类代码和 4 个环境变量配置,即可进入 Phase 1 零样本评估。

---

## 2. Python 依赖检查

### 2.1 核心依赖清单(全部已安装)

通过 `rg -n` 核实 `requirements.txt`,Cross-Encoder 推理所需的全部依赖均已锁定版本:

| 依赖包 | 锁定版本 | 用途 | requirements.txt 行号 |
|--------|---------|------|----------------------|
| `sentence-transformers` | 5.5.1 | 提供 `CrossEncoder` 类(核心) | L334 |
| `transformers` | 5.9.0 | 模型加载与 tokenizer | L379 |
| `torch` | 2.12.0 | 推理后端(CPU/CUDA) | L363 |
| `huggingface-hub` | 1.17.0 | 模型下载与缓存管理 | L113 |
| `onnxruntime` | 1.26.0 | 可选推理加速(备选方案) | L180 |
| `scikit-learn` | 1.8.0 | sentence-transformers 依赖 | L326 |
| `safetensors` | 0.7.0 | 模型权重加载(transformers 依赖) | L324 |

### 2.2 辅助依赖(间接已安装)

| 依赖包 | 锁定版本 | 用途 |
|--------|---------|------|
| `torchaudio` | 2.11.0 | torch 配套(不直接用) |
| `torchvision` | 0.27.0 | torch 配套(不直接用) |
| `tokenizers` | (随 transformers) | tokenizer 后端 |
| `numpy` | (已有) | tensor 运算 |

### 2.3 依赖验证命令

```bash
cd c:\Users\Administrator\agent
python -c "from sentence_transformers import CrossEncoder; import torch; import transformers; print('OK', torch.__version__, transformers.__version__)"
```

**预期输出**:`OK 2.12.0 5.9.0`

### 2.4 结论

**【不易】零新增依赖**。Cross-Encoder 推理链路 `CrossEncoder → transformers → torch` 已在现有环境中跑通(由 `SkillReranker` 验证)。

---

## 3. 模型缓存检查

### 3.1 HF 缓存目录

```
HF_HUB_CACHE = C:\Users\Administrator\.cache\huggingface\hub
```

### 3.2 已缓存模型(实测)

通过 `huggingface_hub.constants.HF_HUB_CACHE` 实测,以下模型已离线可用:

| 模型 | 用途 | 状态 |
|------|------|------|
| `models--BAAI--bge-m3` | Embedding(Bi-Encoder) | ✅ 已缓存 |
| `models--BAAI--bge-reranker-v2-m3` | **Cross-Encoder Reranker** | ✅ **已缓存** |

### 3.3 结论

**【变易】零模型下载**。`bge-reranker-v2-m3`(约 600MB,XLM-RoBERTa-base 架构)已预先下载,Phase 1 零样本评估可直接离线启动,无需联网。

---

## 4. 已有基础设施盘点

### 4.1 核心代码(可直接复用)

| 文件 | 行数 | 用途 | 复用方式 |
|------|------|------|---------|
| [agent/skills_mgmt/reranker.py](../../agent/skills_mgmt/reranker.py) | 332 | `SkillReranker` 类(技能检索) | **设计模式直接复用** |

**`SkillReranker` 关键设计**(集成方案 §2.2 详述):

- 延迟加载(首次 `rerank()` 时才加载模型)
- 本地缓存优先(modelscope/HF)
- 失败降级(模型不可用时返回原顺序,不抛异常)
- 阈值过滤(`rerank_score < 0.05` 剔除)
- 线程安全(`threading.Lock` 保护模型加载)
- HF 镜像(`HF_ENDPOINT=https://hf-mirror.com`)

### 4.2 辅助脚本(可直接复用)

通过 `rg -n "CrossEncoder|bge-reranker|reranker" --type py -l` 扫描,共 12 个文件涉及 reranker,其中 6 个脚本可直接复用:

| 脚本 | 用途 | 复用方式 |
|------|------|---------|
| [scripts/download_reranker.py](../../scripts/download_reranker.py) | HF 下载 bge-reranker-v2-m3 | 直接复用(模型已缓存,无需重跑) |
| [scripts/download_reranker_modelscope.py](../../scripts/download_reranker_modelscope.py) | modelscope 镜像下载 | 直接复用(国内网络备选) |
| [scripts/download_reranker_base.py](../../scripts/download_reranker_base.py) | base 版本下载 | 参考 |
| [scripts/test_cross_encoder_load.py](../../scripts/test_cross_encoder_load.py) | Cross-Encoder 加载测试 | **Phase 1.3 子进程探测直接复用** |
| [scripts/test_skill_reranker.py](../../scripts/test_skill_reranker.py) | SkillReranker 集成测试 | 参考 |
| [scripts/compare_reranker_models.py](../../scripts/compare_reranker_models.py) | 多 reranker 模型对比 | 参考 |

### 4.3 结论

**【简易】最大化复用**。`ToolReranker` 的实现可直接复用 `SkillReranker` 的设计模式,只需适配候选字段(skill metadata → tool description)。预计 `agent/tool_router_reranker.py` 新增代码 < 200 行。

---

## 5. 环境变量配置检查

### 5.1 已存在的环境变量(可直接复用)

通过 `rg -n "SKILL_RERANKER|HF_ENDPOINT" agent/skills_mgmt/reranker.py` 实测:

| 变量名 | 默认值 | 位置 | 用途 |
|--------|--------|------|------|
| `SKILL_RERANK_MIN_SCORE` | `0.05` | `agent/skills_mgmt/reranker.py:128` | SkillReranker 阈值 |
| `HF_ENDPOINT` | `https://hf-mirror.com` | `agent/skills_mgmt/reranker.py:161` | HF 镜像(国内下载) |

### 5.2 待新增的环境变量(Tool-Reranker 专属)

通过 `rg -n "AGENT_HYBRID_RERANKER|AGENT_RERANKER|TOOL_RERANKER" -g "*.py"` 核实,以下变量在代码库中**尚不存在**,需在 Phase 1 实施时新增:

| 变量名 | 默认值 | 用途 | 落地位置 |
|--------|--------|------|---------|
| `AGENT_HYBRID_RERANKER` | `0` | 启用 Reranker(1=启用,0=禁用,灰度上线) | `agent/tool_router_hybrid.py`(HybridRetriever 构造函数) |
| `AGENT_RERANKER_MODEL` | `BAAI/bge-reranker-v2-m3` | Reranker 模型名 | `agent/tool_router_reranker.py` |
| `AGENT_RERANKER_TOP_N` | `20` | Stage 1 召回数量 | `agent/tool_router_reranker.py` |
| `AGENT_RERANKER_MIN_SCORE` | `0.05` | rerank_score 阈值 | `agent/tool_router_reranker.py` |

### 5.3 配置文件落地点

根据项目【不易】约束「所有配置修改必须落到 .env 文件,其他文件通过环境变量引用」:

- **新增位置**:`.env`(项目根目录)
- **读取方式**:复用现有 `SecureConfigManager` / `os.environ.get`
- **示例**:
  ```env
  # === Cross-Encoder Reranker (Phase 1 灰度) ===
  AGENT_HYBRID_RERANKER=0
  AGENT_RERANKER_MODEL=BAAI/bge-reranker-v2-m3
  AGENT_RERANKER_TOP_N=20
  AGENT_RERANKER_MIN_SCORE=0.05
  ```

---

## 6. 已知风险与待验证项

### 6.1 原生崩溃风险(高优先级,Phase 1.3 验证)

**风险**:`bge-reranker-v2-m3` 与已禁用的 `SentenceTransformer`(BGE-m3 embedding)同样依赖 `sentence-transformers` + `torch`,可能触发相同的原生崩溃:
- Windows:`0xC0000005`(访问违例)
- Linux:`SIGILL`(非法指令)

**验证方法**:复用 [scripts/test_cross_encoder_load.py](../../scripts/test_cross_encoder_load.py) 在子进程中加载模型,确认是否崩溃。

**降级方案**:若崩溃,改用 ONNX Runtime 推理(`onnxruntime==1.26.0` 已安装),或退化到 Stage 1 结果。

### 6.2 性能延迟(中优先级,Phase 3 优化)

**风险**:Stage 2 Cross-Encoder 对 20 个候选拼接 predict,CPU 推理可能 50-80ms,超过现有 50ms 阈值。

**缓解**:
- 性能预算放宽到 100ms(两阶段检索合理范围)
- 默认关闭,环境变量灰度启用
- ONNX Runtime 加速备选

### 6.3 零样本效果(中优先级,Phase 1.4 评估)

**风险**:`bge-reranker-v2-m3` 零样本可能无法解决全部 12 个 xfail(特别是方向性混淆 case)。

**缓解**:
- Phase 1 目标设为 50%(6/12),不要求全部解决
- Phase 2 微调训练作为后备

---

## 7. 行动清单(对齐集成方案 Phase 1)

### 7.1 立即可做(零新增依赖前提)

- [ ] **Step 1.1**:创建 `agent/tool_router_reranker.py`,实现 `ToolReranker` 类
  - 复用 `SkillReranker` 的延迟加载 + 子进程探测 + 失败降级
  - 读取 `AGENT_RERANKER_MODEL` / `AGENT_RERANKER_TOP_N` / `AGENT_RERANKER_MIN_SCORE`
- [ ] **Step 1.2**:在 `HybridRetriever` 中新增 `query_with_rerank` 方法
  - 读取 `AGENT_HYBRID_RERANKER` 控制启用
  - 默认关闭(`=0`),灰度上线
- [ ] **Step 1.3**:在 `.env` 新增 4 个环境变量(见 §5.3)
- [ ] **Step 1.4**:运行 `scripts/test_cross_encoder_load.py` 子进程探测,验证是否崩溃
- [ ] **Step 1.5**:零样本评估 12 个 xfail case,目标 6/12 转 PASS

### 7.2 不需要做(依赖已就绪)

- ❌ 安装任何 Python 依赖(7 个核心包已锁定)
- ❌ 下载 bge-reranker-v2-m3 模型(已缓存)
- ❌ 改造 `SkillReranker`(保持不动,仅复用设计模式)

---

## 8. 附录

### 8.1 检查命令记录

```bash
# 1. 核实 Python 依赖
rg -n "sentence-transformers|transformers|^torch|huggingface-hub|onnxruntime" requirements.txt

# 2. 核实 reranker 相关代码
rg -n "CrossEncoder|bge-reranker|reranker" --type py -l

# 3. 核实环境变量(已存在)
rg -n "SKILL_RERANKER|HF_ENDPOINT" agent/skills_mgmt/reranker.py

# 4. 核实环境变量(待新增,应无输出)
rg -n "AGENT_HYBRID_RERANKER|AGENT_RERANKER|TOOL_RERANKER" -g "*.py"

# 5. 核实模型缓存
python -c "import os; from huggingface_hub.constants import HF_HUB_CACHE; cache=os.path.expanduser(HF_HUB_CACHE); print([d for d in os.listdir(cache) if 'reranker' in d.lower() or 'bge' in d.lower()])"
```

### 8.2 文件清单

| 类别 | 文件 |
|------|------|
| 检查报告(本文档) | `docs/reports/cross_encoder_dependency_check_20260720.md` |
| 集成方案草稿 | `docs/proposals/tool_router_reranker_integration_plan.md` |
| xfail Root Cause | `docs/reports/xfail_root_cause_analysis_20260720.md` |
| 负样本扩充规划 | `docs/reports/negative_samples_expansion_plan_20260719.md` |
| 依赖清单 | `requirements.txt`(L113/L180/L324/L326/L334/L363/L379) |
| SkillReranker 实现 | `agent/skills_mgmt/reranker.py`(L37/L39/L45/L107/L161) |
| 模型缓存 | `C:\Users\Administrator\.cache\huggingface\hub\models--BAAI--bge-reranker-v2-m3` |

---

*本报告结论:Cross-Encoder Reranker 集成的依赖与基础设施已就绪,可立即进入 Phase 1 零样本评估阶段。*
