
"""DigitalLife v2 — 云枢的数字生命主类（升级整合版）

整合了 LifeTrace 记忆系统和 Persona 人格系统，
实现更强大的感知-认知-行动闭环。

新功能：
- LifeTrace 三层记忆树（海马体）
- Persona 五层人格模型（immortal-skill）
- 更完整的数据流管理
"""

import logging
import time
import os
import json
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

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

# 新模块：LifeTrace 和 Persona（懒加载时导入）
# from lifetrace import TraceRecorder, MemoryRetriever
# from persona import PersonaModel, PersonaInjector, PersonalityPreferenceExtractor
# from persona.distiller import PersonaDistiller, DistillationStrategy, DistillationConfig

logger = logging.getLogger(__name__)


class LazyLoader:
    """P5 优化：通用的懒加载辅助类
    
    延迟初始化重型模块到首次访问时，减少启动时间和内存占用。
    """
    
    def __init__(self, init_func, name: str):
        """
        Args:
            init_func: 初始化函数，返回初始化后的实例
            name: 模块名称，用于日志
        """
        self._init_func = init_func
        self._name = name
        self._instance = None
        self._initialized = False
        self._init_time_ms = 0
    
    def get(self):
        """获取实例，按需初始化
        
        Returns:
            初始化后的实例
        """
        if not self._initialized:
            logger.info(f"[P5] 懒加载初始化: {self._name}")
            start = time.time()
            self._instance = self._init_func()
            self._init_time_ms = (time.time() - start) * 1000
            self._initialized = True
            logger.info(f"[P5] {self._name} 初始化完成，耗时: {self._init_time_ms:.2f}ms")
        return self._instance
    
    @property
    def is_initialized(self):
        return self._initialized
    
    @property
    def init_time_ms(self):
        return self._init_time_ms


class DigitalLifeV2:
    """云枢主类 v2.0 — 整合了 LifeTrace 和 Persona 系统
    
    新架构：
      感知层 → LifeTrace 记录 → Persona 注入 → LLM 思考 → 行动 → 反思
    """

    def __init__(self, config: dict = None):
        """
        初始化数字生命 v2 —— 唤醒云枢（P5 懒加载优化版）
        
        P5 优化：
          1. BodySensor：直接初始化（已经有懒加载）
          2. LifeTrace：懒加载（首次访问时初始化）
          3. Persona：懒加载（首次访问时初始化）
          4. MemoryManager：懒加载（首次访问时初始化）
          5. BehaviorController：直接初始化
          6. PermissionSystem：直接初始化
        
        懒加载的好处：
          - 更快的启动时间
          - 更少的初始内存占用
          - 按需加载，避免初始化未使用的模块
        """
        config = config or {}
        self._config = config  # 保存配置用于懒加载
        start_init = time.time()
        
        # ── 1. 我的身体：感知层（懒加载优化）──
        sensor_cfg = config.get("sensor", {})
        self.body: BodySensor = BodySensor(
            watch_dirs=sensor_cfg.get("watch_dirs"),
            enable_change_detection=sensor_cfg.get("enable_change_detection", True),
            enable_event_monitor=sensor_cfg.get("enable_event_monitor", True),
            lazy_load=sensor_cfg.get("lazy_load", True),  # 启用懒加载
        )
        logger.info("[ok] 身体（BodySensor）已激活（懒加载模式）")

        # ── 2. LifeTrace：海马体记忆系统（P5 懒加载）──
        logger.info("[P5] 配置 LifeTrace（懒加载模式）")
        
        # ── 3. Persona：人格系统（P5 懒加载）──
        logger.info("[P5] 配置 Persona（懒加载模式）")
        
        # ── 4. 旧记忆管理器（兼容层）（P5 懒加载）──
        logger.info("[P5] 配置 MemoryManager（懒加载模式）")

        # ── 5. 旧 PromptInjector（兼容层）（P5 懒加载）──
        logger.info("[P5] 配置 PromptInjector（懒加载模式）")

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

        # ── P5 懒加载初始化状态 ──
        self._lifetrace_initialized = False
        self._persona_initialized = False
        self._memory_initialized = False
        self._injector_initialized = False
        
        # ── P5 懒加载实例（延迟初始化）──
        self._trace_recorder = None
        self._memory_retriever = None
        self._persona_model = None
        self._persona_injector = None
        self._persona_extractor = None
        self._persona_distiller = None
        self._distillation_enabled = config.get("distillation", {}).get("enabled", True)
        self._distillation_interval = config.get("distillation", {}).get("interval", 10)
        self._distiller_enabled = config.get("distillation", {}).get("distiller_enabled", True)
        self._old_memory = None
        self._llm = None
        self._old_injector = None

        # ── 启动提示 ──
        init_time_ms = (time.time() - start_init) * 1000
        logger.info("=" * 50)
        logger.info("  云枢 v2.0 启动配置 (P5 懒加载优化):")
        logger.info(f"  初始化耗时: {init_time_ms:.2f}ms")
        logger.info(f"  会话:    {self._session_id}")
        logger.info("=" * 50)

    # ════════════════════════════════════════════════════════════
    #  P5 懒加载确保方法
    # ════════════════════════════════════════════════════════════

    def _ensure_lifetrace(self):
        """P5 优化：确保 LifeTrace 系统已初始化"""
        if not self._lifetrace_initialized:
            logger.info("[P5] 首次访问 LifeTrace，执行懒加载初始化")
            start = time.time()
            
            # 导入模块
            from lifetrace import TraceRecorder, MemoryRetriever
            
            # 初始化
            lifetrace_cfg = self._config.get("lifetrace", {})
            self._trace_recorder = TraceRecorder(
                data_dir=lifetrace_cfg.get("data_dir", "./data/lifetrace")
            )
            self._memory_retriever = MemoryRetriever(
                self._trace_recorder.source_tree,
                self._trace_recorder.topic_tree,
                self._trace_recorder.global_tree,
            )
            
            self._lifetrace_initialized = True
            elapsed = (time.time() - start) * 1000
            logger.info(f"[P5] LifeTrace 系统初始化完成，耗时: {elapsed:.2f}ms")

    def _ensure_persona(self):
        """P5 优化：确保 Persona 系统已初始化"""
        if not self._persona_initialized:
            logger.info("[P5] 首次访问 Persona，执行懒加载初始化")
            start = time.time()
            
            # 导入模块
            from persona import PersonaModel, PersonaInjector, PersonalityPreferenceExtractor
            from persona.distiller import PersonaDistiller, DistillationStrategy, DistillationConfig
            
            # 初始化 Persona
            persona_cfg = self._config.get("persona", {})
            self._persona_model = PersonaModel(
                persona_path=persona_cfg.get("persona_path")
            )
            self._persona_injector = PersonaInjector(self._persona_model)
            
            # 初始化人格蒸馏
            distillation_cfg = self._config.get("distillation", {})
            self._persona_extractor = PersonalityPreferenceExtractor(
                data_dir=distillation_cfg.get("data_dir", "./data/persona")
            )
            
            # 初始化 PersonaDistiller
            if self._distiller_enabled:
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
            
            self._persona_initialized = True
            elapsed = (time.time() - start) * 1000
            logger.info(f"[P5] Persona 系统初始化完成，耗时: {elapsed:.2f}ms")

    def _ensure_memory(self):
        """P5 优化：确保 Memory 系统已初始化"""
        if not self._memory_initialized:
            logger.info("[P5] 首次访问 MemoryManager，执行懒加载初始化")
            start = time.time()
            
            # 导入模块
            from memory import MemoryManager
            
            # 初始化
            memory_cfg = self._config.get("memory", {})
            self._old_memory = MemoryManager(memory_cfg)
            self._llm = self._old_memory._llm_service
            
            self._memory_initialized = True
            elapsed = (time.time() - start) * 1000
            logger.info(f"[P5] MemoryManager 系统初始化完成，耗时: {elapsed:.2f}ms")

    def _ensure_injector(self):
        """P5 优化：确保 PromptInjector 已初始化"""
        if not self._injector_initialized:
            logger.info("[P5] 首次访问 PromptInjector，执行懒加载初始化")
            start = time.time()
            
            # 导入模块
            from cognitive import PromptConfig
            
            # 初始化
            cognitive_cfg = self._config.get("cognitive", {})
            prompt_config = PromptConfig(config_path=cognitive_cfg.get("config_path"))
            self._old_injector = OldPromptInjector(config=prompt_config)
            
            self._injector_initialized = True
            elapsed = (time.time() - start) * 1000
            logger.info(f"[P5] PromptInjector 系统初始化完成，耗时: {elapsed:.2f}ms")

    # ════════════════════════════════════════════════════════════
    #  生命周期
    # ════════════════════════════════════════════════════════════

    def start(self):
        """唤醒云枢——启动数字生命 v2（P5 懒加载优化）"""
        self._running = True
        self._started_at = datetime.now(timezone.utc).isoformat()
        self.body.establish_baseline()
        
        # P5 优化：start 时初始化 LifeTrace（记录启动事件需要）
        self._ensure_lifetrace()
        
        # 记录启动事件到 LifeTrace
        if self._lifetrace_initialized and self._trace_recorder:
            self._trace_recorder.record_chat(
                role="system",
                content=f"云枢已觉醒！会话开始：{self._session_id}",
                metadata={"event": "system_start"}
            )
        
        logger.info("* 云枢 v2.0 已觉醒！感知神经全面激活。")

    def stop(self):
        """让云枢休眠——停止数字生命 v2"""
        self._running = False
        
        # 记录停止事件到 LifeTrace（如果已初始化）
        if self._lifetrace_initialized and self._trace_recorder:
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
        
        # P5 优化：记录前确保 LifeTrace 已初始化
        self._ensure_lifetrace()
        
        # 记录传感器数据到 LifeTrace
        if self._lifetrace_initialized and self._trace_recorder:
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
        """自我反思——我的元认知能力 v2（P5 懒加载优化）
        
        执行"执行-暂停-反思"的思维链条。
        每次任务后，我会回顾自己的表现，思考如何改进。
        反思结果也会存入 LifeTrace。

        Returns:
            反思记录
        """
        # P5 优化：确保所需模块已初始化
        self._ensure_lifetrace()
        self._ensure_memory()
        
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
        if self._lifetrace_initialized and self._trace_recorder:
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
        """处理用户输入的内部闭环 v2.1（加入人格蒸馏 + P5 懒加载）

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
        # ── P5 优化：确保所需模块已初始化 ──
        self._ensure_lifetrace()
        self._ensure_persona()
        self._ensure_memory()
        self._ensure_injector()
        
        # ── 第一步：感知 ──
        readings = self.check_health()

        # ── 第二步：记录用户输入 ──
        timestamp = datetime.now(timezone.utc).isoformat()
        if self._lifetrace_initialized and self._trace_recorder:
            self._trace_recorder.record_chat(
                role="user",
                content=user_input,
                metadata={"interaction_id": self._interaction_count, "timestamp": timestamp}
            )

        # ── 第三步：认知 ──
        # 我理解我的身体在告诉我什么…
        body_status = self._build_body_status(readings)
        
        # ── 3.5 人格蒸馏增量更新 ──
        if self._distillation_enabled and self._persona_initialized:
            self._persona_extractor.update_incremental({
                "role": "user",
                "content": user_input,
                "timestamp": timestamp
            })

        # ── 第四步：判断 ──
        # 我现在适合做这件事吗？
        can_execute, reject_reason = self._behavior.can_execute(user_input)
        
        # 人格系统补充决策
        persona_reject = False
        persona_reason = ""
        if self._persona_initialized:
            persona_reject, persona_reason = self._persona_injector.should_refuse_task(user_input)
            if persona_reject and not can_execute:
                reject_reason = f"{reject_reason}；{persona_reason}"
            elif persona_reject:
                can_execute = False
                reject_reason = persona_reason

        if not can_execute:
            response = self._build_reject_response(reject_reason, readings)
            if self._lifetrace_initialized and self._trace_recorder:
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
        
        # ── 第七步：人格蒸馏批量学习（周期性）──
        if self._distillation_enabled and self._interaction_count % self._distillation_interval == 0:
            self._run_persona_distillation()

        # ── 第八步：记录响应 ──
        if self._lifetrace_initialized and self._trace_recorder:
            self._trace_recorder.record_chat(
                role="assistant",
                content=response,
                metadata={"interaction_id": self._interaction_count}
            )
        
        # 兼容旧系统
        if self._memory_initialized and self._old_memory:
            self._old_memory.add_message("user", user_input)
            self._old_memory.add_message("assistant", response)

        return response

    def _build_body_status(self, readings: list) -> str:
        """构建身体状态描述 v2（P5 懒加载优化）
        
        使用旧的 PromptInjector 翻译，然后结合人格系统。
        """
        if not readings:
            return "我感觉很好，一切正常。"
        
        # P5 优化：确保 PromptInjector 已初始化
        self._ensure_injector()

        # 使用旧的 PromptInjector 翻译传感器数据
        reading_dicts = [r.to_dict() for r in readings]
        base_status = self._old_injector.inject(reading_dicts) if self._old_injector else ""

        # 添加行为模式信息
        profile = self._behavior.profile
        mode_line = f"\n当前行为模式：{profile.label} — {profile.description}"
        if self._behavior._reasons:
            mode_line += f"\n触发原因：{'；'.join(self._behavior._reasons)}"

        return base_status + mode_line

    def _call_llm(self, user_input: str, body_status: str) -> str:
        """调用 LLM 生成响应 v2 —— 使用 Persona 系统（P5 懒加载优化）
        
        新流程：
          1. 从 LifeTrace 获取记忆上下文
          2. 使用 Persona 构建系统提示词
          3. 调用 LLM 生成响应
        """
        # P5 优化：确保所需模块已初始化
        self._ensure_lifetrace()
        self._ensure_persona()
        self._ensure_memory()
        
        # 获取行为模式
        mode = self._current_mode
        profile = self._behavior.profile

        # 从 LifeTrace 获取记忆上下文
        memory_context = self._get_lifetrace_context(user_input)

        # 使用 PersonaInjector 构建系统提示词
        system_prompt = ""
        if self._persona_initialized:
            system_prompt = self._persona_injector.build_system_prompt(
                body_status=body_status,
                memory_context=memory_context,
            )

        # 获取历史消息（兼容层）
        messages = []
        if self._memory_initialized and self._old_memory:
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
        """从 LifeTrace 获取相关记忆上下文（P5 懒加载优化）"""
        context_parts = []
        
        # P5 优化：确保 LifeTrace 已初始化
        if not self._lifetrace_initialized or not self._trace_recorder or not self._memory_retriever:
            return "（暂无记忆内容）"
        
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
        """执行人格蒸馏：从历史对话中学习用户偏好（P5 懒加载优化）"""
        # P5 优化：确保所需模块已初始化
        self._ensure_lifetrace()
        self._ensure_persona()
        
        if not self._persona_initialized or not self._lifetrace_initialized:
            return
            
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
        """获取当前学习到的用户偏好报告（P5 懒加载优化）"""
        # P5 优化：确保 Persona 已初始化
        self._ensure_persona()
        
        if not self._persona_initialized or not self._persona_extractor:
            return {"preferences": {}, "extracted_at": datetime.now(timezone.utc).isoformat()}
        return self._persona_extractor.export_preferences()

    def get_preferences_prompt(self) -> str:
        """获取基于用户偏好的人格提示词（P5 懒加载优化）"""
        # P5 优化：确保 Persona 已初始化
        self._ensure_persona()
        
        if not self._persona_initialized or not self._persona_extractor:
            return ""
        return self._persona_extractor.generate_personality_prompt()

    def _build_offline_response(self, user_input: str) -> str:
        """离线/无 LLM 时的本地响应 v2（P5 懒加载优化）"""
        mode = self._current_mode
        profile = self._behavior.profile

        if mode != BehaviorMode.NORMAL:
            return (
                f"{'（轻量模式）' if profile.use_lightweight_logic else ''}"
                f"{profile.description}\n\n"
                f"{profile.suggestion}"
            )

        # 基本的关键词回应（使用人格信息增强）
        identity = {"identity": "云枢"}
        if self._persona_initialized and self._persona_model:
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
        """获取云枢的完整状态报告 v2.1（加入人格蒸馏 + P5 懒加载）"""
        readings = self.body.collect_quick()
        profile = self._behavior.profile
        
        # P5 优化：根据初始化状态获取统计信息
        lifetrace_stats = {"source_nodes": 0, "topic_nodes": 0, "topics": []}
        if self._lifetrace_initialized and self._trace_recorder:
            lifetrace_stats = self._trace_recorder.get_statistics()
        
        preferences_report = {"preferences": {}}
        if self._persona_initialized and self._persona_extractor:
            preferences_report = self._persona_extractor.export_preferences()
        preferences = preferences_report.get("preferences", {})

        # 获取 PersonaDistiller 评估报告（如果可用）
        distiller_report = {}
        if self._persona_initialized and self._distiller_enabled and self._persona_distiller:
            distiller_report = self._persona_distiller.get_evaluation_report()
        
        persona_info = {"人格ID": "default", "版本": "1.0"}
        if self._persona_initialized and self._persona_model:
            persona_info = {
                "人格ID": self._persona_model.persona.get("persona_id"),
                "版本": self._persona_model.persona.get("version"),
            }
        
        return {
            "云枢": {
                "版本": "2.2-P5",
                "会话": self._session_id,
                "运行中": self._running,
                "交互次数": self._interaction_count,
            },
            "P5懒加载状态": {
                "LifeTrace": self._lifetrace_initialized,
                "Persona": self._persona_initialized,
                "Memory": self._memory_initialized,
                "Injector": self._injector_initialized,
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
            "Persona": persona_info,
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
        """获取人类可读的状态描述 v2（P5 懒加载优化）"""
        profile = self._behavior.profile
        health = self.body.get_health_report()
        
        # P5 优化：根据初始化状态获取统计信息
        lifetrace_stats = {"source_nodes": 0}
        if self._lifetrace_initialized and self._trace_recorder:
            lifetrace_stats = self._trace_recorder.get_statistics()
        
        return (
            f"* 云枢 v2.0-P5 状态\n"
            f"━━━━━━━━━━━━━━━\n"
            f"会话: {self._session_id}\n"
            f"运行中: {'是' if self._running else '否'}\n"
            f"交互次数: {self._interaction_count}\n"
            f"行为模式: {profile.label}\n"
            f"记忆节点: {lifetrace_stats.get('source_nodes', 0)}\n"
            f"P5懒加载: 【LifeTrace: {'✓' if self._lifetrace_initialized else '✗'}】【Persona: {'✓' if self._persona_initialized else '✗'}】\n"
            f"━━━━━━━━━━━━━━━\n"
            f"{health}"
        )

    # ════════════════════════════════════════════════════════════
    #  LifeTrace 直接访问（用于调试和扩展）（P5 懒加载）
    # ════════════════════════════════════════════════════════════

    @property
    def trace_recorder(self):
        """获取 LifeTrace 记录器（用于直接访问）（P5 懒加载）"""
        self._ensure_lifetrace()
        return self._trace_recorder

    @property
    def persona_model(self):
        """获取 Persona 模型（用于直接访问）（P5 懒加载）"""
        self._ensure_persona()
        return self._persona_model

    @property
    def persona_injector(self):
        """获取 Persona 注入器（用于直接访问）（P5 懒加载）"""
        self._ensure_persona()
        return self._persona_injector
    
    @property
    def persona_distiller(self):
        """获取 Persona 蒸馏器（用于直接访问）（P5 懒加载）"""
        self._ensure_persona()
        return self._persona_distiller
    
    def set_distillation_strategy(self, strategy: str) -> bool:
        """
        设置人格蒸馏策略（P5 懒加载优化）
        
        Args:
            strategy: 策略名称 (conservative/balanced/aggressive/custom)
            
        Returns:
            是否设置成功
        """
        self._ensure_persona()
        
        if not self._distiller_enabled or not self._persona_distiller:
            return False
        
        try:
            # 延迟导入以避免启动时导入开销
            from persona.distiller import DistillationStrategy
            distillation_strategy = DistillationStrategy(strategy)
            self._persona_distiller.config.strategy = distillation_strategy
            logger.info(f"蒸馏策略已设置为: {strategy}")
            return True
        except ValueError:
            logger.error(f"无效的策略: {strategy}")
            return False
    
    def get_distillation_report(self) -> dict:
        """获取人格蒸馏评估报告（P5 懒加载优化）"""
        self._ensure_persona()
        
        if not self._distiller_enabled or not self._persona_distiller:
            return {}
        return self._persona_distiller.get_evaluation_report()
    
    def auto_tune_distiller(self, feedback: float) -> None:
        """
        基于反馈自动调整蒸馏器参数（P5 懒加载优化）
        
        Args:
            feedback: 反馈评分 (0-1 分)
        """
        self._ensure_persona()
        
        if not self._distiller_enabled or not self._persona_distiller:
            return
        
        if not 0 <= feedback <= 1:
            logger.warning(f"反馈评分必须在 0-1 之间，当前: {feedback}")
            return
        
        self._persona_distiller.auto_tune(feedback)
        logger.info(f"已根据反馈 {feedback} 自动调整蒸馏器参数")
    
    def rollback_persona(self, snapshot_name: str = None) -> bool:
        """
        回滚人格到指定快照（P5 懒加载优化）
        
        Args:
            snapshot_name: 快照名称，如果为 None 则回滚到最新快照
            
        Returns:
            是否回滚成功
        """
        self._ensure_persona()
        
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

