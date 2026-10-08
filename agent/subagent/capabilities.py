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
        question="断网还能不能想？", state=PARTIAL,
        evidence="core/local_llm.py 存在（主智能体侧）；分身执行后端目前只有内部 LLM 与外部 CLI 两档",
        evidence_files=("core/local_llm.py", "agent/subagent/channel.py"),
        gap="分身执行后端无\"本地推理\"档；外部 CLI 通道需 CP_SUBAGENT_AGENT_CLI（当前未配置）",
        next_stage="S5"),
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
        evidence="memory_mode none|brokered：母体按 tenancy 取只读上下文并注入 ②约束（broker + 域越界守卫），分身侧无记忆工具（§5.7 机制 3 不破）",
        evidence_files=("agent/memory/broker.py", "agent/subagent/memory_broker.py", "agent/memory/tenancy.py"),
        gap="scoped（分身自带私人记忆域、放开机制 3 硬禁）未做——需四处判定同改 + 配额 + 熔断 + 审计",
        next_stage="S3"),
    SovereigntyFace(
        key="memory_provider", layer="memory", label="记忆提供商与知识库",
        question="存在哪、离线可用吗？", state=PARTIAL,
        evidence="memory/router.py（5 类任务路由 + L1/L2/L3 分层）+ holographic_adapter（本地 SQLite/FTS5）",
        evidence_files=("agent/memory/router.py", "agent/memory/adapters/holographic_adapter.py"),
        gap="按 provider 取 adapter 的 scoped 接线未完成；brokered 只经母体 tenancy 只读，不按 provider 选 adapter",
        next_stage="S3"),
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
        evidence="TTL ≤ 任务时长 + finally 无条件销毁 + 环境注入 + 销毁证据审计",
        evidence_files=("agent/subagent/credentials.py",),
        gap="跨 provider 的引用式自带密钥未做（属 S5 bundle 契约的一部分）",
        next_stage="S5"),
    # ── 身体层 ──
    SovereigntyFace(
        key="execution_backend", layer="body", label="执行后端",
        question="在哪跑？", state=PARTIAL,
        evidence="channel.py：内部 LLM（同进程）+ 外部 agent CLI（subprocess）两档；协议同为 task_file → JSON Lines",
        evidence_files=("agent/subagent/channel.py", "agent/subagent/container.py"),
        gap="container / bundle 后端未做；subprocess 需 CP_SUBAGENT_AGENT_CLI（当前未配置）",
        next_stage="S5"),
    SovereigntyFace(
        key="lifecycle", layer="body", label="生命周期",
        question="活多久、能不能热更？", state=OWNED,
        evidence="创建/销毁/热更新/TTL（取契约⑦）+ 并发上限与回压（ConcurrencyBarrier）",
        evidence_files=("agent/subagent/lifecycle.py", "agent/subagent/barrier.py")),
    SovereigntyFace(
        key="portability", layer="body", label="可带走 bundle",
        question="换机断网还能不能活？", state=MISSING,
        evidence="pages/hub/workshop/replicate.tsx 是纯说明页（零 API 调用），如实标注规划中",
        evidence_files=("yunshu-ui/src/pages/hub/workshop/replicate.tsx",),
        gap="无分身包格式、无导出/导入端点、无离线依赖打包",
        next_stage="S5"),
    SovereigntyFace(
        key="communication", layer="body", label="通信与协同",
        question="多个它之间怎么说话？", state=PARTIAL,
        evidence="fan_out 并行派发 + 逐任务信封 + delegate_many + delegation_history",
        evidence_files=("agent/tools/fan_out_tools.py", "agent/subagent/lifecycle.py"),
        gap="无共享任务看板（TaskCreate/Update）、无分身间直连；母体为中枢的设计未落地",
        next_stage="S4"),
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