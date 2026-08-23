"""评估集样本校验测试（任务1 Step 2/Step 6）

覆盖:
    1. 合法样本池 → PASS（0 非法）
    2. 非法字段逐类检出（id 重复 / category 错配 / task 缺失 /
       expected_output 非法 / difficulty 非法 / source 非法 / input_hash 非法/不匹配）
    3. input_hash 跨类别去重
    4. manifest 一致性（漏登记 / 登记 id 不存在 / current 缺失）
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from eval_samples_validate import (  # noqa: E402
    ValidationReport,
    compute_input_hash,
    validate_samples,
)


def write_category(base: Path, category: str, samples) -> None:
    d = base / category
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{category}.json").write_text(
        json.dumps(samples, ensure_ascii=False), encoding="utf-8")


def make_sample(sid: str, category: str = "search", *,
                task: str = "查询云枢的定义", input_meta=None,
                difficulty: str = "SIMPLE", source: str = "manual",
                expected=None) -> dict:
    input_meta = input_meta or {"query": "云枢"}
    meta = {
        "input": input_meta,
        "difficulty": difficulty,
        "source": source,
        "input_hash": compute_input_hash(category, task, input_meta),
    }
    return {
        "id": sid, "category": category, "task": task,
        "expected_output": expected or {"type": "contains", "values": ["云枢"]},
        "created_at": "2026-08-22T00:00:00", "metadata": meta,
    }


def write_manifest(base: Path, categories: dict) -> None:
    manifest = {
        "schema_version": 1,
        "current": "v1",
        "versions": {"v1": {
            "description": "test", "created_at": "2026-08-22T00:00:00",
            "categories": categories,
        }},
    }
    (base / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


def build_pool(base: Path) -> dict:
    """构造合法 2 类样本池 + manifest"""
    write_category(base, "search", [
        make_sample("s1", "search", task="查询云枢定义"),
        make_sample("s2", "search", task="查询上海天气", input_meta={"query": "上海天气"}),
    ])
    write_category(base, "code", [
        make_sample("c1", "code", task="实现求和函数", input_meta={"n": 5},
                    difficulty="NORMAL"),
    ])
    write_manifest(base, {"search": ["s1", "s2"], "code": ["c1"]})
    return {"search": ["s1", "s2"], "code": ["c1"]}


# ════════════════════════════════════════════════════════════
#  1. 合法池
# ════════════════════════════════════════════════════════════

class TestValidPool:
    def test_valid_pool_passes(self, tmp_path):
        build_pool(tmp_path)
        report = validate_samples(str(tmp_path))
        assert report.ok, report.summary()
        assert report.total == 3
        assert report.per_category == {"search": 2, "code": 1}

    def test_manifest_missing_is_reported(self, tmp_path):
        write_category(tmp_path, "search", [make_sample("s1", "search")])
        report = validate_samples(str(tmp_path))
        assert not report.ok
        assert any("manifest 缺失" in m for m in report.manifest_issues)

    def test_manifest_current_not_registered(self, tmp_path):
        write_category(tmp_path, "search", [make_sample("s1", "search")])
        (tmp_path / "manifest.json").write_text(json.dumps({
            "current": "v2", "versions": {"v1": {"categories": {"search": ["s1"]}}},
        }), encoding="utf-8")
        report = validate_samples(str(tmp_path))
        assert any("current" in m for m in report.manifest_issues)


# ════════════════════════════════════════════════════════════
#  2. 非法字段逐类检出
# ════════════════════════════════════════════════════════════

class TestInvalidFields:
    def _report_with(self, tmp_path, sample):
        write_category(tmp_path, "search", [sample])
        write_manifest(tmp_path, {"search": [sample.get("id")]})
        return validate_samples(str(tmp_path))

    def test_duplicate_id(self, tmp_path):
        build_pool(tmp_path)
        write_category(tmp_path, "search", [
            make_sample("s1", "search"), make_sample("s1", "search")])
        write_manifest(tmp_path, {"search": ["s1"]})
        report = validate_samples(str(tmp_path))
        assert any("id 重复" in i.message for i in report.issues)

    def test_category_mismatch_with_dir(self, tmp_path):
        s = make_sample("s1", "search")
        s["category"] = "code"  # 与目录 search 不一致
        report = self._report_with(tmp_path, s)
        assert any("与目录" in i.message for i in report.issues)

    def test_missing_task(self, tmp_path):
        s = make_sample("s1", "search")
        s.pop("task")
        report = self._report_with(tmp_path, s)
        assert any("task" in i.message for i in report.issues)

    def test_invalid_expected_output_type(self, tmp_path):
        s = make_sample("s1", "search")
        s["expected_output"] = {"type": "regex", "value": "x"}
        report = self._report_with(tmp_path, s)
        assert any("expected_output.type" in i.message for i in report.issues)

    def test_validator_missing_expression(self, tmp_path):
        s = make_sample("s1", "search")
        s["expected_output"] = {"type": "validator"}
        report = self._report_with(tmp_path, s)
        assert any("validator" in i.message for i in report.issues)

    def test_invalid_difficulty(self, tmp_path):
        s = make_sample("s1", "search")
        s["metadata"]["difficulty"] = "HARD"
        report = self._report_with(tmp_path, s)
        assert any("difficulty" in i.message for i in report.issues)

    def test_invalid_source(self, tmp_path):
        s = make_sample("s1", "search")
        s["metadata"]["source"] = "crawler"
        report = self._report_with(tmp_path, s)
        assert any("source" in i.message for i in report.issues)

    def test_input_hash_mismatch(self, tmp_path):
        s = make_sample("s1", "search")
        s["metadata"]["input_hash"] = "0000000000000000"
        report = self._report_with(tmp_path, s)
        assert any("input_hash 不匹配" in i.message for i in report.issues)

    def test_input_hash_malformed(self, tmp_path):
        s = make_sample("s1", "search")
        s["metadata"]["input_hash"] = "abc"
        report = self._report_with(tmp_path, s)
        assert any("input_hash" in i.message for i in report.issues)

    def test_missing_metadata_input(self, tmp_path):
        s = make_sample("s1", "search")
        s["metadata"].pop("input")
        report = self._report_with(tmp_path, s)
        assert any("metadata.input" in i.message for i in report.issues)


# ════════════════════════════════════════════════════════════
#  3. input_hash 跨类别去重
# ════════════════════════════════════════════════════════════

class TestDedup:
    def test_duplicate_input_hash_same_category(self, tmp_path):
        write_category(tmp_path, "search", [
            make_sample("s1", "search", task="查询云枢的定义",
                        input_meta={"query": "云枢"}),
            make_sample("s2", "search", task="查询云枢的定义",
                        input_meta={"query": "云枢"}),
        ])
        write_manifest(tmp_path, {"search": ["s1", "s2"]})
        report = validate_samples(str(tmp_path))
        assert any("input_hash 重复" in i.message for i in report.issues)

    def test_same_task_different_category_ok(self, tmp_path):
        """input_hash 含 category：跨类别同任务不算重复（不同评估域）"""
        write_category(tmp_path, "search", [make_sample("s1", "search")])
        write_category(tmp_path, "code", [
            make_sample("c1", "code", task="查询云枢的定义",
                        input_meta={"query": "云枢"}),
        ])
        write_manifest(tmp_path, {"search": ["s1"], "code": ["c1"]})
        report = validate_samples(str(tmp_path))
        assert report.ok, report.summary()

    def test_same_task_different_input_allowed(self, tmp_path):
        write_category(tmp_path, "search", [
            make_sample("s1", "search", task="查询云枢", input_meta={"query": "云枢"}),
            make_sample("s2", "search", task="查询云枢", input_meta={"query": "云枢AI"}),
        ])
        write_manifest(tmp_path, {"search": ["s1", "s2"]})
        report = validate_samples(str(tmp_path))
        assert report.ok, report.summary()


# ════════════════════════════════════════════════════════════
#  4. manifest 一致性
# ════════════════════════════════════════════════════════════

class TestManifest:
    def test_unregistered_pool_sample_reported(self, tmp_path):
        write_category(tmp_path, "search", [make_sample("s1", "search")])
        write_manifest(tmp_path, {"search": []})  # 池中有 s1 但未登记
        report = validate_samples(str(tmp_path))
        assert any("未登记" in m for m in report.manifest_issues)

    def test_registered_id_missing_in_pool(self, tmp_path):
        write_category(tmp_path, "search", [make_sample("s1", "search")])
        write_manifest(tmp_path, {"search": ["s1", "ghost"]})
        report = validate_samples(str(tmp_path))
        assert any("ghost" in m and "不在池中" in m for m in report.manifest_issues)

    def test_compute_input_hash_deterministic(self):
        h1 = compute_input_hash("search", "查询云枢", {"query": "云枢"})
        h2 = compute_input_hash("search", "查询云枢", {"query": "云枢"})
        assert h1 == h2
        assert len(h1) == 16
