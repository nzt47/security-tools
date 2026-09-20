"""工具 / 技能「可被 LLM 调用」统一标注 —— 守门测试

    python -m pytest tests/unit/test_tool_callability.py -q

覆盖四层：
    1. **声明层**：`data/tool_definitions/*.yaml` 的六个可调用性字段齐全、取值合法，
       且 `permission_level` 与 plane/effect/risk 的派生值一致（同一件事不许有两份口径）；
    2. **判定层**：`agent/lines/callability.py::judge` 的五条硬条件与三档标识；
    3. **派生层**：`data/capability_manifest.json` 与权威数据一致（清单是派生物，
       手改必须被拦住），且八项统一字段齐全、"不可调用必带原因"；
    4. **接线层**：模型可见集过滤（`agent/tools/__init__.py`）、检索索引过滤
       （`scripts/sync_tool_index.py`）与两个 REST 端点在**真实 app** 里的存在性。

【不易】本文件不依赖网络；运行时过滤用 tmp 目录隔离，**不写**生产数据目录。
"""
from __future__ import annotations

import importlib.util
import ast
import json
from pathlib import Path

import pytest
import yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFS_DIR = _PROJECT_ROOT / "data" / "tool_definitions"
_MANIFEST_PATH = _PROJECT_ROOT / "data" / "capability_manifest.json"
_SKILL_DECL_PATH = _PROJECT_ROOT / "data" / "skill_callability.yaml"


def _load_script(name: str):
    """动态加载 scripts/*.py（scripts 非包，用 importlib）"""
    spec = importlib.util.spec_from_file_location(
        name, _PROJECT_ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[arg-type]
    return mod


from agent.lines import callability as C  # noqa: E402
from agent.lines import models as M  # noqa: E402

backfill_mod = _load_script("backfill_tool_callability")
sync_manifest_mod = _load_script("sync_capability_manifest")
sync_index_mod = _load_script("sync_tool_index")

UNIFIED_FIELDS = ("tool_name", "tool_type", "llm_callable", "callable_mode",
                  "schema_registered", "host_executor", "permission_level",
                  "sandbox_allowed", "reason")
MARKS = (C.MARK_CALLABLE, C.MARK_CONDITIONAL, C.MARK_BLOCKED)


def _tool_docs() -> dict:
    out = {}
    for f in sorted(_DEFS_DIR.glob("*.yaml")):
        out[f.stem] = yaml.safe_load(f.read_text(encoding="utf-8"))
    return out


# ════════════════════════════════════════════════════════════
#  1. 声明层
# ════════════════════════════════════════════════════════════

class TestDeclarations:

    def test_每个工具_YAML_都有可调用性字段(self):
        missing = []
        for name, doc in _tool_docs().items():
            gap = [f for f in C.REQUIRED_DECL_FIELDS if f not in doc]
            if gap:
                missing.append((name, gap))
        assert not missing, (
            f"{len(missing)} 个 YAML 缺可调用性字段：{missing[:10]}\n"
            "→ 运行 python scripts/backfill_tool_callability.py")

    def test_取值都在取值域内(self):
        domains = {
            "tool_type": C.TOOL_TYPES,
            "callable_mode": C.CALLABLE_MODES,
            "permission_level": C.PERMISSION_LEVELS,
        }
        bad = []
        for name, doc in _tool_docs().items():
            for field, allowed in domains.items():
                if str(doc.get(field)) not in allowed:
                    bad.append((name, field, doc.get(field)))
            for field in ("llm_callable", "sandbox_allowed"):
                if not isinstance(doc.get(field), bool):
                    bad.append((name, field, doc.get(field)))
        assert not bad, f"非法声明值：{bad[:10]}"

    def test_permission_level_与治理轴派生值一致(self):
        """同一件事不许有两份口径：声明必须等于 plane/effect/risk 的派生结果

        【TASK-06 修改】原实现在这里**手写**了 `risk == "critical"` 这条规则，
        与 `agent/lines/models.py::ToolMeta.needs_approval` 构成两份副本。
        TASK-06 把审批阈值从 `critical` 提到 `high`（13 个 `risk: high` 工具首次
        进入确认流）⇒ 若守卫测试继续用旧口径，它会用 `want=internal` 去比对新
        声明 `restricted`，**报出一个与真实缺陷无关的失败**；反过来，若只改
        生产代码不改这里，守卫就会变成"绿灯掩盖缺口"。
        现统一转出 `models.needs_approval_for` —— 口径只有一份。
        """
        from agent.lines.models import needs_approval_for
        drifted = []
        for name, doc in _tool_docs().items():
            needs = needs_approval_for(
                str(doc.get("plane")), str(doc.get("effect")), str(doc.get("risk")))
            want = C.effective_permission_level(
                str(doc.get("effect")), str(doc.get("risk")), needs, False)
            if doc.get("permission_level") != want:
                drifted.append((name, doc.get("permission_level"), want))
        assert not drifted, (
            f"permission_level 与治理轴不一致：{drifted[:10]}\n"
            "→ 若确实要偏离治理轴，请同时改 plane/effect/risk（不要只改声明）")

    def test_manual_模式必为不可调用(self):
        bad = [n for n, d in _tool_docs().items()
               if d.get("callable_mode") == "manual" and d.get("llm_callable") is True]
        assert not bad, f"callable_mode=manual 却声明 llm_callable=true：{bad}"

    def test_不可调用必须写明原因(self):
        missing = [n for n, d in _tool_docs().items()
                   if d.get("llm_callable") is False and not str(d.get("reason") or "").strip()]
        assert not missing, f"llm_callable=false 但没写 reason：{missing}"

    def test_取值域与_models_里的一份一致(self):
        """`agent/lines/models.py` 为了避开循环 import 重列了取值域，此处对拍锁死"""
        assert tuple(M.TOOL_TYPES) == tuple(C.TOOL_TYPES)
        assert tuple(M.CALLABLE_MODES) == tuple(C.CALLABLE_MODES)
        assert tuple(M.PERMISSION_LEVELS) == tuple(C.PERMISSION_LEVELS)

    def test_技能侧覆盖表存在且默认口径是_manual(self):
        doc = yaml.safe_load(_SKILL_DECL_PATH.read_text(encoding="utf-8"))
        defaults = doc.get("defaults") or {}
        assert defaults.get("callable_mode") == "manual", (
            "技能默认口径应为 manual（技能不是模型发起的工具调用）；"
            "若已接上技能调用工具，请连同 agent/lines/callability.py 的说明一起改")
        assert defaults.get("llm_callable") is False


# ════════════════════════════════════════════════════════════
#  2. 判定层（纯函数）
# ════════════════════════════════════════════════════════════

def _judge(**over) -> dict:
    base = dict(
        declared=C.parse_declaration({"tool_type": "tool", "llm_callable": True,
                                      "callable_mode": "auto",
                                      "permission_level": "public",
                                      "sandbox_allowed": True}),
        schema_registered=True, host_executor="agent.tools.x:f",
        permission_level="public", is_internal=False, denied=False, deny_all=False,
        enabled=True,
    )
    base.update(over)
    return C.judge(**base)


class TestJudgement:

    def test_全条件满足即可被模型发起(self):
        v = _judge()
        assert v["llm_callable"] is True
        assert v["reachable"] is True
        assert v["trigger"] == "model"
        assert v["mark"] == C.MARK_CALLABLE
        assert v["reason"] == "" and v["reason_kind"] == ""

    def test_缺_schema_是可达但不由模型发起(self):
        """缺参数契约 ≠ 不可用：它仍能被系统/人工触发，故是 ⚠️ 而不是 ❌"""
        v = _judge(schema_registered=False)
        assert v["reachable"] is True
        assert v["llm_callable"] is False
        assert v["mark"] == C.MARK_CONDITIONAL
        assert v["reason_kind"] == "not_model_initiated"
        assert any("JSON Schema" in b for b in v["soft_blockers"])

    def test_缺执行器是不可达(self):
        v = _judge(host_executor="")
        assert v["reachable"] is False
        assert v["trigger"] == "none"
        assert v["mark"] == C.MARK_BLOCKED
        assert v["reason_kind"] == "unreachable"
        assert "无执行器" in v["reason"]

    def test_无内容实体是不可达(self):
        v = _judge(has_entity=False)
        assert v["mark"] == C.MARK_BLOCKED
        assert "无内容实体" in v["reason"]

    def test_被角色策略拒绝是不可达(self):
        v = _judge(denied=True, permission_level="restricted")
        assert v["reachable"] is False
        assert v["mark"] == C.MARK_BLOCKED
        assert "权限策略拒绝" in v["reason"]

    def test_全局拒绝是不可达(self):
        v = _judge(deny_all=True, permission_level="restricted")
        assert v["mark"] == C.MARK_BLOCKED
        assert "全局拒绝" in v["reason"]

    def test_声明不可调用是可达但由系统触发(self):
        decl = C.parse_declaration({"llm_callable": False, "reason": "高风险运维脚本",
                                    "callable_mode": "manual"})
        v = _judge(declared=decl)
        assert v["reachable"] is True and v["llm_callable"] is False
        assert v["trigger"] == "system"
        assert v["mark"] == C.MARK_CONDITIONAL
        assert "高风险运维脚本" in v["reason"]

    def test_内部专用是不可达(self):
        """`internal: true` 是设计上不对模型开放（也不进检索索引），不是"暂时调不动" """
        v = _judge(is_internal=True)
        assert v["reachable"] is False
        assert v["mark"] == C.MARK_BLOCKED
        assert "内部专用" in v["reason"]

    def test_审批边界只是条件可调用(self):
        v = _judge(permission_level="restricted")
        assert v["llm_callable"] is True
        assert v["trigger"] == "model"
        assert v["mark"] == C.MARK_CONDITIONAL
        assert v["reason_kind"] == "needs_approval"
        assert v["conditions"], "属审批边界必须给出条件说明"

    def test_沙箱限制只记_note_不降级标识(self):
        """沙箱适用性是与"能否被模型发起"正交的一条轴，混进标识会让 58/91 个工具变 ⚠️"""
        decl = C.parse_declaration({"llm_callable": True, "callable_mode": "auto",
                                    "sandbox_allowed": False})
        v = _judge(declared=decl)
        assert v["mark"] == C.MARK_CALLABLE
        assert any("沙箱" in n for n in v["notes"])

    def test_标识与判定同源(self):
        for over, want in (({}, C.MARK_CALLABLE),
                           ({"permission_level": "restricted"}, C.MARK_CONDITIONAL),
                           ({"schema_registered": False}, C.MARK_CONDITIONAL),
                           ({"host_executor": ""}, C.MARK_BLOCKED)):
            assert _judge(**over)["mark"] == want

    def test_硬阻断优先于软阻断(self):
        """既不可达又非模型发起时，原因以"不可达"为准（❌ 要能看出真正卡在哪）"""
        v = _judge(host_executor="", schema_registered=False)
        assert v["mark"] == C.MARK_BLOCKED
        assert v["blocker_codes"] == ["no_executor"]
        assert v["soft_codes"] == ["no_schema"]

    def test_权限等级派生(self):
        assert C.effective_permission_level("read", "low", False, False) == "public"
        assert C.effective_permission_level("write", "low", False, False) == "internal"
        assert C.effective_permission_level("execute", "medium", False, False) == "internal"
        assert C.effective_permission_level("read", "low", True, False) == "restricted"
        assert C.effective_permission_level("read", "low", False, True) == "restricted"


# ════════════════════════════════════════════════════════════
#  3. 派生清单
# ════════════════════════════════════════════════════════════

class TestManifest:

    @pytest.fixture(scope="class")
    def on_disk(self) -> dict:
        return json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))

    def test_清单存在且自洽(self, on_disk):
        errs = sync_manifest_mod.validate(on_disk)
        assert not errs, f"清单自洽性校验失败：{errs[:10]}"

    def test_清单与权威数据一致(self, on_disk):
        """手改清单必须被拦住（清单是派生物）"""
        fresh = C.build_manifest()
        assert sync_manifest_mod._diff(on_disk, fresh) == [], (
            "清单与 data/tool_definitions/*.yaml + data/skill_callability.yaml 不一致；"
            "运行 python scripts/sync_capability_manifest.py 重新派生")

    def test_清单只依赖入库数据(self, on_disk, monkeypatch, tmp_path):
        """清单必须能从"干净 checkout"复算出来（CI 口径）

        【为什么单列一条（这条是 CI 教出来的）】清单早期读了 `data/skills.json` 与
        `data/skills_mgmt.json` —— 两者都被 .gitignore 忽略，干净 checkout / CI 里不存在，
        于是 CI 重算出的清单与提交产物**必然不一致**（实测 23 处差异），
        `sync_capability_manifest.py --check` 直接红灯。故这里把两个运行时文件指到
        不存在的路径，复算结果必须与磁盘上的清单逐字段相同。
        """
        monkeypatch.setattr(C, "SKILLS_JSON_PATH", str(tmp_path / "no" / "skills.json"))
        monkeypatch.setattr(C, "SKILLS_MGMT_PATH", str(tmp_path / "no" / "skills_mgmt.json"))
        fresh = C.build_manifest()
        assert sync_manifest_mod._diff(on_disk, fresh) == [], (
            "清单依赖了 .gitignore 里的运行时文件 ⇒ 干净 checkout 下复算不一致；"
            "运行时技能目录/台账只能通过 include_runtime_catalog=True 显式并入（产物不提交）")

    def test_运行时技能不进清单口径(self, on_disk):
        """只在运行时目录/台账里的技能不在清单里，但必须被如实披露"""
        listed = {e["tool_name"] for e in on_disk["skills"]}
        only = set(on_disk.get("runtime_only_declarations") or [])
        assert not (listed & only), f"运行时技能混进了清单：{listed & only}"
        assert on_disk.get("runtime_only_note"), "缺少 runtime_only 说明"
        # 仓库实体的技能必须都在清单里（漏了就是覆盖不全）
        repo_entities = {p.parent.name for p in (_PROJECT_ROOT / "data" / "skills_repo")
                         .glob("*/skill.md")}
        assert repo_entities <= listed, f"仓库技能实体未进清单：{repo_entities - listed}"

    def test_清单条目都标了_repo_口径(self, on_disk):
        assert {e.get("scope") for e in on_disk["entries"]} == {"repo"}, (
            "提交清单里的条目都应是仓库口径（runtime 条目只由 REST 端点请求期补算）")

    def test_运行时补标注可覆盖只在台账里的技能(self, tmp_path, monkeypatch):
        """`runtime_only_skill_entries`：仓库无实体、只在运行时目录/台账里的技能要有标注

        实证案例：id=`skill`（易之三义）内容内联在 `data/skills_mgmt.json`、仓库里没有
        `data/skills_repo/skill/skill.md` ⇒ 收紧口径后界面那行**没有徽章**。
        本函数就是那条补位路径：标注如实（⚠️/由系统触发），并标 `scope=runtime`。
        """
        cat = tmp_path / "skills.json"
        mgmt = tmp_path / "skills_mgmt.json"
        cat.write_text(json.dumps({"skills": [
            {"id": "runtime_only", "name": "运行时技能", "enabled": True,
             "description": "只在运行时台账里的指令型技能", "params": {}},
        ]}, ensure_ascii=False), encoding="utf-8")
        mgmt.write_text(json.dumps({"runtime_only": {
            "id": "runtime_only", "name": "运行时技能", "status": "approved",
            "enabled": True, "content": "# 指令内容", "default_params": {},
            "config_schema": {"type": "object", "properties": {}},
        }}, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(C, "SKILLS_JSON_PATH", str(cat))
        monkeypatch.setattr(C, "SKILLS_MGMT_PATH", str(mgmt))

        entries = C.runtime_only_skill_entries(existing_names=set())
        assert [e["tool_name"] for e in entries] == ["runtime_only"]
        e = entries[0]
        for f in UNIFIED_FIELDS:
            assert f in e, f"运行时补标注缺字段 {f}"
        assert e["scope"] == "runtime"
        assert e["mark"] == C.MARK_CONDITIONAL, "有内容实体+参数契约 ⇒ 可达，但非模型发起 ⇒ ⚠️"
        assert e["trigger"] == "system"
        assert e["llm_callable"] is False
        assert str(e["reason"]).strip(), "⚠️ 必须写明触发方式"
        # 已在仓库口径里的技能不得重复补
        assert C.runtime_only_skill_entries(existing_names={"runtime_only"}) == []

    def test_每条都有八项统一字段(self, on_disk):
        for e in on_disk["entries"]:
            for f in UNIFIED_FIELDS:
                assert f in e, f"{e.get('tool_name')}: 缺字段 {f}"

    def test_工具与技能都被覆盖(self, on_disk):
        tools = [e for e in on_disk["entries"] if e["tool_type"] == "tool"]
        skills = [e for e in on_disk["entries"] if e["tool_type"] == "skill"]
        assert len(tools) == len(_tool_docs()) >= 80, "工具条数与 YAML 数不符"
        assert len(skills) >= 20, f"技能条数过少：{len(skills)}"
        assert {e["tool_name"] for e in tools} == set(_tool_docs())

    def test_不可调用都有原因(self, on_disk):
        for e in on_disk["entries"]:
            if not e["llm_callable"]:
                assert str(e["reason"]).strip(), f"{e['tool_name']} 不可调用但没写原因"

    def test_可调用必有_schema_与执行器(self, on_disk):
        for e in on_disk["entries"]:
            if e["llm_callable"]:
                assert e["schema_registered"], f"{e['tool_name']}: 无 schema 却可调用"
                assert e["host_executor"], f"{e['tool_name']}: 无执行器却可调用"

    def test_统计与条目一致(self, on_disk):
        counts = on_disk["counts"]
        entries = on_disk["entries"]
        assert counts["total"] == len(entries)
        for mark, key in ((C.MARK_CALLABLE, "callable"),
                          (C.MARK_CONDITIONAL, "conditional"),
                          (C.MARK_BLOCKED, "blocked")):
            assert counts[key] == sum(1 for e in entries if e["mark"] == mark)
        for trig in C.TRIGGERS:
            assert counts["by_trigger"][trig] == sum(
                1 for e in entries if e.get("trigger") == trig)

    def test_技能是可达但由系统触发(self, on_disk):
        """本项锁住"技能为什么不是 ❌"：它们照常生效，只是不由模型发起

        反面教材（改动前）：31 个技能全被标成 ❌，看上去像"技能全坏了"。
        分界是"能不能被执行"（可达），不是"模型能不能发起"。
        """
        skills = on_disk["skills"]
        assert len(skills) >= 20
        for e in skills:
            if e["mark"] == C.MARK_BLOCKED:
                # 只有硬阻断（无实体 / 停用 / 无执行器）才允许 ❌
                assert e["blocker_codes"], f"{e['tool_name']}: ❌ 但说不出硬阻断"
                continue
            assert e["reachable"] is True, f"{e['tool_name']}: 可达性判断缺失"
            assert e["trigger"] == "system", f"{e['tool_name']}: 技能触发者应为 system"
            assert e["callable_mode"] == "manual"
            assert e["llm_callable"] is False, "技能不由模型发起，这一字段不许放宽"
            assert str(e["reason"]).strip(), f"{e['tool_name']}: ⚠️ 必须写明触发方式"

    def test_标识分界只由可达性决定(self, on_disk):
        """❌ ⇔ 不可达；⚠️ ⇔ 可达但没有模型可发起的完整条件"""
        for e in on_disk["entries"]:
            if e["mark"] == C.MARK_BLOCKED:
                assert e["reachable"] is False and e["blocker_codes"]
            elif e["mark"] == C.MARK_CONDITIONAL:
                assert e["reachable"] is True
                assert (e["soft_blockers"] or e["conditions"])
            else:
                assert e["llm_callable"] is True and not e["conditions"]

    def test_内部专用工具标不可达(self, on_disk):
        entry = {e["tool_name"]: e for e in on_disk["entries"]}["process_distill_run"]
        assert entry["mark"] == C.MARK_BLOCKED
        assert "internal_only" in entry["blocker_codes"]
        assert entry["trigger"] == "none"

    def test_受控技能的执行器指向真实链路(self, on_disk):
        scripted = [e for e in on_disk["skills"] if e.get("has_scripts")]
        assert scripted, "样本里应至少有一个带脚本技能，否则本断言形同虚设"
        for e in scripted:
            assert "SkillExecutor" in e["host_executor"]
        plain = [e for e in on_disk["skills"] if not e.get("has_scripts")]
        assert plain and all("ContextInjector" in e["host_executor"] for e in plain)

    def test_静态扫描能认出执行器(self):
        """`host_executor` 的默认口径是静态注册点扫描（确定性、CI 可用）"""
        execs = C.static_executors()
        assert len(execs) >= 80, f"静态扫描命中的注册点过少：{len(execs)}"
        assert execs.get("shell_execute", "").startswith("agent.tools.")
        assert execs.get("kb_capture", "").startswith("agent.knowledge.tools")

    def test_不与_agent_tools_形成循环依赖(self):
        """`agent.lines.callability` 不得导入 `agent.tools`（架构规则 no_circular_dependency）

        静默回归的代价很具体：CI 的 architecture-check 会红灯（实测过一次），
        而本地单测全绿 —— 故把这条不变量钉在单测里。
        """
        src = (_PROJECT_ROOT / "agent" / "lines" / "callability.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and str(node.module or "").startswith("agent.tools"):
                offenders.append(f"line {node.lineno}: from {node.module}")
            elif isinstance(node, ast.Import):
                offenders += [f"line {node.lineno}: import {a.name}" for a in node.names
                              if a.name.startswith("agent.tools")]
        assert not offenders, (
            f"callability.py 不得导入 agent.tools（循环依赖）：{offenders}\n"
            "→ 运行时执行器事实由调用方注入（build_manifest(executor_facts=...)）")

    def test_注入的运行时事实优先于静态扫描(self):
        """依赖倒置后仍要能拿到运行时事实：调用方注入 ⇒ 覆盖静态扫描结果"""
        docs = C.load_tool_docs()
        name = sorted(docs)[0]
        manifest = C.build_manifest(executor_facts={name: "runtime.module:handler"})
        row = {e["tool_name"]: e for e in manifest["entries"]}[name]
        assert row["host_executor"] == "runtime.module:handler"


# ════════════════════════════════════════════════════════════
#  4. 接线层：模型可见集 / 检索索引 / REST
# ════════════════════════════════════════════════════════════

def _write_tool(path: Path, name: str, **fields) -> None:
    doc = {
        "name": name, "category": "core", "description": f"{name} 测试用",
        "deprecated": False, "version": "1.0.0",
        "schema": {"type": "object", "properties": {}},
        "examples": [], "plane": "perceive", "effect": "read", "risk": "low",
        "tool_type": "tool", "llm_callable": True, "callable_mode": "auto",
        "permission_level": "public", "sandbox_allowed": True,
    }
    doc.update(fields)
    path.joinpath(f"{name}.yaml").write_text(
        yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")


class TestRuntimeFilter:

    def test_当前判否集合是_internal_的子集(self):
        """上线时点的零行为变化锁：本开关打开前后，模型可见集必须**完全相同**

        若将来有意把某个非 internal 工具标成不可调用，这条断言会失败 —— 那是**故意的**：
        模型可见集变化必须是被批准的显式动作，改这里时要一并写进交付说明。
        """
        from agent import tools as T
        non_callable = set(T._non_callable_names())
        internal = set(T._internal_tool_names())
        assert non_callable <= internal, (
            f"新增了非 internal 的不可调用工具：{sorted(non_callable - internal)}；"
            "请确认这是有意的（会缩小模型可见集），并在交付说明里写明")

    def test_get_tool_defs_隐藏声明判否的工具(self, tmp_path, monkeypatch):
        from agent import tools as T
        _write_tool(tmp_path, "callable_a")
        _write_tool(tmp_path, "manual_b", llm_callable=False, callable_mode="manual",
                    reason="仅人工调用")
        monkeypatch.setattr(C, "TOOL_DEFS_DIR", str(tmp_path))
        monkeypatch.delenv("CP_TOOL_CALLABILITY_ENFORCE", raising=False)
        T.clear()
        try:
            T.register("callable_a", "A", schema={"type": "object", "properties": {}},
                       handler=lambda **k: None)
            T.register("manual_b", "B", schema={"type": "object", "properties": {}},
                       handler=lambda **k: None)
            assert sorted(T._non_callable_names()) == ["manual_b"]
            names = {d["function"]["name"] for d in T.get_tool_defs()}
            assert names == {"callable_a"}, "声明不可调用的工具仍在模型可见集里"
        finally:
            T.clear()

    def test_开关置零退回只隐藏_internal(self, tmp_path, monkeypatch):
        from agent import tools as T
        _write_tool(tmp_path, "manual_b", llm_callable=False, callable_mode="manual",
                    reason="仅人工调用")
        monkeypatch.setattr(C, "TOOL_DEFS_DIR", str(tmp_path))
        monkeypatch.setenv("CP_TOOL_CALLABILITY_ENFORCE", "0")
        T.clear()
        try:
            T.register("manual_b", "B", schema={"type": "object", "properties": {}},
                       handler=lambda **k: None)
            assert T._non_callable_names() == frozenset()
            names = {d["function"]["name"] for d in T.get_tool_defs()}
            assert names == {"manual_b"}, "回滚开关失效"
        finally:
            T.clear()

    def test_registry_facts_报告_schema_与执行器(self):
        from agent import tools as T
        T.clear()
        try:
            T.register("with_schema", "有 schema",
                       schema={"type": "object", "properties": {}},
                       handler=lambda **k: None)
            facts = T.registry_facts()
            assert facts["with_schema"]["schema_registered"] is True
            assert ":" in facts["with_schema"]["host_executor"]
            T.register("no_schema", "无 schema", handler=lambda **k: None)
            assert T.registry_facts()["no_schema"]["schema_registered"] is False
        finally:
            T.clear()


class TestIndexGate:

    def test_索引排除判否工具(self):
        assert sync_index_mod._is_hidden({"internal": True}) is True
        assert sync_index_mod._is_hidden({"llm_callable": False}) is True
        assert sync_index_mod._is_hidden({"callable_mode": "manual"}) is True
        assert sync_index_mod._is_hidden({"llm_callable": True, "callable_mode": "auto"}) is False

    def test_真实索引里没有判否工具(self):
        index = json.loads((_PROJECT_ROOT / "data" / "tool_index.json").read_text(encoding="utf-8"))
        indexed = {t["name"] for t in index["tools"]}
        hidden = {n for n, d in _tool_docs().items() if sync_index_mod._is_hidden(d)}
        assert not (indexed & hidden), f"判否工具泄漏进检索索引：{indexed & hidden}"


@pytest.fixture(scope="module")
def real_app():
    """真实 Flask app（与生产同一份注册代码）—— 手搓 `Flask(__name__)` 会掩盖 404

    【不易·本夹具很贵，只在 slow 车道用】导入 `app_server` 会连带初始化 torch /
    sentence-transformers 等重依赖（实测让本文件从 ~10s 涨到 ~90s）。而 ci.yml 的单元测试
    分片是 `-n 2` 并行 + 贪心按用例数均衡 ⇒ 这份重量会挤到同分片的邻居（实测
    `test_skill_merge` 在负载下从 2.17s 涨到 >60s 超时）。故本类整体标 `slow`：
    ci.yml 的 `-m "not slow"` 会跳过它，由 `full-regression.yml --runslow` 单独跑；
    快速车道上保留 `TestRestSurface::test_端点已在路由模块里声明` 这条轻量守门。
    """
    import app_server
    return app_server.app


class TestRestSurfaceStatic:
    """快速车道上的 REST 面守门（不导入 app_server，开销 ~0）

    【为什么要有这一层】真实 app 的验证在 `TestRestSurface`（slow 车道）—— 它是权威，
    但很贵（导入 app_server 连带 torch/sentence-transformers），不能塞进 `-n 2` 的单元测试
    分片。这里用**源码声明**做一次廉价的存在性检查：端点装饰器不见了就立刻红，
    不必等到 slow 车道。两条的判定口径不同（一条读源码、一条读真实 url_map），
    故不构成"两份口径"，而是"快慢两级守门"。
    """
    _ROUTE_SRC = _PROJECT_ROOT / "agent" / "server_routes" / "routes_agent_lines.py"

    def test_端点已在路由模块里声明(self):
        src = self._ROUTE_SRC.read_text(encoding="utf-8")
        assert '"/api/agent-lines/planes"' in src
        assert '"/api/capability-manifest"' in src, (
            "新端点从 routes_agent_lines.py 里消失了 —— 这正是 /api/agent-lines 曾 404 的原因；"
            "真实 app 的复核见 TestRestSurface（slow 车道）")
        assert "@app.route" in src


@pytest.mark.slow
class TestRestSurface:
    """REST 面的**真实 app** 验证（slow 车道；见 `real_app` 夹具的代价说明）"""

    def test_两个端点在真实_app_里存在(self, real_app):
        rules = {str(r.rule) for r in real_app.url_map.iter_rules()}
        assert "/api/agent-lines/planes" in rules
        assert "/api/capability-manifest" in rules, (
            "新端点在真实 app 里不存在 —— 这正是 /api/agent-lines 曾经 404 的原因")

    def test_planes_每行带可调用性标注(self, real_app):
        resp = real_app.test_client().get("/api/agent-lines/planes")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        rows = body["tools"]
        assert rows, "工具目录为空"
        annotated = [r for r in rows if r.get("callability")]
        assert len(annotated) >= 80, f"带标注的行过少：{len(annotated)}/{len(rows)}"
        for r in annotated:
            assert r["callability"]["mark"] in MARKS
        assert sum(body["callability_marks"].values()) == len(annotated)

    def test_清单端点回得出八字段与统计(self, real_app):
        resp = real_app.test_client().get("/api/capability-manifest")
        assert resp.status_code == 200
        body = resp.get_json()
        doc = body["manifest"]
        assert doc["counts"]["total"] == len(doc["entries"]) > 100
        sample = doc["entries"][0]
        for f in UNIFIED_FIELDS:
            assert f in sample

    def test_清单端点附运行时技能补标注(self, real_app):
        """`runtime_skills`：运行时目录/台账里才有、仓库无实体的技能（如 id=skill 易之三义）

        【CI 安全】CI 的干净 checkout 里 `data/skills.json` / `skills_mgmt.json` 不存在
        （两者都被 .gitignore 忽略）⇒ 该字段为空数组，本断言仍成立；有内容时逐条校验字段。
        """
        resp = real_app.test_client().get("/api/capability-manifest")
        assert resp.status_code == 200
        body = resp.get_json()
        assert "runtime_skills" in body and isinstance(body["runtime_skills"], list)
        assert body.get("runtime_note")
        for e in body["runtime_skills"]:
            for f in UNIFIED_FIELDS:
                assert f in e, f"运行时技能 {e.get('tool_name')} 缺字段 {f}"
            assert e["scope"] == "runtime"
            assert e["mark"] in MARKS
        listed = {e["tool_name"] for e in body["manifest"]["skills"]}
        assert not (listed & {e["tool_name"] for e in body["runtime_skills"]}), (
            "同一技能不得同时出现在清单文件与运行时补标注里（重复会掩盖口径问题）")

    def test_装配预览与目录的标注同源(self, real_app):
        """预览面板与工具目录必须显示**同一份**标注（否则就是两份口径）

        历史教训同型：本仓的"十三处工具真相"都是这么长出来的 —— 每个视图自己算一遍，
        然后各说各话。此断言把两条读取路径钉在同一份派生清单上。
        """
        client = real_app.test_client()
        catalog = client.get("/api/agent-lines/planes").get_json()
        rows = {r["name"]: r.get("callability") or {} for r in catalog["tools"]}
        assert rows, "工具目录为空，本断言会假通过"

        lines = client.get("/api/agent-lines").get_json()["lines"]
        assert lines, "data/agent_lines 下没有可用的主线档案，无法验证预览"
        profile = lines[0]
        resp = client.post("/api/agent-lines/preview", json=profile)
        assert resp.status_code == 200, resp.get_json()
        preview = resp.get_json()["preview"]

        meta = preview["tools_meta"]
        assert meta, "预览未回传 tools_meta"
        assert preview["callability_source"] == "data/capability_manifest.json"

        compared = 0
        for name, row in meta.items():
            assert "callability" in row, f"{name}: 预览行缺 callability 字段"
            if name in rows and rows[name]:
                assert row["callability"] == rows[name], (
                    f"{name}: 预览与目录的标注不一致（两份口径）")
                compared += 1
        assert compared >= 5, f"参与对拍的工具有 {compared} 个，样本过少"

    def test_预览的_meta_覆盖被拒与被截断的工具(self, real_app):
        """预告 trace 里出现的每组工具都应能查到元数据（否则 chip 缺 plane 与标识）"""
        client = real_app.test_client()
        lines = client.get("/api/agent-lines").get_json()["lines"]
        preview = client.post("/api/agent-lines/preview",
                              json=lines[0]).get_json()["preview"]
        meta = preview["tools_meta"]
        declared = set(_tool_docs())
        for key in ("tools", "needs_approval", "muted", "truncated",
                    "denied_by_effect", "denied_unknown"):
            for name in preview.get(key) or []:
                if name in declared:
                    assert name in meta, f"{key} 里的 {name} 没有元数据"


# ════════════════════════════════════════════════════════════
#  5. `--check` 守门脚本可运行（CI 会跑的那两条命令）
# ════════════════════════════════════════════════════════════

class TestGateScripts:

    def test_回填脚本_check_通过(self):
        import subprocess
        import sys
        r = subprocess.run([sys.executable, "scripts/backfill_tool_callability.py", "--check"],
                           cwd=str(_PROJECT_ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        assert r.returncode == 0, f"回填守门失败：{r.stdout}\n{r.stderr}"

    def test_清单同步_check_通过(self):
        import subprocess
        import sys
        r = subprocess.run([sys.executable, "scripts/sync_capability_manifest.py", "--check"],
                           cwd=str(_PROJECT_ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        assert r.returncode == 0, f"清单守门失败：{r.stdout}\n{r.stderr}"
