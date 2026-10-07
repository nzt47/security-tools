/**
 * 云枢前端**端点常量层**（阶段 5 / R5 —— 契约消费收敛的第一块地基）。
 *
 * 【为什么需要它】审计实测：非测试前端有 **256 处**硬编码 `/api` 字面量
 * （React 150 + legacy 106），散在 41 个文件里。它们的害处不是"多打几个字"，而是：
 *   · 后端改了路径/方法，**找不到该改哪些前端文件**（本仓已发生：契约对拍检出 2 处
 *     "方法与路径双错"，编译期全绿、上线才炸）；
 *   · 同一个端点在不同文件里写法不一致（`/api/cp` 与 `/api/cp/tool-exemptions`）；
 *   · 后续要接"显式响应解析"与生成式客户端时，没有可插桩的收口点。
 *
 * 【本层的纪律 —— 两条，都要守】
 *   ① **只放端点路径，不放业务逻辑**。这里每一行都必须是可直接与后端 url_map 对拍的
 *      完整字面量路径（`'/api/xxx'`），**不得**用变量拼出 `/api` 前缀 ——
 *      否则 scripts/audit/contract_diff.py 的"前端调用 → 后端路由"对拍会**失明**，
 *      而那正是当年检出 2 个真实缺陷的判据。
 *   ② **动态段用函数，不用字符串拼接散落各处**：`skillById(id)` 而不是在每个调用点
 *      写模板串。函数内部仍是完整模板串，对拍照样看得见。
 *
 * 【与 contract_diff 的关系】该工具的 `frontend_literals` 计数**排除本文件**
 * （本文件就是被许可的常量层），但**不排除**它的路径参与"是否命中真实后端路由"的校验 ——
 * 计数与正确性分开：计数要的是"散落的还有多少"，正确性要的是"每一条都真实存在"。
 *
 * 【一处**有意**的行为归一（不是遗漏，写在最显眼处）】
 * 动态段的 `encodeURIComponent` 在本层**统一加上**。迁移前 5 个文件里有 4 个已经这么写
 * （sessionApi / knowledge / experienceApi / visualWorkflowApi），只有 skillsApi 没有。
 * 本层取了"更安全的那一边"：对 UUID / slug 这类 id 而言它是**恒等变换**（无行为变化），
 * 而对含 `/`、空格等字符的 id 它是**修复**（不加会被后端当成额外的路径段）。
 * 唯一受影响的是 skillsApi 那条路径，其 id 由后端生成、形态固定，故为无操作。
 * 若将来要改回不编码，**只改这一处**即可 —— 这正是收口点存在的意义。
 */

// ═══════════════════════════════════════════════════════════════
//  技能管理（/api/skills-mgmt）
// ═══════════════════════════════════════════════════════════════
export const SKILLS_MGMT = '/api/skills-mgmt';
export const SKILLS_MGMT_HEALTH = '/api/skills-mgmt/health';
export const SKILLS_MGMT_SEARCH = '/api/skills-mgmt/search';
export const SKILLS_MGMT_CREATE_AI = '/api/skills-mgmt/create/ai';
export const SKILLS_MGMT_CREATE_MANUAL = '/api/skills-mgmt/create/manual';
export const SKILLS_MGMT_INSTALL = '/api/skills-mgmt/install';
export const SKILLS_MGMT_REVIEW_BATCH = '/api/skills-mgmt/review/batch';
export const SKILLS_MGMT_REVIEW_THRESHOLDS = '/api/skills-mgmt/review/thresholds';
export const SKILLS_MGMT_META_CATEGORIES = '/api/skills-mgmt/meta/categories';

/** 单个技能：GET 详情 / PUT 更新 / DELETE 删除（同一路径） */
export const skillById = (skillId: string) => `/api/skills-mgmt/${encodeURIComponent(skillId)}`;
export const skillReview = (skillId: string) => `/api/skills-mgmt/${encodeURIComponent(skillId)}/review`;
export const skillToggle = (skillId: string) => `/api/skills-mgmt/${encodeURIComponent(skillId)}/toggle`;
export const skillOptimize = (skillId: string) => `/api/skills-mgmt/${encodeURIComponent(skillId)}/optimize`;
export const skillExecution = (skillId: string) => `/api/skills-mgmt/${encodeURIComponent(skillId)}/execution`;
export const skillVersions = (skillId: string) => `/api/skills-mgmt/${encodeURIComponent(skillId)}/versions`;
export const skillVersionsBump = (skillId: string) => `/api/skills-mgmt/${encodeURIComponent(skillId)}/versions/bump`;
export const skillVersionsRollback = (skillId: string) => `/api/skills-mgmt/${encodeURIComponent(skillId)}/versions/rollback`;

// ═══════════════════════════════════════════════════════════════
//  工作流学习（/api/workflow-learning）
// ═══════════════════════════════════════════════════════════════
export const WORKFLOW_LEARNING_HEALTH = '/api/workflow-learning/health';
export const WORKFLOW_LEARNING_LEARN = '/api/workflow-learning/learn';
export const WORKFLOW_LEARNING_MATCH = '/api/workflow-learning/match';
export const WORKFLOW_LEARNING_TRY_EXECUTE = '/api/workflow-learning/try-execute';
export const WORKFLOW_LEARNING_WORKFLOWS = '/api/workflow-learning/workflows';

export const workflowLearnExecute = (wfId: string) => `/api/workflow-learning/execute/${encodeURIComponent(wfId)}`;
export const workflowLearnWorkflows = (wfId: string) => `/api/workflow-learning/workflows/${encodeURIComponent(wfId)}`;
export const workflowLearnToggle = (wfId: string) => `/api/workflow-learning/workflows/${encodeURIComponent(wfId)}/toggle`;
export const workflowLearnPriority = (wfId: string) => `/api/workflow-learning/workflows/${encodeURIComponent(wfId)}/priority`;

// ═══════════════════════════════════════════════════════════════
//  会话 / 工作区（/api/sessions*）
// ═══════════════════════════════════════════════════════════════
export const SESSIONS = '/api/sessions';
export const SESSIONS_CURRENT = '/api/sessions/current';
export const SESSION_GROUPS = '/api/session-groups';
export const WORKSPACES = '/api/workspaces';

export const sessionById = (id: string) => `/api/sessions/${encodeURIComponent(id)}`;
export const sessionRename = (id: string) => `/api/sessions/${encodeURIComponent(id)}/rename`;
export const sessionMessages = (id: string) => `/api/sessions/${encodeURIComponent(id)}/messages`;
export const sessionWorkspace = (id: string) => `/api/sessions/${encodeURIComponent(id)}/workspace`;
export const sessionWorkspaceReveal = (id: string) => `/api/sessions/${encodeURIComponent(id)}/workspace/reveal`;
export const sessionWorkspaceRoot = (id: string) => `/api/sessions/${encodeURIComponent(id)}/workspace-root`;
export const sessionGroupSet = (sessionId: string) => `/api/sessions/${encodeURIComponent(sessionId)}/group`;
export const sessionGroupById = (id: string) => `/api/session-groups/${encodeURIComponent(id)}`;

// ═══════════════════════════════════════════════════════════════
//  知识库（/api/knowledge）
// ═══════════════════════════════════════════════════════════════
export const KNOWLEDGE_CARDS = '/api/knowledge/cards';
export const KNOWLEDGE_GRAPH = '/api/knowledge/graph';
export const KNOWLEDGE_INDEX = '/api/knowledge/index';
export const KNOWLEDGE_LINT = '/api/knowledge/lint';
export const KNOWLEDGE_QUERY = '/api/knowledge/query';

/** 单张卡片：GET 详情 / PUT 更新 / DELETE 删除（同一路径） */
export const knowledgeCard = (slug: string) => `/api/knowledge/cards/${encodeURIComponent(slug)}`;

// ═══════════════════════════════════════════════════════════════
//  上下文监控（/api/context）
// ═══════════════════════════════════════════════════════════════
export const CONTEXT_STATUS = '/api/context/status';
export const CONTEXT_CONFIG = '/api/context/config';
export const CONTEXT_COMPRESS = '/api/context/compress';

// ═══════════════════════════════════════════════════════════════
//  经验库（/api/experience）
// ═══════════════════════════════════════════════════════════════
export const EXPERIENCE_STATS = '/api/experience/stats';
export const EXPERIENCE_INGEST = '/api/experience/ingest';
export const EXPERIENCE_LIST = '/api/experience/list';
export const EXPERIENCE_SEARCH = '/api/experience/search';

export const experienceById = (id: string) => `/api/experience/${encodeURIComponent(id)}`;
export const experienceReview = (id: string) => `/api/experience/${encodeURIComponent(id)}/review`;
export const experienceBatchRollback = (batchId: string) => `/api/experience/batch/${encodeURIComponent(batchId)}/rollback`;

// ═══════════════════════════════════════════════════════════════
//  可视化工作流 / 审批 / 能力清单 / 智能体线
// ═══════════════════════════════════════════════════════════════
export const VISUAL_WORKFLOWS = '/api/visual-workflows';
export const visualWorkflowById = (id: string) => `/api/visual-workflows/${encodeURIComponent(id)}`;

export const APPROVAL_SESSION = '/api/approval/session';
export const CAPABILITY_MANIFEST = '/api/capability-manifest';
export const AGENT_LINES = '/api/agent-lines';

// ═══════════════════════════════════════════════════════════════
//  控制平面（/api/cp）—— cpPanelsApi 与 toolExemptionsApi 此前各写一份 PREFIX，
//  口径不一，现收口于此。
// ═══════════════════════════════════════════════════════════════
export const CP = '/api/cp';
export const CP_TOOL_EXEMPTIONS = '/api/cp/tool-exemptions';
// ===============================================================
//  技能评审 / 评估（/api/skills-mgmt/assess|classes|queue|review）
// ===============================================================
export const SKILLS_MGMT_ASSESS_EVENTS = '/api/skills-mgmt/assess/events';
export const SKILLS_MGMT_ASSESS_STREAM = '/api/skills-mgmt/assess/stream';
export const SKILLS_MGMT_ASSESS_FEED = '/api/skills-mgmt/assess/feed';
export const SKILLS_MGMT_ASSESS_RUN_ALL = '/api/skills-mgmt/assess/run-all';
export const SKILLS_MGMT_ASSESS_MERGE_BACKUPS = '/api/skills-mgmt/assess/merge-backups';
export const SKILLS_MGMT_ASSESS_MERGE_SAFE = '/api/skills-mgmt/assess/merge-safe';
export const SKILLS_MGMT_ASSESS_MERGE_UNDO = '/api/skills-mgmt/assess/merge-undo';
export const SKILLS_MGMT_ASSESS_CURATE = '/api/skills-mgmt/assess/curate';
export const skillAssess = (id: string) => `/api/skills-mgmt/assess/${encodeURIComponent(id)}`;
export const SKILLS_MGMT_CLASSES = '/api/skills-mgmt/classes';
export const SKILLS_MGMT_CLASSES_RUN_AUTO = '/api/skills-mgmt/classes/run-auto';
export const SKILLS_MGMT_CLASSES_MOVE = '/api/skills-mgmt/classes/move';
export const SKILLS_MGMT_QUEUE = '/api/skills-mgmt/queue';
export const SKILLS_MGMT_DUPLICATES = '/api/skills-mgmt/duplicates';
export const SKILLS_MGMT_REVIEW_AUDIT = '/api/skills-mgmt/review/audit';
export const SKILLS_MGMT_INSTALL_PREPRECHECK = '/api/skills-mgmt/install/precheck';
export const skillPublish = (id: string) => `/api/skills-mgmt/${encodeURIComponent(id)}/publish`;
export const skillSuggestFix = (id: string) => `/api/skills-mgmt/${encodeURIComponent(id)}/suggest-fix`;
export const skillFixAuto = (id: string) => `/api/skills-mgmt/${encodeURIComponent(id)}/fix-auto`;
export const skillRedraft = (id: string) => `/api/skills-mgmt/${encodeURIComponent(id)}/redraft`;
export const skillSlash = (id: string) => `/api/skills-mgmt/slash/${encodeURIComponent(id)}`;

// ===============================================================
//  技能检索（/api/skills，与 /api/skills-mgmt 是两套）
// ===============================================================
export const SKILLS = '/api/skills';
export const SKILLS_TOGGLE = '/api/skills/toggle';
export const SKILLS_DESCRIBE_AUTO = '/api/skills/describe/auto';
export const SKILLS_CLASSIFY_RUN_AUTO = '/api/skills/classify/run-auto';

// ===============================================================
//  系统提示词（/api/system-prompt）
// ===============================================================
export const SYSTEM_PROMPT_CONFIG = '/api/system-prompt/config';
export const SYSTEM_PROMPT_CONFIG_PREVIEW = '/api/system-prompt/config/preview';
export const SYSTEM_PROMPT_CONFIG_RESET = '/api/system-prompt/config/reset';
export const SYSTEM_PROMPT_CONFIG_APPLY = '/api/system-prompt/config/apply';

// ===============================================================
//  定时调度（/api/schedules）
// ===============================================================
export const SCHEDULES = '/api/schedules';
export const scheduleById = (id: string) => `/api/schedules/${encodeURIComponent(id)}`;
export const schedulePause = (id: string) => `/api/schedules/${encodeURIComponent(id)}/pause`;
export const scheduleResume = (id: string) => `/api/schedules/${encodeURIComponent(id)}/resume`;

// ===============================================================
//  人格（/api/personality）
// ===============================================================
export const PERSONALITY = '/api/personality';
export const PERSONALITY_PROFILE = '/api/personality/profile';
export const PERSONALITY_PARAMS = '/api/personality/params';
export const PERSONALITY_RESET = '/api/personality/reset';

// ===============================================================
//  网络配置（/api/network-config）
// ===============================================================
export const NETWORK_CONFIG = '/api/network-config';
export const NETWORK_CONFIG_RESET = '/api/network-config/reset';
export const APPLY_NETWORK_CONFIG = '/api/apply-network-config';

// ===============================================================
//  MCP 服务（/api/mcp）
// ===============================================================
export const MCP_SERVICES = '/api/mcp/services';
export const MCP_ENABLE = '/api/mcp/enable';
export const mcpServiceById = (id: string) => `/api/mcp/services/${encodeURIComponent(id)}`;

// ===============================================================
//  工作流学习 · 外部技能转换
// ===============================================================
export const WORKFLOW_LEARNING_BATCH_CONVERT = '/api/workflow-learning/batch-convert-external-skills';
export const WORKFLOW_LEARNING_CONVERT_EXTERNAL = '/api/workflow-learning/convert-external-skill';
export const workflowLearnConvertToSkill = (id: string) => `/api/workflow-learning/workflows/${encodeURIComponent(id)}/convert-to-skill`;
// ===============================================================
//  LLM 监控（/api/llm-monitor）
// ===============================================================
export const LLM_MONITOR_STATS = '/api/llm-monitor/stats';
export const LLM_MONITOR_CLEAR = '/api/llm-monitor/clear';
export const LLM_MONITOR_TOGGLE = '/api/llm-monitor/toggle';
export const LLM_MONITOR_RECORDS = '/api/llm-monitor/records';

// ===============================================================
//  记忆 / 向量检索（/api/memory、/api/vector）
// ===============================================================
export const MEMORY_MANUAL = '/api/memory/manual';
export const MEMORY_OVERVIEW = '/api/memory/overview';
export const VECTOR_SEARCH = '/api/vector/search';

// ===============================================================
//  健康 / 模块拓扑 / 状态 / 全景 / 传感器
// ===============================================================
export const HEALTH = '/api/health';
export const HEALTH_DASHBOARD = '/api/health/dashboard';
export const HEALTH_PROBE_TREND = '/api/health/probe-trend';
export const MODULES_TOPOLOGY = '/api/modules/topology';
export const STATUS = '/api/status';
export const PANORAMA = '/api/panorama';
export const SENSORS = '/api/sensors';

// ===============================================================
//  智能体线 / 工具（/api/agent-lines、/api/tools）
// ===============================================================
export const AGENT_LINES_PLANES = '/api/agent-lines/planes';
export const TOOLS_CONFIG = '/api/tools/config';
export const TOOLS_TOGGLE = '/api/tools/toggle';

// ===============================================================
//  回放 / 子智能体（/api/replay、/api/subagent）
// ===============================================================
export const REPLAY_UPLOAD = '/api/replay/upload';
export const SUBAGENT_LIST = '/api/subagent/list';
export const SUBAGENT_CREATE = '/api/subagent/create';
export const SUBAGENT_DELEGATE = '/api/subagent/delegate';
export const SUBAGENT_HISTORY = '/api/subagent/history';
export const subagentDelegateByName = (name: string) => `/api/subagent/${encodeURIComponent(name)}/delegate`;
export const subagentDestroyByName = (name: string) => `/api/subagent/${encodeURIComponent(name)}/destroy`;

// ===============================================================
//  插件 / 心跳 / 可观测 / 杂项
// ===============================================================
export const PLUGINS = '/api/plugins';
export const PLUGINS_RELOAD = '/api/plugins/reload';
// 路由↔UI 覆盖盘点（只读产物；后端 plugins/audit_coverage.py）
export const ROUTE_UI_COVERAGE = '/api/audit/route-ui-coverage';
export const HEARTBEAT = '/api/heartbeat';
export const HEARTBEAT_HISTORY = '/api/heartbeat/history';
export const DIAGNOSTICS_METRICS = '/api/diagnostics/metrics';
export const DIAGNOSTICS_LLM_CHECK = '/api/diagnostics/llm-check';
export const OBSERVABILITY_ALERTS = '/api/observability/alerts';
export const CHAT = '/api/chat';
export const SKILLS_MGMT_SLASH_COMMANDS = '/api/skills-mgmt/slash-commands';
export const USER_LIST = '/api/user/list';
export const PROCESS_LIST = '/api/process/list';
export const BROWSER_NAVIGATE = '/api/browser/navigate';

// ===============================================================
//  资产管理（/api/assets/*）
// ===============================================================
// 【为什么这批要单独补】该页原先在文件内联写死 6 处 `/api` 字面量，
// 而 `contract_diff` 的 stray 计数**漏掉了它**：收集器里有一条
// `"/assets/" in rel` 过滤，本意是排除 `static/assets/` 下的构建产物，
// 却把 `src/pages/hub/assets/` 这个**真实源码目录**一并吞了。
// 后果不是"少几处计数"，而是**这条路径完全逃出对拍** —— 实测它因此藏住了
// 一个真实缺陷：前端用 `POST /api/assets/<cat>/<id>/delete`，
// 而后端注册的是 `DELETE /api/assets/<cat>/<id>`（前端那条路径根本不存在，必 404）。
// 同批修好的还有「备份」按钮：`hubPost` 对空 body **刻意不设 Content-Type**
// （见 components/ui.tsx 的注释），而后端 `api_assets_backup` 直接 `get_json() or {}`
// ⇒ 抛 415 被 `except Exception` 吞成 **500**。故本页的写操作显式传 `{}` 作 body。
export const ASSETS_OVERVIEW = '/api/assets/overview';
export const ASSETS_BACKUP = '/api/assets/backup';
export const ASSETS_BACKUP_LIST = '/api/assets/backup/list';
// 动态段按常量层纪律用**函数**，不散落模板串（文件头「纪律 ②」）。
export const assetsByCategory = (category: string) => `/api/assets/${encodeURIComponent(category)}`;
// 【删除的真实契约是 DELETE + 纯路径】原前端写的是 `.../delete` 后缀 + POST，
// 与后端 `@app.route("/api/assets/<category>/<item_id>", methods=["DELETE"])` 不符。
export const assetById = (category: string, itemId: string) =>
  `/api/assets/${encodeURIComponent(category)}/${encodeURIComponent(itemId)}`;
