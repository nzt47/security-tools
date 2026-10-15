# 未接线（死副本）路由模块清单

> 生成：2026-10-15T21:38:28　数据源：app_server.py 接线清单 + agent/server_routes/*.py 的 AST + reports/route_ui_coverage.json
> 口径：这些模块定义了 register_routes，但 app_server.py 没有接线（见 tests/unit/test_server_routes_registration_inventory.py 的 KNOWN_UNREGISTERED）；其路径由别处（plugins / 已接线模块）提供，改这些死副本不生效也不报错。

## 总览

| 模块 | 用途（模块 docstring 首行） | 定义路由数 | 死副本位点 | 活体提供方（去重） | KNOWN_UNREGISTERED 原因 |
|---|---|---:|---:|---|---|
| routes_config.py | 配置 & 网络配置 & LLM & MCP API 路由 | 27 | 27 | plugins/admin.py, plugins/mcp_scheduler.py | 未接线；27/29 条端点由其他模块提供，语义层热更两条（/api/orchestrator/semantic-config）已单独接线 |
| routes_workspace.py | 工作区 & 系统工具 API 路由 | 21 | 21 | plugins/system_tools.py | 未接线；工作区端点由已注册模块提供 |
| routes_monitoring.py | 心跳 & 定时任务 & 性能监控 & 测试 API 路由 | 18 | 18 | app_server.py, plugins/admin.py, plugins/mcp_scheduler.py, plugins/status.py | 未接线；监控端点由 plugins/status.py 与 routes_dashboard.py 提供 |
| routes_chat.py | 对话 & 语音 & Web 工具 API 路由 | 12 | 12 | plugins/chat.py, plugins/system_tools.py | 未接线；聊天端点由 plugins/chat.py 提供（url_map 中为 chat.* 命名空间） |
| routes_sessions.py | 会话管理 API 路由 | 12 | 12 | plugins/admin.py, plugins/chat.py | 未接线（**故意**）；会话 API 由 plugins/chat.py 提供。原 `/api/handoff` 于 2026-10-01 迁入 plugins/chat.py:api_handoff（此前是「声明了却没接线 ⇒ 线上 404」的孤儿，见 app_server.py:1719 的 T7 注记） |
| extensions.py | 扩展系统 API 路由 | 11 | 11 | plugins/skills.py | 未接线；扩展端点由 plugins/* 提供（注意：本文件名不带 routes_ 前缀） |
| routes_permission.py | 权限 & 安全 & 操作追踪 API 路由 | 9 | 9 | plugins/safety.py | 未接线；权限端点由 plugins/* 与 routes_ui_panels.py 提供 |
| routes_panorama.py | 全景 & 健康 & 状态 API 路由 | 7 | 7 | cognitive/flask_adapter.py, plugins/status.py | 未接线；全景面板端点由其它已注册模块提供 |
| routes_personality.py | 人格配置 API 路由 | 4 | 4 | plugins/status.py | 未接线；人格端点由 plugins/status.py 提供 |
| routes_cli.py | CLI 软件管理 API 路由（占位） | 0 | 0 | - | 未接线；本文件仅 9 行占位（只打一条 info 日志） |
| routes_computer_use.py | Computer Use API 路由（占位） | 0 | 0 | - | 未接线；本文件仅占位实现 |

> routes_cli.py / routes_computer_use.py 也在 KNOWN_UNREGISTERED，但只定义 register_routes 占位、不定义任何路由（0 条）。

## agent/server_routes/routes_config.py（27 条路由）

**用途**：配置 & 网络配置 & LLM & MCP API 路由

**为什么是死副本**：未接线；27/29 条端点由其他模块提供，语义层热更两条（/api/orchestrator/semantic-config）已单独接线

| 路径 | 方法 | 处理函数 | 说明（handler docstring 首行） | 死副本位点 | 活体在 |
|---|---|---|---|---|---|
| /api/network-config | GET,POST | api_network_config_get |  | agent/server_routes/routes_config.py:133 | plugins/admin.py:196, plugins/admin.py:205 |
| /api/network-config | GET,POST | api_network_config_update |  | agent/server_routes/routes_config.py:140 | plugins/admin.py:196, plugins/admin.py:205 |
| /api/network-config/reset | POST | api_network_config_reset |  | agent/server_routes/routes_config.py:153 | plugins/admin.py:241 |
| /api/network-config/export | GET | api_network_config_export |  | agent/server_routes/routes_config.py:161 | plugins/admin.py:251 |
| /api/network-config/import | POST | api_network_config_import |  | agent/server_routes/routes_config.py:172 | plugins/admin.py:264 |
| /api/apply-network-config | POST | api_apply_network_config |  | agent/server_routes/routes_config.py:190 | plugins/admin.py:286 |
| /api/llm/instances | GET,POST | api_llm_instances_get |  | agent/server_routes/routes_config.py:232 | plugins/admin.py:337, plugins/admin.py:365 |
| /api/llm/instances/<string:instance_id> | DELETE,GET,PUT | api_llm_instance_get |  | agent/server_routes/routes_config.py:243 | plugins/admin.py:350, plugins/admin.py:388, plugins/admin.py:408 |
| /api/llm/instances | GET,POST | api_llm_instance_add |  | agent/server_routes/routes_config.py:256 | plugins/admin.py:337, plugins/admin.py:365 |
| /api/llm/instances/<string:instance_id> | DELETE,GET,PUT | api_llm_instance_update |  | agent/server_routes/routes_config.py:276 | plugins/admin.py:350, plugins/admin.py:388, plugins/admin.py:408 |
| /api/llm/instances/<string:instance_id> | DELETE,GET,PUT | api_llm_instance_delete |  | agent/server_routes/routes_config.py:295 | plugins/admin.py:350, plugins/admin.py:388, plugins/admin.py:408 |
| /api/llm/instances/<string:instance_id>/default | POST | api_llm_instance_set_default |  | agent/server_routes/routes_config.py:310 | plugins/admin.py:423 |
| /api/llm/instances/<string:instance_id>/test | POST | api_llm_instance_test | 测试 LLM 实例连通性 | agent/server_routes/routes_config.py:325 | plugins/admin.py:438 |
| /api/mcp/services | GET,POST | api_mcp_services_get |  | agent/server_routes/routes_config.py:398 | plugins/mcp_scheduler.py:51, plugins/mcp_scheduler.py:85 |
| /api/mcp/services/<string:service_id> | DELETE,GET,PUT | api_mcp_service_get |  | agent/server_routes/routes_config.py:409 | plugins/mcp_scheduler.py:108, plugins/mcp_scheduler.py:128, plugins/mcp_scheduler.py:70 |
| /api/mcp/services | GET,POST | api_mcp_service_add |  | agent/server_routes/routes_config.py:422 | plugins/mcp_scheduler.py:51, plugins/mcp_scheduler.py:85 |
| /api/mcp/services/<string:service_id> | DELETE,GET,PUT | api_mcp_service_update |  | agent/server_routes/routes_config.py:440 | plugins/mcp_scheduler.py:108, plugins/mcp_scheduler.py:128, plugins/mcp_scheduler.py:70 |
| /api/mcp/services/<string:service_id> | DELETE,GET,PUT | api_mcp_service_delete |  | agent/server_routes/routes_config.py:457 | plugins/mcp_scheduler.py:108, plugins/mcp_scheduler.py:128, plugins/mcp_scheduler.py:70 |
| /api/mcp/enable | POST | api_mcp_enable |  | agent/server_routes/routes_config.py:470 | plugins/mcp_scheduler.py:143 |
| /api/search/instances | GET,POST | api_search_instances_get |  | agent/server_routes/routes_config.py:489 | plugins/admin.py:491, plugins/admin.py:503 |
| /api/search/instances | GET,POST | api_search_instance_add |  | agent/server_routes/routes_config.py:501 | plugins/admin.py:491, plugins/admin.py:503 |
| /api/search/instances/<string:instance_id> | DELETE,PUT | api_search_instance_update |  | agent/server_routes/routes_config.py:540 | plugins/admin.py:563, plugins/admin.py:623 |
| /api/search/instances/<string:instance_id> | DELETE,PUT | api_search_instance_delete |  | agent/server_routes/routes_config.py:574 | plugins/admin.py:563, plugins/admin.py:623 |
| /api/search/instances/<string:instance_id>/default | POST | api_search_instance_set_default |  | agent/server_routes/routes_config.py:601 | plugins/admin.py:685 |
| /api/search/instances/<string:instance_id>/test | POST | api_search_instance_test | 测试搜索实例连通性 | agent/server_routes/routes_config.py:633 | plugins/admin.py:733 |
| /api/config/logs | GET | api_config_logs |  | agent/server_routes/routes_config.py:679 | plugins/admin.py:768 |
| /api/config | GET,POST | api_config | LLM 配置读写（S2-02：POST 写操作显式审计 config.write；api_key 内容不入链） | agent/server_routes/routes_config.py:87 | plugins/admin.py:71 |

## agent/server_routes/routes_workspace.py（21 条路由）

**用途**：工作区 & 系统工具 API 路由

**为什么是死副本**：未接线；工作区端点由已注册模块提供

| 路径 | 方法 | 处理函数 | 说明（handler docstring 首行） | 死副本位点 | 活体在 |
|---|---|---|---|---|---|
| /api/filesystem/write | POST | api_filesystem_write |  | agent/server_routes/routes_workspace.py:118 | plugins/system_tools.py:165 |
| /api/filesystem/list | GET | api_filesystem_list |  | agent/server_routes/routes_workspace.py:135 | plugins/system_tools.py:187 |
| /api/filesystem/info | GET | api_filesystem_info |  | agent/server_routes/routes_workspace.py:144 | plugins/system_tools.py:198 |
| /api/filesystem/search | GET | api_filesystem_search |  | agent/server_routes/routes_workspace.py:153 | plugins/system_tools.py:209 |
| /api/sandbox/run | POST | api_sandbox_run |  | agent/server_routes/routes_workspace.py:167 | plugins/system_tools.py:225 |
| /api/browser/navigate | POST | api_browser_navigate |  | agent/server_routes/routes_workspace.py:206 | plugins/system_tools.py:284 |
| /api/browser/screenshot | GET | api_browser_screenshot |  | agent/server_routes/routes_workspace.py:217 | plugins/system_tools.py:296 |
| /api/browser/close | POST | api_browser_close |  | agent/server_routes/routes_workspace.py:225 | plugins/system_tools.py:305 |
| /api/process/list | GET | api_process_list |  | agent/server_routes/routes_workspace.py:237 | plugins/system_tools.py:318 |
| /api/process/whitelist | GET | api_process_whitelist |  | agent/server_routes/routes_workspace.py:243 | plugins/system_tools.py:326 |
| /api/process/whitelist/add | POST | api_process_whitelist_add |  | agent/server_routes/routes_workspace.py:249 | plugins/system_tools.py:334 |
| /api/process/whitelist/remove | POST | api_process_whitelist_remove |  | agent/server_routes/routes_workspace.py:258 | plugins/system_tools.py:344 |
| /api/process/start | POST | api_process_start |  | agent/server_routes/routes_workspace.py:267 | plugins/system_tools.py:354 |
| /api/workspace | GET | api_workspace_list |  | agent/server_routes/routes_workspace.py:27 | plugins/system_tools.py:61 |
| /api/process/stop | POST | api_process_stop |  | agent/server_routes/routes_workspace.py:279 | plugins/system_tools.py:367 |
| /api/clipboard | GET,POST | api_clipboard_get |  | agent/server_routes/routes_workspace.py:294 | plugins/system_tools.py:383, plugins/system_tools.py:391 |
| /api/clipboard | GET,POST | api_clipboard_set |  | agent/server_routes/routes_workspace.py:301 | plugins/system_tools.py:383, plugins/system_tools.py:391 |
| /api/workspace/write | POST | api_workspace_write |  | agent/server_routes/routes_workspace.py:40 | plugins/system_tools.py:76 |
| /api/workspace/delete | POST | api_workspace_delete |  | agent/server_routes/routes_workspace.py:59 | plugins/system_tools.py:98 |
| /api/workspace/info | GET | api_workspace_info |  | agent/server_routes/routes_workspace.py:74 | plugins/system_tools.py:114 |
| /api/filesystem/read | POST | api_filesystem_read |  | agent/server_routes/routes_workspace.py:98 | plugins/system_tools.py:141 |

## agent/server_routes/routes_monitoring.py（18 条路由）

**用途**：心跳 & 定时任务 & 性能监控 & 测试 API 路由

**为什么是死副本**：未接线；监控端点由 plugins/status.py 与 routes_dashboard.py 提供

| 路径 | 方法 | 处理函数 | 说明（handler docstring 首行） | 死副本位点 | 活体在 |
|---|---|---|---|---|---|
| /api/scheduler/delete | POST | api_scheduler_delete |  | agent/server_routes/routes_monitoring.py:100 | plugins/mcp_scheduler.py:195 |
| /api/scheduler/toggle | POST | api_scheduler_toggle |  | agent/server_routes/routes_monitoring.py:109 | plugins/mcp_scheduler.py:206 |
| /api/scheduler/execute-now | POST | api_scheduler_execute_now |  | agent/server_routes/routes_monitoring.py:119 | plugins/mcp_scheduler.py:218 |
| /api/scheduler/history | GET | api_scheduler_history |  | agent/server_routes/routes_monitoring.py:134 | plugins/mcp_scheduler.py:235 |
| /api/search-performance/status | GET | api_search_performance_status |  | agent/server_routes/routes_monitoring.py:149 | plugins/admin.py:846 |
| /api/search-performance/start | POST | api_search_performance_start |  | agent/server_routes/routes_monitoring.py:160 | plugins/admin.py:859 |
| /api/search-performance/stop | POST | api_search_performance_stop |  | agent/server_routes/routes_monitoring.py:174 | plugins/admin.py:875 |
| /api/search-performance/check | POST | api_search_performance_check |  | agent/server_routes/routes_monitoring.py:186 | plugins/admin.py:889 |
| /api/search-performance/history | GET | api_search_performance_history |  | agent/server_routes/routes_monitoring.py:198 | plugins/admin.py:903 |
| /api/search-performance/summary | GET | api_search_performance_summary |  | agent/server_routes/routes_monitoring.py:210 | plugins/admin.py:917 |
| /api/test/error | GET | api_test_error |  | agent/server_routes/routes_monitoring.py:225 | app_server.py:2216 |
| /api/test/null | GET | api_test_null |  | agent/server_routes/routes_monitoring.py:232 | app_server.py:2229 |
| /api/test/division | GET | api_test_division |  | agent/server_routes/routes_monitoring.py:239 | app_server.py:2240 |
| /api/heartbeat | GET | api_heartbeat |  | agent/server_routes/routes_monitoring.py:25 | plugins/status.py:789 |
| /api/heartbeat/history | GET | api_heartbeat_history |  | agent/server_routes/routes_monitoring.py:37 | plugins/status.py:815 |
| /api/heartbeat/status | GET | api_heartbeat_status |  | agent/server_routes/routes_monitoring.py:56 | plugins/status.py:844 |
| /api/scheduler/tasks | GET | api_scheduler_list |  | agent/server_routes/routes_monitoring.py:77 | plugins/mcp_scheduler.py:166 |
| /api/scheduler/create | POST | api_scheduler_create |  | agent/server_routes/routes_monitoring.py:83 | plugins/mcp_scheduler.py:174 |

## agent/server_routes/routes_chat.py（12 条路由）

**用途**：对话 & 语音 & Web 工具 API 路由

**为什么是死副本**：未接线；聊天端点由 plugins/chat.py 提供（url_map 中为 chat.* 命名空间）

| 路径 | 方法 | 处理函数 | 说明（handler docstring 首行） | 死副本位点 | 活体在 |
|---|---|---|---|---|---|
| /api/voice/listen | POST | api_voice_listen |  | agent/server_routes/routes_chat.py:157 | plugins/chat.py:83 |
| /api/voice/status | GET | api_voice_status |  | agent/server_routes/routes_chat.py:237 | plugins/chat.py:120 |
| /api/chat | POST | api_chat |  | agent/server_routes/routes_chat.py:258 | plugins/chat.py:141 |
| /api/web/get | POST | api_web_get |  | agent/server_routes/routes_chat.py:452 | plugins/system_tools.py:405 |
| /api/web/post | POST | api_web_post |  | agent/server_routes/routes_chat.py:482 | plugins/system_tools.py:423 |
| /api/web/xpath | POST | api_web_xpath |  | agent/server_routes/routes_chat.py:499 | plugins/system_tools.py:442 |
| /api/web/css | POST | api_web_css |  | agent/server_routes/routes_chat.py:521 | plugins/system_tools.py:469 |
| /api/web/search | GET | api_web_search |  | agent/server_routes/routes_chat.py:544 | plugins/system_tools.py:497 |
| /api/web/clean | POST | api_web_clean |  | agent/server_routes/routes_chat.py:576 | plugins/system_tools.py:518 |
| /api/web/download | POST | api_web_download |  | agent/server_routes/routes_chat.py:597 | plugins/system_tools.py:541 |
| /api/web/stats | GET | api_web_stats |  | agent/server_routes/routes_chat.py:609 | plugins/system_tools.py:554 |
| /api/web/search/status | GET | api_web_search_status |  | agent/server_routes/routes_chat.py:620 | plugins/system_tools.py:567 |

## agent/server_routes/routes_sessions.py（12 条路由）

**用途**：会话管理 API 路由

**为什么是死副本**：未接线（**故意**）；会话 API 由 plugins/chat.py 提供。原 `/api/handoff` 于 2026-10-01 迁入 plugins/chat.py:api_handoff（此前是「声明了却没接线 ⇒ 线上 404」的孤儿，见 app_server.py:1719 的 T7 注记）

| 路径 | 方法 | 处理函数 | 说明（handler docstring 首行） | 死副本位点 | 活体在 |
|---|---|---|---|---|---|
| /api/sessions/<session_id>/rename | PUT | api_sessions_rename |  | agent/server_routes/routes_sessions.py:108 | plugins/chat.py:582 |
| /api/sessions/current | POST | api_sessions_set_current |  | agent/server_routes/routes_sessions.py:120 | plugins/chat.py:597 |
| /api/sessions/<session_id>/messages | DELETE,GET | api_sessions_messages |  | agent/server_routes/routes_sessions.py:144 | plugins/chat.py:626, plugins/chat.py:636 |
| /api/history | GET | api_history |  | agent/server_routes/routes_sessions.py:153 | plugins/chat.py:933 |
| /api/clear | POST | api_clear |  | agent/server_routes/routes_sessions.py:175 | plugins/chat.py:955 |
| /api/auth/token-check | GET | api_auth_token_check |  | agent/server_routes/routes_sessions.py:185 | plugins/admin.py:58 |
| /api/history/search | GET | api_history_search |  | agent/server_routes/routes_sessions.py:191 | plugins/chat.py:969 |
| /api/history/<int:index> | DELETE | api_history_delete |  | agent/server_routes/routes_sessions.py:208 | plugins/chat.py:989 |
| /api/handoff | POST | api_handoff | 生成交接文档 — LLM 压缩当前会话为 Markdown，保存到 OS 临时目录 | agent/server_routes/routes_sessions.py:248 | plugins/chat.py:675 |
| /api/sessions | GET,POST | api_sessions_list |  | agent/server_routes/routes_sessions.py:77 | plugins/chat.py:536, plugins/chat.py:549 |
| /api/sessions | GET,POST | api_sessions_create |  | agent/server_routes/routes_sessions.py:87 | plugins/chat.py:536, plugins/chat.py:549 |
| /api/sessions/<session_id> | DELETE | api_sessions_delete |  | agent/server_routes/routes_sessions.py:98 | plugins/chat.py:562 |

## agent/server_routes/extensions.py（11 条路由）

**用途**：扩展系统 API 路由

**为什么是死副本**：未接线；扩展端点由 plugins/* 提供（注意：本文件名不带 routes_ 前缀）

| 路径 | 方法 | 处理函数 | 说明（handler docstring 首行） | 死副本位点 | 活体在 |
|---|---|---|---|---|---|
| /api/extensions/discover | GET | api_extensions_discover |  | agent/server_routes/extensions.py:101 | plugins/skills.py:754 |
| /api/extensions/market/search | GET | api_extensions_market_search |  | agent/server_routes/extensions.py:115 | plugins/skills.py:768 |
| /api/extensions/market/recommend | GET | api_extensions_market_recommend |  | agent/server_routes/extensions.py:128 | plugins/skills.py:786 |
| /api/extensions/market/refresh | POST | api_extensions_market_refresh |  | agent/server_routes/extensions.py:140 | plugins/skills.py:802 |
| /api/extensions/channels/send | POST | api_extensions_channel_send |  | agent/server_routes/extensions.py:156 | plugins/skills.py:818 |
| /api/extensions/list | GET | api_extensions_list |  | agent/server_routes/extensions.py:17 | plugins/skills.py:635 |
| /api/extensions/installed | GET | api_extensions_installed |  | agent/server_routes/extensions.py:28 | plugins/skills.py:650 |
| /api/extensions/install | POST | api_extensions_install |  | agent/server_routes/extensions.py:38 | plugins/skills.py:664 |
| /api/extensions/uninstall | POST | api_extensions_uninstall |  | agent/server_routes/extensions.py:54 | plugins/skills.py:686 |
| /api/extensions/toggle | POST | api_extensions_toggle |  | agent/server_routes/extensions.py:69 | plugins/skills.py:710 |
| /api/extensions/configure | POST | api_extensions_configure |  | agent/server_routes/extensions.py:85 | plugins/skills.py:732 |

## agent/server_routes/routes_permission.py（9 条路由）

**用途**：权限 & 安全 & 操作追踪 API 路由

**为什么是死副本**：未接线；权限端点由 plugins/* 与 routes_ui_panels.py 提供

| 路径 | 方法 | 处理函数 | 说明（handler docstring 首行） | 死副本位点 | 活体在 |
|---|---|---|---|---|---|
| /api/permission/log | GET | api_permission_log |  | agent/server_routes/routes_permission.py:107 | plugins/safety.py:211 |
| /api/permission/stats | GET | api_permission_stats |  | agent/server_routes/routes_permission.py:122 | plugins/safety.py:231 |
| /api/permission/access-log | GET | api_permission_access_log |  | agent/server_routes/routes_permission.py:166 | plugins/safety.py:280 |
| /api/permission/emergency | POST | api_permission_emergency |  | agent/server_routes/routes_permission.py:175 | plugins/safety.py:291 |
| /api/permission/toggle | POST | api_permission_toggle |  | agent/server_routes/routes_permission.py:207 | plugins/safety.py:326 |
| /api/safety/check | POST | api_safety_check |  | agent/server_routes/routes_permission.py:36 | plugins/safety.py:125 |
| /api/safety/alerts | GET | api_safety_alerts |  | agent/server_routes/routes_permission.py:46 | plugins/safety.py:137 |
| /api/safety/keywords | GET,POST | api_safety_keywords |  | agent/server_routes/routes_permission.py:54 | plugins/safety.py:147 |
| /api/permission/status | GET | api_permission_status |  | agent/server_routes/routes_permission.py:76 | plugins/safety.py:172 |

## agent/server_routes/routes_panorama.py（7 条路由）

**用途**：全景 & 健康 & 状态 API 路由

**为什么是死副本**：未接线；全景面板端点由其它已注册模块提供

| 路径 | 方法 | 处理函数 | 说明（handler docstring 首行） | 死副本位点 | 活体在 |
|---|---|---|---|---|---|
| /api/health | GET | api_health |  | agent/server_routes/routes_panorama.py:169 | plugins/status.py:234 |
| /api/health/auth | GET | api_health_auth | 鉴权配置状态（**只读**；TASK-06 §3 第 5 步第 2 项第 ① 步） | agent/server_routes/routes_panorama.py:176 | plugins/status.py:242 |
| /api/sensors | GET | api_sensors |  | agent/server_routes/routes_panorama.py:198 | plugins/status.py:270 |
| /api/status | GET | api_status |  | agent/server_routes/routes_panorama.py:204 | plugins/status.py:283 |
| /api/mode | GET | api_mode |  | agent/server_routes/routes_panorama.py:220 | plugins/status.py:311 |
| /api/cognitive/status | GET | api_cognitive_status |  | agent/server_routes/routes_panorama.py:237 | cognitive/flask_adapter.py:33, plugins/status.py:353 |
| /api/panorama | GET | api_panorama | 获取全景页面所需的所有数据（单次调用） | agent/server_routes/routes_panorama.py:256 | plugins/status.py:373 |

## agent/server_routes/routes_personality.py（4 条路由）

**用途**：人格配置 API 路由

**为什么是死副本**：未接线；人格端点由 plugins/status.py 提供

| 路径 | 方法 | 处理函数 | 说明（handler docstring 首行） | 死副本位点 | 活体在 |
|---|---|---|---|---|---|
| /api/personality | GET | api_personality_get |  | agent/server_routes/routes_personality.py:23 | plugins/status.py:665 |
| /api/personality/params | POST | api_personality_params |  | agent/server_routes/routes_personality.py:29 | plugins/status.py:684 |
| /api/personality/profile | POST | api_personality_profile |  | agent/server_routes/routes_personality.py:39 | plugins/status.py:694 |
| /api/personality/reset | POST | api_personality_reset |  | agent/server_routes/routes_personality.py:49 | plugins/status.py:704 |

## agent/server_routes/routes_cli.py（0 条路由）

**用途**：CLI 软件管理 API 路由（占位）

**为什么是死副本**：未接线；本文件仅 9 行占位（只打一条 info 日志）

（本模块不定义路由）

## agent/server_routes/routes_computer_use.py（0 条路由）

**用途**：Computer Use API 路由（占位）

**为什么是死副本**：未接线；本文件仅占位实现

（本模块不定义路由）

## 无路由的占位模块

- agent/server_routes/routes_cli.py：CLI 软件管理 API 路由（占位） —— KNOWN_UNREGISTERED：未接线；本文件仅 9 行占位（只打一条 info 日志）
- agent/server_routes/routes_computer_use.py：Computer Use API 路由（占位） —— KNOWN_UNREGISTERED：未接线；本文件仅占位实现

## 非路由的 server_routes 辅助模块（无 register_routes、不是死副本）

- agent/server_routes/observability.py：server_routes 模块可观测性埋点
- agent/server_routes/tracing_decorator.py：API 路由追踪装饰器模块
- agent/server_routes/tracing_middleware.py：Flask 分布式追踪中间件

