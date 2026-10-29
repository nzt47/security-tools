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
/** 文件轨 → 资产主轨补登记（dry_run 默认 true；只补主轨登记，不改 skills_repo） */
export const SKILLS_MGMT_BACKFILL = '/api/skills-mgmt/backfill';
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

// 技能详情抽屉（详情页按需加载的运行/反馈面；与上面「评审-版本」族分开命名，
// 便于在 endpoints.ts 里一眼看出它们是「点开某个技能才用」的接口）
export const skillInstruction = (skillId: string) => `/api/skills-mgmt/${encodeURIComponent(skillId)}/instruction`;
export const skillScripts = (skillId: string) => `/api/skills-mgmt/${encodeURIComponent(skillId)}/scripts`;
export const skillExecute = (skillId: string) => `/api/skills-mgmt/${encodeURIComponent(skillId)}/execute`;
export const skillParamStats = (skillId: string) => `/api/skills-mgmt/${encodeURIComponent(skillId)}/param-stats`;
export const skillFeedback = (skillId: string) => `/api/skills-mgmt/${encodeURIComponent(skillId)}/feedback`;
export const skillOptimizeWithFeedback = (skillId: string) => `/api/skills-mgmt/${encodeURIComponent(skillId)}/optimize-with-feedback`;
export const skillDuplicates = (skillId: string) => `/api/skills-mgmt/${encodeURIComponent(skillId)}/duplicates`;

// 技能治理端点（「技能中心 · 维护」Tab 已于 2026-10-22 退役：清理/合并归「吞噬 · 存量治理」，
// 草稿队列/放行归「吞噬 · 草稿评审」；此处保留常量供上述两个入口与旧链接调用）
export const SKILLS_MGMT_THREE_LAYER_SUMMARY = '/api/skills-mgmt/three-layer/summary';
export const SKILLS_MGMT_CLEANUP_REPORT = '/api/skills-mgmt/cleanup/report';
export const SKILLS_MGMT_CLEANUP_ORPHANS = '/api/skills-mgmt/cleanup/orphans';
export const SKILLS_MGMT_CLEANUP_UNUSED = '/api/skills-mgmt/cleanup/unused';
export const SKILLS_MGMT_AUTO_MERGE = '/api/skills-mgmt/auto-merge';
export const SKILLS_MGMT_MERGE = '/api/skills-mgmt/merge';
export const SKILLS_MGMT_UPLOAD = '/api/skills-mgmt/upload';
/** Claude 技能包导入：GET=扫描 `~/.claude/skills`（只读预览），POST=转换并落库 */
export const SKILLS_MGMT_IMPORT_CLAUDE = '/api/skills-mgmt/import/claude';
export const SKILLS_MGMT_ABSTRACT_FROM_MEMORY = '/api/skills-mgmt/abstract-from-memory';

// ═══════════════════════════════════════════════════════════════
//  扩展市场（/api/extensions）
// ═══════════════════════════════════════════════════════════════
export const EXTENSIONS_LIST = '/api/extensions/list';
export const EXTENSIONS_INSTALLED = '/api/extensions/installed';
export const EXTENSIONS_INSTALL = '/api/extensions/install';
export const EXTENSIONS_UNINSTALL = '/api/extensions/uninstall';
export const EXTENSIONS_TOGGLE = '/api/extensions/toggle';
export const EXTENSIONS_CONFIGURE = '/api/extensions/configure';
export const EXTENSIONS_DISCOVER = '/api/extensions/discover';
export const EXTENSIONS_MARKET_SEARCH = '/api/extensions/market/search';
export const EXTENSIONS_MARKET_RECOMMEND = '/api/extensions/market/recommend';
export const EXTENSIONS_MARKET_REFRESH = '/api/extensions/market/refresh';
export const EXTENSIONS_CHANNELS_SEND = '/api/extensions/channels/send';

// ═══════════════════════════════════════════════════════════════
//  工作流学习（/api/workflow-learning）
// ═══════════════════════════════════════════════════════════════
// ═══════════════════════════════════════════════════════════════
//  日志中心（独立 /logs 日志应用；不受 /api 前缀约束）
// ═══════════════════════════════════════════════════════════════
export const LOGS_DASHBOARD = '/logs/dashboard';
export const LOGS_API_STATS = '/logs/api/stats';
export const LOGS_API_TRENDS = '/logs/api/trends';
export const LOGS_API_ERRORS = '/logs/api/errors';
export const LOGS_API_INSIGHTS = '/logs/api/insights';
export const LOGS_API_KNOWLEDGE = '/logs/api/knowledge';
export const LOGS_API_ACTIONS = '/logs/api/actions';
export const LOGS_API_QUERY = '/logs/api/query';
export const LOGS_API_INTROSPECTION_STATUS = '/logs/api/introspection/status';
export const LOGS_API_INTROSPECTION_RUN = '/logs/api/introspection/run';

export const WORKFLOW_LEARNING_HEALTH = '/api/workflow-learning/health';
export const WORKFLOW_LEARNING_LEARN = '/api/workflow-learning/learn';
export const WORKFLOW_LEARNING_MATCH = '/api/workflow-learning/match';
export const WORKFLOW_LEARNING_TRY_EXECUTE = '/api/workflow-learning/try-execute';
// 可转换成 Skill 的工作流（满足质量门控且未转换过）
export const WORKFLOW_LEARNING_CONVERTIBLE = '/api/workflow-learning/convertible';
export const WORKFLOW_LEARNING_WORKFLOWS = '/api/workflow-learning/workflows';

/** 吞噬生成的骨架草稿（tool/workflow；隔离区，不进技能队列） */
export const INGESTION_DRAFTS = '/api/ingestion/drafts';
export const ingestionDraft = (draftId: string) => `/api/ingestion/drafts/${encodeURIComponent(draftId)}`;
export const ingestionDraftInstallSkill = (draftId: string) =>
  `/api/ingestion/drafts/${encodeURIComponent(draftId)}/install-skill`;
export const ingestionDraftAutobind = (draftId: string) =>
  `/api/ingestion/drafts/${encodeURIComponent(draftId)}/autobind`;
export const ingestionDraftRegister = (draftId: string) =>
  `/api/ingestion/drafts/${encodeURIComponent(draftId)}/register`;
export const ingestionDraftImplement = (draftId: string) =>
  `/api/ingestion/drafts/${encodeURIComponent(draftId)}/implement`;
export const ingestionDraftRegisterTool = (draftId: string) =>
  `/api/ingestion/drafts/${encodeURIComponent(draftId)}/register-tool`;

export const workflowLearnExecute = (wfId: string) => `/api/workflow-learning/execute/${encodeURIComponent(wfId)}`;
export const workflowLearnWorkflows = (wfId: string) => `/api/workflow-learning/workflows/${encodeURIComponent(wfId)}`;
export const workflowLearnToggle = (wfId: string) => `/api/workflow-learning/workflows/${encodeURIComponent(wfId)}/toggle`;
export const workflowLearnPriority = (wfId: string) => `/api/workflow-learning/workflows/${encodeURIComponent(wfId)}/priority`;

// ═══════════════════════════════════════════════════════════════
//  会话 / 工作区（/api/sessions*）
// ═══════════════════════════════════════════════════════════════
export const SESSIONS = '/api/sessions';
// 会话交接文档（LLM 压缩成 Markdown + 脱敏，落盘临时目录）
export const HANDOFF = '/api/handoff';
export const SESSIONS_CURRENT = '/api/sessions/current';
export const SESSION_GROUPS = '/api/session-groups';
export const WORKSPACES = '/api/workspaces';
/** 项目（工作空间归属容器；GET 列表 / POST 新建） */
export const PROJECTS = '/api/projects';

export const sessionById = (id: string) => `/api/sessions/${encodeURIComponent(id)}`;
export const sessionRename = (id: string) => `/api/sessions/${encodeURIComponent(id)}/rename`;
export const sessionMessages = (id: string) => `/api/sessions/${encodeURIComponent(id)}/messages`;
export const sessionWorkspace = (id: string) => `/api/sessions/${encodeURIComponent(id)}/workspace`;
export const sessionWorkspaceReveal = (id: string) => `/api/sessions/${encodeURIComponent(id)}/workspace/reveal`;
export const sessionWorkspaceRoot = (id: string) => `/api/sessions/${encodeURIComponent(id)}/workspace-root`;
export const sessionGroupSet = (sessionId: string) => `/api/sessions/${encodeURIComponent(sessionId)}/group`;
export const sessionGroupById = (id: string) => `/api/session-groups/${encodeURIComponent(id)}`;
/** 单个项目：GET 详情 / PUT 更新 / DELETE 删除（同一路径） */
export const projectById = (id: string) => `/api/projects/${encodeURIComponent(id)}`;
/** 会话归属的项目（PUT 设置 / 传 null 解除归属） */
export const sessionProject = (id: string) => `/api/sessions/${encodeURIComponent(id)}/project`;
/** 会话使用的分身（PUT 设置 / 传 null 回到母体） */
export const sessionAgent = (id: string) => `/api/sessions/${encodeURIComponent(id)}/agent`;

// ═══════════════════════════════════════════════════════════════
//  知识库（/api/knowledge）
// ═══════════════════════════════════════════════════════════════
// 新增知识文档（legacy 记忆/向量域端点；content 必填）
export const KNOWLEDGE_ADD = '/api/knowledge/add';
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
/** 一次性审批链接（会话绑定 + record 绑定；对话内联审批弹窗用） */
export const APPROVAL_LINK = '/api/approval/link';
/** destructive 审批的二次认证确认码（同会话内自动取用） */
export const APPROVAL_SECOND_FACTOR = '/api/approval/second-factor';
/** 单条裁决：POST /approve 或 /reject */
export const approvalDecision = (recordId: string, approve: boolean) =>
  `/api/approval/${encodeURIComponent(recordId)}/${approve ? 'approve' : 'reject'}`;
export const CAPABILITY_MANIFEST = '/api/capability-manifest';
export const AGENT_LINES = '/api/agent-lines';
export const AGENT_LINES_PREVIEW = '/api/agent-lines/preview';
export const AGENT_LINES_VALIDATE = '/api/agent-lines/validate';
/** 单条主线：GET 详情 / PUT 保存 / DELETE 删除（同一路径；删除另需 confirm） */
export const agentLineById = (lineId: string) =>
  `/api/agent-lines/${encodeURIComponent(lineId)}`;

// ═══════════════════════════════════════════════════════════════
//  控制平面（/api/cp）—— cpPanelsApi 与 toolExemptionsApi 此前各写一份 PREFIX，
//  口径不一，现收口于此。
// ═══════════════════════════════════════════════════════════════
export const CP = '/api/cp';
export const CP_TOOL_EXEMPTIONS = '/api/cp/tool-exemptions';
/** 开关中心：一次拿全量登记表（items 里带 value/editable/locked_reason/needs_restart） */
export const CP_SETTINGS = '/api/cp/settings';
/** 改单个开关（A 级直接生效；B 级返回 202 待第二位人工确认） */
export const cpSetting = (key: string) => `/api/cp/settings/${encodeURIComponent(key)}`;
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
// 批量放行（显式 ids；force 豁免必须带原因 —— 服务端二次校验，见 publish_batch）
export const SKILLS_MGMT_QUEUE_PUBLISH_BATCH = '/api/skills-mgmt/queue/publish-batch';
export const SKILLS_MGMT_DUPLICATES = '/api/skills-mgmt/duplicates';
export const SKILLS_MGMT_REVIEW_AUDIT = '/api/skills-mgmt/review/audit';
export const SKILLS_MGMT_INSTALL_PREPRECHECK = '/api/skills-mgmt/install/precheck';
export const skillPublish = (id: string) => `/api/skills-mgmt/${encodeURIComponent(id)}/publish`;
export const skillSuggestFix = (id: string) => `/api/skills-mgmt/${encodeURIComponent(id)}/suggest-fix`;
export const skillFixAuto = (id: string) => `/api/skills-mgmt/${encodeURIComponent(id)}/fix-auto`;
export const skillRedraft = (id: string) => `/api/skills-mgmt/${encodeURIComponent(id)}/redraft`;
// 再定义落地：写 skill.md 的 description_zh/name（PATCH 白名单不含这两个字段）
export const skillRedraftApply = (id: string) => `/api/skills-mgmt/${encodeURIComponent(id)}/redraft/apply`;
// 给「只在主轨」的技能**新建**文件轨（skill.md）：dry_run 预览 → dry_run=false 落盘
export const skillMaterialize = (id: string) => `/api/skills-mgmt/${encodeURIComponent(id)}/materialize`;
export const skillSlash = (id: string) => `/api/skills-mgmt/slash/${encodeURIComponent(id)}`;

// ===============================================================
//  技能检索（/api/skills，与 /api/skills-mgmt 是两套）
// ===============================================================
export const SKILLS = '/api/skills';
export const SKILLS_TOGGLE = '/api/skills/toggle';
export const SKILLS_DESCRIBE_AUTO = '/api/skills/describe/auto';
export const SKILLS_CLASSIFY_RUN_AUTO = '/api/skills/classify/run-auto';

// ===============================================================
//  功能单位监控 / 技能使用台账（只读）
// ===============================================================
export const SYSTEM_UNITS = '/api/system/units';
export const SKILLS_MGMT_USAGE_STATS = '/api/skills-mgmt/usage/stats';

// ===============================================================
//  系统提示词（/api/system-prompt）
// ===============================================================
export const SYSTEM_PROMPT_CONFIG = '/api/system-prompt/config';
export const SYSTEM_PROMPT_CONFIG_PREVIEW = '/api/system-prompt/config/preview';
export const SYSTEM_PROMPT_CONFIG_RESET = '/api/system-prompt/config/reset';
export const SYSTEM_PROMPT_CONFIG_APPLY = '/api/system-prompt/config/apply';

// ═══════════════════════════════════════════════════════════════
//  提示词实验室 · 真实输出代理（/api/prompt-lab）
// ═══════════════════════════════════════════════════════════════
/** 用云枢已配置的 LLM 生成预览文本（前端不再直连外部 Endpoint） */
export const PROMPT_LAB_LLM_PREVIEW = '/api/prompt-lab/llm-preview';

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
// 配置变更日志（读 NetworkConfig.change_log；供 系统管理·日志 页「配置变更日志」区）
export const CONFIG_CHANGE_LOGS = '/api/config/logs';

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
/** 按 id 删除一条长期记忆（默认 dry-run；confirm=true 才真删）。动态段统一在此编码。 */
export const memoryMessage = (msgId: string) => `/api/memory/messages/${encodeURIComponent(msgId)}`;
export const VECTOR_SEARCH = '/api/vector/search';
/** 向量库统计（count / total_memories / backend；尚未迁到统一信封） */
export const VECTOR_STATS = '/api/vector/stats';
// 记忆运维（审查 / 压缩 / 长期摘要 / 窗口监控 / 向量维护）
export const MEMORY_REVIEW = '/api/memory/review';
export const MEMORY_COMPRESS = '/api/memory/compress';
export const MEMORY_SUMMARY = '/api/memory/summary';
export const MEMORY_CLEAR_SUMMARY = '/api/memory/clear-summary';
export const MEMORY_WINDOWS_CURRENT = '/api/memory/windows/current';
export const MEMORY_WINDOWS_STATS = '/api/memory/windows/stats';
export const MEMORY_WINDOWS_EVENTS = '/api/memory/windows/events';
export const MEMORY_WINDOWS_CONFIG = '/api/memory/windows/config';
export const MEMORY_WINDOWS_CLEAR = '/api/memory/windows/clear';
export const VECTOR_ADD = '/api/vector/add';
export const VECTOR_BATCH_ADD = '/api/vector/batch_add';
export const VECTOR_CLEAR = '/api/vector/clear';
export const VECTOR_RECENT = '/api/vector/recent';
// 外部记忆导入（记忆管理页「导入外部记忆」；commit 默认 dry-run，confirm=true 才真写）
export const MEMORY_IMPORT_SOURCES = '/api/memory-import/sources';
export const MEMORY_IMPORT_PREVIEW = '/api/memory-import/preview';
export const MEMORY_IMPORT_COMMIT = '/api/memory-import/commit';
export const MEMORY_IMPORT_BATCHES = '/api/memory-import/batches';
export const MEMORY_IMPORT_ROLLBACK = '/api/memory-import/rollback';
export const vectorItem = (itemId: string) => `/api/vector/item/${encodeURIComponent(itemId)}`;

// ===============================================================
//  健康 / 模块拓扑 / 状态 / 全景 / 传感器
// ===============================================================
export const HEALTH = '/api/health';
export const HEALTH_DASHBOARD = '/api/health/dashboard';
export const HEALTH_PROBE_TREND = '/api/health/probe-trend';
export const MODULES_TOPOLOGY = '/api/modules/topology';
// 模块节点下钻与干预（拓扑面板）
export const modulesDetail = (id: string) => `/api/modules/${encodeURIComponent(id)}/detail`;
export const MODULES_ACTIONS = (id: string) => `/api/modules/${encodeURIComponent(id)}/actions`;
export const STATUS = '/api/status';
// 运行模式（label / description / can_accept_tasks）
export const MODE = '/api/mode';
// 学习有效性度量（评估样本 + KPI）
export const LEARNING_METRICS = '/api/learning/metrics';
export const PANORAMA = '/api/panorama';
export const SENSORS = '/api/sensors';

// 健康体检（评分 / 趋势 / 历史 / 告警 / 权重 / 快速检查 / 导出）
export const HEALTH_SCORE = '/api/health/score';
export const HEALTH_SUMMARY = '/api/health/summary';
export const HEALTH_TREND = '/api/health/trend';
export const HEALTH_HISTORY = '/api/health/history';
export const HEALTH_ALERTS = '/api/health/alerts';
export const HEALTH_QUICK_CHECK = '/api/health/quick-check';
export const HEALTH_WEIGHTS = '/api/health/weights';
export const HEALTH_EXPORT = '/api/health/export';

// 诊断
export const DIAGNOSTICS_CONFIG = '/api/diagnostics/config';
export const DIAGNOSTICS_HEALTH = '/api/diagnostics/health';
export const DIAGNOSTICS_LOGS = '/api/diagnostics/logs';
export const DIAGNOSTICS_TOOLS = '/api/diagnostics/tools';
export const DIAGNOSTICS_ERROR_CORRELATION = '/api/diagnostics/error_correlation';

// 可观测（追踪 / 访问日志 / 状态 / 告警规则）
export const OBSERVABILITY_STATE = '/api/observability/state';
export const OBSERVABILITY_TRACES = '/api/observability/traces';
export const OBSERVABILITY_ACCESS_LOGS = '/api/observability/access_logs';
export const OBSERVABILITY_ACCESS_STATS = '/api/observability/access_stats';

// ═══════════════════════════════════════════════════════════════
//  安全与权限 / 本地资源（文件 · 工作区 · 进程）
// ═══════════════════════════════════════════════════════════════
export const PERMISSION_STATUS = '/api/permission/status';
export const PERMISSION_STATS = '/api/permission/stats';
export const PERMISSION_LOG = '/api/permission/log';
export const PERMISSION_ACCESS_LOG = '/api/permission/access-log';
export const PERMISSION_TOGGLE = '/api/permission/toggle';
export const PERMISSION_EMERGENCY = '/api/permission/emergency';
export const SAFETY_ALERTS = '/api/safety/alerts';
export const SAFETY_CHECK = '/api/safety/check';
export const SAFETY_KEYWORDS = '/api/safety/keywords';
export const PRIVACY_INFO = '/api/privacy/info';
export const WINDOW_CONSENT = '/api/window/consent';

export const FILESYSTEM_LIST = '/api/filesystem/list';
export const FILESYSTEM_INFO = '/api/filesystem/info';
export const FILESYSTEM_SEARCH = '/api/filesystem/search';
export const FILESYSTEM_READ = '/api/filesystem/read';
export const FILESYSTEM_WRITE = '/api/filesystem/write';
/** 工作区根（注意与 /api/workspaces 不是同一个面） */
export const WORKSPACE_ROOT = '/api/workspace';
export const WORKSPACE_INFO = '/api/workspace/info';
export const WORKSPACE_WRITE = '/api/workspace/write';
export const WORKSPACE_DELETE = '/api/workspace/delete';
export const PROCESS_WHITELIST = '/api/process/whitelist';
export const PROCESS_WHITELIST_ADD = '/api/process/whitelist/add';
export const PROCESS_WHITELIST_REMOVE = '/api/process/whitelist/remove';
export const PROCESS_START = '/api/process/start';
export const PROCESS_STOP = '/api/process/stop';
export const observabilityTrace = (traceId: string) => `/api/observability/traces/${encodeURIComponent(traceId)}`;
export const observabilityAlert = (name: string) => `/api/observability/alerts/${encodeURIComponent(name)}`;

// ═══════════════════════════════════════════════════════════════
//  反馈中心 / 业务看板 / 系统仪表盘 / 实例管理
// ═══════════════════════════════════════════════════════════════
export const FEEDBACK_LIST = '/api/feedback/list';
export const FEEDBACK_SUMMARY = '/api/feedback/summary';
export const FEEDBACK_REPORT = '/api/feedback/report';
export const FEEDBACK_QUALITY_CASES = '/api/feedback/quality-cases';
export const FEEDBACK_SUBMIT = '/api/feedback/submit';
export const feedbackById = (id: string) => `/api/feedback/${encodeURIComponent(id)}`;
export const feedbackResolve = (id: string) => `/api/feedback/${encodeURIComponent(id)}/resolve`;

export const BUSINESS_DASHBOARD = '/api/business/dashboard';
export const BUSINESS_DEFINITIONS = '/api/business/definitions';
export const BUSINESS_HEALTH = '/api/business/health';
export const BUSINESS_PROMETHEUS = '/api/business/prometheus';
export const businessMetric = (name: string) => `/api/business/metrics/${encodeURIComponent(name)}`;

export const DASHBOARD_HEALTH = '/api/dashboard/health';
export const DASHBOARD_MEMORY = '/api/dashboard/memory';
export const DASHBOARD_QUALITY = '/api/dashboard/quality';
export const DASHBOARD_TRACES = '/api/dashboard/traces';
export const dashboardTrace = (id: string) => `/api/dashboard/traces/${encodeURIComponent(id)}`;

export const LLM_INSTANCES = '/api/llm/instances';
export const llmInstance = (id: string) => `/api/llm/instances/${encodeURIComponent(id)}`;
export const llmInstanceDefault = (id: string) => `/api/llm/instances/${encodeURIComponent(id)}/default`;
export const llmInstanceTest = (id: string) => `/api/llm/instances/${encodeURIComponent(id)}/test`;
export const SEARCH_INSTANCES = '/api/search/instances';
export const searchInstance = (id: string) => `/api/search/instances/${encodeURIComponent(id)}`;
export const searchInstanceDefault = (id: string) => `/api/search/instances/${encodeURIComponent(id)}/default`;
export const searchInstanceTest = (id: string) => `/api/search/instances/${encodeURIComponent(id)}/test`;

// ===============================================================
//  智能体线 / 工具（/api/agent-lines、/api/tools）
// ===============================================================
export const AGENT_LINES_PLANES = '/api/agent-lines/planes';
export const TOOLS_CONFIG = '/api/tools/config';
export const TOOLS_TOGGLE = '/api/tools/toggle';
// 工具治理（分类 / 健康 / 路由关键词 / 启用总览）
export const TOOLS_CATEGORIES = '/api/tools/categories';
export const TOOLS_HEALTH = '/api/tools/health';
export const TOOLS_KEYWORDS = '/api/tools/keywords';
export const TOOLS_KEYWORDS_UPDATE = '/api/tools/keywords/update';
export const TOOLS_KEYWORDS_RESET = '/api/tools/keywords/reset';
export const TOOLS_STATUS_BATCH = '/api/tools/status-batch';

// 搜索性能监控
export const SEARCH_PERF_STATUS = '/api/search-performance/status';
export const SEARCH_PERF_SUMMARY = '/api/search-performance/summary';
export const SEARCH_PERF_HISTORY = '/api/search-performance/history';
export const SEARCH_PERF_CHECK = '/api/search-performance/check';
export const SEARCH_PERF_START = '/api/search-performance/start';
export const SEARCH_PERF_STOP = '/api/search-performance/stop';

// 系统提示词（模板读写 / 重置 / 同步状态）
export const SYSTEM_PROMPT = '/api/system-prompt';
export const SYSTEM_PROMPT_RESET = '/api/system-prompt/reset';
export const SYSTEM_PROMPT_SYNC_STATUS = '/api/system-prompt/sync-status';

// 后台任务与异步任务（引擎 · 任务队列）
export const BACKGROUND_TASKS = '/api/background/tasks';
export const backgroundTask = (id: string) => `/api/background/tasks/${encodeURIComponent(id)}`;
export const backgroundTaskCancel = (id: string) => `/api/background/tasks/${encodeURIComponent(id)}/cancel`;
export const backgroundTaskResult = (id: string) => `/api/background/tasks/${encodeURIComponent(id)}/result`;
export const ASYNC_TASKS = '/api/tasks';
export const asyncTask = (id: string) => `/api/tasks/${encodeURIComponent(id)}`;
export const asyncTaskCancel = (id: string) => `/api/tasks/${encodeURIComponent(id)}/cancel`;

// ===============================================================
//  回放 / 子智能体（/api/replay、/api/subagent）
// ===============================================================
export const REPLAY_UPLOAD = '/api/replay/upload';
// 回放中心（列表 / 元数据 / 数据 / 统计 / 清理）
export const REPLAY_LIST = '/api/replay/list';
export const REPLAY_STATS = '/api/replay/stats';
export const REPLAY_CLEANUP = '/api/replay/cleanup';
export const replayById = (id: string) => `/api/replay/${encodeURIComponent(id)}`;
export const replayData = (id: string) => `/api/replay/${encodeURIComponent(id)}/data`;
// 资产备份（删除 / 恢复 / 导出）
export const ASSETS_EXPORT = '/api/assets/export';
export const ASSETS_RESTORE = '/api/assets/restore';
export const assetBackupById = (id: string) => `/api/assets/backup/${encodeURIComponent(id)}`;
// 网络配置（导出 / 导入）
export const NETWORK_CONFIG_EXPORT = '/api/network-config/export';
export const NETWORK_CONFIG_IMPORT = '/api/network-config/import';
export const SUBAGENT_LIST = '/api/subagent/list';
export const SUBAGENT_CREATE = '/api/subagent/create';
export const SUBAGENT_DELEGATE = '/api/subagent/delegate';
export const SUBAGENT_HISTORY = '/api/subagent/history';
// 主权分身能力投影（六层二十面三态 + 证据）：组装台「主权清单」的数据源
export const SUBAGENT_CAPABILITIES = '/api/subagent/capabilities';
// 分身行操作：详情 / 热更新 / 离线包（执行是占位骨架，故不接）
export const subagentByName = (name: string) => `/api/subagent/${encodeURIComponent(name)}`;
export const subagentReload = (name: string) => `/api/subagent/${encodeURIComponent(name)}/reload`;
export const subagentOfflinePack = (name: string) => `/api/subagent/${encodeURIComponent(name)}/offline_pack`;
export const subagentDelegateByName = (name: string) => `/api/subagent/${encodeURIComponent(name)}/delegate`;
export const subagentDestroyByName = (name: string) => `/api/subagent/${encodeURIComponent(name)}/destroy`;
// 可带走 bundle（S5）：GET 导出一个分身的包 / POST 从包导入（见 agent/subagent/bundle.py）
export const SUBAGENT_IMPORT = '/api/subagent/import';
export const SUBAGENT_BUNDLE_BY_NAME = (name: string) => `/api/subagent/${encodeURIComponent(name)}/bundle`;

// ===============================================================
//  插件 / 心跳 / 可观测 / 杂项
// ===============================================================
export const PLUGINS = '/api/plugins';
export const PLUGINS_RELOAD = '/api/plugins/reload';
// 路由↔UI 覆盖盘点（只读产物；后端 plugins/audit_coverage.py）
export const ROUTE_UI_COVERAGE = '/api/audit/route-ui-coverage';
export const HEARTBEAT = '/api/heartbeat';
export const HEARTBEAT_HISTORY = '/api/heartbeat/history';
// 心跳概览（total_checks / healthy_checks / latest）——健康仪表盘状态条与「检查次数」用
export const HEARTBEAT_STATUS = '/api/heartbeat/status';
// 定时任务：任务列表与执行历史（健康仪表盘「系统健康·心跳检测」卡的 legacy 口径）
export const SCHEDULER_TASKS = '/api/scheduler/tasks';
export const SCHEDULER_HISTORY = '/api/scheduler/history';
// 调度器**运行态**全量任务（含 cron/interval 的 python 任务；旧 tasks 只列文件型命令任务）
export const SCHEDULER_OVERVIEW = '/api/scheduler/overview';
// 采集器运行期操作（id 基）：立即执行 / 启停（与 overview 同一族：agent.task_scheduler）
export const SCHEDULER_TOGGLE = '/api/scheduler/toggle';
export const SCHEDULER_EXECUTE_NOW = '/api/scheduler/execute-now';
// 另一族调度器（agent.scheduling）的执行历史 —— 与上面的 task_scheduler 不是同一个调度器
export const SCHEDULES_HISTORY = '/api/schedules/history';
// 编排与蒸馏
export const ORCHESTRATOR_SEMANTIC_CONFIG = '/api/orchestrator/semantic-config';
export const PLANNING_TOGGLE = '/api/planning/toggle';
export const PROCESS_DISTILL = '/api/process-distill/distill';
export const PROCESS_DISTILL_HEALTH = '/api/process-distill/health';
// 按任务名启停 / 改「每天 时:分」（改动即时生效并落 data/scheduler_overrides.json）
export const SCHEDULER_CONFIGURE = '/api/scheduler/configure';
export const DIAGNOSTICS_METRICS = '/api/diagnostics/metrics';
export const DIAGNOSTICS_LLM_CHECK = '/api/diagnostics/llm-check';
export const OBSERVABILITY_ALERTS = '/api/observability/alerts';
export const CHAT = '/api/chat';
export const SKILLS_MGMT_SLASH_COMMANDS = '/api/skills-mgmt/slash-commands';
export const USER_LIST = '/api/user/list';
export const PROCESS_LIST = '/api/process/list';
export const BROWSER_NAVIGATE = '/api/browser/navigate';
// 浏览器截图 / 关闭 + 剪贴板（方案 §7 分批接入：browser/screenshot、browser/close、clipboard）
export const BROWSER_SCREENSHOT = '/api/browser/screenshot';
export const BROWSER_CLOSE = '/api/browser/close';
export const CLIPBOARD = '/api/clipboard';
// 认知闭环（元认知）状态 / 任务接受度裁决（方案 §7 第三批接入）
export const COGNITIVE_STATUS = '/api/cognitive/status';
export const COGNITIVE_REJECT = '/api/cognitive/reject';
// 吞噬（P5）：外来能力获取的唯一入口。常量层是 React 侧唯一允许出现 /api 字面量的地方。
export const INGESTION_PREVIEW = '/api/ingestion/preview';
export const INGESTION_COMMIT = '/api/ingestion/commit';
export const INGESTION_SOURCES = '/api/ingestion/sources';
// P6 存量治理（scan/plan/apply）：同样只在常量层出现字面量
export const INGESTION_GOVERNANCE_SCAN = '/api/ingestion/governance/scan';
export const INGESTION_GOVERNANCE_PLAN = '/api/ingestion/governance/plan';
export const INGESTION_GOVERNANCE_APPLY = '/api/ingestion/governance/apply';

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
