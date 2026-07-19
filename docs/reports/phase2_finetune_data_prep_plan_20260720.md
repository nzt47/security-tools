# Phase 2 微调训练 — 配置与数据集准备方案

> **生成日期**:2026-07-20
> **关联文档**:
>   - [集成方案草稿](../proposals/tool_router_reranker_integration_plan.md) §5 训练数据规划
>   - [TODO 任务列表](../proposals/tool_router_reranker_todos.md) Phase 2(P2.1-P2.5)
>   - [xfail Root Cause 分析](xfail_root_cause_analysis_20260720.md)
> **前置条件**:Phase 1 零样本评估 < 12/12 PASS(需微调补足)
> **目标**:通过数据增强 + 微调,让 12 个 xfail case 全部转 PASS

---

## 1. 总体策略

### 1.1 微调必要性判断

| 零样本结果 | 微调必要性 | 行动方案 |
|-----------|-----------|---------|
| ≥ 12/12 PASS | 不需要 | 跳过 Phase 2,直接 Phase 3 |
| 6-11/12 PASS | 需要 | Phase 2 微调补足剩余 case |
| < 6/12 PASS | 必需 | Phase 2 微调 + 可能需要更大模型 |

### 1.2 微调方案选型

| 方案 | 模型 | 数据量 | 预期效果 | 工作量 | 推荐 |
|------|------|--------|---------|--------|------|
| A:零样本(不训练) | bge-reranker-v2-m3 | 0 | 50-70% | 低 | Phase 1 已验证 |
| **B:LoRA 微调** | bge-reranker-v2-m3 | 200+ query | 90-95% | 中 | **推荐** |
| C:全量微调 | bge-reranker-v2-m3 | 500+ query | 95-99% | 高 | 备选 |
| D:训练新模型 | distilbert-multilingual | 1000+ query | 99%+ | 极高 | 不推荐 |

**推荐方案 B(LoRA 微调)**:
- 【不易】不破坏预训练模型权重,仅训练低秩适配器
- 【变易】训练快(30 分钟内)、内存占用小(~2GB)、易回滚
- 【简易】PEFT 库标准化流程,代码量 < 100 行

---

## 2. 数据增强策略(P2.1)

### 2.1 当前数据基础

`data/tool_negative_samples.json` v1.1:
- 10 组工具族(G1-G10)
- 25 个 query(每组 2-3 个)
- 每个 query 标注 `expected_positive` + `negative`

### 2.2 增强目标

| 维度 | 当前 | 目标 | 倍数 |
|------|------|------|------|
| 工具族数 | 10 | 10(不变) | 1x |
| 总 query 数 | 25 | 200+ | 8x+ |
| 每组 query 数 | 2-3 | 20+ | 7-10x |
| 训练样本(query×候选) | ~100 | 800+ | 8x |

### 2.3 增强策略(4 种变体)

#### 策略 1:同义改写(每 query 生成 3-5 个变体)

**示例**:
- 原:`在百度上搜索 Python 教程`
- 变体 1:`用百度查 Python 教程`
- 变体 2:`百度一下 Python 教程`
- 变体 3:`在百度搜索引擎上找 Python 教程`

**适用工具族**:G1(web_search)、G4(list_*)、G6(task_*)

#### 策略 2:句式变换(每 query 生成 2-3 个变体)

**示例**:
- 原:`把 logs 文件夹压缩成 zip`
- 变体 1:`将 logs 目录打包为 zip 格式`
- 变体 2:`压缩 logs 文件夹到 zip 文件`
- 变体 3:`请把 logs 这个目录做成 zip 压缩包`

**适用工具族**:G7(compress/decompress)、G8(json_to_yaml/yaml_to_json)

#### 策略 3:方向反转(每个方向性 case 生成反向 query)

**示例**(G7 压缩/解压):
- 正向:`把 logs 文件夹压缩成 zip` → compress
- 反向:`解压 archive.zip 到 /tmp 目录` → decompress
- 反向 2:`从 backup.tar.gz 提取文件` → decompress

**适用工具族**:G7、G8、G10(read_file/write_file)

#### 策略 4:长度变化(每 query 生成短/中/长 3 个版本)

**示例**(G6 schedule_task):
- 短:`创建定时任务`
- 中:`创建每天凌晨 3 点执行的定时任务`(原)
- 长:`请帮我创建一个每天凌晨 3 点自动执行的数据备份定时任务`

**适用工具族**:全部(覆盖 BM25 D4 词频分散缺陷)

### 2.4 增强工具(可选:LLM 辅助)

**方案 A:人工编写**(推荐,质量高)
- 工作量:200 query × 1 分钟/query = 3.3 小时
- 优点:质量可控,无噪声
- 缺点:耗时

**方案 B:LLM 生成 + 人工校验**
- 用 LLM(如 GLM/Claude)按策略 1-4 生成变体
- 人工校验,剔除噪声(宁精勿多)
- 工作量:生成 1 小时 + 校验 2 小时 = 3 小时
- 优点:速度快,覆盖面广
- 缺点:需校验

**推荐**:方案 B(LLM 生成 + 人工校验)

### 2.5 数据增强产出物

| 文件 | 内容 | 格式 |
|------|------|------|
| `data/tool_negative_samples_expanded.json` | 200+ query 增强版 | 同 v1.1 结构 |
| `data/tool_negative_samples_expanded_changelog.md` | 增强记录 | markdown |

---

## 3. 训练数据格式转换(P2.2)

### 3.1 目标格式

Cross-Encoder 微调需要 `(query, doc, label)` 三元组,其中:
- `query`:用户输入
- `doc`:工具描述(name + description + parameter_names)
- `label`:1(正相关)或 0(负相关)

### 3.2 转换规则

对于每个 query:
- `(query, expected_positive_description, 1)` — 正样本
- `(query, negative_1_description, 0)` — 负样本
- `(query, negative_2_description, 0)` — 负样本(若有多个 negative)
- `(query, random_other_tool_description, 0)` — 随机负样本(增强难度)

**正负样本比例**:1:3(每个正样本配 3 个负样本)

### 3.3 训练集/验证集划分

- **训练集**:80%(160+ query → 640+ 样本)
- **验证集**:20%(40+ query → 160+ 样本)
- **划分原则**:按 group_id 分层抽样,确保每组都有样本进入验证集

### 3.4 转换脚本设计(P2.2 产出)

**文件**:`scripts/convert_negative_samples_to_trainset.py`

```python
"""把 tool_negative_samples_expanded.json 转为 Cross-Encoder 训练格式

输入:data/tool_negative_samples_expanded.json
输出:
  - data/reranker_trainset.jsonl  (训练集,80%)
  - data/reranker_valset.jsonl    (验证集,20%)
"""
import json
import random
from pathlib import Path

def load_tool_descriptions(tool_index_path: str) -> dict[str, str]:
    """加载 tool_name -> description 映射"""
    with open(tool_index_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    descs = {}
    for tool in data.get("tools", []):
        name = tool.get("name", "")
        desc = tool.get("description", "") or name
        params = tool.get("parameter_names", []) or []
        if params:
            desc = f"{desc} 参数: {', '.join(params)}"
        descs[name] = desc
    return descs

def convert(samples_path: str, tool_index_path: str,
            train_output: str, val_output: str,
            val_ratio: float = 0.2, neg_ratio: int = 3, seed: int = 42):
    """转换主函数

    Args:
        samples_path: tool_negative_samples_expanded.json 路径
        tool_index_path: tool_index.json 路径
        train_output: 训练集输出路径(.jsonl)
        val_output: 验证集输出路径(.jsonl)
        val_ratio: 验证集比例(默认 0.2)
        neg_ratio: 每个正样本配几个负样本(默认 3)
        seed: 随机种子
    """
    random.seed(seed)

    # 加载工具描述
    tool_descs = load_tool_descriptions(tool_index_path)
    all_tool_names = list(tool_descs.keys())

    # 加载负样本
    with open(samples_path, "r", encoding="utf-8") as f:
        samples = json.load(f)

    # 按 group 分层抽样
    groups = samples.get("groups", [])
    train_samples = []
    val_samples = []

    for group in groups:
        gid = group["group_id"]
        group_samples = []
        for q in group["queries"]:
            query = q["query"]
            positives = q["expected_positive"]
            negatives = q["negative"]

            # 正样本
            for pos in positives:
                if pos in tool_descs:
                    group_samples.append({
                        "query": query,
                        "doc": tool_descs[pos],
                        "label": 1,
                        "tool": pos,
                        "group": gid,
                    })

            # 负样本(标注的 negatives)
            for neg in negatives:
                if neg in tool_descs:
                    group_samples.append({
                        "query": query,
                        "doc": tool_descs[neg],
                        "label": 0,
                        "tool": neg,
                        "group": gid,
                    })

            # 随机负样本(补足到 neg_ratio)
            existing_negs = set(negatives)
            random_negs = [t for t in all_tool_names
                          if t not in positives and t not in existing_negs]
            random.shuffle(random_negs)
            needed = max(0, neg_ratio - len(negatives))
            for neg in random_negs[:needed]:
                group_samples.append({
                    "query": query,
                    "doc": tool_descs[neg],
                    "label": 0,
                    "tool": neg,
                    "group": gid,
                })

        # 80/20 划分
        random.shuffle(group_samples)
        split_idx = int(len(group_samples) * (1 - val_ratio))
        train_samples.extend(group_samples[:split_idx])
        val_samples.extend(group_samples[split_idx:])

    # 写入文件
    with open(train_output, "w", encoding="utf-8") as f:
        for s in train_samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    with open(val_output, "w", encoding="utf-8") as f:
        for s in val_samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    print(f"训练集: {len(train_samples)} 样本 → {train_output}")
    print(f"验证集: {len(val_samples)} 样本 → {val_output}")
    print(f"正样本比例: {sum(1 for s in train_samples if s['label']==1)/len(train_samples):.2%}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", default="data/tool_negative_samples_expanded.json")
    parser.add_argument("--tool-index", default="data/tool_index.json")
    parser.add_argument("--train-output", default="data/reranker_trainset.jsonl")
    parser.add_argument("--val-output", default="data/reranker_valset.jsonl")
    parser.add_argument("--val-ratio", type=float, default=0.2)
    parser.add_argument("--neg-ratio", type=int, default=3)
    args = parser.parse_args()
    convert(args.samples, args.tool_index,
            args.train_output, args.val_output,
            args.val_ratio, args.neg_ratio)
```

### 3.5 训练数据示例(JSONL 格式)

```jsonl
{"query": "在百度上搜索 Python 教程", "doc": "网页搜索,搜索互联网信息。默认单引擎搜索 参数: query, engine", "label": 1, "tool": "web_search", "group": "G1_web_search_family"}
{"query": "在百度上搜索 Python 教程", "doc": "发送 HTTP GET 请求抓取网页内容,获取 HTML 参数: url, headers", "label": 0, "tool": "web_get", "group": "G1_web_search_family"}
{"query": "在百度上搜索 Python 教程", "doc": "取消正在等待或运行中的异步任务 参数: task_id", "label": 0, "tool": "cancel_task", "group": "G1_web_search_family"}
{"query": "在百度上搜索 Python 教程", "doc": "获取传感器摘要信息 参数: ", "label": 0, "tool": "get_sensor_summary", "group": "G1_web_search_family"}
```

---

## 4. 微调训练脚本设计(P2.3)

### 4.1 技术方案

| 维度 | 选型 | 理由 |
|------|------|------|
| 微调方式 | LoRA(Low-Rank Adaptation) | 内存小、训练快、易回滚 |
| 库 | PEFT(HuggingFace) | 标准化、社区支持 |
| 损失函数 | BinaryCrossEntropyLoss | label ∈ {0, 1} |
| 学习率 | 2e-5(AdamW) | LoRA 标准学习率 |
| batch_size | 16 | 平衡速度与显存 |
| epochs | 3-5(早停) | 避免过拟合 |
| LoRA rank | 8 | 标准值,适配 600MB 模型 |
| LoRA alpha | 16 | rank × 2(标准比例) |

### 4.2 训练脚本设计

**文件**:`scripts/finetune_reranker.py`

```python
"""微调 bge-reranker-v2-m3(LoRA)

【不易】不修改预训练模型权重,仅训练 LoRA 适配器
【变易】支持早停、学习率调度、验证集评估
【简易】单脚本完成训练 + 评估 + 保存

用法:
    python scripts/finetune_reranker.py
        --train data/reranker_trainset.jsonl
        --val data/reranker_valset.jsonl
        --output data/reranker_finetuned/
        --epochs 5 --batch-size 16 --lr 2e-5
"""
import argparse
import json
import os
import time
from pathlib import Path

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")


def load_jsonl(path: str) -> list[dict]:
    """加载 JSONL 文件"""
    samples = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                samples.append(json.loads(line))
    return samples


def main():
    parser = argparse.ArgumentParser(description="微调 bge-reranker-v2-m3(LoRA)")
    parser.add_argument("--train", required=True, help="训练集 JSONL")
    parser.add_argument("--val", required=True, help="验证集 JSONL")
    parser.add_argument("--output", required=True, help="输出目录")
    parser.add_argument("--base-model", default="BAAI/bge-reranker-v2-m3")
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--lr", type=float, default=2e-5)
    parser.add_argument("--lora-rank", type=int, default=8)
    parser.add_argument("--lora-alpha", type=int, default=16)
    parser.add_argument("--early-stopping-patience", type=int, default=2)
    args = parser.parse_args()

    # 1. 加载数据
    print(f"[1/5] 加载训练数据...")
    train_samples = load_jsonl(args.train)
    val_samples = load_jsonl(args.val)
    print(f"  训练集: {len(train_samples)} 样本")
    print(f"  验证集: {len(val_samples)} 样本")

    # 2. 加载基础模型
    print(f"[2/5] 加载基础模型: {args.base_model}")
    from sentence_transformers import CrossEncoder
    model = CrossEncoder(args.base_model, max_length=512)
    print(f"  模型加载完成")

    # 3. 应用 LoRA
    print(f"[3/5] 应用 LoRA(rank={args.lora_rank}, alpha={args.lora_alpha})")
    # PEFT 集成(伪代码,实际实现需参考 PEFT 文档)
    # from peft import LoraConfig, get_peft_model
    # lora_config = LoraConfig(r=args.lora_rank, lora_alpha=args.lora_alpha,
    #                          target_modules=["query", "value"],
    #                         task_type="FEATURE_EXTRACTION")
    # model = get_peft_model(model, lora_config)

    # 4. 训练
    print(f"[4/5] 开始训练(epochs={args.epochs}, batch_size={args.batch_size})")
    # 使用 sentence-transformers 的 train 方法
    # model.train(...)  # 详细实现见 PEFT 文档

    # 5. 评估 + 保存
    print(f"[5/5] 评估 + 保存")
    # val_accuracy = evaluate(model, val_samples)
    # print(f"  验证集准确率: {val_accuracy:.2%}")
    # model.save(args.output)
    print(f"  模型保存到: {args.output}")


if __name__ == "__main__":
    main()
```

> **注**:上述脚本为框架设计,实际 LoRA 集成需参考 [PEFT 文档](https://huggingface.co/docs/peft)。关键点:
> - `sentence-transformers.CrossEncoder` 内部用 `transformers.AutoModelForSequenceClassification`
> - 用 PEFT 包装该 model,再传回 CrossEncoder
> - 训练用 `CrossEncoder.train()` 标准接口

### 4.3 训练环境要求

| 资源 | 要求 | 当前环境 |
|------|------|---------|
| GPU | 可选(CPU 也可,但慢 5-10x) | CPU(torch 2.13.0+cpu) |
| 内存 | ≥ 4GB(LoRA 微调) | 待确认 |
| 磁盘 | ≥ 2GB(模型 + 适配器 + 训练数据) | 充足 |
| 训练时间(CPU) | 30-60 分钟(800 样本 × 5 epochs) | - |
| 训练时间(GPU) | 5-10 分钟 | - |

### 4.4 早停策略

- 监控指标:验证集 BinaryCrossEntropy loss
- patience:2(连续 2 个 epoch 无改善则停止)
- 最小改善:0.001(小于此值视为无改善)

---

## 5. 模型保存与加载(P2.5)

### 5.1 保存格式

```
data/reranker_finetuned/
├── config.json              # 模型配置
├── pytorch_model.bin        # 合并后的权重(LoRA 已合并)
├── adapter_config.json      # LoRA 配置(可选,用于回滚)
├── adapter_model.bin        # LoRA 适配器权重(可选)
├── tokenizer.json
├── tokenizer_config.json
└── special_tokens_map.json
```

### 5.2 加载方式

```python
# 方式 1:加载合并后的模型(推荐,生产用)
from sentence_transformers import CrossEncoder
model = CrossEncoder("data/reranker_finetuned", max_length=512)

# 方式 2:加载基础模型 + LoRA 适配器(开发用,可回滚)
# from peft import PeftModel
# base = CrossEncoder("BAAI/bge-reranker-v2-m3")
# model = PeftModel.from_pretrained(base, "data/reranker_finetuned")
```

### 5.3 环境变量配置

微调模型上线后,更新 `.env`:
```env
AGENT_RERANKER_MODEL=data/reranker_finetuned
```

---

## 6. 评估方法(P2.4)

### 6.1 评估指标

| 指标 | 计算 | 目标 |
|------|------|------|
| xfail 转 PASS 率 | (rerank_pass 数 / 12) × 100% | 100% |
| 验证集准确率 | 正确分类的样本数 / 总样本数 | ≥ 95% |
| 验证集 loss | BinaryCrossEntropy | < 0.1 |
| 召回率(recall@5) | expected_positive 在 top-5 的比例 | 1.0 |
| 区分率 | negative 不在 top-5 的比例 | 1.0 |

### 6.2 评估脚本

复用 `scripts/eval_reranker_zero_shot.py`,只需切换模型:

```bash
# 零样本基线
python scripts/eval_reranker_zero_shot.py --model BAAI/bge-reranker-v2-m3

# 微调后
python scripts/eval_reranker_zero_shot.py --model data/reranker_finetuned
```

### 6.3 评估报告产出

**文件**:`docs/reports/reranker_finetuned_eval_result_20260720.md`

**内容**:
- 零样本 vs 微调后对比表
- 12 个 case 的 PASS/FAIL 明细
- 改善 case 明细(零样本 FAIL → 微调后 PASS)
- 仍未解决 case(若有,需补充训练数据)

---

## 7. 实施时间线

| 阶段 | 任务 | 预估工时 | 依赖 |
|------|------|---------|------|
| P2.1 | 数据增强(LLM 生成 + 人工校验) | 1.5 天 | Phase 1 完成 |
| P2.2 | 训练数据格式转换 | 0.5 天 | P2.1 |
| P2.3 | 微调训练脚本编写 | 1 天 | P2.2 |
| P2.4 | 微调后评估 | 0.5 天 | P2.3 |
| P2.5 | 微调模型上线(.env 配置) | 0.2 天 | P2.4 |
| **合计** | | **3.7 天** | |

---

## 8. 风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| 数据增强质量差(LLM 生成噪声) | 中 | 高 | 人工校验,宁精勿多;保留 20% 验证集 |
| LoRA 微调效果不足 | 低 | 中 | 改用全量微调(方案 C) |
| CPU 训练太慢(> 2 小时) | 中 | 低 | 租用 GPU 云服务器;或减少 epochs |
| 过拟合(训练集 loss 低但验证集差) | 中 | 中 | 早停 + Dropout + 数据增强 |
| 微调后部分 case 仍失败 | 中 | 中 | 补充针对性训练数据,重训 |

---

## 9. 行动清单

### 9.1 立即可做(不依赖 Phase 1 结果)

- [ ] **P2.1a**:用 LLM 按策略 1-4 生成 200+ query 变体
- [ ] **P2.1b**:人工校验,剔除噪声,保存为 `data/tool_negative_samples_expanded.json`
- [ ] **P2.2a**:编写 `scripts/convert_negative_samples_to_trainset.py`(见 §3.4 代码)
- [ ] **P2.2b**:运行转换,生成 `data/reranker_trainset.jsonl` + `data/reranker_valset.jsonl`
- [ ] **P2.3a**:安装 PEFT 依赖(`pip install peft`)

### 9.2 依赖 Phase 1 结果

- [ ] **P2.3b**:编写 `scripts/finetune_reranker.py`(见 §4.2 框架)
- [ ] **P2.3c**:运行微调训练
- [ ] **P2.4**:运行 `eval_reranker_zero_shot.py --model data/reranker_finetuned` 评估
- [ ] **P2.5**:更新 `.env`,把 `AGENT_RERANKER_MODEL` 改为微调模型路径

---

## 10. 附录

### 10.1 新增依赖

| 包 | 版本 | 用途 | 是否需安装 |
|----|------|------|-----------|
| `peft` | ≥ 0.7.0 | LoRA 微调 | **需安装**(`pip install peft`) |
| `accelerate` | ≥ 0.25.0 | 训练加速 | **需安装**(`pip install accelerate`) |

> **注**:Phase 1 零样本评估不需要这两个包,仅 Phase 2 微调需要。

### 10.2 相关文件清单

| 文件 | 用途 | 阶段 |
|------|------|------|
| `data/tool_negative_samples_expanded.json` | 增强后的负样本库 | P2.1 |
| `scripts/convert_negative_samples_to_trainset.py` | 数据转换脚本 | P2.2 |
| `data/reranker_trainset.jsonl` | 训练集 | P2.2 |
| `data/reranker_valset.jsonl` | 验证集 | P2.2 |
| `scripts/finetune_reranker.py` | 微调训练脚本 | P2.3 |
| `data/reranker_finetuned/` | 微调后模型 | P2.3 |
| `docs/reports/reranker_finetuned_eval_result_20260720.md` | 评估报告 | P2.4 |

### 10.3 参考资料

- [PEFT 文档](https://huggingface.co/docs/peft)
- [LoRA 论文](https://arxiv.org/abs/2106.09685)
- [sentence-transformers 训练指南](https://www.sbert.net/docs/training/overview.html)
- [bge-reranker-v2-m3 模型卡](https://huggingface.co/BAAI/bge-reranker-v2-m3)

---

*本方案对齐集成方案 §5 训练数据规划,详细规划 Phase 2 微调的数据增强、格式转换、训练脚本、评估方法。Phase 1 零样本评估结果出来后,若需微调可直接按本方案执行。*
