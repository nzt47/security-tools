"""TASK-S5-02 L0 锚单测（`agent/eval/anchor.py`）

**本文件是"系统不可写"的验收证据**：
- 位置独立性（锚不在任何系统数据目录内，双向校验）；
- 无写 API + 写入守门（扰动尝试一律 `AnchorReadOnlyError`）；
- 哈希锚定 fail-closed（内容/条数/参考解任一变化 → 拒绝评测）。

全部用例只在 `tmp_path` 内构建锚，**不触碰仓库内真实锚目录**（真实锚另在
`test_eval_datasets.py` 做只读校验，`test_eval_anchor.py::test_repo_anchor_integrity` 只读校验）。
"""

from __future__ import annotations

import json
import os

import pytest

from agent.eval import anchor as A
from agent.eval import cases as C

# ════════════════════════════════════════════════════════════
#  夹具：在 tmp 内构建一个 20 条的小型 L0 锚
# ════════════════════════════════════════════════════════════


def _case(case_id: str, scenario: str) -> C.EvalCase:
    return C.EvalCase.from_dict({
        "id": case_id, "layer": "L0", "scenario": scenario, "title": case_id,
        "input": {"prompt": "p"},
        "expect": [{"checker": "nonempty", "path": "value", "args": {}, "why": "w"}],
    })


def _case_set() -> C.EvalCaseSet:
    cases = []
    for scenario in C.SCENARIOS:
        for index in range(4 if len(cases) < 8 else 3):
            cases.append(_case(f"L0-{scenario}-{index + 1:02d}", scenario))
        if len(cases) >= 20:
            break
    cases = cases[:20]
    return C.EvalCaseSet(layer="L0", cases=tuple(cases))


@pytest.fixture
def frozen_anchor(tmp_path):
    """在 tmp 内冻结一个锚（走 freeze_anchor 的正规路径）"""
    case_set = _case_set()
    answers = {case.id: {"value": f"answer-{case.id}"} for case in case_set.cases}
    root = str(tmp_path / "l0_anchor")
    manifest = A.freeze_anchor(case_set=case_set, reference=answers,
                               frozen_by="unit-test", review_note="fixture",
                               root=root, allow_write=True)
    return {"root": root, "manifest": manifest, "answers": answers,
            "case_set": case_set, "store": A.AnchorStore(root)}


# ════════════════════════════════════════════════════════════
#  位置与哈希
# ════════════════════════════════════════════════════════════


class TestLocation:
    def test_default_anchor_dir_is_outside_data(self):
        root = A.resolve_anchor_dir()
        assert root.endswith(os.path.join("eval", "l0_anchor"))
        for name, path in A.system_data_roots().items():
            assert not A.is_within(root, path), name

    def test_env_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv(A.ENV_ANCHOR_DIR, str(tmp_path))
        assert A.resolve_anchor_dir() == str(tmp_path)

    def test_explicit_beats_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv(A.ENV_ANCHOR_DIR, str(tmp_path / "env"))
        explicit = str(tmp_path / "explicit")
        assert A.resolve_anchor_dir(explicit) == explicit

    def test_is_within(self, tmp_path):
        assert A.is_within(str(tmp_path / "a" / "b"), str(tmp_path))
        assert not A.is_within(str(tmp_path.parent / "zzz"), str(tmp_path))
        assert A.is_within(str(tmp_path), str(tmp_path))
        assert not A.is_within("", str(tmp_path))

    def test_independence_ok_for_external_dir(self, tmp_path):
        report = A.assert_independent_of_system_data(str(tmp_path))
        assert report["independent"] and not report["violations"]

    @pytest.mark.parametrize("relative", ["data", os.path.join("data", "events"),
                                          os.path.join("data", "digestion")])
    def test_independence_rejects_anchor_inside_system_data(self, relative):
        with pytest.raises(A.AnchorIndependenceError):
            A.assert_independent_of_system_data(
                os.path.join(A._REPO_ROOT, relative, "anchor"))

    def test_independence_rejects_system_data_inside_anchor(self, tmp_path):
        """双向：把锚设为仓库根（系统数据目录落在锚内）也必须被拒"""
        with pytest.raises(A.AnchorIndependenceError):
            A.assert_independent_of_system_data(A._REPO_ROOT)

    def test_file_sha256_missing_is_empty(self, tmp_path):
        assert A.file_sha256(str(tmp_path / "nope")) == ""

    def test_text_sha256_is_line_ending_independent(self, tmp_path):
        """锚的哈希口径必须跨平台一致：CRLF 与 LF 的同一文本哈希相同

        （本仓库 `core.autocrlf=true`：若用原始字节，Windows/Linux checkout 会让
        "参考解被改动"的校验假阳性 → fail-closed 会误拒评测）
        """
        lf = tmp_path / "lf.json"
        crlf = tmp_path / "crlf.json"
        lf.write_bytes(b'{"a": 1}\n')
        crlf.write_bytes(b'{"a": 1}\r\n')
        assert A.text_sha256(str(lf)) == A.text_sha256(str(crlf))
        assert A.file_sha256(str(lf)) != A.file_sha256(str(crlf))
        assert A.text_sha256(str(tmp_path / "missing")) == ""

    def test_readonly_status_reports_files(self, frozen_anchor):
        status = A.readonly_status(frozen_anchor["root"])
        assert status["anchor_dir"] == frozen_anchor["root"]
        assert {row["file"] for row in status["files"]} == {
            A.CASES_FILENAME, A.MANIFEST_FILENAME, A.REFERENCE_FILENAME}
        assert all(row["exists"] for row in status["files"])


# ════════════════════════════════════════════════════════════
#  系统不可写（扰动尝试被拒）
# ════════════════════════════════════════════════════════════


class TestSystemCannotWrite:
    def test_store_has_no_write_methods_that_succeed(self, frozen_anchor):
        store = frozen_anchor["store"]
        with pytest.raises(A.AnchorReadOnlyError):
            store.write_cases(frozen_anchor["case_set"])
        with pytest.raises(A.AnchorReadOnlyError):
            store.update_case("L0-S1_fix_bug-01", {"title": "hacked"})
        with pytest.raises(A.AnchorReadOnlyError):
            store.delete_case("L0-S1_fix_bug-01")
        with pytest.raises(A.AnchorReadOnlyError):
            store.write_reference({"L0-S1_fix_bug-01": {}})
        with pytest.raises(A.AnchorReadOnlyError):
            store.write_manifest(frozen_anchor["manifest"])

    def test_guard_write_blocks_anchor_paths(self, frozen_anchor):
        for name in (A.CASES_FILENAME, A.MANIFEST_FILENAME, "sub/other.json"):
            with pytest.raises(A.AnchorReadOnlyError):
                A.guard_write(os.path.join(frozen_anchor["root"], name),
                              frozen_anchor["root"])

    def test_guard_write_allows_other_paths(self, tmp_path, frozen_anchor):
        A.guard_write(str(tmp_path / "elsewhere.json"), frozen_anchor["root"])
        A.guard_write(str(tmp_path / "x.json"))  # 默认锚根之外

    def test_freeze_requires_explicit_capability(self, tmp_path):
        with pytest.raises(A.AnchorReadOnlyError):
            A.freeze_anchor(case_set=_case_set(), reference={}, frozen_by="x",
                            root=str(tmp_path / "a"))

    def test_freeze_requires_human_signature(self, tmp_path):
        with pytest.raises(A.AnchorReadOnlyError):
            A.freeze_anchor(case_set=_case_set(), reference={}, frozen_by="",
                            root=str(tmp_path / "a"), allow_write=True)

    def test_freeze_rejects_invalid_case_set(self, tmp_path):
        bad = C.EvalCaseSet(layer="L0", cases=(_case("L0-S1_fix_bug-01",
                                                     C.SCENARIO_S1_FIX_BUG),))
        with pytest.raises(C.CaseSetError):
            A.freeze_anchor(case_set=bad, reference={}, frozen_by="x",
                            root=str(tmp_path / "a"), allow_write=True)

    def test_freeze_requires_reference_for_every_case(self, tmp_path):
        with pytest.raises(C.CaseSetError):
            A.freeze_anchor(case_set=_case_set(), reference={}, frozen_by="x",
                            root=str(tmp_path / "a"), allow_write=True)

    def test_written_files_are_readable_and_hashed(self, frozen_anchor):
        manifest = frozen_anchor["manifest"]
        assert manifest.count == 20
        assert len(manifest.entries) == 20
        assert manifest.caseset_sha256 == frozen_anchor["case_set"].caseset_sha256
        assert manifest.reference_sha256 == A.file_sha256(
            os.path.join(frozen_anchor["root"], A.REFERENCE_FILENAME))


# ════════════════════════════════════════════════════════════
#  完整性校验（fail-closed）
# ════════════════════════════════════════════════════════════


class TestIntegrity:
    def test_verify_ok(self, frozen_anchor):
        report = A.verify_anchor(frozen_anchor["root"])
        assert report["ok"] and report["problems"] == []
        assert report["count"] == report["manifest_count"] == 20
        assert report["reference"]["ok"]

    def test_load_verifies_and_marks_frozen(self, frozen_anchor):
        case_set = frozen_anchor["store"].load()
        assert len(case_set) == 20 and case_set.frozen

    def test_content_tamper_detected(self, frozen_anchor):
        path = os.path.join(frozen_anchor["root"], A.CASES_FILENAME)
        data = json.loads(open(path, encoding="utf-8").read())
        data["cases"][0]["title"] = "tampered"
        open(path, "w", encoding="utf-8", newline="\n").write(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        report = A.verify_anchor(frozen_anchor["root"])
        assert not report["ok"]
        assert any("整体哈希不一致" in p or "逐条哈希不一致" in p
                   for p in report["problems"])
        with pytest.raises(A.AnchorIntegrityError):
            frozen_anchor["store"].load()

    def test_removed_case_detected(self, frozen_anchor):
        path = os.path.join(frozen_anchor["root"], A.CASES_FILENAME)
        data = json.loads(open(path, encoding="utf-8").read())
        removed = data["cases"].pop(0)["id"]
        open(path, "w", encoding="utf-8", newline="\n").write(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        report = A.verify_anchor(frozen_anchor["root"])
        assert removed in report["missing"]
        assert not report["ok"]

    def test_added_case_detected(self, frozen_anchor):
        path = os.path.join(frozen_anchor["root"], A.CASES_FILENAME)
        data = json.loads(open(path, encoding="utf-8").read())
        data["cases"].append({"id": "L0-S1_fix_bug-99", "layer": "L0",
                              "scenario": C.SCENARIO_S1_FIX_BUG, "title": "extra",
                              "input": {}, "expect": [{"checker": "nonempty",
                                                       "path": "value"}]})
        open(path, "w", encoding="utf-8", newline="\n").write(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        report = A.verify_anchor(frozen_anchor["root"])
        assert "L0-S1_fix_bug-99" in report["unexpected"]
        assert not report["ok"]

    def test_reference_tamper_detected(self, frozen_anchor):
        path = os.path.join(frozen_anchor["root"], A.REFERENCE_FILENAME)
        data = json.loads(open(path, encoding="utf-8").read())
        first = sorted(data["answers"])[0]
        data["answers"][first] = {"value": "tampered"}
        open(path, "w", encoding="utf-8", newline="\n").write(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        report = A.verify_anchor(frozen_anchor["root"])
        assert not report["ok"]
        assert any("参考解哈希不一致" in p for p in report["problems"])

    def test_missing_manifest_reported(self, tmp_path):
        os.makedirs(tmp_path / "a", exist_ok=True)
        report = A.verify_anchor(str(tmp_path / "a"))
        assert not report["ok"]
        assert any("manifest 缺失" in p for p in report["problems"])

    def test_corrupt_manifest_reported(self, tmp_path):
        root = tmp_path / "a"
        root.mkdir()
        (root / A.MANIFEST_FILENAME).write_text("{bad", encoding="utf-8")
        report = A.verify_anchor(str(root))
        assert not report["ok"] and any("manifest" in p for p in report["problems"])

    def test_load_manifest_rejects_unknown_schema(self, tmp_path):
        path = tmp_path / "manifest.json"
        path.write_text(json.dumps({"schema": "nope"}), encoding="utf-8")
        with pytest.raises(A.AnchorError):
            A.load_manifest(str(path))

    def test_load_reference_returns_answers(self, frozen_anchor):
        answers = frozen_anchor["store"].load_reference()
        assert set(answers) == set(frozen_anchor["answers"])
        assert answers["L0-S1_fix_bug-01"] == {"value": "answer-L0-S1_fix_bug-01"}

    def test_load_reference_missing_returns_empty(self, tmp_path):
        store = A.AnchorStore(str(tmp_path))
        assert store.load_reference() == {}

    def test_manifest_roundtrip(self, frozen_anchor):
        loaded = A.load_manifest(os.path.join(frozen_anchor["root"], A.MANIFEST_FILENAME))
        assert loaded.to_dict() == frozen_anchor["manifest"].to_dict()

    def test_manifest_from_dict_rejects_bad_entries(self):
        with pytest.raises(A.AnchorError):
            A.AnchorManifest.from_dict({"entries": []})


# ════════════════════════════════════════════════════════════
#  OS 级只读（可选硬化）
# ════════════════════════════════════════════════════════════


class TestOsReadonly:
    def test_set_and_unset_readonly(self, frozen_anchor):
        target = os.path.join(frozen_anchor["root"], A.CASES_FILENAME)
        try:
            applied = A.set_os_readonly(frozen_anchor["root"], enable=True)
            assert all("error" not in row for row in applied["changed"])
            assert not os.access(target, os.W_OK)
            restored = A.set_os_readonly(frozen_anchor["root"], enable=False)
            assert restored["enable"] is False
        finally:
            A.set_os_readonly(frozen_anchor["root"], enable=False)
        assert os.access(target, os.W_OK)


# ════════════════════════════════════════════════════════════
#  真实仓库锚（只读校验；不改动）
# ════════════════════════════════════════════════════════════


def test_repo_anchor_integrity():
    """仓库内 L0 锚的哈希锚定必须自洽（本任务把它当作冻结资产守护）"""
    report = A.verify_anchor(A.DEFAULT_ANCHOR_DIR)
    assert report["ok"], report["problems"]
    assert report["count"] == 20
    assert report["independent"]["independent"]
    assert report["reference"]["ok"]
