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
        evidence="resolve_subagent_llm（inherit/explicit/fallback-after-error）+ LLMService.with_model + 温度在调用面钉住",
        evidence_files=("agent/subagent/llm_factory.py", "memory/llm_service.py"),
        gap="仅限同一 provider 下的模型名；跨 provider / 自带密钥需 per-分身 TTL 凭据",
        next_stage="S5"),
    SovereigntyFace(
        key="local_inference", layer="brain", label="本地推理",
        question="断网还能不能想？", state=OWNED,
        evidence="第三执行后端 local（agent/subagent/local_inference.py）：LocalLLMAdapter 接 core/local_llm.py 的 Ollama，"
                 "LocalInferenceChannelExecutor 复用 LlmChannelExecutor 的多轮/JSONL 协议；"
                 "bundle.runtime.backend=local + CP_SUBAGENT_LOCAL_ENABLED 显式开启，通道判定（路由/工具）同步认识",
        evidence_files=("agent/subagent/local_inference.py", "core/local_llm.py",
                        "agent/subagent/channel.py", "agent/subagent/bundle.py"),
        gap="真机 E2E 需本机 Ollama 服务在跑（CI 只做协议级注入验证）；core/local_llm 的 vLLM 分支未实现（本档 fail-closed，不假装支持）",
        next_stage=""),
    SovereigntyFace(
        key="cognitive_loop", layer="brain", label="认知闭环（规划/反思/学习）",
        question="它会不会自我纠错、越用越聪明？", state=PARTIAL,
        evidence="云枢复评（TriadCollector + CloudReviewer）对单次委派做回收三件套校验",
        evidence_files=("agent/subagent/collection.py",),
        gap="分身侧无独立的规划/反思/学习闭环；fan_out 只有一次性并行派发",
        next_stage="S4"),
    SovereigntyFace(
        key="autonomy", layer="brain", label="自主权分级",
        question="它能自己决定到什么程度？", state=OWNED,
        evidence="agent/autonomy.py L1–L5 + actor_matrix + requires_approval（主智能体侧）",
        evidence_files=("agent/autonomy.py", "agent/security/actor_matrix.py"),
        gap="自主权档位尚未进 SubagentConfig（分身目前是受治理的一次性委派）",
        next_stage="S4"),
    # ── 记忆层 ──
    SovereigntyFace(
        key="memory_scope", layer="memory", label="记忆域与档位",
        question="它记得的东西归谁？", state=OWNED,
        evidence="memory_mode none|brokered|scoped：none 不分身记忆；brokered 母体按 tenancy 取只读上下文注入 ②约束；scoped 为显式开启的受控档（三要素 + provider 非空），四处判定同改；委派成功后经 ScopedMemoryDomain 写回结构化事实（admit 配额 → tenancy 域校验 → 真实后端 write → record_write → 审计），配额/熔断/审计真实强制（§5.7 机制 3 受控例外）",
        evidence_files=("agent/memory/broker.py", "agent/memory/scoped_store.py", "agent/subagent/memory_broker.py", "agent/subagent/memory_quota.py", "agent/memory/tenancy.py", "agent/subagent/executor.py"),
        gap="写回仅覆盖委派成功路径；分身侧工具主动 read/write 尚未接入（ScopedMemoryDomain.read 已就绪但无消费者），配额熔断按容器存活期生效",
        next_stage="S5"),
    SovereigntyFace(
        key="memory_provider", layer="memory", label="记忆提供商与知识库",
        question="存在哪、离线可用吗？", state=OWNED,
        evidence="scoped 档按 memory_provider 经 ScopedMemoryDomain.select_store 取真实后端：holographic → LayeredMemoryStore（本地 SQLite/FTS5）；mem0 → Mem0Adapter（可选依赖 + 内置降级）；未知 provider 显式 400 / UnknownMemoryProviderError，不静默回退；memory_view 如实回显 provider/store/离线 degraded",
        evidence_files=("agent/memory/scoped_store.py", "agent/subagent/memory_broker.py", "agent/memory/router.py", "agent/memory/adapters/holographic_adapter.py"),
        gap="mem0 为可选依赖：未安装时 Mem0Adapter 走内置 JSON 降级（非语义去重）且单文件无租户物理分库；brokered 仍只经母体 tenancy 只读、不按 provider 选 adapter（设计如此）",
        next_stage="S5"),
    SovereigntyFace(
        key="memory_ops", layer="memory", label="记忆导出/擦除/迁移",
        question="能不能删干净、搬走？", state=MISSING,
        evidence="无按分身导出/擦除入口（data/ 约 272MB 且多处被 .gitignore 排除）",
        gap="无记忆主权操作入口；P5 做包裁剪与脱敏时一并做",
        next_stage="S5"),
    # ── 手脚层 ──
    SovereigntyFace(
        key="tools_skills", layer="hands", label="工具 / 技能 / 组件",
        question="能调用什么、谁批准？", state=OWNED,
        evidence="resolve_subagent_assembly 按主线装配（去 govern、去机制 3 硬禁）+ SkillPack 技能面 + SubAgentToolset 裁剪",
        evidence_files=("agent/subagent/assembly.py", "agent/lines/skillpack.py"),
        gap="扩展中心插件（Plugin.client_slot/schema）与 MCP 接入未进分身装配面",
        next_stage="S5"),
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
        gap="跨 provider 的引用式自带密钥未做：bundle 已给出引用槽位，"
            "但引用→值的签发/注入（按到达端 TTL 凭据）尚未接线",
        next_stage="S5"),
    # ── 身体层 ──
    SovereigntyFace(
        key="execution_backend", layer="body", label="执行后端",
        question="在哪跑？", state=PARTIAL,
        evidence="channel.py：内部 LLM（同进程）+ 外部 agent CLI（subprocess）+ 本地推理（local，见 local_inference.py）+ 容器（container，见 container_backend.py）四档；"
                 "协议同为 task_file → JSON Lines；bundle 的 runtime.backend 经 resolve_backend 与 SubagentConfig.execution_backend 映射（未知/不可用 fail-closed）；"
                 "container 已有镜像内可执行对端（scripts/subagent_peer.py，§3.10 协议：离线回执 + --handler 注入镜内执行体），"
                 "task_file 只读挂 /task 与可写 tmpfs /work 分离（同目标会被 tmpfs 盖住，实测真跑必红）",
        evidence_files=("agent/subagent/channel.py", "agent/subagent/local_inference.py",
                        "agent/subagent/container_backend.py",
                        "agent/subagent/container.py", "agent/subagent/bundle.py",
                        "scripts/subagent_peer.py", "docker/subagent-peer/Dockerfile"),
        gap="container 已可协议真跑（镜内对端 + 挂载分离实测绿灯），但镜内**真实推理**仍需镜像自带 local 后端与模型（本轮不做）；装对端镜像后 subprocess 档需配 CP_SUBAGENT_AGENT_CLI（当前未配置）",
        next_stage="S5"),
    SovereigntyFace(
        key="lifecycle", layer="body", label="生命周期",
        question="活多久、能不能热更？", state=OWNED,
        evidence="创建/销毁/热更新/TTL（取契约⑦）+ 并发上限与回压（ConcurrencyBarrier）",
        evidence_files=("agent/subagent/lifecycle.py", "agent/subagent/barrier.py")),
    SovereigntyFace(
        key="portability", layer="body", label="可带走 bundle",
        question="换机断网还能不能活？", state=PARTIAL,
        evidence="bundle 契约（身份/装配/引用式密钥/entrypoint 协议 + v2 environment 依赖清单）"
                 "+ GET /api/subagent/<name>/bundle 导出（过密钥闸）+ POST /api/subagent/import 导入；"
                 "replicate.tsx 已从纯说明页改为真实导出/导入面",
        evidence_files=("agent/subagent/bundle.py", "agent/subagent/dependencies.py",
                        "agent/server_routes/routes_subagent.py",
                        "yunshu-ui/src/pages/hub/workshop/replicate.tsx"),
        gap="依赖清单已采集（environment 段）但未打包 wheel，换机仍不能离线安装；"
            "container 已有镜像内 CLI 入口（scripts/subagent_peer.py）可协议真跑，"
            "但镜内真实推理仍需自带 local 后端——换机断网仍不能完整运行",
        next_stage="S5"),
    SovereigntyFace(
        key="communication", layer="body", label="通信与协同",
        question="多个它之间怎么说话？", state=OWNED,
        evidence="共享任务看板（append-only 任务事件，母体**唯一**写板；fan_out/单发逐任务上板，可折出最新态）+ 二次派发轮次机制（HTTP delegate 的 previous_delegation_id ⇒ 同 task_id 第二轮 + 上一轮显式 superseded）+ fan_out 并行派发 + delegation_history + 静态对端与心跳（CP_SUBAGENT_PEERS 声明式白名单 + send_heartbeats 逐对端健康折叠与审计，读面内联 /api/subagent/history 的 peers 段；app_server 启动期注册周期任务、读面共享 live registry、健康态落 data/subagent_peer_health.json 并在启动时回填）",
        evidence_files=("agent/subagent/task_board.py", "agent/subagent/rounds.py",
                        "agent/tools/fan_out_tools.py", "agent/subagent/lifecycle.py",
                        "agent/subagent/peers.py", "app_server.py",
                        "agent/server_routes/routes_subagent.py"),
        gap="分身间直连 / 服务发现刻意不做（母体中枢），对端只来自静态白名单、不做任何探测；"
            "HTTP delegate 单发跟单已接线，fan_out 批量第二轮尚未接线；"
            "心跳已在启动期接线并持久化健康态（app_server 注册 + 读面共享 live registry + 落盘回填），"
            "但需回调出站配置齐（开关 + host 白名单 + 令牌）才注册；"
            "离线回退未做（offline_fallback_report 显式登记 status=not_implemented，无 outbox、无重放）",
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