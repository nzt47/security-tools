#!/usr/bin/env python3
"""
版本管理模块生产部署演示脚本

演示内容：
1. 生产环境部署配置
2. 灰度发布流程
3. 自动回滚触发
4. 部署历史查看
"""

import json
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.prompt_manager.deployment import (
    VersionDeploymentManager, DeploymentConfig, DeploymentStatus,
    RollbackTrigger, get_deployment_manager
)
from agent.prompt_manager.version_control import (
    VersionManager, get_version_manager
)
from agent.prompt_manager.storage import PromptStorage
from agent.cognitive.failure_collector import get_failure_collector
from agent.cognitive.logging_integration import configure_production_environment


def demo_production_deployment():
    """演示生产环境部署流程"""
    
    print("\n" + "="*70)
    print("📦 版本管理模块 - 生产环境部署演示")
    print("="*70)
    
    # 1. 初始化生产环境
    print("\n[步骤 1] 初始化生产环境...")
    print("-" * 50)
    
    collector = configure_production_environment()
    print("✅ 生产环境配置完成")
    print(f"   - 日志格式: JSON结构化")
    print(f"   - 失败收集: 已启用")
    print(f"   - 告警规则: {len(collector.get_alert_rules())} 条")
    
    # 2. 准备版本管理器
    print("\n[步骤 2] 初始化版本管理器...")
    print("-" * 50)
    
    import tempfile
    temp_dir = tempfile.mkdtemp(prefix="prod_deploy_")
    
    from agent.prompt_manager.storage import PromptRecord, PromptType
    
    storage = PromptStorage(os.path.join(temp_dir, "prompts.db"))
    
    # 创建一个提示词和多个版本
    prompt_id = "prod_demo_prompt"
    prompt = PromptRecord(
        prompt_id=prompt_id,
        name="生产演示提示词",
        content="初始版本：你是一个助手",
        prompt_type=PromptType.SYSTEM,
    )
    storage.save_prompt(prompt)
    
    vm = VersionManager(storage)
    v1 = vm.create_version(prompt_id, change_log="初始生产版本", author="admin")
    print(f"✅ 提示词创建完成: {prompt_id}")
    print(f"   - 当前版本: {v1.version_number}")
    
    # 创建新版本
    prompt.content = "v2版本：你是一个专业助手，请仔细回答问题"
    storage.save_prompt(prompt)
    v2 = vm.create_version(prompt_id, change_log="优化回答质量", author="admin")
    print(f"   - 待发布版本: {v2.version_number}")
    
    # 3. 配置部署管理器
    print("\n[步骤 3] 配置部署管理器...")
    print("-" * 50)
    
    dm = VersionDeploymentManager()
    
    # 注册健康检查器
    health_status = {"healthy": True, "message": "服务正常"}
    
    def health_check():
        return health_status["healthy"], health_status["message"]
    
    dm.register_health_checker(prompt_id, health_check)
    print("✅ 健康检查器已注册")
    
    # 4. 配置并启动部署
    print("\n[步骤 4] 启动灰度部署...")
    print("-" * 50)
    
    config = DeploymentConfig(
        prompt_id=prompt_id,
        target_version=v2.version_number,
        canary_enabled=True,
        canary_percentage=20,
        canary_duration_seconds=5,  # 演示用，缩短到5秒
        auto_rollback_enabled=True,
        max_error_rate=0.1,  # 10%错误率
        max_failure_count=5,
        health_check_interval=1,
        deployment_timeout=60,
    )
    
    # 模拟部署函数
    error_count = [0]
    total_requests = [0]
    
    def deploy_fn(pid, version):
        print(f"   🚀 正在部署版本 {version}...")
        time.sleep(1)
        print(f"   ✅ 版本 {version} 部署完成")
        return True
    
    record = dm.start_deployment(config, deploy_fn)
    
    print(f"✅ 部署已启动: {record.deployment_id}")
    print(f"   - 目标版本: {record.target_version}")
    print(f"   - 灰度比例: {config.canary_percentage}%")
    print(f"   - 自动回滚: {'启用' if config.auto_rollback_enabled else '禁用'}")
    
    # 5. 模拟流量
    print("\n[步骤 5] 模拟灰度流量...")
    print("-" * 50)
    
    for i in range(10):
        dm.report_success(prompt_id)
        total_requests[0] += 1
        print(f"   请求 #{i+1}: 成功")
        time.sleep(0.3)
    
    # 6. 等待灰度阶段完成
    print("\n[步骤 6] 等待灰度验证完成...")
    print("-" * 50)
    
    while True:
        current = dm.get_deployment(record.deployment_id)
        if current.status in [DeploymentStatus.SUCCESS, 
                              DeploymentStatus.FAILED,
                              DeploymentStatus.ROLLED_BACK]:
            break
        
        print(f"   状态: {current.status.value}, "
              f"灰度: {current.canary_percentage}%, "
              f"错误率: {current.error_rate:.2%}")
        time.sleep(1)
    
    # 7. 查看最终结果
    print("\n[步骤 7] 部署结果...")
    print("-" * 50)
    
    final = dm.get_deployment(record.deployment_id)
    print(f"   最终状态: {final.status.value}")
    print(f"   总请求数: {final.total_requests}")
    print(f"   错误数: {final.error_count}")
    print(f"   错误率: {final.error_rate:.2%}")
    
    if final.status == DeploymentStatus.SUCCESS:
        print("   ✅ 部署成功！")
    elif final.status == DeploymentStatus.ROLLED_BACK:
        print(f"   ⚠️  已回滚，原因: {final.rollback_reason}")
    else:
        print(f"   ❌ 部署失败")
    
    # 8. 演示自动回滚场景
    print("\n" + "="*70)
    print("🔄 自动回滚场景演示")
    print("="*70)
    
    print("\n[场景] 模拟高错误率触发自动回滚...")
    print("-" * 50)
    
    # 再创建一个新版本
    prompt.content = "v3版本：有问题的版本"
    storage.save_prompt(prompt)
    v3 = vm.create_version(prompt_id, change_log="有问题的版本", author="admin")
    
    config2 = DeploymentConfig(
        prompt_id=prompt_id,
        target_version=v3.version_number,
        canary_enabled=True,
        canary_percentage=10,
        canary_duration_seconds=10,
        auto_rollback_enabled=True,
        max_error_rate=0.3,  # 30%错误率就触发
        max_failure_count=3,
        health_check_interval=1,
        deployment_timeout=30,
    )
    
    record2 = dm.start_deployment(config2, deploy_fn)
    print(f"   部署ID: {record2.deployment_id}")
    print(f"   目标版本: {v3.version_number}")
    
    # 模拟高错误率
    print("\n   模拟高错误率流量...")
    time.sleep(1)
    
    for i in range(5):
        dm.report_error(prompt_id)
        print(f"   请求 #{i+1}: 失败 ❌")
        time.sleep(0.2)
    
    # 等待回滚完成
    print("\n   等待回滚...")
    while True:
        current = dm.get_deployment(record2.deployment_id)
        if current.status in [DeploymentStatus.ROLLED_BACK, DeploymentStatus.FAILED]:
            break
        print(f"   状态: {current.status.value}, 错误数: {current.error_count}")
        time.sleep(0.5)
    
    final2 = dm.get_deployment(record2.deployment_id)
    print(f"\n   最终状态: {final2.status.value}")
    if final2.rollback_trigger:
        print(f"   回滚触发: {final2.rollback_trigger.value}")
        print(f"   回滚原因: {final2.rollback_reason}")
        print("   ✅ 自动回滚成功！")
    
    # 9. 查看部署历史
    print("\n" + "="*70)
    print("📋 部署历史记录")
    print("="*70)
    
    history = dm.get_deployment_history(prompt_id)
    for i, h in enumerate(history):
        print(f"\n  [{i+1}] {h['deployment_id']}")
        print(f"      版本: {h['previous_version']} → {h['target_version']}")
        print(f"      状态: {h['status']}")
        if h['rollback_trigger']:
            print(f"      回滚触发: {h['rollback_trigger']}")
    
    # 10. 查看失败案例收集
    print("\n" + "="*70)
    print("📊 失败案例与告警统计")
    print("="*70)
    
    stats = collector.get_failure_statistics(hours=1)
    print(f"\n   总失败数: {stats['total_failures']}")
    print(f"   失败类型分布:")
    for ftype, count in stats['by_type'].items():
        print(f"     - {ftype}: {count}")
    print(f"   告警规则数: {len(stats['alert_rules'])}")
    
    print("\n" + "="*70)
    print("✅ 生产部署演示完成")
    print("="*70)


if __name__ == "__main__":
    demo_production_deployment()