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
 *      写 `\`/api/skills-mgmt/\${id}\``。函数内部仍是完整模板串，对拍照样看得见。
 *
 * 【与 contract_diff 的关系】该工具的 `frontend_literals` 计数**排除本文件**
 * （本文件就是被许可的常量层），但**不排除**它的路径参与"是否命中真实后端路由"的校验 ——
 * 计数与正确性分开：计数要的是"散落的还有多少"，正确性要的是"每一条都真实存在"。
 *
 * 【本轮迁移范围】先做**客户端层**（src/lib/** + src/api/**，45 处 / 11 文件），
 * 这是审计给出的灰度顺序（"先 lib/apiClient 消费者，后 api/*"，页面层随后按模块跟进）。
 *
 * 【迁移记录】2026-10-03 · 阶段 5 / R5 第一批。行为**逐字不变**：
 * 本轮只做"字面量 → 常量/构造函数"的等价替换，**不加** encodeURIComponent 等
 * 语义改动（那会改变含特殊字符 id 的 URL，属另一件事，需单独评估）。
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
export const skillById = (skillId: string) => `/api/skills-mgmt/${skillId}`;
export const skillReview = (skillId: string) => `/api/skills-mgmt/${skillId}/review`;
export const skillToggle = (skillId: string) => `/api/skills-mgmt/${skillId}/toggle`;
export const skillOptimize = (skillId: string) => `/api/skills-mgmt/${skillId}/optimize`;
export const skillExecution = (skillId: string) => `/api/skills-mgmt/${skillId}/execution`;
export const skillVersions = (skillId: string) => `/api/skills-mgmt/${skillId}/versions`;
export const skillVersionsBump = (skillId: string) => `/api/skills-mgmt/${skillId}/versions/bump`;
export const skillVersionsRollback = (skillId: string) => `/api/skills-mgmt/${skillId}/versions/rollback`;

// ═══════════════════════════════════════════════════════════════
//  工作流学习（/api/workflow-learning）
// ═══════════════════════════════════════════════════════════════
export const WORKFLOW_LEARNING_HEALTH = '/api/workflow-learning/health';
export const WORKFLOW_LEARNING_LEARN = '/api/workflow-learning/learn';
export const WORKFLOW_LEARNING_MATCH = '/api/workflow-learning/match';
export const WORKFLOW_LEARNING_TRY_EXECUTE = '/api/workflow-learning/try-execute';

export const workflowLearnExecute = (wfId: string) => `/api/workflow-learning/execute/${wfId}`;
export const workflowLearnWorkflows = (wfId: string) => `/api/workflow-learning/workflows/${wfId}`;
export const workflowLearnToggle = (wfId: string) => `/api/workflow-learning/workflows/${wfId}/toggle`;
export const workflowLearnPriority = (wfId: string) => `/api/workflow-learning/workflows/${wfId}/priority`;
/** 列表（可带 enabled_only 过滤）；查询串由调用方拼，保持与迁移前逐字一致。 */
export const WORKFLOW_LEARNING_WORKFLOWS = '/api/workflow-learning/workflows';

// ═══════════════════════════════════════════════════════════════
//  会话 / 工作区（/api/sessions*）
// ═══════════════════════════════════════════════════════════════
export const SESSIONS = '/api/sessions';
export const SESSIONS_CURRENT = '/api/sessions/current';
export const SESSION_GROUPS = '/api/session-groups';
export const WORKSPACES = '/api/workspaces';
// ═══════════════════════════════════════════════════════════════
//  知识库（/api/knowledge）
// ═══════════════════════════════════════════════════════════════
export const KNOWLEDGE_CARDS = '/api/knowledge/cards';
export const KNOWLEDGE_GRAPH = '/api/knowledge/graph';
export const KNOWLEDGE_INDEX = '/api/knowledge/index';
export const KNOWLEDGE_LINT = '/api/knowledge/lint';
export const KNOWLEDGE_QUERY = '/api/knowledge/query';

// ═══════════════════════════════════════════════════════════════
//  上下文监控（/api/context）
// ═══════════════════════════════════════════════════════════════
export const CONTEXT_STATUS = '/api/context/status';
export const CONTEXT_CONFIG = '/api/context/config';
export const CONTEXT_COMPRESS = '/api/context/compress';

// ═══════════════════════════════════════════════════════════════
//  经验 / 审批 / 能力清单 / 可视化工作流 / 智能体线
// ═══════════════════════════════════════════════════════════════
export const EXPERIENCE_STATS = '/api/experience/stats';
export const EXPERIENCE_INGEST = '/api/experience/ingest';
export const APPROVAL_SESSION = '/api/approval/session';
export const CAPABILITY_MANIFEST = '/api/capability-manifest';
export const VISUAL_WORKFLOWS = '/api/visual-workflows';
export const AGENT_LINES = '/api/agent-lines';

// ═══════════════════════════════════════════════════════════════
//  控制平面（/api/cp）—— 两个文件此前各写一份，口径不一，现收口于此
// ═══════════════════════════════════════════════════════════════
export const CP = '/api/cp';
export const CP_TOOL_EXEMPTIONS = '/api/cp/tool-exemptions';
