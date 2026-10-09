"""可带走 bundle 契约与导入/导出端点（S5）单元测试

覆盖设计原文的三条守卫 + 契约细节，每条都**可证伪**：

  1. 密钥零泄漏：环境里的伪造密钥不进包；带 api_key 值的 bundle 被既有密钥闸拒绝；
     secret ref 多带一个键（例如 value）即抛 —— "顺手带值"没有路。
  2. 严格序列化：往返逐字相等；未知 schema_version / 非对象 / 缺必需键一律明确异常。
  3. 同源对拍：导出→导入后再跑 resolve_subagent_assembly / resolve_subagent_llm，
     结论与源逐字一致（LLM 用替身；assembly 用真实 engineering 线，可复现）。
  4. 幂等：同一 bundle 导入两次得到逐字相同的配置。
  5. 协议不变：entrypoint.argv_template 的渲染结果 == channel.build_cli_argv；
     inproc / subprocess 共用同一份模板；未知 backend 明确报错（fail-closed）。
  6. 路由：最小 Flask app + 替身（**不 import app_server**）：GET 过闸、检出密钥拒绝；
     POST 非法 400 且不建容器、合法成功建配置。

【不易】不 import app_server；GET/POST 用 FakeYunshu 替身；不落任何运行时文件。
"""

from __future__ import annotations

import dataclasses
import json

import pytest
from flask import Flask

from agent.lines import get_line_registry, load_tool_meta
from agent.server_routes.routes_subagent import register_routes
from agent.subagent import bundle as B
from agent.subagent.assembly import resolve_subagent_assembly
from agent.subagent.channel import SubprocessChannelExecutor, build_cli_argv
from agent.subagent.container import SubagentConfig
from agent.subagent.credentials import ManifestSecretLeak, assert_manifest_secret_free
from agent.subagent.executor import DelegationExecutor, LlmChannelExecutor
from agent.subagent.llm_factory import resolve_subagent_llm

#: 固定的时间与 id，保证同一用例内可复现（不依赖时钟/随机）
STAMP = "2026-10-09T00:00:00+00:00"
BID = "bnd-unit-test"


def make_config(**over) -> SubagentConfig:
    data = {
        "name": "sa-1",
        "model_id": "deepseek-v4-pro",
        "tags": ["alpha", "beta"],
        "ttl_seconds": 120,
        "context_window": 8192,
        "tool_sources": ["builtin"],
        "permissions": ["read"],
        "llm_temperature": 0.2,
        "role_template": "",
        "role_text": "",
        "role_mode": "template",
        "memory_mode": "none",
    }
    data.update(over)
    return SubagentConfig(**data)


def make_bundle(config: SubagentConfig | None = None, **kw) -> dict:
    kw.setdefault("generated_at", STAMP)
    kw.setdefault("bundle_id", BID)
    # v2 起 environment 为必需段（离线依赖清单）；本文件用最小合法形态占位，
    # 依赖清单本身的语义由 tests/unit/test_subagent_dependencies.py 专测。
    kw.setdefault("environment", {
        "source": "pyproject.toml [project].dependencies",
        "source_status": "ok",
        "captured_at": STAMP,
        "python": {"required": "", "running": "", "status": "unknown"},
        "items": [],
        "counts": {"declared": 0, "ok": 0, "missing": 0,
                   "version_mismatch": 0, "unknown": 0},
        "satisfied_locally": True,
        "artifacts": {"mode": "none", "count": 0},
        "offline_ready": False,
        "reason": "测试占位：未打包 wheel",
    })
    return B.build_bundle(config or make_config(), **kw)


class StubLLM:
    """母体 LLM 替身：with_model 派生同型实例，模型名可对拍"""

    def __init__(self, model: str = "deepseek-flash") -> None:
        self.model = model
        self.provider = "stub"

    def with_model(self, model: str) -> "StubLLM":
        return StubLLM(model)

    def chat(self, *a, **k) -> str:  # pragma: no cover - 不在本文件真跑
        return "{}"


# ════════════════════════════════════════════════════════════
#  ① 密钥零泄漏
# ════════════════════════════════════════════════════════════


class TestSecretFree:
    def test_环境里的伪造密钥不进包(self, monkeypatch):
        fake = "sk-" + "A" * 24
        monkeypatch.setenv("CP_TEMP_X", fake)
        refs = B.secret_refs_from_env()
        assert {"source": "env", "name": "X", "env_var": "CP_TEMP_X"} in refs
        b = make_bundle(secret_refs=refs)
        text = B.bundle_to_json(b)
        assert fake not in text, "环境凭据的值绝不能进包"
        for shape in ("sk-", "ghp_", "AKIA", "PRIVATE KEY"):
            assert shape not in text, f"包内不得出现密钥形态: {shape}"
        for ref in b["secrets"]["refs"]:
            assert set(ref) == {"source", "name", "env_var"}, "引用只能有三个键（不存值）"

    def test_带api_key非空值的bundle被密钥闸拒绝(self):
        leaky = {"api_key": "sk-" + "B" * 24}
        with pytest.raises(ManifestSecretLeak):
            assert_manifest_secret_free(leaky)

    def test_密钥引用多带一个值键即抛(self):
        with pytest.raises(B.BundleValidationError):
            make_bundle(secret_refs=[{"source": "env", "name": "X",
                                      "env_var": "CP_TEMP_X", "value": "sk-" + "C" * 24}])

    def test_validate与import都不放行带密钥的包(self):
        b = make_bundle()
        b["assembly"]["role"]["text"] = "token: sk-" + "D" * 24
        assert any("密钥" in p for p in B.validate_bundle(b))
        with pytest.raises(B.BundleValidationError):
            B.import_config(b)


# ════════════════════════════════════════════════════════════
#  ② 严格序列化与固定结构
# ════════════════════════════════════════════════════════════


class TestContract:
    def test_往返逐字相等(self):
        b = make_bundle(line_id="engineering")
        assert B.bundle_from_json(B.bundle_to_json(b)) == b

    def test_顶层键集固定(self):
        b = make_bundle()
        # v2 顶层 = 必需键 ∪ {environment}（依赖清单为可选段里的必需项）
        assert set(b) == set(B.REQUIRED_TOP_KEYS) | {"environment"}
        assert set(b["identity"]) == {"name", "tags", "ttl_seconds", "context_window"}
        assert set(b["assembly"]) == {"role", "model", "memory", "tools", "permissions"}
        assert set(b["assembly"]["role"]) == {"template", "text", "mode"}
        assert set(b["assembly"]["model"]) == {"model_id", "temperature"}
        assert set(b["assembly"]["memory"]) == {"mode", "scope"}
        assert set(b["assembly"]["tools"]) == {"tool_sources", "line"}
        assert set(b["secrets"]) == {"refs"}
        assert set(b["entrypoint"]) == {"protocol", "argv_template"}
        assert set(b["runtime"]) == {"backend"}

    def test_未知schema_version明确异常(self):
        b = make_bundle()
        b["schema_version"] = 999
        with pytest.raises(B.BundleValidationError) as ei:
            B.bundle_from_json(json.dumps(b))
        assert "schema_version" in str(ei.value)

    def test_非对象与缺必需键明确异常(self):
        with pytest.raises(B.BundleValidationError):
            B.bundle_from_json("[1, 2, 3]")
        with pytest.raises(B.BundleValidationError):
            B.bundle_from_json(json.dumps({"schema_version": 1}))

    def test_合法bundle校验为空(self):
        assert B.validate_bundle(make_bundle()) == []


# ════════════════════════════════════════════════════════════
#  ③ 同源对拍（assembly + llm）
# ════════════════════════════════════════════════════════════


class TestSameSource:
    def test_导出导入后装配与模型结论一致(self):
        meta = load_tool_meta()
        available = sorted(meta.keys())
        registry = get_line_registry()
        line_id = "engineering"

        src_asm = resolve_subagent_assembly(line_id, registry, meta, available)
        cfg = make_config()
        parent = StubLLM("deepseek-flash")
        src_llm = resolve_subagent_llm(cfg.model_id, parent_llm=parent,
                                       temperature=cfg.llm_temperature)

        b = make_bundle(cfg, line_id=line_id, llm_resolution=src_llm, assembly=src_asm)
        assert b["assembly"]["tools"]["line"] == line_id
        assert b["assembly"]["tools"]["authorized"] == list(src_asm.tools)

        cfg2 = B.import_config(B.bundle_from_json(B.bundle_to_json(b)))
        asm2 = resolve_subagent_assembly(
            b["assembly"]["tools"]["line"], registry, meta, available)
        llm2 = resolve_subagent_llm(cfg2.model_id, parent_llm=parent,
                                    temperature=cfg2.llm_temperature)

        assert asm2.to_dict() == src_asm.to_dict(), "导入后装配结论必须与母体一致"
        assert llm2.to_dict() == src_llm.to_dict(), "导入后模型解析结论必须与母体一致"
        assert cfg2.model_id == cfg.model_id
        assert cfg2.llm_temperature == cfg.llm_temperature


# ════════════════════════════════════════════════════════════
#  ④ 幂等
# ════════════════════════════════════════════════════════════


class TestIdempotent:
    def test_同一bundle导入两次逐字相同(self):
        b = make_bundle(line_id="engineering")
        c1 = B.import_config(b)
        c2 = B.import_config(B.bundle_from_json(B.bundle_to_json(b)))
        assert c1 == c2
        assert dataclasses.asdict(c1) == dataclasses.asdict(c2)


# ════════════════════════════════════════════════════════════
#  ⑤ 协议不变 + 后端映射
# ════════════════════════════════════════════════════════════


class TestProtocol:
    @pytest.mark.parametrize("cli", ["my-agent", "python -m my_agent"])
    def test_argv渲染等于build_cli_argv(self, cli):
        b = make_bundle()
        rendered = B.render_argv(b, agent_cli=cli, task_file="tf.json",
                                 max_turns=7, output_format="stream-json")
        assert rendered == build_cli_argv(cli, "tf.json", max_turns=7,
                                          output_format="stream-json")

    def test_两档后端共用同一份协议模板(self):
        a = make_bundle(backend="inproc")
        c = make_bundle(backend="subprocess")
        assert a["entrypoint"] == c["entrypoint"]

    def test_后端映射复用既有执行器(self, monkeypatch):
        monkeypatch.delenv("CP_SUBAGENT_AGENT_CLI", raising=False)
        ex_in = B.resolve_backend(make_bundle(backend="inproc"), llm=StubLLM("m"))
        assert isinstance(ex_in, DelegationExecutor)
        assert isinstance(ex_in._channel, LlmChannelExecutor)

        ex_sub = B.resolve_backend(make_bundle(backend="subprocess"), agent_cli="my-cli")
        assert isinstance(ex_sub, DelegationExecutor)
        assert isinstance(ex_sub._channel, SubprocessChannelExecutor)

    def test_未知后端明确报错(self, monkeypatch):
        monkeypatch.delenv("CP_SUBAGENT_AGENT_CLI", raising=False)
        b = make_bundle(backend="docker")
        with pytest.raises(B.UnsupportedBackend):
            B.get_backend(b)
        with pytest.raises(B.UnsupportedBackend):
            B.resolve_backend(b, llm=StubLLM("m"))
        assert any("backend" in p for p in B.validate_bundle(b))

    def test_subprocess缺CLI明确失败不回落到inproc(self, monkeypatch):
        monkeypatch.delenv("CP_SUBAGENT_AGENT_CLI", raising=False)
        with pytest.raises(B.BundleError):
            B.resolve_backend(make_bundle(backend="subprocess"), llm=StubLLM("m"))


# ════════════════════════════════════════════════════════════
#  ⑥ 路由（最小 Flask app + 替身，不 import app_server）
# ════════════════════════════════════════════════════════════


class FakeContainer:
    def __init__(self, config: SubagentConfig) -> None:
        self.config = config

    def get_status(self) -> dict:
        return {"name": self.config.name, "model_id": self.config.model_id}


class FakeManager:
    def __init__(self, container) -> None:
        self._container = container

    def get(self, name):
        if self._container is not None and name == self._container.config.name:
            return self._container
        return None


class FakeYunshu:
    def __init__(self, container=None, llm=None) -> None:
        self._subagent_mgr = FakeManager(container)
        self._llm = llm if llm is not None else StubLLM("deepseek-flash")
        self.created: list = []

    def create_subagent(self, config):
        self.created.append(config)
        return FakeContainer(config)

    def list_subagents(self):
        return []


def make_client(yunshu) -> object:
    app = Flask(__name__)
    app.config.update(TESTING=True)
    register_routes(app, type("S", (), {"Yunshu": yunshu})())
    return app.test_client()


class TestRoutes:
    def test_GET导出过闸且不含密钥(self):
        client = make_client(FakeYunshu(FakeContainer(make_config())))
        resp = client.get("/api/subagent/sa-1/bundle")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["ok"] is True
        assert body["bundle"]["schema_version"] == B.BUNDLE_SCHEMA_VERSION
        # 用密钥闸的**同一份判定**断言"无密钥形态"：环境段含依赖名
        # prometheus-flask-exporter（裸 "sk-" 子串会被它误伤，那不是密钥）
        from agent.subagent.credentials import find_manifest_secrets
        assert find_manifest_secrets(body) == []
        # 导出必带 v2 environment 段（离线依赖清单），且 offline_ready 恒 false（未打包 wheel）
        env = body["bundle"]["environment"]
        assert env["artifacts"]["mode"] == "none"
        assert env["offline_ready"] is False

    def test_GET检出密钥拒绝导出(self):
        cfg = make_config(role_text="token: sk-" + "E" * 24)
        client = make_client(FakeYunshu(FakeContainer(cfg)))
        resp = client.get("/api/subagent/sa-1/bundle")
        assert resp.status_code == 409, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["error_code"] == "E_MANIFEST_SECRET_LEAK"
        assert body.get("bundle") is None

    def test_GET分身不存在404(self):
        client = make_client(FakeYunshu(None))
        assert client.get("/api/subagent/nope/bundle").status_code == 404

    def test_POST导入非法400且不建容器(self):
        yunshu = FakeYunshu(FakeContainer(make_config()))
        client = make_client(yunshu)
        resp = client.post("/api/subagent/import",
                           json={"bundle": {"schema_version": 1}})
        assert resp.status_code == 400, resp.get_data(as_text=True)
        assert resp.get_json()["error_code"] == "E_BUNDLE_INVALID"
        assert yunshu.created == [], "非法 bundle 绝不能建容器"

    def test_POST导入未知后端400且不建容器(self):
        yunshu = FakeYunshu(None)
        client = make_client(yunshu)
        resp = client.post("/api/subagent/import",
                           json={"bundle": make_bundle(backend="docker")})
        assert resp.status_code == 400
        assert yunshu.created == []

    def test_POST导入成功建配置并回报来源(self):
        yunshu = FakeYunshu(None)
        client = make_client(yunshu)
        resp = client.post("/api/subagent/import", json={"bundle": make_bundle()})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["ok"] is True
        imported = body["imported"]
        assert imported["bundle_id"] == BID
        assert imported["backend"] == "inproc"
        # 导入成功回显 environment（原样）与到达端对拍：导入成功 ≠ 可离线跑
        assert imported["environment"]["artifacts"]["mode"] == "none"
        assert body["environment_check"]["offline_ready"] is False
        assert body["subagent"]["name"] == "sa-1"
        assert len(yunshu.created) == 1
        assert isinstance(yunshu.created[0], SubagentConfig)
        assert yunshu.created[0].model_id == "deepseek-v4-pro"
