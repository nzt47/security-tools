"""离线依赖清单守卫（agent/subagent/dependencies.py + bundle environment 段 + 路由回显）

每条都可证伪（回退产品逻辑 ⇒ 红）：
  D1 诚实标记：缺失=missing、版本不符=version_mismatch、判不了=unknown，永不默认 ok；
  D2 不谎报离线就绪：全部满足也不自称 offline_ready（未打包 wheel）；
  D3 python 版本不匹配如实标记；
  D4 pyproject 不可读 ⇒ unreadable + 空清单，不抛、不谎报；
  D5 包名不当 dict 键（tokenizers 等）不触发密钥闸（本模块第一风险）；
  D6 environment 段往返 + 严格校验 + v2 必需、v1 旧包仍可导入（可带走优先）；
  D7 不联网、不跑 pip/subprocess。

不 import app_server；不起网络。
"""
from __future__ import annotations

import pytest

from agent.subagent import bundle as B
from agent.subagent import dependencies as D
from agent.subagent.container import SubagentConfig
from agent.subagent.credentials import (ManifestSecretLeak, assert_manifest_secret_free,
                                        find_manifest_secrets)


def _config() -> SubagentConfig:
    return SubagentConfig(name="sa-dep", model_id="deepseek-v4-pro")


def _manifest(**kw):
    return D.build_dependency_manifest(
        captured_at="2026-10-09T00:00:00+00:00",
        running_python=kw.pop("running_python", "3.12.10"),
        **kw)


# ════════════════════════════════════════════════════════════
#  D1 / D2 / D3 / D4 诚实标记
# ════════════════════════════════════════════════════════════


class TestHonesty:
    def test_缺失依赖如实标记missing(self):
        manifest = _manifest(declared=[{"name": "flask", "specifier": ">=3.0,<4.0"}],
                             installed={})
        item = manifest["items"][0]
        assert item["status"] == D.STATUS_MISSING
        assert item["installed"] is None
        assert manifest["satisfied_locally"] is False
        assert manifest["counts"]["missing"] == 1

    def test_版本不满足标记version_mismatch(self):
        manifest = _manifest(declared=[{"name": "flask", "specifier": ">=3.0,<4.0"}],
                             installed={"flask": "2.0.0"})
        assert manifest["items"][0]["status"] == D.STATUS_VERSION_MISMATCH
        assert manifest["satisfied_locally"] is False

    def test_全部满足也不自称离线就绪(self):
        manifest = _manifest(declared=[{"name": "flask", "specifier": ">=3.0,<4.0"}],
                             installed={"flask": "3.1.0"},
                             expected_python=">=3.11,<3.13")
        assert manifest["items"][0]["status"] == D.STATUS_OK
        assert manifest["satisfied_locally"] is True
        assert manifest["artifacts"]["mode"] == "none"
        assert manifest["offline_ready"] is False, "未打包 wheel 却自称离线就绪 = 谎报"
        assert manifest["reason"]

    def test_python版本不匹配如实标记(self):
        manifest = _manifest(declared=[], installed={},
                             expected_python=">=3.11,<3.13", running_python="3.10.4")
        assert manifest["python"]["status"] == D.STATUS_VERSION_MISMATCH
        assert manifest["offline_ready"] is False

    def test_pyproject不可读不谎报(self):
        declared, status = D.read_declared_dependencies(
            "C:/definitely/not/here/pyproject.toml")
        assert declared == [] and status == "unreadable"
        manifest = D.build_dependency_manifest(declared=[], installed={},
                                               source_status="unreadable")
        assert manifest["source_status"] == "unreadable"
        assert manifest["offline_ready"] is False


# ════════════════════════════════════════════════════════════
#  D2 offline_ready 短路契约
# ════════════════════════════════════════════════════════════


class TestOfflineReadyContract:
    def test_未打包时恒_false(self):
        assert D.offline_ready({"artifacts": {"mode": "none", "count": 0},
                                "counts": {D.STATUS_MISSING: 0, D.STATUS_VERSION_MISMATCH: 0},
                                "python": {"status": D.STATUS_OK}}) is False

    def test_真带_wheelhouse_且无缺口才_true(self):
        assert D.offline_ready({"artifacts": {"mode": "wheelhouse", "count": 3},
                                "counts": {D.STATUS_MISSING: 0, D.STATUS_VERSION_MISMATCH: 0},
                                "python": {"status": D.STATUS_OK}}) is True
        # 有一个 missing 就不是就绪
        assert D.offline_ready({"artifacts": {"mode": "wheelhouse", "count": 3},
                                "counts": {D.STATUS_MISSING: 1, D.STATUS_VERSION_MISMATCH: 0},
                                "python": {"status": D.STATUS_OK}}) is False


# ════════════════════════════════════════════════════════════
#  D5 密钥闸误伤（本模块第一风险）
# ════════════════════════════════════════════════════════════


class TestNoSecretFalsePositive:
    def test_包名不当字典键_不触发密钥闸(self):
        manifest = _manifest(declared=[{"name": "tokenizers", "specifier": ">=0.15"}],
                             installed={"tokenizers": "0.15.2"})
        # list-of-objects 形态：包名是值、不是键 ⇒ 不命中"可疑键名"正则
        assert_manifest_secret_free({"environment": manifest})
        bundle = B.build_bundle(_config(), environment=manifest,
                                generated_at="2026-10-09T00:00:00+00:00",
                                bundle_id="bnd-dep-test")
        assert find_manifest_secrets(bundle) == []

    def test_反例_包名作字典键会被误判(self):
        with pytest.raises(ManifestSecretLeak):
            assert_manifest_secret_free({"environment": {"tokenizers": "0.15.2"}})


# ════════════════════════════════════════════════════════════
#  D6 environment 段契约 / v1 兼容
# ════════════════════════════════════════════════════════════


def _valid_bundle() -> dict:
    manifest = _manifest(declared=[{"name": "flask", "specifier": ">=3.0"}],
                         installed={"flask": "3.1.0"},
                         expected_python=">=3.11,<3.13")
    return B.build_bundle(_config(), environment=manifest,
                          generated_at="2026-10-09T00:00:00+00:00", bundle_id="bnd-dep-test")


class TestBundleCompat:
    def test_v2_导出带环境段且往返逐字相等(self):
        bundle = _valid_bundle()
        assert B.BUNDLE_SCHEMA_VERSION == 2
        assert bundle["schema_version"] == 2
        assert "environment" in bundle
        assert bundle["environment"]["artifacts"]["mode"] == "none"
        assert B.validate_bundle(bundle) == []
        assert B.bundle_from_json(B.bundle_to_json(bundle)) == bundle

    def test_v2缺environment被拒(self):
        bundle = _valid_bundle()
        del bundle["environment"]
        problems = B.validate_bundle(bundle)
        assert any("environment" in p for p in problems), problems

    def test_v1旧包仍可导入(self):
        bundle = _valid_bundle()
        bundle["schema_version"] = 1
        del bundle["environment"]
        assert B.validate_bundle(bundle) == [], "v1 旧包不得因新增字段而被拒（可带走优先）"
        assert B.import_config(bundle).name == "sa-dep"

    def test_环境段严格校验(self):
        assert D.validate_environment("not-a-map")
        assert D.validate_environment({"source_status": "weird"})
        assert D.validate_environment({"source_status": "ok", "offline_ready": "yes"})
        assert D.validate_environment({"source_status": "ok", "offline_ready": True,
                                       "items": "not-a-list"})
        assert D.validate_environment({"source_status": "ok", "offline_ready": True,
                                       "items": []}) == []


# ════════════════════════════════════════════════════════════
#  D7 不联网 / 不跑 pip
# ════════════════════════════════════════════════════════════


class TestNoNetwork:
    def test_不联网不跑pip(self, monkeypatch):
        import subprocess

        def _boom(*a, **k):  # pragma: no cover - 一旦被调用即失败
            raise AssertionError("依赖清单不得触发 subprocess/pip")

        monkeypatch.setattr(subprocess, "run", _boom)
        manifest = D.build_dependency_manifest()
        assert isinstance(manifest, dict)
        # 未传 artifacts ⇒ 完整"无打包物"形状（键集固定，UI/校验不必分支）
        assert manifest["artifacts"]["mode"] == D.ARTIFACTS_MODE_NONE
        assert manifest["artifacts"]["count"] == 0
        assert manifest["artifacts"]["files"] == []

    def test_声明依赖来自_pyproject(self):
        manifest = D.build_dependency_manifest()
        assert manifest["source"] == D.CANONICAL_SOURCE
        # 本仓 pyproject 必有 requires-python 与若干依赖（读不到即 unreadable，也算如实）
        assert manifest["source_status"] in ("ok", "unreadable")
        if manifest["source_status"] == "ok":
            assert manifest["counts"]["declared"] > 0
