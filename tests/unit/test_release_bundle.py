#!/usr/bin/env python3
"""整包回滚原子单位 ReleaseBundle 单元测试（TASK-S4-03 步骤 2 / v7.2 §4.4 P7.2-15）

覆盖验收项：
    1. **不可拆**：回滚原子单位 = 整包五组件（code+skills+weights+data-baseline+manifest）
       的整体 hash；任何真子集 → `PartialRollbackError` + L4 事故卡（"禁止只回技能不回代码"）；
    2. **一致性可验**：`verify_consistency()` 把「部分匹配」判为部分回滚残留 → L4；
    3. **绝不真动仓库**：本模块无执行能力，`applier=None` 即 dry-run；用例不做任何真实回滚。

【路径纪律】台账/事故卡目录一律显式指向 `tmp_path`，绝不写仓库 `data/`。
【安全纪律】不跑 git、不碰真实仓库；applier 只做记录。
"""
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from agent.self_healing.levels import (
    HealLevel,
    list_incidents,
    load_incident,
)
from agent.self_healing.release_bundle import (
    COMPONENT_CODE,
    COMPONENT_DATA_BASELINE,
    COMPONENT_LABELS,
    COMPONENT_MANIFEST,
    COMPONENT_NAMES,
    COMPONENT_SKILLS,
    COMPONENT_WEIGHTS,
    DEFAULT_BUNDLES_DIR,
    ENV_BUNDLES_DIR,
    HASH_ALGO,
    LEDGER_FILENAME,
    BundleComponent,
    BundleIntegrityError,
    BundleNotFoundError,
    BundleValidationError,
    PartialRollbackError,
    ReleaseBundle,
    ReleaseStore,
    build_bundle,
    bundles_dir,
    check_atomic_request,
    compute_bundle_hash,
    hash_mapping,
    hash_path,
    plan_rollback,
    reset_bundle_state,
    rollback_bundle,
    snapshot_bundle,
    # 别名导入：pytest.ini 的 python_functions 含 `verify_*`，直名导入会被当作用例收集
    verify_consistency as check_consistency,
)

#: 组件 → 版本（规范序的五组件，测试基线）
BASE_VERSIONS = {
    COMPONENT_CODE: "git:aaa1111",
    COMPONENT_SKILLS: "skills-1.2.0",
    COMPONENT_WEIGHTS: "weights-2026.08",
    COMPONENT_DATA_BASELINE: "baseline-v3",
    COMPONENT_MANIFEST: "manifest-7",
}


def _components(versions=None, suffix: str = ""):
    """构造五组件映射（BundleComponent 形态）"""
    versions = dict(versions or BASE_VERSIONS)
    return {
        name: BundleComponent(name=name, version=versions[name],
                              hash=f"{HASH_ALGO}:{name}-{versions[name]}{suffix}",
                              ref=f"ref/{name}")
        for name in COMPONENT_NAMES
    }


def _bundle(**kwargs) -> ReleaseBundle:
    """构造一个自校验通过的整包"""
    return build_bundle(_components(), **kwargs)


def _store(tmp_path, name: str = "release_bundles.json") -> ReleaseStore:
    """显式路径台账（绝不落到仓库 data/releases）"""
    return ReleaseStore(path=str(tmp_path / name))


@pytest.fixture(autouse=True)
def _isolate_bundle_state(monkeypatch, tmp_path):
    """逐用例隔离：事件目录改道 tmp + 审计链停写 + 模块状态复位"""
    monkeypatch.setenv("CP_EVENTS_DIR", str(tmp_path / "events"))
    events_mod = None
    try:
        import agent.observability.events as events_mod  # noqa: F401
        events_mod.reset_event_stores()
    except Exception:  # noqa: BLE001
        events_mod = None
    try:
        from agent.audit import facade as audit_facade
        monkeypatch.setattr(audit_facade.audit, "_enabled", False, raising=False)
    except Exception:  # noqa: BLE001
        pass
    reset_bundle_state()
    yield
    reset_bundle_state()
    if events_mod is not None:
        events_mod.reset_event_stores()


# ════════════════════════════════════════════════════════════
#  1. 整包 hash（不可拆的数据模型基础）
# ════════════════════════════════════════════════════════════


class TestComputeBundleHash:
    """五组件 → 整包 hash"""

    def test_deterministic_and_prefixed(self):
        """同内容两次求哈希完全相同，且带算法前缀"""
        first = compute_bundle_hash(_components())
        second = compute_bundle_hash(_components())
        assert first == second
        assert first.startswith(f"{HASH_ALGO}:")

    def test_any_component_version_change_changes_hash(self):
        """改动任意一个组件的**版本** → 整包 hash 必变"""
        base = compute_bundle_hash(_components())
        for name in COMPONENT_NAMES:
            variants = dict(BASE_VERSIONS)
            variants[name] = variants[name] + "-patched"
            assert compute_bundle_hash(_components(variants)) != base, name

    def test_any_component_hash_change_changes_hash(self):
        """仅改动一个组件的**内容哈希**（版本不变）也要变（防"版本回退即安全"错觉）"""
        base = compute_bundle_hash(_components())
        for name in COMPONENT_NAMES:
            tweaked = _components()
            tweaked[name] = BundleComponent(name=name, version=tweaked[name].version,
                                            hash=f"{HASH_ALGO}:tampered-{name}")
            assert compute_bundle_hash(tweaked) != base, name

    def test_ref_is_not_part_of_identity(self):
        """ref 是溯源元数据：只改 ref 不改 hash"""
        base = compute_bundle_hash(_components())
        tweaked = _components()
        for name in COMPONENT_NAMES:
            tweaked[name] = BundleComponent(name=name, version=tweaked[name].version,
                                            hash=tweaked[name].hash, ref="其他引用")
        assert compute_bundle_hash(tweaked) == base

    def test_missing_component_raises(self):
        """缺任一组件 → BundleValidationError（不静默补默认值）"""
        for name in COMPONENT_NAMES:
            partial = _components()
            partial.pop(name)
            with pytest.raises(BundleValidationError) as exc:
                compute_bundle_hash(partial)
            assert name in str(exc.value)
        with pytest.raises(BundleValidationError):
            compute_bundle_hash({})

    def test_unknown_extra_component_raises(self):
        """多出未知组件 → BundleValidationError（防悄悄扩包）"""
        extra = _components()
        extra["eval_anchor"] = BundleComponent("eval_anchor", "v1", f"{HASH_ALGO}:x")
        with pytest.raises(BundleValidationError) as exc:
            compute_bundle_hash(extra)
        assert "eval_anchor" in str(exc.value)

    def test_empty_version_or_hash_raises(self):
        """组件 version/hash 为空 → 拒绝（不得生成"空身份"整包）"""
        blank_version = _components()
        blank_version[COMPONENT_CODE] = BundleComponent(COMPONENT_CODE, "", f"{HASH_ALGO}:h")
        with pytest.raises(BundleValidationError) as exc:
            compute_bundle_hash(blank_version)
        assert COMPONENT_CODE in str(exc.value)
        blank_hash = _components()
        blank_hash[COMPONENT_WEIGHTS] = BundleComponent(COMPONENT_WEIGHTS, "v1", "")
        with pytest.raises(BundleValidationError):
            compute_bundle_hash(blank_hash)


# ════════════════════════════════════════════════════════════
#  2. build_bundle 的四种输入形态
# ════════════════════════════════════════════════════════════


class TestBuildBundle:
    """BundleComponent / dict / 2 元组 / 字符串"""

    def test_accepts_all_four_forms(self):
        """四种形态混用也能构造出同一整包"""
        comps = _components()
        mixed = {
            COMPONENT_CODE: comps[COMPONENT_CODE],
            COMPONENT_SKILLS: {"name": COMPONENT_SKILLS,
                               "version": BASE_VERSIONS[COMPONENT_SKILLS],
                               "hash": comps[COMPONENT_SKILLS].hash},
            COMPONENT_WEIGHTS: (BASE_VERSIONS[COMPONENT_WEIGHTS],
                                comps[COMPONENT_WEIGHTS].hash),
            COMPONENT_DATA_BASELINE: [BASE_VERSIONS[COMPONENT_DATA_BASELINE],
                                      comps[COMPONENT_DATA_BASELINE].hash],
            COMPONENT_MANIFEST: comps[COMPONENT_MANIFEST],
        }
        bundle = build_bundle(mixed)
        assert bundle.verify_integrity() is True
        assert bundle.component_versions()[COMPONENT_WEIGHTS] == BASE_VERSIONS[COMPONENT_WEIGHTS]
        assert bundle.note == ""  # 无降级组件

    def test_string_form_recorded_as_degraded_in_note(self):
        """纯字符串（version-only 降级）必须在 note 中留痕，hash 由 version 派生"""
        bundle = build_bundle({
            COMPONENT_CODE: BASE_VERSIONS[COMPONENT_CODE],
            COMPONENT_SKILLS: BASE_VERSIONS[COMPONENT_SKILLS],
            COMPONENT_WEIGHTS: BASE_VERSIONS[COMPONENT_WEIGHTS],
            COMPONENT_DATA_BASELINE: BASE_VERSIONS[COMPONENT_DATA_BASELINE],
            COMPONENT_MANIFEST: BASE_VERSIONS[COMPONENT_MANIFEST],
        }, note="插件升级前快照")
        assert "version-only 降级组件" in bundle.note
        assert "code" in bundle.note and "manifest" in bundle.note
        assert bundle.note.startswith("插件升级前快照｜")
        # 同一 version → 同一派生 hash（确定性，可复算）
        again = build_bundle({n: BASE_VERSIONS[n] for n in COMPONENT_NAMES})
        assert again.bundle_hash == bundle.bundle_hash

    def test_component_name_is_normalized_to_key(self):
        """组件自带 name 与键不符时以键为准（防串位）"""
        comps = _components()
        wrong = dict(comps)
        wrong[COMPONENT_CODE] = BundleComponent("skills", "v9", f"{HASH_ALGO}:zz")
        bundle = build_bundle(wrong)
        assert bundle.components[COMPONENT_CODE].name == COMPONENT_CODE
        assert bundle.verify_integrity() is True

    def test_missing_or_invalid_form_raises(self):
        """缺组件 / 形态非法 → BundleValidationError"""
        with pytest.raises(BundleValidationError) as exc:
            build_bundle({COMPONENT_CODE: "v1"})
        assert COMPONENT_SKILLS in str(exc.value)
        bad = {n: BASE_VERSIONS[n] for n in COMPONENT_NAMES}
        bad[COMPONENT_WEIGHTS] = 12345
        with pytest.raises(BundleValidationError) as exc2:
            build_bundle(bad)
        assert COMPONENT_WEIGHTS in str(exc2.value)

    def test_bundle_id_and_serialization_round_trip(self):
        """bundle_id = hash 前 12 位；to_dict/from_dict 往返守恒"""
        bundle = _bundle(release_tag="v7.2.0")
        assert bundle.bundle_id == "rb-" + bundle.bundle_hash.split(":", 1)[-1][:12]
        payload = bundle.to_dict()
        restored = ReleaseBundle.from_dict(payload)
        assert restored.bundle_hash == bundle.bundle_hash
        assert restored.bundle_id == bundle.bundle_id
        assert restored.release_tag == "v7.2.0"
        assert restored.verify_integrity() is True
        assert restored.component_hashes() == bundle.component_hashes()

    def test_verify_integrity_detects_tampering(self):
        """内容被改而 hash 未更新 → verify_integrity() False"""
        bundle = _bundle()
        assert bundle.verify_integrity() is True
        bundle.components[COMPONENT_SKILLS] = BundleComponent(
            COMPONENT_SKILLS, "skills-9.9.9", f"{HASH_ALGO}:evil")
        assert bundle.verify_integrity() is False


# ════════════════════════════════════════════════════════════
#  3. 台账 ReleaseStore（显式路径）
# ════════════════════════════════════════════════════════════


class TestReleaseStore:
    """put / get / require / latest / list / count"""

    def test_put_get_require_list_count(self, tmp_path):
        """基本台账 API 全通，且落盘位置就在显式路径"""
        store = _store(tmp_path)
        bundle = store.put(_bundle())
        assert store.path == tmp_path / LEDGER_FILENAME
        assert store.path.exists()
        assert store.get(bundle.bundle_hash) is bundle
        assert store.require(bundle.bundle_hash).bundle_id == bundle.bundle_id
        assert store.count() == 1
        assert [b.bundle_hash for b in store.list()] == [bundle.bundle_hash]
        assert store.latest().bundle_hash == bundle.bundle_hash
        assert store.get("sha256:missing") is None

    def test_require_missing_raises_bundle_not_found(self, tmp_path):
        """require 缺失 hash → BundleNotFoundError"""
        store = _store(tmp_path)
        store.put(_bundle())
        with pytest.raises(BundleNotFoundError):
            store.require("sha256:0000")
        with pytest.raises(BundleNotFoundError):
            store.require("")

    def test_empty_store_latest_is_none(self, tmp_path):
        """空台账：latest() None、count() 0、list() 空（不抛）"""
        store = _store(tmp_path)
        assert store.latest() is None
        assert store.list() == []
        assert store.count() == 0

    def test_put_same_hash_and_content_is_idempotent(self, tmp_path):
        """同 hash 同内容重复 put：不重复入库、不改写台账"""
        store = _store(tmp_path)
        first = store.put(_bundle())
        mtime = store.path.stat().st_mtime_ns
        second = store.put(_bundle())
        assert second is first
        assert store.count() == 1
        assert store.path.stat().st_mtime_ns == mtime

    def test_put_tampered_bundle_raises_integrity_error(self, tmp_path):
        """内容与自身 hash 不符的整包拒绝入库（篡改防护）"""
        store = _store(tmp_path)
        tampered = _bundle()
        tampered.components[COMPONENT_CODE] = BundleComponent(
            COMPONENT_CODE, "git:bbb2222", f"{HASH_ALGO}:swapped")
        with pytest.raises(BundleIntegrityError):
            store.put(tampered)
        assert store.count() == 0

    def test_persisted_ledger_reloads_in_new_instance(self, tmp_path):
        """台账落盘后，新实例可读回同一整包（跨进程可见）"""
        store = _store(tmp_path)
        bundle = store.put(_bundle(release_tag="v7.2.0"))
        reopened = _store(tmp_path)
        assert reopened.count() == 1
        loaded = reopened.require(bundle.bundle_hash)
        assert loaded.bundle_hash == bundle.bundle_hash
        assert loaded.release_tag == "v7.2.0"
        assert loaded.verify_integrity() is True

    def test_store_directory_form_and_env_override(self, tmp_path, monkeypatch):
        """路径优先级：path > directory > 环境变量 > 默认（默认即 data/releases）"""
        by_directory = ReleaseStore(directory=str(tmp_path / "d1"))
        assert by_directory.path == tmp_path / "d1" / LEDGER_FILENAME
        monkeypatch.setenv(ENV_BUNDLES_DIR, str(tmp_path / "d2"))
        assert bundles_dir() == tmp_path / "d2"
        assert ReleaseStore().path == tmp_path / "d2" / LEDGER_FILENAME
        monkeypatch.delenv(ENV_BUNDLES_DIR, raising=False)
        assert bundles_dir() == type(tmp_path)(DEFAULT_BUNDLES_DIR)
        assert DEFAULT_BUNDLES_DIR.replace("\\", "/").startswith("data/")

    def test_snapshot_bundle_puts_into_store(self, tmp_path):
        """snapshot_bundle：构造 + 入库（"升级前自动快照入 D5"）"""
        store = _store(tmp_path)
        bundle = snapshot_bundle({n: BASE_VERSIONS[n] for n in COMPONENT_NAMES},
                                 store=store, release_tag="v7.2.0")
        assert store.count() == 1
        assert store.require(bundle.bundle_hash).release_tag == "v7.2.0"
        assert len(list_incidents(directory=str(tmp_path / "none"))) == 0


# ════════════════════════════════════════════════════════════
#  4. 原子性闸门（headline：禁止只回技能不回代码）
# ════════════════════════════════════════════════════════════


class TestAtomicGate:
    """check_atomic_request：真子集一律拒绝 + L4"""

    def test_partial_component_subset_rejected_with_missing_details(self, tmp_path):
        """只传 skills → 拒绝，且 missing 含 code 与其余三组件（headline 验收）"""
        incidents = tmp_path / "incidents"
        with pytest.raises(PartialRollbackError) as exc:
            check_atomic_request([COMPONENT_SKILLS], incidents_dir=str(incidents))
        err = exc.value
        assert COMPONENT_CODE in err.missing
        assert set(err.missing) == {COMPONENT_CODE, COMPONENT_WEIGHTS,
                                    COMPONENT_DATA_BASELINE, COMPONENT_MANIFEST}
        assert err.requested == [COMPONENT_SKILLS]
        assert err.extra == []
        assert "P7.2-15" in str(err)
        assert "拒绝部分回滚" in str(err)

    @pytest.mark.parametrize("subset", [
        [COMPONENT_CODE],
        [COMPONENT_SKILLS, COMPONENT_CODE],
        [COMPONENT_MANIFEST],
        [COMPONENT_SKILLS, COMPONENT_WEIGHTS, COMPONENT_DATA_BASELINE],
    ])
    def test_every_true_subset_is_rejected(self, subset, tmp_path):
        """任何真子集（含四组件）都被拒——原子单位只有「整包」一种"""
        with pytest.raises(PartialRollbackError) as exc:
            check_atomic_request(subset, trigger_incident=False)
        assert exc.value.missing or exc.value.extra

    def test_rejection_creates_l4_incident_on_disk(self, tmp_path):
        """拒绝即触发 L4：事故卡落盘（显式目录）+ incident_id 非空"""
        incidents = tmp_path / "incidents"
        with pytest.raises(PartialRollbackError) as exc:
            check_atomic_request([COMPONENT_SKILLS], tenant_id="tenant-x",
                                 incidents_dir=str(incidents))
        incident_id = exc.value.incident_id
        assert incident_id
        card = load_incident(incident_id, directory=str(incidents))
        assert card is not None
        assert card.severity is HealLevel.L4
        assert card.tenant_id == "tenant-x"
        assert card.detail["requested"] == [COMPONENT_SKILLS]
        assert COMPONENT_CODE in card.detail["missing"]
        assert card.detail["component_labels"][COMPONENT_CODE] == COMPONENT_LABELS[COMPONENT_CODE]
        assert len(list_incidents(directory=str(incidents), severity="L4")) == 1

    def test_full_bundle_requests_pass_the_gate(self, tmp_path):
        """None（不传）与恰好五组件全集都通过（不抛、不开事故卡）"""
        incidents = tmp_path / "incidents"
        assert check_atomic_request(None, incidents_dir=str(incidents)) is None
        assert check_atomic_request(list(COMPONENT_NAMES), incidents_dir=str(incidents)) is None
        assert check_atomic_request([], incidents_dir=str(incidents)) is None
        assert check_atomic_request(["", "  "], incidents_dir=str(incidents)) is None
        assert not incidents.exists()

    def test_unknown_component_name_is_rejected(self, tmp_path):
        """多出未知组件（即便五组件齐）也拒绝，并在 extra 中列出"""
        with pytest.raises(PartialRollbackError) as exc:
            check_atomic_request(
                [COMPONENT_CODE, COMPONENT_SKILLS, COMPONENT_WEIGHTS,
                 COMPONENT_DATA_BASELINE, COMPONENT_MANIFEST, "bogus"],
                incidents_dir=str(tmp_path / "incidents"))
        assert exc.value.extra == ["bogus"]
        assert exc.value.missing == []
        assert exc.value.incident_id

    def test_duplicate_component_names_are_rejected_with_evidence(self, tmp_path):
        """重复组件名被拒，且报错文案/事故卡 detail 都写明「重复=…」

        【实现期修正】判定加入 `duplicates`，故 missing/extra 皆空时也有可定位的证据
        （此前只靠 `len(requested) != 5` 兜住，文案里查不出为什么被拒）。
        """
        incidents = tmp_path / "incidents"
        with pytest.raises(PartialRollbackError) as exc:
            check_atomic_request(
                [COMPONENT_CODE, COMPONENT_CODE, COMPONENT_SKILLS, COMPONENT_WEIGHTS,
                 COMPONENT_DATA_BASELINE, COMPONENT_MANIFEST],
                incidents_dir=str(incidents))
        err = exc.value
        assert err.missing == [] and err.extra == []
        assert "重复=['code']" in str(err)
        card = load_incident(err.incident_id, directory=str(incidents))
        assert card is not None and card.severity is HealLevel.L4
        assert card.detail["duplicates"] == [COMPONENT_CODE]
        assert card.detail["missing"] == [] and card.detail["extra"] == []

    def test_extra_components_are_deduped_and_sorted(self, tmp_path):
        """extra 去重 + 排序（同一未知组件重复出现只报一次）"""
        with pytest.raises(PartialRollbackError) as exc:
            check_atomic_request([COMPONENT_SKILLS, "zeta", "alpha", "zeta"],
                                 trigger_incident=False)
        assert exc.value.extra == ["alpha", "zeta"]
        assert "alpha" in str(exc.value) and "zeta" in str(exc.value)
        # 真子集 + 未知组件：两类证据同时给出
        assert exc.value.missing

    def test_trigger_incident_false_creates_no_incident_file(self, tmp_path):
        """dry 校验（trigger_incident=False）：拒绝但不留事故卡"""
        incidents = tmp_path / "incidents"
        with pytest.raises(PartialRollbackError) as exc:
            check_atomic_request([COMPONENT_SKILLS], trigger_incident=False,
                                 incidents_dir=str(incidents))
        assert exc.value.incident_id == ""
        assert not incidents.exists()
        assert list_incidents(directory=str(incidents)) == []

    def test_subset_with_context_keeps_trace_ids(self, tmp_path):
        """拒绝路径携带调用方上下文（fatal_change / trace_ids 存入事故卡）"""
        incidents = tmp_path / "incidents"
        with pytest.raises(PartialRollbackError) as exc:
            check_atomic_request([COMPONENT_SKILLS], incidents_dir=str(incidents),
                                 context={"fatal_change": "c0ffee", "trace_ids": ["t-9"]})
        card = load_incident(exc.value.incident_id, directory=str(incidents))
        assert card.fatal_change == "c0ffee"
        assert card.trace_ids == ["t-9"]


# ════════════════════════════════════════════════════════════
#  5. 事后一致性校验
# ════════════════════════════════════════════════════════════


class TestVerifyConsistency:
    """current vs 目标整包：consistent / diverged / partial / unknown"""

    def test_all_match_is_consistent(self, tmp_path):
        """五组件全部等于目标（版本或内容哈希皆可）→ consistent，无事故卡"""
        bundle = _bundle()
        incidents = tmp_path / "incidents"
        by_version = check_consistency(bundle.component_versions(), bundle,
                                        incidents_dir=str(incidents))
        assert by_version["status"] == "consistent"
        assert sorted(by_version["matched"]) == sorted(COMPONENT_NAMES)
        assert by_version["mismatched"] == [] and by_version["unknown"] == []
        assert by_version["incident_id"] == ""
        by_hash = check_consistency(bundle.component_hashes(), bundle,
                                     incidents_dir=str(incidents))
        assert by_hash["status"] == "consistent"
        assert not incidents.exists()

    def test_none_match_is_diverged(self, tmp_path):
        """五组件全不等于目标 = 尚未开始回滚 → diverged（不是部分回滚）"""
        bundle = _bundle()
        old = {name: BASE_VERSIONS[name] + "-old" for name in COMPONENT_NAMES}
        result = check_consistency(old, bundle, incidents_dir=str(tmp_path / "incidents"))
        assert result["status"] == "diverged"
        assert result["matched"] == []
        assert sorted(result["mismatched"]) == sorted(COMPONENT_NAMES)
        assert result["incident_id"] == ""

    def test_mixed_match_is_partial_and_creates_l4_incident(self, tmp_path):
        """部分匹配 = 部分回滚残留 → partial + L4 事故卡（事后检测互补于事前拒绝）"""
        bundle = _bundle()
        incidents = tmp_path / "incidents"
        current = bundle.component_versions()
        current[COMPONENT_SKILLS] = "skills-0.9.0"      # 只回了技能
        current[COMPONENT_WEIGHTS] = "weights-old"
        result = check_consistency(current, bundle, tenant_id="tenant-z",
                                    incidents_dir=str(incidents))
        assert result["status"] == "partial"
        assert set(result["matched"]) == {COMPONENT_CODE, COMPONENT_DATA_BASELINE,
                                          COMPONENT_MANIFEST}
        assert set(result["mismatched"]) == {COMPONENT_SKILLS, COMPONENT_WEIGHTS}
        assert result["incident_id"]
        card = load_incident(result["incident_id"], directory=str(incidents))
        assert card.severity is HealLevel.L4
        assert card.tenant_id == "tenant-z"
        assert card.detail["bundle_hash"] == bundle.bundle_hash
        assert set(card.detail["mismatched"]) == {COMPONENT_SKILLS, COMPONENT_WEIGHTS}

    def test_partial_without_trigger_incident_creates_nothing(self, tmp_path):
        """partial 但 trigger_incident=False → 只报告，不开事故卡"""
        bundle = _bundle()
        incidents = tmp_path / "incidents"
        current = bundle.component_versions()
        current[COMPONENT_SKILLS] = "skills-0.9.0"
        result = check_consistency(current, bundle, trigger_incident=False,
                                    incidents_dir=str(incidents))
        assert result["status"] == "partial"
        assert result["incident_id"] == ""
        assert list_incidents(directory=str(incidents)) == []

    def test_unknown_values_take_precedence(self, tmp_path):
        """任一组件缺失/为空 → unknown（不误判为一致或部分）"""
        bundle = _bundle()
        current = bundle.component_versions()
        current[COMPONENT_MANIFEST] = ""
        result = check_consistency(current, bundle, incidents_dir=str(tmp_path / "inc"))
        assert result["status"] == "unknown"
        assert result["unknown"] == [COMPONENT_MANIFEST]
        assert result["incident_id"] == ""
        result2 = check_consistency({}, bundle, incidents_dir=str(tmp_path / "inc"))
        assert result2["status"] == "unknown"
        assert sorted(result2["unknown"]) == sorted(COMPONENT_NAMES)

    def test_extra_keys_in_current_are_ignored(self, tmp_path):
        """current 多给键无影响（只按五组件比对）"""
        bundle = _bundle()
        current = bundle.component_versions()
        current["eval_anchor"] = "whatever"
        result = check_consistency(current, bundle, incidents_dir=str(tmp_path / "inc"))
        assert result["status"] == "consistent"


# ════════════════════════════════════════════════════════════
#  6. 回滚计划与入口
# ════════════════════════════════════════════════════════════


class TestRollbackPlanAndEntry:
    """plan_rollback（纯计算）+ rollback_bundle（唯一副作用入口）"""

    def test_plan_rollback_has_exactly_five_moves(self):
        """计划恒为五组件移动；is_full_bundle() True（计划的形状即原子性）"""
        target = _bundle()
        source = build_bundle({
            name: BundleComponent(name, BASE_VERSIONS[name] + "-old",
                                  f"{HASH_ALGO}:old-{name}")
            for name in COMPONENT_NAMES})
        plan = plan_rollback(target, from_bundle=source)
        assert plan.component_count() == 5
        assert plan.is_full_bundle() is True
        assert set(plan.moves) == set(COMPONENT_NAMES)
        assert plan.bundle_hash == target.bundle_hash
        assert plan.from_bundle_hash == source.bundle_hash
        for name in COMPONENT_NAMES:
            move = plan.moves[name]
            assert move["to_version"] == BASE_VERSIONS[name]
            assert move["to_hash"] == target.components[name].hash
            assert move["from_version"] == BASE_VERSIONS[name] + "-old"
            assert move["from_hash"] == source.components[name].hash
        assert plan.dry_run is True and plan.applied is False

    def test_plan_rollback_uses_explicit_current(self):
        """给了 current 就不看 from_bundle 的版本（from_hash 仍取 from_bundle）"""
        target = _bundle()
        plan = plan_rollback(target, current={"code": "git:zzz"})
        assert plan.component_count() == 5
        assert plan.moves[COMPONENT_CODE]["from_version"] == "git:zzz"
        assert plan.moves[COMPONENT_SKILLS]["from_version"] == ""
        assert plan.from_bundle_hash == ""
        assert plan.to_dict()["component_count"] == 5

    def test_dry_run_without_applier_has_no_side_effects(self, tmp_path):
        """applier=None → 强制 dry-run：applied False，无任何落地动作"""
        store = _store(tmp_path)
        bundle = store.put(_bundle())
        incidents = tmp_path / "incidents"
        ledger_before = store.path.read_bytes()
        plan = rollback_bundle(bundle.bundle_hash, store=store, current=BASE_VERSIONS,
                               incidents_dir=str(incidents))
        assert plan.applied is False
        assert plan.dry_run is True
        assert plan.component_count() == 5
        assert "dry-run" in plan.note
        assert store.path.read_bytes() == ledger_before
        assert list_incidents(directory=str(incidents)) == []

    def test_explicit_dry_run_does_not_call_applier(self, tmp_path):
        """显式 dry_run=True：即便给了 applier 也不调用（安全开关优先）"""
        store = _store(tmp_path)
        bundle = store.put(_bundle())
        seen = []
        plan = rollback_bundle(bundle.bundle_hash, store=store, dry_run=True,
                               applier=lambda p: seen.append(p),
                               incidents_dir=str(tmp_path / "incidents"))
        assert plan.applied is False and plan.dry_run is True
        assert plan.note == "显式 dry_run"
        assert seen == []

    def test_applier_receives_five_component_plan_and_marks_applied(self, tmp_path):
        """applier 收到五组件计划 → applied True（落地动作只由调用方注入）"""
        store = _store(tmp_path)
        bundle = store.put(_bundle())
        seen = []

        class _Recorder:
            def __init__(self):
                self.calls = []

            def __call__(self, plan):
                self.calls.append(plan)
                return {"ok": True}

        recorder = _Recorder()
        plan = rollback_bundle(bundle.bundle_hash, store=store, current=BASE_VERSIONS,
                               applier=recorder, incidents_dir=str(tmp_path / "incidents"))
        assert plan.applied is True and plan.dry_run is False
        assert recorder.calls == [plan]
        assert recorder.calls[0].component_count() == 5
        assert recorder.calls[0].is_full_bundle() is True
        assert set(recorder.calls[0].moves) == set(COMPONENT_NAMES)
        assert plan.bundle_id == bundle.bundle_id

    def test_applier_failure_propagates_and_creates_l4(self, tmp_path):
        """applier 抛错 → 原样上抛 + L4 事故卡（回滚失败即补偿/快照路径）"""
        store = _store(tmp_path)
        bundle = store.put(_bundle())
        incidents = tmp_path / "incidents"

        def _boom(plan):
            raise RuntimeError("模拟落地失败")

        with pytest.raises(RuntimeError) as exc:
            rollback_bundle(bundle.bundle_hash, store=store, applier=_boom,
                            incidents_dir=str(incidents))
        assert "模拟落地失败" in str(exc.value)
        cards = list_incidents(directory=str(incidents), severity="L4")
        assert len(cards) == 1
        assert cards[0].detail["bundle_hash"] == bundle.bundle_hash
        assert "整包回滚落地失败" in cards[0].root_cause

    def test_unknown_hash_raises_bundle_not_found(self, tmp_path):
        """目标整包不在台账 → BundleNotFoundError（且不产生事故卡）"""
        store = _store(tmp_path)
        store.put(_bundle())
        incidents = tmp_path / "incidents"
        with pytest.raises(BundleNotFoundError):
            rollback_bundle("sha256:deadbeef", store=store, incidents_dir=str(incidents))
        assert list_incidents(directory=str(incidents)) == []

    def test_tampered_ledger_bundle_refuses_rollback(self, tmp_path):
        """台账里的整包自校验失败 → BundleIntegrityError，拒绝回滚"""
        store = _store(tmp_path)
        bundle = _bundle()
        store._bundles[bundle.bundle_hash] = bundle   # 绕过 put 直接塞入（模拟台账被改）
        bundle.components[COMPONENT_CODE] = BundleComponent(
            COMPONENT_CODE, "git:evil", f"{HASH_ALGO}:evil")
        with pytest.raises(BundleIntegrityError):
            rollback_bundle(bundle.bundle_hash, store=store,
                            incidents_dir=str(tmp_path / "incidents"))

    def test_partial_request_is_rejected_before_any_store_access(self, tmp_path):
        """原子性闸门先于台账读取：部分回滚请求即使 hash 不存在也先报 PartialRollbackError"""
        store = _store(tmp_path)
        incidents = tmp_path / "incidents"
        with pytest.raises(PartialRollbackError):
            rollback_bundle("sha256:whatever", store=store, components=[COMPONENT_SKILLS],
                            incidents_dir=str(incidents))
        assert len(list_incidents(directory=str(incidents))) == 1

    def test_rollback_with_full_component_set_succeeds(self, tmp_path):
        """显式传五组件全集 = 合法整包回滚"""
        store = _store(tmp_path)
        bundle = store.put(_bundle())
        plan = rollback_bundle(bundle.bundle_hash, store=store,
                               components=list(COMPONENT_NAMES),
                               incidents_dir=str(tmp_path / "incidents"))
        assert plan.is_full_bundle() is True
        assert plan.applied is False  # 仍未给 applier → dry-run


# ════════════════════════════════════════════════════════════
#  7. 内容哈希工具
# ════════════════════════════════════════════════════════════


class TestHashUtils:
    """hash_path / hash_mapping：只读、显式路径、确定性"""

    def test_hash_path_file_deterministic_and_content_sensitive(self, tmp_path):
        """文件：同内容同哈希；内容变化必变；空文件有独立占位"""
        target = tmp_path / "a.bin"
        target.write_bytes(b"hello world")
        first = hash_path(target)
        assert first == hash_path(str(target))
        assert first.startswith(f"{HASH_ALGO}:")
        target.write_bytes(b"hello world!")
        assert hash_path(target) != first
        empty = tmp_path / "empty.bin"
        empty.write_bytes(b"")
        assert hash_path(empty) != hash_path(target)
        assert hash_path(empty) == hash_path(empty)

    def test_hash_path_directory_deterministic_and_content_sensitive(self, tmp_path):
        """目录：相对路径排序后累加；内容/文件增删都会改变哈希"""
        base = tmp_path / "skills"
        (base / "sub").mkdir(parents=True)
        (base / "a.py").write_text("print(1)", encoding="utf-8")
        (base / "sub" / "b.py").write_text("print(2)", encoding="utf-8")
        first = hash_path(base)
        assert hash_path(base) == first
        (base / "sub" / "b.py").write_text("print(3)", encoding="utf-8")
        assert hash_path(base) != first
        second = hash_path(base)
        (base / "c.py").write_text("print(4)", encoding="utf-8")
        assert hash_path(base) not in (first, second)
        # 同内容复制到另一目录 → 同一哈希（按相对路径，不含绝对路径）
        clone = tmp_path / "clone"
        (clone / "sub").mkdir(parents=True)
        (clone / "a.py").write_text("print(1)", encoding="utf-8")
        (clone / "sub" / "b.py").write_text("print(3)", encoding="utf-8")
        (clone / "c.py").write_text("print(4)", encoding="utf-8")
        assert hash_path(clone) == hash_path(base)

    def test_hash_path_missing_raises(self, tmp_path):
        """不存在的路径 → BundleValidationError（绝不猜仓库位置）"""
        with pytest.raises(BundleValidationError) as exc:
            hash_path(tmp_path / "nope")
        assert "路径不存在" in str(exc.value)

    def test_hash_mapping_order_independent_and_stable(self):
        """hash_mapping：sort_keys → 键序无关；值变化必变"""
        first = hash_mapping({"b": 2, "a": 1})
        assert first == hash_mapping({"a": 1, "b": 2})
        assert first.startswith(f"{HASH_ALGO}:")
        assert first != hash_mapping({"a": 1, "b": 3})
        assert first != hash_mapping({"a": 1})
        assert hash_mapping({}) == hash_mapping({})

    def test_hash_mapping_handles_non_json_values(self):
        """不可 JSON 序列化的值走 default=str 兜底（不抛）"""
        class _Weird:
            def __repr__(self):
                return "weird()"

        first = hash_mapping({"x": _Weird()})
        assert first.startswith(f"{HASH_ALGO}:")
        assert first == hash_mapping({"x": _Weird()})
