"""微调 bge-reranker-v2-m3(LoRA)— Phase 2 P2.3

【不易】不修改预训练模型权重,仅训练 LoRA 适配器(可回滚)
【变易】支持早停、断点续训、多种优化器、自定义 PyTorch 训练循环
【简易】单脚本完成训练 + 评估 + checkpoint 保存

用法:
    python scripts/finetune_reranker.py \\
        --train data/reranker_trainset.jsonl \\
        --val data/reranker_valset.jsonl \\
        --output data/reranker_finetuned/ \\
        --epochs 5 --batch-size 16 --lr 2e-5

    # 断点续训
    python scripts/finetune_reranker.py --resume ... 

    # 全量微调(禁用 LoRA)
    python scripts/finetune_reranker.py --no-lora ...

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
#  LoRA 配置
# ════════════════════════════════════════════════════════════

def apply_lora_to_cross_encoder(model, lora_rank: int, lora_alpha: int):
    """对 CrossEncoder 内部的 AutoModelForSequenceClassification 应用 LoRA

    【不易】不修改 base model 权重,仅注入 LoRA 适配器
    【变易】target_modules 覆盖 attention 的 query/value 投影层
    """
    from peft import LoraConfig, get_peft_model

    base_model = model.model

    lora_config = LoraConfig(
        r=lora_rank,
        lora_alpha=lora_alpha,
        target_modules=["query", "value"],
        lora_dropout=0.1,
        bias="none",
        task_type="SEQ_CLS",
    )
    base_model = get_peft_model(base_model, lora_config)
    model.model = base_model
    return model


# ════════════════════════════════════════════════════════════
#  Checkpoint 保存/加载(LoRA adapter,非完整模型)
# ════════════════════════════════════════════════════════════

def save_lora_checkpoint(peft_model, ckpt_path: Path, training_state: dict) -> None:
    """保存 LoRA adapter checkpoint

    【不易】只保存 LoRA adapter 权重(~1MB),不保存 base model(~2.2GB)
    【变易】优先用 PEFT 原生 save_pretrained,失败时 fallback 到手动 state_dict
    【简易】training_state.json 独立保存,与 adapter 权重分离
    """
    ckpt_path.mkdir(parents=True, exist_ok=True)

    try:
        from peft import PeftModel
        if isinstance(peft_model, PeftModel):
            # 方法 1: PEFT 原生 save_pretrained(应输出 adapter_config.json + adapter_model.safetensors)
            peft_model.save_pretrained(str(ckpt_path))

            # 验证 adapter 文件是否真正生成
            adapter_safetensors = ckpt_path / "adapter_model.safetensors"
            adapter_bin = ckpt_path / "adapter_model.bin"

            if not adapter_safetensors.exists() and not adapter_bin.exists():
                # 方法 2: Fallback — 手动提取 LoRA state_dict
                print(f"  [checkpoint] PEFT 未生成 adapter 文件,使用 fallback", flush=True)
                from peft import get_peft_model_state_dict
                lora_state = get_peft_model_state_dict(peft_model)
                import torch
                torch.save(lora_state, ckpt_path / "adapter_model.pt")
            else:
                print(f"  [checkpoint] LoRA adapter 已保存: {ckpt_path}", flush=True)
                # 清理可能被 PEFT 误存的完整模型文件(节省磁盘)
                for full_model_name in ["model.safetensors", "pytorch_model.bin"]:
                    full_model = ckpt_path / full_model_name
                    if full_model.exists():
                        full_model.unlink()
                        print(f"  [checkpoint] 清理误存的完整模型: {full_model_name}", flush=True)
        else:
            # 非 PeftModel(全量微调):保存完整 state_dict
            print(f"  [checkpoint] 非 PeftModel,保存完整 state_dict", flush=True)
            import torch
            torch.save(peft_model.state_dict(), ckpt_path / "model_state.pt")
    except Exception as e:
        print(f"  [checkpoint] 保存失败: {e}", flush=True)

    # 保存训练状态(epoch/best_val_loss/patience_counter 等)
    state_path = ckpt_path / "training_state.json"
    try:
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(training_state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  [checkpoint] training_state 保存失败: {e}", flush=True)


def load_lora_checkpoint(peft_model, ckpt_path: Path) -> tuple[bool, "dict | None"]:
    """加载 LoRA adapter checkpoint

    【不易】优先用 PEFT 原生 load_adapter,失败时 fallback 到 set_peft_model_state_dict
    【变易】training_state.json 损坏时不影响 adapter 加载,返回 None
    【简易】返回 (success, training_state) 二元组

    Returns:
        (True, state_dict) — adapter 加载成功,training_state 可用
        (True, None)       — adapter 加载成功,但 training_state 不可用
        (False, None)      — adapter 加载失败
    """
    ckpt_path = Path(ckpt_path)
    training_state = None

    # 加载 training_state.json(损坏不影响 adapter 加载)
    state_path = ckpt_path / "training_state.json"
    if state_path.exists():
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                training_state = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"  [resume] ⚠ training_state.json 解析失败: {e}", flush=True)
            training_state = None

    # 加载 adapter 权重
    try:
        from peft import PeftModel
        if isinstance(peft_model, PeftModel):
            adapter_safetensors = ckpt_path / "adapter_model.safetensors"
            adapter_bin = ckpt_path / "adapter_model.bin"
            adapter_pt = ckpt_path / "adapter_model.pt"

            # 路径 1: PEFT 原生 load_adapter
            if adapter_safetensors.exists() or adapter_bin.exists():
                try:
                    peft_model.load_adapter(str(ckpt_path), adapter_name="default")
                    print(f"  [resume] LoRA adapter 已加载(PEFT native): {ckpt_path}", flush=True)
                    return True, training_state
                except Exception as e:
                    print(f"  [resume] ⚠ PEFT load_adapter 失败: {e}", flush=True)

            # 路径 2: Fallback — 手动加载 state_dict
            if adapter_pt.exists():
                try:
                    import torch
                    from peft import set_peft_model_state_dict
                    lora_state = torch.load(adapter_pt, map_location="cpu")
                    set_peft_model_state_dict(peft_model, lora_state)
                    print(f"  [resume] LoRA adapter 已加载(fallback): {ckpt_path}", flush=True)
                    return True, training_state
                except Exception as e:
                    print(f"  [resume] ⚠ 手动加载失败: {e}", flush=True)

            # 没有 adapter 文件
            print(f"  [resume] ⚠ 未找到 adapter 文件: {ckpt_path}", flush=True)
            return False, None
        else:
            # 非 PeftModel:加载完整 state_dict
            model_state = ckpt_path / "model_state.pt"
            if model_state.exists():
                try:
                    import torch
                    state = torch.load(model_state, map_location="cpu")
                    peft_model.load_state_dict(state, strict=False)
                    print(f"  [resume] 完整 state_dict 已加载", flush=True)
                    return True, training_state
                except Exception as e:
                    print(f"  [resume] ⚠ state_dict 加载失败: {e}", flush=True)

            return False, None
    except Exception as e:
        print(f"  [resume] ⚠ 加载异常: {e}", flush=True)
        return False, None


def find_latest_checkpoint(output_dir: Path) -> "tuple[Path, int] | None":
    """扫描 checkpoint 目录,返回最新 checkpoint 的路径和 epoch 编号

    Why: 将 main() 中的 checkpoint 扫描逻辑提取为独立函数,便于测试和复用。
         扫描 {output_dir}/checkpoints/epoch_N/ 目录,找最大 N。

    Returns:
        (ckpt_path, epoch_num) 或 None(无 checkpoint)
    """
    checkpoint_dir = output_dir / "checkpoints"
    if not checkpoint_dir.exists():
        return None

    ckpt_dirs = [d for d in checkpoint_dir.iterdir()
                 if d.is_dir() and d.name.startswith("epoch_")]
    if not ckpt_dirs:
        return None

    def _extract_epoch(d: Path) -> int:
        try:
            return int(d.name.split("_")[1])
        except (IndexError, ValueError):
            return 0

    ckpt_dirs.sort(key=_extract_epoch, reverse=True)
    latest_ckpt = ckpt_dirs[0]
    return latest_ckpt, _extract_epoch(latest_ckpt)


# ════════════════════════════════════════════════════════════
#  自定义 PyTorch 训练循环(替代 CrossEncoder.fit,兼容 PEFT)
# ════════════════════════════════════════════════════════════

def train_loop(model, train_samples: list[dict], val_samples: list[dict],
               epochs: int, batch_size: int, lr: float,
               early_stopping_patience: int,
               optimizer_name: str = "adamw",
               checkpoint_dir: "Path | None" = None,
               resume_epoch: int = 0,
               resume_state: "dict | None" = None) -> dict[str, Any]:
    """自定义 PyTorch 训练循环(支持 LoRA checkpoint 断点续训)

    Why: CrossEncoder.fit() 与 PEFT 包装后的 model 不兼容
        (FitMixinLoss.forward() 收到意外参数 'prompt'),
        必须用自定义循环直接调用 peft_model(input_ids=..., attention_mask=...)。

    【不易】BCEWithLogitsLoss + AdamW,早停监控 val_loss
    【变易】每 epoch 保存 checkpoint,resume_epoch>0 时跳过已训练 epoch
    """
    import torch
    from torch.utils.data import Dataset, DataLoader

    class RerankerDataset(Dataset):
        def __init__(self, samples, tokenizer, max_length):
            self.samples = samples
            self.tokenizer = tokenizer
            self.max_length = max_length

        def __len__(self):
            return len(self.samples)

        def __getitem__(self, idx):
            s = self.samples[idx]
            encoded = self.tokenizer(
                s["query"], s["doc"],
                truncation=True, padding="max_length",
                max_length=self.max_length, return_tensors="pt",
            )
            return {
                "input_ids": encoded["input_ids"].squeeze(0),
                "attention_mask": encoded["attention_mask"].squeeze(0),
                "label": torch.tensor(float(s["label"]), dtype=torch.float32),
            }

    # 获取 tokenizer 和 peft_model
    peft_model = model.model
    tokenizer = model.tokenizer

    train_dataset = RerankerDataset(train_samples, tokenizer, model.max_length)
    val_dataset = RerankerDataset(val_samples, tokenizer, model.max_length)
    train_dataloader = DataLoader(train_dataset, shuffle=True, batch_size=batch_size)
    val_dataloader = DataLoader(val_dataset, shuffle=False, batch_size=batch_size)

    # 选择优化器
    if optimizer_name == "adamw":
        optimizer = torch.optim.AdamW(peft_model.parameters(), lr=lr, weight_decay=0.01)
    elif optimizer_name == "sgd":
        optimizer = torch.optim.SGD(peft_model.parameters(), lr=lr, momentum=0.9)
    elif optimizer_name == "adafactor":
        from transformers import Adafactor
        optimizer = Adafactor(peft_model.parameters(), lr=lr, scale_parameter=False,
                              relative_step=False, warmup_init=False)
    else:
        optimizer = torch.optim.AdamW(peft_model.parameters(), lr=lr, weight_decay=0.01)

    loss_fct = torch.nn.BCEWithLogitsLoss()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    peft_model.to(device)

    print(f"  训练配置: epochs={epochs}, batch_size={batch_size}, lr={lr}, optimizer={optimizer_name}")
    print(f"  设备: {device}")
    print(f"  训练样本: {len(train_samples)}, 验证样本: {len(val_samples)}")
    print(f"  早停 patience: {early_stopping_patience}")
    if resume_epoch > 0:
        print(f"  [resume] 从 epoch {resume_epoch+1} 继续(跳过前 {resume_epoch} 个 epoch)")

    # 恢复训练状态
    best_val_loss = float("inf")
    patience_counter = 0
    best_epoch = 0
    if resume_state is not None:
        best_val_loss = resume_state.get("best_val_loss", float("inf"))
        patience_counter = resume_state.get("patience_counter", 0)
        best_epoch = resume_state.get("best_epoch", 0)

    train_losses = []
    val_losses = []
    val_accuracies = []

    t0 = time.time()
    for epoch in range(epochs):
        # 跳过已训练的 epoch
        if epoch < resume_epoch:
            continue

        # --- 训练 ---
        peft_model.train()
        epoch_loss = 0.0
        for batch in train_dataloader:
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            labels = batch["label"].to(device)

            optimizer.zero_grad()
            outputs = peft_model(input_ids=input_ids, attention_mask=attention_mask)
            logits = outputs.logits.squeeze(-1) if outputs.logits.size(-1) == 1 else outputs.logits[:, 0]
            loss = loss_fct(logits, labels)
            loss.backward()
            optimizer.step()
            epoch_loss += loss.item()

        avg_train_loss = epoch_loss / len(train_dataloader)
        train_losses.append(avg_train_loss)

        # --- 验证 ---
        peft_model.eval()
        val_loss = 0.0
        correct = 0
        total = 0
        with torch.no_grad():
            for batch in val_dataloader:
                input_ids = batch["input_ids"].to(device)
                attention_mask = batch["attention_mask"].to(device)
                labels = batch["label"].to(device)

                outputs = peft_model(input_ids=input_ids, attention_mask=attention_mask)
                logits = outputs.logits.squeeze(-1) if outputs.logits.size(-1) == 1 else outputs.logits[:, 0]
                loss = loss_fct(logits, labels)
                val_loss += loss.item()

                probs = torch.sigmoid(logits)
                preds = (probs > 0.5).long()
                correct += (preds == labels.long()).sum().item()
                total += len(labels)

        avg_val_loss = val_loss / len(val_dataloader)
        val_accuracy = correct / total if total > 0 else 0.0
        val_losses.append(avg_val_loss)
        val_accuracies.append(val_accuracy)

        epoch_num = epoch + 1
        print(f"  Epoch {epoch_num}/{epochs}: train_loss={avg_train_loss:.4f}, "
              f"val_loss={avg_val_loss:.4f}, val_acc={val_accuracy:.2%}", flush=True)

        # --- 保存 checkpoint ---
        if checkpoint_dir is not None:
            ckpt_path = checkpoint_dir / f"epoch_{epoch_num}"
            training_state = {
                "epoch": epoch_num,
                "best_val_loss": best_val_loss,
                "patience_counter": patience_counter,
                "best_epoch": best_epoch,
                "train_loss": avg_train_loss,
                "val_loss": avg_val_loss,
                "val_acc": val_accuracy,
            }
            save_lora_checkpoint(peft_model, ckpt_path, training_state)

        # --- 早停 ---
        if avg_val_loss < best_val_loss:
            best_val_loss = avg_val_loss
            best_epoch = epoch_num
            patience_counter = 0
        else:
            patience_counter += 1
            if patience_counter >= early_stopping_patience:
                print(f"  早停: val_loss 连续 {patience_counter} epoch 未改善", flush=True)
                break

    elapsed = time.time() - t0
    print(f"  训练耗时: {elapsed:.1f}s, 最佳 epoch: {best_epoch}")

    # 最终评估
    val_score = evaluate_model(model, val_samples)
    print(f"  验证集准确率: {val_score['accuracy']:.2%}")
    print(f"  验证集 loss: {val_score['loss']:.4f}")

    return {
        "val_accuracy": val_score["accuracy"],
        "val_loss": val_score["loss"],
        "train_time_sec": elapsed,
        "best_epoch": best_epoch,
        "train_losses": train_losses,
        "val_losses": val_losses,
        "val_accuracies": val_accuracies,
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
        loss_fct = BCEWithLogitsLoss()
        loss = loss_fct(logits.squeeze(-1) if logits.size(-1) == 1 else logits[:, 0], labels).item()
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
    parser.add_argument("--optimizer", default="adamw", choices=["adamw", "sgd", "adafactor"])
    parser.add_argument("--resume", action="store_true", help="从最后 checkpoint 恢复训练")
    parser.add_argument("--no-checkpoint", action="store_true", help="禁用 checkpoint 保存")
    parser.add_argument("--no-lora", action="store_true", help="全量微调(禁用 LoRA)")
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

    # 优先从本地缓存加载
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
    if args.no_lora:
        print(f"\n[3/5] 跳过 LoRA(全量微调)")
    else:
        print(f"\n[3/5] 应用 LoRA(rank={args.lora_rank}, alpha={args.lora_alpha})")
        model = apply_lora_to_cross_encoder(model, args.lora_rank, args.lora_alpha)
        trainable_params = sum(p.numel() for p in model.model.parameters() if p.requires_grad)
        total_params = sum(p.numel() for p in model.model.parameters())
        print(f"  可训练参数: {trainable_params:,} / {total_params:,} ({100*trainable_params/total_params:.2f}%)")

    # 4. 训练
    print(f"\n[4/5] 开始训练")

    # 断点续训:扫描 checkpoint 目录,找到最新 checkpoint
    checkpoint_dir = None if args.no_checkpoint else output_path / "checkpoints"
    resume_epoch = 0
    resume_state = None

    if args.resume and checkpoint_dir is not None:
        found = find_latest_checkpoint(output_path)
        if found is not None:
            latest_ckpt, latest_epoch = found
            print(f"  [resume] 找到 checkpoint: {latest_ckpt} (epoch {latest_epoch})", flush=True)

            # 加载 LoRA adapter 权重 + 训练状态
            success, resume_state = load_lora_checkpoint(model.model, latest_ckpt)
            if success:
                resume_epoch = latest_epoch
                print(f"  [resume] 从 epoch {resume_epoch+1} 继续", flush=True)
            else:
                print(f"  [resume] ⚠ adapter 加载失败,从头开始训练", flush=True)
                resume_state = None
        else:
            print(f"  [resume] ⚠ --resume 但 checkpoint 目录不存在或为空,从头开始", flush=True)

    if checkpoint_dir is not None:
        checkpoint_dir.mkdir(parents=True, exist_ok=True)

    result = train_loop(
        model, train_samples, val_samples,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        early_stopping_patience=args.early_stopping_patience,
        optimizer_name=args.optimizer,
        checkpoint_dir=checkpoint_dir,
        resume_epoch=resume_epoch,
        resume_state=resume_state,
    )

    # 5. 保存
    print(f"\n[5/5] 保存模型到: {output_path}")
    try:
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
        "optimizer": args.optimizer,
        "train_samples": len(train_samples),
        "val_samples": len(val_samples),
        "val_accuracy": result["val_accuracy"],
        "val_loss": result["val_loss"],
        "train_time_sec": result["train_time_sec"],
        "best_epoch": result.get("best_epoch", 0),
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


if __name__ == "__main__":
    main()
