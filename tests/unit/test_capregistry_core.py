"""`agent/capregistry` 的只读性 / 查询 / 命名冲突 / 错误语义（TASK-05 E2 / E7 / E8 / E11）

本文件只放**能在单测里确定性复现**的断言；端到端的两条硬门禁另有可执行脚本：

    scripts/verify_llm_off_entrypoints.py        # E1 + E6（关掉 LLM 的三条链路 + 对拍）
    scripts/verify_loader_degradation_startup.py # E5（Loader 失败不阻塞启动）
    scripts/audit_call_paths.py --check          # E1b（调用路径收敛 + 白名单不腐化）
    scripts/bench_capregistry.py                 # E9（性能实测）

【为什么分开】单测里跑真实 HTTP 服务 / 起 waitress / 压测会让 `pytest` 变成
"另一个压测框架"，且与 `TASK-00` D13（pytest 必须串行、不与其它 pytest 并发）
的纪律冲突。**可执行脚本 + 单测各司其职**，两边都在报告里给出命令。
"""

from __future__ import annotations

import ast
import inspect
import os
import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agent.capregistry import (  # noqa: E402
    CALLABLE_BY, ERROR_CODES, CODE_META, CapabilityError, CapabilityRegistry,
    CapabilityRecord, build_registry, derive_callable_by, from_exception,
    get_registry, redact, reset_registry, to_llm_safe)
from agent.capregistry import modelcaps  # noqa: E402
from agent.capregistry import view as _view  # noqa: E402


@pytest.fixture(scope="module")
def real_registry() -> CapabilityRegistry:
    """真实清单构建的 Registry（只读；模块级复用避免重复构建）"""
    return build_registry()


# ════════════════════════════════════════════════════════════
#  E2 · 单一真相源：Registry 是**只读派生视图**
# ════════════════════════════════════════════════════════════


class TestReadOnlyDerivedView:
    """E2 的三条证据：类型级、API 级、源码级"""

    def test_spec_是_frozen_类型级只读(self):
        spec = CapabilityRecord(tool_name="x")
        with pytest.raises(Exception):
            spec.tool_name = "y"  # type: ignore[misc]

    #: 任何名字含这些片段的**公开**方法都视为"写入 API"
    _WRITE_VERBS = ("register", "unregister", "add", "remove", "delete", "set",
                    "update", "put", "insert", "write", "save", "commit", "upload",
                    "create", "drop", "reset_", "load_from", "sync_")

    def test_registry_没有公开写入_api(self):
        """★ 验收核心：`CapabilityRegistry` 不暴露任何写入方法

        【判据为什么是"方法名动词"而不是"函数体里有没有赋值"】`__init__` 里必然
        有赋值（构造索引），那是**构造期**的一次性动作，不是"运行期写入 API"。
        真正要禁的是"外部调用方能让 Registry 改变内容"。故：
          · 允许 `_` 下划线开头（内部实现）；
          · 允许只读属性（`@property`）；
          · 禁止公开的写入型动词方法。
        """
        offenders = []
        for name, member in inspect.getmembers(CapabilityRegistry):
            if name.startswith("_"):
                continue
            if isinstance(member, property):
                continue
            if not callable(member):
                continue
            low = name.lower()
            if any(low == v or low.startswith(v) for v in self._WRITE_VERBS):
                offenders.append(name)
        assert offenders == [], (
            f"CapabilityRegistry 暴露了写入型方法 {offenders} ⇒ 违反 D1"
            "（Registry 必须是只读派生视图）")

    def test_registry_源码里没有反向写入_yaml_或_registry(self):
        """源码级：`view.py` 不得出现写文件 / 调 register / 反向导出

        【为什么必须查源码而不是只查 API】最危险的不是"有个 setter"，
        而是"某个查询函数顺手把结果写回 YAML / 注册表" —— 那是**静默的
        第二真相源**。故直接对源码文本做否定式断言。
        """
        src = Path(_view.__file__).read_text(encoding="utf-8")
        # 去掉注释与 docstring 后再断言（文档里提到这些词是正常的）
        tree = ast.parse(src)
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                ds = ast.get_docstring(node)
                if ds:
                    docstrings.add(ds)
        code_only = src
        for ds in docstrings:
            code_only = code_only.replace(ds, "")
        code_only = re.sub(r"#[^\n]*", "", code_only)
        # 【判据是"写"而不是"读"】`open(path, "r")` / `json.load` 是**读**清单，完全正当。
        # 要禁的是**反向写入**：写文件、改注册表、序列化回 YAML。
        for banned in ('yaml.dump(', "yaml.safe_dump(", "json.dump(",
                       'open(path, "w"', "open(path, 'w'", '", "a")', "', 'a')",
                       ".write(", ".writelines(", "os.remove(", "shutil.",
                       "register(", "register_dynamic(", "unregister("):
            assert banned not in code_only, (
                f"view.py 的**可执行代码**里出现 {banned!r} ⇒ 存在反向写入路径（违反 D1）")

    def test_registry_数据来自权威声明而非自己解析_yaml(self):
        """主源必须经 `agent/lines/models.py::load_tool_meta()`（唯一权威入口）

        若 `view.py` 自己 `yaml.safe_load` 那些 YAML，就绕过了"唯一权威读取入口"，
        等于**第二份解析口径**（与 `agent/tool_gate.py`、`rate_limiter`、
        `tool_approval` 读到的元数据可能不一致）。
        """
        src = Path(_view.__file__).read_text(encoding="utf-8")
        assert "load_tool_meta" in src, "view.py 必须经 load_tool_meta 读工具声明"
        code_only = re.sub(r"#[^\n]*", "", src)
        assert "yaml.safe_load" not in code_only and "yaml.load" not in code_only, (
            "view.py 不得自己解析 YAML（第二真相源）")


# ════════════════════════════════════════════════════════════
#  查询 / 索引 / 模型能力裁剪
# ════════════════════════════════════════════════════════════


class TestQuery:
    def test_真实清单规模与分布与清单一致(self, real_registry):
        st = real_registry.stats()
        # 【G1-C 2026-09-26】114/23 → 119/28、local 93 → 98：H-3 迁移的 5 条技能
        # 从「只在运行时台账里」变为仓库内 skill.md 实体 ⇒ 计入清单的能力面。
        # 断言仍是"精确等于"，只是基线随能力面变化而刷新。
        assert st["total"] == 119, f"能力总数应为 119，实际 {st['total']}"
        assert st["by_kind"] == {"skill": 28, "tool": 91}
        assert st["by_location"] == {"local": 98, "remote": 21}
        assert st["degraded"] is False

    def test_主键索引_tenant_name(self, real_registry):
        spec = real_registry.get("data_format_detect")
        assert spec is not None
        assert spec.tenant_id == "default"
        assert real_registry.get("definitely_not_a_tool") is None

    def test_过滤查询按_kind_与_location(self, real_registry):
        tools = real_registry.query(kind="tool")
        skills = real_registry.query(kind="skill")
        assert len(tools) == 91 and len(skills) == 28
        remote = real_registry.query(location="remote")
        assert len(remote) == 21

    def test_identity_白名单过滤(self, real_registry):
        """`callable_by` 是入口身份白名单（v1.4 §5.1）"""
        # 24 条 trigger=system（技能侧）不该被 llm 身份选中
        llm_only = real_registry.query(identity="llm")
        assert all("llm" in s.callable_by for s in llm_only)
        assert len(llm_only) < real_registry.stats()["total"]

    def test_索引_确实被构建(self, real_registry):
        idx = real_registry.stats()["index"]
        assert idx["primary"] == 119
        assert idx["facet"] >= 1


class TestCapabilitySpecDerivation:
    @pytest.mark.parametrize("trigger,llm_callable,perm,deprecated,expected", [
        ("model", True, "public", False, ["llm", "human", "system", "service_account"]),
        ("model", False, "public", False, ["human", "system", "service_account"]),
        ("system", True, "public", False, ["system", "human", "service_account"]),
        ("human", True, "public", False, ["human"]),
        ("none", True, "public", False, []),
        ("model", True, "restricted", False, ["llm", "human", "system"]),
        ("model", True, "public", True, []),
    ])
    def test_derive_callable_by_规则(self, trigger, llm_callable, perm,
                                     deprecated, expected):
        got = derive_callable_by(trigger=trigger, llm_callable=llm_callable,
                                 permission_level=perm, deprecated=deprecated)
        assert got == expected

    def test_service_account_是新增值域(self):
        """`service_account` 在仓库既有 `trigger` 里**不存在**，是本任务补的"""
        assert "service_account" in CALLABLE_BY
        assert len(CALLABLE_BY) == 4

    def test_kind_归并(self):
        """`kind` 的归并规则在 `ToolMeta.kind`（**唯一归并点**），不是 `CapabilityRecord`

        `CapabilityRecord.kind` 是普通字段（由 `ToolMeta.kind` 填进来），
        `agent/lines/models.py::ToolMeta.kind` 才是做 4→2 值域归并的地方。
        断言必须打在**真正实现规则的那一处**，否则测的是别的东西。
        """
        from agent.lines.models import ToolMeta
        assert ToolMeta(name="a", tool_type="skill").kind == "skill"
        assert ToolMeta(name="a", tool_type="tool").kind == "tool"
        assert ToolMeta(name="a", tool_type="api").kind == "tool"
        assert ToolMeta(name="a", tool_type="script").kind == "skill"


class TestModelCapabilityProbe:
    """E7 · 模型能力探测有效（不支持 tool calling ⇒ 返回裁剪后清单）"""

    def test_未指定模型不裁剪(self, real_registry):
        env = real_registry.list_envelope()
        assert env["data"]["returned"] == env["data"]["total"] == 119
        assert env["data"]["model_capability"]["supports_tool_calling"] is True

    @pytest.mark.parametrize("sentinel", ["none", "off", "-", "no-tools"])
    def test_哨兵值返回裁剪后的清单(self, real_registry, sentinel):
        env = real_registry.list_envelope(model=sentinel)
        assert env["data"]["model_capability"]["supports_tool_calling"] is False
        assert env["data"]["returned"] == 0
        assert env["data"]["items"] == []
        # **total 仍然如实报告**：裁剪的是"给这个模型看的清单"，
        # 不是"注册表里有多少能力"。两个数含义不同，不许混。
        assert env["data"]["total"] == 119

    def test_不支持前缀被识别(self):
        ok, why = modelcaps.supports_tool_calling("text-davinci-003")
        assert ok is False and "denies" in why or "不支持" in why

    def test_未知模型按支持处理并说明理由(self):
        ok, why = modelcaps.supports_tool_calling("some-unknown-model-xyz")
        assert ok is True
        assert "未知" in why and "宁可多暴露" in why


class TestDegradation:
    """降级路径：主源构建失败 ⇒ 走清单快照并标 degraded（不得阻塞启动）"""

    def test_主源失败时降级为快照(self, monkeypatch):
        def _boom(*_a, **_kw):
            raise RuntimeError("模拟 load_tool_meta 故障")
        monkeypatch.setattr("agent.lines.models.load_tool_meta", _boom)
        reg = build_registry()
        assert reg.degraded is True
        assert len(reg) > 0, "降级后仍应有能力（否则等于把能力面整体藏掉）"
        assert reg.stats()["by_spec_source"] == {"snapshot": len(reg)}

    def test_连快照都读不到时返回空表而不抛(self, monkeypatch, tmp_path):
        monkeypatch.setattr(_view, "MANIFEST_PATH",
                            str(tmp_path / "nope" / "missing.json"))
        reg = build_registry(root=str(tmp_path))
        assert len(reg) == 0 and reg.degraded is True
        assert reg.build_warnings, "空表必须带明确告警（不许静默）"

    def test_get_registry_单例与重置(self):
        reset_registry()
        a = get_registry()
        b = get_registry()
        assert a is b
        reset_registry()
        assert get_registry() is not a


# ════════════════════════════════════════════════════════════
#  E11 · 命名冲突有报告
# ════════════════════════════════════════════════════════════


class TestNameConflicts:
    def test_冲突报告可产出且结构完整(self, real_registry):
        conflicts = real_registry.name_conflicts()
        assert isinstance(conflicts, list)
        for c in conflicts:
            assert "name" in c and "kind" in c

    def test_同一租户同名同源不报冲突(self, real_registry):
        """119 条里没有 `(tenant, name)` 重复 ⇒ 声明层零冲突（实证 E11 的结论）"""
        seen = {}
        for s in real_registry.specs:
            seen.setdefault((s.tenant_id, s.tool_name), []).append(s)
        dup = {k: v for k, v in seen.items() if len(v) > 1}
        assert dup == {}, f"声明层存在同名重复：{dup}"

    def test_清单里的同名冲突有登记_不静默(self):
        """`data/capability_manifest.json` 的 `same_name_conflicts` 是 TASK-04 的
        实测结论（`get_status` / `search_memory` / `get_sensor_summary` 在
        `global` 与 `planning` 两套注册表里各有一份）。本任务**不合并**它们
        （D2），但结论必须能被读到。
        """
        import json
        path = _ROOT / "data" / "capability_manifest.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        conflicts = doc.get("same_name_conflicts") or []
        names = {c.get("name") for c in conflicts}
        assert {"get_status", "search_memory", "get_sensor_summary"} <= names
        for c in conflicts:
            assert str(c.get("resolved") or "").strip(), "每条冲突必须写明处置"


# ════════════════════════════════════════════════════════════
#  E8 · 错误语义统一（14 个码 + 异常不进 LLM 上下文）
# ════════════════════════════════════════════════════════════


class TestErrorSemantics:
    def test_十四个错误码齐全(self):
        assert len(ERROR_CODES) == 14
        assert set(ERROR_CODES) == {
            "ok", "timeout", "denied", "schema_error", "validation_error",
            "unhealthy", "not_found", "quota_exceeded", "llm_unavailable",
            "upstream_error", "cancelled", "deadline_exceeded", "conflict",
            "internal_error"}

    @pytest.mark.parametrize("code", sorted(CODE_META))
    def test_每个码都有元信息与可重试语义(self, code):
        meta = CODE_META[code]
        assert meta.llm_hint.strip(), f"{code} 缺少给 LLM 的固定描述"
        assert isinstance(meta.retryable, bool)
        assert 200 <= meta.http_status < 600

    @pytest.mark.parametrize("exc,expected", [
        (TimeoutError("x"), "timeout"),
        (ValueError("x"), "validation_error"),
        (KeyError("x"), "validation_error"),
        (PermissionError("x"), "denied"),
        (FileNotFoundError("x"), "not_found"),
        (FileExistsError("x"), "conflict"),
        (ConnectionError("x"), "upstream_error"),
        (RuntimeError("x"), "internal_error"),
    ])
    def test_异常到错误码的唯一映射(self, exc, expected):
        err = from_exception(exc)
        assert err.code == expected
        assert isinstance(err, CapabilityError)

    def test_未知工具归_not_found_而不是_internal(self):
        """`ToolError("未知工具: 'x'")` 是**通用包装**，必须按语义拆开

        【为什么这条重要】"能力不存在"与"能力坏了"对调用方是完全不同的处置
        （前者改名、后者重试/查日志）。若一律归 `internal_error`，
        CI 就无法区分，`TASK-05` §5 的"CI 可凭 status 阻断"也就失去了意义。
        """
        from agent.tools import ToolError
        err = from_exception(ToolError("未知工具: 'nope'，可用工具: []"))
        assert err.code == "not_found"

    def test_工具执行失败归_internal_而不是_not_found(self):
        from agent.tools import ToolError
        err = from_exception(ToolError("工具 'x' 执行失败: boom"))
        assert err.code == "internal_error"

    def test_映射是幂等的(self):
        err = CapabilityError("denied", "no")
        assert from_exception(err) is err

    def test_capability_error_带可重试与_http_状态(self):
        err = CapabilityError("quota_exceeded", "too fast")
        assert err.retryable is True
        assert err.http_status == 429


class TestRedaction:
    """★ 铁律：Python 异常原文 / HTML 错误页**不得**进入 LLM 上下文"""

    #: 一个真实形态的"危险原文"：traceback 骨架 + 绝对路径 + 内网 URL/主机名
    #: + 邮箱 + 疑似密钥 + HTML 错误页片段
    _DANGEROUS = (
        "Traceback (most recent call last):\n"
        "  File \"C:\\Users\\Administrator\\agent\\agent\\tools\\x.py\", line 12\n"
        "    raise\n"
        "requests.exceptions.ConnectionError: HTTPSConnectionPool(host="
        "'internal.corp.local', port=443): Max retries exceeded with url: "
        "https://internal.corp.local/api/v1/token?key=sk-ABCDEFGH1234567890abcdefghijklmnopqrstuvwxyz\n"
        "contact: ops@example.com token=ghp_0123456789abcdefghijklmnopqrstuvwxyz\n"
        "<html><body><h1>502 Bad Gateway</h1></body></html>"
    )

    def test_绝对路径被抹掉(self):
        out = redact(self._DANGEROUS)
        # 路径可能被 `<path>` 占位符替换，也可能随整条 `File "..." , line N`
        # 一起被抹掉（两条规则都覆盖它）—— 判据是"路径不在了"，不是"用了哪条规则"。
        assert "C:\\Users" not in out
        assert "Administrator" not in out
        assert "\\agent\\tools" not in out

    def test_url_被抹掉(self):
        out = redact(self._DANGEROUS)
        assert "<url>" in out
        assert "internal.corp.local/api" not in out

    def test_traceback_骨架被抹掉(self):
        out = redact(self._DANGEROUS)
        assert "Traceback" not in out
        assert "line 12" not in out

    def test_html_被抹掉(self):
        # HTML 是"最典型的泄漏源 + 最典型的噪声源"，且保留标记毫无价值
        assert "<html>" not in redact(self._DANGEROUS)

    def test_邮箱被抹掉(self):
        assert "ops@example.com" not in redact(self._DANGEROUS)

    def test_疑似密钥被抹掉(self):
        out = redact(self._DANGEROUS)
        assert "sk-ABCDEFGH" not in out
        assert "ghp_0123456789" not in out

    def test_长度被硬截断(self):
        assert len(redact("x" * 10_000)) <= 200

    def test_空白与换行被压平(self):
        assert "\n" not in redact("a\nb\nc")

    def test_to_llm_safe_只含固定描述与脱敏摘要(self):
        err = CapabilityError("internal_error", self._DANGEROUS,
                              detail=self._DANGEROUS, capability="shell_execute")
        safe = err.to_llm_safe()
        blob = repr(safe)
        for leak in ("C:\\Users", "internal.corp.local", "ops@example.com",
                     "sk-ABCDEFGH", "Traceback", "line 12"):
            assert leak not in blob, f"LLM 安全形态里泄漏了 {leak!r}"
        assert safe["code"] == "internal_error"
        assert safe["message"] == CODE_META["internal_error"].llm_hint
        assert safe["retryable"] is False
        assert safe["capability"] == "shell_execute"

    def test_html_错误页失去结构(self):
        html = ("<!DOCTYPE html><html><head><title>502 Bad Gateway</title>"
                "</head><body><center><h1>502 Bad Gateway</h1></center>"
                "<hr><center>nginx/1.24.0</center></body></html>")
        safe = to_llm_safe("upstream_error", detail=html)
        assert "<html>" not in repr(safe)
        assert "<" not in safe.get("detail", "")

    def test_脱敏失败也不泄漏(self):
        class _Evil:
            def __str__(self):  # noqa: D105
                raise RuntimeError("boom")
        assert redact(_Evil()) == ""

    def test_每条错误码的固定描述都不含运行时数据(self):
        """`llm_hint` 是**常量串** ⇒ 结构上不可能携带路径/URL/密钥"""
        for code, meta in CODE_META.items():
            assert not re.search(r"[A-Za-z]:\\|https?://|@", meta.llm_hint), \
                f"{code} 的 llm_hint 含疑似运行时数据：{meta.llm_hint!r}"


# ════════════════════════════════════════════════════════════
#  result_schema 机制 + CI 阻断语义
# ════════════════════════════════════════════════════════════


class TestResultSchema:
    def test_至少三个不依赖_LLM_的工具已声明_result_schema(self, real_registry):
        from agent.capregistry.contract import RESULT_SCHEMA_REQUIRED_TOOLS
        for name in RESULT_SCHEMA_REQUIRED_TOOLS:
            spec = real_registry.get(name)
            assert spec is not None, f"{name} 不在清单里"
            assert isinstance(spec.result_schema, dict) and spec.result_schema, \
                f"{name} 缺少 result_schema"

    def test_契约违约被检出(self, real_registry):
        from agent.capregistry.contract import result_status
        spec = real_registry.get("json_query")
        bad = result_status(spec, {"count": "not-an-int"})
        assert bad["status"] == "contract_violation"
        assert bad["contract"] == "declared"
        good = result_status(spec, {"ok": True, "data": [1], "count": 1})
        assert good["status"] == "ok"

    def test_未声明契约时如实披露而不是谎报通过(self, real_registry):
        from agent.capregistry.contract import result_status
        spec = real_registry.get("shell_execute")
        out = result_status(spec, {"anything": 1})
        assert out["contract"] == "undeclared"
        assert out["status"] == "ok"      # 不制造假红
        assert out["contract_errors"] == []

    def test_真实工具返回值满足契约(self):
        """★ 用**真实产物**复测（D12：不许用夹具形状代替生产形状）"""
        from agent.capregistry.contract import validate_result
        from agent.data_process_tools import data_format_detect, json_query
        specs = build_registry()
        real = data_format_detect('{"a": 1, "b": [2, 3]}')
        ok, errs, has = validate_result(specs.get("data_format_detect"), real)
        assert has is True and ok is True, f"真实返回值不合契约：{errs}"
        real2 = json_query('{"store":{"book":[{"title":"A"}]}}',
                           "$.store.book[0].title")
        ok2, errs2, has2 = validate_result(specs.get("json_query"), real2)
        assert has2 is True and ok2 is True, f"真实返回值不合契约：{errs2}"

    def test_内置降级校验器可用(self):
        from agent.capregistry.contract import validate_against_schema
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}},
                  "required": ["ok"]}
        ok, errs, validator = validate_against_schema({"ok": True}, schema)
        assert ok and validator == "jsonschema"
        ok2, errs2, _ = validate_against_schema({"ok": 1}, schema)
        assert not ok2 and errs2
        ok3, _e, v3 = validate_against_schema({}, None)
        assert ok3 and v3 == "none"
