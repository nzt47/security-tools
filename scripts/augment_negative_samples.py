"""数据增强脚本:把 25 个 query 扩充到 200+ query

【不易】不修改原 tool_negative_samples.json,产出新文件 tool_negative_samples_expanded.json
【变易】4 种增强策略:同义改写 / 句式变换 / 方向反转 / 长度变化
【简易】纯规则模板,不依赖 LLM,可控可回放

用法:
    python scripts/augment_negative_samples.py
    python scripts/augment_negative_samples.py --input data/tool_negative_samples.json \\
                                              --output data/tool_negative_samples_expanded.json
"""
from __future__ import annotations

import argparse
import copy
import json
import random
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ════════════════════════════════════════════════════════════
#  4 种增强策略的模板
# ════════════════════════════════════════════════════════════

# 策略 1:同义词替换表(关键词 → 同义列表)
_SYNONYMS: dict[str, list[str]] = {
    "搜索": ["查找", "搜", "检索", "查"],
    "列出": ["展示", "显示", "查看", "罗列"],
    "提交": ["创建", "发起", "启动", "新建"],
    "取消": ["终止", "停止", "撤销", "中止"],
    "压缩": ["打包", "归档", "压紧"],
    "解压": ["提取", "还原", "释放", "解开"],
    "读取": ["查看", "加载", "读入", "查看"],
    "写入": ["保存", "存储", "写到"],
    "转换": ["转为", "改成", "变成", "转"],
    "安装": ["装", "部署"],
    "执行": ["运行", "跑"],
    "启动": ["打开", "运行"],
    "抓取": ["获取", "下载", "拉取"],
    "查看": ["看", "查询", "列出"],
    "合并": ["整合", "拼接"],
}

# 策略 2:句式变换模板(基于动词位置的模板)
_SENTENCE_PATTERNS: dict[str, list[str]] = {
    # "在 X 上搜索 Y" 句式
    "在百度上搜索": ["用百度搜索", "百度搜索", "用百度查", "百度一下"],
    "在 Google 上搜索": ["用 Google 搜索", "Google 搜索", "用谷歌搜", "谷歌搜索"],
    # "把 X 压缩成 Y" 句式
    "把": ["将", "请把", "帮我把", "我要把"],
    # "解压 X 到 Y" 句式
    "解压": ["提取", "释放", "解开"],
    # "读取 X 转成 Y" 句式
    "读取": ["读入", "加载", "解析"],
    # "列出 X 下的所有文件" 句式
    "列出": ["展示", "显示", "查看"],
}

# 策略 4:长度变化模板
_LENGTH_VARIANTS: dict[str, dict[str, str]] = {
    # G7_q16 压缩
    "把 logs 文件夹压缩成 zip": {
        "short": "压缩 logs 文件夹",
        "medium": "把 logs 文件夹压缩成 zip",
        "long": "请帮我把 logs 这个文件夹压缩成 zip 压缩包",
    },
    # G7_q17 解压
    "解压 archive.tar.gz 到当前目录": {
        "short": "解压 archive.tar.gz",
        "medium": "解压 archive.tar.gz 到当前目录",
        "long": "请把 archive.tar.gz 这个压缩包解压到当前目录下",
    },
    # G8_q18 json_to_yaml
    "把 config.json 转换成 yaml 格式": {
        "short": "config.json 转 yaml",
        "medium": "把 config.json 转换成 yaml 格式",
        "long": "请帮我把 config.json 这个 JSON 文件转换成 yaml 格式",
    },
    # G8_q19 yaml_to_json
    "读取 data.yaml 转成 JSON 对象": {
        "short": "data.yaml 转 JSON",
        "medium": "读取 data.yaml 转成 JSON 对象",
        "long": "请读取 data.yaml 文件并把它转换成 JSON 对象格式",
    },
    # G6_q14 schedule_task
    "创建每天凌晨 3 点执行的定时任务": {
        "short": "创建定时任务",
        "medium": "创建每天凌晨 3 点执行的定时任务",
        "long": "请帮我创建一个每天凌晨 3 点自动执行的数据备份定时任务",
    },
}


# ════════════════════════════════════════════════════════════
#  增强函数
# ════════════════════════════════════════════════════════════

def _synonym_rewrite(query: str, seed: int = 0) -> list[str]:
    """策略 1:同义词改写,生成 3-5 个变体"""
    rng = random.Random(seed)
    variants = set()
    for word, syns in _SYNONYMS.items():
        if word in query:
            for syn in syns:
                if syn != word:
                    new_q = query.replace(word, syn, 1)
                    if new_q != query:
                        variants.add(new_q)
    return rng.sample(list(variants), min(4, len(variants)))


def _sentence_transform(query: str, seed: int = 0) -> list[str]:
    """策略 2:句式变换,生成 2-3 个变体"""
    rng = random.Random(seed + 1000)
    variants = set()
    for trigger, repls in _SENTENCE_PATTERNS.items():
        if trigger in query:
            for repl in repls:
                if repl != trigger:
                    new_q = query.replace(trigger, repl, 1)
                    if new_q != query:
                        variants.add(new_q)
    return rng.sample(list(variants), min(3, len(variants)))


def _length_variant(query: str) -> list[str]:
    """策略 4:长度变化,返回 short/medium/long 三个版本(去重 medium)"""
    if query in _LENGTH_VARIANTS:
        v = _LENGTH_VARIANTS[query]
        return [v["short"], v["long"]]  # medium 等于原 query,不重复
    return []


def _direction_inverse(group_id: str, queries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """策略 3:方向反转 - 对 G7/G8/G10 等方向性 case 生成反向 query 变体

    【不易】不改变 expected_positive 和 negative,只生成新的方向性 query
    """
    extra: list[dict[str, Any]] = []

    if group_id == "G7_compress_family":
        # 压缩方向额外变体
        extra.append({
            "query": "把 data 目录打包成 tar.gz",
            "expected_positive": ["compress"],
            "negative": ["decompress"],
            "rationale": "压缩方向变体,用「打包」替换「压缩成 zip」",
        })
        extra.append({
            "query": "归档 logs 目录到 backup.zip",
            "expected_positive": ["compress"],
            "negative": ["decompress"],
            "rationale": "压缩方向变体,「归档」同义于「压缩」",
        })
        # 解压方向额外变体
        extra.append({
            "query": "从 backup.tar.gz 提取所有文件",
            "expected_positive": ["decompress"],
            "negative": ["compress"],
            "rationale": "解压方向变体,用「提取」替换「解压」",
        })
        extra.append({
            "query": "解开 archive.zip 到 /tmp",
            "expected_positive": ["decompress"],
            "negative": ["compress"],
            "rationale": "解压方向变体,「解开」同义于「解压」",
        })

    elif group_id == "G8_format_convert_direction":
        # JSON→YAML 额外变体
        extra.append({
            "query": "把 settings.json 改写成 yaml",
            "expected_positive": ["json_to_yaml"],
            "negative": ["yaml_to_json"],
            "rationale": "JSON→YAML 方向变体",
        })
        extra.append({
            "query": "将 package.json 转为 yaml 配置",
            "expected_positive": ["json_to_yaml"],
            "negative": ["yaml_to_json"],
            "rationale": "JSON→YAML 方向变体",
        })
        # YAML→JSON 额外变体
        extra.append({
            "query": "把 docker-compose.yaml 转成 JSON",
            "expected_positive": ["yaml_to_json"],
            "negative": ["json_to_yaml"],
            "rationale": "YAML→JSON 方向变体",
        })
        extra.append({
            "query": "将 config.yaml 解析为 JSON",
            "expected_positive": ["yaml_to_json"],
            "negative": ["json_to_yaml"],
            "rationale": "YAML→JSON 方向变体",
        })

    elif group_id == "G10_read_write_direction":
        # 读方向额外变体
        extra.append({
            "query": "读取 settings.yaml 的内容",
            "expected_positive": ["read_file"],
            "negative": ["write_file"],
            "rationale": "读方向变体",
        })
        # 写方向额外变体
        extra.append({
            "query": "把 hello world 写入 output.txt",
            "expected_positive": ["write_file"],
            "negative": ["read_file"],
            "rationale": "写方向变体",
        })

    return extra


# ════════════════════════════════════════════════════════════
#  主增强流程
# ════════════════════════════════════════════════════════════

def augment_group(group: dict[str, Any], seed_base: int = 0) -> dict[str, Any]:
    """对一个 group 做数据增强"""
    new_group = copy.deepcopy(group)
    gid = group["group_id"]
    expanded_queries: list[dict[str, Any]] = []

    for i, q in enumerate(group["queries"]):
        # 保留原 query
        expanded_queries.append(q)

        # 策略 1:同义词改写
        for v in _synonym_rewrite(q["query"], seed=seed_base + i):
            new_q = copy.deepcopy(q)
            new_q["query"] = v
            new_q["rationale"] = f"[增强-同义改写] {q['rationale']}"
            expanded_queries.append(new_q)

        # 策略 2:句式变换
        for v in _sentence_transform(q["query"], seed=seed_base + i):
            new_q = copy.deepcopy(q)
            new_q["query"] = v
            new_q["rationale"] = f"[增强-句式变换] {q['rationale']}"
            expanded_queries.append(new_q)

        # 策略 4:长度变化
        for v in _length_variant(q["query"]):
            new_q = copy.deepcopy(q)
            new_q["query"] = v
            new_q["rationale"] = f"[增强-长度变化] {q['rationale']}"
            expanded_queries.append(new_q)

    # 策略 3:方向反转(group 级别)
    direction_extras = _direction_inverse(gid, group["queries"])
    for q in direction_extras:
        q["rationale"] = f"[增强-方向反转] {q['rationale']}"
        expanded_queries.append(q)

    new_group["queries"] = expanded_queries
    return new_group


def main():
    parser = argparse.ArgumentParser(description="数据增强:25 query → 200+ query")
    parser.add_argument("--input", default="data/tool_negative_samples.json")
    parser.add_argument("--output", default="data/tool_negative_samples_expanded.json")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    input_path = _PROJECT_ROOT / args.input
    output_path = _PROJECT_ROOT / args.output

    print(f"[1/3] 加载原始负样本: {input_path}")
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    original_count = sum(len(g["queries"]) for g in data["groups"])
    print(f"  原始 query 数: {original_count}")

    print(f"[2/3] 数据增强(4 种策略)...")
    new_data = copy.deepcopy(data)
    new_data["version"] = data.get("version", "1.1") + "-expanded"
    new_data["description"] = data.get("description", "") + " | 增强版(P2.1)"
    new_data["augmentation_strategies"] = [
        "策略1:同义词改写(每 query 3-5 变体)",
        "策略2:句式变换(每 query 2-3 变体)",
        "策略3:方向反转(G7/G8/G10 额外方向性变体)",
        "策略4:长度变化(short/medium/long 三版本)",
    ]
    new_data["groups"] = [augment_group(g, seed_base=args.seed + i * 100)
                         for i, g in enumerate(data["groups"])]

    expanded_count = sum(len(g["queries"]) for g in new_data["groups"])
    print(f"  增强 query 数: {expanded_count} (扩大 {expanded_count/original_count:.1f}x)")

    # 分组统计
    print(f"  分组明细:")
    for g in new_data["groups"]:
        print(f"    {g['group_id']}: {len(g['queries'])} query")

    print(f"[3/3] 保存到: {output_path}")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(new_data, f, ensure_ascii=False, indent=2)
    print(f"  完成,文件大小: {output_path.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
