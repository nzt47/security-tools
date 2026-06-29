#!/usr/bin/env python3
"""
Mock 数据生成脚本 - 为仪表盘生成测试数据

运行此脚本可以生成大量测试数据，以便在本地查看仪表盘效果。
"""

import json
import os
import uuid
import random
from datetime import datetime, timedelta
from pathlib import Path

def generate_mock_trace_data(count=100):
    """生成模拟追踪数据"""
    services = ["DigitalLife", "VectorMemory", "API", "Critic", "TaskPlanner", "Memory", "WebServer"]
    operations = ["chat", "search", "evaluate", "plan", "execute", "save", "load", "update"]
    statuses = ["success", "success", "success", "success", "success", "error", "timeout"]
    
    traces = []
    now = datetime.now()
    
    for i in range(count):
        trace_id = uuid.uuid4().hex[:16]
        service = random.choice(services)
        operation = random.choice(operations)
        status = random.choice(statuses)
        duration = int(30 + random.random() * 500)
        timestamp = (now - timedelta(minutes=i * 5)).timestamp()
        
        traces.append({
            "trace_id": trace_id,
            "service": service,
            "operation": operation,
            "status": status,
            "duration_ms": duration,
            "timestamp": timestamp
        })
    
    return traces

def generate_mock_quality_data():
    """生成模拟质量数据"""
    now = datetime.now()
    
    # Schema 校验趋势 (25个时间点)
    schema_trend = []
    for i in range(24, -1, -1):
        hour_start = (now - timedelta(hours=i)).replace(minute=0, second=0, microsecond=0)
        base_total = 80 + random.randint(0, 40)
        success_rate = 0.75 + random.random() * 0.2
        schema_trend.append({
            "time": hour_start.strftime("%H:00"),
            "total": base_total,
            "success": int(base_total * success_rate),
            "fail": base_total - int(base_total * success_rate)
        })
    
    # Critic 评分趋势
    critic_trend = []
    for i in range(24, -1, -1):
        hour_start = (now - timedelta(hours=i)).replace(minute=0, second=0, microsecond=0)
        base_score = 70 + random.randint(-5, 20)
        critic_trend.append({
            "time": hour_start.strftime("%H:00"),
            "avg_score": min(100, max(0, base_score)),
            "count": 15 + random.randint(0, 20)
        })
    
    # 失败模式分布
    failure_dist = {
        "api_fabrication": 15 + random.randint(-5, 10),
        "field_error": 12 + random.randint(-3, 8),
        "logic_error": 8 + random.randint(-2, 6),
        "timeout": 5 + random.randint(-2, 4),
        "validation_failure": 20 + random.randint(-5, 15),
        "rate_limit": 3 + random.randint(-1, 3),
        "network_error": 6 + random.randint(-2, 5)
    }
    
    return {
        "schema_trend": schema_trend,
        "critic_trend": critic_trend,
        "failure_distribution": failure_dist,
        "total_validations": sum(s["total"] for s in schema_trend),
        "successful_validations": sum(s["success"] for s in schema_trend),
        "total_evaluations": sum(c["count"] for c in critic_trend),
        "avg_critic_score": sum(c["avg_score"] * c["count"] for c in critic_trend) / sum(c["count"] for c in critic_trend)
    }

def generate_mock_memory_data():
    """生成模拟内存数据"""
    now = datetime.now()
    
    # 长期记忆增长趋势 (8天)
    long_term_trend = []
    base_count = 2000
    for i in range(7, -1, -1):
        date = (now - timedelta(days=i)).strftime("%m-%d")
        count = base_count + i * 100 + random.randint(-50, 50)
        long_term_trend.append({
            "date": date,
            "count": count,
            "size_mb": count * 0.02
        })
    
    # 临时记忆趋势 (24小时)
    short_term_trend = []
    for i in range(24, -1, -1):
        hour_start = (now - timedelta(hours=i)).replace(minute=0, second=0, microsecond=0)
        short_term_trend.append({
            "time": hour_start.strftime("%H:00"),
            "count": 40 + random.randint(0, 30),
            "hit_count": 25 + random.randint(0, 20)
        })
    
    # 命中率趋势
    hit_rate_trend = []
    for i in range(24, -1, -1):
        hour_start = (now - timedelta(hours=i)).replace(minute=0, second=0, microsecond=0)
        hit_rate = 65 + random.randint(-10, 20)
        hit_rate_trend.append({
            "time": hour_start.strftime("%H:00"),
            "hit_rate": min(100, max(0, hit_rate)),
            "requests": 80 + random.randint(0, 40)
        })
    
    # 最近访问记录
    types = ["read", "write", "update", "delete", "read", "read", "write"]
    categories = ["对话历史", "知识库", "用户偏好", "任务状态", "系统配置"]
    
    recent_access = []
    for i in range(20):
        recent_access.append({
            "id": uuid.uuid4().hex[:8],
            "type": random.choice(types),
            "category": random.choice(categories),
            "content": f"记忆条目 {i + 1}: {random.choice(['用户对话记录', '知识库文档', '用户偏好设置', '任务执行状态', '系统配置信息'])}",
            "timestamp": (now - timedelta(minutes=i * 3)).timestamp(),
            "duration_ms": 10 + random.randint(0, 30)
        })
    
    return {
        "long_term_trend": long_term_trend,
        "short_term_trend": short_term_trend,
        "hit_rate_trend": hit_rate_trend,
        "recent_access": recent_access,
        "category_distribution": [
            {"name": "对话历史", "count": 1250, "percentage": 50},
            {"name": "知识库", "count": 550, "percentage": 22},
            {"name": "用户偏好", "count": 350, "percentage": 14},
            {"name": "任务状态", "count": 200, "percentage": 8},
            {"name": "其他", "count": 150, "percentage": 6}
        ],
        "long_term_count": 2500,
        "short_term_count": 150,
        "overall_hit_rate": 73.5,
        "total_size_mb": 50.5
    }

def save_mock_data():
    """保存 mock 数据到文件"""
    data_dir = Path("data/mock")
    data_dir.mkdir(parents=True, exist_ok=True)
    
    # 保存追踪数据
    traces = generate_mock_trace_data(100)
    with open(data_dir / "traces.json", "w", encoding="utf-8") as f:
        json.dump(traces, f, ensure_ascii=False, indent=2)
    
    # 保存质量数据
    quality = generate_mock_quality_data()
    with open(data_dir / "quality.json", "w", encoding="utf-8") as f:
        json.dump(quality, f, ensure_ascii=False, indent=2)
    
    # 保存内存数据
    memory = generate_mock_memory_data()
    with open(data_dir / "memory.json", "w", encoding="utf-8") as f:
        json.dump(memory, f, ensure_ascii=False, indent=2)
    
    print(f"✅ Mock 数据已生成并保存到 {data_dir}")
    print(f"  - 追踪数据: {len(traces)} 条")
    print(f"  - 质量数据: 已生成")
    print(f"  - 内存数据: 已生成")

def load_mock_traces():
    """加载模拟追踪数据"""
    data_path = Path("data/mock/traces.json")
    if data_path.exists():
        with open(data_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def load_mock_quality():
    """加载模拟质量数据"""
    data_path = Path("data/mock/quality.json")
    if data_path.exists():
        with open(data_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None

def load_mock_memory():
    """加载模拟内存数据"""
    data_path = Path("data/mock/memory.json")
    if data_path.exists():
        with open(data_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None

if __name__ == "__main__":
    save_mock_data()
