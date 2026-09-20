"""TASK-04 · CapabilitySpec + location + 盘点表 的守门测试

【为什么这些用例必须存在（D8：有测试才算完成）】
    TASK-04 把能力从"隐式约定"变成"显式规格"，它守的每一条不变量都是**回归风险点**：

      1. `CapabilitySpec` 只在 `agent/lines/models.py` 单点定义（E1）——多一个类就是第二真相源；
      2. 114 条能力全部有 `location`/`kind`/`owner`/`version`/`capability_id`（E2）；
      3. 4 个 MCP 管理面工具的 `location` 判定依据是**事实**（E3）；
      4. `location` 声明与事实不一致时 `--check` 必须非零退出（E4）；
      5. 已知假能力不得出现在"可用能力"集合里（E5）；
      6. 表驱动注册的 6 个 `kb_*` 必须在清单里（E11 的静态扫描盲区）；
      7. 同名冲突必须**可见**且**未被静默改名为 `_2`**（E11）；
      8. 主线装配结果必须纳入清单（E12）——`web_search` 在 engineering 主线里是 `muted`；
      9. 无效配置开关与技能侧实施约束必须被显式标注（E13 / E14）；
     10. `tenant_id` 不可由客户端指定（E8）。

【口径】与 `test_tool_callability.py` 一致：默认读**已落盘的清单**（快），
只有"清单与权威一致"这类必须复算的用例才调 `build_manifest()`。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST_PATH = _PROJECT_ROOT / "data" / "capability_manifest.json"
_TOOL_DEFS = _PROJECT_ROOT / "data" / "tool_definitions"
_INVENTORY = _PROJECT_ROOT / "docs" / "rfc" / "云枢能力清单盘点表.md"

from agent.lines import callability as C  # noqa: E402
from agent.lines import location as L  # noqa: E402
from agent.lines import models as M  # noqa: E402
from agent.lines.location import judge_executor_location  # noqa: E402

#: MCP **管理面**四工具（TASK-04 §2.3(a)：风险语义最集中、归属标签最不准的 4 个）
_MCP_ADMIN = ("scan_mcp", "connect_mcp", "disconnect_mcp", "list_mcp_connections")


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def entries(manifest) -> dict:
    return {e["tool_name"]: e for e in manifest["entries"]}


# ════════════════════════════════════════════════════════════
#  1. E1 单一真相源
# ════════════════════════════════════════════════════════════

class TestSingleSourceOfTruth:

    def test_能力定义只有一个结构(self):
        """`CapabilitySpec` 必须就是 `ToolMeta`，不得存在第二个能力定义类

        【为什么单列一条】TASK-04 的「不通过」条件第一条就是
        "新建一个 CapabilitySpec 类而 ToolMeta 继续独立存在且两者不一致"。
        这条测试用**源码扫描**守住它（而不是靠约定）。
        """
        text = (_PROJECT_ROOT / "agent" / "lines" / "models.py").read_text(encoding="utf-8")
        assert re.search(r"^class ToolMeta\b", text, re.M), "ToolMeta 定义不见了"
        # 全仓不得出现第二个承载"能力定义"的 dataclass
        offenders = []
        for path in (_PROJECT_ROOT / "agent").rglob("*.py"):
            if "__pycache__" in str(path):
                continue
            body = path.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"^class CapabilitySpec\b", body, re.M):
                offenders.append(str(path.relative_to(_PROJECT_ROOT)))
        assert offenders == [], f"出现了第二个能力定义结构：{offenders}（违反 D1）"

    def test_取值域与_models_里的一份一致(self):
        """`callability` 与 `models` 的取值域必须逐项相同（两处并列是为了避免循环 import）"""
        assert tuple(C.TOOL_TYPES) == tuple(M.TOOL_TYPES)
        assert tuple(C.CALLABLE_MODES) == tuple(M.CALLABLE_MODES)
        assert tuple(C.PERMISSION_LEVELS) == tuple(M.PERMISSION_LEVELS)
        assert tuple(C.LOCATIONS) == tuple(M.LOCATIONS)
        assert tuple(C.OWNERS) == tuple(M.OWNERS)
        assert tuple(C.KINDS) == tuple(M.KINDS)

    def test_location_判定器不导入_agent_tools(self):
        """判定器不得与 `agent.tools` 形成循环依赖（架构规则 no_circular_dependency）"""
        text = (_PROJECT_ROOT / "agent" / "lines" / "location.py").read_text(encoding="utf-8")
        assert "from agent.tools" not in text
        assert "import agent.tools" not in text


# ════════════════════════════════════════════════════════════
#  2. 必填字段与默认值（D2 向后兼容）
# ════════════════════════════════════════════════════════════

class TestCapabilitySpecFields:

    def test_所有新字段都有默认值(self):
        """只给 `name` 也必须能构造出完整的 CapabilitySpec（D2：新增字段一律可选）"""
        meta = M.ToolMeta(name="x")
        assert meta.location == "remote", "location 的默认值必须是保守侧 remote"
        assert meta.owner == "builtin"
        assert meta.version == "1.0.0"
        assert meta.tenant_id == "default"
        assert meta.namespace == "yunshu"
        assert meta.registry_source == "global"
        assert meta.manifest_version == 1
        assert meta.signature == "" and meta.source_trust == ""
        assert meta.input_schema is None and meta.output_schema is None
        assert meta.deprecated is False
        assert meta.aliases == ()

    def test_to_dict_只增不减(self):
        """`to_dict()` 必须保留旧的 14 个键（既有消费者靠它们）"""
        d = M.ToolMeta(name="x").to_dict()
        for key in ("name", "category", "plane", "effect", "risk", "tags",
                    "needs_approval", "internal", "tool_type", "llm_callable",
                    "callable_mode", "permission_level", "sandbox_allowed", "reason"):
            assert key in d, f"to_dict() 丢了既有键 {key!r}（违反 D2）"

    def test_input_schema_是_schema_的别名(self):
        """YAML 的 `schema:` 就是 `input_schema`（做别名，不改名）"""
        meta = M.load_tool_meta(force=True)["shell_execute"]
        assert meta.input_schema is not None
        assert meta.schema is meta.input_schema
        assert meta.result_schema is meta.output_schema

    def test_capability_id_构造规则(self):
        meta = M.ToolMeta(name="demo", version="2.1.0", tenant_id="t1", namespace="ns")
        assert meta.capability_id == "t1:ns:demo@2.1.0"

    def test_tool_name_别名兼容(self, entries):
        """`capability_id` 之外必须保留 `tool_name`（UI 与 agent/lines 都在用它）"""
        for name, e in entries.items():
            assert e["tool_name"] == name
            assert e["capability_id"].endswith(f":{name}@1.0.0") or "@" in e["capability_id"]

    def test_kind_归并规则(self):
        assert M.ToolMeta(name="a", tool_type="api").kind == "tool"
        assert M.ToolMeta(name="a", tool_type="script").kind == "skill"
        assert M.ToolMeta(name="a", tool_type="tool").kind == "tool"
        assert M.ToolMeta(name="a", tool_type="skill").kind == "skill"

    def test_91_条_YAML_都能解析且无一条崩溃(self):
        metas = M.load_tool_meta(force=True)
        assert len(metas) == 91, f"工具 YAML 数量变了（实测应为 91）：{len(metas)}"
        for name, meta in metas.items():
            d = meta.to_dict()          # 不抛异常即通过
            assert d["capability_id"], f"{name}: capability_id 为空"
            assert d["location"] in M.LOCATIONS, f"{name}: location 非法"


# ════════════════════════════════════════════════════════════
#  3. E2 全部能力都有 location
# ════════════════════════════════════════════════════════════

class TestLocationCoverage:

    def test_114_条全部有_location(self, manifest):
        assert len(manifest["entries"]) == 114
        for e in manifest["entries"]:
            assert e.get("location") in ("local", "remote"), \
                f"{e['tool_name']}: location 缺失或非法"
            assert e.get("location_source") in L.LOCATION_SOURCES, \
                f"{e['tool_name']}: location_source 非法: {e.get('location_source')!r}"

    def test_零条使用默认兜底(self, manifest):
        """E2：不得有条目靠"保守默认"兜底；若出现必须逐条说明

        `location_source=default` 只在 `host_executor` 为空时出现（那是真问题，
        必须在盘点表里逐条写明原因）。当前应为 **0 条**。
        """
        fallback = [e["tool_name"] for e in manifest["entries"]
                    if e.get("location_source") == "default"]
        assert fallback == [], (
            f"以下条目用了 location 保守默认兜底，需逐条说明原因：{fallback}")

    def test_分布与统计一致(self, manifest):
        by_loc = manifest["counts"]["by_location"]
        for loc in ("local", "remote"):
            assert by_loc[loc] == sum(1 for e in manifest["entries"]
                                      if e["location"] == loc)
        assert by_loc["local"] + by_loc["remote"] == 114

    def test_每条都有证据(self, manifest):
        """判定不接受黑箱结论：每条都必须有可核的 `location_evidence`"""
        for e in manifest["entries"]:
            assert e.get("location_evidence"), f"{e['tool_name']}: 缺少 location 判定依据"
            assert isinstance(e["location_evidence"], list)


# ════════════════════════════════════════════════════════════
#  4. E3 location 事实判定（≥6 个已知能力）
# ════════════════════════════════════════════════════════════

class TestLocationJudge:

    @pytest.mark.parametrize("name", _MCP_ADMIN)
    def test_四个_MCP_管理工具是_remote(self, name):
        """E3：4 个 MCP 管理面工具的 location 必须是 remote

        【判定依据（事实，不是人工声明）】
          · `connect_mcp` / `scan_mcp`：调用链进入 `agent.tools.mcp_connector`，
            命中 `urllib.request.urlopen`（HTTP 传输）与 `MCPClient`（`asyncio.create_subprocess_exec`，stdio 传输）
          · `disconnect_mcp` / `list_mcp_connections`：链路进入**跨边界客户端类**
            `McpConnector`（同类其它方法直接含边界原语）——它们管理的是跨 stdio/HTTP 的连接。
        【不易】这 4 个是 MCP 的「管理面」，**不是 MCP 客户端调用**。
        """
        docs = C.load_tool_docs()
        declared = C.parse_declaration(docs[name])
        executor = declared["host_executor"] or C.static_executors().get(name, "")
        res = judge_executor_location(
            executor, registry_source=C.static_registration_sources().get(name, ""))
        assert res["location"] == "remote", f"{name}: 期望 remote，实得 {res['location']}"
        assert res["evidence"], f"{name}: 缺少判定依据"

    def test_四个_MCP_管理工具的注册来源已修(self):
        """E3 要求**修事实源**（`register()` 带上了 `source`），不是在派生层硬编码"""
        src = C.static_registration_sources()
        for name in _MCP_ADMIN:
            assert src.get(name) == "mcp_admin", (
                f"{name}: 注册点没有显式 source（期望 mcp_admin），实得 {src.get(name)!r}")

    def test_注册来源常量不与_MCP_动态工具冲突(self):
        """`SOURCE_MCP_ADMIN` 必须独立于 `SOURCE_MCP`

        【为什么】`unregister_by_source("mcp")`（MCP 断连时的批量清理，
        见 tests/test_dynamic_tools.py:143,488）会把同来源的工具一并注销 ⇒
        若管理面工具标成 `mcp`，清理远端工具会误伤"管理远端的能力"。
        """
        tools_init = (_PROJECT_ROOT / "agent" / "tools" / "__init__.py").read_text(encoding="utf-8")
        assert 'SOURCE_MCP_ADMIN = "mcp_admin"' in tools_init
        assert 'SOURCE_MCP = "mcp"' in tools_init

    def test_subprocess_类能力是_remote(self):
        """subprocess 调 CLI ⇒ remote（v1.4 §2.5 第一行）"""
        res = judge_executor_location("agent.tools.shell_tools:execute_shell")
        assert res["location"] == "remote"
        assert "subprocess.run" in res["evidence"][0]

    def test_进程内_SQLite_是_local(self):
        """进程内数据库直连 ⇒ local（与 Postgres/5432 的 remote 相对）"""
        res = judge_executor_location("agent.tools.db_tools:_sqlite_query")
        assert res["location"] == "local"

    def test_网络客户端是_remote(self):
        """HTTP 出口（含 localhost）⇒ remote"""
        res = judge_executor_location("agent.web.http_client:request")
        assert res["location"] == "remote"
        assert "requests.Session" in res["evidence"][0]

    def test_同一模块内不同工具可以判不同位置(self):
        """**不能靠"模块级"归类**：`test_tools.py` 里 `run_tests` 起子进程、`apply_patch` 不起

        【为什么单列一条】判定器早期版本对"执行器所在模块含边界原语"就整模块判 remote，
        结果 `apply_patch` 被误判成 remote（实测）。函数级链式判定才正确。
        """
        assert judge_executor_location("agent.tools.test_tools:_run_tests")["location"] == "remote"
        assert judge_executor_location("agent.tools.test_tools:_apply_patch")["location"] == "local"

    def test_FFI_分类常量(self):
        """FFI 的安全归类（本任务必须明确，见 CapabilitySpec规范.md §7 缺陷 2）

        · **同进程** FFI（ctypes / win32gui / win32clipboard）⇒ local（v1.4 §2.5 铁律）
        · **出进程**的 COM/DCOM/WMI ⇒ remote（跨进程 RPC，不属"同进程 FFI"那一行）
        """
        assert "ctypes.CDLL" in L._LOCAL_FFI
        assert "wmi.WMI" in L._REMOTE_FFI
        assert "win32com.client.Dispatch" in L._REMOTE_FFI
        # 出进程 FFI 必须计入"跨边界"集合，同进程 FFI 必须**不**计入
        assert set(L._REMOTE_FFI) <= set(L._BOUNDARY_CALLS)
        assert not (set(L._LOCAL_FFI) & set(L._BOUNDARY_CALLS))

    def test_技能侧_带脚本是_remote_纯提示词是_local(self):
        """技能侧 location 由**事实**判（有无脚本目录 ⇒ 有无 subprocess 执行面）"""
        assert L.judge_skill_location("s", {"has_scripts": True})["location"] == "remote"
        assert L.judge_skill_location("s", {"has_scripts": False})["location"] == "local"

    def test_声明与事实不一致被标出(self):
        """声明钉住值与事实判定不符 ⇒ `consistent=False`（`--check` 据此非零退出）"""
        res = judge_executor_location("agent.tools.shell_tools:execute_shell", declared="local")
        assert res["location"] == "local"          # 声明值被采纳
        assert res["consistent"] is False          # 但必须标出不一致
        assert any("不一致" in x for x in res["evidence"])


# ════════════════════════════════════════════════════════════
#  5. E4 / E10 三层校验与同源产物
# ════════════════════════════════════════════════════════════

class TestThreeLayerCheck:

    def test_清单自洽性校验通过(self, manifest):
        import scripts.sync_capability_manifest as S
        assert S.validate(manifest) == []

    def test_清单与权威数据一致(self, manifest):
        """手改清单/盘点表会被拦住（两者都是派生物）"""
        import scripts.sync_capability_manifest as S
        fresh = C.build_manifest()
        assert S._diff(manifest, fresh) == [], (
            "清单与权威数据不一致；运行 python scripts/sync_capability_manifest.py 重新派生")

    def test_盘点表与清单同源(self, manifest):
        """E10：Markdown 盘点表与 JSON 由同一脚本从同一数据源生成"""
        import scripts.sync_capability_manifest as S
        assert _INVENTORY.exists(), f"盘点表不存在：{_INVENTORY}"
        on_disk = _INVENTORY.read_text(encoding="utf-8")
        rendered = S.render_inventory_markdown(manifest)
        assert S._strip_generated_at(on_disk) == S._strip_generated_at(rendered), (
            "盘点表与清单不同源（手改了 Markdown 或没重跑脚本）")

    def test_location_负例能被_validate_拦住(self, manifest):
        """E4：故意把某条能力的 location 手改成错误值 ⇒ 校验必须失败

        【为什么用"声明 vs 事实"这条路径】它才是**永远有效**的负例：
        手改 YAML 的 location 会让 `location_consistent=False`，
        `validate()` 明确报错；而单纯手改 JSON 会被 `_diff` 拦住。
        两条路径都由 `scripts/sync_capability_manifest.py --check` 转成非零退出码。
        """
        import copy
        import scripts.sync_capability_manifest as S
        broken = copy.deepcopy(manifest)
        target = next(e for e in broken["entries"] if e["tool_name"] == "read_file")
        target["location_declared"] = "remote"    # 声明与事实（local）不符
        target["location_consistent"] = False
        errs = S.validate(broken)
        assert any("location 声明与事实判定不一致" in e for e in errs), (
            f"location 不一致没有被 validate 拦住：{errs[:5]}")

    def test_假能力在可用集合外(self, manifest):
        """E5：8 个已知案例不得出现在"可用能力"集合里"""
        import scripts.sync_capability_manifest as S
        assert S.validate(manifest) == []
        available = set(manifest["available_names"])
        # 7 条根本不在清单口径内
        for name in ("register_knowledge_audit_job", "mcp_executor.McpClient._mock_call",
                     "yunshu_mcp_bridge", "agent.skills_mgmt.mcp_adapter"):
            assert name not in available
        # schedule_task 在清单里，但必须被降级（执行体是空实现）
        entry = next(e for e in manifest["entries"] if e["tool_name"] == "schedule_task")
        assert entry["mark"] != C.MARK_CALLABLE, "schedule_task 是空实现，不得标 ✅"
        assert "hollow_executor" in entry["soft_codes"]
        assert "schedule_task" not in available


# ════════════════════════════════════════════════════════════
#  6. E11 注册点盲区与同名冲突
# ════════════════════════════════════════════════════════════

class TestRegistryBlindSpots:

    def test_表驱动注册的_6_个_kb_工具在清单里(self, entries):
        """`agent/knowledge/tools.py:297-298` 遍历 `_TOOL_DEFS` 注册 6 个 `kb_*`

        【为什么单列一条】这叫**表驱动注册**，会逃过所有"找 `register("字面调用"`"的扫描。
        若判定器漏掉它，`location` 就会给它们兜底成默认值（E11）。
        """
        kb = sorted(n for n in entries if n.startswith("kb_"))
        assert kb == ["kb_capture", "kb_card", "kb_discuss", "kb_distill", "kb_lint",
                      "kb_search"], f"表驱动注册的 kb_* 工具未全进清单：{kb}"
        for name in kb:
            assert entries[name]["host_executor"], f"{name}: 缺少执行器"
            assert entries[name]["location_source"] != "default", \
                f"{name}: location 走了默认兜底（说明判定器漏了表驱动注册）"

    def test_三组同名冲突逐组定案(self, manifest):
        """E11：3 组同名必须**可见**（带 registry_source），且**未被静默改名为 `_2`**"""
        groups = {g["name"]: g for g in manifest["same_name_conflicts"]}
        assert set(groups) == {"get_status", "search_memory", "get_sensor_summary"}
        for name, g in groups.items():
            srcs = {d["registry_source"] for d in g["definitions"]}
            assert srcs == {"global", "planning"}, f"{name}: 两个来源未同时登记：{srcs}"
            assert g["silently_renamed"] is False, f"{name}: 不得静默改名为 _2"
        # 清单里不得出现 `_2` / `_3` 形态的自动改名产物
        auto = [n for n in (e["tool_name"] for e in manifest["entries"])
                if re.search(r"_\d+$", n)]
        assert auto == [], f"出现了自动数字后缀（静默改名的证据）：{auto}"

    def test_planning_注册表的_5_个工具带标记(self, manifest):
        rv = manifest["registry_variants"]
        names = sorted(v["tool_name"] for v in rv)
        assert names == ["check_health", "get_sensor_summary", "get_status",
                         "llm_chat", "search_memory"], f"planning 工具清单不符：{names}"
        for v in rv:
            assert v["registry_source"] == "planning"
            assert v["declared_in"].endswith("core_tools.py")

    def test_注册期同名冲突有结构化记录(self):
        """`register()` / `register_dynamic()` 的同名冲突必须**可查**（不是只打日志）"""
        tools_init = (_PROJECT_ROOT / "agent" / "tools" / "__init__.py").read_text(encoding="utf-8")
        assert "def name_conflicts()" in tools_init
        assert '"kind": "overwrite"' in tools_init
        assert '"kind": "renamed"' in tools_init


# ════════════════════════════════════════════════════════════
#  7. E12 主线装配结果
# ════════════════════════════════════════════════════════════

class TestMainLine:

    def test_主线可见集与装配实跑一致(self, manifest):
        line = manifest["main_line"]
        assert line["line_id"] == "engineering"
        assert len(line["visible"]) == 26, f"engineering 主线可见集应为 26 条，实得 {len(line['visible'])}"
        assert set(line["muted"]) == {"web_extract", "web_search", "web_batch"}
        assert "connect_mcp" in line["denied_by_effect"]

    def test_web_search_被如实标注为当前主线不可见(self, manifest, entries):
        """E12：不得因为 `web_search` 在 YAML 里存在就当作"模型能用它\""""
        assert entries["web_search"]["main_line_status"] == "muted"
        assert "web_search" not in set(manifest["main_line"]["visible"])

    def test_每条工具都有主线状态(self, manifest):
        for e in manifest["tools"]:
            assert e.get("main_line_status") in (
                "visible", "muted", "denied_by_effect", "not_assembled", "unknown")


# ════════════════════════════════════════════════════════════
#  8. E13 / E14 无效配置与技能侧实施约束
# ════════════════════════════════════════════════════════════

class TestDisclosures:

    def test_三处无效配置开关被标注(self, manifest):
        switches = {(c["path"], c["key"]): c for c in manifest["config_switches"]}
        assert ("data/tools_config.json", "tool_states") in switches
        assert ("data/system_prompt_config.json", "tool_definitions") in switches
        assert ("config.yaml", "tools.whitelist") in switches
        assert switches[("data/tools_config.json", "tool_states")]["status"] == "ineffective"
        assert switches[("config.yaml", "tools.whitelist")]["status"] == "stale"

    def test_技能侧实施约束已暴露(self, manifest):
        c = {x["key"]: x for x in manifest["skill_entity_constraints"]}
        assert c["entity_versioned_skills_json"]["value"] == "false"
        assert c["entity_versioned_skills_mgmt"]["value"] == "false"
        assert "digital_life_persona.py" in c["unmigrated_reader"]["value"]
        assert "email-helper" in c["orphan_skill_ids"]["value"]

    def test_技能条目带实体受版本控制列(self, manifest):
        """E14：每一条都必须有「实体是否受版本控制」，且**如实**

        【口径（勿简化成"技能全是 false"）】清单里的 23 条技能实体是
        `data/skills_repo/<id>/skill.md`，它**确实入库** ⇒ 这些条目为 true；
        真正不可复现的是**运行时技能目录/台账**（`data/skills.json`、
        `data/skills_mgmt.json`，被 .gitignore 忽略）——它们被登记在
        `runtime_only_entities` 里（entity_versioned=false）并单独披露。
        """
        for e in manifest["entries"]:
            assert "entity_versioned" in e, f"{e['tool_name']}: 缺「实体是否受版本控制」列"
        # 清单内的技能必须都有仓库实体（否则它们本就该在 runtime_only 里）
        assert [e["tool_name"] for e in manifest["skills"] if not e.get("entity_versioned")] == []
        # 运行时技能实体必须被如实标为不可复现
        roe = manifest["runtime_only_entities"]
        assert roe, "缺运行时技能实体的披露"
        assert all(x["entity_versioned"] is False for x in roe)
        assert {x["name"] for x in roe} == set(manifest["runtime_only_declarations"])
        # 25 条工具/技能合计不重复
        assert len(manifest["entries"]) == len(manifest["tools"]) + len(manifest["skills"])

    def test_性能列不编造(self, manifest):
        """E7：`avg_ms` / `p99_ms` / `dpm` 不得出现在**任何条目**里（未采集就是未采集）"""
        for e in manifest["entries"]:
            for key in ("avg_ms", "p99_ms", "dpm", "p50_ms", "avg_latency_ms"):
                assert key not in e, (
                    f"{e['tool_name']}: 出现了未采集的性能字段 {key!r}（严禁估算填充）")
        assert manifest["performance_note"], "缺少性能列采集方案说明"
        # 说明里必须点明"不可引用进程内计时冒充压测"
        assert "未采集" in manifest["performance_note"]


# ════════════════════════════════════════════════════════════
#  9. E8 tenant_id 不可客户端指定
# ════════════════════════════════════════════════════════════

class TestTenantId:

    def test_服务端派生忽略客户端值(self):
        from agent.server_routes import routes_ui_panels as R
        effective, declared = R._tenant_id_with_declaration("attacker-tenant")
        assert effective != "attacker-tenant", "客户端指定的 tenant_id 被采纳了（跨租户越权）"
        assert effective == R._server_tenant_id()
        assert declared == "attacker-tenant", "客户端值应作为待校验声明保留"

    def test_面板路由不再从请求参数取_tenant_id(self):
        """两个路由必须走 `_tenant_id_with_declaration`（源码级守门）"""
        text = (_PROJECT_ROOT / "agent" / "server_routes" / "routes_ui_panels.py"
                ).read_text(encoding="utf-8")
        assert 'tenant_id=request.args.get("tenant_id"' not in text, \
            "GET 路由仍把客户端 tenant_id 当生效值"
        assert 'tenant_id=str(body.get("tenant_id"' not in text, \
            "POST 路由仍把客户端 tenant_id 当生效值"
        assert text.count("_tenant_id_with_declaration(") >= 2

    def test_清单里_tenant_id_是占位_default(self, manifest):
        """§6.4：盘点表 tenant_id 列一律 default（预留占位，非真实隔离）"""
        assert {e["tenant_id"] for e in manifest["entries"]} == {"default"}


# ════════════════════════════════════════════════════════════
#  10. 假能力调用方判定
# ════════════════════════════════════════════════════════════

class TestHasCaller:

    def test_孤儿函数没有生产调用方(self):
        """`register_knowledge_audit_job` 只在测试里被调用 ⇒ 不是能力"""
        res = C.has_caller("register_knowledge_audit_job")
        assert res["has_caller"] is False, f"误判为有调用方：{res['callers']}"

    def test_真实工具能识别出调用方(self):
        """对照组：真正被生产链路引用的名字必须能识别（否则判据本身失效）"""
        res = C.has_caller("web_search")
        assert res["has_caller"] is True
        assert res["callers"], "命中却没有给出调用方文件"

    def test_测试目录不算调用方(self):
        """判定必须排除 tests/（否则"只有测试在调"的孤儿会被误判成有调用方）"""
        res = C.has_caller("register_knowledge_audit_job")
        assert all(not c.startswith("tests/") for c in res["callers"])
