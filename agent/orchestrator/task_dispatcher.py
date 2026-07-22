"""TaskDispatcher — 云枢任务调度器

职责:
- 任务类型判断与分发决策
- 复杂度评估（是否需要规划引擎）
- 智能模型选择（待命模型 vs 深度模型）
- 智能工具选择（路由到相关工具）
"""

import logging

logger = logging.getLogger(__name__)


class TaskDispatcher:
    """云枢任务调度与分发

    负责评估任务复杂度，决策使用哪种处理路径（直接/规划/深度模型），
    并为任务选择最合适的工具集合。

    继承自 DigitalLifePersonaMixin 和 DigitalLifeStateMixin 的宿主类
    应对外暴露 dispatch_task 作为任务分发的统一入口。
    """

    def dispatch_task(self, user_input: str) -> dict:
        """统一任务分发入口

        分析输入并返回任务处理策略:
        - 处理路径: direct / planning / v2
        - 模型选择: 待命模型 / 深度模型
        - 工具列表: 相关工具白名单

        Args:
            user_input: 用户输入

        Returns:
            dict: {path, model, tools_whitelist, needs_planning, complexity}
        """
        from agent import tools as _tools

        needs_planning = self._needs_planning(user_input)
        _llm, _model = self._select_model_for_request(user_input)
        whitelist = self._get_enabled_tools_whitelist()

        # 智能工具选择
        if self._is_smart_tool_selection_enabled():
            try:
                from agent.tool_router import get_tools_for_input
                from agent.tool_router_hybrid import hybrid_select_tools
                _smart = hybrid_select_tools(user_input, whitelist) or get_tools_for_input(user_input, whitelist)
                if _smart:
                    whitelist = _smart
                    logger.info("[任务分发] 智能工具选择: %d/%d 个工具",
                                len(_smart), len(_tools.list_tools()))
            except Exception as _e:
                logger.debug("工具路由失败: %s", _e)

        return {
            "path": "planning" if needs_planning else "direct",
            "model": _model,
            "tools_whitelist": whitelist,
            "needs_planning": needs_planning,
        }

    def _needs_planning(self, message: str) -> bool:
        """判断是否需要规划

        基于关键词规则评估任务复杂度。
        若规划引擎不可用或禁用，始终返回 False。
        """
        planner = getattr(self, '_planner', None)
        planning_enabled = getattr(self, '_planning_enabled', False)

        if not planning_enabled or not planner:
            return False

        complex_indicators = [
            "帮我完成", "帮我创建", "帮我分析",
            "帮我构建", "流程", "系统",
            "第一步", "第二步", "然后", "接下来",
        ]
        complex_count = sum(1 for indicator in complex_indicators if indicator in message)

        action_keywords = ["检查", "分析", "创建", "生成", "整理", "监控"]
        action_count = sum(1 for keyword in action_keywords if keyword in message.lower())

        needs_planning = complex_count >= 1 or action_count >= 2

        logger.info("  复杂关键词匹配: %d 个", complex_count)
        logger.info("  动作关键词匹配: %d 个", action_count)
        result_text = "需要规划" if needs_planning else "简单任务"
        logger.info("  评估结果: %s", result_text)

        return needs_planning

    def _select_model_for_request(self, user_input: str):
        """智能调度：根据任务复杂度选择模型

        策略:
          - 简单/聊天任务 → 待命模型（flash，快速响应）
          - 单步工具调用（搜索等）→ 待命模型
          - 复杂多步任务（搜索+写入文件等）→ 深度模型（pro，多轮TC）

        Returns:
            (llm_service, model_name): 选中的 LLM 和名称
        """
        llm = getattr(self, '_llm', None)
        llm_pro = getattr(self, '_llm_pro', None)
        model_router = getattr(self, '_model_router', None)

        router_ok = model_router is not None
        pro_ok = llm_pro is not None

        logger.info("[调度] 选择模型: input=%s router=%s pro=%s standby=%s",
                    user_input[:15], router_ok, pro_ok,
                    llm.model if llm else 'N/A')

        # 没有深度模型 → 尝试从路由器延迟加载
        if not pro_ok and router_ok:
            try:
                _pro_cfg = model_router.select('complex')
                if _pro_cfg:
                    from memory.llm_service import LLMService
                    llm_pro = LLMService(**_pro_cfg.to_llm_kwargs())
                    llm_pro._get_client()
                    self._llm_pro = llm_pro
                    pro_ok = True
                    logger.info("[调度] 延迟加载深度模型成功: %s", _pro_cfg.model)
            except Exception as _e:
                logger.warning("[调度] 延迟加载深度模型失败: %s", _e)

        if not router_ok or not pro_ok:
            logger.warning("[调度] 无深度模型(router=%s pro=%s)，使用待命模型 %s",
                          router_ok, pro_ok, llm.model if llm else 'N/A')
            self._set_thinking_mode()
            return llm, llm.model

        try:
            _complexity = model_router.analyze_complexity(user_input)
        except Exception:
            _complexity = 'simple'

        if _complexity == 'complex' and self._llm_pro:
            logger.info("[调度] 复杂任务(%s) → 深度模型 %s", _complexity, self._llm_pro.model)
            self._set_thinking_mode('deep')
            return self._llm_pro, self._llm_pro.model

        # 简单/单步 → 待命模型
        self._set_thinking_mode()
        return llm, llm.model

    def _is_smart_tool_selection_enabled(self) -> bool:
        """检查智能工具选择是否启用

        通过 agent.system_prompt_config.is_section_enabled 查询 smart_tool_selection
        配置节状态。配置模块不可用或查询异常时返回 False（保守禁用）。
        """
        try:
            from agent.system_prompt_config import is_section_enabled
            return is_section_enabled("smart_tool_selection", default=False)
        except Exception:
            return False

    def _is_extra_section_enabled(self, section_name: str, default: bool = True) -> bool:
        """检查额外配置节是否启用

        通过 agent.system_prompt_config.is_section_enabled 查询配置节状态。
        配置模块不可用或查询异常时返回 default。

        Args:
            section_name: 配置节名称（如 tool_definitions / working_memory / lifetrace）
            default: 配置节不存在或查询失败时的默认返回值
        """
        try:
            from agent.system_prompt_config import is_section_enabled
            return is_section_enabled(section_name, default=default)
        except Exception:
            return default
