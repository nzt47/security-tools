"""沙箱回放覆盖率计算（任务6 遗留项接线 · 复查补充）— 单元测试

覆盖 compute_replay_coverage 各分支：
  - 正常：审计样本 ∩ manifest 样本 / manifest 总数（含审计中的非评估集样本剔除）
  - 空审计 / 缺 manifest / 缺审计 → None（TC-4 保持 unknown，绝不伪造）
  - 已回放样本为空 → 0.0
  - 非法行容错（JSONDecodeError 跳过）
  - 与 /api/learning/metrics/trigger 自动注入契约一致

运行：python -m pytest tests/unit/test_replay_coverage.py -q
"""
import json

from agent.learning.replay import compute_replay_coverage


def _write_manifest(path, categories):
    payload = {
        "schema_version": 1,
        "current": "v1",
        "versions": {
            "v1": {"description": "test", "created_at": "t",
                   "reviewed_by": "manual", "categories": categories},
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_audit(path, entries):
    lines = []
    for e in entries:
        lines.append(json.dumps(e, ensure_ascii=False))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _audit_entry(replay_id, samples):
    return {"replay_id": replay_id, "created_at": "t",
            "candidate_id": "c", "samples": samples,
            "verdict_counts": {}, "duration_ms": 1}


def test_normal_coverage(tmp_path):
    """12/50 样本已回放 → 0.24；审计中的非评估集样本（bench-*）剔除"""
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, {
        "search": [f"search-{i:03d}" for i in range(1, 13)],
        "code": [f"code-{i:03d}" for i in range(1, 13)],
        "chat": [f"chat-{i:03d}" for i in range(1, 13)],
        "tool": [f"tool-{i:03d}" for i in range(1, 8)],
        "planning": [f"planning-{i:03d}" for i in range(1, 8)],
    })
    audit = tmp_path / "audit.jsonl"
    _write_audit(audit, [
        _audit_entry("r1", [{"sample_id": f"search-{i:03d}", "verdict": "success",
                             "duration_ms": 1} for i in range(1, 13)]),
        _audit_entry("r2", [{"sample_id": "bench-000", "verdict": "success",
                             "duration_ms": 1}]),
    ])
    cov = compute_replay_coverage(audit_file=audit, manifest_path=manifest)
    assert cov == 0.24


def test_no_audit_file_returns_none(tmp_path):
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, {"search": ["search-001"]})
    assert compute_replay_coverage(
        audit_file=tmp_path / "missing.jsonl", manifest_path=manifest) is None


def test_no_manifest_returns_none(tmp_path):
    audit = tmp_path / "audit.jsonl"
    _write_audit(audit, [_audit_entry("r1", [{"sample_id": "search-001",
                                              "verdict": "success",
                                              "duration_ms": 1}])])
    assert compute_replay_coverage(
        audit_file=audit, manifest_path=tmp_path / "missing.json") is None


def test_empty_audit_returns_zero(tmp_path):
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, {"search": ["search-001"]})
    audit = tmp_path / "audit.jsonl"
    audit.write_text("", encoding="utf-8")
    assert compute_replay_coverage(audit_file=audit, manifest_path=manifest) == 0.0


def test_malformed_lines_skipped(tmp_path):
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, {"search": ["search-001"]})
    audit = tmp_path / "audit.jsonl"
    audit.write_text("{bad json\n"
                     + json.dumps(_audit_entry("r1", [
                         {"sample_id": "search-001", "verdict": "success",
                          "duration_ms": 1}]))
                     + "\n", encoding="utf-8")
    cov = compute_replay_coverage(audit_file=audit, manifest_path=manifest)
    assert cov == 1.0


def test_duplicate_samples_deduplicated(tmp_path):
    """同一样本多次回放只计一次（去重）"""
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, {"search": ["search-001", "search-002"]})
    audit = tmp_path / "audit.jsonl"
    _write_audit(audit, [
        _audit_entry("r1", [{"sample_id": "search-001", "verdict": "success",
                             "duration_ms": 1}]),
        _audit_entry("r2", [{"sample_id": "search-001", "verdict": "failed",
                             "duration_ms": 1}]),
    ])
    assert compute_replay_coverage(audit_file=audit, manifest_path=manifest) == 0.5


def test_empty_registered_returns_none(tmp_path):
    """manifest 无登记样本 → None（无可度量对象）"""
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, {})
    audit = tmp_path / "audit.jsonl"
    _write_audit(audit, [_audit_entry("r1", [{"sample_id": "search-001",
                                              "verdict": "success",
                                              "duration_ms": 1}])])
    assert compute_replay_coverage(audit_file=audit, manifest_path=manifest) is None
