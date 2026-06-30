#!/usr/bin/env python3
"""
扩展系统快速排错工具

使用方法：
    python scripts/debug_extensions.py                  # 显示概览
    python scripts/debug_extensions.py --trace <id>     # 追踪某个 trace_id
    python scripts/debug_extensions.py --ext <id>       # 查看某个扩展的所有操作
    python scripts/debug_extensions.py --errors         # 只显示错误
    python scripts/debug_extensions.py --action install # 只显示安装操作
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from collections import defaultdict, Counter


LOG_FILE = "./logs/extensions.log"
ERROR_LOG = "./logs/errors.log"


def parse_args():
    parser = argparse.ArgumentParser(description="扩展系统快速排错工具")
    parser.add_argument("--trace", help="追踪指定 trace_id 的完整链路")
    parser.add_argument("--ext", help="查看指定扩展 ID 的所有操作")
    parser.add_argument("--errors", action="store_true", help="只显示错误日志")
    parser.add_argument("--action", help="只显示指定操作类型 (install/uninstall/toggle/configure/...)")
    parser.add_argument("--tail", type=int, default=100, help="显示最近 N 条 (默认 100)")
    parser.add_argument("--summary", action="store_true", help="显示统计概览")
    parser.add_argument("--last", type=str, default="1h", help="查看最近时间 (1h, 24h, 7d)")
    return parser.parse_args()


def parse_log_line(line):
    """解析日志行，提取 JSON 部分"""
    try:
        # 尝试找到 JSON 部分（日志格式可能包含前缀）
        start = line.find('{')
        if start >= 0:
            json_str = line[start:].strip()
            return json.loads(json_str)
    except (json.JSONDecodeError, IndexError):
        pass
    return None


def load_logs(log_file, tail_count=100):
    """加载日志文件"""
    if not os.path.exists(log_file):
        return []
    
    logs = []
    with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()
        for line in lines[-tail_count:]:
            log_entry = parse_log_line(line)
            if log_entry:
                logs.append(log_entry)
    return logs


def filter_logs(logs, args):
    """根据参数过滤日志"""
    filtered = logs
    
    if args.trace:
        filtered = [l for l in filtered if l.get("trace_id") == args.trace]
    
    if args.ext:
        ext_id = args.ext
        filtered = [
            l for l in filtered 
            if l.get("ext_id") == ext_id 
            or l.get("source") == ext_id
            or l.get("channel_id") == ext_id
        ]
    
    if args.errors:
        filtered = [
            l for l in filtered 
            if l.get("status") in ("failed", "exception", "bad_request")
        ]
    
    if args.action:
        filtered = [l for l in filtered if l.get("action") == args.action]
    
    return filtered


def print_summary(logs):
    """打印统计概览"""
    print("=" * 70)
    print("  扩展系统日志统计概览")
    print("=" * 70)
    
    if not logs:
        print("\n  ⚠️  没有找到日志记录\n")
        return
    
    total = len(logs)
    status_counter = Counter(l.get("status", "unknown") for l in logs)
    action_counter = Counter(l.get("action", "unknown") for l in logs)
    type_counter = Counter(l.get("ext_type", l.get("channel_id", "n/a")) for l in logs)
    
    print(f"\n  📊 总日志数: {total}")
    print(f"\n  📈 状态分布:")
    for status, count in status_counter.most_common():
        pct = count / total * 100
        bar = "█" * int(pct / 5)
        icon = "✅" if status == "success" else "❌" if status == "failed" else "⚠️" if status == "bad_request" else "💥" if status == "exception" else "❓"
        print(f"    {icon} {status:12s} {count:5d} ({pct:5.1f}%) {bar}")
    
    print(f"\n  🔧 操作类型分布:")
    for action, count in action_counter.most_common():
        print(f"    - {action:20s} {count:5d} 次")
    
    # 最近错误
    errors = [l for l in logs if l.get("status") in ("failed", "exception", "bad_request")]
    if errors:
        print(f"\n  ❌ 最近 5 个错误:")
        for err in errors[-5:]:
            print(f"    [{err.get('action', '?')}] {err.get('error', err.get('message', '未知错误'))[:80]}")
            print(f"       trace_id: {err.get('trace_id', 'N/A')}")
    
    print("\n" + "=" * 70)


def print_trace_chain(logs, trace_id):
    """打印 trace_id 的完整链路"""
    trace_logs = [l for l in logs if l.get("trace_id") == trace_id]
    
    if not trace_logs:
        print(f"\n⚠️  未找到 trace_id: {trace_id}\n")
        return
    
    # 按时间排序（假设日志是顺序的）
    print(f"\n🔍 链路追踪: {trace_id}")
    print("-" * 70)
    
    total_duration = 0
    for i, log in enumerate(trace_logs, 1):
        action = log.get("action", "?")
        status = log.get("status", "?")
        duration = log.get("duration_ms", 0)
        total_duration += duration
        
        icon = "✅" if status == "success" else "❌" if status == "failed" else "⏳" if status == "start" else "⚠️"
        print(f"  {i:2d}. {icon} {action:20s} {status:12s} {duration:5d}ms")
        
        # 显示额外信息
        extra = []
        if log.get("ext_type"):
            extra.append(f"type={log['ext_type']}")
        if log.get("ext_id") or log.get("source"):
            extra.append(f"id={log.get('ext_id') or log.get('source')}")
        if log.get("error") or log.get("message"):
            extra.append(f"msg={log.get('error') or log.get('message')}")
        if extra:
            print(f"      {' | '.join(extra)}")
    
    print("-" * 70)
    print(f"  总计: {len(trace_logs)} 步, 耗时: {total_duration}ms")
    print()


def print_log_table(logs, limit=50):
    """以表格形式打印日志"""
    if not logs:
        print("\n  ⚠️  没有符合条件的日志\n")
        return
    
    print(f"\n📋 日志列表 (最近 {min(limit, len(logs))} 条)")
    print("-" * 90)
    print(f"  {'动作':15s} {'类型':10s} {'扩展ID':15s} {'状态':10s} {'耗时':>8s} {'trace_id':20s}")
    print("-" * 90)
    
    for log in logs[-limit:]:
        action = log.get("action", "?")[:15]
        ext_type = log.get("ext_type", log.get("channel_id", "?"))[:10]
        ext_id = log.get("ext_id") or log.get("source") or log.get("channel_id") or "?"
        ext_id = ext_id[:15]
        status = log.get("status", "?")[:10]
        duration = f"{log.get('duration_ms', 0):>5d}ms"
        trace_id = log.get("trace_id", "?")[:20]
        
        print(f"  {action:15s} {ext_type:10s} {ext_id:15s} {status:10s} {duration:>8s} {trace_id:20s}")
    
    print("-" * 90)
    print(f"  共 {len(logs)} 条记录\n")


def main():
    args = parse_args()
    
    # 加载日志
    tail_count = args.tail * 10 if args.summary or args.trace else args.tail
    logs = load_logs(LOG_FILE, tail_count)
    
    if args.summary or args.trace or args.errors or args.ext or args.action:
        # 过滤模式
        if args.trace:
            print_trace_chain(logs, args.trace)
            return
        
        filtered = filter_logs(logs, args)
        print_log_table(filtered, args.tail)
        return
    
    # 默认显示概览
    print_summary(logs)
    print_log_table(logs, 20)
    
    print("💡 提示:")
    print("   使用 --trace <id> 查看完整调用链路")
    print("   使用 --ext <id> 查看某个扩展的所有操作")
    print("   使用 --errors 只显示错误日志")
    print("   使用 --action install 查看安装操作")
    print()


if __name__ == "__main__":
    main()
