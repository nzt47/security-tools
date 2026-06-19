#!/usr/bin/env python3
"""
Prometheus 监控系统集成示例

演示如何将 DigitalLife V2 功能的性能指标导出到 Prometheus

使用方式：
    python prometheus_example.py
    
然后访问 http://localhost:8000/metrics 查看指标

日志级别：
    --debug    启用详细调试日志
    --quiet    只显示错误日志
"""

import sys
import os
import time
import logging
import argparse
import signal
from pathlib import Path
from datetime import datetime

# 项目根目录
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# 默认日志配置
def setup_logging(debug=False, quiet=False):
    """设置日志级别"""
    if quiet:
        level = logging.ERROR
    elif debug:
        level = logging.DEBUG
    else:
        level = logging.INFO
    
    logging.basicConfig(
        level=level,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(project_root / 'prometheus_export.log', mode='a')
        ]
    )
    
    return logging.getLogger('prometheus_example')

logger = setup_logging()

# 尝试导入 Prometheus 导出器
try:
    from agent.prometheus_exporter import PrometheusMetricsExporter, create_exporter_from_digital_life
    PROMETHEUS_AVAILABLE = True
    logger.debug("[DEBUG] prometheus_client imported successfully")
except ImportError as e:
    logger.error(f"[ERROR] Failed to import prometheus_client: {e}")
    logger.error("[ERROR] Install with: pip install prometheus_client")
    PROMETHEUS_AVAILABLE = False

# 尝试导入 DigitalLife
try:
    from agent.digital_life import DigitalLife
    DIGITAL_LIFE_AVAILABLE = True
    logger.debug("[DEBUG] DigitalLife imported successfully")
except ImportError as e:
    logger.error(f"[ERROR] Failed to import DigitalLife: {e}")
    DIGITAL_LIFE_AVAILABLE = False


class PrometheusMonitor:
    """Prometheus 监控管理器
    
    提供完整的指标导出和监控功能，包括：
    - 自动指标上报
    - 周期性状态检查
    - 详细日志记录
    - 优雅关闭
    """
    
    def __init__(self, port=8000, debug=False):
        """初始化监控管理器
        
        Args:
            port: Prometheus HTTP 服务器端口
            debug: 是否启用调试模式
        """
        self.port = port
        self.debug = debug
        self.exporter = None
        self.dl = None
        self.running = False
        self.start_time = None
        self._shutdown_requested = False
        
        # 设置信号处理
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        logger.info(f"[INFO] PrometheusMonitor initialized (port={port}, debug={debug})")
    
    def _signal_handler(self, signum, frame):
        """处理关闭信号"""
        logger.info(f"[INFO] Received signal {signum}, shutting down...")
        self._shutdown_requested = True
        self.stop()
    
    def initialize(self):
        """初始化 DigitalLife 和 Prometheus 导出器
        
        Returns:
            bool: 是否成功初始化
        """
        logger.info("[INFO] Starting initialization...")
        
        # 1. 检查依赖
        if not PROMETHEUS_AVAILABLE:
            logger.error("[ERROR] prometheus_client not available")
            return False
        
        if not DIGITAL_LIFE_AVAILABLE:
            logger.error("[ERROR] DigitalLife not available")
            return False
        
        logger.debug("[DEBUG] All dependencies available")
        
        # 2. 创建 DigitalLife 实例
        logger.info("[INFO] Creating DigitalLife instance...")
        try:
            config = {
                "features": {
                    "v2_lifetrace": True,
                    "v2_persona": True,
                    "v2_distillation": True,
                }
            }
            self.dl = DigitalLife(config=config)
            logger.info("[OK] DigitalLife created successfully")
            
            # 记录 V2 模块状态
            features = self.dl.get_v2_features()
            logger.info(f"[INFO] V2 Features:")
            logger.info(f"       - LifeTrace: {features['v2_lifetrace']}")
            logger.info(f"       - Persona: {features['v2_persona']}")
            logger.info(f"       - Distillation: {features['v2_distillation']}")
            
        except Exception as e:
            logger.error(f"[ERROR] Failed to create DigitalLife: {e}")
            if self.debug:
                import traceback
                traceback.print_exc()
            return False
        
        # 3. 创建 Prometheus 导出器
        logger.info(f"[INFO] Creating Prometheus exporter on port {self.port}...")
        try:
            self.exporter = create_exporter_from_digital_life(self.dl, port=self.port)
            logger.info("[OK] Prometheus exporter created")
            
            # 记录模块加载时间（从性能报告中获取）
            perf_report = self.dl.get_performance_report()
            if perf_report and 'performance_summary' in perf_report:
                for module, stats in perf_report['performance_summary'].items():
                    module_name = module.replace('v2.', '')
                    duration_ms = stats.get('avg', 0)
                    logger.debug(f"[DEBUG] Module {module_name} load time: {duration_ms:.2f}ms")
                    self.exporter.record_module_load(module_name, duration_ms, True)
            
        except Exception as e:
            logger.error(f"[ERROR] Failed to create Prometheus exporter: {e}")
            if self.debug:
                import traceback
                traceback.print_exc()
            return False
        
        # 4. 启动 HTTP 服务器
        logger.info("[INFO] Starting Prometheus HTTP server...")
        try:
            self.exporter.start()
            logger.info(f"[OK] HTTP server started on port {self.port}")
            logger.info(f"[INFO] Metrics URL: http://localhost:{self.port}/metrics")
            
            # 验证服务器是否正常
            time.sleep(1)
            try:
                import urllib.request
                url = f"http://localhost:{self.port}/metrics"
                response = urllib.request.urlopen(url, timeout=5)
                if response.status == 200:
                    logger.info("[OK] Metrics endpoint verified (HTTP 200)")
                else:
                    logger.warning(f"[WARN] Metrics endpoint returned HTTP {response.status}")
            except Exception as e:
                logger.warning(f"[WARN] Could not verify metrics endpoint: {e}")
            
        except Exception as e:
            logger.error(f"[ERROR] Failed to start HTTP server: {e}")
            if self.debug:
                import traceback
                traceback.print_exc()
            return False
        
        self.start_time = datetime.now()
        logger.info(f"[OK] Initialization completed at {self.start_time}")
        
        return True
    
    def simulate_metrics(self):
        """模拟指标上报（用于演示和测试）"""
        logger.info("[INFO] Simulating metrics...")
        
        # 模拟交互
        logger.debug("[DEBUG] Recording simulated interactions...")
        for i in range(3):
            duration = 150.0 + (i * 10.0)
            self.exporter.record_interaction(duration)
            logger.info(f"[METRIC] Interaction #{i+1}: {duration:.2f}ms")
        
        # 模拟告警
        logger.debug("[DEBUG] Recording simulated alerts...")
        alerts = [
            ("rm -rf /", "critical"),
            ("git status", "safe"),
            ("chmod 777 /home", "warning"),
        ]
        
        for text, level in alerts:
            self.exporter.record_alert(level)
            logger.info(f"[METRIC] Alert: '{text}' -> {level}")
        
        # 更新记忆数量
        memory_stats = self.dl.get_memory_stats()
        if memory_stats.get("available"):
            count = memory_stats.get("total_memories", 0)
            self.exporter.set_memory_count(count)
            logger.info(f"[METRIC] Memory count: {count}")
        
        logger.info("[OK] Metrics simulation completed")
    
    def run_periodic_check(self, interval=30):
        """周期性检查系统状态
        
        Args:
            interval: 检查间隔（秒）
        """
        logger.info(f"[INFO] Starting periodic check (interval={interval}s)")
        
        check_count = 0
        while self.running and not self._shutdown_requested:
            check_count += 1
            logger.debug(f"[DEBUG] Periodic check #{check_count}")
            
            try:
                # 检查 V2 模块状态
                features = self.dl.get_v2_features()
                for module, enabled in features.items():
                    if module.startswith('v2_'):
                        module_name = module.replace('v2_', '')
                        self.exporter.set_module_enabled(module_name, enabled)
                        logger.debug(f"[DEBUG] Module {module_name}: {enabled}")
                
                # 更新记忆数量
                memory_stats = self.dl.get_memory_stats()
                if memory_stats.get("available"):
                    count = memory_stats.get("total_memories", 0)
                    self.exporter.set_memory_count(count)
                    logger.debug(f"[DEBUG] Memory count: {count}")
                
                # 记录运行时间
                if self.start_time:
                    elapsed = (datetime.now() - self.start_time).total_seconds()
                    logger.debug(f"[DEBUG] Running time: {elapsed:.1f}s")
                
                logger.info(f"[CHECK] Periodic check #{check_count} completed")
                
            except Exception as e:
                logger.error(f"[ERROR] Periodic check failed: {e}")
                if self.debug:
                    import traceback
                    traceback.print_exc()
            
            # 等待下一次检查
            for _ in range(interval):
                if self._shutdown_requested:
                    break
                time.sleep(1)
    
    def start(self, simulate=True, periodic=True, interval=30):
        """启动监控
        
        Args:
            simulate: 是否模拟指标上报
            periodic: 是否启用周期性检查
            interval: 周期性检查间隔（秒）
        """
        if not self.initialize():
            logger.error("[ERROR] Initialization failed, cannot start")
            return False
        
        self.running = True
        
        # 模拟指标上报
        if simulate:
            self.simulate_metrics()
        
        # 周期性检查
        if periodic:
            self.run_periodic_check(interval)
        else:
            # 简单保持运行
            logger.info("[INFO] Running in simple mode (no periodic check)")
            while self.running and not self._shutdown_requested:
                time.sleep(1)
        
        return True
    
    def stop(self):
        """停止监控"""
        logger.info("[INFO] Stopping Prometheus monitor...")
        
        self.running = False
        
        if self.exporter:
            try:
                self.exporter.stop()
                logger.info("[OK] Prometheus exporter stopped")
            except Exception as e:
                logger.error(f"[ERROR] Failed to stop exporter: {e}")
        
        # 记录运行统计
        if self.start_time:
            elapsed = (datetime.now() - self.start_time).total_seconds()
            logger.info(f"[INFO] Total running time: {elapsed:.1f}s")
        
        logger.info("[OK] Prometheus monitor stopped")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="DigitalLife V2 Prometheus Integration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python prometheus_example.py                  # 默认运行
    python prometheus_example.py --debug          # 调试模式
    python prometheus_example.py --quiet          # 安静模式
    python prometheus_example.py --no-simulate    # 不模拟指标
    python prometheus_example.py --interval 60    # 60秒检查间隔
"""
    )
    
    parser.add_argument(
        "--debug", "-d",
        action="store_true",
        help="Enable debug logging"
    )
    
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Only show error messages"
    )
    
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=8000,
        help="Prometheus HTTP server port (default: 8000)"
    )
    
    parser.add_argument(
        "--no-simulate",
        action="store_true",
        help="Do not simulate metrics on startup"
    )
    
    parser.add_argument(
        "--no-periodic",
        action="store_true",
        help="Do not run periodic checks"
    )
    
    parser.add_argument(
        "--interval", "-i",
        type=int,
        default=30,
        help="Periodic check interval in seconds (default: 30)"
    )
    
    args = parser.parse_args()
    
    # 更新日志配置
    global logger
    logger = setup_logging(debug=args.debug, quiet=args.quiet)
    
    print("\n" + "=" * 70)
    print("[INFO] DigitalLife V2 Prometheus Integration")
    print("=" * 70)
    print(f"[INFO] Port: {args.port}")
    print(f"[INFO] Debug: {args.debug}")
    print(f"[INFO] Periodic check: {not args.no_periodic} (interval={args.interval}s)")
    print("=" * 70 + "\n")
    
    # 创建监控管理器
    monitor = PrometheusMonitor(port=args.port, debug=args.debug)
    
    # 启动监控
    try:
        monitor.start(
            simulate=not args.no_simulate,
            periodic=not args.no_periodic,
            interval=args.interval
        )
    except KeyboardInterrupt:
        logger.info("[INFO] Keyboard interrupt received")
    except Exception as e:
        logger.error(f"[ERROR] Unexpected error: {e}")
        if args.debug:
            import traceback
            traceback.print_exc()
        return 1
    
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"\n[ERROR] Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)