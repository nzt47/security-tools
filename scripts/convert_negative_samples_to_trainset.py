"""把 tool_negative_samples_expanded.json 转为 Cross-Encoder 训练格式

输入:data/tool_negative_samples_expanded.json
输出:
  - data/reranker_trainset.jsonl  (训练集,80%)
  - data/reranker_valset.jsonl    (验证集,20%)

【不易】输出 (query, doc, label) 三元组,label ∈ {0, 1}
【变易】正负样本比例 1:3(每个 positive 配 3 个 negatives)
【简易】按 group_id 分层抽样,确保每组都有样本进入验证集

用法:
    python scripts/convert_negative_samples_to_trainset.py
    python scripts/convert_negative_samples_to_trainset.py --neg-ratio 5 --val-ratio 0.15
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_tool_descriptions(tool_index_path: Path) -> dict[str, str]:
    """加载 tool_name -> description 映射

    【变易】description 为空时用 name 兜底(与 SkillReranker 一致)
    【变易】附加 parameter_names 增强语义(与 BM25 索引内容一致)
    """
    with open(tool_index_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    descs: dict[str, str] = {}
    for tool in data.get("tools", []):
        name = tool.get("name", "")
        if not name:
            continue
        desc = tool.get("description", "") or name
        params = tool.get("parameter_names", []) or []
        if params:
            desc = f"{desc} 参数: {', '.join(params)}"
        descs[name] = desc
    return descs


def convert(samples_path: Path, tool_index_path: Path,
            train_output: Path, val_output: Path,
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

    # 1. 加载工具描述
    print(f"[1/4] 加载工具描述: {tool_index_path}")
    tool_descs = load_tool_descriptions(tool_index_path)
    all_tool_names = list(tool_descs.keys())
    print(f"  工具数: {len(all_tool_names)}")

    # 2. 加载增强后的负样本
    print(f"[2/4] 加载负样本: {samples_path}")
    with open(samples_path, "r", encoding="utf-8") as f:
        samples = json.load(f)
    groups = samples.get("groups", [])
    total_queries = sum(len(g["queries"]) for g in groups)
    print(f"  分组数: {len(groups)}, query 数: {total_queries}")

    # 3. 按 group 分层抽样生成 (query, doc, label) 三元组
    print(f"[3/4] 生成训练样本(正负比 1:{neg_ratio})...")
    train_samples: list[dict] = []
    val_samples: list[dict] = []

    for group in groups:
        gid = group["group_id"]
        group_samples: list[dict] = []

        for q in group["queries"]:
            query = q["query"]
            positives = q.get("expected_positive", [])
            negatives = q.get("negative", [])

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
            existing_pos = set(positives)
            random_negs = [t for t in all_tool_names
                          if t not in existing_pos and t not in existing_negs]
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

        # 80/20 划分(按 group 内随机)
        random.shuffle(group_samples)
        split_idx = int(len(group_samples) * (1 - val_ratio))
        train_samples.extend(group_samples[:split_idx])
        val_samples.extend(group_samples[split_idx:])

    # 4. 写入 JSONL
    print(f"[4/4] 写入训练集/验证集...")
    with open(train_output, "w", encoding="utf-8") as f:
        for s in train_samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    with open(val_output, "w", encoding="utf-8") as f:
        for s in val_samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    # 统计
    train_pos = sum(1 for s in train_samples if s["label"] == 1)
    train_neg = sum(1 for s in train_samples if s["label"] == 0)
    val_pos = sum(1 for s in val_samples if s["label"] == 1)
    val_neg = sum(1 for s in val_samples if s["label"] == 0)

    print(f"\n=== 转换完成 ===")
    print(f"训练集: {len(train_samples)} 样本 → {train_output}")
    print(f"  正样本: {train_pos}, 负样本: {train_neg}, 比例 1:{train_neg/max(train_pos,1):.1f}")
    print(f"验证集: {len(val_samples)} 样本 → {val_output}")
    print(f"  正样本: {val_pos}, 负样本: {val_neg}, 比例 1:{val_neg/max(val_pos,1):.1f}")
    print(f"\n样本示例(前 3 条):")
    for s in train_samples[:3]:
        print(f"  label={s['label']} | query='{s['query'][:30]}...' | tool={s['tool']}")


def main():
    parser = argparse.ArgumentParser(description="负样本 → Cross-Encoder 训练格式")
    parser.add_argument("--samples", default="data/tool_negative_samples_expanded.json")
    parser.add_argument("--tool-index", default="data/tool_index.json")
    parser.add_argument("--train-output", default="data/reranker_trainset.jsonl")
    parser.add_argument("--val-output", default="data/reranker_valset.jsonl")
    parser.add_argument("--val-ratio", type=float, default=0.2)
    parser.add_argument("--neg-ratio", type=int, default=3)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    convert(
        samples_path=_PROJECT_ROOT / args.samples,
        tool_index_path=_PROJECT_ROOT / args.tool_index,
        train_output=_PROJECT_ROOT / args.train_output,
        val_output=_PROJECT_ROOT / args.val_output,
        val_ratio=args.val_ratio,
        neg_ratio=args.neg_ratio,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()