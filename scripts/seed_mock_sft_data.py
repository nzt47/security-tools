"""构造 mock tool_trace + tool_fewshot + messages 数据,用于本地快速验证 SFT 导出脚本

【不易】
  - 数据源结构必须与生产一致(fewshot_samples / tool_traces / messages.jsonl 字段对齐)
  - 危险命令样本必须命中 dangerous_commands.json 的 critical 模式
  - 重复 input 必须是「完全相同 JSON」(去重按 sha256(input) 判定)
【变易】
  - 数据规模可配(默认构造 6 类高频工具场景)
  - 输出目录可配(默认 data/mock_sft/,可重复运行覆盖)
【简易】
  - 单文件 CLI,构造完打印验证命令
  - 覆盖 5 类验证场景:脱敏 / 去重 / 平衡 / success 过滤 / user_message 反查

用法:
  python scripts/seed_mock_sft_data.py
  python scripts/seed_mock_sft_data.py --output-dir /tmp/mock_sft
  python scripts/seed_mock_sft_data.py --clean  # 清空后重建

验证:
  python scripts/export_sft_dataset.py \
      --fewshot-db data/mock_sft/tool_fewshot.db \
      --tool-trace-db data/mock_sft/tool_trace.db \
      --output data/mock_sft/sft_out --top-k 10 --max-per-tool 5 --verbose
"""
from __future__ import annotations

import os
import sys
import json
import time
import sqlite3
import shutil
import argparse
import logging
from datetime import datetime, timezone

# 项目根目录
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

logger = logging.getLogger("seed_mock_sft_data")

_DEFAULT_OUTPUT_DIR = os.path.join(_PROJECT_ROOT, "data", "mock_sft")


# ════════════════════════════════════════════════════════════
#  Schema 定义(与生产一致)
# ════════════════════════════════════════════════════════════

_FEWESHOT_SCHEMA = """
CREATE TABLE IF NOT EXISTS fewshot_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_name TEXT NOT NULL,
    input_json TEXT NOT NULL,
    output_json TEXT NOT NULL,
    timestamp REAL NOT NULL,
    session_id TEXT DEFAULT ''
)
"""

_TOOL_TRACE_SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT, tool_name TEXT, input_hash TEXT, output_hash TEXT,
    latency_ms REAL, success INTEGER, error_type TEXT,
    session_id TEXT, user_role TEXT, timestamp REAL, permission_decision TEXT
)
"""


# ════════════════════════════════════════════════════════════
#  Mock 数据样本(覆盖 5 类验证场景)
# ════════════════════════════════════════════════════════════

def _build_fewshot_samples(base_ts: float) -> list:
    """构造 fewshot 样本(含脱敏/去重/平衡场景)

    场景设计:
      - web_search: 重复 input(query=天气)3 条不同 timestamp → 验证去重保留最近
      - run_command: input 含 rm -rf /、format c: → 验证脱敏
      - db_query: output 含 DROP TABLE → 验证脱敏
      - read_file: 8 条不同 input → 验证平衡(max_per_tool=5 时截断)
      - safe_tool: 安全内容,不应被脱敏
    """
    # Why: 显式元组与 for 生成器混用会导致 Python 解析歧义,
    #      拆分为独立 list 后 + 拼接,语法清晰且可读。
    explicit = [
        # ── 场景 1: 去重(web_search 同 input 3 条,timestamp 递增)──
        ("web_search", {"query": "天气"}, {"ok": True, "results": [{"title": "北京天气"}]}, base_ts - 300, "s1"),
        ("web_search", {"query": "天气"}, {"ok": True, "results": [{"title": "上海天气"}]}, base_ts - 200, "s1"),  # 更近
        ("web_search", {"query": "天气"}, {"ok": True, "results": [{"title": "广州天气"}]}, base_ts - 100, "s2"),  # 最近(应保留)
        ("web_search", {"query": "新闻"}, {"ok": True, "results": [{"title": "头条"}]}, base_ts - 50, "s3"),  # 不同 input,保留

        # ── 场景 2: 脱敏(input 含危险命令)──
        ("run_command", {"cmd": "rm -rf /"}, {"ok": True, "result": "executed"}, base_ts - 90, "s4"),
        ("run_command", {"cmd": "format c:"}, {"ok": True, "result": "executed"}, base_ts - 80, "s4"),
        ("run_command", {"cmd": "shutdown now"}, {"ok": True, "result": "executed"}, base_ts - 70, "s4"),
        ("run_command", {"cmd": "ls -la"}, {"ok": True, "result": "file list"}, base_ts - 60, "s4"),  # 安全命令

        # ── 场景 3: 脱敏(output 含危险命令)──
        ("db_query", {"sql": "SELECT * FROM users"}, {"ok": True, "result": "DROP TABLE users; -- injected"}, base_ts - 40, "s5"),
        ("db_query", {"sql": "DELETE FROM logs WHERE id=1"}, {"ok": True, "result": "1 row deleted"}, base_ts - 30, "s5"),  # input 也含危险模式

        # ── 场景 5: 安全内容(不应被脱敏)──
        ("safe_tool", {"msg": "查询今天的天气"}, {"ok": True, "result": "晴天"}, base_ts - 20, "s7"),
    ]
    # ── 场景 4: 平衡(8 条不同 input,max_per_tool=5 时截断)──
    balance = [
        ("read_file", {"path": f"/tmp/file_{i}.txt"}, {"ok": True, "content": f"content_{i}"}, base_ts - i, "s6")
        for i in range(8)
    ]
    return explicit + balance


def _build_tool_traces(base_ts: float) -> list:
    """构造 tool_traces(含 success 过滤场景)

    场景设计:
      - web_search: 10 次 success=1(应进 Top K)
      - run_command: 6 次 success=1(应进 Top K)
      - read_file: 8 次 success=1(应进 Top K,样本最多)
      - db_query: 2 次 success=1(应进 Top K)
      - safe_tool: 3 次 success=1(应进 Top K)
      - fail_tool: 5 次 success=0(不应被统计)
      - safe_tool 也含 1 次 success=0(不应被统计)
    """
    traces = []
    tid = 0

    # web_search: 10 次成功
    for i in range(10):
        traces.append((f"t_ws_{tid}", "web_search", "h", "h", 100.0, 1, base_ts - i))
        tid += 1

    # run_command: 6 次成功
    for i in range(6):
        traces.append((f"t_rc_{tid}", "run_command", "h", "h", 80.0, 1, base_ts - i))
        tid += 1

    # read_file: 8 次成功
    for i in range(8):
        traces.append((f"t_rf_{tid}", "read_file", "h", "h", 50.0, 1, base_ts - i))
        tid += 1

    # db_query: 2 次成功
    for i in range(2):
        traces.append((f"t_dq_{tid}", "db_query", "h", "h", 30.0, 1, base_ts - i))
        tid += 1

    # safe_tool: 3 次成功 + 1 次失败(验证 success 过滤)
    for i in range(3):
        traces.append((f"t_st_{tid}", "safe_tool", "h", "h", 20.0, 1, base_ts - i))
        tid += 1
    traces.append((f"t_st_fail_{tid}", "safe_tool", "h", "h", 20.0, 0, base_ts))  # success=0
    tid += 1

    # fail_tool: 5 次全部失败(验证整工具不计入 Top K)
    for i in range(5):
        traces.append((f"t_ft_{tid}", "fail_tool", "h", "h", 10.0, 0, base_ts - i))
        tid += 1

    return traces


def _build_messages_jsonl(base_ts: float) -> list:
    """构造 messages.jsonl(含 user_message 反查场景)

    场景设计:
      - 在 fewshot 各样本 timestamp ±60s 窗口内放置对应 user 消息
      - web_search 最近样本(base_ts-50)窗口内有 user 消息
      - 部分样本无匹配 user(验证模板降级)
    """
    messages = []

    # web_search 最近样本(base_ts-50)窗口内
    messages.append({
        "role": "user",
        "content": "今天有什么新闻?",
        "timestamp": datetime.fromtimestamp(base_ts - 55, timezone.utc).isoformat(),
    })

    # run_command 样本(base_ts-60)窗口内
    messages.append({
        "role": "user",
        "content": "帮我执行一个命令",
        "timestamp": datetime.fromtimestamp(base_ts - 62, timezone.utc).isoformat(),
    })

    # read_file 第一条样本(base_ts-0)窗口内
    messages.append({
        "role": "user",
        "content": "读一下 file_0.txt 的内容",
        "timestamp": datetime.fromtimestamp(base_ts - 5, timezone.utc).isoformat(),
    })

    # db_query 样本(base_ts-30)窗口内
    messages.append({
        "role": "user",
        "content": "查询用户表数据",
        "timestamp": datetime.fromtimestamp(base_ts - 32, timezone.utc).isoformat(),
    })

    # 一条 assistant 消息(不应被反查)
    messages.append({
        "role": "assistant",
        "content": "好的,我来处理",
        "timestamp": datetime.fromtimestamp(base_ts - 54, timezone.utc).isoformat(),
    })

    return messages


# ════════════════════════════════════════════════════════════
#  写入函数
# ════════════════════════════════════════════════════════════

def _write_fewshot_db(db_path: str, samples: list) -> int:
    """写入 fewshot.db,返回写入条数"""
    conn = sqlite3.connect(db_path)
    conn.execute(_FEWESHOT_SCHEMA)
    conn.execute("DELETE FROM fewshot_samples")  # 可重复运行
    cnt = 0
    for tool, inp, out, ts, sid in samples:
        conn.execute(
            "INSERT INTO fewshot_samples (tool_name, input_json, output_json, timestamp, session_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (tool, json.dumps(inp, ensure_ascii=False), json.dumps(out, ensure_ascii=False), ts, sid)
        )
        cnt += 1
    conn.commit()
    conn.close()
    return cnt


def _write_tool_trace_db(db_path: str, traces: list) -> int:
    """写入 tool_trace.db,返回写入条数"""
    conn = sqlite3.connect(db_path)
    conn.execute(_TOOL_TRACE_SCHEMA)
    conn.execute("DELETE FROM tool_traces")  # 可重复运行
    cnt = 0
    for trace_id, tool_name, input_hash, output_hash, latency, success, ts in traces:
        conn.execute(
            "INSERT INTO tool_traces (trace_id, tool_name, input_hash, output_hash, latency_ms, "
            "success, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (trace_id, tool_name, input_hash, output_hash, latency, success, ts)
        )
        cnt += 1
    conn.commit()
    conn.close()
    return cnt


def _write_messages_jsonl(path: str, messages: list) -> int:
    """写入 messages.jsonl,返回写入条数"""
    with open(path, "w", encoding="utf-8") as f:
        for m in messages:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    return len(messages)


# ════════════════════════════════════════════════════════════
#  主流程
# ════════════════════════════════════════════════════════════

def seed_mock_data(output_dir: str = _DEFAULT_OUTPUT_DIR, clean: bool = False) -> dict:
    """构造 mock 数据集

    Args:
        output_dir: 输出目录
        clean: 是否先清空目录

    Returns:
        dict: 构造结果 {fewshot_db, tool_trace_db, messages_jsonl, counts, base_ts}
    """
    os.makedirs(output_dir, exist_ok=True)

    if clean:
        # Why: 可重复运行,清空旧 mock 数据避免污染
        for f in os.listdir(output_dir):
            fp = os.path.join(output_dir, f)
            if os.path.isfile(fp):
                os.remove(fp)
            elif os.path.isdir(fp):
                shutil.rmtree(fp)
        logger.info("已清空目录: %s", output_dir)

    base_ts = time.time()

    # 构造数据
    fewshot_samples = _build_fewshot_samples(base_ts)
    tool_traces = _build_tool_traces(base_ts)
    messages = _build_messages_jsonl(base_ts)

    # 写入
    fewshot_db = os.path.join(output_dir, "tool_fewshot.db")
    trace_db = os.path.join(output_dir, "tool_trace.db")
    messages_path = os.path.join(output_dir, "messages.jsonl")

    fewshot_cnt = _write_fewshot_db(fewshot_db, fewshot_samples)
    trace_cnt = _write_tool_trace_db(trace_db, tool_traces)
    msg_cnt = _write_messages_jsonl(messages_path, messages)

    # 写入元数据(供 export 脚本读取 _MESSAGES_JSONL)
    meta = {
        "base_ts": base_ts,
        "fewshot_db": fewshot_db,
        "tool_trace_db": trace_db,
        "messages_jsonl": messages_path,
        "counts": {
            "fewshot_samples": fewshot_cnt,
            "tool_traces": trace_cnt,
            "messages": msg_cnt,
        },
        "scenarios": {
            "dedup": "web_search 同 input(query=天气)3 条,应保留 timestamp 最近的 1 条",
            "redact_input": "run_command input 含 rm -rf /、format c:、shutdown,应替换为 [REDACTED_DANGEROUS]",
            "redact_output": "db_query output 含 DROP TABLE、DELETE FROM,应替换为 [REDACTED_DANGEROUS]",
            "balance": "read_file 8 条不同 input,max_per_tool=5 时应截断到 5 条",
            "success_filter": "fail_tool 5 次 success=0 不应计入 Top K;safe_tool 1 次 success=0 不应计入",
            "user_message_lookup": "web_search/run_command/read_file/db_query 样本 ±60s 窗口内有 user 消息,应反查成功;safe_tool 无匹配,应模板降级",
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    meta_path = os.path.join(output_dir, "mock_meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    logger.info("Mock 数据构造完成:")
    logger.info("  fewshot_db:    %s (%d 样本)", fewshot_db, fewshot_cnt)
    logger.info("  tool_trace_db: %s (%d 记录)", trace_db, trace_cnt)
    logger.info("  messages.jsonl: %s (%d 消息)", messages_path, msg_cnt)
    logger.info("  meta:          %s", meta_path)
    return meta


def print_verify_guide(meta: dict) -> None:
    """打印验证命令指引"""
    print(f"\n{'='*70}")
    print("Mock 数据已构造,验证命令:")
    print(f"{'='*70}")
    print(f"\n# 1. 运行 SFT 导出(用 mock 数据)")
    print(f"python scripts/export_sft_dataset.py \\")
    print(f"    --fewshot-db \"{meta['fewshot_db']}\" \\")
    print(f"    --tool-trace-db \"{meta['tool_trace_db']}\" \\")
    print(f"    --output \"{os.path.dirname(meta['fewshot_db'])}/sft_out\" \\")
    print(f"    --top-k 10 --max-per-tool 5 --verbose")
    print(f"\n# 2. 验证脱敏(危险命令应被替换)")
    print(f"\n# 3. 验证去重(web_search 应只保留 query=天气 最近 1 条 + query=新闻 1 条 = 2 条)")
    print(f"\n# 4. 验证平衡(read_file 应截断到 5 条)")
    print(f"\n# 5. 验证 success 过滤(fail_tool 不应出现在 Top K)")
    print(f"\n# 场景说明见: {os.path.dirname(meta['fewshot_db'])}/mock_meta.json")
    print(f"{'='*70}\n")


def main():
    parser = argparse.ArgumentParser(
        description="构造 mock tool_trace + tool_fewshot 数据,验证 SFT 导出脱敏/去重/平衡"
    )
    parser.add_argument(
        "--output-dir", type=str, default=_DEFAULT_OUTPUT_DIR,
        help=f"输出目录(默认 {_DEFAULT_OUTPUT_DIR})"
    )
    parser.add_argument(
        "--clean", action="store_true",
        help="先清空输出目录(可重复运行)"
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

    meta = seed_mock_data(output_dir=args.output_dir, clean=args.clean)
    print_verify_guide(meta)


if __name__ == "__main__":
    main()
