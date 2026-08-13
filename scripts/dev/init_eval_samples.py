"""初始化真实任务样本池（data/evals/）— 任务 EVO-T2

用途:
    幂等地初始化/重建 data/evals/ 下的基础样本集（search/code/chat）。
    - 若对应类别文件已存在（非空），则跳过（不覆盖人工扩充的样本）；
    - 完成后用 EvalSamplePool 验证加载并打印统计。

用法:
    python scripts/dev/init_eval_samples.py
    python scripts/dev/init_eval_samples.py --force   # 覆盖重建全部类别

扩充样本:
    编辑 data/evals/<category>/<file>.json 追加条目即可（见 data/evals/README.md）。
    新增类别只需新建 data/evals/<类别>/ 目录放 JSON，无需改本脚本。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 项目根目录 = 本文件上两级（scripts/dev → agent 根）
_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

# 内置模板（与手工维护的 data/evals/ 内容一致；仅用于初始化/重建）
_TEMPLATES: dict[str, list[dict]] = {
    "search": [
        {
            "id": "search-001",
            "category": "search",
            "task": "查询「云枢 Digital Life」的定义与核心定位",
            "expected_output": {"type": "contains", "values": ["云枢"]},
            "created_at": "2026-08-12T00:00:00",
            "metadata": {"input": {"query": "云枢 Digital Life 定义"},
                         "note": "搜索类样本：校验输出包含关键词"},
        },
        {
            "id": "search-002",
            "category": "search",
            "task": "查询今天上海天气的实时气温",
            "expected_output": {"type": "contains", "values": ["上海"]},
            "created_at": "2026-08-12T00:00:00",
            "metadata": {"input": {"query": "上海今天天气 气温"},
                         "note": "开放结果类样本：仅校验主题相关性"},
        },
        {
            "id": "search-003",
            "category": "search",
            "task": "查询 Python 3.12 最新稳定版本号",
            "expected_output": {"type": "json", "key": "found", "value": True},
            "created_at": "2026-08-12T00:00:00",
            "metadata": {"input": {"query": "Python 3.12 latest version",
                                   "require_json": True},
                         "note": "结构化输出样本：校验 result.found == true"},
        },
        {
            "id": "search-004",
            "category": "search",
            "task": "查询「三义原则」中不易的含义",
            "expected_output": {"type": "contains", "values": ["不易"]},
            "created_at": "2026-08-12T00:00:00",
            "metadata": {"input": {"query": "三义原则 不易 不变"},
                         "note": "知识检索类样本"},
        },
        {
            "id": "search-005",
            "category": "search",
            "task": "查询用户本地记忆中存在的一个主题名称",
            "expected_output": {"type": "contains", "values": ["主题"]},
            "created_at": "2026-08-12T00:00:00",
            "metadata": {"input": {"query": "记忆 主题 列表", "source": "local_memory"},
                         "note": "本地检索类样本"},
        },
    ],
    "code": [
        {
            "id": "code-001",
            "category": "code",
            "task": "实现一个函数：输入整数 n，返回 1..n 的和",
            "expected_output": {"type": "validator",
                                "expression": "isinstance(result, (int, float)) and result == 15"},
            "created_at": "2026-08-12T00:00:00",
            "metadata": {"input": {"n": 5}, "note": "校验 sum(1..5)==15"},
        },
        {
            "id": "code-002",
            "category": "code",
            "task": "实现一个函数：判断字符串是否为回文",
            "expected_output": {"type": "validator",
                                "expression": "isinstance(result, bool) and result is True"},
            "created_at": "2026-08-12T00:00:00",
            "metadata": {"input": {"text": "level"}, "note": "校验回文判定"},
        },
        {
            "id": "code-003",
            "category": "code",
            "task": "实现一个函数：计算斐波那契数列第 n 项（n=10 → 55）",
            "expected_output": {"type": "validator",
                                "expression": "isinstance(result, (int, float)) and result == 55"},
            "created_at": "2026-08-12T00:00:00",
            "metadata": {"input": {"n": 10}, "note": "校验斐波那契第 10 项==55"},
        },
        {
            "id": "code-004",
            "category": "code",
            "task": "实现一个函数：返回输入列表去重后的元素个数",
            "expected_output": {"type": "validator",
                                "expression": "isinstance(result, int) and result == 3"},
            "created_at": "2026-08-12T00:00:00",
            "metadata": {"input": {"items": [1, 2, 2, 3, 3, 3]},
                         "note": "校验去重后个数==3"},
        },
        {
            "id": "code-005",
            "category": "code",
            "task": "实现一个函数：将输入字符串转为大写",
            "expected_output": {"type": "validator",
                                "expression": "isinstance(result, str) and result == 'HELLO'"},
            "created_at": "2026-08-12T00:00:00",
            "metadata": {"input": {"text": "hello"}, "note": "校验大写转换"},
        },
    ],
    "chat": [
        {
            "id": "chat-001",
            "category": "chat",
            "task": "用户说：你好，请问你能做什么？请给出友好简洁的自我介绍",
            "created_at": "2026-08-12T00:00:00",
            "metadata": {"input": {"user_message": "你好，请问你能做什么？"},
                         "note": "开放域对话样本：走自一致性评分"},
        },
        {
            "id": "chat-002",
            "category": "chat",
            "task": "用户情绪低落时，请给出共情式回应",
            "created_at": "2026-08-12T00:00:00",
            "metadata": {"input": {"user_message": "我今天心情很不好，工作又出错了"},
                         "note": "开放域对话样本：走自一致性+反馈信号"},
        },
        {
            "id": "chat-003",
            "category": "chat",
            "task": "用户问：今天适合出门吗？请基于天气给出建议",
            "created_at": "2026-08-12T00:00:00",
            "metadata": {"input": {"user_message": "今天适合出门吗？"},
                         "note": "开放域建议类样本"},
        },
        {
            "id": "chat-004",
            "category": "chat",
            "task": "用户说：帮我规划明天的日程，我上午开会下午写报告",
            "created_at": "2026-08-12T00:00:00",
            "metadata": {"input": {"user_message": "帮我规划明天的日程，我上午开会下午写报告"},
                         "note": "开放域任务规划类样本"},
        },
        {
            "id": "chat-005",
            "category": "chat",
            "task": "用户表达感谢，请给出礼貌简洁的回应",
            "created_at": "2026-08-12T00:00:00",
            "metadata": {"input": {"user_message": "太感谢你了！"},
                         "note": "开放域礼貌回应样本"},
        },
    ],
}

_DEFAULT_DIR = _ROOT / "data" / "evals"

# 各类别默认文件名（与 data/evals/README.md 文档一致）
_CATEGORY_FILES = {
    "search": "qa_pairs.json",
    "code": "code_tasks.json",
    "chat": "dialog_flows.json",
}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="初始化真实任务样本池 data/evals/")
    p.add_argument("--force", action="store_true",
                   help="覆盖重建全部类别（默认仅填充缺失类别）")
    p.add_argument("--dir", default=None, help="样本池根目录（默认 data/evals）")
    return p.parse_args()


def _write_samples(path: Path, samples: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(samples, f, ensure_ascii=False, indent=2)


def main() -> int:
    args = _parse_args()
    base = Path(args.dir) if args.dir else _DEFAULT_DIR
    base.mkdir(parents=True, exist_ok=True)

    print(f"[init_eval_samples] 样本池根目录: {base}")
    created: list[str] = []
    skipped: list[str] = []

    for category, samples in _TEMPLATES.items():
        target = base / category / _CATEGORY_FILES.get(category, f"{category}.json")
        if target.exists() and target.stat().st_size > 0 and not args.force:
            skipped.append(str(target))
            continue
        _write_samples(target, samples)
        created.append(str(target))

    # 验证加载（复用 evaluator.EvalSamplePool）
    try:
        from agent.skills_mgmt.evaluator import EvalSamplePool
        pool = EvalSamplePool(base_dir=str(base))
        print("[init_eval_samples] 验证加载:")
        for cat in pool.categories():
            n = len(pool.load_category(cat))
            print(f"  - {cat}: {n} 条样本")
        total = sum(len(pool.load_category(c)) for c in pool.categories())
        print(f"[init_eval_samples] 样本池合计 {total} 条")
    except Exception as e:  # noqa: BLE001 初始化脚本：加载失败仅告警
        print(f"[init_eval_samples] 警告: 样本池验证失败: {e}", file=sys.stderr)

    print(f"[init_eval_samples] 新建 {len(created)} 个文件，跳过 {len(skipped)} 个已有文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
