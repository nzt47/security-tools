"""TASK-S4-02 策略库单元测试（版本化 / effective_range / 签名 / 内置不变量）

覆盖：装载（文件/目录/裸数组/单条）/ 版本历史与最高 SemVer 生效 / 同版本冲突 /
effective_range / 验签（ed25519 与 sha256-self 降级、强制签名、篡改检测）/
内置不变量不可遮蔽 / shadow_report / validate_all / revision 推进。
"""
from __future__ import annotations

import json

import pytest

from policy_testkit import make_policy, make_store, write_policy_file

from agent.policy import signing as signing_mod
from agent.policy.models import PolicyError, PolicyValidationError
# 注意：pytest.ini 的 python_functions = test_* verify_* 会把名为 verify_* 的模块级
# 函数当作用例收集；这里必须起别名，否则导入来的验签函数会被误当成测试而报错。
from agent.policy.signing import (
    PolicySigner,
    SignatureResult,
    self_sign,
    verify_policy_signature as _verify,
)
from agent.policy.store import (
    BUILTIN_ID_PREFIX,
    PolicyStore,
    PolicyStoreError,
    builtin_policies,
    builtin_policy_dicts,
    version_key,
)


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    from policy_testkit import isolate_policy
    return isolate_policy(tmp_path, monkeypatch)


# ────────────────────────────────────────────────────────────
#  SemVer
# ────────────────────────────────────────────────────────────


class TestVersionKey:
    def test_排序(self):
        items = ["1.0.0", "1.10.0", "1.2.0", "2.0.0", "0.9.9"]
        assert sorted(items, key=version_key) == [
            "0.9.9", "1.0.0", "1.2.0", "1.10.0", "2.0.0"]

    def test_预发布排在正式版之后(self):
        assert version_key("1.0.0-rc1") > version_key("1.0.0")

    def test_非法版本不抛异常(self):
        assert version_key("")[0] == 0
        assert version_key(None)[0] == 0

    def test_构建元数据被忽略(self):
        assert version_key("1.2.3+build7") == version_key("1.2.3")


# ────────────────────────────────────────────────────────────
#  内置不变量
# ────────────────────────────────────────────────────────────


class TestBuiltins:
    def test_默认装载内置不变量(self):
        store = PolicyStore(path="missing.json")
        ids = [p.id for p in store.active()]
        assert ids == [BUILTIN_ID_PREFIX + "secret-egress-deny"]

    def test_内置不变量自带签名指纹(self):
        store = PolicyStore(path="missing.json")
        policy = store.active()[0]
        assert policy.signature.startswith("sha256-self:")
        assert _verify(policy).ok is True

    def test_可关闭内置不变量(self):
        store = PolicyStore(path="missing.json", include_builtins=False)
        assert store.active() == []

    def test_内置策略_match_语义(self):
        policy = builtin_policies()[0]
        assert policy.effect.value == "deny"
        assert policy.match["all"][0]["value"] == "secret"

    def test_builtin_dicts_可解析(self):
        for raw in builtin_policy_dicts():
            from agent.policy.models import Policy
            Policy.parse(raw)

    def test_内置排在文件策略之前(self, tmp_path):
        path = write_policy_file(str(tmp_path / "p.json"), [make_policy(id="author.a")])
        store = PolicyStore(path=path)
        assert [p.id for p in store.active()] == [
            BUILTIN_ID_PREFIX + "secret-egress-deny", "author.a"]


# ────────────────────────────────────────────────────────────
#  装载
# ────────────────────────────────────────────────────────────


class TestLoad:
    def test_文件不存在返回零并保留内置(self):
        store = PolicyStore(path="definitely/missing.json")
        assert store.load() == 0
        assert len(store.active()) == 1

    def test_规范形态(self, tmp_path):
        path = write_policy_file(str(tmp_path / "p.json"), [make_policy(id="a.one")])
        store = PolicyStore(path=path)
        assert store.get("a.one") is not None

    def test_裸数组形态(self, tmp_path):
        path = tmp_path / "arr.json"
        path.write_text(json.dumps([make_policy(id="a.arr")]), encoding="utf-8")
        store = PolicyStore(path=str(path))
        assert store.get("a.arr") is not None

    def test_单条策略形态(self, tmp_path):
        path = tmp_path / "single.json"
        path.write_text(json.dumps(make_policy(id="a.single")), encoding="utf-8")
        store = PolicyStore(path=str(path))
        assert store.get("a.single") is not None

    def test_schema_不匹配报错(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps({"schema": "policy.v9", "policies": []}),
                        encoding="utf-8")
        with pytest.raises(PolicyStoreError):
            PolicyStore(path=str(path))

    def test_非法_JSON_报错(self, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(PolicyStoreError):
            PolicyStore(path=str(path))

    def test_无法识别的结构报错(self, tmp_path):
        path = tmp_path / "weird.json"
        path.write_text(json.dumps({"nothing": 1}), encoding="utf-8")
        with pytest.raises(PolicyStoreError):
            PolicyStore(path=str(path))

    def test_目录装载(self, tmp_path):
        write_policy_file(str(tmp_path / "a.json"), [make_policy(id="d.one")])
        write_policy_file(str(tmp_path / "b.json"), [make_policy(id="d.two")])
        (tmp_path / "ignored.txt").write_text("x", encoding="utf-8")
        store = PolicyStore(path=str(tmp_path))
        assert store.get("d.one") and store.get("d.two")

    def test_装载失败计入_problems_不抛出(self, tmp_path):
        write_policy_file(str(tmp_path / "p.json"), [
            make_policy(id="ok.one"),
            make_policy(id="bad.one", effect="permit"),
        ])
        store = PolicyStore(path=str(tmp_path / "p.json"))
        assert store.get("ok.one") is not None
        assert any(p.policy_id == "bad.one" for p in store.problems)

    def test_loaded_paths_记录来源(self, tmp_path):
        path = write_policy_file(str(tmp_path / "p.json"), [make_policy(id="a.x")])
        store = PolicyStore(path=path)
        assert store.loaded_paths == [path]

    def test_路径可由环境变量给定(self, tmp_path, monkeypatch):
        path = write_policy_file(str(tmp_path / "env.json"), [make_policy(id="e.x")])
        monkeypatch.setenv("CP_POLICY_FILE", path)
        assert PolicyStore().path == path

    def test_reload_重建并推进_revision(self, tmp_path):
        path = write_policy_file(str(tmp_path / "p.json"), [make_policy(id="r.x")])
        store = PolicyStore(path=path)
        before = store.revision
        store.reload()
        assert store.revision > before
        assert store.get("r.x") is not None


# ────────────────────────────────────────────────────────────
#  版本化
# ────────────────────────────────────────────────────────────


class TestVersioning:
    def test_同_id_多版本保留历史_最高生效(self):
        store = make_store([make_policy(id="v.a", version="1.0.0"),
                            make_policy(id="v.a", version="1.2.0"),
                            make_policy(id="v.a", version="1.10.0")])
        assert [p.version for p in store.history("v.a")] == ["1.0.0", "1.2.0", "1.10.0"]
        assert store.get("v.a").version == "1.10.0"
        assert len([p for p in store.active() if p.id == "v.a"]) == 1

    def test_同版本同内容幂等(self):
        store = make_store([make_policy(id="i.a")])
        before = store.revision
        store.add(make_policy(id="i.a"))
        assert store.revision == before
        assert len(store.history("i.a")) == 1

    def test_同版本不同内容报错(self):
        store = make_store([make_policy(id="c.a")])
        with pytest.raises(PolicyStoreError) as exc:
            store.add(make_policy(id="c.a", effect="allow"))
        assert "同版本不同文本" in str(exc.value)

    def test_add_推进_revision(self):
        store = make_store()
        before = store.revision
        store.add(make_policy(id="n.a"))
        assert store.revision == before + 1

    def test_remove_指定版本(self):
        store = make_store([make_policy(id="d.a", version="1.0.0"),
                            make_policy(id="d.a", version="2.0.0")])
        assert store.remove("d.a", "2.0.0") == 1
        assert store.get("d.a").version == "1.0.0"

    def test_remove_全部版本(self):
        store = make_store([make_policy(id="d.b", version="1.0.0"),
                            make_policy(id="d.b", version="2.0.0")])
        assert store.remove("d.b") == 2
        assert store.get("d.b") is None
        assert "d.b" not in store.ids()

    def test_remove_不存在返回零(self):
        assert make_store().remove("nope") == 0

    def test_get_指定版本(self):
        store = make_store([make_policy(id="g.a", version="1.0.0"),
                            make_policy(id="g.a", version="2.0.0")])
        assert store.get("g.a", "1.0.0").version == "1.0.0"
        assert store.get("g.a", "9.9.9") is None
        assert store.get("nope") is None

    def test_count_统计全部版本(self):
        store = make_store([make_policy(id="c.a", version="1.0.0"),
                            make_policy(id="c.a", version="2.0.0")])
        assert store.count() == 3  # 2 + 内置不变量

    def test_clear_保留内置(self):
        store = make_store([make_policy(id="cl.a")])
        store.clear()
        assert store.get("cl.a") is None
        assert len(store.active()) == 1

    def test_clear_可全清(self):
        store = make_store([make_policy(id="cl.b")])
        store.clear(keep_builtins=False)
        assert store.active() == []

    def test_adopt_搬运不校验(self):
        source = make_store([make_policy(id="ad.a")])
        target = make_store()
        target.adopt(source.get("ad.a"))
        assert target.get("ad.a") is not None

    def test_指纹随内容变化(self):
        store = make_store([make_policy(id="f.a")])
        first = store.fingerprint()
        store.add(make_policy(id="f.a", version="2.0.0"))
        assert store.fingerprint() != first

    def test_to_dict_含_schema_与指纹(self):
        body = make_store([make_policy(id="t.a")]).to_dict()
        assert body["schema"] == "policy.v1"
        assert body["fingerprint"]
        assert isinstance(body["policies"], list)


# ────────────────────────────────────────────────────────────
#  诊断
# ────────────────────────────────────────────────────────────


class TestDiagnostics:
    def test_shadow_report_抓_allow_遮蔽_deny(self):
        store = make_store([
            make_policy(id="s.allow", effect="allow"),
            make_policy(id="s.deny", effect="deny"),
        ])
        report = store.shadow_report()
        assert any(item["shadowing"] == "s.allow@1.0.0"
                   and item["shadowed"] == "s.deny@1.0.0" for item in report)

    def test_shadow_report_deny_在前无告警(self):
        store = make_store([
            make_policy(id="s.deny2", effect="deny"),
            make_policy(id="s.allow2", effect="allow"),
        ])
        assert store.shadow_report() == []

    def test_validate_all_干净库无问题(self):
        assert make_store([make_policy(id="ok.a")]).validate_all() == []

    def test_validate_all_抓坏签名(self):
        """``adopt`` 绕过 add 的验签闸门，用来构造「库里已存在坏签名」的状态。"""
        from agent.policy.models import Policy
        store = make_store()
        store.adopt(Policy.parse(make_policy(id="bs.a",
                                             signature="ed25519:deadbeef")))
        problems = store.validate_all()
        assert any(p.code == "BAD_SIGNATURE" for p in problems)

    def test_validate_all_强制签名时抓缺失(self):
        from agent.policy.models import Policy
        store = make_store(require_signed=True)
        store.adopt(Policy.parse(make_policy(id="ms.a")))
        assert any(p.code == "MISSING_SIGNATURE" for p in store.validate_all())


# ────────────────────────────────────────────────────────────
#  签名
# ────────────────────────────────────────────────────────────


class TestSigning:
    @staticmethod
    def _signer(tmp_path):
        return PolicySigner(private_key_path=str(tmp_path / "k.pem"),
                            public_key_path=str(tmp_path / "k.pub.pem"))

    def test_ed25519_签名与验签(self, tmp_path):
        signer = self._signer(tmp_path)
        signed = signer.sign_dict(make_policy(id="sgn.a"))
        assert signed["signature"].startswith("ed25519:")
        assert signer.degraded is False
        from agent.policy.models import Policy
        result = _verify(Policy.parse(signed),
                            public_key_path=str(tmp_path / "k.pub.pem"))
        assert result.ok is True and result.degraded is False

    def test_篡改内容验签失败(self, tmp_path):
        signer = self._signer(tmp_path)
        signed = signer.sign_dict(make_policy(id="tamper.a"))
        signed["effect"] = "allow"  # 篡改
        from agent.policy.models import Policy
        assert _verify(Policy.parse(signed),
                          public_key_path=str(tmp_path / "k.pub.pem")).ok is False

    def test_策略库可直接用注入的公钥验签(self, tmp_path):
        """「用哪把公钥验签」是组合根的决定，不该靠环境变量对齐（见 store 注释）。"""
        signer = self._signer(tmp_path)
        store = make_store(public_key_path=str(tmp_path / "k.pub.pem"))
        store.add(signer.sign_dict(make_policy(id="pk.a")))
        assert store.get("pk.a") is not None
        assert store.validate_all() == []

    def test_策略库用错公钥时拒绝装载(self, tmp_path):
        signer = self._signer(tmp_path)
        other = PolicySigner(private_key_path=str(tmp_path / "o.pem"),
                             public_key_path=str(tmp_path / "o.pub.pem"))
        store = make_store(public_key_path=str(tmp_path / "o.pub.pem"))
        with pytest.raises(PolicyStoreError) as exc:
            store.add(signer.sign_dict(make_policy(id="pk.b")))
        assert "验签失败" in str(exc.value)
        assert other.public_key_pem  # 另一把公钥确实存在（排除"文件缺失"这一巧合）

    def test_未签名返回未通过(self):
        from agent.policy.models import Policy
        result = _verify(Policy.parse(make_policy(id="u.a")))
        assert result.ok is False and "未签名" in result.reason

    def test_未知方案(self):
        from agent.policy.models import Policy
        result = _verify(
            Policy.parse(make_policy(id="x.a", signature="pgp:abc")))
        assert result.ok is False and "未知签名方案" in result.reason

    def test_形态非法缺摘要(self):
        from agent.policy.models import Policy
        result = _verify(
            Policy.parse(make_policy(id="y.a", signature="ed25519:")))
        assert result.ok is False

    def test_sha256_self_占位(self):
        policy = make_policy(id="self.a")
        policy["signature"] = self_sign(policy)
        from agent.policy.models import Policy
        result = _verify(Policy.parse(policy))
        assert result.ok is True and result.degraded is True

    def test_sha256_self_篡改失败(self):
        policy = make_policy(id="self.b")
        policy["signature"] = self_sign(policy)
        policy["version"] = "9.9.9"
        from agent.policy.models import Policy
        assert _verify(Policy.parse(policy)).ok is False

    def test_无公钥时_ed25519_验签失败(self, tmp_path):
        signer = self._signer(tmp_path)
        signed = signer.sign_dict(make_policy(id="nokey.a"))
        from agent.policy.models import Policy
        result = _verify(Policy.parse(signed),
                            public_key_path=str(tmp_path / "absent.pem"))
        assert result.ok is False and "无公钥可用" in result.reason

    def test_策略非法时验签直接失败(self):
        assert _verify({"id": "bad"}).ok is False

    def test_公钥不可解析(self, tmp_path):
        from agent.policy.models import Policy
        result = _verify(
            Policy.parse(make_policy(id="badkey.a", signature="ed25519:00")),
            public_key_pem="not a pem")
        assert result.ok is False and "公钥不可解析" in result.reason

    def test_签名摘要非_hex(self, tmp_path):
        from agent.policy.models import Policy
        result = _verify(
            Policy.parse(make_policy(id="badhex.a", signature="ed25519:zzzz")),
            public_key_pem=self._signer(tmp_path).public_key_pem)
        assert result.ok is False and "非 hex" in result.reason

    def test_强制签名时拒收未签名策略(self):
        store = make_store(require_signed=True)
        with pytest.raises(PolicyStoreError) as exc:
            store.add(make_policy(id="req.a"))
        assert "未签名" in str(exc.value)

    def test_强制签名时拒收降级占位(self):
        store = make_store(require_signed=True)
        policy = make_policy(id="req.b")
        policy["signature"] = self_sign(policy)
        with pytest.raises(PolicyStoreError) as exc:
            store.add(policy)
        assert "占位签名" in str(exc.value)

    def test_强制签名时可接受真签名(self, tmp_path):
        signer = self._signer(tmp_path)
        store = make_store(require_signed=True,
                           public_key_path=str(tmp_path / "k.pub.pem"))
        store.add(signer.sign_dict(make_policy(id="req.c")))
        assert store.get("req.c") is not None

    def test_签名方案属性(self, tmp_path):
        signer = self._signer(tmp_path)
        assert signer.scheme == "ed25519"
        assert signer.degraded is False
        assert signer.public_key_pem.startswith("-----BEGIN PUBLIC KEY-----")

    def test_sign_all_产出可验签文档(self, tmp_path):
        signer = self._signer(tmp_path)
        store = make_store([make_policy(id="sa.a")])
        document = store.sign_all(signer)
        assert document["schema"] == "policy.v1"
        assert document["signing_degraded"] is False
        assert all(item["signature"].startswith("ed25519:")
                   for item in document["policies"])

    def test_signing_payload_不含签名字段(self):
        from agent.policy.models import Policy
        policy = Policy.parse(make_policy(id="pl.a", signature="ed25519:aa"))
        assert "signature" not in policy.signing_payload()

    def test_require_signature_读环境变量(self, monkeypatch):
        monkeypatch.setenv("CP_POLICY_REQUIRE_SIGNATURE", "1")
        assert signing_mod.require_signature() is True
        monkeypatch.setenv("CP_POLICY_REQUIRE_SIGNATURE", "off")
        assert signing_mod.require_signature() is False


# ────────────────────────────────────────────────────────────
#  进程级单例
# ────────────────────────────────────────────────────────────


class TestSingleton:
    def test_get_policy_store_同实例(self):
        from agent.policy import get_policy_store, reset_policy_store
        reset_policy_store()
        assert get_policy_store() is get_policy_store()

    def test_reset_换实例(self):
        from agent.policy import get_policy_store, reset_policy_store
        reset_policy_store()
        first = get_policy_store()
        reset_policy_store()
        assert get_policy_store() is not first


class TestExceptions:
    def test_策略库错误继承策略基类(self):
        assert issubclass(PolicyStoreError, PolicyError)
