"""主权分身能力投影 —— 六层二十面的**单一权威**（只读、纯逻辑、零副作用）

【为什么需要这个模块（不这样会怎样）】
    "组装台"页面要回答一个问题：**这个分身到底拥有什么主权？** 如果让前端自己判
    （"memory_provider 有字段所以记忆已生效"），就会出现第二份口径：后端今天把
    `memory_provider` 当声明字段，前端却按已生效展示 —— 这正是本仓最忌讳的
    "字段在、没人读"被 UI 化妆成"已接线"。

    本模块把三态（owned / partial / missing）与**证据**收敛成一份后端投影，
    页面只渲染、不判定。设计原文见 `docs/主权分身_维度与组装页设计_20261008.md`
    与起源设计 `docs/superpowers/design/11_架构合规性终极审核.md`。

【三态口径（写死在这里，调用方不要另行发明）】
    · owned   —— 有配置字段 / 机制、**有消费者**、随响应回显、可审计；
    · partial —— 有字段或地基，但**消费者不在**（或只在主智能体侧成立）；
    · missing —— 机制不存在（不是"没配"，是"没做"）。

【反幻觉（本模块最关键的一条纪律）】
    每条 owned / partial 面必须给出 `evidence_files`，且这些路径**必须真实存在**
    —— 由 `tests/unit/test_subagent_capabilities.py` 逐个 `os.path.exists` 断言。
    没有这条守卫，"能力清单"会变成一份漂亮但编造的 PPT；有了它，说某面"拥有"
    就等于承诺：去这些文件里能读到实现。missing / partial 必须给 `gap` 指明缺什么、
    `next_stage` 指明何时补（S3/S4/S5），不许含糊。

【依赖纪律】零第三方依赖、零 agent 内部导入（纯 dataclass + 常量），
    因此路由、测试、前端文档镜像都能安全引用它。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

__all__ = [
    "LAYERS",
    "MATURITY_LEVELS",
    "SOVEREIGNTY_FACES",
    "STATE_LABELS",
    "STATES",
    "SovereigntyFace",
    "sovereignty_report",
    "verify_faces",
]

#: 三态词表（封闭；UI 的颜色/文案由 STATE_LABELS 派生，不另立）
OWNED = "owned"
PARTIAL = "partial"
MISSING = "missing"
STATES: Tuple[str, ...] = (OWNED, PARTIAL, MISSING)
STATE_LABELS: Dict[str, str] = {
    OWNED: "拥有",
    PARTIAL: "声明未接线",
    MISSING: "未做",
}

#: 六层（顺序即页面阅读顺序：主体 → 大脑 → 记忆 → 手脚 → 身体 → 治理）
LAYERS: Tuple[Dict[str, str], ...] = (
    {"key": "identity", "label": "主体", "question": "它是谁"},
    {"key": "brain", "label": "大脑", "question": "它怎么想"},
    {"key": "memory", "label": "记忆", "question": "它记得什么"},
    {"key": "hands", "label": "手脚", "question": "它能做什么"},
    {"key": "body", "label": "身体", "question": "它在哪跑、能否搬家"},
    {"key": "governance", "label": "治理", "question": "谁说了算、看得见吗"},
)

#: 主权成熟度四档（L0→L3；页面据此给出"当前档"）
MATURITY_LEVELS: Tuple[Dict[str, str], ...] = (
    {"key": "L0", "label": "借用型", "note": "跑在别人云上，身份/记忆/密钥都不在你手里"},
    {"key": "L1", "label": "声明型", "note": "有字段、有界面、没有消费者（填了不生效）"},
    {"key": "L2", "label": "拥有型", "note": "每面都有消费者 + 随响应回显 + 可审计"},
    {"key": "L3", "label": "可带走型", "note": "断网换机仍能完整运行（bundle + 本地推理 + 本地记忆）"},
)


@dataclass(frozen=True)
class SovereigntyFace:
    """一个主权面

    Attributes:
        key: 稳定键（前端 testid / 审计引用；不要改）。
        layer: 所属层键（LAYERS）。
        label: 人读名称（页面显示）。
        question: 这个面回答的"归属"问题。
        state: owned | partial | missing（STATES）。
        evidence: 人读证据（一句话说清凭什么）。
        evidence_files: 证据文件路径（owned/partial 必须有且真实存在）。
        gap: partial/missing 时的缺口说明（缺什么）。
        next_stage: 计划补齐的阶段（S3/S4/S5）；owned 为空串。
    """

    key: str
    layer: str
    label: str
    question: str
    state: str
    evidence: str
    evidence_files: Tuple[str, ...] = ()
    gap: str = ""
    next_stage: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "key": self.key,
            "layer": self.layer,
            "label": self.label,
            "question": self.question,
            "state": self.state,
            "state_label": STATE_LABELS.get(self.state, self.state),
            "evidence": self.evidence,
            "evidence_files": list(self.evidence_files),
            "gap": self.gap,
            "next_stage": self.next_stage,
        }


#: 六层二十面（**唯一权威表**）。新增一面 = 在这里加一条 + 一条守卫用例；
#: 改状态 = 改这里的 state（页面与接口随之变化，前端不需要动）。
SOVEREIGNTY_FACES: Tuple[SovereigntyFace, ...] = (
    # ── 主体层 ──
    SovereigntyFace(
        key="identity", layer="identity", label="身份与命名",
        question="它是不是稳定可追溯的实体？", state=OWNED,
        evidence="SubagentConfig.name + 容器 sa-<hex>；创建/销毁/热更新均在生命周期管理器内",
        evidence_files=("agent/subagent/container.py", "agent/subagent/lifecycle.py")),
    SovereigntyFace(
        key="role", layer="identity", label="角色与人格",
        question="它\"是谁\"由谁定、能不能改？", state=OWNED,
        evidence="受控角色模板词表 ROLE_TEMPLATES + 提示词硬角色 delegate_role + 三档显式开启",
        evidence_files=("agent/subagent/role_templates.py", "agent/prompt_manager/roles.py")),
    SovereigntyFace(
        key="system_prompt", layer="identity", label="系统提示词底稿",
        question="它的底稿是不是你自己的、可审的？", state=OWNED,
        evidence="DELEGATE_SYSTEM_PROMPT 为云枢自有固定文本；角色片段拼在基座之后；§5.7 机制 1/2 禁止外来文本进 system prompt",
        evidence_files=("agent/subagent/executor.py",)),
    # ── 大脑层 ──
    SovereigntyFace(
        key="model", layer="brain", label="模型",
        question="用哪个大脑、能不能换？", state=OWNED,
        evidence="resolve_subagent_llm（inherit/explicit/fallback-after-error）+ LLMService.with_model + "
                 "温度在调用面钉住 + 跨 provider（resolve_cross_provider_llm：opt-in + host 白名单 + "
                 "到达端引用式密钥，失败回退且可见）",
        evidence_files=("agent/subagent/llm_factory.py", "memory/llm_service.py"),
        gap="同 provider 换模型名是默认路径（with_model 复用部署客户端）；**跨 provider 已接线**"
            "（opt-in + host 白名单）：分身声明 llm_override{provider, base_url, model, 凭据引用}，"
            "凭据值由**到达端环境**经 credentials 桥解析，base_url host 必须落在 CP_SUBAGENT_LLM_ALLOW，"
            "任一步不成即回退母体实例并带回非空 error（**绝不静默换 provider**）。残留边界：白名单需显式"
            "配置；跨 provider 的真实连通性属环境事实（CI 只做协议级注入验证）",
        next_stage=""),
    SovereigntyFace(
        key="local_inference", layer="brain", label="本地推理",
        question="断网还能不能想？", state=OWNED,
        evidence="第三执行后端 local（agent/subagent/local_inference.py）：LocalLLMAdapter 接 core/local_llm.py 的 Ollama，"
                 "LocalInferenceChannelExecutor 复用 LlmChannelExecutor 的多轮/JSONL 协议；"
                 "bundle.runtime.backend=local + CP_SUBAGENT_LOCAL_ENABLED 显式开启，通道判定（路由/工具）同步认识",
        evidence_files=("agent/subagent/local_inference.py", "core/local_llm.py",
                        "agent/subagent/channel.py", "agent/subagent/bundle.py"),
        gap="真机 E2E 需本机服务在跑（CI 只做协议级注入验证；Ollama 探活 /api/tags、vLLM 探活 /v1/models）；"
            "超时仍硬编码（探活 5s / 生成 60s），未登记 agent/settings/registry.py",
        next_stage=""),
    SovereigntyFace(
        key="cognitive_loop", layer="brain", label="认知闭环（规划/反思/学习）",
        question="它会不会自我纠错、越用越聪明？", state=OWNED,
        evidence="分身侧有界环 agent/subagent/cognitive_loop.py（opt-in，包在**通道咽喉** ⇒ "
                 "四档后端同时获得）：plan（LLM 出步骤写进 task_file.plan）→ act（原通道不变）→ "
                 "reflect（LLM 自评 verdict=pass|revise）→ revise（注入 task_file.self_review 重跑；"
                 "第二轮更差/超时则**保留第一轮**）；学习 = 同 workspace 教训流水"
                 "（cognitive_lessons.jsonl：规划前回读最近 5 条、revise 后落盘）；"
                 "跨 workspace/跨实例共享（一期，默认关）+ 提升闸门二期 = 按 (tenant, subject) 隔离的共享 "
                 "lessons.jsonl（方案 A + 双上限 + erase + taint=upstream），"
                 "经 ContextVar 由母体 executor 设定作用域；"
                 "任一步失败 ⇒ 退回原产出 + degraded 审计（绝不拖死正常委派）；"
                 "另有云枢复评（TriadCollector + CloudReviewer）对单次委派做回收三件套校验",
        evidence_files=("agent/subagent/cognitive_loop.py", "agent/subagent/cognitive_lessons.py",
                        "agent/subagent/collection.py", "agent/subagent/executor.py"),
        gap="环**默认关闭**（CP_SUBAGENT_COGNITIVE_LOOP=1；未开=逐字旧行为）；跨 workspace 共享池同样"
            "**默认关**（CP_SUBAGENT_COGNITIVE_SHARED_LESSONS=1 + CP_SUBAGENT_COGNITIVE_LESSONS_DIR）；"
            "提升闸门（candidate→shared，I2）**二期已落地**：N 个不同 instance_id 独立确认自动提升，"
            "或人工 promote（N=CP_SUBAGENT_COGNITIVE_PROMOTION_N 默认 3，0=只走人工；instance 身份取 "
            "CP_SUBAGENT_INSTANCE_ID，兜底 host+仓库根哈希）；"
            "三期已落地：教训池与 memory_ops **同口径导出/导入**"
            "（scripts/subagent_cognitive_lessons.py 的 export/import）；导入按 "
            "(tenant, subject, signature, advice) **重算 lesson_id** 并强制目标作用域（外来域标识丢弃），"
            "幂等合并 confirmed_by/state（跨租户共享明确不做）",
        next_stage=""),
    SovereigntyFace(
        key="autonomy", layer="brain", label="自主权分级",
        question="它能自己决定到什么程度？", state=OWNED,
        evidence="档位（L1–L5）**已进 SubagentConfig**：agent/subagent/autonomy_gate.py —— "
                 "创建/热更新端点严格校验（非法 400，不静默回退默认档）；委派时 config→ctx 桥把"
                 "生效档位写进 DelegationContext.metadata；执行器包 AutonomyContext，并在工具闸门里"
                 "把**档位之外类别**的声称调用计入 violations（与“裁剪集外调用”同款，整次委派失败）；"
                 "主智能体侧另有 agent/autonomy.py 的 L1–L5 策略表 + actor_matrix + requires_approval",
        evidence_files=("agent/subagent/autonomy_gate.py", "agent/subagent/container.py",
                        "agent/subagent/executor.py", "agent/subagent/lifecycle.py",
                        "agent/autonomy.py", "agent/security/actor_matrix.py"),
        gap="档位→工具类别已由 S5-1 的可表达策略覆盖（config.yaml autonomy.tool_policy 显式声明"
            " → 权威元数据派生 → 未登记工具保守默认 high_risk），自定义工具不再落到 LOW_RISK 兜底；"
            "残留：per_level_policy 的 config 覆盖尚未透传到分身侧（当前只读策略表）",
        next_stage=""),
    # ── 记忆层 ──
    SovereigntyFace(
        key="memory_scope", layer="memory", label="记忆域与档位",
        question="它记得的东西归谁？", state=OWNED,
        evidence="memory_mode none|brokered|scoped：none 不分身记忆；brokered 母体按 tenancy 取只读上下文注入 ②约束；"
                 "scoped 为显式开启的受控档（三要素 + provider 非空），四处判定同改；委派成功后经 ScopedMemoryDomain "
                 "写回结构化事实（admit 配额 → tenancy 域校验 → 真实后端 write → record_write → 审计）；"
                 "**分身侧主动 read/write 已接线**：协议内声明式守门器 agent/subagent/scoped_memory_gate.py"
                 "（子代理在载荷声明 memory_reads/writes ⇒ 母体 ScopedMemoryGate 代执行；四道闸：mode/域启用判定 → "
                 "toolset 权限 → 母体域强制覆盖 → fail-soft；域标识一律取母体 domain.scope，子代理声明的域被丢弃），"
                 "配额/熔断/审计真实强制（§5.7 机制 3 受控例外）",
        evidence_files=("agent/memory/broker.py", "agent/memory/scoped_store.py", "agent/subagent/memory_broker.py",
                        "agent/subagent/scoped_memory_gate.py", "agent/subagent/tool_loop.py",
                        "agent/subagent/memory_quota.py",
                        "agent/memory/tenancy.py", "agent/subagent/executor.py"),
        gap="工具循环已落地（S5-3：agent/subagent/tool_loop.py + peer_local_handler 接线——母体把已授权"
            "工具定义写进 task_file，分身自己执行并回报；白名单外一律拒），不再必须“声明 + 母体代执行”；"
            "残留：容器/CLI 等 execution backend 是否支持工具调用取决于环境侧（不支持则逐字回退声明式守门器）；"
            "mem0 后端无列出/删除见 memory_ops 面；配额熔断按容器存活期生效",
        next_stage=""),
    SovereigntyFace(
        key="memory_provider", layer="memory", label="记忆提供商与知识库",
        question="存在哪、离线可用吗？", state=OWNED,
        evidence="scoped 档按 memory_provider 经 ScopedMemoryDomain.select_store 取真实后端：holographic → LayeredMemoryStore（本地 SQLite/FTS5）；mem0 → Mem0Adapter（可选依赖 + 内置降级）；未知 provider 显式 400 / UnknownMemoryProviderError，不静默回退；memory_view 如实回显 provider/store/离线 degraded",
        evidence_files=("agent/memory/scoped_store.py", "agent/subagent/memory_broker.py", "agent/memory/router.py", "agent/memory/adapters/holographic_adapter.py"),
        gap="mem0 为可选依赖：未安装时 Mem0Adapter 走内置 JSON 降级（非语义去重）；该降级已按"
            " (tenant, workspace, subject) 物理分库（scoped_shard_path，S5-2），不再单文件混租户；"
            "残留：真实 mem0 引擎的租户隔离依赖其自身 metadata/user 作用域（引擎未装，未实测）；"
            "brokered 仍只经母体 tenancy 只读、不按 provider 选 adapter（设计如此）",
        next_stage=""),
    SovereigntyFace(
        key="memory_ops", layer="memory", label="记忆导出/擦除/迁移",
        question="能不能删干净、搬走？", state=OWNED,
        evidence="ScopedMemoryDomain 三种记忆主权操作（agent/memory/scoped_store.py）："
                 "export_entries（只读；空域断言；失败返回明确 error_code 且返回空）、"
                 "erase_entries（**必须 confirm** + **先快照后删**，快照写失败即中止删除）、"
                 "import_entries（逐条 fail-soft；复用 write 的密钥闸/域校验/配额，域由目标域强制覆盖）；"
                 "入口 CLI scripts/subagent_memory_ops.py（export/import/erase/migrate）；"
                 "三者均写审计（subagent.memory.export / erase / import，只记条数）",
        evidence_files=("agent/memory/scoped_store.py", "scripts/subagent_memory_ops.py"),
        gap="mem0 **引擎**路径的 add/search 不接受域过滤参数 ⇒ 其列出/删除仍 fail-closed"
            "（E_MEMORY_OP_UNSUPPORTED，不假装支持）；**内置降级**（未装 mem0 时的本地 JSON 存储）"
            "已实现按域列出/删除并做后置域过滤；data/ 整目录裁剪与脱敏仍属独立项",
        next_stage=""),
    # ── 手脚层 ──
    SovereigntyFace(
        key="tools_skills", layer="hands", label="工具 / 技能 / 组件",
        question="能调用什么、谁批准？", state=OWNED,
        evidence="resolve_subagent_assembly 按主线装配（去 govern、去机制 3 硬禁）+ SkillPack 技能面 + "
                 "SubAgentToolset 裁剪 + **运行期工具声明桥**（插件/MCP 经 "
                 "CP_SUBAGENT_RUNTIME_TOOL_DECLS 显式声明 plane/effect/risk 后进装配候选）",
        evidence_files=("agent/subagent/assembly.py", "agent/lines/skillpack.py",
                        "agent/subagent/runtime_tool_decls.py"),
        gap="插件/MCP 在**运行期**注册（agent/extensions、agent/tools/mcp_connector）但没有 "
            "data/tool_definitions/<name>.yaml ⇒ assembler 对未声明工具刻意 fail-closed"
            "（无法证明安全边界）。已提供**显式声明**通道：只有**已注册且被运维声明**"
            "（plane/effect/risk 取值合法）的工具才进候选；第三方不得自证边界，未声明行为逐字不变",
        next_stage=""),
    SovereigntyFace(
        key="permission_sandbox", layer="hands", label="权限 / 沙箱 / 护栏",
        question="做坏事时谁拦？", state=OWNED,
        evidence="actor_matrix 权限矩阵 + 第三方默认隔离（无宿主网络/无 SSH agent/无 HOME）+ 工具调用闸门（越界即整次委派失败）",
        evidence_files=("agent/security/actor_matrix.py", "agent/subagent/sandbox.py", "agent/subagent/toolset.py")),
    SovereigntyFace(
        key="credentials", layer="hands", label="凭据",
        question="钥匙在谁手里？", state=OWNED,
        evidence="TTL ≤ 任务时长 + finally 无条件销毁 + 环境注入 + 销毁证据审计；"
                 "bundle 的 secrets.refs 只存引用（source/name/env_var，无值）",
        evidence_files=("agent/subagent/credentials.py", "agent/subagent/bundle.py"),
        gap="引用→值的签发/注入**已接线**：bundle 的 secrets.refs 经 import_config 存进 "
            "SubagentConfig.secret_refs（**只搬引用不搬值**），容器每次委派按**到达端本地环境**解析"
            "（credentials.refs_to_specs，ref 的 env_var 原样沿用，缺值跳过不伪造）并走 "
            "credential_scope 签发 + finally 无条件销毁（TTL ≤ 任务时长）；跨 provider 的**模型客户端**"
            "构造仍缺（见 model 面）",
        next_stage=""),
    # ── 身体层 ──
    SovereigntyFace(
        key="execution_backend", layer="body", label="执行后端",
        question="在哪跑？", state=OWNED,
        evidence="channel.py：内部 LLM（同进程）+ 外部 agent CLI（subprocess）+ 本地推理（local，见 local_inference.py）+ 容器（container，见 container_backend.py）四档；"
                 "协议同为 task_file → JSON Lines；bundle 的 runtime.backend 经 resolve_backend 与 SubagentConfig.execution_backend 映射（未知/不可用 fail-closed）；"
                 "container 已有镜像内可执行对端（scripts/subagent_peer.py，§3.10 协议：离线回执 + --handler 注入镜内执行体），"
                 "task_file 只读挂 /task 与可写 tmpfs /work 分离（同目标会被 tmpfs 盖住，实测真跑必红）；"
                 "镜内真推理执行体 agent/subagent/peer_local_handler.py（--handler 调 core.local_llm，"
                 "失败即非零退出、绝不假成功）；**真实推理已端到端验证**：容器内 peer → handler → "
                 "Ollama(qwen2.5:0.5b)，产出 status=done 且 channel_meta.llm_used=true"
                 "（容器内实跑退出码 0；gated 测试 tests/unit/test_peer_local_handler_ollama_e2e.py）",
        evidence_files=("agent/subagent/channel.py", "agent/subagent/local_inference.py",
                        "agent/subagent/container_backend.py",
                        "agent/subagent/container.py", "agent/subagent/bundle.py",
                        "scripts/subagent_peer.py", "agent/subagent/peer_local_handler.py",
                        "docker/subagent-peer/Dockerfile",
                        "docker/subagent-peer/Dockerfile.local",
                        "tests/unit/test_peer_local_handler_ollama_e2e.py"),
        gap="镜内**全离线**变体（镜像自带 Ollama+模型、运行时 --network none）已在容器档真跑验证"
            "（Dockerfile.local 形态的镜像；产出 status=done / channel_meta.llm_used=true）；"
            "两条硬前提：容器档默认 256MiB cgroup 装不下 0.5B 模型（需 CP_DIGESTION_ISOLATION_MEMORY_MB≥1024），"
            "且 --read-only 下 /tmp 不可写（entrypoint 日志落 $HOME）；镜像仍不进 CI——构建期要下 ollama "
            "二进制 1.44GB + 模型 397MB，需按 docker/subagent-peer/Dockerfile.local 在目标机构建；"
            "subprocess 档需配 CP_SUBAGENT_AGENT_CLI（当前未配置）",
        next_stage=""),
    SovereigntyFace(
        key="lifecycle", layer="body", label="生命周期",
        question="活多久、能不能热更？", state=OWNED,
        evidence="创建/销毁/热更新/TTL（取契约⑦）+ 并发上限与回压（ConcurrencyBarrier）",
        evidence_files=("agent/subagent/lifecycle.py", "agent/subagent/barrier.py")),
    SovereigntyFace(
        key="portability", layer="body", label="可带走 bundle",
        question="换机断网还能不能活？", state=OWNED,
        evidence="bundle 契约（身份/装配/引用式密钥/entrypoint 协议 + v2 environment 依赖清单）"
                 "+ GET /api/subagent/<name>/bundle 导出（过密钥闸）+ POST /api/subagent/import 导入；"
                 "离线包端到端：scripts/build_offline_pack.py 在联网机产出含 wheelhouse 的 tar.gz"
                 "（offline_ready **覆盖判定**；scripts/trim_bundle.py 脱敏）→ --pack-dir 交付到 "
                 "CP_SUBAGENT_OFFLINE_PACK_DIR → GET /api/subagent/<name>/offline_pack 下载"
                 "（agent/subagent/offline_pack.py 定位；路径穿越/未构建一律 fail-closed）；"
                 "replicate.tsx 为真实导出/导入面",
        evidence_files=("agent/subagent/bundle.py", "agent/subagent/dependencies.py",
                        "agent/subagent/offline_pack.py",
                        "agent/server_routes/routes_subagent.py",
                        "yunshu-ui/src/pages/hub/workshop/replicate.tsx",
                        "scripts/subagent_peer.py", "docker/subagent-peer/Dockerfile",
                        "scripts/build_offline_pack.py", "scripts/trim_bundle.py"),
        gap="全量 wheelhouse 需运维在联网机执行 build_offline_pack（本仓不预置二进制 wheel）；"
            "container 镜内真实推理另见执行后端面",
        next_stage=""),
    SovereigntyFace(
        key="communication", layer="body", label="通信与协同",
        question="多个它之间怎么说话？", state=OWNED,
        evidence="共享任务看板（append-only 任务事件，母体**唯一**写板；fan_out/单发逐任务上板，可折出最新态）+ 二次派发轮次机制（HTTP delegate 的 previous_delegation_id ⇒ 同 task_id 第二轮 + 上一轮显式 superseded）+ fan_out 并行派发 + delegation_history + 静态对端与心跳（CP_SUBAGENT_PEERS 声明式白名单 + send_heartbeats 逐对端健康折叠与审计，读面内联 /api/subagent/history 的 peers 段；app_server 启动期注册周期任务、读面共享 live registry、健康态落 data/subagent_peer_health.json 并在启动时回填）",
        evidence_files=("agent/subagent/task_board.py", "agent/subagent/rounds.py",
                        "agent/tools/fan_out_tools.py", "agent/subagent/lifecycle.py",
                        "agent/subagent/peers.py", "app_server.py",
                        "agent/server_routes/routes_subagent.py"),
        gap="分身间直连 / 服务发现刻意不做（母体中枢），对端只来自静态白名单、不做任何探测；"
            "**二次派发轮次**单发（HTTP delegate 的 previous_delegation_id）与批量"
            "（fan_out 逐任务 previous_delegation_id，经 rounds.link_follow_up/validate_previous）"
            "均已接线（不合格只让该任务失败，fail-closed，不降级成普通第一轮）；"
            "心跳已在启动期接线并持久化健康态（app_server 注册 + 读面共享 live registry + 落盘回填），"
            "但需回调出站配置齐（开关 + host 白名单 + 令牌）才注册；"
            "离线回退**已接线（opt-in）**：HTTP 回调投递失败（传输异常 / 5xx）落本地 outbox "
            "（CP_SUBAGENT_CALLBACK_OUTBOX_DIR），可用 callback_outbox.replay 稍后重放；未配置该目录即逐字旧行为",
        next_stage=""),
    # ── 治理层 ──
    SovereigntyFace(
        key="observability", layer="governance", label="可观测 / 审计 / 成本",
        question="每一步能不能复算？", state=OWNED,
        evidence="trace_v2（child() 串父链）+ 链式审计（追加写入）+ CostLedger 成本记账 + delegation_history",
        evidence_files=("agent/observability/trace_v2.py", "agent/audit/chain.py", "agent/subagent/collection.py")),
    SovereigntyFace(
        key="declarative_config", layer="governance", label="声明式配置 / 零硬编码",
        question="行为在配置还是代码？", state=OWNED,
        evidence="settings registry（473 开关，scan_settings --check 零缺口）+ data/agent_lines/*.yaml + data/tool_definitions/*.yaml",
        evidence_files=("agent/settings/registry.py", "data/agent_lines/engineering.yaml")),
    SovereigntyFace(
        key="testing_guards", layer="governance", label="测试 / 守卫",
        question="回退了会不会红？", state=OWNED,
        evidence="可证伪守卫（回退产品逻辑 ⇒ 立刻红）+ 失败基线回归门禁（只允许收缩）",
        evidence_files=("tests/unit/test_subagent_role_templates.py",)),
)


def verify_faces(faces: Tuple[SovereigntyFace, ...] = SOVEREIGNTY_FACES,
                 *, require_files: bool = True) -> List[str]:
    """自检：返回问题清单（空 = 通过）。**不抛异常**，供测试与调用方复用同一份判据。

    判据（缺一即问题）：
      1. key 唯一、layer 在 LAYERS 内、state 在 STATES 内、label/question/evidence 非空；
      2. owned / partial 必须至少有一个 evidence_file；
      3. partial / missing 必须有 gap 与 next_stage；
      4. require_files=True 时，evidence_files 里的路径**必须真实存在**（反幻觉）。
    """
    import os

    # 证据路径按"相对仓库根"书写；即以任意 CWD 运行也能判定（不依赖调用方 chdir）
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def _exists(path: str) -> bool:
        return os.path.exists(path) or os.path.exists(os.path.join(root, path))

    problems: List[str] = []
    seen: set = set()
    layer_keys = {layer["key"] for layer in LAYERS}
    for face in faces:
        where = face.key or "<空 key>"
        if not face.key or face.key in seen:
            problems.append("key 缺失或重复: " + where)
        seen.add(face.key)
        if face.layer not in layer_keys:
            problems.append(where + ": 未知层 " + face.layer)
        if face.state not in STATES:
            problems.append(where + ": 未知状态 " + face.state)
        for name, value in (("label", face.label), ("question", face.question),
                            ("evidence", face.evidence)):
            if not str(value or "").strip():
                problems.append(where + ": " + name + " 不能为空")
        if not face.evidence_files and face.state in (OWNED, PARTIAL):
            problems.append(where + ": " + face.state + " 面必须给出证据文件")
        if face.state in (PARTIAL, MISSING):
            if not face.gap.strip():
                problems.append(where + ": " + face.state + " 面必须写明 gap（缺什么）")
            if not face.next_stage.strip():
                problems.append(where + ": " + face.state + " 面必须给出 next_stage")
        if require_files:
            for path in face.evidence_files:
                if not _exists(path):
                    problems.append(where + ": 证据文件不存在 " + path)
    return problems


def sovereignty_report() -> Dict[str, object]:
    """六层二十面的完整投影（`GET /api/subagent/capabilities` 的载荷）

    载荷结构：
      「layers」  六层（key/label/question/faces 有序键）
      「faces」   二十面（每面含 state/state_label/evidence/evidence_files/gap/next_stage）
      「summary」 {owned, partial, missing, total}
      「maturity」L0–L3 四档 + 当前档（按 summary 推导：有 missing/partial 即未到 L3）
    """
    faces = [face.to_dict() for face in SOVEREIGNTY_FACES]
    summary = {
        "owned": sum(1 for f in SOVEREIGNTY_FACES if f.state == OWNED),
        "partial": sum(1 for f in SOVEREIGNTY_FACES if f.state == PARTIAL),
        "missing": sum(1 for f in SOVEREIGNTY_FACES if f.state == MISSING),
        "total": len(SOVEREIGNTY_FACES),
    }
    layers = []
    for layer in LAYERS:
        layers.append({
            **layer,
            "faces": [f.key for f in SOVEREIGNTY_FACES if f.layer == layer["key"]],
        })
    # 当前档：missing>0 或 partial>0 ⇒ 未到 L3（L3 要求每一面都真正可带走）
    current = "L2" if (summary["missing"] or summary["partial"]) else "L3"
    return {
        "layers": layers,
        "faces": faces,
        "summary": summary,
        "maturity": {"levels": [dict(m) for m in MATURITY_LEVELS], "current": current},
    }