#!/usr/bin/env python3
"""模拟灰度发布 10% 后的验证工程运行日志

模拟场景：
- 10% 的请求会启用验证工程
- 90% 的请求走原有流程
- 保守模式下，验证失败只记录日志不阻断
"""

import json
import time
import random
import logging
from typing import Dict, Any

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)8s] %(name)-30s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("verification_simulation")

# 模拟配置
ROLLOUT_PERCENTAGE = 10  # 灰度发布比例
CONSERVATIVE_MODE = True  # 保守模式

def should_enable_verification(trace_id: str) -> bool:
    """根据灰度比例决定是否启用验证
    
    使用 trace_id 的哈希值来决定，确保同一 trace_id 的行为一致
    """
    # 简化的哈希算法：取 trace_id 最后两位数字
    hash_value = int(trace_id[-2:]) if trace_id[-2:].isdigit() else random.randint(0, 99)
    return hash_value < ROLLOUT_PERCENTAGE


def simulate_request(request_id: int) -> Dict[str, Any]:
    """模拟单个请求的处理流程"""
    trace_id = f"trace-{request_id:04d}"
    user_query = f"用户查询 #{request_id}"
    
    # 生成模拟响应
    response = {
        "output_type": "text_response",
        "content": f"这是对查询 #{request_id} 的智能回复。" * 5,
        "confidence": random.uniform(0.7, 0.95)
    }
    
    result = {
        "trace_id": trace_id,
        "request_id": request_id,
        "verification_enabled": False,
        "schema_valid": None,
        "critic_score": None,
        "critic_passed": None,
        "final_status": "success",
        "duration_ms": 0
    }
    
    start_time = time.time()
    
    # 决定是否启用验证工程（灰度发布）
    if should_enable_verification(trace_id):
        result["verification_enabled"] = True
        
        # ========== Schema 校验阶段 ==========
        logger.info(json.dumps({
            "trace_id": trace_id,
            "module_name": "output_schema",
            "action": "validate",
            "duration_ms": round(random.uniform(0.1, 0.5), 2),
            "result": "success",
            "output_type": "text_response",
            "rollout": "enabled"
        }, ensure_ascii=False))
        
        result["schema_valid"] = True
        
        # ========== Critic 评估阶段 ==========
        critic_score = random.randint(60, 85)
        result["critic_score"] = critic_score
        result["critic_passed"] = critic_score >= 70
        
        logger.info(json.dumps({
            "trace_id": trace_id,
            "module_name": "critic",
            "action": "evaluate",
            "duration_ms": round(random.uniform(1.0, 3.0), 2),
            "mode": "rule_based",
            "threshold": 70,
            "overall_score": critic_score,
            "passed": result["critic_passed"],
            "dimension_scores": {
                "factual_accuracy": random.randint(80, 100),
                "completeness": random.randint(60, 90),
                "relevance": random.randint(50, 80),
                "logic": random.randint(70, 90),
                "clarity": random.randint(60, 85)
            },
            "rollout": "enabled"
        }, ensure_ascii=False))
        
        # 保守模式：即使 Critic 未通过，也只记录警告
        if not result["critic_passed"] and CONSERVATIVE_MODE:
            logger.warning(json.dumps({
                "trace_id": trace_id,
                "module_name": "critic",
                "action": "conservative_mode_degrade",
                "warning": "评估未通过，保守模式下降级处理",
                "overall_score": critic_score,
                "threshold": 70,
                "degraded_response": "已记录质量问题，继续返回原始响应"
            }, ensure_ascii=False))
        
        # ========== 失败分析阶段（如果有问题）==========
        if not result["critic_passed"]:
            logger.info(json.dumps({
                "trace_id": trace_id,
                "module_name": "failure_analysis",
                "action": "classify_failure",
                "duration_ms": round(random.uniform(0.5, 2.0), 2),
                "failure_type": "quality_issue",
                "severity": "medium",
                "message": f"Critic 评分低于阈值: {critic_score} < 70",
                "suggested_fix": "建议优化响应的相关性和完整性"
            }, ensure_ascii=False))
    
    else:
        # 未启用验证工程（90% 流量）
        result["verification_enabled"] = False
        
        logger.info(json.dumps({
            "trace_id": trace_id,
            "module_name": "verification_gate",
            "action": "skip_verification",
            "duration_ms": 0.01,
            "reason": "rollout_percentage_check",
            "rollout_percentage": ROLLOUT_PERCENTAGE,
            "hash_value": int(trace_id[-2:]) if trace_id[-2:].isdigit() else "N/A",
            "rollout": "disabled"
        }, ensure_ascii=False))
    
    # 模拟响应返回
    result["duration_ms"] = round((time.time() - start_time) * 1000, 2)
    
    logger.info(json.dumps({
        "trace_id": trace_id,
        "module_name": "response_handler",
        "action": "return_response",
        "duration_ms": result["duration_ms"],
        "verification_enabled": result["verification_enabled"],
        "final_status": result["final_status"]
    }, ensure_ascii=False))
    
    return result


def run_simulation(num_requests: int = 20):
    """运行模拟"""
    logger.info("=" * 70)
    logger.info("[模拟开始] 验证工程灰度发布 10% 运行日志模拟")
    logger.info("=" * 70)
    logger.info(f"配置: rollout_percentage={ROLLOUT_PERCENTAGE}%, conservative_mode={CONSERVATIVE_MODE}")
    logger.info("")
    
    results = []
    enabled_count = 0
    
    for i in range(1, num_requests + 1):
        logger.info(f"\n--- 请求 #{i} ---")
        result = simulate_request(i)
        results.append(result)
        if result["verification_enabled"]:
            enabled_count += 1
    
    # 统计汇总
    logger.info("\n" + "=" * 70)
    logger.info("[模拟结束] 运行统计汇总")
    logger.info("=" * 70)
    
    actual_percentage = (enabled_count / num_requests) * 100
    logger.info(f"总请求数: {num_requests}")
    logger.info(f"启用验证的请求: {enabled_count} ({actual_percentage:.1f}%)")
    logger.info(f"未启用验证的请求: {num_requests - enabled_count} ({100 - actual_percentage:.1f}%)")
    
    # 验证启用请求的统计
    enabled_results = [r for r in results if r["verification_enabled"]]
    if enabled_results:
        passed_count = sum(1 for r in enabled_results if r["critic_passed"])
        avg_score = sum(r["critic_score"] for r in enabled_results) / len(enabled_results)
        
        logger.info(f"\n验证启用请求统计:")
        logger.info(f"  Critic 通过数: {passed_count}/{len(enabled_results)}")
        logger.info(f"  平均 Critic 评分: {avg_score:.1f}")
        logger.info(f"  保守模式生效: {CONSERVATIVE_MODE} (失败不阻断)")
    
    return results


if __name__ == "__main__":
    results = run_simulation(num_requests=20)
    
    # 输出详细结果
    print("\n\n详细结果:")
    for r in results:
        status = "✓ 验证启用" if r["verification_enabled"] else "○ 验证跳过"
        if r["verification_enabled"] and r["critic_passed"]:
            status += " | Critic通过"
        elif r["verification_enabled"] and not r["critic_passed"]:
            status += " | Critic未通过(保守模式)"
        print(f"  {r['trace_id']}: {status}")