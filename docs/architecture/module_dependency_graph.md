# 模块依赖图（自动生成）

```mermaid
flowchart LR
    classDef violation fill:#ff4444,stroke:#cc0000,color:#fff,stroke-width:2px
    classDef crosslayer fill:#fff3cd,stroke:#ffc107,color:#664d03
    subgraph audit [audit]
        agent_audit["agent.audit"]
        agent_audit_logger["agent.audit.logger"]
        agent_audit_observability["agent.audit.observability"]
    end
    subgraph caching [caching]
        agent_caching_multi_level_cache["agent.caching.multi_level_cache"]:::crosslayer
        agent_caching_observability["agent.caching.observability"]
    end
    subgraph cognitive [cognitive]
        agent_cognitive["agent.cognitive"]
        agent_cognitive_actor_critic["agent.cognitive.actor_critic"]
        agent_cognitive_critic["agent.cognitive.critic"]
        agent_cognitive_debate["agent.cognitive.debate"]
        agent_cognitive_failure_analysis["agent.cognitive.failure_analysis"]:::crosslayer
        agent_cognitive_failure_bucket["agent.cognitive.failure_bucket"]
        agent_cognitive_failure_collector["agent.cognitive.failure_collector"]
        agent_cognitive_knowledge["agent.cognitive.knowledge"]
        agent_cognitive_logging_integration["agent.cognitive.logging_integration"]
        agent_cognitive_loop["agent.cognitive.loop"]
        agent_cognitive_observability["agent.cognitive.observability"]
        agent_cognitive_prompt_optimizer["agent.cognitive.prompt_optimizer"]:::crosslayer
        agent_cognitive_reflection["agent.cognitive.reflection"]:::crosslayer
    end
    subgraph common [common]
        agent_common_stop_mixin["agent.common.stop_mixin"]:::crosslayer
    end
    subgraph config [config]
        agent_config_etcd_config_client["agent.config.etcd_config_client"]
    end
    subgraph context [context]
        agent_context_assembler["agent.context.assembler"]:::crosslayer
    end
    subgraph core [core]
        agent_ab_testing["agent.ab_testing"]
        agent_api_gateway["agent.api_gateway"]
        agent_async_executor["agent.async_executor"]:::crosslayer
        agent_auto_tuner["agent.auto_tuner"]
        agent_autonomy["agent.autonomy"]:::crosslayer
        agent_behavior_controller["agent.behavior_controller"]:::crosslayer
        agent_circuit_breaker["agent.circuit_breaker"]:::crosslayer
        agent_code_review["agent.code_review"]:::crosslayer
        agent_compression_tools["agent.compression_tools"]:::crosslayer
        agent_config_validation["agent.config_validation"]:::crosslayer
        agent_data_analytics["agent.data_analytics"]
        agent_data_process_tools["agent.data_process_tools"]:::crosslayer
        agent_diagram_tools["agent.diagram_tools"]:::crosslayer
        agent_diff_tools["agent.diff_tools"]:::crosslayer
        agent_digital_life["agent.digital_life"]:::crosslayer
        agent_digital_life_persona["agent.digital_life_persona"]
        agent_digital_life_state["agent.digital_life_state"]
        agent_disaster_recovery["agent.disaster_recovery"]:::crosslayer
        agent_env_config_manager["agent.env_config_manager"]
        agent_error_handler["agent.error_handler"]:::crosslayer
        agent_error_reporting_config["agent.error_reporting_config"]:::crosslayer
        agent_feedback["agent.feedback"]:::crosslayer
        agent_graceful_degrade["agent.graceful_degrade"]:::crosslayer
        agent_lazy_loader_async["agent.lazy_loader_async"]
        agent_learning_budget["agent.learning_budget"]
        agent_learning_metrics["agent.learning_metrics"]:::crosslayer
        agent_learning_metrics_api["agent.learning_metrics_api"]
        agent_llm_monitor["agent.llm_monitor"]
        agent_llm_response_cache["agent.llm_response_cache"]
        agent_logging_utils["agent.logging_utils"]:::crosslayer
        agent_mcp_executor["agent.mcp_executor"]
        agent_memory_optimized["agent.memory_optimized"]
        agent_modules_api["agent.modules_api"]
        agent_modules_registry["agent.modules_registry"]
        agent_multi_tenant["agent.multi_tenant"]
        agent_network_config["agent.network_config"]:::crosslayer
        agent_p6_config_loader["agent.p6_config_loader"]
        agent_p6_snapshot["agent.p6_snapshot"]
        agent_pdf_tools["agent.pdf_tools"]:::crosslayer
        agent_performance_logging["agent.performance_logging"]
        agent_performance_monitor["agent.performance_monitor"]
        agent_permission_system["agent.permission_system"]:::crosslayer
        agent_prometheus_exporter["agent.prometheus_exporter"]:::crosslayer
        agent_rate_limiter["agent.rate_limiter"]:::crosslayer
        agent_response_workflows["agent.response_workflows"]:::crosslayer
        agent_safety_guard["agent.safety_guard"]
        agent_scheduling["agent.scheduling"]:::crosslayer
        agent_search_aggregator["agent.search_aggregator"]:::crosslayer
        agent_search_performance_monitor["agent.search_performance_monitor"]:::crosslayer
        agent_security_utils["agent.security_utils"]
        agent_server_auth["agent.server_auth"]:::crosslayer
        agent_server_ui["agent.server_ui"]:::crosslayer
        agent_software_backends["agent.software_backends"]:::crosslayer
        agent_software_manager["agent.software_manager"]:::crosslayer
        agent_state_manager["agent.state_manager"]:::crosslayer
        agent_system_prompt_config["agent.system_prompt_config"]:::crosslayer
        agent_system_prompt_manager["agent.system_prompt_manager"]:::crosslayer
        agent_system_tools["agent.system_tools"]:::crosslayer
        agent_task_scheduler["agent.task_scheduler"]:::crosslayer
        agent_test_permission_system["agent.test_permission_system"]
        agent_text_tools["agent.text_tools"]:::crosslayer
        agent_tool_calling["agent.tool_calling"]:::crosslayer
        agent_tool_fewshot_store["agent.tool_fewshot_store"]:::crosslayer
        agent_tool_router["agent.tool_router"]:::crosslayer
        agent_tool_router_hybrid["agent.tool_router_hybrid"]:::crosslayer
        agent_tool_router_reranker["agent.tool_router_reranker"]:::crosslayer
        agent_tool_schema_pruner["agent.tool_schema_pruner"]:::crosslayer
        agent_v2_performance_patch["agent.v2_performance_patch"]
        agent_weekly_report_generator["agent.weekly_report_generator"]
    end
    subgraph dao [dao]
        agent_data_observability["agent.data.observability"]
    end
    subgraph evolution [evolution]
        agent_evolution_injector["agent.evolution.injector"]:::crosslayer
    end
    subgraph extensions [extensions]
        agent_extensions["agent.extensions"]
        agent_extensions_base["agent.extensions.base"]:::crosslayer
        agent_extensions_channels_installer["agent.extensions.channels_installer"]
        agent_extensions_dependency_manager["agent.extensions.dependency_manager"]
        agent_extensions_installer["agent.extensions.installer"]
        agent_extensions_manager["agent.extensions.manager"]:::crosslayer
        agent_extensions_market["agent.extensions.market"]:::crosslayer
        agent_extensions_mcp_installer["agent.extensions.mcp_installer"]
        agent_extensions_observability["agent.extensions.observability"]
        agent_extensions_plugins_installer["agent.extensions.plugins_installer"]
        agent_extensions_sandbox["agent.extensions.sandbox"]
        agent_extensions_security_check_skill["agent.extensions.security_check_skill"]
        agent_extensions_security_checker["agent.extensions.security_checker"]
        agent_extensions_skills_installer["agent.extensions.skills_installer"]
        agent_extensions_store["agent.extensions.store"]:::crosslayer
    end
    subgraph feedback_collector [feedback_collector]
        agent_feedback_collector["agent.feedback_collector"]:::crosslayer
    end
    subgraph guardrails [guardrails]
        agent_guardrails_input_guard["agent.guardrails.input_guard"]:::crosslayer
        agent_guardrails_observability["agent.guardrails.observability"]
        agent_guardrails_output_guard["agent.guardrails.output_guard"]:::crosslayer
        agent_guardrails_output_schema["agent.guardrails.output_schema"]
    end
    subgraph handoff [handoff]
        agent_handoff_handoff_generator["agent.handoff.handoff_generator"]:::crosslayer
    end
    subgraph health [health]
        agent_health_assessor["agent.health.assessor"]:::crosslayer
        agent_health_collector["agent.health.collector"]
        agent_health_dashboard["agent.health.dashboard"]:::crosslayer
        agent_health_health_score["agent.health.health_score"]:::crosslayer
        agent_health_observability["agent.health.observability"]
        agent_health_probes["agent.health.probes"]
        agent_health_storage["agent.health.storage"]
    end
    subgraph human_in_the_loop [human_in_the_loop]
        agent_human_in_the_loop["agent.human_in_the_loop"]
        agent_human_in_the_loop_ethics["agent.human_in_the_loop.ethics"]
        agent_human_in_the_loop_hitl["agent.human_in_the_loop.hitl"]
        agent_human_in_the_loop_observability["agent.human_in_the_loop.observability"]
        agent_human_in_the_loop_takeover_queue["agent.human_in_the_loop.takeover_queue"]:::crosslayer
    end
    subgraph knowledge [knowledge]
        agent_knowledge["agent.knowledge"]:::crosslayer
        agent_knowledge___main__["agent.knowledge.__main__"]
        agent_knowledge_audit_job["agent.knowledge.audit_job"]
        agent_knowledge_card["agent.knowledge.card"]
        agent_knowledge_conflict["agent.knowledge.conflict"]
        agent_knowledge_discuss["agent.knowledge.discuss"]
        agent_knowledge_distill["agent.knowledge.distill"]
        agent_knowledge_distill_feedback["agent.knowledge.distill_feedback"]
        agent_knowledge_index["agent.knowledge.index"]
        agent_knowledge_ingest["agent.knowledge.ingest"]
        agent_knowledge_lifecycle["agent.knowledge.lifecycle"]
        agent_knowledge_light_loader["agent.knowledge.light_loader"]
        agent_knowledge_link_cache["agent.knowledge.link_cache"]
        agent_knowledge_links["agent.knowledge.links"]
        agent_knowledge_links_index["agent.knowledge.links_index"]:::crosslayer
        agent_knowledge_lint["agent.knowledge.lint"]:::crosslayer
        agent_knowledge_logbook["agent.knowledge.logbook"]
        agent_knowledge_observability["agent.knowledge.observability"]
        agent_knowledge_prompts["agent.knowledge.prompts"]
        agent_knowledge_reporting["agent.knowledge.reporting"]
        agent_knowledge_schema["agent.knowledge.schema"]
        agent_knowledge_search["agent.knowledge.search"]
        agent_knowledge_skill_bridge["agent.knowledge.skill_bridge"]
        agent_knowledge_tools["agent.knowledge.tools"]
        agent_knowledge_watcher["agent.knowledge.watcher"]
        agent_knowledge_workflow["agent.knowledge.workflow"]
    end
    subgraph lazy_loader [lazy_loader]
        agent_lazy_loader["agent.lazy_loader"]:::crosslayer
        agent_lazy_loader__core["agent.lazy_loader._core"]:::crosslayer
        agent_lazy_loader_observability["agent.lazy_loader.observability"]
    end
    subgraph learning [learning]
        agent_learning_behavior_drift["agent.learning.behavior_drift"]:::crosslayer
        agent_learning_novelty_hooks["agent.learning.novelty_hooks"]:::crosslayer
    end
    subgraph log_system [log_system]
        agent_log_system_dashboard["agent.log_system.dashboard"]
        agent_log_system_emoji_map["agent.log_system.emoji_map"]
        agent_log_system_formatter["agent.log_system.formatter"]
        agent_log_system_handlers["agent.log_system.handlers"]
        agent_log_system_introspection["agent.log_system.introspection"]
        agent_log_system_models["agent.log_system.models"]
        agent_log_system_observability["agent.log_system.observability"]
        agent_log_system_optimized_storage["agent.log_system.optimized_storage"]
        agent_log_system_safe_logger["agent.log_system.safe_logger"]
        agent_log_system_storage["agent.log_system.storage"]:::crosslayer
    end
    subgraph memory [memory]
        agent_memory["agent.memory"]
        agent_memory_adapters["agent.memory.adapters"]
        agent_memory_adapters_holographic_adapter["agent.memory.adapters.holographic_adapter"]
        agent_memory_adapters_mem0_adapter["agent.memory.adapters.mem0_adapter"]
        agent_memory_base["agent.memory.base"]
        agent_memory_context_assembler["agent.memory.context_assembler"]
        agent_memory_file_watcher["agent.memory.file_watcher"]
        agent_memory_filter["agent.memory.filter"]
        agent_memory_hotness_scorer["agent.memory.hotness_scorer"]
        agent_memory_long_term_memory["agent.memory.long_term_memory"]:::crosslayer
        agent_memory_markdown_syncer["agent.memory.markdown_syncer"]
        agent_memory_observability["agent.memory.observability"]
        agent_memory_reviewer["agent.memory.reviewer"]:::crosslayer
        agent_memory_router["agent.memory.router"]:::crosslayer
        agent_memory_short_term_memory["agent.memory.short_term_memory"]:::crosslayer
    end
    subgraph model_router [model_router]
        agent_model_router_adapters["agent.model_router.adapters"]
        agent_model_router_observability["agent.model_router.observability"]
        agent_model_router_router["agent.model_router.router"]
    end
    subgraph monitoring [monitoring]
        agent_monitoring["agent.monitoring"]:::crosslayer
        agent_monitoring_alert_evaluator["agent.monitoring.alert_evaluator"]
        agent_monitoring_alert_manager["agent.monitoring.alert_manager"]:::crosslayer
        agent_monitoring_alert_notifier["agent.monitoring.alert_notifier"]
        agent_monitoring_business_metrics["agent.monitoring.business_metrics"]:::crosslayer
        agent_monitoring_chaos_injector["agent.monitoring.chaos_injector"]
        agent_monitoring_config_observability["agent.monitoring.config_observability"]
        agent_monitoring_decorators["agent.monitoring.decorators"]
        agent_monitoring_error_reporter["agent.monitoring.error_reporter"]
        agent_monitoring_llm_monitor["agent.monitoring.llm_monitor"]:::crosslayer
        agent_monitoring_loki["agent.monitoring.loki"]:::crosslayer
        agent_monitoring_metrics["agent.monitoring.metrics"]:::crosslayer
        agent_monitoring_observability_config["agent.monitoring.observability_config"]:::crosslayer
        agent_monitoring_observability_optimizations["agent.monitoring.observability_optimizations"]
        agent_monitoring_optimized_metrics["agent.monitoring.optimized_metrics"]
        agent_monitoring_performance["agent.monitoring.performance"]:::crosslayer
        agent_monitoring_performance_optimization["agent.monitoring.performance_optimization"]
        agent_monitoring_prometheus["agent.monitoring.prometheus"]:::crosslayer
        agent_monitoring_replay_storage["agent.monitoring.replay_storage"]:::crosslayer
        agent_monitoring_resource_monitor["agent.monitoring.resource_monitor"]
        agent_monitoring_search["agent.monitoring.search"]:::crosslayer
        agent_monitoring_self_healer["agent.monitoring.self_healer"]
        agent_monitoring_sensitive_data_filter["agent.monitoring.sensitive_data_filter"]:::crosslayer
        agent_monitoring_trace_http_client["agent.monitoring.trace_http_client"]
        agent_monitoring_tracing["agent.monitoring.tracing"]:::crosslayer
        agent_monitoring_tracing_cache["agent.monitoring.tracing_cache"]
        agent_monitoring_tracing_config["agent.monitoring.tracing_config"]
        agent_monitoring_tracing_perf["agent.monitoring.tracing_perf"]
        agent_monitoring_tracing_sampling["agent.monitoring.tracing_sampling"]
        agent_monitoring_utils["agent.monitoring.utils"]
    end
    subgraph network [network]
        agent_network["agent.network"]
        agent_network_config_validator["agent.network.config_validator"]
        agent_network_observability["agent.network.observability"]
    end
    subgraph observability [observability]
        agent_observability_arch_rules["agent.observability.arch_rules"]
        agent_observability_dependency_graph["agent.observability.dependency_graph"]
        agent_observability_subscriber["agent.observability.subscriber"]:::crosslayer
        agent_observability_tool_trace["agent.observability.tool_trace"]:::crosslayer
        agent_observability_tracer["agent.observability.tracer"]:::crosslayer
    end
    subgraph orchestrator [orchestrator]
        agent_orchestrator["agent.orchestrator"]:::crosslayer
        agent_orchestrator_dialog_state["agent.orchestrator.dialog_state"]
        agent_orchestrator_lifecycle_manager["agent.orchestrator.lifecycle_manager"]
        agent_orchestrator_message_handler["agent.orchestrator.message_handler"]
        agent_orchestrator_observability["agent.orchestrator.observability"]
        agent_orchestrator_orchestrator["agent.orchestrator.orchestrator"]:::crosslayer
        agent_orchestrator_prompt_builder["agent.orchestrator.prompt_builder"]
        agent_orchestrator_response_builder["agent.orchestrator.response_builder"]
        agent_orchestrator_routing_observability["agent.orchestrator.routing_observability"]
        agent_orchestrator_status_reporter["agent.orchestrator.status_reporter"]
        agent_orchestrator_subagent_manager["agent.orchestrator.subagent_manager"]
        agent_orchestrator_task_dispatcher["agent.orchestrator.task_dispatcher"]
    end
    subgraph p6 [p6]
        agent_p6["agent.p6"]
        agent_p6_frequency["agent.p6.frequency"]
        agent_p6_observability["agent.p6.observability"]
        agent_p6_performance["agent.p6.performance"]
        agent_p6_snapshot["agent.p6.snapshot"]
    end
    subgraph preflight [preflight]
        agent_preflight["agent.preflight"]
        agent_preflight___main__["agent.preflight.__main__"]
        agent_preflight_runner["agent.preflight.runner"]
    end
    subgraph prompt_manager [prompt_manager]
        agent_prompt_manager_deployment["agent.prompt_manager.deployment"]
        agent_prompt_manager_observability["agent.prompt_manager.observability"]
        agent_prompt_manager_registry["agent.prompt_manager.registry"]
        agent_prompt_manager_storage["agent.prompt_manager.storage"]
        agent_prompt_manager_version_control["agent.prompt_manager.version_control"]
    end
    subgraph quality [quality]
        agent_quality_defect_tracker["agent.quality.defect_tracker"]
        agent_quality_observability["agent.quality.observability"]
    end
    subgraph self_healing [self_healing]
        agent_self_healing_policy["agent.self_healing.policy"]:::crosslayer
    end
    subgraph server_routes [server_routes]
        agent_server_routes_extensions["agent.server_routes.extensions"]
        agent_server_routes_observability["agent.server_routes.observability"]
        agent_server_routes_routes_assets["agent.server_routes.routes_assets"]
        agent_server_routes_routes_business_dashboard["agent.server_routes.routes_business_dashboard"]
        agent_server_routes_routes_chat["agent.server_routes.routes_chat"]
        agent_server_routes_routes_config["agent.server_routes.routes_config"]
        agent_server_routes_routes_dashboard["agent.server_routes.routes_dashboard"]
        agent_server_routes_routes_feedback["agent.server_routes.routes_feedback"]
        agent_server_routes_routes_health["agent.server_routes.routes_health"]
        agent_server_routes_routes_knowledge["agent.server_routes.routes_knowledge"]
        agent_server_routes_routes_llm_monitor["agent.server_routes.routes_llm_monitor"]
        agent_server_routes_routes_logging["agent.server_routes.routes_logging"]
        agent_server_routes_routes_memory["agent.server_routes.routes_memory"]
        agent_server_routes_routes_monitoring["agent.server_routes.routes_monitoring"]
        agent_server_routes_routes_panorama["agent.server_routes.routes_panorama"]
        agent_server_routes_routes_permission["agent.server_routes.routes_permission"]
        agent_server_routes_routes_personality["agent.server_routes.routes_personality"]
        agent_server_routes_routes_replay["agent.server_routes.routes_replay"]
        agent_server_routes_routes_sessions["agent.server_routes.routes_sessions"]
        agent_server_routes_routes_skills["agent.server_routes.routes_skills"]
        agent_server_routes_routes_skills_mgmt["agent.server_routes.routes_skills_mgmt"]
        agent_server_routes_routes_subagent["agent.server_routes.routes_subagent"]
        agent_server_routes_routes_system_prompt["agent.server_routes.routes_system_prompt"]
        agent_server_routes_routes_workflow_learning["agent.server_routes.routes_workflow_learning"]
        agent_server_routes_routes_workspace["agent.server_routes.routes_workspace"]
        agent_server_routes_tracing_decorator["agent.server_routes.tracing_decorator"]
        agent_server_routes_tracing_middleware["agent.server_routes.tracing_middleware"]
    end
    subgraph skills_mgmt [skills_mgmt]
        agent_skills_mgmt["agent.skills_mgmt"]:::crosslayer
        agent_skills_mgmt_bm25_searcher["agent.skills_mgmt.bm25_searcher"]
        agent_skills_mgmt_context_injector["agent.skills_mgmt.context_injector"]
        agent_skills_mgmt_creator["agent.skills_mgmt.creator"]:::crosslayer
        agent_skills_mgmt_enhancer["agent.skills_mgmt.enhancer"]:::crosslayer
        agent_skills_mgmt_evaluator["agent.skills_mgmt.evaluator"]:::crosslayer
        agent_skills_mgmt_evolution_scheduler["agent.skills_mgmt.evolution_scheduler"]
        agent_skills_mgmt_exceptions["agent.skills_mgmt.exceptions"]:::crosslayer
        agent_skills_mgmt_executor["agent.skills_mgmt.executor"]
        agent_skills_mgmt_feedback_agent["agent.skills_mgmt.feedback_agent"]
        agent_skills_mgmt_few_shot_injector["agent.skills_mgmt.few_shot_injector"]
        agent_skills_mgmt_file_store["agent.skills_mgmt.file_store"]
        agent_skills_mgmt_index_cache["agent.skills_mgmt.index_cache"]
        agent_skills_mgmt_learning_scheduler["agent.skills_mgmt.learning_scheduler"]
        agent_skills_mgmt_lifecycle["agent.skills_mgmt.lifecycle"]
        agent_skills_mgmt_lineage["agent.skills_mgmt.lineage"]:::crosslayer
        agent_skills_mgmt_loader["agent.skills_mgmt.loader"]:::crosslayer
        agent_skills_mgmt_memory_abstractor["agent.skills_mgmt.memory_abstractor"]:::crosslayer
        agent_skills_mgmt_meta_editor["agent.skills_mgmt.meta_editor"]
        agent_skills_mgmt_models["agent.skills_mgmt.models"]:::crosslayer
        agent_skills_mgmt_negative_intent_detector["agent.skills_mgmt.negative_intent_detector"]
        agent_skills_mgmt_observability["agent.skills_mgmt.observability"]:::crosslayer
        agent_skills_mgmt_offline_evolver["agent.skills_mgmt.offline_evolver"]:::crosslayer
        agent_skills_mgmt_precipitate["agent.skills_mgmt.precipitate"]
        agent_skills_mgmt_reranker["agent.skills_mgmt.reranker"]
        agent_skills_mgmt_reviewer["agent.skills_mgmt.reviewer"]:::crosslayer
        agent_skills_mgmt_searcher["agent.skills_mgmt.searcher"]
        agent_skills_mgmt_service["agent.skills_mgmt.service"]:::crosslayer
        agent_skills_mgmt_skill_manager["agent.skills_mgmt.skill_manager"]
        agent_skills_mgmt_store["agent.skills_mgmt.store"]:::crosslayer
        agent_skills_mgmt_vector_adapter["agent.skills_mgmt.vector_adapter"]
    end
    subgraph subagent [subagent]
        agent_subagent["agent.subagent"]
        agent_subagent_container["agent.subagent.container"]:::crosslayer
        agent_subagent_lifecycle["agent.subagent.lifecycle"]:::crosslayer
        agent_subagent_observability["agent.subagent.observability"]
        agent_subagent_sandbox["agent.subagent.sandbox"]
        agent_subagent_summarizer["agent.subagent.summarizer"]
    end
    subgraph task_planner [task_planner]
        agent_task_planner_dag["agent.task_planner.dag"]
        agent_task_planner_enhanced_dag["agent.task_planner.enhanced_dag"]
        agent_task_planner_enhanced_planner["agent.task_planner.enhanced_planner"]
        agent_task_planner_observability["agent.task_planner.observability"]
        agent_task_planner_planner["agent.task_planner.planner"]
    end
    subgraph tools [tools]
        agent_tools["agent.tools"]:::crosslayer
        agent_tools_browser_tools["agent.tools.browser_tools"]:::crosslayer
        agent_tools_code_tools["agent.tools.code_tools"]:::crosslayer
        agent_tools_core_tools["agent.tools.core_tools"]:::crosslayer
        agent_tools_discovery_service["agent.tools.discovery_service"]:::crosslayer
        agent_tools_ext_tools["agent.tools.ext_tools"]:::crosslayer
        agent_tools_file_tools["agent.tools.file_tools"]:::crosslayer
        agent_tools_file_tools_reg["agent.tools.file_tools_reg"]:::crosslayer
        agent_tools_mcp_connector["agent.tools.mcp_connector"]
        agent_tools_observability["agent.tools.observability"]
        agent_tools_pdf_tools["agent.tools.pdf_tools"]:::crosslayer
        agent_tools_process_tools["agent.tools.process_tools"]:::crosslayer
        agent_tools_shell_tools["agent.tools.shell_tools"]:::crosslayer
        agent_tools_software_tools["agent.tools.software_tools"]:::crosslayer
        agent_tools_system_tools["agent.tools.system_tools"]:::crosslayer
        agent_tools_task_tools["agent.tools.task_tools"]:::crosslayer
        agent_tools_tool_generator["agent.tools.tool_generator"]
        agent_tools_web_tools["agent.tools.web_tools"]:::crosslayer
        agent_tools_workspace_tools["agent.tools.workspace_tools"]:::crosslayer
    end
    subgraph unknown [unknown]
        agent["agent"]
    end
    subgraph utils [utils]
        agent_utils_compatibility["agent.utils.compatibility"]:::crosslayer
        agent_utils_index_manager["agent.utils.index_manager"]
        agent_utils_observability["agent.utils.observability"]
        agent_utils_perf_monitor["agent.utils.perf_monitor"]
        agent_utils_periodic_sampler["agent.utils.periodic_sampler"]:::crosslayer
        agent_utils_sensitive_data_filter["agent.utils.sensitive_data_filter"]:::crosslayer
        agent_utils_singleton_manager["agent.utils.singleton_manager"]:::crosslayer
        agent_utils_token_redactor["agent.utils.token_redactor"]:::crosslayer
    end
    subgraph verification [verification]
        agent_verification_output_validator["agent.verification.output_validator"]:::crosslayer
    end
    subgraph web [web]
        agent_web["agent.web"]:::crosslayer
        agent_web_browser_agent["agent.web.browser_agent"]
        agent_web_http_client["agent.web.http_client"]
        agent_web_observability["agent.web.observability"]
        agent_web_search["agent.web.search"]
    end
    subgraph workflow_engine [workflow_engine]
        agent_workflow_engine_builtin_rules["agent.workflow_engine.builtin_rules"]:::crosslayer
        agent_workflow_engine_engine["agent.workflow_engine.engine"]:::crosslayer
        agent_workflow_engine_matcher["agent.workflow_engine.matcher"]
        agent_workflow_engine_observability["agent.workflow_engine.observability"]
        agent_workflow_engine_registry["agent.workflow_engine.registry"]
    end
    subgraph workflow_learning [workflow_learning]
        agent_workflow_learning["agent.workflow_learning"]:::crosslayer
        agent_workflow_learning_matcher["agent.workflow_learning.matcher"]
        agent_workflow_learning_models["agent.workflow_learning.models"]:::crosslayer
        agent_workflow_learning_observability["agent.workflow_learning.observability"]
        agent_workflow_learning_service["agent.workflow_learning.service"]:::crosslayer
        agent_workflow_learning_skill_converter["agent.workflow_learning.skill_converter"]
    end
    agent_p6_snapshot --> agent_logging_utils
    agent_p6_snapshot --> agent_behavior_controller
    agent_learning_metrics_api --> agent_learning_metrics
    agent_modules_api --> agent_modules_registry
    agent_modules_api --> agent_server_auth
    agent_modules_api --> agent_rate_limiter
    agent_modules_api -.-> agent_health_dashboard
    agent_modules_api --> agent_system_tools
    agent_mcp_executor -.-> agent_utils_singleton_manager
    agent_mcp_executor --> agent_tool_router
    agent_mcp_executor --> agent_tool_router
    agent_task_scheduler --> agent_logging_utils
    agent_task_scheduler -.-> agent_utils_singleton_manager
    agent_task_scheduler --> agent_weekly_report_generator
    agent_task_scheduler -.-> agent_monitoring_observability_config
    agent_task_scheduler -.-> agent_monitoring_observability_config
    agent_task_scheduler -.-> agent_monitoring_observability_config
    agent_task_scheduler -.-> agent_monitoring_observability_config
    agent_task_scheduler -.-> agent_monitoring_observability_config
    agent_task_scheduler -.-> agent_monitoring_observability_config
    agent_search_aggregator --> agent_logging_utils
    agent_tool_router_hybrid --> agent_logging_utils
    agent_tool_router_hybrid -.-> agent_observability_tool_trace
    agent_tool_router_hybrid --> agent_tool_router
    agent_safety_guard -.-> agent_utils_singleton_manager
    agent_test_permission_system --> agent_permission_system
    agent_v2_performance_patch --> agent_logging_utils
    agent_digital_life_persona --> agent_logging_utils
    agent_digital_life_persona --> agent_behavior_controller
    agent_digital_life_persona -.-> agent_tools
    agent_digital_life_persona -.-> agent_tools
    agent_digital_life_persona --> agent_performance_monitor
    agent_digital_life_persona -.-> agent_extensions_store
    agent_digital_life_persona -.-> agent_extensions_base
    agent_learning_metrics -.-> agent_monitoring_metrics
    agent_learning_metrics -.-> agent_utils_singleton_manager
    agent_p6_config_loader --> agent_p6_snapshot
    agent_learning_budget --> agent_circuit_breaker
    agent_learning_budget --> agent_rate_limiter
    agent_learning_budget -.-> agent_monitoring_metrics
    agent_error_reporting_config -.-> agent_monitoring_tracing
    agent_digital_life --> agent_logging_utils
    agent_digital_life -.-> agent_orchestrator
    agent_digital_life -.-> agent_monitoring
    agent_digital_life --> agent_system_prompt_manager
    agent_data_analytics --> agent_logging_utils
    agent_data_analytics -.-> agent_monitoring_observability_config
    agent_security_utils --> agent_logging_utils
    agent_error_handler --> agent_logging_utils
    agent_error_handler --> agent_circuit_breaker
    agent_error_handler --> agent_circuit_breaker
    agent_error_handler --> agent_circuit_breaker
    agent_error_handler -.-> agent_monitoring_observability_config
    agent_error_handler -.-> agent_monitoring_metrics
    agent_error_handler -.-> agent_monitoring_observability_config
    agent_error_handler -.-> agent_monitoring_observability_config
    agent_error_handler -.-> agent_monitoring_metrics
    agent_error_handler -.-> agent_monitoring_metrics
    agent_llm_response_cache -.-> agent_caching_multi_level_cache
    agent_llm_response_cache --> agent_logging_utils
    agent_system_tools -.-> agent_tools_file_tools
    agent_system_tools -.-> agent_tools_workspace_tools
    agent_system_tools -.-> agent_tools_browser_tools
    agent_system_tools -.-> agent_tools_process_tools
    agent_system_tools -.-> agent_tools_task_tools
    agent_system_tools -.-> agent_tools_shell_tools
    agent_network_config --> agent_logging_utils
    agent_network_config --> agent_env_config_manager
    agent_network_config -.-> agent_tools
    agent_text_tools --> agent_logging_utils
    agent_system_prompt_config -.-> agent_utils_singleton_manager
    agent_llm_monitor -.-> agent_utils_singleton_manager
    agent_llm_monitor -.-> agent_monitoring_observability_config
    agent_ab_testing --> agent_logging_utils
    agent_ab_testing -.-> agent_utils_singleton_manager
    agent_software_backends --> agent_logging_utils
    agent_async_executor -.-> agent_tools
    agent_async_executor -.-> agent_utils_singleton_manager
    agent_compression_tools --> agent_logging_utils
    agent_compression_tools --> agent_system_tools
    agent_compression_tools --> agent_system_tools
    agent_tool_fewshot_store -.-> agent_utils_sensitive_data_filter
    agent_scheduling --> agent_logging_utils
    agent_scheduling -.-> agent_utils_singleton_manager
    agent_software_manager --> agent_logging_utils
    agent_tool_router -.-> agent_observability_tool_trace
    agent_tool_router -.-> agent_evolution_injector
    agent_tool_router -.-> agent_monitoring_tracing
    agent_tool_calling --> agent_logging_utils
    agent_tool_calling --> agent
    agent_tool_calling --> agent_rate_limiter
    agent_tool_calling --> agent_circuit_breaker
    agent_tool_calling --> agent_circuit_breaker
    agent_tool_calling -.-> agent_observability_tool_trace
    agent_tool_calling --> agent_response_workflows
    agent_tool_router_reranker --> agent_logging_utils
    agent_tool_router_reranker -.-> agent_utils_singleton_manager
    agent_digital_life_state --> agent_state_manager
    agent_digital_life_state --> agent_logging_utils
    agent_digital_life_state --> agent_p6_snapshot
    agent_digital_life_state --> agent_p6_snapshot
    agent_digital_life_state --> agent_behavior_controller
    agent_diff_tools --> agent_logging_utils
    agent_diff_tools --> agent_system_tools
    agent_performance_monitor --> agent_logging_utils
    agent_performance_monitor -.-> agent_monitoring_performance
    agent_memory_optimized --> agent_logging_utils
    agent_lazy_loader_async -.-> agent_lazy_loader
    agent_lazy_loader_async -.-> agent_lazy_loader__core
    agent_lazy_loader_async --> agent_logging_utils
    agent_lazy_loader_async -.-> agent_utils_singleton_manager
    agent_rate_limiter --> agent_logging_utils
    agent_rate_limiter -.-> agent_monitoring_metrics
    agent_performance_logging --> agent_logging_utils
    agent_performance_logging -.-> agent_monitoring_performance
    agent_feedback --> agent_logging_utils
    agent_feedback -.-> agent_utils_singleton_manager
    agent_feedback -.-> agent_cognitive_failure_analysis
    agent_feedback --> agent_learning_metrics
    agent_state_manager --> agent_logging_utils
    agent_state_manager -.-> agent_utils_singleton_manager
    agent_state_manager -.-> agent_skills_mgmt
    agent_state_manager -.-> agent_workflow_learning
    agent_auto_tuner --> agent_logging_utils
    agent_auto_tuner -.-> agent_utils_singleton_manager
    agent_auto_tuner -.-> agent_evolution_injector
    agent_auto_tuner -.-> agent_evolution_injector
    agent_auto_tuner -.-> agent_evolution_injector
    agent_auto_tuner -.-> agent_evolution_injector
    agent_api_gateway --> agent_rate_limiter
    agent_api_gateway -.-> agent_monitoring_tracing
    agent_api_gateway -.-> agent_utils_singleton_manager
    agent_disaster_recovery -.-> agent_utils_singleton_manager
    agent_behavior_controller --> agent_logging_utils
    agent_env_config_manager --> agent_logging_utils
    agent_prometheus_exporter -.-> agent_monitoring_prometheus
    agent_multi_tenant -.-> agent_monitoring_tracing
    agent_search_performance_monitor --> agent_logging_utils
    agent_search_performance_monitor -.-> agent_monitoring_search
    agent_weekly_report_generator --> agent_logging_utils
    agent_weekly_report_generator --> agent_data_analytics
    agent_audit --> agent_audit_logger
    agent_audit_observability -.-> agent_logging_utils
    agent_audit_observability -.-> agent_monitoring_business_metrics
    agent_audit_logger -.-> agent_observability_tracer
    agent_audit_logger -.-> agent_logging_utils
    agent_guardrails_input_guard -.-> agent_logging_utils
    agent_guardrails_observability -.-> agent_logging_utils
    agent_guardrails_observability -.-> agent_monitoring_business_metrics
    agent_guardrails_output_schema -.-> agent_monitoring_tracing
    agent_guardrails_output_schema -.-> agent_circuit_breaker
    agent_guardrails_output_schema -.-> agent_graceful_degrade
    agent_guardrails_output_schema -.-> agent_logging_utils
    agent_guardrails_output_guard -.-> agent_logging_utils
    agent_workflow_learning_service -.-> agent_state_manager
    agent_workflow_learning_service -.-> agent_skills_mgmt_service
    agent_workflow_learning_observability -.-> agent_monitoring_business_metrics
    agent_workflow_learning_skill_converter -.-> agent_logging_utils
    agent_workflow_learning_matcher -.-> agent_logging_utils
    agent_lazy_loader -.-> agent_logging_utils
    agent_lazy_loader -.-> agent_utils_singleton_manager
    agent_lazy_loader_observability -.-> agent_logging_utils
    agent_lazy_loader_observability -.-> agent_monitoring_business_metrics
    agent_lazy_loader__core -.-> agent_logging_utils
    agent_p6 --> agent_p6_snapshot
    agent_p6 --> agent_p6_performance
    agent_p6 --> agent_p6_frequency
    agent_p6_frequency -.-> agent_logging_utils
    agent_p6_observability -.-> agent_logging_utils
    agent_p6_observability -.-> agent_monitoring_business_metrics
    agent_p6_performance -.-> agent_logging_utils
    agent_p6_snapshot --> agent_p6_performance
    agent_p6_snapshot --> agent_p6_frequency
    agent_p6_snapshot -.-> agent_logging_utils
    agent_p6_snapshot -.-> agent_behavior_controller
    agent_web_search -.-> agent_logging_utils
    agent_web_observability -.-> agent_logging_utils
    agent_web_observability -.-> agent_monitoring_business_metrics
    agent_web_browser_agent -.-> agent_error_handler
    agent_web_browser_agent -.-> agent_error_handler
    agent_web_browser_agent -.-> agent_error_handler
    agent_web_http_client -.-> agent_monitoring_observability_config
    agent_web_http_client -.-> agent_monitoring_observability_config
    agent_web_http_client -.-> agent_monitoring_observability_config
    agent_web_http_client -.-> agent_monitoring_observability_config
    agent_preflight --> agent_preflight_runner
    agent_preflight___main__ --> agent_preflight_runner
    agent_preflight_runner --> agent
    agent_evolution_injector -.-> agent_utils_singleton_manager
    agent_orchestrator_task_dispatcher --> agent
    agent_orchestrator_task_dispatcher -.-> agent_system_prompt_config
    agent_orchestrator_task_dispatcher -.-> agent_system_prompt_config
    agent_orchestrator_task_dispatcher -.-> agent_tool_router
    agent_orchestrator_task_dispatcher -.-> agent_tool_router_hybrid
    agent_orchestrator_prompt_builder -.-> agent_logging_utils
    agent_orchestrator_prompt_builder -.-> agent_digital_life
    agent_orchestrator_observability -.-> agent_logging_utils
    agent_orchestrator_observability -.-> agent_monitoring_business_metrics
    agent_orchestrator_orchestrator -.-> agent_autonomy
    agent_orchestrator_orchestrator -.-> agent_guardrails_input_guard
    agent_orchestrator_orchestrator -.-> agent_guardrails_output_guard
    agent_orchestrator_orchestrator -.-> agent_observability_subscriber
    agent_orchestrator_orchestrator --> agent_orchestrator_message_handler
    agent_orchestrator_orchestrator --> agent_orchestrator_response_builder
    agent_orchestrator_orchestrator -.-> agent_logging_utils
    agent_orchestrator_orchestrator --> agent_orchestrator_routing_observability
    agent_orchestrator_orchestrator -.-> agent_tool_calling
    agent_orchestrator_orchestrator -.-> agent_tool_router
    agent_orchestrator_orchestrator -.-> agent_tool_router_hybrid
    agent_orchestrator_orchestrator -.-> agent_digital_life
    agent_orchestrator_orchestrator -.-> agent_learning_metrics
    agent_orchestrator_orchestrator -.-> agent_monitoring_prometheus
    agent_orchestrator_orchestrator -.-> agent_verification_output_validator
    agent_orchestrator_orchestrator --> agent_orchestrator_dialog_state
    agent_orchestrator_orchestrator -.-> agent_response_workflows
    agent_orchestrator_orchestrator --> agent_orchestrator_dialog_state
    agent_orchestrator_orchestrator -.-> agent_state_manager
    agent_orchestrator_orchestrator -.-> agent_state_manager
    agent_orchestrator_orchestrator -.-> agent_workflow_learning_models
    agent_orchestrator_orchestrator -.-> agent_state_manager
    agent_orchestrator_orchestrator -.-> agent_monitoring_prometheus
    agent_orchestrator_orchestrator -.-> agent_context_assembler
    agent_orchestrator_orchestrator -.-> agent_state_manager
    agent_orchestrator_orchestrator -.-> agent_cognitive_reflection
    agent_orchestrator_orchestrator -.-> agent_autonomy
    agent_orchestrator_orchestrator -.-> agent_autonomy
    agent_orchestrator_orchestrator -.-> agent_state_manager
    agent_orchestrator_orchestrator --> agent_orchestrator_dialog_state
    agent_orchestrator_orchestrator -.-> agent_monitoring_prometheus
    agent_orchestrator_orchestrator --> agent
    agent_orchestrator_orchestrator -.-> agent_skills_mgmt_loader
    agent_orchestrator_orchestrator -.-> agent_tools
    agent_orchestrator_orchestrator -.-> agent_tool_schema_pruner
    agent_orchestrator_orchestrator -.-> agent_tool_fewshot_store
    agent_orchestrator_orchestrator --> agent_orchestrator_prompt_builder
    agent_orchestrator_orchestrator -.-> agent_monitoring_llm_monitor
    agent_orchestrator_subagent_manager -.-> agent_subagent_container
    agent_orchestrator_subagent_manager -.-> agent_subagent_container
    agent_orchestrator_lifecycle_manager -.-> agent_logging_utils
    agent_orchestrator_lifecycle_manager -.-> agent_monitoring_tracing
    agent_orchestrator_lifecycle_manager -.-> agent_digital_life
    agent_orchestrator_lifecycle_manager -.-> agent_tools_core_tools
    agent_orchestrator_lifecycle_manager -.-> agent_tools_file_tools_reg
    agent_orchestrator_lifecycle_manager -.-> agent_tools_web_tools
    agent_orchestrator_lifecycle_manager -.-> agent_tools_ext_tools
    agent_orchestrator_lifecycle_manager -.-> agent_tools_pdf_tools
    agent_orchestrator_lifecycle_manager -.-> agent_tools_software_tools
    agent_orchestrator_lifecycle_manager -.-> agent_tools_system_tools
    agent_orchestrator_lifecycle_manager -.-> agent_tools_code_tools
    agent_orchestrator_lifecycle_manager -.-> agent_tools_core_tools
    agent_orchestrator_lifecycle_manager -.-> agent_utils_compatibility
    agent_orchestrator_lifecycle_manager -.-> agent_system_prompt_config
    agent_orchestrator_lifecycle_manager -.-> agent_learning_novelty_hooks
    agent_orchestrator_lifecycle_manager -.-> agent_memory_router
    agent_orchestrator_lifecycle_manager -.-> agent_workflow_engine_engine
    agent_orchestrator_lifecycle_manager -.-> agent_workflow_engine_builtin_rules
    agent_orchestrator_lifecycle_manager -.-> agent_state_manager
    agent_orchestrator_lifecycle_manager -.-> agent_extensions_manager
    agent_orchestrator_lifecycle_manager -.-> agent_network_config
    agent_orchestrator_lifecycle_manager --> agent
    agent_orchestrator_lifecycle_manager --> agent
    agent_orchestrator_lifecycle_manager -.-> agent_tools_discovery_service
    agent_orchestrator_lifecycle_manager -.-> agent_extensions_market
    agent_orchestrator_lifecycle_manager --> agent
    agent_orchestrator_lifecycle_manager -.-> agent_memory_short_term_memory
    agent_orchestrator_lifecycle_manager -.-> agent_memory_long_term_memory
    agent_orchestrator_lifecycle_manager -.-> agent_memory_reviewer
    agent_orchestrator_lifecycle_manager -.-> agent_tool_calling
    agent_orchestrator_lifecycle_manager -.-> agent_subagent_lifecycle
    agent_orchestrator_lifecycle_manager -.-> agent_extensions_manager
    agent_orchestrator_lifecycle_manager --> agent
    agent_orchestrator_lifecycle_manager -.-> agent_tool_calling
    agent_orchestrator_lifecycle_manager -.-> agent_web
    agent_orchestrator_routing_observability -.-> agent_logging_utils
    agent_orchestrator_response_builder -.-> agent_logging_utils
    agent_orchestrator_status_reporter --> agent
    agent_orchestrator_message_handler -.-> agent_logging_utils
    agent_orchestrator_message_handler --> agent_orchestrator_dialog_state
    agent_server_routes_routes_skills_mgmt -.-> agent_server_auth
    agent_server_routes_routes_skills_mgmt --> agent_server_routes_tracing_decorator
    agent_server_routes_routes_skills_mgmt -.-> agent_state_manager
    agent_server_routes_routes_skills_mgmt -.-> agent_skills_mgmt
    agent_server_routes_routes_skills_mgmt -.-> agent_skills_mgmt_reviewer
    agent_server_routes_routes_skills_mgmt -.-> agent_skills_mgmt_enhancer
    agent_server_routes_routes_skills_mgmt -.-> agent_skills_mgmt_models
    agent_server_routes_routes_skills_mgmt -.-> agent_skills_mgmt_memory_abstractor
    agent_server_routes_routes_skills_mgmt -.-> agent_skills_mgmt_offline_evolver
    agent_server_routes_routes_dashboard -.-> agent_server_auth
    agent_server_routes_routes_dashboard -.-> agent_monitoring_tracing
    agent_server_routes_routes_dashboard -.-> agent_monitoring_metrics
    agent_server_routes_routes_dashboard -.-> agent_health_assessor
    agent_server_routes_routes_dashboard --> agent_server_routes_tracing_decorator
    agent_server_routes_routes_dashboard -.-> agent_logging_utils
    agent_server_routes_routes_dashboard -.-> agent_cognitive_failure_analysis
    agent_server_routes_routes_dashboard -.-> agent_monitoring_tracing
    agent_server_routes_routes_dashboard -.-> agent_monitoring_tracing
    agent_server_routes_routes_knowledge -.-> agent_knowledge
    agent_server_routes_routes_knowledge -.-> agent_knowledge_links_index
    agent_server_routes_routes_knowledge -.-> agent_knowledge_lint
    agent_server_routes_routes_knowledge -.-> agent_server_auth
    agent_server_routes_routes_knowledge --> agent_server_routes_tracing_decorator
    agent_server_routes_routes_knowledge -.-> agent_tool_router_reranker
    agent_server_routes_routes_health -.-> agent_server_auth
    agent_server_routes_routes_health -.-> agent_health_health_score
    agent_server_routes_routes_health -.-> agent_logging_utils
    agent_server_routes_routes_health -.-> agent_utils_singleton_manager
    agent_server_routes_routes_health -.-> agent_monitoring_alert_manager
    agent_server_routes_routes_health -.-> agent_monitoring_alert_manager
    agent_server_routes_routes_health -.-> agent_task_scheduler
    agent_server_routes_routes_health -.-> agent_prometheus_exporter
    agent_server_routes_routes_memory -.-> agent_server_auth
    agent_server_routes_routes_memory --> agent_server_routes_tracing_decorator
    agent_server_routes_routes_memory -.-> agent_logging_utils
    agent_server_routes_extensions -.-> agent_server_auth
    agent_server_routes_observability -.-> agent_logging_utils
    agent_server_routes_observability -.-> agent_monitoring_business_metrics
    agent_server_routes_routes_assets --> agent_server_routes_tracing_decorator
    agent_server_routes_routes_chat -.-> agent_server_auth
    agent_server_routes_routes_chat --> agent_server_routes_tracing_decorator
    agent_server_routes_routes_chat -.-> agent_logging_utils
    agent_server_routes_routes_chat -.-> agent_monitoring_tracing
    agent_server_routes_routes_chat --> agent_server_routes_observability
    agent_server_routes_routes_chat -.-> agent_system_tools
    agent_server_routes_routes_chat --> agent_server_routes_routes_sessions
    agent_server_routes_routes_chat -.-> agent_web
    agent_server_routes_routes_chat -.-> agent_web
    agent_server_routes_routes_monitoring -.-> agent_server_auth
    agent_server_routes_routes_monitoring -.-> agent_task_scheduler
    agent_server_routes_routes_monitoring -.-> agent_system_tools
    agent_server_routes_routes_monitoring --> agent_server_routes_tracing_decorator
    agent_server_routes_routes_monitoring -.-> agent_search_performance_monitor
    agent_server_routes_routes_monitoring -.-> agent_search_performance_monitor
    agent_server_routes_routes_monitoring -.-> agent_search_performance_monitor
    agent_server_routes_routes_monitoring -.-> agent_search_performance_monitor
    agent_server_routes_routes_monitoring -.-> agent_search_performance_monitor
    agent_server_routes_routes_monitoring -.-> agent_search_performance_monitor
    agent_server_routes_routes_personality -.-> agent_server_auth
    agent_server_routes_routes_personality --> agent_server_routes_tracing_decorator
    agent_server_routes_routes_personality -.-> agent_logging_utils
    agent_server_routes_routes_feedback -.-> agent_logging_utils
    agent_server_routes_routes_feedback -.-> agent_feedback
    agent_server_routes_routes_feedback -.-> agent_feedback
    agent_server_routes_routes_feedback -.-> agent_feedback
    agent_server_routes_routes_feedback -.-> agent_feedback
    agent_server_routes_routes_feedback -.-> agent_feedback
    agent_server_routes_routes_feedback -.-> agent_feedback
    agent_server_routes_routes_feedback -.-> agent_feedback
    agent_server_routes_tracing_middleware -.-> agent_monitoring_tracing
    agent_server_routes_routes_config -.-> agent_server_auth
    agent_server_routes_routes_config -.-> agent_network_config
    agent_server_routes_routes_config --> agent_server_routes_tracing_decorator
    agent_server_routes_routes_config -.-> agent_logging_utils
    agent_server_routes_routes_config -.-> agent_config_validation
    agent_server_routes_routes_config -.-> agent_orchestrator_orchestrator
    agent_server_routes_routes_config -.-> agent_orchestrator_orchestrator
    agent_server_routes_routes_config -.-> agent_monitoring_metrics
    agent_server_routes_routes_config -.-> agent_tools
    agent_server_routes_routes_config -.-> agent_monitoring_metrics
    agent_server_routes_routes_system_prompt --> agent_server_routes_tracing_decorator
    agent_server_routes_routes_system_prompt -.-> agent_server_auth
    agent_server_routes_routes_system_prompt -.-> agent_system_prompt_config
    agent_server_routes_routes_system_prompt -.-> agent_system_prompt_manager
    agent_server_routes_routes_system_prompt -.-> agent_system_prompt_manager
    agent_server_routes_routes_system_prompt -.-> agent_system_prompt_manager
    agent_server_routes_routes_logging -.-> agent_server_auth
    agent_server_routes_routes_logging -.-> agent_monitoring_tracing
    agent_server_routes_routes_logging -.-> agent_monitoring_metrics
    agent_server_routes_routes_logging -.-> agent_monitoring_performance
    agent_server_routes_routes_logging -.-> agent_monitoring_prometheus
    agent_server_routes_routes_logging -.-> agent_health_assessor
    agent_server_routes_routes_logging -.-> agent_tools
    agent_server_routes_routes_logging --> agent_server_routes_tracing_decorator
    agent_server_routes_routes_logging -.-> agent_monitoring_sensitive_data_filter
    agent_server_routes_routes_logging -.-> agent_logging_utils
    agent_server_routes_routes_logging -.-> agent_utils_singleton_manager
    agent_server_routes_routes_logging -.-> agent_monitoring_replay_storage
    agent_server_routes_routes_logging -.-> agent_error_reporting_config
    agent_server_routes_routes_logging -.-> agent_log_system_storage
    agent_server_routes_routes_logging -.-> agent_monitoring_loki
    agent_server_routes_routes_logging -.-> agent_monitoring_loki
    agent_server_routes_routes_logging -.-> agent_monitoring_loki
    agent_server_routes_routes_logging -.-> agent_monitoring_tracing
    agent_server_routes_routes_logging -.-> agent_monitoring_tracing
    agent_server_routes_routes_subagent -.-> agent_server_auth
    agent_server_routes_routes_subagent --> agent_server_routes_tracing_decorator
    agent_server_routes_tracing_decorator -.-> agent_monitoring_tracing
    agent_server_routes_tracing_decorator -.-> agent_logging_utils
    agent_server_routes_routes_workflow_learning -.-> agent_server_auth
    agent_server_routes_routes_workflow_learning --> agent_server_routes_tracing_decorator
    agent_server_routes_routes_workflow_learning -.-> agent_state_manager
    agent_server_routes_routes_workflow_learning -.-> agent_workflow_learning
    agent_server_routes_routes_workflow_learning -.-> agent_state_manager
    agent_server_routes_routes_workflow_learning -.-> agent_state_manager
    agent_server_routes_routes_business_dashboard -.-> agent_server_auth
    agent_server_routes_routes_business_dashboard -.-> agent_monitoring_tracing
    agent_server_routes_routes_business_dashboard --> agent_server_routes_tracing_decorator
    agent_server_routes_routes_business_dashboard -.-> agent_logging_utils
    agent_server_routes_routes_business_dashboard -.-> agent_monitoring_business_metrics
    agent_server_routes_routes_business_dashboard -.-> agent_monitoring_business_metrics
    agent_server_routes_routes_business_dashboard -.-> agent_monitoring_business_metrics
    agent_server_routes_routes_business_dashboard -.-> agent_monitoring_business_metrics
    agent_server_routes_routes_business_dashboard -.-> agent_monitoring_business_metrics
    agent_server_routes_routes_sessions -.-> agent_server_auth
    agent_server_routes_routes_sessions --> agent_server_routes_tracing_decorator
    agent_server_routes_routes_sessions -.-> agent_logging_utils
    agent_server_routes_routes_sessions -.-> agent_handoff_handoff_generator
    agent_server_routes_routes_skills -.-> agent_server_auth
    agent_server_routes_routes_skills -.-> agent_tools
    agent_server_routes_routes_skills --> agent_server_routes_tracing_decorator
    agent_server_routes_routes_skills -.-> agent_server_ui
    agent_server_routes_routes_skills -.-> agent_server_ui
    agent_server_routes_routes_skills -.-> agent_extensions_store
    agent_server_routes_routes_skills -.-> agent_extensions_base
    agent_server_routes_routes_skills -.-> agent_extensions_base
    agent_server_routes_routes_skills -.-> agent_extensions_base
    agent_server_routes_routes_skills -.-> agent_extensions_store
    agent_server_routes_routes_skills -.-> agent_extensions_base
    agent_server_routes_routes_workspace -.-> agent_server_auth
    agent_server_routes_routes_workspace -.-> agent_system_tools
    agent_server_routes_routes_workspace --> agent_server_routes_tracing_decorator
    agent_server_routes_routes_workspace -.-> agent_system_tools
    agent_server_routes_routes_panorama -.-> agent_server_auth
    agent_server_routes_routes_panorama -.-> agent_tools
    agent_server_routes_routes_panorama --> agent_server_routes_tracing_decorator
    agent_server_routes_routes_replay -.-> agent_server_auth
    agent_server_routes_routes_replay -.-> agent_monitoring_replay_storage
    agent_server_routes_routes_replay -.-> agent_logging_utils
    agent_server_routes_routes_permission -.-> agent_server_auth
    agent_server_routes_routes_permission -.-> agent_tools
    agent_server_routes_routes_permission -.-> agent_tools
    agent_server_routes_routes_permission --> agent_server_routes_tracing_decorator
    agent_server_routes_routes_llm_monitor --> agent_server_routes_tracing_decorator
    agent_observability_tracer -.-> agent_monitoring_tracing
    agent_observability_arch_rules --> agent_observability_dependency_graph
    agent_observability_tool_trace -.-> agent_logging_utils
    agent_observability_subscriber -.-> agent_logging_utils
    agent_memory --> agent_memory_base
    agent_memory --> agent_memory_router
    agent_memory --> agent_memory_adapters
    agent_memory_context_assembler -.-> agent_logging_utils
    agent_memory_context_assembler --> agent_memory_observability
    agent_memory_long_term_memory --> agent_memory_base
    agent_memory_long_term_memory -.-> agent_logging_utils
    agent_memory_long_term_memory -.-> agent_monitoring_business_metrics
    agent_memory_router --> agent_memory_base
    agent_memory_router --> agent_memory_adapters_holographic_adapter
    agent_memory_router --> agent_memory_adapters_mem0_adapter
    agent_memory_router -.-> agent_logging_utils
    agent_memory_router --> agent_memory_filter
    agent_memory_hotness_scorer -.-> agent_logging_utils
    agent_memory_hotness_scorer --> agent_memory_observability
    agent_memory_short_term_memory --> agent_memory_base
    agent_memory_short_term_memory -.-> agent_logging_utils
    agent_memory_observability -.-> agent_logging_utils
    agent_memory_observability -.-> agent_monitoring_business_metrics
    agent_memory_filter -.-> agent_logging_utils
    agent_memory_filter -.-> agent_utils_sensitive_data_filter
    agent_memory_filter -.-> agent_utils_sensitive_data_filter
    agent_memory_file_watcher -.-> agent_logging_utils
    agent_memory_file_watcher --> agent_memory_markdown_syncer
    agent_memory_file_watcher --> agent_memory_observability
    agent_memory_reviewer --> agent_memory_long_term_memory
    agent_memory_markdown_syncer -.-> agent_logging_utils
    agent_memory_base -.-> agent_logging_utils
    agent_memory_adapters --> agent_memory_adapters_holographic_adapter
    agent_memory_adapters --> agent_memory_adapters_mem0_adapter
    agent_memory_adapters_mem0_adapter --> agent_memory_base
    agent_memory_adapters_mem0_adapter -.-> agent_logging_utils
    agent_memory_adapters_holographic_adapter --> agent_memory_base
    agent_memory_adapters_holographic_adapter -.-> agent_logging_utils
    agent_memory_adapters_holographic_adapter -.-> agent_error_handler
    agent_memory_adapters_holographic_adapter --> agent_memory_markdown_syncer
    agent_memory_adapters_holographic_adapter --> agent_memory_file_watcher
    agent_memory_adapters_holographic_adapter -.-> agent_caching_multi_level_cache
    agent_memory_adapters_holographic_adapter --> agent_memory_observability
    agent_cognitive --> agent_cognitive_loop
    agent_cognitive --> agent_cognitive_reflection
    agent_cognitive --> agent_cognitive_knowledge
    agent_cognitive --> agent_cognitive_actor_critic
    agent_cognitive --> agent_cognitive_debate
    agent_cognitive_failure_collector --> agent_cognitive_failure_analysis
    agent_cognitive_failure_collector -.-> agent_logging_utils
    agent_cognitive_failure_collector -.-> agent_utils_singleton_manager
    agent_cognitive_actor_critic -.-> agent_logging_utils
    agent_cognitive_observability -.-> agent_logging_utils
    agent_cognitive_observability -.-> agent_monitoring_business_metrics
    agent_cognitive_logging_integration --> agent_cognitive_failure_collector
    agent_cognitive_failure_analysis -.-> agent_logging_utils
    agent_cognitive_failure_analysis -.-> agent_utils_singleton_manager
    agent_cognitive_reflection -.-> agent_logging_utils
    agent_cognitive_reflection -.-> agent_monitoring_observability_config
    agent_cognitive_prompt_optimizer -.-> agent_skills_mgmt_evaluator
    agent_cognitive_prompt_optimizer -.-> agent_skills_mgmt_lineage
    agent_cognitive_prompt_optimizer --> agent_cognitive_failure_bucket
    agent_cognitive_prompt_optimizer -.-> agent_logging_utils
    agent_cognitive_prompt_optimizer -.-> agent_skills_mgmt_observability
    agent_cognitive_prompt_optimizer -.-> agent_skills_mgmt_observability
    agent_cognitive_prompt_optimizer -.-> agent_skills_mgmt_observability
    agent_cognitive_debate -.-> agent_logging_utils
    agent_cognitive_critic -.-> agent_monitoring_tracing
    agent_cognitive_critic -.-> agent_circuit_breaker
    agent_cognitive_critic -.-> agent_graceful_degrade
    agent_cognitive_critic -.-> agent_logging_utils
    agent_cognitive_critic -.-> agent_evolution_injector
    agent_cognitive_loop --> agent_cognitive_reflection
    agent_cognitive_loop --> agent_cognitive_knowledge
    agent_cognitive_loop --> agent_cognitive_actor_critic
    agent_cognitive_loop --> agent_cognitive_debate
    agent_cognitive_loop -.-> agent_logging_utils
    agent_data_observability -.-> agent_logging_utils
    agent_data_observability -.-> agent_monitoring_business_metrics
    agent_verification_output_validator -.-> agent_monitoring_metrics
    agent_verification_output_validator -.-> agent_guardrails_output_guard
    agent_subagent --> agent_subagent_container
    agent_subagent --> agent_subagent_lifecycle
    agent_subagent --> agent_subagent_sandbox
    agent_subagent_sandbox -.-> agent_permission_system
    agent_subagent_lifecycle --> agent_subagent_container
    agent_subagent_lifecycle --> agent_subagent_sandbox
    agent_subagent_observability -.-> agent_logging_utils
    agent_subagent_observability -.-> agent_monitoring_business_metrics
    agent_subagent_summarizer -.-> agent_logging_utils
    agent_subagent_container --> agent_subagent_sandbox
    agent_tools -.-> agent_rate_limiter
    agent_tools_software_tools --> agent
    agent_tools_software_tools -.-> agent_software_manager
    agent_tools_software_tools -.-> agent_software_backends
    agent_tools_software_tools -.-> agent_web
    agent_tools_code_tools --> agent
    agent_tools_code_tools -.-> agent_diagram_tools
    agent_tools_code_tools -.-> agent_text_tools
    agent_tools_code_tools -.-> agent_data_process_tools
    agent_tools_code_tools -.-> agent_async_executor
    agent_tools_code_tools -.-> agent_code_review
    agent_tools_code_tools -.-> agent_scheduling
    agent_tools_code_tools -.-> agent_scheduling
    agent_tools_code_tools -.-> agent_scheduling
    agent_tools_code_tools -.-> agent_scheduling
    agent_tools_code_tools -.-> agent_scheduling
    agent_tools_code_tools -.-> agent_scheduling
    agent_tools_ext_tools --> agent
    agent_tools_ext_tools -.-> agent_extensions_market
    agent_tools_ext_tools --> agent_tools_tool_generator
    agent_tools_tool_generator --> agent
    agent_tools_tool_generator -.-> agent_logging_utils
    agent_tools_discovery_service -.-> agent_extensions_base
    agent_tools_discovery_service --> agent_tools_mcp_connector
    agent_tools_discovery_service -.-> agent_extensions_market
    agent_tools_system_tools --> agent
    agent_tools_system_tools -.-> agent_system_tools
    agent_tools_system_tools -.-> agent_system_tools
    agent_tools_web_tools --> agent
    agent_tools_web_tools -.-> agent_web
    agent_tools_web_tools -.-> agent_network_config
    agent_tools_web_tools -.-> agent_search_aggregator
    agent_tools_observability -.-> agent_logging_utils
    agent_tools_observability -.-> agent_monitoring_business_metrics
    agent_tools_mcp_connector --> agent
    agent_tools_task_tools -.-> agent_task_scheduler
    agent_tools_task_tools -.-> agent_task_scheduler
    agent_tools_task_tools -.-> agent_task_scheduler
    agent_tools_file_tools_reg --> agent
    agent_tools_file_tools_reg -.-> agent_system_tools
    agent_tools_file_tools_reg -.-> agent_compression_tools
    agent_tools_file_tools_reg -.-> agent_diff_tools
    agent_tools_pdf_tools --> agent
    agent_tools_pdf_tools -.-> agent_logging_utils
    agent_tools_pdf_tools -.-> agent_pdf_tools
    agent_tools_core_tools --> agent
    agent_tools_core_tools -.-> agent_system_tools
    agent_tools_file_tools -.-> agent_logging_utils
    agent_utils_perf_monitor -.-> agent_logging_utils
    agent_utils_perf_monitor -.-> agent_logging_utils
    agent_utils_perf_monitor -.-> agent_logging_utils
    agent_utils_perf_monitor -.-> agent_logging_utils
    agent_utils_sensitive_data_filter --> agent_utils_singleton_manager
    agent_utils_observability -.-> agent_logging_utils
    agent_utils_observability -.-> agent_monitoring_business_metrics
    agent_utils_index_manager --> agent_utils_singleton_manager
    agent_utils_token_redactor -.-> agent_logging_utils
    agent_handoff_handoff_generator -.-> agent_utils_token_redactor
    agent_handoff_handoff_generator -.-> agent_state_manager
    agent_skills_mgmt_enhancer -.-> agent_feedback
    agent_skills_mgmt_negative_intent_detector -.-> agent_logging_utils
    agent_skills_mgmt_reranker -.-> agent_logging_utils
    agent_skills_mgmt_memory_abstractor -.-> agent_state_manager
    agent_skills_mgmt_memory_abstractor -.-> agent_workflow_learning_service
    agent_skills_mgmt_memory_abstractor -.-> agent_feedback_collector
    agent_skills_mgmt_memory_abstractor -.-> agent_memory_long_term_memory
    agent_skills_mgmt_service -.-> agent_health_dashboard
    agent_skills_mgmt_service -.-> agent_feedback
    agent_skills_mgmt_service -.-> agent_feedback
    agent_skills_mgmt_creator -.-> agent_error_handler
    agent_skills_mgmt_creator -.-> agent_extensions_market
    agent_skills_mgmt_lifecycle -.-> agent_logging_utils
    agent_skills_mgmt_lifecycle --> agent_skills_mgmt_service
    agent_skills_mgmt_lifecycle -.-> agent_task_scheduler
    agent_skills_mgmt_lifecycle -.-> agent_task_scheduler
    agent_skills_mgmt_exceptions -.-> agent_logging_utils
    agent_skills_mgmt_feedback_agent --> agent_skills_mgmt_service
    agent_skills_mgmt_feedback_agent --> agent_skills_mgmt_reviewer
    agent_skills_mgmt_feedback_agent -.-> agent_task_scheduler
    agent_skills_mgmt_feedback_agent -.-> agent_task_scheduler
    agent_skills_mgmt_learning_scheduler --> agent_skills_mgmt_feedback_agent
    agent_skills_mgmt_learning_scheduler --> agent_skills_mgmt_evolution_scheduler
    agent_skills_mgmt_learning_scheduler --> agent_skills_mgmt_lifecycle
    agent_skills_mgmt_learning_scheduler -.-> agent_learning_behavior_drift
    agent_skills_mgmt_learning_scheduler --> agent_skills_mgmt_feedback_agent
    agent_skills_mgmt_learning_scheduler --> agent_skills_mgmt_evolution_scheduler
    agent_skills_mgmt_learning_scheduler --> agent_skills_mgmt_lifecycle
    agent_skills_mgmt_learning_scheduler -.-> agent_learning_behavior_drift
    agent_skills_mgmt_learning_scheduler -.-> agent_task_scheduler
    agent_skills_mgmt_observability -.-> agent_monitoring_business_metrics
    agent_skills_mgmt_observability -.-> agent_monitoring_tracing
    agent_skills_mgmt_index_cache -.-> agent_logging_utils
    agent_skills_mgmt_searcher -.-> agent_logging_utils
    agent_skills_mgmt_skill_manager -.-> agent_logging_utils
    agent_skills_mgmt_bm25_searcher -.-> agent_logging_utils
    agent_skills_mgmt_evolution_scheduler -.-> agent_learning_metrics
    agent_skills_mgmt_evolution_scheduler --> agent_skills_mgmt_service
    agent_skills_mgmt_evolution_scheduler -.-> agent_task_scheduler
    agent_skills_mgmt_evolution_scheduler -.-> agent_task_scheduler
    agent_skills_mgmt_few_shot_injector -.-> agent_logging_utils
    agent_skills_mgmt_file_store -.-> agent_logging_utils
    agent_skills_mgmt_context_injector -.-> agent_logging_utils
    agent_skills_mgmt_offline_evolver -.-> agent_logging_utils
    agent_skills_mgmt_offline_evolver -.-> agent_task_scheduler
    agent_skills_mgmt_offline_evolver -.-> agent_task_scheduler
    agent_skills_mgmt_vector_adapter -.-> agent_logging_utils
    agent_skills_mgmt_executor -.-> agent_logging_utils
    agent_skills_mgmt_precipitate -.-> agent_logging_utils
    agent_skills_mgmt_precipitate -.-> agent_learning_metrics
    agent_skills_mgmt_precipitate --> agent_skills_mgmt_memory_abstractor
    agent_skills_mgmt_precipitate -.-> agent_task_scheduler
    agent_skills_mgmt_precipitate -.-> agent_task_scheduler
    agent_skills_mgmt_loader -.-> agent_logging_utils
    agent_skills_mgmt_loader -.-> agent_monitoring_prometheus
    agent_skills_mgmt_evaluator -.-> agent_feedback
    agent_skills_mgmt_meta_editor -.-> agent_logging_utils
    agent_task_planner_planner -.-> agent_logging_utils
    agent_task_planner_enhanced_dag -.-> agent_logging_utils
    agent_task_planner_enhanced_dag --> agent_task_planner_dag
    agent_task_planner_observability -.-> agent_logging_utils
    agent_task_planner_observability -.-> agent_monitoring_business_metrics
    agent_task_planner_enhanced_planner --> agent_task_planner_enhanced_dag
    agent_task_planner_enhanced_planner -.-> agent_logging_utils
    agent_task_planner_dag -.-> agent_logging_utils
    agent_log_system_models -.-> agent_logging_utils
    agent_log_system_optimized_storage -.-> agent_logging_utils
    agent_log_system_optimized_storage -.-> agent_utils_singleton_manager
    agent_log_system_introspection -.-> agent_common_stop_mixin
    agent_log_system_introspection -.-> agent_logging_utils
    agent_log_system_introspection -.-> agent_tool_calling
    agent_log_system_storage -.-> agent_logging_utils
    agent_log_system_dashboard -.-> agent_logging_utils
    agent_log_system_observability -.-> agent_logging_utils
    agent_log_system_observability -.-> agent_monitoring_business_metrics
    agent_log_system_handlers -.-> agent_logging_utils
    agent_log_system_handlers --> agent_log_system_formatter
    agent_log_system_handlers --> agent_log_system_emoji_map
    agent_log_system_handlers --> agent_log_system_safe_logger
    agent_log_system_formatter -.-> agent_logging_utils
    agent_log_system_safe_logger -.-> agent_utils_sensitive_data_filter
    agent_log_system_safe_logger -.-> agent_logging_utils
    agent_log_system_safe_logger -.-> agent_utils_singleton_manager
    agent_monitoring_alert_evaluator -.-> agent_logging_utils
    agent_monitoring_alert_evaluator --> agent_monitoring_tracing
    agent_monitoring_alert_evaluator --> agent_monitoring_metrics
    agent_monitoring_alert_evaluator -.-> agent_utils_singleton_manager
    agent_monitoring_alert_evaluator -.-> agent_utils_singleton_manager
    agent_monitoring_alert_evaluator -.-> agent_health_health_score
    agent_monitoring_alert_evaluator -.-> agent_health_health_score
    agent_monitoring_tracing -.-> agent_logging_utils
    agent_monitoring_tracing -.-> agent_observability_subscriber
    agent_monitoring_tracing -.-> agent_observability_subscriber
    agent_monitoring_tracing -.-> agent_utils_singleton_manager
    agent_monitoring_tracing_perf -.-> agent_logging_utils
    agent_monitoring_error_reporter -.-> agent_logging_utils
    agent_monitoring_error_reporter --> agent_monitoring_tracing
    agent_monitoring_error_reporter -.-> agent_error_handler
    agent_monitoring_error_reporter -.-> agent_utils_singleton_manager
    agent_monitoring_error_reporter -.-> agent_error_reporting_config
    agent_monitoring_decorators --> agent_monitoring_metrics
    agent_monitoring_decorators --> agent_monitoring_tracing
    agent_monitoring_decorators --> agent_monitoring_error_reporter
    agent_monitoring_decorators -.-> agent_error_handler
    agent_monitoring_decorators -.-> agent_error_handler
    agent_monitoring_optimized_metrics --> agent_monitoring_tracing
    agent_monitoring_optimized_metrics -.-> agent_utils_singleton_manager
    agent_monitoring_sensitive_data_filter -.-> agent_utils_sensitive_data_filter
    agent_monitoring_sensitive_data_filter -.-> agent_utils_singleton_manager
    agent_monitoring_search -.-> agent_logging_utils
    agent_monitoring_search --> agent_monitoring_tracing
    agent_monitoring_search -.-> agent_common_stop_mixin
    agent_monitoring_search -.-> agent_utils_singleton_manager
    agent_monitoring_search --> agent_monitoring_observability_config
    agent_monitoring_trace_http_client -.-> agent_logging_utils
    agent_monitoring_trace_http_client --> agent_monitoring_tracing
    agent_monitoring_tracing_config --> agent_monitoring_observability_config
    agent_monitoring_business_metrics --> agent_monitoring_utils
    agent_monitoring_business_metrics -.-> agent_logging_utils
    agent_monitoring_observability_optimizations --> agent_monitoring_tracing
    agent_monitoring_observability_optimizations -.-> agent_utils_singleton_manager
    agent_monitoring_chaos_injector --> agent_monitoring_tracing
    agent_monitoring_chaos_injector -.-> agent_logging_utils
    agent_monitoring_chaos_injector -.-> agent_utils_singleton_manager
    agent_monitoring_chaos_injector --> agent_monitoring_observability_config
    agent_monitoring_prometheus -.-> agent_logging_utils
    agent_monitoring_prometheus -.-> agent_error_handler
    agent_monitoring_prometheus -.-> agent_error_handler
    agent_monitoring_prometheus --> agent_monitoring_observability_config
    agent_monitoring_prometheus -.-> agent_error_handler
    agent_monitoring_prometheus -.-> agent_error_handler
    agent_monitoring_prometheus -.-> agent_error_handler
    agent_monitoring_prometheus -.-> agent_error_handler
    agent_monitoring_prometheus --> agent_monitoring_observability_config
    agent_monitoring_config_observability --> agent_monitoring_tracing
    agent_monitoring_config_observability --> agent_monitoring_prometheus
    agent_monitoring_config_observability --> agent_monitoring_loki
    agent_monitoring_config_observability --> agent_monitoring_alert_notifier
    agent_monitoring_alert_notifier --> agent_monitoring_tracing
    agent_monitoring_alert_notifier -.-> agent_logging_utils
    agent_monitoring_alert_notifier -.-> agent_utils_singleton_manager
    agent_monitoring_alert_notifier --> agent_monitoring_observability_config
    agent_monitoring_alert_notifier --> agent_monitoring_prometheus
    agent_monitoring_replay_storage -.-> agent_utils_singleton_manager
    agent_monitoring_replay_storage --> agent_monitoring_observability_config
    agent_monitoring_metrics -.-> agent_logging_utils
    agent_monitoring_resource_monitor --> agent_monitoring_tracing
    agent_monitoring_resource_monitor -.-> agent_logging_utils
    agent_monitoring_resource_monitor -.-> agent_utils_singleton_manager
    agent_monitoring_resource_monitor --> agent_monitoring_observability_config
    agent_monitoring_resource_monitor --> agent_monitoring_business_metrics
    agent_monitoring_tracing_sampling -.-> agent_utils_singleton_manager
    agent_monitoring_observability_config --> agent_monitoring_tracing
    agent_monitoring_observability_config -.-> agent_logging_utils
    agent_monitoring_observability_config -.-> agent_utils_singleton_manager
    agent_monitoring_observability_config -.-> agent_disaster_recovery
    agent_monitoring_observability_config --> agent_monitoring_config_observability
    agent_monitoring_performance -.-> agent_logging_utils
    agent_monitoring_performance --> agent_monitoring_tracing
    agent_monitoring_performance -.-> agent_utils_singleton_manager
    agent_monitoring_self_healer --> agent_monitoring_tracing
    agent_monitoring_self_healer -.-> agent_logging_utils
    agent_monitoring_self_healer -.-> agent_utils_singleton_manager
    agent_monitoring_self_healer -.-> agent_circuit_breaker
    agent_monitoring_self_healer --> agent_monitoring_observability_config
    agent_monitoring_self_healer -.-> agent_permission_system
    agent_monitoring_self_healer -.-> agent_circuit_breaker
    agent_monitoring_self_healer -.-> agent_health_assessor
    agent_monitoring_self_healer -.-> agent_self_healing_policy
    agent_monitoring_alert_manager --> agent_monitoring_tracing
    agent_monitoring_alert_manager -.-> agent_logging_utils
    agent_monitoring_alert_manager --> agent_monitoring_alert_evaluator
    agent_monitoring_alert_manager --> agent_monitoring_alert_notifier
    agent_monitoring_alert_manager --> agent_monitoring_self_healer
    agent_monitoring_alert_manager -.-> agent_human_in_the_loop_takeover_queue
    agent_monitoring_alert_manager -.-> agent_utils_singleton_manager
    agent_monitoring_alert_manager -.-> agent_human_in_the_loop_takeover_queue
    agent_monitoring_alert_manager -.-> agent_self_healing_policy
    agent_monitoring_alert_manager --> agent_monitoring_observability_config
    agent_monitoring_performance_optimization --> agent_monitoring_tracing
    agent_monitoring_performance_optimization -.-> agent_utils_singleton_manager
    agent_monitoring_performance_optimization --> agent_monitoring_tracing
    agent_monitoring_tracing_cache --> agent_monitoring_tracing
    agent_monitoring_tracing_cache -.-> agent_utils_singleton_manager
    agent_monitoring_tracing_cache --> agent_monitoring_observability_config
    agent_monitoring_loki -.-> agent_logging_utils
    agent_monitoring_loki -.-> agent_utils_singleton_manager
    agent_monitoring_loki --> agent_monitoring_observability_config
    agent_network -.-> agent_network_config
    agent_network --> agent_network_config_validator
    agent_network_observability -.-> agent_logging_utils
    agent_network_observability -.-> agent_monitoring_business_metrics
    agent_knowledge --> agent_knowledge_schema
    agent_knowledge --> agent_knowledge_lifecycle
    agent_knowledge --> agent_knowledge_logbook
    agent_knowledge --> agent_knowledge_links
    agent_knowledge --> agent_knowledge_card
    agent_knowledge --> agent_knowledge_index
    agent_knowledge --> agent_knowledge_link_cache
    agent_knowledge --> agent_knowledge_search
    agent_knowledge_conflict --> agent_knowledge_card
    agent_knowledge_conflict --> agent_knowledge_logbook
    agent_knowledge_links --> agent_knowledge_schema
    agent_knowledge_index --> agent_knowledge_links
    agent_knowledge_index --> agent_knowledge_links_index
    agent_knowledge_index --> agent_knowledge_schema
    agent_knowledge_skill_bridge -.-> agent_skills_mgmt_creator
    agent_knowledge_skill_bridge -.-> agent_skills_mgmt_exceptions
    agent_knowledge_skill_bridge -.-> agent_skills_mgmt_reviewer
    agent_knowledge_skill_bridge -.-> agent_learning_metrics
    agent_knowledge_skill_bridge -.-> agent_skills_mgmt_store
    agent_knowledge_skill_bridge -.-> agent_skills_mgmt_creator
    agent_knowledge_skill_bridge --> agent_knowledge_card
    agent_knowledge_workflow --> agent_knowledge_card
    agent_knowledge_workflow --> agent_knowledge_discuss
    agent_knowledge_workflow --> agent_knowledge_distill
    agent_knowledge_workflow --> agent_knowledge_ingest
    agent_knowledge_workflow --> agent_knowledge_lint
    agent_knowledge_workflow --> agent_knowledge_logbook
    agent_knowledge_workflow --> agent_knowledge_schema
    agent_knowledge_workflow -.-> agent_logging_utils
    agent_knowledge_ingest -.-> agent_utils_sensitive_data_filter
    agent_knowledge_ingest -.-> agent_monitoring_observability_config
    agent_knowledge_search --> agent_knowledge_card
    agent_knowledge_search --> agent_knowledge_link_cache
    agent_knowledge_search --> agent_knowledge_schema
    agent_knowledge_search -.-> agent_utils_periodic_sampler
    agent_knowledge_search -.-> agent_logging_utils
    agent_knowledge___main__ --> agent_knowledge_card
    agent_knowledge___main__ --> agent_knowledge_distill
    agent_knowledge___main__ --> agent_knowledge_index
    agent_knowledge___main__ --> agent_knowledge_links
    agent_knowledge___main__ --> agent_knowledge_audit_job
    agent_knowledge___main__ --> agent_knowledge_lint
    agent_knowledge___main__ --> agent_knowledge_conflict
    agent_knowledge___main__ --> agent_knowledge_skill_bridge
    agent_knowledge___main__ -.-> agent_skills_mgmt_store
    agent_knowledge_card --> agent_knowledge_index
    agent_knowledge_card --> agent_knowledge_lifecycle
    agent_knowledge_card --> agent_knowledge_light_loader
    agent_knowledge_card --> agent_knowledge_links
    agent_knowledge_card --> agent_knowledge_links_index
    agent_knowledge_card --> agent_knowledge_logbook
    agent_knowledge_card --> agent_knowledge_schema
    agent_knowledge_card -.-> agent_logging_utils
    agent_knowledge_link_cache --> agent_knowledge_links
    agent_knowledge_link_cache --> agent_knowledge_schema
    agent_knowledge_audit_job --> agent_knowledge_lint
    agent_knowledge_audit_job --> agent_knowledge_logbook
    agent_knowledge_audit_job --> agent_knowledge_reporting
    agent_knowledge_tools --> agent_knowledge_card
    agent_knowledge_tools --> agent_knowledge_observability
    agent_knowledge_tools --> agent_knowledge_workflow
    agent_knowledge_tools -.-> agent_logging_utils
    agent_knowledge_tools -.-> agent_tools
    agent_knowledge_tools -.-> agent_tools
    agent_knowledge_tools --> agent_knowledge_card
    agent_knowledge_tools --> agent_knowledge_search
    agent_knowledge_discuss --> agent_knowledge_ingest
    agent_knowledge_discuss --> agent_knowledge_logbook
    agent_knowledge_discuss --> agent_knowledge_observability
    agent_knowledge_discuss --> agent_knowledge_prompts
    agent_knowledge_links_index --> agent_knowledge_links
    agent_knowledge_watcher --> agent_knowledge_ingest
    agent_knowledge_distill_feedback -.-> agent_cognitive_prompt_optimizer
    agent_knowledge_distill_feedback -.-> agent_feedback
    agent_knowledge_distill_feedback --> agent_knowledge_ingest
    agent_knowledge_reporting --> agent_knowledge_lint
    agent_knowledge_distill --> agent_knowledge_card
    agent_knowledge_distill --> agent_knowledge_ingest
    agent_knowledge_distill --> agent_knowledge_logbook
    agent_knowledge_distill --> agent_knowledge_observability
    agent_knowledge_distill --> agent_knowledge_prompts
    agent_knowledge_distill --> agent_knowledge_schema
    agent_knowledge_distill -.-> agent_logging_utils
    agent_knowledge_lint --> agent_knowledge_index
    agent_knowledge_lint --> agent_knowledge_links
    agent_config_etcd_config_client -.-> agent_logging_utils
    agent_config_etcd_config_client -.-> agent_orchestrator_orchestrator
    agent_config_etcd_config_client -.-> agent_orchestrator_orchestrator
    agent_config_etcd_config_client -.-> agent_monitoring_metrics
    agent_config_etcd_config_client -.-> agent_error_handler
    agent_config_etcd_config_client -.-> agent_orchestrator_orchestrator
    agent_config_etcd_config_client -.-> agent_orchestrator_orchestrator
    agent_model_router_router -.-> agent_logging_utils
    agent_model_router_observability -.-> agent_logging_utils
    agent_model_router_observability -.-> agent_monitoring_business_metrics
    agent_model_router_adapters -.-> agent_logging_utils
    agent_human_in_the_loop --> agent_human_in_the_loop_takeover_queue
    agent_human_in_the_loop_ethics -.-> agent_logging_utils
    agent_human_in_the_loop_observability -.-> agent_logging_utils
    agent_human_in_the_loop_observability -.-> agent_monitoring_business_metrics
    agent_human_in_the_loop_hitl -.-> agent_logging_utils
    agent_extensions --> agent_extensions_base
    agent_extensions --> agent_extensions_manager
    agent_extensions --> agent_extensions_store
    agent_extensions_sandbox -.-> agent_monitoring_tracing
    agent_extensions_sandbox -.-> agent_logging_utils
    agent_extensions_mcp_installer --> agent_extensions_base
    agent_extensions_mcp_installer --> agent_extensions_installer
    agent_extensions_mcp_installer --> agent_extensions_store
    agent_extensions_mcp_installer -.-> agent_logging_utils
    agent_extensions_installer -.-> agent_logging_utils
    agent_extensions_manager --> agent_extensions_base
    agent_extensions_manager --> agent_extensions_store
    agent_extensions_manager --> agent_extensions_skills_installer
    agent_extensions_manager --> agent_extensions_mcp_installer
    agent_extensions_manager --> agent_extensions_channels_installer
    agent_extensions_manager --> agent_extensions_plugins_installer
    agent_extensions_manager -.-> agent_logging_utils
    agent_extensions_store --> agent_extensions_base
    agent_extensions_store -.-> agent_logging_utils
    agent_extensions_plugins_installer --> agent_extensions_base
    agent_extensions_plugins_installer --> agent_extensions_installer
    agent_extensions_plugins_installer --> agent_extensions_store
    agent_extensions_plugins_installer -.-> agent_logging_utils
    agent_extensions_observability -.-> agent_logging_utils
    agent_extensions_observability -.-> agent_monitoring_business_metrics
    agent_extensions_skills_installer --> agent_extensions_base
    agent_extensions_skills_installer --> agent_extensions_installer
    agent_extensions_skills_installer --> agent_extensions_store
    agent_extensions_skills_installer -.-> agent_logging_utils
    agent_extensions_channels_installer --> agent_extensions_base
    agent_extensions_channels_installer --> agent_extensions_installer
    agent_extensions_channels_installer --> agent_extensions_store
    agent_extensions_channels_installer -.-> agent_logging_utils
    agent_extensions_market --> agent_extensions_base
    agent_extensions_market -.-> agent_logging_utils
    agent_extensions_security_checker -.-> agent_logging_utils
    agent_extensions_security_checker -.-> agent_utils_singleton_manager
    agent_extensions_security_checker --> agent_extensions_base
    agent_extensions_security_check_skill --> agent_extensions_security_checker
    agent_extensions_security_check_skill -.-> agent_logging_utils
    agent_extensions_security_check_skill --> agent_extensions_security_checker
    agent_extensions_dependency_manager -.-> agent_monitoring_tracing
    agent_extensions_dependency_manager -.-> agent_logging_utils
    agent_extensions_base -.-> agent_logging_utils
    agent_quality_defect_tracker -.-> agent_monitoring_observability_config
    agent_quality_defect_tracker -.-> agent_monitoring_observability_config
    agent_quality_observability -.-> agent_logging_utils
    agent_quality_observability -.-> agent_monitoring_business_metrics
    agent_learning_behavior_drift --> agent_learning_novelty_hooks
    agent_learning_behavior_drift -.-> agent_task_scheduler
    agent_learning_behavior_drift -.-> agent_task_scheduler
    agent_caching_multi_level_cache -.-> agent_logging_utils
    agent_caching_multi_level_cache -.-> agent_monitoring_observability_config
    agent_caching_observability -.-> agent_logging_utils
    agent_caching_observability -.-> agent_monitoring_business_metrics
    agent_workflow_engine_observability -.-> agent_logging_utils
    agent_workflow_engine_observability -.-> agent_monitoring_business_metrics
    agent_workflow_engine_registry -.-> agent_logging_utils
    agent_workflow_engine_matcher -.-> agent_logging_utils
    agent_prompt_manager_version_control -.-> agent_logging_utils
    agent_prompt_manager_version_control -.-> agent_utils_singleton_manager
    agent_prompt_manager_deployment -.-> agent_logging_utils
    agent_prompt_manager_deployment -.-> agent_utils_singleton_manager
    agent_prompt_manager_deployment --> agent_prompt_manager_version_control
    agent_prompt_manager_deployment --> agent_prompt_manager_version_control
    agent_prompt_manager_storage -.-> agent_logging_utils
    agent_prompt_manager_storage -.-> agent_utils_singleton_manager
    agent_prompt_manager_observability -.-> agent_logging_utils
    agent_prompt_manager_observability -.-> agent_monitoring_business_metrics
    agent_prompt_manager_registry -.-> agent_logging_utils
    agent_prompt_manager_registry -.-> agent_utils_singleton_manager
    agent_health_dashboard --> agent_health_assessor
    agent_health_dashboard --> agent_health_probes
    agent_health_dashboard --> agent_health_storage
    agent_health_observability -.-> agent_logging_utils
    agent_health_observability -.-> agent_monitoring_business_metrics
    agent_health_assessor -.-> agent_logging_utils
    agent_health_collector --> agent_health_assessor
    agent_health_collector --> agent_health_probes
    agent_health_collector --> agent_health_storage
    agent_health_probes -.-> agent_logging_utils
    agent_health_probes -.-> agent_monitoring_metrics
    agent_health_probes -.-> agent_monitoring_metrics
    agent_health_probes -.-> agent_feedback
    agent_health_probes -.-> agent_circuit_breaker
    agent_health_health_score -.-> agent_logging_utils
    agent_health_health_score -.-> agent_utils_singleton_manager
```

## 图例说明
- `-->` : 普通依赖（灰色实线）
- `-.->` : 跨层调用（允许但需关注，黄色虚线）
- `==>|违规|` : 跨层违规调用（红色粗线，目标节点红色背景，需修复）

## 统计信息
- 扫描文件数: 425
- 模块节点数: 364
- 依赖边数: 962
- 跨层调用数: 630
- 违规调用数: 0
- 动态 import 数: 1
- 构建耗时: 1758.62 ms
