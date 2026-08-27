"""高频工具 SFT 数据集导出管道

从 tool_trace 采样高频工具的成功调用,构造 (system_prompt, user_message,
tool_call, tool_result, assistant_response) 五元组,脱敏后输出为 OpenAI/Anthropic
兼容的 SFT JSONL 数据集。

【不易】
  - 数据源:tool_trace.db 统计高频工具, tool_fewshot.db 取成功调用样本
  - 危险命令脱敏:匹配 data/dangerous_commands.json critical 模式后用
    [REDACTED_DANGEROUS] 替换匹配段(可读且安全,不复用 SHA256 哈希破坏可读性)
  - 去重:相同 (tool_name, sha256(input)[:16]) 仅保留最近 1 条
  - 平衡:每工具最多 N 条(默认 1000,可配)
  - 仅保留 success=True 的工具调用
【变易】
  - user_message 反查:用 timestamp ±60s 窗口从 messages.jsonl 反查真实用户消息,
    查不到时模板降级
  - system_prompt 用 digital_life._get_template 模板,失败时简化版降级
【简易】
  - 单文件 CLI,直接 python scripts/export_sft_dataset.py 运行
  - JSONL 格式可被 transformers/datasets 直接加载

输出:
  - data/sft_datasets/sft_{YYYYMMDD}.jsonl       - 训练集
  - data/sft_datasets/sft_{YYYYMMDD}_report.json - 导出报告(每工具样本数等)
"""
from __future__ import annotations

import os
import re
import sys
import json
import time
import sqlite3
import hashlib
import argparse
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# 把项目根目录加入 sys.path(便于 import agent.*)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent.tool_calling import summarize_tool_result  # 公共入口

logger = logging.getLogger("export_sft_dataset")

# ════════════════════════════════════════════════════════════
#  路径
# ════════════════════════════════════════════════════════════

_TOOL_TRACE_DB = os.path.join(_PROJECT_ROOT, "agent", "data", "tool_trace.db")
_FEWESHOT_DB = os.path.join(_PROJECT_ROOT, "agent", "data", "tool_fewshot.db")
_DANGEROUS_CMDS_PATH = os.path.join(_PROJECT_ROOT, "data", "dangerous_commands.json")
_MESSAGES_JSONL = os.path.join(_PROJECT_ROOT, "data", "messages.jsonl")
_DEFAULT_OUTPUT_DIR = os.path.join(_PROJECT_ROOT, "data", "sft_datasets")

# user_message 反查窗口(秒)
_USER_MSG_LOOKUP_WINDOW_SECONDS = 60


# ════════════════════════════════════════════════════════════
#  Top K 高频工具
# ════════════════════════════════════════════════════════════

def get_top_k_tools(top_k: int, db_path: str = _TOOL_TRACE_DB) -> List[Tuple[str, int]]:
    """从 tool_trace.db 查 Top K 高频工具(按 success=True 调用数排序)

    Args:
        top_k: 返回工具数上限
        db_path: tool_trace.db 路径

    Returns:
        List[(tool_name, call_count)]: 按调用数倒序
    """
    if not os.path.exists(db_path):
        logger.warning("tool_trace.db 不存在: %s,返回空列表", db_path)
        return []
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT tool_name, COUNT(*) as cnt "
            "FROM tool_traces WHERE success=1 "
            "GROUP BY tool_name ORDER BY cnt DESC LIMIT ?",
            (top_k,)
        ).fetchall()
        conn.close()
        return [(row["tool_name"], row["cnt"]) for row in rows]
    except Exception as e:
        logger.warning("查询 Top K 工具失败: %s", e)
        return []


# ════════════════════════════════════════════════════════════
#  Few-shot 样本查询(直接查 SQLite,取 timestamp/session_id)
# ════════════════════════════════════════════════════════════

def fetch_fewshot_samples(
    tool_name: str,
    limit: int,
    db_path: str = _FEWESHOT_DB,
) -> List[Dict]:
    """直接查 fewshot_samples 表,取 input/output/timestamp/session_id

    Why: 直接查表才能拿到 timestamp/session_id(反查 user_message 需要)。

    Args:
        tool_name: 工具名
        limit: 最大返回数
        db_path: tool_fewshot.db 路径

    Returns:
        list[{tool_name, input, output, timestamp, session_id}]: 按 timestamp 倒序
    """
    if not os.path.exists(db_path):
        return []
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT input_json, output_json, timestamp, session_id "
            "FROM fewshot_samples WHERE tool_name=? "
            "ORDER BY timestamp DESC LIMIT ?",
            (tool_name, limit)
        ).fetchall()
        conn.close()
        out: List[Dict] = []
        for r in rows:
            try:
                out.append({
                    # Why: dedup_and_balance 需以 (tool_name, input_hash) 为去重键,
                    # 直接查表无法返回 tool_name,故用入参回填(同表行 tool_name 一致)。
                    "tool_name": tool_name,
                    "input": json.loads(r["input_json"]),
                    "output": json.loads(r["output_json"]),
                    "timestamp": float(r["timestamp"]),
                    "session_id": r["session_id"] or "",
                })
            except Exception:
                continue
        return out
    except Exception as e:
        logger.warning("查询 fewshot_samples 失败 tool=%s: %s", tool_name, e)
        return []


# ════════════════════════════════════════════════════════════
#  危险命令脱敏(占位符替换)
# ════════════════════════════════════════════════════════════

def load_critical_patterns(path: str = _DANGEROUS_CMDS_PATH) -> List[re.Pattern]:
    """加载 dangerous_commands.json 的 critical 模式正则"""
    patterns: List[re.Pattern] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for item in data.get("critical", []):
            try:
                patterns.append(re.compile(item["pattern"]))
            except (re.error, KeyError):
                continue
    except Exception as e:
        logger.warning("加载危险命令模式失败: %s", e)
    return patterns


_CRITICAL_PATTERNS: Optional[List[re.Pattern]] = None


def _get_critical_patterns() -> List[re.Pattern]:
    """懒加载 critical 模式(全局缓存)"""
    global _CRITICAL_PATTERNS
    if _CRITICAL_PATTERNS is None:
        _CRITICAL_PATTERNS = load_critical_patterns()
    return _CRITICAL_PATTERNS


def redact_dangerous_content(content: str) -> str:
    """匹配 dangerous_commands.json critical 模式后用 [REDACTED_DANGEROUS] 替换

    Why: 危险命令(如 rm -rf /)需额外占位符替换以防 LLM 学习危险操作。
         用占位符而非哈希,保留 SFT 可读性(未匹配部分可读)。

    Args:
        content: 任意字符串(JSON 后)

    Returns:
        str: 替换后的字符串
    """
    if not content:
        return content
    result = content
    for pattern in _get_critical_patterns():
        try:
            result = pattern.sub("[REDACTED_DANGEROUS]", result)
        except Exception:
            continue
    return result


def redact_dict(data: Any) -> Any:
    """递归对 dict/list/str 应用危险命令占位符替换"""
    if isinstance(data, str):
        return redact_dangerous_content(data)
    if isinstance(data, dict):
        return {k: redact_dict(v) for k, v in data.items()}
    if isinstance(data, list):
        return [redact_dict(v) for v in data]
    return data


# ════════════════════════════════════════════════════════════
#  user_message 反查(从 messages.jsonl)
# ════════════════════════════════════════════════════════════

def _parse_iso_timestamp(ts_str: str) -> Optional[float]:
    """解析 ISO8601 时间戳为 Unix epoch

    Why: messages.jsonl 的 timestamp 是 ISO8601 字符串,
         fewshot_samples 的 timestamp 是 Unix epoch float,需统一比较。
    """
    try:
        ts_str = ts_str.rstrip("Z")
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def lookup_user_message(
    timestamp: float,
    window_seconds: int = _USER_MSG_LOOKUP_WINDOW_SECONDS,
    messages_path: str = _MESSAGES_JSONL,
) -> Optional[str]:
    """从 messages.jsonl 反查 ±window 秒内最近的 user 消息

    Args:
        timestamp: fewshot 样本的 Unix timestamp
        window_seconds: 时间窗口(秒,默认 ±60s)
        messages_path: messages.jsonl 路径

    Returns:
        str: 用户消息内容;查不到返回 None(由调用方模板降级)
    """
    if not os.path.exists(messages_path):
        return None
    try:
        best_msg = None
        best_diff = float("inf")
        with open(messages_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                if msg.get("role") != "user":
                    continue
                ts_str = msg.get("timestamp", "")
                if not ts_str:
                    continue
                msg_ts = _parse_iso_timestamp(ts_str)
                if msg_ts is None:
                    continue
                diff = abs(msg_ts - timestamp)
                if diff <= window_seconds and diff < best_diff:
                    best_diff = diff
                    best_msg = msg.get("content", "")
        return best_msg
    except Exception as e:
        logger.debug("反查 messages.jsonl 失败: %s", e)
        return None


def fallback_user_message(tool_name: str, input_data: Dict) -> str:
    """模板降级 user_message(反查失败时使用)

    Args:
        tool_name: 工具名
        input_data: 工具输入

    Returns:
        str: 模板生成的 user_message
    """
    try:
        input_summary = json.dumps(input_data, ensure_ascii=False)
        if len(input_summary) > 100:
            input_summary = input_summary[:100] + "..."
    except Exception:
        input_summary = str(input_data)[:100]
    return f"请帮我使用 {tool_name} 工具处理: {input_summary}"


# ════════════════════════════════════════════════════════════
#  system_prompt 构造
# ════════════════════════════════════════════════════════════

def build_default_system_prompt() -> str:
    """构造默认 system_prompt(优先用 digital_life._get_template 模板填充占位符)

    Why: SFT 五元组需要 system_prompt,但样本不存原始 system_prompt。
         用默认模板填充合理默认值,保持 SFT 样本结构完整。
    """
    try:
        from agent.digital_life import _get_template
        template = _get_template()
        return template.format(
            current_date=datetime.now().strftime("%Y年%m月%d日"),
            body_status="",
            mode_name="默认",
            mode_description="",
            memory_context="",
            tool_status="",
            skill_instructions="",
        )
    except Exception as e:
        logger.warning("构造默认 system_prompt 失败,使用简化版: %s", e)
        return (
            "你是一个能调用工具的智能助手。根据用户需求选择合适的工具完成任务,"
            "并基于工具返回结果给出回复。"
        )


# ════════════════════════════════════════════════════════════
#  五元组构造
# ════════════════════════════════════════════════════════════

def build_sft_quintuple(
    tool_name: str,
    input_data: Dict,
    output_data: Dict,
    timestamp: float,
    session_id: str,
    call_id: str,
    system_prompt: str,
) -> Dict:
    """构造 SFT 五元组(OpenAI Function Calling 格式)

    Args:
        tool_name: 工具名
        input_data: 工具输入(已脱敏)
        output_data: 工具输出(已脱敏)
        timestamp: 调用时间戳(Unix epoch)
        session_id: 会话 ID
        call_id: 工具调用 ID
        system_prompt: 系统提示词

    Returns:
        dict: {"messages": [...]} OpenAI SFT 格式
    """
    # user_message 反查 + 模板降级
    user_msg = lookup_user_message(timestamp)
    if not user_msg:
        user_msg = fallback_user_message(tool_name, input_data)

    # assistant_response 用 summarize_tool_result 生成
    assistant_response = summarize_tool_result(tool_name, output_data)

    # tool_call arguments 序列化为 JSON 字符串
    try:
        args_str = json.dumps(input_data, ensure_ascii=False)
    except Exception:
        args_str = "{}"

    # tool_result content 序列化为 JSON 字符串
    try:
        result_str = json.dumps(output_data, ensure_ascii=False)
    except Exception:
        result_str = "{}"

    return {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": args_str,
                    },
                }],
            },
            {
                "role": "tool",
                "tool_call_id": call_id,
                "content": result_str,
            },
            {"role": "assistant", "content": assistant_response},
        ]
    }


# ════════════════════════════════════════════════════════════
#  去重 + 平衡
# ════════════════════════════════════════════════════════════

def _input_hash(input_data: Any) -> str:
    """计算 input 的 SHA256 前 16 位(用于去重)"""
    try:
        content = json.dumps(input_data, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        content = str(input_data)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def dedup_and_balance(
    samples: List[Dict],
    max_per_tool: int,
) -> Tuple[List[Dict], int, int]:
    """去重 + 平衡

    Args:
        samples: 原始样本列表(每项含 tool_name, input, output, timestamp, session_id)
        max_per_tool: 每工具最多保留条数

    Returns:
        (deduped_samples, before_count, after_count)
    """
    seen: Dict[str, Dict] = {}  # key=(tool_name, input_hash), value=sample
    for s in samples:
        key = f"{s['tool_name']}|{_input_hash(s['input'])}"
        # 仅保留最近 1 条(samples 已按 timestamp 倒序,首个即最近)
        if key not in seen:
            seen[key] = s
    deduped = list(seen.values())[:max_per_tool]
    return deduped, len(samples), len(deduped)


# ════════════════════════════════════════════════════════════
#  主流程
# ════════════════════════════════════════════════════════════

def export_sft_dataset(
    top_k: int = 20,
    max_per_tool: int = 1000,
    output_dir: str = _DEFAULT_OUTPUT_DIR,
    fewshot_db: str = _FEWESHOT_DB,
    tool_trace_db: str = _TOOL_TRACE_DB,
) -> Dict:
    """导出 SFT 数据集主流程

    Args:
        top_k: 取 Top K 高频工具
        max_per_tool: 每工具最多样本数
        output_dir: 输出目录
        fewshot_db: tool_fewshot.db 路径
        tool_trace_db: tool_trace.db 路径

    Returns:
        dict: 导出报告 {date, total_samples, per_tool: {...}, before_dedup, after_dedup}
    """
    os.makedirs(output_dir, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    output_path = os.path.join(output_dir, f"sft_{date_str}.jsonl")
    report_path = os.path.join(output_dir, f"sft_{date_str}_report.json")

    # 1. Top K 高频工具
    top_tools = get_top_k_tools(top_k, db_path=tool_trace_db)
    logger.info("Top %d 高频工具: %s", top_k, [t[0] for t in top_tools])

    # 2. 默认 system_prompt(全部样本共用)
    system_prompt = build_default_system_prompt()

    # 3. 逐工具采样 + 构造五元组
    total_samples = 0
    total_before_dedup = 0
    per_tool_report: Dict[str, Dict] = {}

    with open(output_path, "w", encoding="utf-8") as f:
        for tool_name, call_count in top_tools:
            # 多取 2x 便于去重后仍能满足 max_per_tool
            raw_samples = fetch_fewshot_samples(
                tool_name, limit=max_per_tool * 2, db_path=fewshot_db
            )
            if not raw_samples:
                per_tool_report[tool_name] = {
                    "call_count_in_trace": call_count,
                    "samples_before_dedup": 0,
                    "samples_after_dedup": 0,
                }
                continue

            # 去重 + 平衡
            deduped, before_cnt, after_cnt = dedup_and_balance(raw_samples, max_per_tool)
            total_before_dedup += before_cnt

            # 构造五元组并写入
            for idx, s in enumerate(deduped):
                call_id = f"call_{tool_name}_{idx}"
                # 危险命令占位符替换
                safe_input = redact_dict(s["input"])
                safe_output = redact_dict(s["output"])
                quintuple = build_sft_quintuple(
                    tool_name=tool_name,
                    input_data=safe_input,
                    output_data=safe_output,
                    timestamp=s["timestamp"],
                    session_id=s.get("session_id", ""),
                    call_id=call_id,
                    system_prompt=system_prompt,
                )
                f.write(json.dumps(quintuple, ensure_ascii=False) + "\n")
                total_samples += 1

            per_tool_report[tool_name] = {
                "call_count_in_trace": call_count,
                "samples_before_dedup": before_cnt,
                "samples_after_dedup": after_cnt,
            }

    # 4. 生成报告
    report = {
        "date": date_str,
        "top_k": top_k,
        "max_per_tool": max_per_tool,
        "total_samples": total_samples,
        "total_before_dedup": total_before_dedup,
        "output_file": output_path,
        "per_tool": per_tool_report,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    logger.info("SFT 数据集导出完成: %s (%d 样本)", output_path, total_samples)
    logger.info("报告: %s", report_path)
    return report


# ════════════════════════════════════════════════════════════
#  CLI 入口
# ════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="导出高频工具 SFT 数据集(JSONL,OpenAI Function Calling 格式)"
    )
    parser.add_argument(
        "--top-k", type=int, default=20,
        help="Top K 高频工具(默认 20)"
    )
    parser.add_argument(
        "--max-per-tool", type=int, default=1000,
        help="每工具最多样本数(默认 1000)"
    )
    parser.add_argument(
        "--output", type=str, default=_DEFAULT_OUTPUT_DIR,
        help="输出目录(默认 data/sft_datasets/)"
    )
    parser.add_argument(
        "--fewshot-db", type=str, default=_FEWESHOT_DB,
        help="tool_fewshot.db 路径"
    )
    parser.add_argument(
        "--tool-trace-db", type=str, default=_TOOL_TRACE_DB,
        help="tool_trace.db 路径"
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="启用 DEBUG 日志"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    report = export_sft_dataset(
        top_k=args.top_k,
        max_per_tool=args.max_per_tool,
        output_dir=args.output,
        fewshot_db=args.fewshot_db,
        tool_trace_db=args.tool_trace_db,
    )

    # 打印摘要
    print(f"\n{'='*60}")
    print(f"SFT 数据集导出完成")
    print(f"{'='*60}")
    print(f"输出文件: {report['output_file']}")
    print(f"总样本数: {report['total_samples']}")
    print(f"去重前: {report['total_before_dedup']}")
    print(f"Top K 工具: {report['top_k']}")
    print(f"每工具最多: {report['max_per_tool']}")
    print(f"\n各工具样本数:")
    for tool, info in report["per_tool"].items():
        print(f"  {tool}: trace={info['call_count_in_trace']}, "
              f"dedup={info['samples_before_dedup']}->{info['samples_after_dedup']}")


if __name__ == "__main__":
    main()
