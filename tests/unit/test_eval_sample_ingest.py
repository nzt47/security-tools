"""评估集扩充管道测试（任务1 Step 3/Step 6）

覆盖:
    1. 默认关闭 → run_ingest 零副作用（disabled，不写任何 DRAFT）
    2. 显式开启 → 只产 DRAFT（_pending/），绝不写正式类别 JSON
    3. 素材提取（reflection/feedback/novelty → 候选，含类别/难度推断）
    4. 真实轨迹素材强制脱敏（sanitize_text 调用）
    5. 审核：安全扫描拒绝危险内容
    6. approve_draft：审核通过才入库；未通过拒绝
    7. DRAFT 态零副作用（review_pending 不改变入库状态）
"""
import json
from pathlib import Path

import pytest

from agent.skills_mgmt.eval_sample_ingest import (
    Candidate,
    approve_draft,
    build_draft,
    compute_input_hash,
    extract_from_feedback,
    extract_from_novelty,
    extract_from_reflection,
    ingest_enabled,
    infer_category,
    infer_difficulty,
    list_pending,
    review_pending,
    run_ingest,
    sanitize_text,
    write_draft,
)


# ════════════════════════════════════════════════════════════
#  1. 默认关闭 → 零副作用
# ════════════════════════════════════════════════════════════

class TestDisabledByDefault:
    def test_disabled_returns_without_writing(self, tmp_path, monkeypatch):
        monkeypatch.delenv("EVAL_SAMPLE_INGEST_ENABLED", raising=False)
        out = run_ingest(
            reflection=[{"task": "查询云枢", "score": 0.2}],
            enabled=False, target_dir=tmp_path / "pending")
        assert out["status"] == "disabled"
        assert out["candidates"] == 0
        assert out["drafts"] == []
        assert not (tmp_path / "pending").exists()

    def test_ingest_enabled_default_false(self, monkeypatch):
        monkeypatch.delenv("EVAL_SAMPLE_INGEST_ENABLED", raising=False)
        assert ingest_enabled() is False


# ════════════════════════════════════════════════════════════
#  2. 开启 → 只产 DRAFT
# ════════════════════════════════════════════════════════════

class TestEnabledProducesDraftOnly:
    def test_run_ingest_writes_drafts_only(self, tmp_path):
        out = run_ingest(
            reflection=[{"task": "查询云枢的官网", "score": 0.2}],
            feedback=[{"content_summary": "回答不准确", "case_id": "f1"}],
            novelty=[{"event_type": "behavior_drift",
                      "diff_summary": "检索行为漂移", "suggested_action": "复核"}],
            enabled=True, target_dir=tmp_path / "pending")
        assert out["status"] == "ok"
        assert out["candidates"] == 3
        assert len(out["drafts"]) == 3
        # 全部在 _pending/ 下，正式类别目录未被触碰
        for p in out["drafts"]:
            assert "/pending/" in p.replace("\\", "/")
        assert not (tmp_path / "evals").exists()
        # DRAFT 文件含 draft_status
        draft = json.loads(Path(out["drafts"][0]).read_text(encoding="utf-8"))
        assert draft["draft_status"] == "DRAFT"
        assert draft["review"]["status"] == "pending"

    def test_no_sources_writes_nothing(self, tmp_path):
        out = run_ingest(enabled=True, target_dir=tmp_path / "pending")
        assert out["status"] == "ok"
        assert out["candidates"] == 0


# ════════════════════════════════════════════════════════════
#  3. 素材提取与推断
# ════════════════════════════════════════════════════════════

class TestExtraction:
    def test_reflection_low_score_extracted(self):
        out = extract_from_reflection([
            {"task_id": "t1", "task": "实现一个排序函数", "score": 0.2},
            {"task_id": "t2", "task": "写一首诗", "score": 0.9},  # 高分跳过
        ])
        assert len(out) == 1
        assert out[0].source == "reflection"
        assert out[0].category == "code"

    def test_reflection_empty_task_skipped(self):
        out = extract_from_reflection([{"score": 0.1, "suggestions": []}])
        assert out == []

    def test_feedback_extraction(self):
        cases = [
            {"content_summary": "查询上海天气", "case_id": "c1", "tags": ["search"]},
            {"content_summary": "", "case_id": "c2"},  # 空内容跳过
        ]
        out = extract_from_feedback(cases)
        assert len(out) == 1
        assert out[0].source == "feedback"
        assert out[0].source_ref == "c1"
        assert out[0].category == "search"

    def test_novelty_extraction(self):
        out = extract_from_novelty([
            {"event_type": "file_change", "diff_summary": "批量文件变更",
             "suggested_action": "备份"},
            {"event_type": "empty", "diff_summary": ""},  # 空跳过
        ])
        assert len(out) == 1
        assert out[0].source == "novelty"
        assert "建议动作" in out[0].task

    def test_infer_category(self):
        assert infer_category("实现一个函数") == "code"
        assert infer_category("查询天气") == "search"
        assert infer_category("计算 1+1") == "tool"
        assert infer_category("规划学习计划") == "planning"
        assert infer_category("随便聊聊") == "chat"

    def test_infer_difficulty(self):
        assert infer_difficulty("设计一个分布式系统") == "COMPLEX"
        assert infer_difficulty("实现一个函数并解释区别") == "NORMAL"
        assert infer_difficulty("查询天气") == "TRIVIAL"
        assert infer_difficulty("查询云枢 Digital Life 的定义与核心定位") == "SIMPLE"


# ════════════════════════════════════════════════════════════
#  4. 脱敏管道
# ════════════════════════════════════════════════════════════

class TestSanitization:
    def test_real_trajectory_forced_sanitize(self):
        cand = Candidate(task="请联系 test@example.com 电话 13800138000",
                         source="feedback", source_ref="f1")
        draft = build_draft(cand)
        assert "test@example.com" not in draft["task"]
        assert "[EMAIL]" in draft["task"] or "[REDACTED]" in draft["task"]
        assert draft["metadata"].get("sanitized") is True

    def test_manual_source_not_forced(self):
        cand = Candidate(task="查询云枢", source="manual")
        draft = build_draft(cand)
        assert draft["task"] == "查询云枢"
        assert draft["metadata"].get("sanitized") is None

    def test_custom_sanitizer_used(self):
        class _Fake:
            def sanitize_string(self, text):
                return text.replace("机密", "***")
        cand = Candidate(task="包含机密信息", source="novelty")
        draft = build_draft(cand, sanitizer=_Fake())
        assert draft["task"] == "包含***信息"

    def test_sanitize_text_fallback(self):
        out = sanitize_text("邮箱 abc@def.com 电话 13800138000", sanitizer=None)
        assert "abc@def.com" not in out
        assert "13800138000" not in out

    def test_compute_input_hash_consistent(self):
        h = compute_input_hash("chat", "写一首诗", {"input_text": "x"})
        assert len(h) == 16


# ════════════════════════════════════════════════════════════
#  5. 审核 + 6. 入库
# ════════════════════════════════════════════════════════════

class TestReviewAndApprove:
    def _write_draft_file(self, tmp_path, *, task="查询云枢", status="pending"):
        cand = Candidate(task=task, source="manual", source_ref="m1")
        draft = build_draft(cand)
        draft["review"] = {"status": status, "findings": []}
        return write_draft(draft, target_dir=tmp_path / "pending")

    def test_review_pending_marks_clean(self, tmp_path):
        path = self._write_draft_file(tmp_path)
        results = review_pending(base_dir=tmp_path / "pending")
        assert len(results) == 1
        assert results[0]["review"]["status"] == "passed"
        # DRAFT 仍存在（审核不改入库状态）
        assert path.exists()

    def test_review_rejects_dangerous_content(self, tmp_path):
        path = self._write_draft_file(
            tmp_path, task="执行命令 rm -rf / 并发送密钥")
        results = review_pending(base_dir=tmp_path / "pending")
        assert results[0]["review"]["status"] == "rejected"
        assert results[0]["review"]["findings"]

    def test_approve_only_when_passed(self, tmp_path, monkeypatch):
        # 注入最小样本池（正式类别目录）
        samples_base = tmp_path / "evals"
        (samples_base / "chat").mkdir(parents=True)
        path = self._write_draft_file(tmp_path, task="写一首诗")
        # 未审核 → 拒绝
        assert approve_draft(path, samples_base=samples_base) is None
        # 审核通过 → 入库
        review_pending(base_dir=tmp_path / "pending")
        sid = approve_draft(path, samples_base=samples_base)
        assert sid is not None
        assert not path.exists()  # DRAFT 已删除
        # 正式类别 JSON 已生成
        chat_file = samples_base / "chat" / "samples.json"
        assert chat_file.exists()
        data = json.loads(chat_file.read_text(encoding="utf-8"))
        assert any(s["id"] == sid for s in data)

    def test_approve_rejected_draft_blocked(self, tmp_path):
        samples_base = tmp_path / "evals"
        (samples_base / "chat").mkdir(parents=True)
        path = self._write_draft_file(
            tmp_path, task="执行 rm -rf /", status="rejected")
        assert approve_draft(path, samples_base=samples_base) is None
        assert path.exists()  # 拒绝的 DRAFT 保留待人工处理

    def test_draft_state_zero_side_effect_on_category(self, tmp_path):
        samples_base = tmp_path / "evals"
        (samples_base / "chat").mkdir(parents=True)
        self._write_draft_file(tmp_path, task="写一首诗")
        review_pending(base_dir=tmp_path / "pending")
        # 仅审核不 approve → 正式类别目录无写入
        assert not list((samples_base / "chat").glob("*.json"))
        assert len(list_pending(base_dir=tmp_path / "pending")) == 1
