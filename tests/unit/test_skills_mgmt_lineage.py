"""进化谱系与档案库单元测试（任务 EVO-T1）

覆盖验收条件:
    1. EvolutionArchive 全部公开方法有单元测试，覆盖率 ≥ 80%；
    2. 谱系回溯正确：5 代链 A→B→C→D→E 按序返回；
    3. 分层保留生效：超阈值记录压缩归档，摘要可查、不丢 record_id/decision；
    4. 并发追加不丢记录（并发写 100 条全量可查）；
    5. 损坏/不存在的 JSONL 不抛致命异常，能重建或跳过；
    6. 既有测试（test_skills_mgmt.py）全部通过（向后兼容）；
    7. offline_evolver.py 未修改（本任务边界）。
"""
import json
import threading
from pathlib import Path

import pytest

from agent.skills_mgmt.lineage import (
    EvolutionArchive,
    EvolutionRecord,
    get_default_archive,
    print_lineage,
)


# ════════════════════════════════════════════════════════════
#  Fixtures 与构造辅助
# ════════════════════════════════════════════════════════════

@pytest.fixture
def archive(tmp_path):
    """默认临时档案库（active_generations=10）"""
    return EvolutionArchive(
        active_path=str(tmp_path / "evolution_archive.jsonl"),
        archive_path=str(tmp_path / "evolution_archive_old.jsonl"),
        active_generations=10,
    )


def make_record(obj="skill-a", version="1.0.0", *, parent_id=None,
                parent_version="", decision="committed", score=None,
                record_id=None, strategy="fine_tune",
                created_at="2026-08-12T10:00:00"):
    """构造记录（参数可覆盖；record_id 为空时自动生成）"""
    eval_result = None
    if score is not None:
        eval_result = {
            "score": score,
            "dimensions": {"success_rate": 0.9},
            "sample_count": 10,
            "evaluator_version": "1.0",
        }
    return EvolutionRecord(
        object_id=obj,
        parent_record_id=parent_id,
        parent_version=parent_version,
        new_version=version,
        strategy=strategy,
        decision=decision,
        eval_result=eval_result,
        record_id=record_id or "",
        created_at=created_at,
    )


def build_chain(archive, obj, n=5, *, base_score=0.5, step=0.1):
    """构造 n 代链 A→B→...，返回按根→最新排序的 record_id 列表"""
    ids = [f"evt-test-{obj}-{i}" for i in range(n)]
    prev_id, prev_ver = None, ""
    for i in range(n):
        ver = f"{i + 1}.0.0"
        archive.append(make_record(
            obj, ver, parent_id=prev_id, parent_version=prev_ver,
            score=base_score + i * step,
            record_id=ids[i],
            created_at=f"2026-08-12T10:00:{i:02d}",
        ))
        prev_id, prev_ver = ids[i], ver
    return ids


def active_lines(tmp_path):
    p = tmp_path / "evolution_archive.jsonl"
    return [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] \
        if p.exists() else []


def archive_lines(tmp_path):
    p = tmp_path / "evolution_archive_old.jsonl"
    return [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] \
        if p.exists() else []


# ════════════════════════════════════════════════════════════
#  数据模型
# ════════════════════════════════════════════════════════════

class TestEvolutionRecord:
    def test_record_id_auto_generated(self):
        rec = EvolutionRecord(object_id="x", new_version="1.0.0")
        assert rec.record_id.startswith("evt-")

    def test_created_at_auto_generated(self):
        rec = EvolutionRecord(object_id="x")
        assert rec.created_at  # 非空 ISO 时间戳

    def test_explicit_id_and_created_at_preserved(self):
        rec = EvolutionRecord(
            object_id="x", record_id="evt-fixed-1",
            created_at="2026-08-12T09:00:00",
        )
        assert rec.record_id == "evt-fixed-1"
        assert rec.created_at == "2026-08-12T09:00:00"

    def test_to_dict_from_dict_roundtrip(self):
        rec = EvolutionRecord(
            object_id="r", record_id="evt-r-1", parent_record_id="evt-r-0",
            parent_version="1.0.0", new_version="1.1.0", strategy="mutate",
            change_summary="变更说明", eval_result={"score": 0.8},
            decision="pending_review", decision_reason="需人工复核",
            trigger="scheduler", actor="user",
            cost={"tokens": 100, "duration_ms": 5.0},
            created_at="2026-08-12T10:00:00",
        )
        restored = EvolutionRecord.from_dict(rec.to_dict())
        assert restored == rec

    def test_from_dict_tolerates_unknown_fields(self):
        rec = EvolutionRecord.from_dict(
            {"object_id": "x", "record_id": "evt-x-1", "future_field": 42}
        )
        assert rec.object_id == "x"
        assert rec.record_id == "evt-x-1"

    def test_from_dict_maps_archive_version_field(self):
        summary = {
            "record_id": "evt-s-1", "object_id": "x", "version": "2.0.0",
            "decision": "committed", "score": 0.9,
            "created_at": "2026-08-12T10:00:00", "archived": True,
        }
        rec = EvolutionRecord.from_dict(summary)
        assert rec.new_version == "2.0.0"
        assert rec.archived is True

    def test_from_bump_builds_record(self):
        ctx = {
            "skill_id": "s1", "old_version": "1.0.0",
            "new_version": "1.0.1", "changelog": "修复 bug",
        }
        rec = EvolutionRecord.from_bump(
            ctx, parent_record_id="evt-p-1",
            strategy="llm_edit", trigger="scheduler",
            cost={"tokens": 50, "duration_ms": 3.0},
        )
        assert rec.object_id == "s1"
        assert rec.parent_version == "1.0.0"
        assert rec.new_version == "1.0.1"
        assert rec.change_summary == "修复 bug"
        assert rec.parent_record_id == "evt-p-1"
        assert rec.strategy == "llm_edit"
        assert rec.trigger == "scheduler"
        assert rec.cost == {"tokens": 50, "duration_ms": 3.0}

    def test_to_summary_keeps_critical_fields(self):
        rec = make_record("s", "2.0.0", score=0.88, record_id="evt-sum-1")
        summary = rec.to_summary()
        assert summary["record_id"] == "evt-sum-1"
        assert summary["decision"] == "committed"
        assert summary["score"] == 0.88
        assert summary["version"] == "2.0.0"
        assert summary["archived"] is True

    def test_get_score(self):
        rec = make_record("s", score=0.75)
        assert rec.get_score() == 0.75
        assert make_record("s").get_score() is None


# ════════════════════════════════════════════════════════════
#  档案库基础：追加 / 查询
# ════════════════════════════════════════════════════════════

class TestEvolutionArchiveBasics:
    def test_append_returns_id_and_get(self, archive):
        rid = archive.append(make_record("obj-1", "1.0.0", record_id="evt-a-1"))
        assert rid == "evt-a-1"
        rec = archive.get("evt-a-1")
        assert rec is not None
        assert rec.object_id == "obj-1"
        assert rec.new_version == "1.0.0"

    def test_get_missing_returns_none(self, archive):
        assert archive.get("evt-nope") is None

    def test_append_empty_object_id_raises(self, archive):
        with pytest.raises(ValueError):
            archive.append(EvolutionRecord(object_id="", new_version="1.0.0"))

    def test_append_invalid_object_type_raises(self, archive):
        with pytest.raises(ValueError):
            archive.append(EvolutionRecord(
                object_id="x", object_type="not_a_type", new_version="1.0.0"))

    def test_append_invalid_decision_raises(self, archive):
        with pytest.raises(ValueError):
            archive.append(EvolutionRecord(
                object_id="x", decision="maybe", new_version="1.0.0"))

    def test_list_by_object(self, archive):
        archive.append(make_record("obj-1", "1.0.0", record_id="evt-l-1"))
        archive.append(make_record("obj-2", "1.0.0", record_id="evt-l-2"))
        archive.append(make_record("obj-1", "1.1.0", record_id="evt-l-3"))
        recs = archive.list_by_object("obj-1")
        assert [r.record_id for r in recs] == ["evt-l-1", "evt-l-3"]

    def test_query_equality(self, archive):
        archive.append(make_record("q", "1.0.0", decision="committed", record_id="evt-q-1"))
        archive.append(make_record("q", "1.1.0", decision="rejected", record_id="evt-q-2"))
        archive.append(make_record("o", "1.0.0", decision="committed", record_id="evt-q-3"))
        committed = archive.query({"decision": "committed"})
        assert len(committed) == 2

    def test_query_list_membership(self, archive):
        archive.append(make_record("q", decision="committed", record_id="evt-q-1"))
        archive.append(make_record("q", decision="rejected", record_id="evt-q-2"))
        multi = archive.query({"decision": ["committed", "rejected"]})
        assert len(multi) == 2

    def test_query_combined_filter(self, archive):
        archive.append(make_record("q", decision="committed", record_id="evt-q-1"))
        archive.append(make_record("q", decision="rejected", record_id="evt-q-2"))
        hit = archive.query({"object_id": "q", "decision": "committed"})
        assert len(hit) == 1

    def test_query_limit(self, archive):
        for i in range(5):
            archive.append(make_record("q", record_id=f"evt-q-{i}"))
        assert len(archive.query({"object_id": "q"}, limit=2)) == 2

    def test_query_empty_filter_returns_all(self, archive):
        archive.append(make_record("q", record_id="evt-q-1"))
        archive.append(make_record("o", record_id="evt-q-2"))
        assert len(archive.query()) == 2

    def test_count(self, archive):
        archive.append(make_record("obj-1", record_id="evt-c-1"))
        archive.append(make_record("obj-1", record_id="evt-c-2"))
        archive.append(make_record("obj-2", record_id="evt-c-3"))
        assert archive.count() == 3
        assert archive.count("obj-1") == 2
        assert archive.count("obj-2") == 1

    def test_persistence_across_instances(self, tmp_path):
        p1, p2 = str(tmp_path / "a.jsonl"), str(tmp_path / "old.jsonl")
        a1 = EvolutionArchive(active_path=p1, archive_path=p2)
        a1.append(make_record("skill-p", "1.0.0", record_id="evt-p-1"))
        a2 = EvolutionArchive(active_path=p1, archive_path=p2)
        rec = a2.get("evt-p-1")
        assert rec is not None
        assert rec.new_version == "1.0.0"

    def test_missing_files_create_empty(self, tmp_path):
        a = EvolutionArchive(
            active_path=str(tmp_path / "no.jsonl"),
            archive_path=str(tmp_path / "no_old.jsonl"),
        )
        assert a.count() == 0
        a.append(make_record("x", "1.0.0", record_id="evt-m-1"))
        assert a.count() == 1
        assert (tmp_path / "no.jsonl").exists()


# ════════════════════════════════════════════════════════════
#  谱系回溯（验收 2）
# ════════════════════════════════════════════════════════════

class TestLineageTracing:
    def test_get_lineage_five_generations(self, archive):
        ids = build_chain(archive, "chain-obj", n=5)
        chain = archive.get_lineage("chain-obj")
        assert [r.record_id for r in chain] == ids  # A→B→C→D→E 按序
        assert [r.new_version for r in chain] == [
            "1.0.0", "2.0.0", "3.0.0", "4.0.0", "5.0.0",
        ]
        # 首代无父，末代是最后追加
        assert chain[0].parent_record_id is None

    def test_get_lineage_unknown_object_empty(self, archive):
        assert archive.get_lineage("ghost") == []

    def test_get_lineage_single_record(self, archive):
        archive.append(make_record("solo", "1.0.0", record_id="evt-solo-1"))
        chain = archive.get_lineage("solo")
        assert len(chain) == 1
        assert chain[0].record_id == "evt-solo-1"

    def test_list_by_object_orders_by_created_at(self, archive):
        archive.append(make_record(
            "ord-obj", "1.0.0", record_id="evt-o-1",
            created_at="2026-08-12T09:00:00"))
        archive.append(make_record(
            "ord-obj", "2.0.0", record_id="evt-o-2",
            created_at="2026-08-12T08:00:00"))
        recs = archive.list_by_object("ord-obj")
        assert [r.record_id for r in recs] == ["evt-o-2", "evt-o-1"]


# ════════════════════════════════════════════════════════════
#  分层保留（验收 3）
# ════════════════════════════════════════════════════════════

class TestArchiving:
    @pytest.fixture
    def small_archive(self, tmp_path):
        return EvolutionArchive(
            active_path=str(tmp_path / "evolution_archive.jsonl"),
            archive_path=str(tmp_path / "evolution_archive_old.jsonl"),
            active_generations=3,
        )

    def test_archive_threshold_triggers(self, small_archive, tmp_path):
        ids = build_chain(small_archive, "arch-obj", n=5)
        assert len(active_lines(tmp_path)) == 3    # 活跃只留最近 3 代
        assert len(archive_lines(tmp_path)) == 2   # 最老 2 代压缩归档

    def test_archived_summary_still_queryable(self, small_archive):
        ids = build_chain(small_archive, "arch-obj", n=5)
        first = small_archive.get(ids[0])
        assert first is not None
        assert first.archived is True
        assert first.record_id == ids[0]           # record_id 不丢失
        assert first.decision == "committed"       # decision 不丢失
        assert first.new_version == "1.0.0"        # version 不丢失

    def test_active_records_kept_full(self, small_archive):
        ids = build_chain(small_archive, "arch-obj", n=5)
        last = small_archive.get(ids[4])
        assert last is not None
        assert last.archived is False
        assert last.get_score() == pytest.approx(0.9)
        # 完整记录字段仍保留（区别于摘要）
        assert last.eval_result is not None

    def test_archived_summary_file_fields(self, small_archive, tmp_path):
        ids = build_chain(small_archive, "arch-obj", n=5)
        lines = archive_lines(tmp_path)
        first_summary = json.loads(lines[0])
        assert first_summary["record_id"] == ids[0]
        assert first_summary["decision"] == "committed"
        assert first_summary["score"] == pytest.approx(0.5)
        assert first_summary["archived"] is True

    def test_lineage_survives_archiving(self, tmp_path):
        a = EvolutionArchive(
            active_path=str(tmp_path / "evolution_archive.jsonl"),
            archive_path=str(tmp_path / "evolution_archive_old.jsonl"),
            active_generations=2,
        )
        ids = build_chain(a, "chain-obj", n=5)
        chain = a.get_lineage("chain-obj")
        assert [r.record_id for r in chain] == ids          # 5 代完整且顺序正确
        assert [r.archived for r in chain] == [True, True, True, False, False]
        # 归档代仍能回溯出分数（摘要 score 映射回 eval_result）
        assert chain[0].get_score() == pytest.approx(0.5)

    def test_archive_keeps_other_objects(self, tmp_path):
        a = EvolutionArchive(
            active_path=str(tmp_path / "evolution_archive.jsonl"),
            archive_path=str(tmp_path / "evolution_archive_old.jsonl"),
            active_generations=2,
        )
        build_chain(a, "obj-a", n=3)
        build_chain(a, "obj-b", n=2)
        assert a.count("obj-a") == 3
        assert a.count("obj-b") == 2
        chain_a = a.get_lineage("obj-a")
        assert len(chain_a) == 3
        assert chain_a[0].archived is True

    def test_no_archiving_below_threshold(self, archive):
        build_chain(archive, "small-obj", n=3)  # 阈值 10
        assert archive.count("small-obj") == 3


# ════════════════════════════════════════════════════════════
#  并发追加（验收 4）
# ════════════════════════════════════════════════════════════

class TestConcurrency:
    def test_concurrent_append_no_loss(self, tmp_path):
        a = EvolutionArchive(
            active_path=str(tmp_path / "evolution_archive.jsonl"),
            archive_path=str(tmp_path / "evolution_archive_old.jsonl"),
            active_generations=1000,  # 大阈值，聚焦并发不丢记录
        )
        n_total, n_threads = 100, 8
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker(base):
            try:
                barrier.wait(timeout=10)
                for i in range(base, n_total, n_threads):
                    a.append(make_record(f"obj-{i % 5}", "1.0.0"))
            except Exception as e:  # noqa: BLE001 收集线程异常供主线程断言
                errors.append(e)

        threads = [
            threading.Thread(target=worker, args=(t,))
            for t in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"并发追加线程异常: {errors}"
        assert a.count() == n_total                    # 全量可查
        all_recs = a.query()
        assert len(all_recs) == n_total
        assert len({r.record_id for r in all_recs}) == n_total  # 无重复
        assert len(active_lines(tmp_path)) == n_total  # 落盘完整


# ════════════════════════════════════════════════════════════
#  损坏/缺失容错（验收 5）
# ════════════════════════════════════════════════════════════

class TestCorruptionTolerance:
    def test_corrupted_lines_skipped(self, tmp_path):
        active = tmp_path / "evolution_archive.jsonl"
        good = json.dumps(
            make_record("obj-x", "1.0.0", record_id="evt-good-1").to_dict(),
            ensure_ascii=False)
        active.write_text(
            good + "\n{not valid json\n" + good + "\n" + '{"object_id": "broken\n',
            encoding="utf-8")
        a = EvolutionArchive(
            active_path=str(active),
            archive_path=str(tmp_path / "old.jsonl"),
        )
        assert a.count() == 2          # 2 条有效，2 条坏行被跳过
        assert a.get("evt-good-1") is not None

    def test_whole_file_corrupted_rebuilds(self, tmp_path):
        active = tmp_path / "evolution_archive.jsonl"
        active.write_bytes(b"\xff\xfe\x00\x01garbage\x80\x81")  # 非法 UTF-8
        a = EvolutionArchive(
            active_path=str(active),
            archive_path=str(tmp_path / "old.jsonl"),
        )
        assert a.count() == 0          # 重建为空，不抛致命异常
        a.append(make_record("rebuild-obj", "1.0.0", record_id="evt-rb-1"))
        assert a.count() == 1          # 重建后可正常写入
        assert (tmp_path / "evolution_archive.jsonl.corrupted").exists()

    def test_corrupted_archive_file_skipped(self, tmp_path):
        old = tmp_path / "evolution_archive_old.jsonl"
        old.write_text("{bad json\n", encoding="utf-8")
        a = EvolutionArchive(
            active_path=str(tmp_path / "evolution_archive.jsonl"),
            archive_path=str(old),
        )
        assert a.count() == 0          # 归档文件坏行跳过，不致命

    def test_append_after_reload_from_clean_files(self, tmp_path):
        p1, p2 = str(tmp_path / "a.jsonl"), str(tmp_path / "old.jsonl")
        a1 = EvolutionArchive(active_path=p1, archive_path=p2)
        a1.append(make_record("r", "1.0.0", record_id="evt-r-1"))
        a2 = EvolutionArchive(active_path=p1, archive_path=p2)
        a2.append(make_record("r", "2.0.0", record_id="evt-r-2"))
        assert a2.count() == 2


# ════════════════════════════════════════════════════════════
#  审计输出与默认档案库
# ════════════════════════════════════════════════════════════

class TestPrintLineage:
    def test_print_lineage_formats_chain(self, archive):
        build_chain(archive, "print-obj", n=3, base_score=0.5)
        out = print_lineage("print-obj", archive=archive)
        assert "进化谱系: print-obj（共 3 代）" in out
        assert "evt-test-print-obj-0" in out
        assert "score=0.5" in out
        assert "Δ+0.10" in out  # 含每代评分变化
        assert "decision=committed" in out

    def test_print_lineage_no_records(self, archive):
        out = print_lineage("ghost", archive=archive)
        assert "（无记录）" in out

    def test_get_default_archive_respects_env(self, monkeypatch, tmp_path):
        import agent.skills_mgmt.lineage as lineage_mod
        lineage_mod._default_archive = None
        monkeypatch.setenv("EVOLUTION_ARCHIVE_PATH",
                           str(tmp_path / "evo.jsonl"))
        monkeypatch.setenv("EVOLUTION_ARCHIVE_OLD_PATH",
                           str(tmp_path / "evo_old.jsonl"))
        a1 = get_default_archive()
        a2 = get_default_archive()
        assert a1 is a2  # 进程内单例
        a1.append(make_record("env-obj", "1.0.0", record_id="evt-env-1"))
        assert a2.get("evt-env-1") is not None
        lineage_mod._default_archive = None  # 复位，避免影响其他测试

    def test_env_active_generations_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("EVOLUTION_ARCHIVE_ACTIVE_GENERATIONS", "2")
        a = EvolutionArchive(
            active_path=str(tmp_path / "a.jsonl"),
            archive_path=str(tmp_path / "old.jsonl"),
        )
        assert a._active_generations == 2
        monkeypatch.setenv("EVOLUTION_ARCHIVE_ACTIVE_GENERATIONS", "abc")
        a2 = EvolutionArchive(
            active_path=str(tmp_path / "a.jsonl"),
            archive_path=str(tmp_path / "old.jsonl"),
        )
        assert a2._active_generations == 10  # 非法值回退默认


# ════════════════════════════════════════════════════════════
#  SkillEnhancer 谱系钩子（交付物 2：可选钩子，向后兼容）
# ════════════════════════════════════════════════════════════

@pytest.fixture
def enhancer(tmp_path):
    from agent.skills_mgmt.store import SkillStore
    from agent.skills_mgmt.enhancer import SkillEnhancer
    from agent.skills_mgmt.models import Skill

    store = SkillStore(path=str(tmp_path / "skills.json"))
    store.upsert(Skill(
        id="hook-skill", name="Hook Skill",
        content="# test", content_type="python",
    ))
    return SkillEnhancer(store)


class TestEnhancerLineageHook:
    def test_auto_record_by_default(self, enhancer):
        # 未注册 hook → 内置自动记录到默认档案库（conftest 已隔离为 tmp），
        # bump_version 返回类型/行为不变
        bump = enhancer.bump_version("hook-skill", "patch", changelog="v1")
        assert bump.new_version == "0.1.1"
        assert bump.old_version == "0.1.0"
        archive = enhancer._get_lineage_archive()
        # 默认单例被多个测试共享（skill_id 复用），用 query 精确断言本次记录
        hits = archive.query({"object_id": "hook-skill",
                              "change_summary": "v1"})
        assert len(hits) == 1
        assert hits[0].new_version == "0.1.1"
        assert hits[0].decision == "committed"

    def test_disabled_via_env_skips_recording(self, enhancer, monkeypatch):
        # EVOLUTION_ARCHIVE_AUTO_RECORD=0 → 版本升级成功但谱系不落库（总开关）
        monkeypatch.setenv("EVOLUTION_ARCHIVE_AUTO_RECORD", "0")
        archive = enhancer._get_lineage_archive()
        bump = enhancer.bump_version("hook-skill", "patch", changelog="muted")
        assert bump.new_version == "0.1.1"  # 版本升级不受影响
        hits = archive.query({"object_id": "hook-skill",
                              "change_summary": "muted"})
        assert hits == []  # 谱系记录被跳过

    def test_auto_record_when_enabled_creates_lineage_entry(
            self, tmp_path, monkeypatch):
        # EVOLUTION_ARCHIVE_AUTO_RECORD=1（显式开启）→ bump_version 生成
        # 完整且字段正确的谱系条目（与默认开启/关闭路径构成三态覆盖）
        monkeypatch.setenv("EVOLUTION_ARCHIVE_AUTO_RECORD", "1")
        from agent.skills_mgmt.store import SkillStore
        from agent.skills_mgmt.enhancer import SkillEnhancer
        from agent.skills_mgmt.models import Skill

        store = SkillStore(path=str(tmp_path / "skills.json"))
        store.upsert(Skill(
            id="full-skill", name="Full",
            content="# c", content_type="python"))
        archive = EvolutionArchive(
            active_path=str(tmp_path / "e.jsonl"),
            archive_path=str(tmp_path / "eo.jsonl"),
        )
        enhancer = SkillEnhancer(store, lineage_archive=archive)
        bump = enhancer.bump_version(
            "full-skill", "minor", changelog="开启记录下的完整链路")
        chain = archive.get_lineage("full-skill")
        assert len(chain) == 1  # 仅本次一条
        rec = chain[0]
        assert rec.object_id == "full-skill"
        assert rec.object_type == "skill"
        assert rec.parent_record_id is None        # 首代无父
        assert rec.parent_version == "0.1.0"        # 升级前版本
        assert rec.new_version == bump.new_version == "0.2.0"
        assert rec.change_summary == "开启记录下的完整链路"
        assert rec.decision == "committed"          # 版本升级默认提交决策

    def test_auto_records_to_injected_archive(self, tmp_path):
        from agent.skills_mgmt.store import SkillStore
        from agent.skills_mgmt.enhancer import SkillEnhancer
        from agent.skills_mgmt.models import Skill

        store = SkillStore(path=str(tmp_path / "skills.json"))
        store.upsert(Skill(
            id="inj-skill", name="Inj", content="# c", content_type="python"))
        archive = EvolutionArchive(
            active_path=str(tmp_path / "e.jsonl"),
            archive_path=str(tmp_path / "eo.jsonl"),
        )
        enhancer = SkillEnhancer(store, lineage_archive=archive)
        bump = enhancer.bump_version("inj-skill", "minor", changelog="auto")
        chain = archive.get_lineage("inj-skill")
        assert len(chain) == 1
        assert chain[0].new_version == bump.new_version == "0.2.0"
        assert chain[0].change_summary == "auto"
        assert chain[0].object_type == "skill"

    def test_auto_record_failure_does_not_break_bump(self, tmp_path, monkeypatch):
        from agent.skills_mgmt.store import SkillStore
        from agent.skills_mgmt.enhancer import SkillEnhancer
        from agent.skills_mgmt.models import Skill

        store = SkillStore(path=str(tmp_path / "skills.json"))
        store.upsert(Skill(
            id="boom-skill", name="Boom", content="# c", content_type="python"))
        archive = EvolutionArchive(
            active_path=str(tmp_path / "e.jsonl"),
            archive_path=str(tmp_path / "eo.jsonl"),
        )
        monkeypatch.setattr(
            archive, "append",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("写盘失败")))

        enhancer = SkillEnhancer(store, lineage_archive=archive)
        bump = enhancer.bump_version("boom-skill", "patch")
        assert bump.new_version == "0.1.1"  # 谱系写失败不阻塞版本升级

    def test_hook_overrides_auto_record(self, tmp_path):
        from agent.skills_mgmt.store import SkillStore
        from agent.skills_mgmt.enhancer import SkillEnhancer
        from agent.skills_mgmt.models import Skill

        store = SkillStore(path=str(tmp_path / "skills.json"))
        store.upsert(Skill(
            id="ov-skill", name="Ov", content="# c", content_type="python"))
        archive = EvolutionArchive(
            active_path=str(tmp_path / "e.jsonl"),
            archive_path=str(tmp_path / "eo.jsonl"),
        )
        enhancer = SkillEnhancer(store, lineage_archive=archive)
        calls = []
        enhancer.set_lineage_hook(lambda ctx: calls.append(ctx))
        enhancer.bump_version("ov-skill", "patch")
        assert len(calls) == 1              # 自定义 hook 生效
        assert archive.count() == 0         # 内置自动记录被 hook 覆盖

    def test_hook_called_with_context(self, enhancer):
        calls = []
        enhancer.set_lineage_hook(lambda ctx: calls.append(ctx))
        bump = enhancer.bump_version("hook-skill", "patch", changelog="v2")
        assert len(calls) == 1
        ctx = calls[0]
        assert ctx["skill_id"] == "hook-skill"
        assert ctx["old_version"] == "0.1.0"
        assert ctx["new_version"] == "0.1.1"
        assert ctx["changelog"] == "v2"
        assert bump.new_version == ctx["new_version"]

    def test_hook_failure_does_not_break_bump(self, enhancer):
        def boom(ctx):
            raise RuntimeError("hook 失败")

        enhancer.set_lineage_hook(boom)
        bump = enhancer.bump_version("hook-skill", "patch")
        assert bump.new_version == "0.1.1"  # 谱系记录失败不阻塞版本升级

    def test_hook_disabled_by_none(self, enhancer):
        calls = []
        enhancer.set_lineage_hook(lambda ctx: calls.append(ctx))
        enhancer.bump_version("hook-skill", "patch")
        enhancer.set_lineage_hook(None)
        enhancer.bump_version("hook-skill", "patch")
        assert len(calls) == 1  # None 后恢复内置自动记录，不再走自定义 hook

    def test_hook_end_to_end_with_archive(self, enhancer, tmp_path):
        archive = EvolutionArchive(
            active_path=str(tmp_path / "evo.jsonl"),
            archive_path=str(tmp_path / "evo_old.jsonl"),
        )
        enhancer.set_lineage_hook(
            lambda ctx: archive.append(EvolutionRecord.from_bump(ctx)))
        bump = enhancer.bump_version("hook-skill", "minor", changelog="e2e")
        chain = archive.get_lineage("hook-skill")
        assert len(chain) == 1
        assert chain[0].new_version == bump.new_version
        assert chain[0].change_summary == "e2e"


# ════════════════════════════════════════════════════════════
#  EvolutionArchive.import_records 批量导入（历史数据迁移）
# ════════════════════════════════════════════════════════════

class TestImportRecords:
    def test_import_bulk_records(self, archive):
        records = [
            make_record("imp-obj", f"{i}.0.0", record_id=f"evt-imp-{i}")
            for i in range(1, 4)
        ]
        assert archive.import_records(records) == 3
        assert archive.count("imp-obj") == 3
        assert archive.get("evt-imp-1") is not None

    def test_import_accepts_dicts(self, archive):
        dicts = [
            {"object_id": "imp-obj", "record_id": f"evt-d-{i}",
             "new_version": f"{i}.0.0"}
            for i in range(1, 4)
        ]
        assert archive.import_records(dicts) == 3
        assert archive.get("evt-d-2").new_version == "2.0.0"

    def test_import_skips_existing(self, archive):
        archive.append(make_record("imp-obj", "1.0.0", record_id="evt-dup-1"))
        records = [
            make_record("imp-obj", "2.0.0", record_id="evt-dup-1"),
            make_record("imp-obj", "2.0.0", record_id="evt-dup-2"),
        ]
        assert archive.import_records(records) == 1  # dup-1 已存在跳过
        assert archive.get("evt-dup-1").new_version == "1.0.0"  # 未被覆盖
        assert archive.count() == 2

    def test_import_overwrite_replaces(self, archive):
        archive.append(make_record("imp-obj", "1.0.0", record_id="evt-ow-1"))
        assert archive.import_records(
            [make_record("imp-obj", "3.0.0", record_id="evt-ow-1")],
            overwrite=True) == 1
        assert archive.get("evt-ow-1").new_version == "3.0.0"
        assert archive.count() == 1  # 覆盖不新增

    def test_import_overwrite_recovers_archived(self, tmp_path):
        a = EvolutionArchive(
            active_path=str(tmp_path / "evolution_archive.jsonl"),
            archive_path=str(tmp_path / "evolution_archive_old.jsonl"),
            active_generations=1,
        )
        a.append(make_record("imp-obj", "1.0.0", record_id="evt-a-1"))
        a.append(make_record("imp-obj", "2.0.0", record_id="evt-a-2"))
        assert a.get("evt-a-1").archived is True  # 已压缩为摘要
        rec = make_record("imp-obj", "1.1.0", record_id="evt-a-1",
                          strategy="mutate")
        assert a.import_records([rec], overwrite=True) == 1
        restored = a.get("evt-a-1")
        assert restored.archived is False   # 摘要被完整记录替换
        assert restored.new_version == "1.1.0"
        assert restored.strategy == "mutate"

    def test_import_empty_object_id_raises(self, archive):
        with pytest.raises(ValueError):
            archive.import_records(
                [{"record_id": "evt-x-1", "new_version": "1.0.0"}])

    def test_import_archiving_applies(self, tmp_path):
        a = EvolutionArchive(
            active_path=str(tmp_path / "evolution_archive.jsonl"),
            archive_path=str(tmp_path / "evolution_archive_old.jsonl"),
            active_generations=3,
        )
        records = [
            make_record("imp-obj", f"{i}.0.0", record_id=f"evt-imp-{i}")
            for i in range(1, 6)
        ]
        assert a.import_records(records) == 5
        assert len(active_lines(tmp_path)) == 3  # 批量导入同样触发分层归档
        assert len(archive_lines(tmp_path)) == 2

    def test_import_persists_across_instances(self, tmp_path):
        p1, p2 = str(tmp_path / "a.jsonl"), str(tmp_path / "old.jsonl")
        a1 = EvolutionArchive(active_path=p1, archive_path=p2)
        a1.import_records(
            [make_record("imp-obj", "1.0.0", record_id="evt-persist-1")])
        a2 = EvolutionArchive(active_path=p1, archive_path=p2)
        assert a2.get("evt-persist-1").new_version == "1.0.0"


# ════════════════════════════════════════════════════════════
#  Service 只读谱系查询路由（交付物 4）
# ════════════════════════════════════════════════════════════

class TestServiceLineageRoutes:
    def test_service_lineage_routes(self, tmp_path):
        from agent.skills_mgmt import SkillsMgmtService

        svc = SkillsMgmtService(store_path=str(tmp_path / "skills.json"))
        svc.create_manual({
            "id": "route-skill", "name": "route-skill",
            "description": "t", "content": "# t",
            "content_type": "python", "category": "custom",
            "tags": ["t"], "author": "tester",
        })
        archive = EvolutionArchive(
            active_path=str(tmp_path / "evo.jsonl"),
            archive_path=str(tmp_path / "evo_old.jsonl"),
        )
        svc.set_lineage_hook(
            lambda ctx: archive.append(EvolutionRecord.from_bump(ctx)))
        svc.bump_version("route-skill", "patch", changelog="r1")
        svc._evolution_archive = archive  # 注入临时档案库（默认档案库用 env 路径）
        chain = svc.get_evolution_lineage("route-skill")
        assert len(chain) == 1
        assert chain[0].new_version == "0.1.1"
        out = svc.print_evolution_lineage("route-skill")
        assert "route-skill" in out
        assert "1 代" in out
        # 无记录对象不抛异常
        assert svc.get_evolution_lineage("ghost") == []

    def test_service_auto_record_visible_via_route(self, tmp_path):
        """真实进化（bump_version）自动记录后，service 只读路由可查询"""
        from agent.skills_mgmt import SkillsMgmtService

        svc = SkillsMgmtService(store_path=str(tmp_path / "skills.json"))
        svc.create_manual({
            "id": "auto-skill", "name": "auto-skill",
            "description": "t", "content": "# t",
            "content_type": "python", "category": "custom",
            "tags": ["t"], "author": "tester",
        })
        svc.bump_version("auto-skill", "patch", changelog="auto-record")
        # 未注入/未设置 hook → 内置自动记录写入默认档案库（conftest 隔离 tmp），
        # 路由与自动记录共用默认单例，应能直接查到
        chain = svc.get_evolution_lineage("auto-skill")
        assert len(chain) == 1
        assert chain[0].new_version == "0.1.1"
        assert chain[0].change_summary == "auto-record"
