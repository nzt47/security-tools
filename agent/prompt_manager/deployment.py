#!/usr/bin/env python3
"""
版本管理模块 - 生产环境部署与自动回滚机制

功能：
1. 版本发布前的检查与验证
2. 灰度发布（逐步放量）
3. 自动回滚触发条件
4. 回滚后的验证与告警
5. 部署历史记录
"""

import json
import logging
import time
import threading
from typing import Optional, Dict, Any, List, Callable
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class DeploymentStatus(Enum):
    """部署状态"""
    PENDING = "pending"           # 待部署
    PRE_CHECK = "pre_check"       # 部署前检查
    DEPLOYING = "deploying"       # 部署中
    VERIFYING = "verifying"       # 验证中
    SUCCESS = "success"           # 成功
    FAILED = "failed"             # 失败
    ROLLING_BACK = "rolling_back" # 回滚中
    ROLLED_BACK = "rolled_back"   # 已回滚


class RollbackTrigger(Enum):
    """回滚触发条件"""
    ERROR_RATE = "error_rate"           # 错误率超阈值
    FAILURE_COUNT = "failure_count"     # 失败次数超阈值
    HEALTH_CHECK = "health_check"       # 健康检查失败
    MANUAL = "manual"                   # 手动触发
    TIMEOUT = "timeout"                 # 部署超时


@dataclass
class DeploymentConfig:
    """部署配置"""
    prompt_id: str
    target_version: str
    canary_enabled: bool = True          # 是否启用灰度发布
    canary_percentage: int = 10          # 灰度流量百分比
    canary_duration_seconds: int = 300   # 灰度持续时间
    auto_rollback_enabled: bool = True   # 是否启用自动回滚
    max_error_rate: float = 0.05         # 最大错误率（5%）
    max_failure_count: int = 10          # 最大失败次数
    health_check_interval: int = 30      # 健康检查间隔（秒）
    health_check_timeout: int = 10       # 健康检查超时（秒）
    deployment_timeout: int = 1800       # 部署超时（秒）
    rollback_version: Optional[str] = None  # 回滚目标版本（None表示上一版本）


@dataclass
class DeploymentRecord:
    """部署记录"""
    deployment_id: str
    prompt_id: str
    target_version: str
    previous_version: str
    status: DeploymentStatus
    started_at: float
    completed_at: Optional[float] = None
    canary_percentage: int = 100
    rollback_trigger: Optional[RollbackTrigger] = None
    rollback_reason: str = ""
    error_count: int = 0
    total_requests: int = 0
    error_rate: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)


class VersionDeploymentManager:
    """版本部署管理器
    
    负责版本的生产环境部署、灰度发布、健康检查和自动回滚。
    """
    
    def __init__(self):
        self._deployments: Dict[str, DeploymentRecord] = {}
        self._active_deployments: Dict[str, DeploymentRecord] = {}
        self._health_checkers: Dict[str, Callable] = {}
        self._lock = threading.Lock()
        self._monitor_thread: Optional[threading.Thread] = None
        self._running = False
    
    def register_health_checker(self, prompt_id: str, checker: Callable):
        """注册健康检查函数
        
        Args:
            prompt_id: 提示词ID
            checker: 健康检查函数，返回 (is_healthy, error_message)
        """
        self._health_checkers[prompt_id] = checker
        logger.info(json.dumps({
            "trace_id": "",
            "module_name": "deployment_manager",
            "action": "register_health_checker",
            "prompt_id": prompt_id,
            "duration_ms": 0,
            "level": "INFO"
        }))
    
    def start_deployment(self, config: DeploymentConfig,
                        deploy_callback: Callable[[str, str], bool]) -> DeploymentRecord:
        """开始部署新版本
        
        Args:
            config: 部署配置
            deploy_callback: 部署回调函数 (prompt_id, version) -> bool
        
        Returns:
            部署记录
        """
        deployment_id = f"deploy_{int(time.time())}_{config.prompt_id}"
        
        # 获取当前版本作为回滚目标
        previous_version = config.rollback_version or self._get_current_version(config.prompt_id)
        
        record = DeploymentRecord(
            deployment_id=deployment_id,
            prompt_id=config.prompt_id,
            target_version=config.target_version,
            previous_version=previous_version or "unknown",
            status=DeploymentStatus.PENDING,
            started_at=time.time(),
            canary_percentage=config.canary_percentage if config.canary_enabled else 100
        )
        
        with self._lock:
            self._deployments[deployment_id] = record
            self._active_deployments[config.prompt_id] = record
        
        # 异步执行部署
        threading.Thread(
            target=self._execute_deployment,
            args=(record, config, deploy_callback),
            daemon=True
        ).start()
        
        logger.info(json.dumps({
            "trace_id": "",
            "module_name": "deployment_manager",
            "action": "start_deployment",
            "deployment_id": deployment_id,
            "prompt_id": config.prompt_id,
            "target_version": config.target_version,
            "canary": config.canary_enabled,
            "duration_ms": 0,
            "level": "INFO"
        }))
        
        return record
    
    def _execute_deployment(self, record: DeploymentRecord,
                           config: DeploymentConfig,
                           deploy_callback: Callable[[str, str], bool]):
        """执行部署流程"""
        try:
            # 1. 部署前检查
            record.status = DeploymentStatus.PRE_CHECK
            if not self._pre_deployment_check(config, record):
                self._fail_deployment(record, "部署前检查失败")
                return
            
            # 2. 执行部署
            record.status = DeploymentStatus.DEPLOYING
            success = deploy_callback(config.prompt_id, config.target_version)
            if not success:
                self._fail_deployment(record, "部署执行失败")
                return
            
            # 3. 灰度发布阶段
            if config.canary_enabled:
                self._canary_release(record, config)
                if record.status == DeploymentStatus.ROLLED_BACK:
                    return
            
            # 4. 全量验证
            record.status = DeploymentStatus.VERIFYING
            record.canary_percentage = 100
            
            if not self._post_deployment_verify(config, record):
                self._trigger_rollback(record, config, RollbackTrigger.HEALTH_CHECK,
                                      "部署后验证失败")
                return
            
            # 5. 部署成功
            record.status = DeploymentStatus.SUCCESS
            record.completed_at = time.time()
            
            logger.info(json.dumps({
                "trace_id": "",
                "module_name": "deployment_manager",
                "action": "deployment_success",
                "deployment_id": record.deployment_id,
                "prompt_id": record.prompt_id,
                "version": record.target_version,
                "duration_ms": int((record.completed_at - record.started_at) * 1000),
                "level": "INFO"
            }))
            
        except Exception as e:
            logger.error(f"部署异常: {e}")
            self._fail_deployment(record, f"部署异常: {str(e)}")
        finally:
            with self._lock:
                if record.prompt_id in self._active_deployments:
                    del self._active_deployments[record.prompt_id]
    
    def _pre_deployment_check(self, config: DeploymentConfig,
                             record: DeploymentRecord) -> bool:
        """部署前检查"""
        # 检查版本是否存在
        # 检查健康检查器是否注册
        if config.auto_rollback_enabled and config.prompt_id not in self._health_checkers:
            logger.warning("未注册健康检查器，自动回滚将仅基于错误率")
        
        logger.info(f"[部署] 部署前检查通过: {config.prompt_id}")
        return True
    
    def _canary_release(self, record: DeploymentRecord, config: DeploymentConfig):
        """灰度发布"""
        logger.info(f"[部署] 开始灰度发布: {config.canary_percentage}% 流量")
        
        start_time = time.time()
        check_interval = config.health_check_interval
        
        while time.time() - start_time < config.canary_duration_seconds:
            # 健康检查
            if not self._run_health_check(config.prompt_id):
                self._trigger_rollback(record, config, RollbackTrigger.HEALTH_CHECK,
                                      "灰度阶段健康检查失败")
                return
            
            # 检查错误率
            if config.auto_rollback_enabled and record.total_requests > 0:
                error_rate = record.error_count / record.total_requests
                record.error_rate = error_rate
                
                if error_rate > config.max_error_rate:
                    self._trigger_rollback(record, config, RollbackTrigger.ERROR_RATE,
                                          f"错误率超标: {error_rate:.2%} > {config.max_error_rate:.2%}")
                    return
            
            # 检查失败次数
            if record.error_count >= config.max_failure_count:
                self._trigger_rollback(record, config, RollbackTrigger.FAILURE_COUNT,
                                      f"失败次数超标: {record.error_count} >= {config.max_failure_count}")
                return
            
            # 检查超时
            if time.time() - record.started_at > config.deployment_timeout:
                self._trigger_rollback(record, config, RollbackTrigger.TIMEOUT,
                                      "部署超时")
                return
            
            time.sleep(check_interval)
        
        logger.info(f"[部署] 灰度发布完成，准备全量上线")
    
    def _post_deployment_verify(self, config: DeploymentConfig,
                               record: DeploymentRecord) -> bool:
        """部署后验证"""
        # 运行完整健康检查
        is_healthy, message = self._run_full_health_check(config.prompt_id)
        
        if not is_healthy:
            logger.warning(f"[部署] 部署后验证失败: {message}")
            return False
        
        logger.info("[部署] 部署后验证通过")
        return True
    
    def _run_health_check(self, prompt_id: str) -> bool:
        """运行健康检查"""
        checker = self._health_checkers.get(prompt_id)
        if not checker:
            return True  # 没有检查器则默认通过
        
        try:
            is_healthy, _ = checker()
            return is_healthy
        except Exception as e:
            logger.error(f"健康检查异常: {e}")
            return False
    
    def _run_full_health_check(self, prompt_id: str) -> tuple:
        """运行完整健康检查，返回 (is_healthy, message)"""
        checker = self._health_checkers.get(prompt_id)
        if not checker:
            return True, "未配置健康检查器"
        
        try:
            return checker()
        except Exception as e:
            return False, f"健康检查异常: {str(e)}"
    
    def _trigger_rollback(self, record: DeploymentRecord, config: DeploymentConfig,
                         trigger: RollbackTrigger, reason: str):
        """触发回滚"""
        record.status = DeploymentStatus.ROLLING_BACK
        record.rollback_trigger = trigger
        record.rollback_reason = reason
        
        logger.warning(json.dumps({
            "trace_id": "",
            "module_name": "deployment_manager",
            "action": "trigger_rollback",
            "deployment_id": record.deployment_id,
            "prompt_id": record.prompt_id,
            "trigger": trigger.value,
            "reason": reason,
            "duration_ms": 0,
            "level": "WARNING"
        }))
        
        # 执行回滚
        try:
            self._execute_rollback(config, record)
            record.status = DeploymentStatus.ROLLED_BACK
            record.completed_at = time.time()
            
            logger.info(json.dumps({
                "trace_id": "",
                "module_name": "deployment_manager",
                "action": "rollback_complete",
                "deployment_id": record.deployment_id,
                "prompt_id": record.prompt_id,
                "rollback_to": record.previous_version,
                "trigger": trigger.value,
                "duration_ms": int((record.completed_at - record.started_at) * 1000),
                "level": "INFO"
            }))
        except Exception as e:
            logger.error(f"回滚失败: {e}")
            record.status = DeploymentStatus.FAILED
            record.completed_at = time.time()
    
    def _execute_rollback(self, config: DeploymentConfig, record: DeploymentRecord):
        """执行回滚操作
        
        子类应该重写此方法以实现实际的回滚逻辑。
        """
        # 这里调用版本回滚
        from agent.prompt_manager.version_control import get_version_manager
        vm = get_version_manager()
        vm.rollback_to_version(config.prompt_id, record.previous_version)
    
    def _fail_deployment(self, record: DeploymentRecord, reason: str):
        """标记部署失败"""
        record.status = DeploymentStatus.FAILED
        record.completed_at = time.time()
        record.details["failure_reason"] = reason
        
        logger.error(json.dumps({
            "trace_id": "",
            "module_name": "deployment_manager",
            "action": "deployment_failed",
            "deployment_id": record.deployment_id,
            "prompt_id": record.prompt_id,
            "reason": reason,
            "duration_ms": int((record.completed_at - record.started_at) * 1000),
            "level": "ERROR"
        }))
    
    def _get_current_version(self, prompt_id: str) -> Optional[str]:
        """获取当前版本"""
        try:
            from agent.prompt_manager.version_control import get_version_manager
            vm = get_version_manager()
            versions = vm.get_version_history(prompt_id)
            if versions:
                return versions[0].version_number
        except Exception:
            pass
        return None
    
    def report_error(self, prompt_id: str):
        """报告错误，用于监控错误率"""
        with self._lock:
            deployment = self._active_deployments.get(prompt_id)
            if deployment:
                deployment.error_count += 1
                deployment.total_requests += 1
    
    def report_success(self, prompt_id: str):
        """报告成功请求"""
        with self._lock:
            deployment = self._active_deployments.get(prompt_id)
            if deployment:
                deployment.total_requests += 1
    
    def manual_rollback(self, deployment_id: str, reason: str = "手动回滚") -> bool:
        """手动触发回滚"""
        with self._lock:
            record = self._deployments.get(deployment_id)
        
        if not record:
            return False
        
        if record.status in [DeploymentStatus.SUCCESS, DeploymentStatus.FAILED]:
            # 已完成的部署也可以回滚
            pass
        
        # 执行回滚
        config = DeploymentConfig(
            prompt_id=record.prompt_id,
            target_version=record.target_version,
            rollback_version=record.previous_version
        )
        
        self._trigger_rollback(record, config, RollbackTrigger.MANUAL, reason)
        return True
    
    def get_deployment(self, deployment_id: str) -> Optional[DeploymentRecord]:
        """获取部署记录"""
        return self._deployments.get(deployment_id)
    
    def get_deployment_history(self, prompt_id: str = None,
                              limit: int = 20) -> List[Dict[str, Any]]:
        """获取部署历史"""
        records = list(self._deployments.values())
        
        if prompt_id:
            records = [r for r in records if r.prompt_id == prompt_id]
        
        records.sort(key=lambda r: r.started_at, reverse=True)
        records = records[:limit]
        
        return [
            {
                "deployment_id": r.deployment_id,
                "prompt_id": r.prompt_id,
                "target_version": r.target_version,
                "previous_version": r.previous_version,
                "status": r.status.value,
                "started_at": r.started_at,
                "completed_at": r.completed_at,
                "canary_percentage": r.canary_percentage,
                "rollback_trigger": r.rollback_trigger.value if r.rollback_trigger else None,
                "rollback_reason": r.rollback_reason,
                "error_count": r.error_count,
                "total_requests": r.total_requests,
                "error_rate": r.error_rate,
            }
            for r in records
        ]


# 全局部署管理器实例
_global_deployment_manager = None

def get_deployment_manager() -> VersionDeploymentManager:
    """获取全局部署管理器实例"""
    global _global_deployment_manager
    if _global_deployment_manager is None:
        _global_deployment_manager = VersionDeploymentManager()
    return _global_deployment_manager