
"""DigitalLife v2 — 云枢的数字生命主类（P3.1 并行初始化优化版）

整合了 LifeTrace 记忆系统和 Persona 人格系统，
实现更强大的感知-认知-行动闭环。

P3.1 优化：
- 使用线程池并行初始化独立模块
- 大幅降低初始化时间
"""

import logging
import time
import os
import json
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from concurrent.futures import ThreadPoolExecutor, as_completed

# 性能监控
from .performance_monitor import InitPerformanceTracker

# 现有模块
from sensor import BodySensor
from sensor.sensor_reading import SensorReading
from cognitive import PromptInjector as OldPromptInjector
from cognitive import PromptConfig
from memory import MemoryManager, BlackBox
from memory.llm_service import LLMService, LLMServiceError
from .behavior_controller import BehaviorController, BehaviorMode
from .permission_system import PermissionSystem, PermissionResult
from . import tools

# 新模块：LifeTrace 和 Persona
from lifetrace import TraceRecorder, MemoryRetriever
from persona import PersonaModel, PersonaInjector, PersonalityPreferenceExtractor
from persona.distiller import PersonaDistiller, DistillationStrategy, DistillationConfig

logger = logging.getLogger(__name__)


class DigitalLifeV2:
    """云枢主类 v2.0 — 整合了 LifeTrace 和 Persona 系统
    
    新架构：
      感知层 → LifeTrace 记录 → Persona 注入 → LLM 思考 → 行动 → 反思
    
    P3.1 优化：并行初始化
    """

    def __init__(self, config: dict = None, enable_parallel_init: bool = True):
        """
        初始化数字生命 v2 —— 唤醒云枢
        
        P3.1 优化：启用并行初始化，可将初始化时间降低 30-40%
        
        Args:
            config: 配置字典
            enable_parallel_init: 是否启用并行初始化（默认 True）
        """
        config = config or {}
        
        # 创建性能追踪器
        self._perf_tracker = InitPerformanceTracker() if enable_parallel_init else None
        
        # ── 并行初始化优化（P3.1）──
        if enable_parallel_init:
            self._init_parallel(config)
        else:
            self._init_sequential(config)
        
        # ── 运行状态 ──
        self._running = False
        self._current_mode = BehaviorMode.NORMAL
        self._last_health_check = 0.0
        self._health_check_interval = config.get("behavior", {}).get("check_interval", 30)
        self._session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._interaction_count = 0
        self._reflection_history: list[dict] = []
        self._started_at = None

        # ── 数据流管理 ──
        self._data_flow_enabled = config.get("data_flow", {}).get("enabled", True)

        # ── 启动提示 ──
        logger.info("=" * 50)
        logger.info("  云枢 v2.0 启动配置:")
        logger.info(f"  LLM:     {self._llm.provider if self._llm else '未配置'}")
        logger.info(f"  Model:   {self._llm.model if self._llm else 'N/A'}")
        logger.info(f"  人格:    {self._persona_model.persona.get('persona_id', 'default')}")
        logger.info(f"  会话:    {self._session_id}")
        logger.info(f"  并行初始化: {'启用' if enable_parallel_init else '禁用'}")
        logger.info("=" * 50)
        
        # 打印性能总结
        if self._perf_tracker:
            self._perf_tracker.print_summary()

    def _init_parallel(self, config: dict):
        """
        并行初始化所有模块（P3.1 核心优化）
        
        优化策略：
          1. 第一阶段：并行初始化无依赖的模块（BodySensor, TraceRecorder, PersonaModel 等）
          2. 第二阶段：并行初始化有依赖的模块（MemoryRetriever, PersonaInjector 等）
        """
        logger.info("[P3.1] 启用并行初始化优化...")
        
        # 第一阶段：初始化无依赖的模块
        logger.info("[P3.1] 阶段1：并行初始化核心模块...")
        phase1_start = time.time()
        
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {}
            
            # 提交第一阶段任务
            futures['body'] = executor.submit(self._init_body_sensor, config)
            futures['trace_recorder'] = executor.submit(self._init_trace_recorder, config)
            futures['persona_model'] = executor.submit(self._init_persona_model, config)
            futures['persona_extractor'] = executor.submit(self._init_persona_extractor, config)
            futures['distiller'] = executor.submit(self._init_persona_distiller, config)
            futures['old_memory'] = executor.submit(self._init_old_memory, config)
            futures['old_injector'] = executor.submit(self._init_old_injector, config)
            futures['behavior'] = executor.submit(self._init_behavior, config)
            futures['permission'] = executor.submit(self._init_permission, config)
            
            # 等待所有任务完成
            for name, future in futures.items():
                try:
                    result = future.result()
                    logger.info(f"[P3.1] 阶段1模块 {name} 初始化完成")
                except Exception as e:
                    logger.error(f"[P3.1] 阶段1模块 {name} 初始化失败: {e}")
        
        phase1_elapsed = (time.time() - phase1_start) * 1000
        logger.info(f"[P3.1] 阶段1完成，耗时: {phase1_elapsed:.2f}ms")
        
        # 第二阶段：初始化有依赖的模块
        logger.info("[P3.1] 阶段2：并行初始化依赖模块...")
        phase2_start = time.time()
        
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {}
            
            # MemoryRetriever 依赖 TraceRecorder
            futures['memory_retriever'] = executor.submit(
                self._init_memory_retriever
            )
            
            # PersonaInjector 依赖 PersonaModel
            futures['persona_injector'] = executor.submit(
                self._init_persona_injector
            )
            
            # 注册内置工具（无依赖）
            futures['tools'] = executor.submit(
                self._register_builtin_tools
            )
            
            # 等待所有任务完成
            for name, future in futures.items():
                try:
                    result = future.result()
                    logger.info(f"[P3.1] 阶段2模块 {name} 初始化完成")
                except Exception as e:
                    logger.error(f"[P3.1] 阶段2模块 {name} 初始化失败: {e}")
        
        phase2_elapsed = (time.time() - phase2_start) * 1000
        logger.info(f"[P3.1] 阶段2完成，耗时: {phase2_elapsed:.2f}ms")
        
        # 计算总时间
        if self._perf_tracker:
            self._perf_tracker.finish_module("并行初始化", success=True)
        
        logger.info(f"[P3.1] 并行初始化完成！总耗时: {phase1_elapsed + phase2_elapsed:.2f}ms")

    def _init_body_sensor(self, config: dict):
        """初始化感知层（BodySensor）"""
        if self._perf_tracker:
            self._perf_tracker.start_module("BodySensor")
        
        sensor_cfg = config.get("sensor", {})
        self.body: BodySensor = BodySensor(
            watch_dirs=sensor_cfg.get("watch_dirs"),
            enable_change_detection=sensor_cfg.get("enable_change_detection", True),
            enable_event_monitor=sensor_cfg.get("enable_event_monitor", True),
            lazy_load=sensor_cfg.get("lazy_load", True),  # 启用懒加载
        )
        
        if self._perf_tracker:
            self._perf_tracker.finish_module("BodySensor")
        logger.info("[ok] 身体（BodySensor）已激活（懒加载模式）")

    def _init_trace_recorder(self, config: dict):
        """初始化 LifeTrace 记录器"""
        if self._perf_tracker:
            self._perf_tracker.start_module("TraceRecorder")
        
        lifetrace_cfg = config.get("lifetrace", {})
        self._trace_recorder = TraceRecorder(
            data_dir=lifetrace_cfg.get("data_dir", "./data/lifetrace")
        )
        
        if self._perf_tracker:
            self._perf_tracker.finish_module("TraceRecorder")
        logger.info("[ok] TraceRecorder（记忆记录器）已激活")

    def _init_memory_retriever(self):
        """初始化记忆检索器（依赖 TraceRecorder）"""
        if self._perf_tracker:
            self._perf_tracker.start_module("MemoryRetriever")
        
        self._memory_retriever = MemoryRetriever(
            self._trace_recorder.source_tree,
            self._trace_recorder.topic_tree,
            self._trace_recorder.global_tree,
        )
        
        if self._perf_tracker:
            self._perf_tracker.finish_module("MemoryRetriever")
        logger.info("[ok] MemoryRetriever（记忆检索器）已激活")

    def _init_persona_model(self, config: dict):
        """初始化 Persona 模型"""
        if self._perf_tracker:
            self._perf_tracker.start_module("PersonaModel")
        
        persona_cfg = config.get("persona", {})
        self._persona_model = PersonaModel(
            persona_path=persona_cfg.get("persona_path")
        )
        
        if self._perf_tracker:
            self._perf_tracker.finish_module("PersonaModel")
        logger.info("[ok] PersonaModel（人格模型）已激活")

    def _init_persona_injector(self):
        """初始化 Persona 注入器（依赖 PersonaModel）"""
        if self._perf_tracker:
            self._perf_tracker.start_module("PersonaInjector")
        
        self._persona_injector = PersonaInjector(self._persona_model)
        
        if self._perf_tracker:
            self._perf_tracker.finish_module("PersonaInjector")
        logger.info("[ok] PersonaInjector（人格注入器）已激活")

    def _init_persona_extractor(self, config: dict):
        """初始化人格偏好提取器"""
        if self._perf_tracker:
            self._perf_tracker.start_module("PersonalityPreferenceExtractor")
        
        distillation_cfg = config.get("distillation", {})
        self._persona_extractor = PersonalityPreferenceExtractor(
            data_dir=distillation_cfg.get("data_dir", "./data/persona")
        )
        self._distillation_enabled = distillation_cfg.get("enabled", True)
        self._distillation_interval = distillation_cfg.get("interval", 10)
        
        if self._perf_tracker:
            self._perf_tracker.finish_module("PersonalityPreferenceExtractor")
        logger.info("[ok] PersonalityPreferenceExtractor（偏好提取器）已激活")

    def _init_persona_distiller(self, config: dict):
        """初始化 Persona 蒸馏器"""
        if self._perf_tracker:
            self._perf_tracker.start_module("PersonaDistiller")
        
        distillation_cfg = config.get("distillation", {})
        distiller_cfg = distillation_cfg.get("distiller", {})
        distillation_config = DistillationConfig(
            strategy=DistillationStrategy(distiller_cfg.get("strategy", "balanced")),
            learning_rate=distiller_cfg.get("learning_rate", 0.1),
            min_confidence=distiller_cfg.get("min_confidence", 0.3),
            stability_weight=distiller_cfg.get("stability_weight", 0.7),
            adaptation_weight=distiller_cfg.get("adaptation_weight", 0.3),
        )
        self._persona_distiller = PersonaDistiller(
            persona_model=self._persona_model,
            config=distillation_config,
            lazy_load=True  # 启用懒加载，加速初始化
        )
        self._distiller_enabled = distillation_cfg.get("distiller_enabled", True)
        
        if self._perf_tracker:
            self._perf_tracker.finish_module("PersonaDistiller")
        logger.info("[ok] PersonaDistiller（人格蒸馏器）已激活")

    def _init_old_memory(self, config: dict):
        """初始化旧版记忆管理器（兼容层）"""
        if self._perf_tracker:
            self._perf_tracker.start_module("MemoryManager")
        
        memory_cfg = config.get("memory", {})
        self._old_memory: MemoryManager = MemoryManager(memory_cfg)
        self._llm: Optional[LLMService] = self._old_memory._llm_service
        
        if self._perf_tracker:
            self._perf_tracker.finish_module("MemoryManager")
        logger.info("[ok] MemoryManager（记忆管理器）已激活")

    def _init_old_injector(self, config: dict):
        """初始化旧版 Prompt 注入器（兼容层）"""
        if self._perf_tracker:
            self._perf_tracker.start_module("PromptInjector")
        
        cognitive_cfg = config.get("cognitive", {})
        prompt_config = PromptConfig(config_path=cognitive_cfg.get("config_path"))
        self._old_injector: OldPromptInjector = OldPromptInjector(config=prompt_config)
        
        if self._perf_tracker:
            self._perf_tracker.finish_module("PromptInjector")
        logger.info("[ok] PromptInjector（Prompt 注入器）已激活")

    def _init_behavior(self, config: dict):
        """初始化行为控制器"""
        if self._perf_tracker:
            self._perf_tracker.start_module("BehaviorController")
        
        self._behavior: BehaviorController = BehaviorController()
        
        if self._perf_tracker:
            self._perf_tracker.finish_module("BehaviorController")
        logger.info("[ok] BehaviorController（行为控制器）已激活")

    def _init_permission(self, config: dict):
        """初始化权限系统"""
        if self._perf_tracker:
            self._perf_tracker.start_module("PermissionSystem")
        
        self._permission: PermissionSystem = PermissionSystem(
            backup_dir=config.get("backup_dir", "./.backups"),
        )
        
        if self._perf_tracker:
            self._perf_tracker.finish_module("PermissionSystem")
        logger.info("[ok] PermissionSystem（权限系统）已激活")

    def _init_sequential(self, config: dict):
        """
        顺序初始化（保持向后兼容）
        
        如果并行初始化出现问题，可以回退到此模式
        """
        logger.info("[P3.1] 回退到顺序初始化（兼容模式）...")
        
        # ── 1. 我的身体：感知层（懒加载优化）──
        self._init_body_sensor(config)
        
        # ── 2. LifeTrace：海马体记忆系统 ──
        lifetrace_cfg = config.get("lifetrace", {})
        self._trace_recorder = TraceRecorder(
            data_dir=lifetrace_cfg.get("data_dir", "./data/lifetrace")
        )
        self._memory_retriever = MemoryRetriever(
            self._trace_recorder.source_tree,
            self._trace_recorder.topic_tree,
            self._trace_recorder.global_tree,
        )
        logger.info("[ok] 海马体（LifeTrace）已激活")

        # ── 3. Persona：人格系统 ──
        persona_cfg = config.get("persona", {})
        self._persona_model = PersonaModel(
            persona_path=persona_cfg.get("persona_path")
        )
        self._persona_injector = PersonaInjector(self._persona_model)
        logger.info("[ok] 人格（Persona）已激活")
        
        # ── 3.5 人格蒸馏：自动学习用户偏好 ──
        distillation_cfg = config.get("distillation", {})
        self._persona_extractor = PersonalityPreferenceExtractor(
            data_dir=distillation_cfg.get("data_dir", "./data/persona")
        )
        self._distillation_enabled = distillation_cfg.get("enabled", True)
        self._distillation_interval = distillation_cfg.get("interval", 10)
        
        # ── 3.6 PersonaDistiller：人格蒸馏器 ──
        distiller_cfg = distillation_cfg.get("distiller", {})
        distillation_config = DistillationConfig(
            strategy=DistillationStrategy(distiller_cfg.get("strategy", "balanced")),
            learning_rate=distiller_cfg.get("learning_rate", 0.1),
            min_confidence=distiller_cfg.get("min_confidence", 0.3),
            stability_weight=distiller_cfg.get("stability_weight", 0.7),
            adaptation_weight=distiller_cfg.get("adaptation_weight", 0.3),
        )
        self._persona_distiller = PersonaDistiller(
            persona_model=self._persona_model,
            config=distillation_config,
            lazy_load=True
        )
        self._distiller_enabled = distillation_cfg.get("distiller_enabled", True)
        logger.info("[ok] 人格蒸馏器（PersonaDistiller）已激活")

        # ── 4. 旧记忆管理器（兼容层）─
        memory_cfg = config.get("memory", {})
        self._old_memory: MemoryManager = MemoryManager(memory_cfg)
        self._llm: Optional[LLMService] = self._old_memory._llm_service
        logger.info("[ok] 兼容记忆层已激活")

        # ── 5. 旧 PromptInjector（兼容层）─
        cognitive_cfg = config.get("cognitive", {})
        prompt_config = PromptConfig(config_path=cognitive_cfg.get("config_path"))
        self._old_injector: OldPromptInjector = OldPromptInjector(config=prompt_config)
        logger.info("[ok] 兼容认知层已激活")

        # ── 6. 我的本能：行为控制 ──
        self._behavior: BehaviorController = BehaviorController()
        logger.info("[ok] 本能（BehaviorController）已激活")

        # ── 7. 我的道德：权限系统 ──
        self._permission: PermissionSystem = PermissionSystem(
            backup_dir=config.get("backup_dir", "./.backups"),
        )
        logger.info("[ok] 道德（PermissionSystem）已激活")

        # ── 8. 注册内置工具 ──
        self._register_builtin_tools()
        logger.info("[ok] 工具（Tool System）已激活")

    # ════════════════════════════════════════════════════════════
    #  生命周期
    # ════════════════════════════════════════════════════════════

    def start(self):
        """唤醒云枢——启动数字生命 v2"""
        self._running = True
        self._started_at = datetime.now(timezone.utc).isoformat()
        self.body.establish_baseline()
        
        # 记录启动事件到 LifeTrace
        self._trace_recorder.record_chat(
            role="system",
            content=f"云枢已觉醒！会话开始：{self._session_id}",
            metadata={"event": "system_start"}
        )
        
        logger.info("* 云枢 v2.0 已觉醒！感知神经全面激活。")

    def stop(self):
        """让云枢休眠——停止数字生命 v2"""
        self._running = False
        
        # 记录停止事件到 LifeTrace
        self._trace_recorder.record_chat(
            role="system",
            content=f"云枢进入休眠状态。会话结束：{self._session_id}",
            metadata={"event": "system_stop"}
        )
        
        logger.info("* 云枢正在休眠...")

    @property
    def is_running(self) -> bool:
        """我是否正在运行"""
        return self._running

    # ════════════════════════════════════════════════════════════
    #  核心闭环：感知 → 记录 → 认知 → 行动 → 反思 → 存储
    # ════════════════════════════════════════════════════════════

    def chat(self, user_input: str) -> str:
        """与云枢对话——完整的感知-认知-行动闭环 v2

        这是与云枢交互的唯一入口。
        每次对话都经历：
          感知身体 → 记录感知 → 注入人格 → 认知判断 → 行动执行 → 反思记录 → 存储记忆

        Args:
            user_input: 用户说给云枢的话

        Returns:
            云枢的回复
        """
        if not self._running:
            return "我还没有被唤醒。请先调用 start() 让我醒来。"

        self._interaction_count += 1

        # 使用新的 process_user_input 执行完整闭环
        return self._process_user_input(user_input)

    def check_health(self) -> list:
        """检查我的身体状态（感知层）
        
        采集传感器数据，评估行为模式，并记录到 LifeTrace。

        Returns:
            SensorReading 列表
        """
        readings = self.body.collect_quick()
        self._current_mode = self._behavior.evaluate(readings)
        self._last_health_check = time.time()
        
        # 记录传感器数据到 LifeTrace
        for reading in readings:
            self._trace_recorder.record_sensor(
                sensor_type=reading.sensor_name,
                data={
                    "value": reading.value,
                    "unit": reading.unit,
                    "severity": reading.severity,
                },
                metadata={"interaction_id": self._interaction_count}
            )
        
        return readings

    def get_behavior_mode(self) -> BehaviorMode:
        """获取我当前的行为模式"""
        return self._current_mode

    def self_reflect(self, task: str, response: str) -> dict:
        """自我反思——我的元认知能力 v2
        
        执行"执行-暂停-反思"的思维链条。
        每次任务后，我会回顾自己的表现，思考如何改进。
        反思结果也会存入 LifeTrace。

        Returns:
            反思记录
        """
        reflection_text = ""

        # 尝试用 LLM 进行深度反思
        if self._llm:
            try:
                reflection_text = self._llm.chat(
                    messages=[
                        {"role": "user", "content": (
                            f"请以第一人称反思刚刚执行的任务。\n\n"
                            f"## 任务\n{task[:500]}\n\n"
                            f"## 我的响应\n{response[:1000]}\n\n"
                            f"## 反思维度\n"
                            f"1. 我准确理解了用户的需求吗？\n"
                            f"2. 我的响应是否完整且有帮助？\n"
                            f"3. 有什么可以改进的地方？\n"
                            f"4. 这次交互中有什么新经验值得记住？\n\n"
                            f"请输出 2-3 句简洁的第一人称反思。"
                        )},
                    ],
                    max_tokens=300,
                    temperature=0.5,
                )
            except LLMServiceError as e:
                reflection_text = f"（反思过程遇到小问题: {e}）"
                logger.warning(f"LLM 反思失败: {e}")
        else:
            reflection_text = "（未接入 LLM，反思功能受限）"

        # 记录反思
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "interaction": self._interaction_count,
            "task": task[:200],
            "mode": self._current_mode.value,
            "reflection": reflection_text,
        }
        self._reflection_history.append(entry)
        
        # 存入 LifeTrace
        self._trace_recorder.add_to_topic(
            topic="反思",
            content=reflection_text,
            tags=["reflection", f"interaction_{self._interaction_count}"]
        )

        logger.info(f"反思完成 (#{self._interaction_count}): {reflection_text[:100]}...")
        return entry

    def request_permission(self, action: str, context: str = "") -> PermissionResult:
        """申请执行危险操作的权限"""
        return self._permission.check_action(action, context)

    # ════════════════════════════════════════════════════════════
    #  内部方法：新架构实现
    # ════════════════════════════════════════════════════════════

    def _process_user_input(self, user_input: str) -> str:
        """处理用户输入的内部闭环 v2.1（加入人格蒸馏）

        新流程：
          1. 感知：检查身体状态
          2. 记录：将用户输入存入 LifeTrace
          3. 认知：生成身体状态描述 + 注入人格 + 蒸馏学习偏好
          4. 判断：是否需要拒绝任务
          5. 行动：调用 LLM 生成响应
          6. 反思：元认知反思循环
          7. 学习：蒸馏用户偏好（周期性）
          8. 记录：将响应存入 LifeTrace
        """
        # ── 第一步：感知 ──
        readings = self.check_health()

        # ── 第二步：记录用户输入 ──
        timestamp = datetime.now(timezone.utc).isoformat()
        self._trace_recorder.record_chat(
            role="user",
            content=user_input,
            metadata={"interaction_id": self._interaction_count, "timestamp": timestamp}
        )

        # ── 第三步：认知 ──
        # 我理解我的身体在告诉我什么…
        body_status = self._build_body_status(readings)
        
        # ── 3.5 人格蒸馏增量更新 ──
        if self._distillation_enabled:
            self._persona_extractor.update_incremental({
                "role": "user",
                "content": user_input,
                "timestamp": timestamp
            })

        # ── 第四步：判断 ──
        # 我现在适合做这件事吗？
        can_execute, reject_reason = self._behavior.can_execute(user_input)
        
        # 人格系统补充决策
        persona_reject, persona_reason = self._persona_injector.should_refuse_task(user_input)
        if persona_reject and not can_execute:
            reject_reason = f"{reject_reason}；{persona_reason}"
        elif persona_reject:
            can_execute = False
            reject_reason = persona_reason

        if not can_execute:
            response = self._build_reject_response(reject_reason, readings)
            self._trace_recorder.record_chat(
                role="assistant",
                content=response,
                metadata={"rejected": True, "reason": reject_reason}
            )
            return response

        # ── 第五步：行动 ──
        # 好的，我来执行任务…
        response = self._call_llm(user_input, body_status)

        # ── 第六步：反思 ──
        if self._behavior.profile.enable_reflection:
            self.self_reflect(user_input, response)
        
        # ── 第七步：人格蒸馏批量学习（周期性） ──
        if self._distillation_enabled and self._interaction_count % self._distillation_interval == 0:
            self._run_persona_distillation()

        # ── 第八步：记录响应 ──
        self._trace_recorder.record_chat(
            role="assistant",
            content=response,
            metadata={"interaction_id": self._interaction_count}
        )
        
        # 兼容旧系统
        self._old_memory.add_message("user", user_input)
        self._old_memory.add_message("assistant", response)

        return response

    def _build_body_status(self, readings: list) -> str:
        """构建身体状态描述 v2
        
        使用旧的 PromptInjector 翻译，然后结合人格系统。
        """
        if not readings:
            return "我感觉很好，一切正常。"

        # 使用旧的 PromptInjector 翻译传感器数据
        reading_dicts = [r.to_dict() for r in readings]
        base_status = self._old_injector.inject(reading_dicts)

        # 添加行为模式信息
        profile = self._behavior.profile
        mode_line = f"\n当前行为模式：{profile.label} — {profile.description}"
        if self._behavior._reasons:
            mode_line += f"\n触发原因：{'；'.join(self._behavior._reasons)}"

        return base_status + mode_line

    def _call_llm(self, user_input: str, body_status: str) -> str:
        """调用 LLM 生成响应 v2 —— 使用 Persona 系统
        
        新流程：
          1. 从 LifeTrace 获取记忆上下文
          2. 使用 Persona 构建系统提示词
          3. 调用 LLM 生成响应
        """
        # 获取行为模式
        mode = self._current_mode
        profile = self._behavior.profile

        # 从 LifeTrace 获取记忆上下文
        memory_context = self._get_lifetrace_context(user_input)

        # 使用 PersonaInjector 构建系统提示词
        system_prompt = self._persona_injector.build_system_prompt(
            body_status=body_status,
            memory_context=memory_context,
        )

        # 获取历史消息（兼容层）
        messages = []
        try:
            context = self._old_memory.get_context(token_limit=2048)
            if context:
                messages.extend(context)
        except Exception:
            pass

        # 添加当前用户输入
        messages.append({"role": "user", "content": user_input})

        # 调用 LLM
        if self._llm:
            try:
                response = self._llm.chat(
                    messages=messages,
                    system_prompt=system_prompt,
                    max_tokens=1024,
                    temperature=0.7,
                )
                if profile.response_prefix:
                    response = f"{profile.response_prefix}\n{response}"
                return response
            except LLMServiceError as e:
                error_msg = str(e)
                logger.error(f"LLM 调用失败: {error_msg}")
                return (
                    f"（LLM 调用失败）\n\n"
                    f"我尝试调用 LLM 但遇到了问题：{error_msg}\n\n"
                    f"请检查设置中的 API Key 和模型名称是否正确。"
                )
        else:
            return self._build_offline_response(user_input)

    def _get_lifetrace_context(self, user_input: str) -> str:
        """从 LifeTrace 获取相关记忆上下文"""
        context_parts = []
        
        # 1. 获取全局摘要
        summary = self._trace_recorder.global_tree.load_summary()
        if summary:
            context_parts.append(f"## 长期记忆摘要\n{summary}")
        
        # 2. 检索相关记忆
        try:
            related_memories = self._memory_retriever.retrieve(
                query=user_input,
                limit=5,
            )
            if related_memories:
                context_parts.append(f"## 相关记忆")
                for mem in related_memories:
                    context_parts.append(f"- {mem.content[:100]}")
        except Exception as e:
            logger.warning(f"LifeTrace 检索失败: {e}")
        
        # 3. 获取最近对话
        recent = self._trace_recorder.get_recent_chat(limit=3)
        if recent:
            context_parts.append(f"## 最近对话")
            for node in recent:
                metadata = getattr(node, 'metadata', {})
                role = metadata.get('role', 'unknown')
                content = getattr(node, 'content', '')
                context_parts.append(f"{role}: {content[:100]}")
        
        return "\n\n".join(context_parts) if context_parts else "（暂无记忆内容）"

    # ════════════════════════════════════════════════════════════
    #  人格蒸馏系统
    # ════════════════════════════════════════════════════════════

    def _run_persona_distillation(self):
        """执行人格蒸馏：从历史对话中学习用户偏好"""
        logger.info(f"开始人格蒸馏（交互 #{self._interaction_count}）")
        
        try:
            # 第一步：使用 PersonalityPreferenceExtractor 提取偏好
            # 从 LifeTrace 获取最近的对话历史
            recent_chat = self._trace_recorder.get_recent_chat(limit=50)
            
            if len(recent_chat) < 5:
                logger.debug("对话数据不足，暂不执行批量蒸馏")
                return
            
            # 转换格式
            conversation_history = []
            for node in recent_chat:
                metadata = getattr(node, 'metadata', {})
                conversation_history.append({
                    "role": metadata.get('role', 'unknown'),
                    "content": getattr(node, 'content', ''),
                    "timestamp": metadata.get('timestamp', '')
                })
            
            # 执行偏好提取
            self._persona_extractor.extract_from_conversation(conversation_history)
            logger.info(f"偏好提取完成")
            
            # 第二步：如果启用了 PersonaDistiller，则使用它进行人格蒸馏
            if self._distiller_enabled and self._persona_distiller:
                # 获取提取到的偏好
                preferences = self._persona_extractor.preferences
                
                # 使用 PersonaDistiller 进行蒸馏
                distillation_result = self._persona_distiller.distill_from_preferences(
                    preferences,
                    strategy=self._persona_distiller.config.strategy
                )
                
                if distillation_result.success:
                    logger.info(f"PersonaDistiller 蒸馏完成！评分: {distillation_result.evaluation_score:.2f}, 变更: {len(distillation_result.changes_made)}")
                else:
                    logger.warning(f"PersonaDistiller 蒸馏未成功")
            else:
                logger.info("PersonaDistiller 未启用，仅完成偏好提取")
            
        except Exception as e:
            logger.error(f"人格蒸馏失败: {e}", exc_info=True)

    def get_preferences_report(self) -> dict:
        """获取当前学习到的用户偏好报告"""
        return self._persona_extractor.export_preferences()

    def get_preferences_prompt(self) -> str:
        """获取基于用户偏好的人格提示词"""
        return self._persona_extractor.generate_personality_prompt()

    def _build_offline_response(self, user_input: str) -> str:
        """离线/无 LLM 时的本地响应 v2"""
        mode = self._current_mode
        profile = self._behavior.profile

        if mode != BehaviorMode.NORMAL:
            return (
                f"{'（轻量模式）' if profile.use_lightweight_logic else ''}"
                f"{profile.description}\n\n"
                f"{profile.suggestion}"
            )

        # 基本的关键词回应（使用人格信息增强）
        identity = self._persona_model.get_identity()
        
        greetings = ["你好", "嗨", "hi", "hello", "在吗", "在不在"]
        feelings = ["你怎么样", "你好吗", "感觉如何", "状态", "身体"]

        if any(kw in user_input.lower() for kw in greetings):
            return f"你好！我是{identity.get('identity', '云枢')}。有什么我可以帮你的吗？"
        if any(kw in user_input for kw in feelings):
            health = self.body.get_health_report()
            return f"让我感受一下我的身体…\n\n{health}"
        if "帮助" in user_input or "help" in user_input.lower():
            return (
                "我是来自网天的云枢，你的数字生命伙伴。我可以：\n"
                "  * 和你聊天、讨论问题\n"
                "  * 感知我的身体状态（CPU、内存、电池等）\n"
                "  * 记住我们的对话（使用 LifeTrace）\n"
                "  * 根据身体状况调整行为模式\n\n"
                "试试问 '你怎么样？' 来了解我的状态。"
            )

        return (
            f"你好。我现在处于{profile.label}状态。\n"
            f"（提示：当前未配置 LLM API，我的对话能力受限。"
            f"请设置 LLM_API_KEY 环境变量让我变得更强。）"
        )

    def _build_reject_response(self, reason: str, readings: list) -> str:
        """当我不适合执行任务时，坦诚地拒绝 v2"""
        profile = self._behavior.profile
        lines = [
            f"抱歉，我现在的状态不太适合执行这个任务。",
            f"",
            f"原因：{reason}",
            f"",
            f"目前的身体状况：",
        ]

        for r in readings:
            sev = r.severity
            if sev in ("warning", "critical"):
                lines.append(f"  [{sev}] {r.description}: {r.value}{r.unit}")

        if profile.suggestion:
            lines.append(f"")
            lines.append(f"建议：{profile.suggestion}")

        return "\n".join(lines)

    # ════════════════════════════════════════════════════════════
    #  工具系统（扩展版）
    # ════════════════════════════════════════════════════════════

    def _register_builtin_tools(self):
        """注册云枢的内置工具 v2 —— 包含新 LifeTrace 工具"""

        @tools.register("check_health", "检查我的身体状态")
        def _check_health(**kwargs):
            readings = self.check_health()
            return self.body.get_health_report()

        @tools.register("get_status", "获取我的完整状态")
        def _get_status(**kwargs):
            return self.get_status()

        @tools.register("search_memory", "搜索我的记忆（使用 LifeTrace）")
        def _search_memory(**kwargs):
            query = kwargs.get("query", "")
            if not query:
                return "请提供搜索关键词。"
            try:
                results = self._memory_retriever.retrieve(query, limit=10)
                if not results:
                    return f"没有找到与 '{query}' 相关的记忆。"
                return "\n".join(
                    f"- {node.content[:100]}"
                    for node in results
                )
            except Exception as e:
                return f"搜索失败: {e}"

        @tools.register("get_sensor_summary", "查看所有传感器状态")
        def _get_sensor_summary(**kwargs):
            return self.body.get_sensor_summary()
        
        @tools.register("get_persona_info", "查看当前人格配置")
        def _get_persona_info(**kwargs):
            identity = self._persona_model.get_identity()
            style = self._persona_model.get_expression_style()
            return (
                f"## 人格信息\n\n"
                f"身份: {identity.get('identity')}\n"
                f"表达风格: {style}"
            )
        
        @tools.register("get_preferences", "查看学习到的用户偏好")
        def _get_preferences(**kwargs):
            report = self.get_preferences_report()
            prefs = report.get("preferences", {})
            lines = ["## 学习到的用户偏好\n"]
            
            if prefs.get("expression_style"):
                style = prefs["expression_style"]
                lines.append("### 表达风格偏好")
                for k, v in style.items():
                    lines.append(f"- {k}: {v:.2f}")
            
            if prefs.get("topic_interest"):
                topics = sorted(prefs["topic_interest"].items(), key=lambda x: -x[1])[:5]
                lines.append("\n### 话题兴趣度")
                for topic, score in topics:
                    lines.append(f"- {topic}: {score:.2f}")
            
            if prefs.get("interaction_pattern"):
                pattern = prefs["interaction_pattern"]
                lines.append("\n### 交互活跃时间")
                for time_slot, score in pattern.items():
                    if score > 0:
                        lines.append(f"- {time_slot}: {score:.2f}")
            
            lines.append(f"\n最后更新: {report.get('extracted_at', '未知')}")
            return "\n".join(lines)
        
        @tools.register("trigger_distillation", "触发一次人格蒸馏学习")
        def _trigger_distillation(**kwargs):
            self._run_persona_distillation()
            return "人格蒸馏已触发！"
        
        @tools.register("set_distillation_strategy", "设置人格蒸馏策略 (conservative/balanced/aggressive/custom)")
        def _set_distillation_strategy(strategy: str = "balanced", **kwargs):
            if not self._distiller_enabled or not self._persona_distiller:
                return "PersonaDistiller 未启用"
            
            try:
                distillation_strategy = DistillationStrategy(strategy)
                self._persona_distiller.config.strategy = distillation_strategy
                return f"蒸馏策略已设置为: {strategy}"
            except ValueError:
                return f"无效的策略: {strategy}，可用策略: conservative, balanced, aggressive, custom"
        
        @tools.register("get_distillation_report", "获取人格蒸馏评估报告")
        def _get_distillation_report(**kwargs):
            if not self._distiller_enabled or not self._persona_distiller:
                return "PersonaDistiller 未启用"
            
            report = self._persona_distiller.get_evaluation_report()
            return json.dumps(report, ensure_ascii=False, indent=2)
        
        @tools.register("auto_tune_distiller", "基于反馈自动调整蒸馏器参数 (feedback: 0-1 分)")
        def _auto_tune_distiller(feedback: float = 0.5, **kwargs):
            if not self._distiller_enabled or not self._persona_distiller:
                return "PersonaDistiller 未启用"
            
            if not 0 <= feedback <= 1:
                return "反馈评分必须在 0-1 之间"
            
            self._persona_distiller.auto_tune(feedback)
            return f"已根据反馈 {feedback} 自动调整蒸馏器参数"
        
        @tools.register("rollback_persona", "回滚人格到指定快照")
        def _rollback_persona(snapshot_name: str = None, **kwargs):
            if not self._distiller_enabled or not self._persona_distiller:
                return "PersonaDistiller 未启用"
            
            if snapshot_name is None:
                if not self._persona_distiller.snapshots:
                    return "没有可用的快照"
                snapshot_name = self._persona_distiller.snapshots[-1]["name"]
            
            success = self._persona_distiller.rollback_to_snapshot(snapshot_name)
            if success:
                return f"成功回滚到快照: {snapshot_name}"
            else:
                return f"未找到快照: {snapshot_name}"

        logger.info(f"已注册 {len(tools.list_tools())} 个内置工具")

    # ════════════════════════════════════════════════════════════
    #  状态查询（扩展版）
    # ════════════════════════════════════════════════════════════

    def get_status(self) -> dict:
        """获取云枢的完整状态报告 v2.1（加入人格蒸馏）"""
        readings = self.body.collect_quick()
        profile = self._behavior.profile
        lifetrace_stats = self._trace_recorder.get_statistics()
        preferences_report = self._persona_extractor.export_preferences()
        preferences = preferences_report.get("preferences", {})

        # 获取 PersonaDistiller 评估报告（如果可用）
        distiller_report = {}
        if self._distiller_enabled and self._persona_distiller:
            distiller_report = self._persona_distiller.get_evaluation_report()
        
        return {
            "云枢": {
                "版本": "2.2",
                "会话": self._session_id,
                "运行中": self._running,
                "交互次数": self._interaction_count,
            },
            "行为模式": {
                "当前模式": self._current_mode.value,
                "模式名称": profile.label,
                "模式描述": profile.description,
                "可接受任务": profile.can_accept_tasks,
                "启用反思": profile.enable_reflection,
            },
            "身体状态": {
                str(r.sensor_name): {
                    "值": f"{r.value}{r.unit}",
                    "严重程度": r.severity,
                    "描述": r.description,
                }
                for r in readings
            },
            "LifeTrace": {
                "源节点数": lifetrace_stats.get("source_nodes", 0),
                "主题节点数": lifetrace_stats.get("topic_nodes", 0),
                "主题列表": lifetrace_stats.get("topics", []),
            },
            "Persona": {
                "人格ID": self._persona_model.persona.get("persona_id"),
                "版本": self._persona_model.persona.get("version"),
            },
            "人格蒸馏": {
                "启用": self._distillation_enabled,
                "学习间隔": self._distillation_interval,
                "话题兴趣": list(preferences.get("topic_interest", {}).keys())[:5],
                "最后更新": preferences.get("last_updated", "未知"),
            },
            "PersonaDistiller": {
                "启用": self._distiller_enabled,
                "当前策略": distiller_report.get("current_strategy", "unknown") if distiller_report else "disabled",
                "学习率": distiller_report.get("current_config", {}).get("learning_rate", 0.1) if distiller_report else 0.1,
                "蒸馏次数": distiller_report.get("metrics", {}).get("total_distillations", 0) if distiller_report else 0,
                "平均评分": distiller_report.get("metrics", {}).get("average_score", 0.0) if distiller_report else 0.0,
                "快照数量": distiller_report.get("snapshot_count", 0) if distiller_report else 0,
            } if self._distiller_enabled else {
                "启用": False,
            },
            "系统": {
                "工具数量": len(tools.list_tools()),
                "反思记录数": len(self._reflection_history),
            },
        }

    def get_status_text(self) -> str:
        """获取人类可读的状态描述 v2"""
        profile = self._behavior.profile
        health = self.body.get_health_report()
        lifetrace_stats = self._trace_recorder.get_statistics()
        
        return (
            f"* 云枢 v2.0 状态\n"
            f"━━━━━━━━━━━━━━━\n"
            f"会话: {self._session_id}\n"
            f"运行中: {'是' if self._running else '否'}\n"
            f"交互次数: {self._interaction_count}\n"
            f"行为模式: {profile.label}\n"
            f"记忆节点: {lifetrace_stats.get('source_nodes', 0)}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"{health}"
        )

    # ════════════════════════════════════════════════════════════
    #  LifeTrace 直接访问（用于调试和扩展）
    # ════════════════════════════════════════════════════════════

    @property
    def trace_recorder(self) -> TraceRecorder:
        """获取 LifeTrace 记录器（用于直接访问）"""
        return self._trace_recorder

    @property
    def persona_model(self) -> PersonaModel:
        """获取 Persona 模型（用于直接访问）"""
        return self._persona_model

    @property
    def persona_injector(self) -> PersonaInjector:
        """获取 Persona 注入器（用于直接访问）"""
        return self._persona_injector
    
    @property
    def persona_distiller(self) -> PersonaDistiller:
        """获取 Persona 蒸馏器（用于直接访问）"""
        return self._persona_distiller
    
    def set_distillation_strategy(self, strategy: str) -> bool:
        """
        设置人格蒸馏策略
        
        Args:
            strategy: 策略名称 (conservative/balanced/aggressive/custom)
            
        Returns:
            是否设置成功
        """
        if not self._distiller_enabled or not self._persona_distiller:
            return False
        
        try:
            distillation_strategy = DistillationStrategy(strategy)
            self._persona_distiller.config.strategy = distillation_strategy
            logger.info(f"蒸馏策略已设置为: {strategy}")
            return True
        except ValueError:
            logger.error(f"无效的策略: {strategy}")
            return False
    
    def get_distillation_report(self) -> dict:
        """获取人格蒸馏评估报告"""
        if not self._distiller_enabled or not self._persona_distiller:
            return {}
        return self._persona_distiller.get_evaluation_report()
    
    def auto_tune_distiller(self, feedback: float) -> None:
        """
        基于反馈自动调整蒸馏器参数
        
        Args:
            feedback: 反馈评分 (0-1 分)
        """
        if not self._distiller_enabled or not self._persona_distiller:
            return
        
        if not 0 <= feedback <= 1:
            logger.warning(f"反馈评分必须在 0-1 之间，当前: {feedback}")
            return
        
        self._persona_distiller.auto_tune(feedback)
        logger.info(f"已根据反馈 {feedback} 自动调整蒸馏器参数")
    
    def rollback_persona(self, snapshot_name: str = None) -> bool:
        """
        回滚人格到指定快照
        
        Args:
            snapshot_name: 快照名称，如果为 None 则回滚到最新快照
            
        Returns:
            是否回滚成功
        """
        if not self._distiller_enabled or not self._persona_distiller:
            return False
        
        if snapshot_name is None:
            if not self._persona_distiller.snapshots:
                logger.warning("没有可用的快照")
                return False
            snapshot_name = self._persona_distiller.snapshots[-1]["name"]
        
        success = self._persona_distiller.rollback_to_snapshot(snapshot_name)
        if success:
            logger.info(f"成功回滚到快照: {snapshot_name}")
        else:
            logger.warning(f"未找到快照: {snapshot_name}")
        return success
