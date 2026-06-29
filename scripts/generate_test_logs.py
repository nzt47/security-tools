#!/usr/bin/env python3
"""
扩展系统日志生成脚本

用于生成测试日志，模拟各种扩展操作场景：
- 成功的扩展安装
- 失败的扩展安装（缺少参数）
- 扩展卸载
- 通道消息发送
- API 错误
"""

import json
import os
import sys
import time
import random
import uuid
from datetime import datetime


LOG_FILE = "./logs/extensions.log"


def get_trace_id():
    """生成追踪ID"""
    return str(uuid.uuid4())[:8]


def write_log(log_entry):
    """写入日志文件（JSON格式）"""
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')


def simulate_install_success(trace_id):
    """模拟成功的扩展安装"""
    duration = random.randint(50, 200)
    log = {
        "trace_id": trace_id,
        "module_name": "extensions_api",
        "action": "install",
        "ext_type": "skill",
        "source": "calculator",
        "duration_ms": duration,
        "status": "success",
        "message": "扩展安装成功"
    }
    write_log(log)
    return log


def simulate_install_failure_missing_params(trace_id):
    """模拟安装失败 - 缺少参数"""
    duration = random.randint(5, 15)
    log = {
        "trace_id": trace_id,
        "module_name": "extensions_api",
        "action": "install",
        "ext_type": "",
        "source": "",
        "duration_ms": duration,
        "status": "bad_request",
        "error": "缺少 type 或 source/id"
    }
    write_log(log)
    return log


def simulate_install_failure_not_found(trace_id):
    """模拟安装失败 - 扩展不存在"""
    duration = random.randint(100, 300)
    log = {
        "trace_id": trace_id,
        "module_name": "extensions_api",
        "action": "install",
        "ext_type": "skill",
        "source": "non_existent_extension",
        "duration_ms": duration,
        "status": "failed",
        "message": "扩展源不存在或无法访问"
    }
    write_log(log)
    return log


def simulate_uninstall_success(trace_id):
    """模拟成功卸载扩展"""
    duration = random.randint(30, 100)
    log = {
        "trace_id": trace_id,
        "module_name": "extensions_api",
        "action": "uninstall",
        "ext_type": "mcp",
        "ext_id": "github",
        "duration_ms": duration,
        "status": "success",
        "message": "扩展卸载成功"
    }
    write_log(log)
    return log


def simulate_toggle_extension(trace_id):
    """模拟切换扩展状态"""
    duration = random.randint(20, 50)
    log = {
        "trace_id": trace_id,
        "module_name": "extensions_api",
        "action": "toggle",
        "ext_type": "channel",
        "ext_id": "slack_notify",
        "enabled": True,
        "duration_ms": duration,
        "status": "success"
    }
    write_log(log)
    return log


def simulate_channel_send_failure(trace_id):
    """模拟通道消息发送失败"""
    duration = random.randint(500, 2000)
    log = {
        "trace_id": trace_id,
        "module_name": "extensions_api",
        "action": "channel_send",
        "channel_id": "slack_notify",
        "message_length": 256,
        "duration_ms": duration,
        "status": "failed",
        "message": "Webhook URL 无效或网络超时"
    }
    write_log(log)
    return log


def simulate_api_exception(trace_id):
    """模拟 API 异常"""
    duration = random.randint(1, 5)
    log = {
        "trace_id": trace_id,
        "module_name": "extensions_api",
        "action": "list",
        "duration_ms": duration,
        "status": "exception",
        "error": "ConnectionError: 无法连接到扩展存储"
    }
    write_log(log)
    return log


def simulate_market_search(trace_id):
    """模拟市场搜索"""
    duration = random.randint(200, 800)
    log = {
        "trace_id": trace_id,
        "module_name": "extensions_api",
        "action": "market_search",
        "query": "calculator",
        "ext_type": "all",
        "include_github": True,
        "duration_ms": duration,
        "builtin_count": 3,
        "community_count": 12,
        "github_count": 5,
        "status": "success"
    }
    write_log(log)
    return log


def simulate_discover_extensions(trace_id):
    """模拟发现扩展"""
    duration = random.randint(100, 300)
    log = {
        "trace_id": trace_id,
        "module_name": "extensions_api",
        "action": "discover",
        "duration_ms": duration,
        "available_skills": 5,
        "available_mcp": 3,
        "available_channels": 2,
        "status": "success"
    }
    write_log(log)
    return log


def generate_full_chain_trace():
    """生成一个完整的 trace_id 链路追踪"""
    base_trace_id = get_trace_id()
    
    # 步骤1: 发现扩展
    discover_trace = get_trace_id()
    log1 = simulate_discover_extensions(discover_trace)
    print(f"  [1] 发现扩展: trace_id={discover_trace}")
    
    # 步骤2: 市场搜索
    search_trace = get_trace_id()
    log2 = simulate_market_search(search_trace)
    print(f"  [2] 市场搜索: trace_id={search_trace}")
    
    # 步骤3: 安装扩展
    install_trace = get_trace_id()
    log3 = simulate_install_success(install_trace)
    print(f"  [3] 安装扩展: trace_id={install_trace}")
    
    # 步骤4: 配置扩展
    configure_trace = get_trace_id()
    duration = random.randint(30, 80)
    log4 = {
        "trace_id": configure_trace,
        "module_name": "extensions_api",
        "action": "configure",
        "ext_type": "skill",
        "ext_id": "calculator",
        "config_keys": ["api_key", "timeout"],
        "duration_ms": duration,
        "status": "success"
    }
    write_log(log4)
    print(f"  [4] 配置扩展: trace_id={configure_trace}")
    
    # 步骤5: 切换状态
    toggle_trace = get_trace_id()
    log5 = simulate_toggle_extension(toggle_trace)
    print(f"  [5] 切换状态: trace_id={toggle_trace}")
    
    print(f"\n  完整链路 base_trace_id: {base_trace_id}")
    print(f"  (这是模拟的 base_trace_id，实际中应贯穿整个请求链路)\n")
    
    return {
        "discover": discover_trace,
        "search": search_trace,
        "install": install_trace,
        "configure": configure_trace,
        "toggle": toggle_trace
    }


def main():
    print("=" * 70)
    print("  扩展系统日志生成工具")
    print("=" * 70)
    
    # 确保日志目录存在
    os.makedirs(os.path.dirname(LOG_FILE) if os.path.dirname(LOG_FILE) else '.', exist_ok=True)
    
    print(f"\n日志文件: {LOG_FILE}")
    print("\n生成模拟日志...")
    print("-" * 70)
    
    # 生成各种场景的日志
    scenarios = [
        ("成功安装扩展", simulate_install_success),
        ("成功安装扩展", simulate_install_success),
        ("失败安装 - 缺少参数", simulate_install_failure_missing_params),
        ("失败安装 - 扩展不存在", simulate_install_failure_not_found),
        ("成功卸载扩展", simulate_uninstall_success),
        ("切换扩展状态", simulate_toggle_extension),
        ("通道消息发送失败", simulate_channel_send_failure),
        ("API 异常", simulate_api_exception),
        ("市场搜索", simulate_market_search),
        ("发现扩展", simulate_discover_extensions),
    ]
    
    traces = []
    for i, (desc, func) in enumerate(scenarios, 1):
        trace_id = get_trace_id()
        log = func(trace_id)
        traces.append((trace_id, desc, log.get("status")))
        print(f"  [{i:2d}] {desc:30s} trace_id={trace_id} status={log.get('status')}")
    
    # 生成一个完整链路
    print("\n" + "-" * 70)
    print("生成完整链路追踪...")
    chain = generate_full_chain_trace()
    
    print("-" * 70)
    print(f"\n✅ 已生成 {len(scenarios) + 5} 条测试日志")
    print(f"\n📋 日志文件路径: {os.path.abspath(LOG_FILE)}")
    print(f"📊 查看统计: python scripts/debug_extensions.py --summary")
    print(f"🔍 追踪链路: python scripts/debug_extensions.py --trace <trace_id>")
    print(f"❌ 查看错误: python scripts/debug_extensions.py --errors")
    print("=" * 70)
    
    return traces, chain


if __name__ == "__main__":
    traces, chain = main()
