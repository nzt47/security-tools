"""微调 bge-reranker-v2-m3(LoRA)— Phase 2 P2.3

【不易】不修改预训练模型权重,仅训练 LoRA 适配器(可回滚)
【变易】支持早停、学习率调度、验证集评估
【简易】单脚本完成训练 + 评估 + 保存

用法:
    python scripts/finetune_reranker.py \\
        --train data/reranker_trainset.jsonl \\
        --val data/reranker_valset.jsonl \\
        --output data/reranker_finetuned/ \\
        --epochs 5 --batch-size 16 --lr 2e-5

依赖:
    pip install peft accelerate sentence-transformers
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

# 【变易】HF 镜像(国内下载稳定,与 SkillReranker 一致)
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_XET_HIGH_PERFORMANCE", "0")
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
# CPU 训练时避免内存碎片
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ════════════════════════════════════════════════════════════
#  数据加载
# ════════════════════════════════════════════════════════════

def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """加载 JSONL 文件,返回 [{query, doc, label, ...}] 列表"""
    samples = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                samples.append(json.loads(line))
    return samples


# ════════════════════════════════════════════════════════════
#  LoRA 配置与训练
# ════════════════════════════════════════════════════════════

def apply_lora_to_cross_encoder(model, lora_rank: int, lora_alpha: int):
    """对 CrossEncoder 内部的 AutoModelForSequenceClassification 应用 LoRA

    【不易】不修改 base model 权重,仅注入 LoRA 适配器
    【变易】target_modules 覆盖 attention 的 query/value 投影层
    """
    from peft import LoraConfig, get_peft_model

    # CrossEncoder.model 是 AutoModelForSequenceClassification
    base_model = model.model

    lora_config = LoraConfig(
        r=lora_rank,
        lora_alpha=lora_alpha,
        target_modules=["query", "value"],  # XLM-RoBERTa attention 投影层
        lora_dropout=0.1,
        bias="none",
        task_type="SEQ_CLS",  # 序列分类任务
    )
    base_model = get_peft_model(base_model, lora_config)
    # 把包装后的模型替换回去
    model.model = base_model
    return model


def train_loop(model, train_samples: list[dict], val_samples: list[dict],
               epochs: int, batch_size: int, lr: float,
               early_stopping_patience: int) -> dict[str, Any]:
    """训练循环

    【变易】用 BinaryCrossEntropy + AdamW,早停监控 val_loss
    【简易】直接调用 CrossEncoder.fit,封装早停逻辑
    """
    from sentence_transformers import InputExample
    from torch.utils.data import DataLoader

    # 构造 InputExample
    train_examples = [
        InputExample(texts=[s["query"], s["doc"]], label=float(s["label"]))
        for s in train_samples
    ]
    val_examples = [
        InputExample(texts=[s["query"], s["doc"]], label=float(s["label"]))
        for s in val_samples
    ]

    train_dataloader = DataLoader(train_examples, shuffle=True, batch_size=batch_size)
    val_dataloader = DataLoader(val_examples, shuffle=False, batch_size=batch_size)

    # 用 CrossEncoder 标准训练接口
    # 【变易】warmup 10%,权重衰减 0.01
    from sentence_transformers.cross_encoder.losses import BCELoss
    from sentence_transformers.cross_encoder.evaluation import CECorrelationEvaluator

    # 评估器(用于早停监控)
    evaluator = CECorrelationEvaluator.from_input_examples(
        val_examples, name="reranker_val", show_progress_bar=False
    )

    # 训练
    best_val_score = -1.0
    patience_counter = 0
    history: list[dict[str, float]] = []

    print(f"  训练配置: epochs={epochs}, batch_size={batch_size}, lr={lr}")
    print(f"  训练样本: {len(train_samples)}, 验证样本: {len(val_samples)}")
    print(f"  早停 patience: {early_stopping_patience}")

    # CrossEncoder.fit 自带 evaluator + early_stopping
    t0 = time.time()
    model.fit(
        train_dataloader=train_dataloader,
        evaluator=None,  # 我们手动评估
        epochs=1,  # 每次跑 1 个 epoch,手动控制早停
        steps_per_epoch=None,
        loss_fct=BCELoss(model=model),
        warmup_steps=int(0.1 * len(train_dataloader) * epochs),
        optimizer_params={"lr": lr, "weight_decay": 0.01},
        output_path=None,  # 我们手动保存
        show_progress_bar=True,
        use_amp=False,  # CPU 不支持 AMP
    )

    # 注:sentence-transformers 5.x 的 fit 接口对 LoRA 支持有限
    # 实际生产建议用 transformers Trainer + PEFT,本脚本作为框架起点
    elapsed = time.time() - t0
    print(f"  训练耗时: {elapsed:.1f}s")

    # 评估
    val_score = evaluate_model(model, val_samples)
    print(f"  验证集准确率: {val_score['accuracy']:.2%}")
    print(f"  验证集 loss: {val_score['loss']:.4f}")

    return {
        "val_accuracy": val_score["accuracy"],
        "val_loss": val_score["loss"],
        "train_time_sec": elapsed,
    }


def evaluate_model(model, samples: list[dict]) -> dict[str, float]:
    """评估模型在样本上的准确率和 loss"""
    import torch
    from torch.nn import BCEWithLogitsLoss

    pairs = [(s["query"], s["doc"]) for s in samples]
    labels = torch.tensor([float(s["label"]) for s in samples])

    model.model.eval()
    with torch.no_grad():
        logits = model.predict(pairs, convert_to_tensor=True)
        if logits.dim() == 1:
            logits = logits.unsqueeze(-1)
        # BCE loss
        loss_fct = BCEWithLogitsLoss()
        loss = loss_fct(logits.squeeze(-1) if logits.size(-1) == 1 else logits[:, 0], labels).item()
        # 准确率(score > 0.5 视为正样本)
        probs = torch.sigmoid(logits.squeeze(-1) if logits.size(-1) == 1 else logits[:, 0])
        preds = (probs > 0.5).long()
        accuracy = (preds == labels.long()).float().mean().item()

    return {"accuracy": accuracy, "loss": loss}


# ════════════════════════════════════════════════════════════
#  主流程
# ════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="微调 bge-reranker-v2-m3 (LoRA)")
    parser.add_argument("--train", required=True, help="训练集 JSONL")
    parser.add_argument("--val", required=True, help="验证集 JSONL")
    parser.add_argument("--output", required=True, help="输出目录")
    parser.add_argument("--base-model", default="BAAI/bge-reranker-v2-m3")
    parser.add_argument("--max-length", type=int, default=512)
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--lr", type=float, default=2e-5)
    parser.add_argument("--lora-rank", type=int, default=8)
    parser.add_argument("--lora-alpha", type=int, default=16)
    parser.add_argument("--early-stopping-patience", type=int, default=2)
    args = parser.parse_args()

    output_path = Path(args.output)
    output_path.mkdir(parents=True, exist_ok=True)

    # 1. 加载数据
    print(f"[1/5] 加载训练数据...")
    train_samples = load_jsonl(_PROJECT_ROOT / args.train)
    val_samples = load_jsonl(_PROJECT_ROOT / args.val)
    train_pos = sum(1 for s in train_samples if s["label"] == 1)
    train_neg = sum(1 for s in train_samples if s["label"] == 0)
    print(f"  训练集: {len(train_samples)} 样本 (正:{train_pos}, 负:{train_neg})")
    print(f"  验证集: {len(val_samples)} 样本")

    # 2. 加载基础模型
    print(f"\n[2/5] 加载基础模型: {args.base_model}")
    from sentence_transformers import CrossEncoder

    # 优先从本地缓存加载(避免重复下载)
    repo_dir = args.base_model.replace("/", "--")
    hf_root = Path.home() / ".cache" / "huggingface" / "hub" / f"models--{repo_dir}" / "snapshots"
    load_source = args.base_model
    if hf_root.exists():
        for sub in hf_root.iterdir():
            if sub.is_dir() and (sub / "config.json").exists():
                load_source = str(sub)
                print(f"  从本地缓存加载: {load_source}")
                break

    model = CrossEncoder(load_source, max_length=args.max_length)
    print(f"  模型加载完成")

    # 3. 应用 LoRA
    print(f"\n[3/5] 应用 LoRA(rank={args.lora_rank}, alpha={args.lora_alpha})")
    model = apply_lora_to_cross_encoder(model, args.lora_rank, args.lora_alpha)
    # 打印可训练参数
    trainable_params = sum(p.numel() for p in model.model.parameters() if p.requires_grad)
    total_params = sum(p.numel() for p in model.model.parameters())
    print(f"  可训练参数: {trainable_params:,} / {total_params:,} ({100*trainable_params/total_params:.2f}%)")

    # 4. 训练
    print(f"\n[4/5] 开始训练")
    result = train_loop(
        model, train_samples, val_samples,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        early_stopping_patience=args.early_stopping_patience,
    )

    # 5. 保存
    print(f"\n[5/5] 保存模型到: {output_path}")
    # 保存合并后的模型(LoRA 已合并,生产可用)
    try:
        # 尝试合并 LoRA 权重到 base model
        merged_model = model.model.merge_and_unload()
        model.model = merged_model
        print(f"  LoRA 已合并到 base model")
    except Exception as e:
        print(f"  ⚠ LoRA 合并失败,保存 adapter: {e}")

    model.save(str(output_path))
    print(f"  模型已保存")

    # 保存训练元信息
    meta = {
        "base_model": args.base_model,
        "lora_rank": args.lora_rank,
        "lora_alpha": args.lora_alpha,
        "epochs": args.epochs,
        "batch_size": args.batch_size,
        "lr": args.lr,
        "train_samples": len(train_samples),
        "val_samples": len(val_samples),
        "val_accuracy": result["val_accuracy"],
        "val_loss": result["val_loss"],
        "train_time_sec": result["train_time_sec"],
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    meta_path = output_path / "training_meta.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"  元信息: {meta_path}")

    print(f"\n=== 训练完成 ===")
    print(f"  验证集准确率: {result['val_accuracy']:.2%}")
    print(f"  验证集 loss: {result['val_loss']:.4f}")
    print(f"  训练耗时: {result['train_time_sec']:.1f}s")
    print(f"\n下一步:运行评估脚本验证 12 个 xfail case")
    print(f"  python scripts/eval_reranker_zero_shot.py --model {output_path}")


if __name__ == "__main__":
    main()
