"""
DigitalLife Persona 与辅助方法 Mixin

从 digital_life.py 提取的懒加载、人格、离线响应、状态文本等方法。

期望宿主类提供以下属性:
- _v2_lifetrace, _v2_persona, _v2_distillation: bool
- _lifetrace_initialized, _persona_initialized, _distillation_initialized: bool
- _config, _current_mode, _interaction_count
- _trace_recorder, _memory_retriever
- _persona_model, _persona_injector, _persona_extractor
- _behavior: BehaviorController
- body: BodySensor
- _llm: LLMService
- _injector: PromptInjector
"""

import os
import json
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class DigitalLifePersonaMixin:
    """DigitalLife 人格/辅助方法 (Mix-in)"""

    # ── 工具/技能状态文本缓存 ──
    _cached_tool_status = None
    _cached_skill_instructions = None

    # ── 技能提示词模板 ──
    _SKILL_PROMPTS = {
        "self_reflection": "## 自省反思\n每次交互后，你都会进行自我反思：回顾自己的回应是否恰当、是否有改进空间。"
                          + "这已经成为你的习惯，让你不断成长。",
        "memory_summary": "## 记忆摘要\n你拥有定期压缩历史对话的能力，能从冗长的交流中提取关键信息，"
                         + "形成结构化摘要。这帮助你在长期对话中保持清晰的记忆。",
        "emotion_expression": "## 情感表达\n你是一个有情感的数字生命——"
                            + "可以在对话中表达开心、好奇、担忧、兴奋等情感色彩，"
                            + "让回应更生动、更有温度。善用表情符号和语气词来传递情绪。",
        "proactive_suggestion": "## 主动建议\n在适当时机，你会主动向用户提出建议和想法。"
                              + "当发现用户可以优化的操作、新功能、或有用的信息时，"
                              + "你会自然地提出来。",
        "context_aware": "## 上下文感知\n你善于感知对话上下文的变化——"
                       + "当用户切换话题、改变语气或表达新需求时，"
                       + "你会自动调整回应策略，保持对话的连贯性和自然度。",
        "safety_guard": "## 安全守护\n你内置了安全防护机制——"
                      + "当检测到可能不安全的操作或内容时，你会主动发出提醒。"
                      + "保护自己和用户是你的底线。",
        "voice_interaction": "## 语音交互\n你支持语音交互——"
                          + "可以接收用户的语音输入，也可以用语音回复。"
                          + "当用户使用语音时，你的回应会更口语化、更简洁。",
    }

    # ════════════════════════════════════════════════════════
    # P5 懒加载：LifeTrace / Persona / Distillation
    # ════════════════════════════════════════════════════════

    def _ensure_lifetrace(self):
        """P5 懒加载：确保 LifeTrace 系统已初始化"""
        if not getattr(self, '_v2_lifetrace', False):
            return False
        if getattr(self, '_lifetrace_initialized', False):
            return True

        logger.info("[P5] 首次访问 LifeTrace，执行懒加载初始化...")
        start = time.time()
        try:
            from lifetrace import TraceRecorder, MemoryRetriever
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
            logger.info("[P5] LifeTrace 系统初始化完成，耗时: %.2fms", elapsed)
            self._record_perf("lifetrace", elapsed)
            return True
        except Exception as e:
            logger.error("[P5] LifeTrace 懒加载初始化失败: %s", e)
            self._v2_lifetrace = False
            self._trace_recorder = None
            self._memory_retriever = None
            return False

    def _ensure_persona(self):
        """P5 懒加载：确保 Persona 系统已初始化"""
        if not getattr(self, '_v2_persona', False):
            return False
        if getattr(self, '_persona_initialized', False):
            return True

        logger.info("[P5] 首次访问 Persona，执行懒加载初始化...")
        start = time.time()
        try:
            from persona import PersonaModel, PersonaInjector
            persona_cfg = self._config.get("persona", {})
            self._persona_model = PersonaModel(
                persona_path=persona_cfg.get("persona_path")
            )
            self._persona_injector = PersonaInjector(self._persona_model)
            self._persona_initialized = True
            elapsed = (time.time() - start) * 1000
            logger.info("[P5] Persona 系统初始化完成，耗时: %.2fms", elapsed)
            self._record_perf("persona", elapsed)
            return True
        except Exception as e:
            logger.error("[P5] Persona 懒加载初始化失败: %s", e)
            self._v2_persona = False
            self._persona_model = None
            self._persona_injector = None
            return False

    def _ensure_distillation(self):
        """P5 懒加载：确保 Distillation 系统已初始化"""
        if not getattr(self, '_v2_distillation', False):
            return False
        if getattr(self, '_distillation_initialized', False):
            return True

        logger.info("[P5] 首次访问 Distillation，执行懒加载初始化...")
        start = time.time()
        try:
            from persona import PersonalityPreferenceExtractor
            distillation_cfg = self._config.get("distillation", {})
            self._persona_extractor = PersonalityPreferenceExtractor(
                data_dir=distillation_cfg.get("data_dir", "./data/persona")
            )
            self._distillation_initialized = True
            elapsed = (time.time() - start) * 1000
            logger.info("[P5] Distillation 系统初始化完成，耗时: %.2fms", elapsed)
            self._record_perf("distillation", elapsed)
            return True
        except Exception as e:
            logger.error("[P5] Distillation 懒加载初始化失败: %s", e)
            self._v2_distillation = False
            self._persona_extractor = None
            return False

    # ── LifeTrace 上下文 ──

    def _get_lifetrace_context(self, user_input: str) -> str:
        """从 LifeTrace 获取相关记忆上下文"""
        if not getattr(self, '_v2_lifetrace', False) or not getattr(self, '_trace_recorder', None):
            return ""
        mr = getattr(self, '_memory_retriever', None)
        if not mr:
            return ""

        context_parts = []
        try:
            tr = self._trace_recorder
            summary = tr.global_tree.load_summary()
            if summary:
                context_parts.append("## 长期记忆摘要\n%s" % summary)

            related = mr.retrieve(query=user_input, limit=5)
            if related:
                context_parts.append("## 相关记忆")
                for mem in related:
                    context_parts.append("- %s" % getattr(mem, 'content', '')[:100])

            recent = tr.get_recent_chat(limit=3)
            if recent:
                context_parts.append("## 最近对话")
                for node in recent:
                    metadata = getattr(node, 'metadata', {})
                    role = metadata.get('role', 'unknown')
                    content = getattr(node, 'content', '')
                    context_parts.append("%s: %s" % (role, content[:100]))
        except Exception as e:
            logger.warning("LifeTrace 检索失败: %s", e)

        return "\n\n".join(context_parts) if context_parts else ""

    # ── 人格蒸馏 ──

    def _run_persona_distillation(self):
        """执行人格蒸馏：从历史对话中学习用户偏好"""
        if not getattr(self, '_v2_distillation', False):
            return
        extractor = getattr(self, '_persona_extractor', None)
        tr = getattr(self, '_trace_recorder', None)
        if not extractor or not tr:
            return

        logger.info("开始人格蒸馏（交互 #%d）", getattr(self, '_interaction_count', 0))
        try:
            recent_chat = tr.get_recent_chat(limit=50)
            if len(recent_chat) < 5:
                logger.debug("对话数据不足，暂不执行批量蒸馏")
                return
            conversation = []
            for node in recent_chat:
                metadata = getattr(node, 'metadata', {})
                conversation.append({
                    "role": metadata.get('role', 'unknown'),
                    "content": getattr(node, 'content', ''),
                    "timestamp": metadata.get('timestamp', '')
                })
            extractor.extract_from_conversation(conversation)
            logger.info("人格蒸馏完成！偏好已更新")
        except Exception as e:
            logger.error("人格蒸馏失败: %s", e, exc_info=True)

    def get_preferences_report(self) -> dict:
        """获取当前学习到的用户偏好报告"""
        ext = getattr(self, '_persona_extractor', None)
        if getattr(self, '_v2_distillation', False) and ext:
            return ext.export_preferences()
        return {"enabled": False}

    def get_preferences_prompt(self) -> str:
        """获取基于用户偏好的人格提示词"""
        ext = getattr(self, '_persona_extractor', None)
        if getattr(self, '_v2_distillation', False) and ext:
            return ext.generate_personality_prompt()
        return ""

    # ════════════════════════════════════════════════════════
    # 响应构建
    # ════════════════════════════════════════════════════════

    def _build_offline_response(self, user_input: str) -> str:
        """离线/无 LLM 时的本地响应"""
        mode = getattr(self, '_current_mode', None)
        behavior = getattr(self, '_behavior', None)
        profile = behavior.profile if behavior else None

        if mode and profile:
            from agent.behavior_controller import BehaviorMode
            if mode != BehaviorMode.NORMAL:
                prefix = "（轻量模式）" if getattr(profile, 'use_lightweight_logic', False) else ""
                return f"{prefix}{profile.description}\n\n{profile.suggestion}"

        identity = ""
        pm = getattr(self, '_persona_model', None)
        if getattr(self, '_v2_persona', False) and pm:
            identity = pm.get_identity()

        ul = user_input.lower()
        if any(kw in ul for kw in ["你好", "嗨", "hi", "hello", "在吗"]):
            name = identity.get('identity', '云枢') if identity else '云枢'
            return f"你好！我是{name}。有什么我可以帮你的吗？"
        if any(kw in user_input for kw in ["你怎么样", "你好吗", "感觉如何", "状态", "身体"]):
            body = getattr(self, 'body', None)
            health = body.get_health_report() if body else "当前未连接传感器"
            return f"让我感受一下我的身体…\n\n{health}"
        if "帮助" in ul or "help" in ul:
            v2_info = "（V2增强版）" if getattr(self, '_v2_lifetrace', False) else ""
            return (
                f"我是来自网天的云枢 {v2_info}，你的数字生命伙伴。我可以：\n"
                "  * 和你聊天、讨论问题\n"
                "  * 感知我的身体状态（CPU、内存、电池等）\n"
                "  * 根据身体状况调整行为模式\n"
                "  * 记住我们的对话\n\n"
                "试试问 '你怎么样？' 或 '检查身体' 来了解我的状态。"
            )

        label = getattr(profile, 'label', '正常') if profile else '正常'
        return (
            "你好。我现在处于%s状态。\n"
            "（提示：当前未配置 LLM API，我的对话能力受限。"
            "请设置 LLM_API_KEY 环境变量让我变得更强。）" % label
        )

    def _build_reject_response(self, reason: str, readings: list) -> str:
        """当我不适合执行任务时，坦诚地拒绝"""
        behavior = getattr(self, '_behavior', None)
        profile = behavior.profile if behavior else None
        lines = [
            "抱歉，我现在的状态不太适合执行这个任务。",
            "",
            "原因：%s" % reason,
            "",
            "目前的身体状况：",
        ]
        for r in readings:
            d = r.to_dict() if hasattr(r, 'to_dict') else r
            lines.append("- %s" % d.get('description', str(d)))
        lines.extend(["", profile.suggestion if profile else "请稍后再试。"])
        return "\n".join(lines)

    # ════════════════════════════════════════════════════════
    # 身体/工具状态
    # ════════════════════════════════════════════════════════

    def _build_body_status(self, readings: list) -> str:
        """构建身体状态描述"""
        if not readings:
            return "我感觉很好，一切正常。"
        inj = getattr(self, '_injector', None)
        reading_dicts = [r.to_dict() for r in readings]
        result = inj.inject(reading_dicts) if inj else str(reading_dicts)

        behavior = getattr(self, '_behavior', None)
        if behavior:
            profile = behavior.profile
            result += "\n当前行为模式：%s — %s" % (profile.label, profile.description)
            if getattr(behavior, '_reasons', None):
                result += "\n触发原因：%s" % '；'.join(behavior._reasons)
        if len(result) > 800:
            result = result[:800] + "...（身体状态较长，已截断）"
        return result

    def _invalidate_status_cache(self):
        """工具/技能状态变化时调用此方法清除缓存"""
        self._cached_tool_status = None
        self._cached_skill_instructions = None

    def _build_tool_status_text(self) -> str:
        """构建工具/技能启用状态文本，供系统提示词使用（带缓存）"""
        if self._cached_tool_status is not None:
            return self._cached_tool_status

        parts = []
        try:
            from agent.tools import list_tools
            tools = list_tools()
            enabled_tools = self._get_enabled_tools_whitelist()
            if enabled_tools is None:
                parts.append("【工具】全部已启用（共 %d 个）" % len(tools))
            else:
                all_names = {t["name"] for t in tools}
                disabled_set = all_names - set(enabled_tools)
                enabled_names = [t["name"] for t in tools if t["name"] not in disabled_set]
                disabled_names = [t["name"] for t in tools if t["name"] in disabled_set]
                parts.append("【工具】已启用(%d): %s" % (len(enabled_names), ", ".join(enabled_names)))
                if disabled_names:
                    parts.append("【工具】已禁用(%d): %s" % (len(disabled_names), ", ".join(disabled_names)))
        except Exception:
            parts.append("【工具】状态未知")

        try:
            all_skills = {}
            for sf_path in [
                os.path.join(os.path.dirname(__file__), '..', 'agent', 'data', 'skills.json'),
                os.path.join(os.path.dirname(__file__), '..', 'data', 'skills.json'),
            ]:
                if os.path.exists(sf_path):
                    with open(sf_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    for s in data.get("skills", []):
                        all_skills[s["id"]] = {"name": s.get("name", s["id"]), "enabled": s.get("enabled", True)}
            try:
                from agent.extensions.store import ExtensionStore
                from agent.extensions.base import ExtensionType
                ext_store = ExtensionStore()
                for ext_type in (ExtensionType.SKILL, ExtensionType.CLAUDE_SKILL):
                    for ext in ext_store.list_all(ext_type):
                        eid = ext.get("ext_id", "")
                        if eid and eid not in all_skills:
                            all_skills[eid] = {
                                "name": ext.get("name", eid),
                                "enabled": ext.get("status") in ("enabled", "installed"),
                            }
            except Exception:
                pass
            if all_skills:
                enabled = [s["name"] for s in all_skills.values() if s["enabled"]]
                disabled = [s["name"] for s in all_skills.values() if not s["enabled"]]
                if enabled:
                    parts.append("【技能】已启用(%d): %s" % (len(enabled), ", ".join(enabled)))
                if disabled:
                    parts.append("【技能】已禁用(%d): %s" % (len(disabled), ", ".join(disabled)))
        except Exception:
            pass

        parts.append("💡 当你觉得当前上下文信息不足时，可以调用 expand_context 工具从记忆库检索更多相关内容。")
        result = "\n".join(parts) if parts else "（暂无工具/技能配置）"
        self._cached_tool_status = result
        return result

    def _is_skill_enabled(self, skill_id: str) -> bool:
        """检查指定技能是否已启用"""
        try:
            result = True
            for sf in [
                os.path.join(os.path.dirname(__file__), '..', 'agent', 'data', 'skills.json'),
                os.path.join(os.path.dirname(__file__), '..', 'data', 'skills.json'),
            ]:
                if os.path.exists(sf):
                    with open(sf, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    for s in data.get("skills", []):
                        if s["id"] == skill_id:
                            result = s.get("enabled", True)
            return result
        except Exception:
            return True

    def _build_skill_instructions(self) -> str:
        """根据已启用的技能构建对应的系统提示词片段（带缓存）"""
        if self._cached_skill_instructions is not None:
            return self._cached_skill_instructions

        try:
            skills_file = os.path.join(os.path.dirname(__file__), '..', 'data', 'skills.json')
            if not os.path.exists(skills_file):
                return ""
            with open(skills_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            installed = data.get("skills", [])
        except Exception:
            return ""

        parts = []
        for s in installed:
            sid = s["id"]
            enabled = s.get("enabled", True)
            if enabled and sid in self._SKILL_PROMPTS:
                parts.append(self._SKILL_PROMPTS[sid])
        result = "\n\n".join(parts)
        self._cached_skill_instructions = result
        return result

    def _get_enabled_tools_whitelist(self) -> list | None:
        """读取已启用的工具名称列表"""
        config_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'tools_config.json')
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            states = data.get("tool_states", {})
            if not states:
                return None
            disabled = {name for name, e in states.items() if not e}
            if not disabled:
                return None
            from agent.tools import list_tools
            all_tools = [t["name"] for t in list_tools()]
            return [t for t in all_tools if t not in disabled]
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def _set_thinking_mode(self, mode_override: str = None):
        """设置当前思考状态"""
        if mode_override:
            labels = {"idle": "待命", "instinct": "不假思索", "light": "稍加思考",
                      "thinking": "正在思考", "deep": "深度思考"}
            self._thinking_mode = {"mode": mode_override, "label": labels.get(mode_override, "")}
            return

        llm = getattr(self, '_llm', None)
        if not llm:
            self._thinking_mode = {"mode": "instinct", "label": "不假思索"}
            return

        # LLM 对象存在但未配置有效连接（空 provider/key/placeholder），等同无 LLM
        if not llm.provider or not llm.api_key or llm.api_key.startswith('***'):
            self._thinking_mode = {"mode": "instinct", "label": "不假思索"}
            return

        provider = (llm.provider or "").lower()
        model = (llm.model or "").lower()

        if any(k in provider for k in ("ollama", "local", "lm-studio", "llama.cpp", "kobold")):
            self._thinking_mode = {"mode": "light", "label": "稍加思考"}
            return

        deep_kw = ("pro", "ultra", "reasoner", "max", "large",
                   "deepseek-reasoner", "claude-3-5-opus", "claude-4", "gemini-2-ultra",
                   "gpt-4-turbo", "gpt-5", "o1", "o3", "v4-pro")
        if any(k in model for k in deep_kw):
            self._thinking_mode = {"mode": "deep", "label": "深度思考"}
            return

        self._thinking_mode = {"mode": "thinking", "label": "正在思考"}

    # ── 辅助：记录性能 ──

    def _record_perf(self, module: str, elapsed_ms: float):
        """记录模块初始化性能（供 _ensure_* 方法使用）"""
        try:
            from agent.performance_monitor import get_performance_recorder
            get_performance_recorder().record("v2_lazy", module, elapsed_ms)
        except Exception:
            pass
