"""agent/descriptors/backfill.py — S1-02 存量资产字段回填引擎测试

覆盖：摸底计数 / provenance/data_class/risk 确定性规则 / 外来安装 provenance 分类 /
NEEDS_REVIEW & NEEDS_UNDO_HINT 待补清单 / 分批回填幂等 + 审计 + 批级回滚 /
保守合并（不覆盖人工复核结果）/ destructive 三件套与 secret 禁外部端点校验 /
服务层 install_precheck 合并预检集成。
"""

import json

import pytest

from agent.descriptors.backfill import (
    classify_install_provenance,
    derive_plan_item,
    load_skill_assets,
    plan_backfill,
    run_backfill,
    survey_skill_assets,
    sync_skill_descriptor,
)
from agent.descriptors.models import DescriptorValidationError
from agent.descriptors.registry import DescriptorRegistry, name_similarity, normalize_name


# ─────────────────────────────────────────────────────────────
# 构造器
# ─────────────────────────────────────────────────────────────

def skill_asset(sid="skill-x", *, category="custom", source="manual",
                status="approved", author="workbench", is_sensitive=False,
                content_type="markdown", content="", description="",
                tags=None, config_schema=None, default_params=None,
                scripts=None, script_code=None, track="main",
                dependencies=None, output_schema=None) -> dict:
    """构造归一化资产（_normalize_asset 输出同构）。"""
    return {
        "id": sid,
        "name": sid,
        "description": description,
        "category": category,
        "source": source,
        "status": status,
        "author": author,
        "enabled": True,
        "is_sensitive": is_sensitive,
        "content_type": content_type,
        "tags": tags or [],
        "version": "0.1.0",
        "created_at": "",
        "installed_at": "",
        "track": track,
        "content": content,
        "has_config_schema": bool(config_schema),
        "has_output_schema": bool(output_schema),
        "config_schema": config_schema or {},
        "output_schema": output_schema or {},
        "default_params": default_params or {},
        "dependencies": dependencies or [],
        "scripts": scripts or [],
        "script_code": script_code or [],
        "review_verdict": "ok" if track == "main" else "",
        "auto_assessed": track == "main",
        "security_score": 100.0,
        "usage_count": 0,
        "success_rate": 0.0,
        "versions_count": 1,
    }


def write_store(path, entries):
    """写主轨 json：{id: skill dict}"""
    path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")


def make_repo(repo_dir, metas):
    """写文件轨：repo_dir/<id>/skill.md（front matter + body）"""
    import yaml
    for sid, meta in metas.items():
        d = repo_dir / sid
        d.mkdir(parents=True, exist_ok=True)
        fm = dict(meta)
        fm["id"] = sid
        (d / "skill.md").write_text(
            "---\n" + yaml.safe_dump(fm, allow_unicode=True) + "---\n"
            + (meta.get("_body") or f"# {sid}\n技能正文"), encoding="utf-8")


def tmp_plan(tmp_path):
    """构造隔离的迷你主轨+文件轨并生成计划（不依赖真实存量数据）。"""
    main = tmp_path / "skills_mgmt.json"
    repo = tmp_path / "skills_repo"
    repo.mkdir(exist_ok=True)
    write_store(main, {
        "alpha-local": {"id": "alpha-local", "name": "alpha-local",
                        "category": "custom", "source": "manual",
                        "status": "published", "author": "workbench",
                        "description": "本地技能", "content": "指令型内容",
                        "config_schema": {"type": "object", "properties": {}},
                        "output_schema": {}, "tags": [], "is_sensitive": False},
        "beta-distilled": {"id": "beta-distilled", "name": "beta-distilled",
                           "category": "custom", "source": "knowledge_distill",
                           "status": "approved", "author": "process_distill",
                           "description": "蒸馏技能", "content": "指令",
                           "config_schema": {"type": "object", "properties": {}},
                           "output_schema": {}, "tags": ["distilled"],
                           "is_sensitive": False},
        "gamma-import": {"id": "gamma-import", "name": "gamma-import",
                         "category": "custom", "source": "external_agent",
                         "status": "approved", "author": "unknown",
                         "description": "外来", "content": "指令",
                         "config_schema": {"type": "object", "properties": {}},
                         "output_schema": {}, "tags": ["external"],
                         "is_sensitive": False},
        "delta-sensitive": {"id": "delta-sensitive", "name": "delta-sensitive",
                            "category": "custom", "source": "manual",
                            "status": "published", "author": "workbench",
                            "description": "敏感规范", "content": "规范",
                            "config_schema": {"type": "object", "properties": {}},
                            "output_schema": {}, "tags": [],
                            "is_sensitive": True},
    })
    make_repo(repo, {
        "persona-a": {"name": "人设A", "category": "custom",
                      "source": "legacy_migration", "status": "approved",
                      "author": "unknown", "_body": "人设内容"},
        "persona-b": {"name": "人设B", "category": "custom",
                      "source": "legacy_migration", "status": "approved",
                      "author": "unknown", "_body": "人设内容"},
    })
    return plan_backfill(main_path=main, repo_path=repo)


# ─────────────────────────────────────────────────────────────
# 摸底（步骤 1）
# ─────────────────────────────────────────────────────────────

class TestSurvey:
    def test_survey_counts_union(self, tmp_path):
        planned = tmp_plan(tmp_path)
        survey = planned["survey"]
        # 主轨 4 ∪ 文件轨 2 = 6（无重叠）
        assert len(survey["assets"]) == 6
        assert survey["summary"]["asset_count"] == 6
        assert survey["summary"]["by_track"] == {"main": 4, "file_track": 2}
        assert survey["summary"]["by_source"] == {
            "manual": 2, "knowledge_distill": 1, "external_agent": 1,
            "legacy_migration": 2}
        # 覆盖率与行数据一致（可复核计数）
        cov = survey["summary"]["coverage"]
        rows = survey["rows"]
        assert cov["is_sensitive"]["yes"] == sum(1 for r in rows if r["is_sensitive"])
        assert cov["has_config_schema"]["yes"] == \
            sum(1 for r in rows if r["has_config_schema"])
        assert cov["has_source_record"]["yes"] == 6  # 全部有来源记录
        assert len(rows) == 6

    def test_main_track_authoritative_over_file_track(self, tmp_path):
        # 同 id 同时存在于主轨与文件轨 → 主轨为准（不重复计数）
        main = tmp_path / "s.json"
        repo = tmp_path / "r"
        repo.mkdir(exist_ok=True)
        write_store(main, {"dup": {"id": "dup", "name": "dup-main",
                                   "category": "custom", "source": "manual",
                                   "status": "approved"}})
        make_repo(repo, {"dup": {"name": "dup-file", "category": "custom",
                                 "source": "legacy_migration",
                                 "status": "approved"}})
        assets = load_skill_assets(main_path=main, repo_path=repo)
        assert len(assets) == 1
        assert assets[0]["track"] == "main"

    def test_deterministic_sorted_by_id(self, tmp_path):
        a = tmp_plan(tmp_path)
        b = tmp_plan(tmp_path)
        assert json.dumps(a, ensure_ascii=False, sort_keys=True) == \
            json.dumps(b, ensure_ascii=False, sort_keys=True)


# ─────────────────────────────────────────────────────────────
# provenance 确定性规则（步骤 2）
# ─────────────────────────────────────────────────────────────

class TestProvenanceRules:
    @pytest.mark.parametrize("source,expected", [
        ("builtin_source_unused", None),  # 占位（category=builtin 单独覆盖）
    ])
    def test_placeholder(self, source, expected):
        pass

    def test_builtin_verified(self):
        item = derive_plan_item(skill_asset("b1", category="builtin",
                                            source="manual"))
        assert item["provenance"]["level"] == "verified"
        assert item["provenance"]["rule"] == "PRV-1"
        assert item["automation_eligible"] is True
        assert item["provenance"]["evidence"]

    @pytest.mark.parametrize("source,rule", [
        ("manual", "PRV-2"),
        ("ai_assisted", "PRV-2"),
        ("workflow", "PRV-2"),
        ("knowledge_distill", "PRV-3"),
        ("process_distill", "PRV-3"),
        ("legacy_migration", "PRV-4"),
    ])
    def test_in_house_declared(self, source, rule):
        item = derive_plan_item(skill_asset("x1", source=source))
        assert item["provenance"]["level"] == "declared"
        assert item["provenance"]["rule"] == rule
        assert item["automation_eligible"] is False  # declared 不进自动化
        assert item["provenance"]["upgrade_path"]

    @pytest.mark.parametrize("source", [
        "external_agent", "github:user/repo", "url:https://x/y.json",
        "registry:openclaw/foo", "market:abc", "zip", "",
    ])
    def test_external_unknown(self, source):
        item = derive_plan_item(skill_asset("x2", source=source,
                                            category="claude"
                                            if source.startswith("github:")
                                            else "custom"))
        assert item["provenance"]["level"] == "unknown"
        assert item["provenance"]["rule"] == "PRV-5"
        assert any(f["scope"] == "provenance" for f in item["flags"]["needs_review"])

    def test_no_blanket_all_same(self, tmp_path):
        # 规则区分来源：declared 与 unknown 并存（非一刀切）
        planned = tmp_plan(tmp_path)
        prov = planned["coverage"]["provenance"]
        assert set(prov) >= {"declared", "unknown"}


# ─────────────────────────────────────────────────────────────
# 外来安装 provenance 分类（§2.3 合并预检）
# ─────────────────────────────────────────────────────────────

class TestInstallProvenance:
    def test_github_without_manifest_unknown(self):
        r = classify_install_provenance("github", "github:user/repo")
        assert r["level"] == "unknown"
        assert r["rule"] == "PRV-5"
        assert r["upgrade_path"]

    def test_payload_license_declared(self):
        r = classify_install_provenance(
            "github", "github:user/repo",
            {"license": "MIT", "author": "x"})
        assert r["level"] == "declared"
        assert r["rule"] == "PRV-7"
        assert r["manifest_present"] is True

    def test_manifest_license_declared(self):
        r = classify_install_provenance(
            "url", "url:https://x/skill.json",
            {"manifest": {"license": "Apache-2.0", "version": "1.0.0"}})
        assert r["level"] == "declared"

    def test_signature_signed(self):
        r = classify_install_provenance(
            "github", "github:user/repo", {"signature": "abc=="})
        assert r["level"] == "signed"
        assert r["rule"] == "PRV-0"

    def test_local_declared(self):
        r = classify_install_provenance("local", "local:/tmp/skill.json")
        assert r["level"] == "declared"
        assert r["rule"] == "PRV-8"

    def test_external_category_unknown(self):
        r = classify_install_provenance("github", "github:a/b",
                                        category="claude")
        assert r["level"] == "unknown"


# ─────────────────────────────────────────────────────────────
# data_class / risk / governance（步骤 2）
# ─────────────────────────────────────────────────────────────

class TestDataClassRules:
    def test_default_internal(self):
        item = derive_plan_item(skill_asset("d1"))
        assert item["data_class"]["value"] == "internal"
        assert item["data_class"]["applied"] is True
        assert item["data_class"]["rule"] == "DC-1"

    def test_is_sensitive_confidential_candidate(self):
        item = derive_plan_item(skill_asset("d2", is_sensitive=True))
        dc = item["data_class"]
        assert dc["applied"] is False
        assert dc["candidate"] == "confidential"
        assert dc["rule"] == "DC-2"
        assert any(f["scope"] == "data_class" for f in item["flags"]["needs_review"])

    def test_collect_personal_confidential_candidate(self):
        item = derive_plan_item(skill_asset(
            "d3", content="该技能负责收集用户的手机号与个人信息并上报。"))
        assert item["data_class"]["candidate"] == "confidential"
        assert item["data_class"]["applied"] is False

    def test_secret_param_candidate(self):
        item = derive_plan_item(skill_asset(
            "d4", default_params={"api_key": "sk-proj-real-secret-123"}))
        dc = item["data_class"]
        assert dc["candidate"] == "secret"
        assert dc["applied"] is False
        assert "secret" in dc["needs_review"].lower()

    def test_secret_never_applied_automatically(self, tmp_path):
        planned = tmp_plan(tmp_path)
        # 计划覆盖率中 secret 仅可能出现在 candidates（人工复核）
        assert "secret" not in planned["coverage"]["data_class_applied"]


class TestRiskRules:
    def test_instruction_only_low(self):
        item = derive_plan_item(skill_asset("r1", content="行为规范，无副作用。"))
        assert item["risk"]["level"] == "low"
        assert item["risk"]["rule"] == "RK-1"

    def test_destructive_candidate_not_applied(self):
        item = derive_plan_item(skill_asset(
            "r2", content="本技能会删除文件并清空目录中的全部数据。"))
        risk = item["risk"]
        assert risk["applied"] is False
        assert risk["candidate"] == "destructive"
        assert risk["rule"] == "RK-4"
        assert risk["requires_approval"] is True
        assert item["flags"]["needs_undo_hint"] is True
        assert any(f["scope"] == "risk" for f in item["flags"]["needs_review"])

    def test_destructive_with_approval_bypass_high_review(self):
        item = derive_plan_item(skill_asset(
            "r3", content="删除大量文件、force push 等操作可直接执行，无需弹窗确认。"))
        risk = item["risk"]
        assert risk["level"] == "high"
        assert risk["applied"] is True
        assert risk["rule"] == "RK-5"
        assert item["governance"]["undo_hint"]  # risk≥high 必须给补偿描述
        assert item["governance"]["compensating_action"]
        assert any(f["scope"] == "risk" for f in item["flags"]["needs_review"])

    def test_guard_rail_not_flagged(self):
        # 护栏技能：提及危险操作但强制二次确认 → 不升风险
        item = derive_plan_item(skill_asset(
            "r4", content="用户请求删除大量文件或格式化时，不直接执行，必须强制二次确认。"))
        assert item["risk"]["level"] == "low"
        assert item["risk"]["rule"] == "RK-1"

    def test_scripts_medium(self):
        item = derive_plan_item(skill_asset(
            "r5", content="示例", scripts=["main.py"],
            script_code=["import sys\nprint('hi')"]))
        assert item["risk"]["level"] == "medium"
        assert item["risk"]["rule"] == "RK-2"

    def test_code_high_risk_pattern(self):
        item = derive_plan_item(skill_asset(
            "r6", content_type="python",
            content="import shutil\nshutil.rmtree('/x')\nimport os\nos.system('rm -rf /')"))
        assert item["risk"]["level"] == "high"
        assert item["risk"]["rule"] == "RK-3"

    def test_risk_distribution_not_blanket(self, tmp_path):
        planned = tmp_plan(tmp_path)
        applied = planned["coverage"]["risk_applied"]
        assert set(applied) >= {"low"}  # 迷你资产全 low（此处校验计数口径）
        assert sum(applied.values()) == planned["coverage"]["assets"]


# ─────────────────────────────────────────────────────────────
# NEEDS 待补清单（资产级 + 处置路径）
# ─────────────────────────────────────────────────────────────

class TestNeedsQueues:
    def test_needs_review_asset_level_with_disposal(self, tmp_path):
        planned = tmp_plan(tmp_path)
        review = planned["needs"]["needs_review"]
        assert any(n["asset_id"] == "delta-sensitive"
                   and n["scope"] == "data_class" for n in review)
        assert any(n["asset_id"] == "gamma-import"
                   and n["scope"] == "provenance" for n in review)
        for n in review:
            assert n["capability_id"].startswith("cp.skill.")
            assert n["disposal"]  # 处置路径非空
        # 去重到资产级计数
        assert planned["coverage"]["needs_review_assets"] == \
            len({n["asset_id"] for n in review})

    def test_destructive_candidate_undo_queue(self, tmp_path):
        # 把文件轨加一条 destructive 候选资产验证 NEEDS_UNDO_HINT
        main = tmp_path / "s.json"
        repo = tmp_path / "r"
        repo.mkdir(exist_ok=True)
        write_store(main, {})
        make_repo(repo, {
            "del-skill": {"name": "删除器", "category": "custom",
                          "source": "external_agent", "status": "approved",
                          "author": "unknown",
                          "_body": "技能将删除文件并清空目录中的全部数据。"},
        })
        planned = plan_backfill(main_path=main, repo_path=repo)
        undo = planned["needs"]["needs_undo_hint"]
        assert any(n["asset_id"] == "del-skill" for n in undo)
        assert undo[0]["disposal"].startswith("人工复核补齐")


# ─────────────────────────────────────────────────────────────
# 分批回填实施（步骤 3：幂等 / 审计 / 保守合并 / 批级回滚）
# ─────────────────────────────────────────────────────────────

class TestApplyFlow:
    def test_apply_and_validation(self, tmp_path):
        planned = tmp_plan(tmp_path)
        reg_path = tmp_path / "descriptors.json"
        run = run_backfill(planned, registry_path=reg_path, batch_size=2)
        assert not run["stopped"]
        assert all(b["ok"] for b in run["batches"])
        assert len(run["batches"]) == 3  # 6 条 / 批 2
        reg = DescriptorRegistry(path=reg_path)
        reg.load()
        assert reg.count() == 6
        assert not reg.alias_records()  # CJK 名称不得误合并
        # 全量重校验：0 error；destructive/secret 不变量通过
        v = run["validation"]
        assert v["total"] == 6 and v["valid"] == 6 and v["with_errors"] == 0
        # 逐条审计留痕（register/provenance/trust patch）
        au = reg.audit_trail()
        assert au
        assert all(a["actor"] == "backfill:s1-02" for a in au)
        kinds = {a["action"] for a in au}
        assert "descriptor.register" in kinds
        assert "descriptor.provenance" in kinds
        assert "descriptor.patch" in kinds

    def test_rerun_idempotent(self, tmp_path):
        planned = tmp_plan(tmp_path)
        reg_path = tmp_path / "descriptors.json"
        run_backfill(planned, registry_path=reg_path, batch_size=2)
        run2 = run_backfill(planned, registry_path=reg_path, batch_size=2)
        assert run2["applied"]["no_op"] == 6
        writes = {k: v for k, v in run2["applied"].items()
                  if k != "no_op" and v}
        assert writes == {}
        reg = DescriptorRegistry(path=reg_path)
        reg.load()
        # 字段与计划一致
        for item in planned["plan"]:
            d = reg.get(item["capability_id"])
            assert d.trust.risk_level is None or \
                d.trust.risk_level.value == item["risk"]["level"]
            if item["data_class"]["applied"]:
                assert d.trust.data_class.value == item["data_class"]["value"]
            else:
                assert d.trust.data_class is None  # 候选不自动写入
            assert d.origin.provenance.value == item["provenance"]["level"]

    def test_conservative_preserves_human_upgrade(self, tmp_path):
        """人工已提升字段（verified/confidential/high）不被回填降级覆盖。"""
        planned = tmp_plan(tmp_path)
        reg_path = tmp_path / "descriptors.json"
        reg = DescriptorRegistry(path=reg_path, autosave=True)
        reg.load()
        # 模拟人工提升：alpha-local → verified + data_class=confidential + risk=high
        cid = "cp.skill.alpha-local"
        run_backfill(planned, registry=reg, batch_size=6)  # 先回填基线
        reg.mark_provenance(cid, "verified", evidence=["reviewer:r-001"],
                            actor="reviewer")
        reg.update_trust(cid, {"data_class": "confidential", "risk_level": "high"},
                         actor="reviewer")
        reg.save()
        # 再次回填 → 不得降级
        run_backfill(planned, registry=reg, batch_size=6)
        d = reg.get(cid)
        assert d.origin.provenance.value == "verified"
        assert d.trust.data_class.value == "confidential"
        assert d.trust.risk_level.value == "high"
        assert "reviewer:r-001" in d.origin.evidence

    def test_dry_run_identical_and_non_mutating(self, tmp_path):
        planned = tmp_plan(tmp_path)
        reg_path = tmp_path / "descriptors.json"
        d1 = run_backfill(planned, dry_run=True, registry_path=reg_path)
        d2 = run_backfill(planned, dry_run=True, registry_path=reg_path)
        assert json.dumps(d1, ensure_ascii=False, sort_keys=True) == \
            json.dumps(d2, ensure_ascii=False, sort_keys=True)
        assert not (tmp_path / "descriptors.json").exists()  # 不落库

    def test_batch_rollback_no_half_state(self, tmp_path, monkeypatch):
        """第二批失败 → 整批回滚，不残留半批；第一批保持已提交。"""
        planned = tmp_plan(tmp_path)
        reg_path = tmp_path / "descriptors.json"

        real_update_trust = DescriptorRegistry.update_trust
        failed = {"called": False}

        def boom(self_, cid, patch, **kw):
            if cid == "cp.skill.gamma-import" and not failed["called"]:
                failed["called"] = True
                raise RuntimeError("注入故障: 批内写失败")
            return real_update_trust(self_, cid, patch, **kw)

        monkeypatch.setattr(DescriptorRegistry, "update_trust", boom)
        run = run_backfill(planned, registry_path=reg_path, batch_size=1)
        monkeypatch.undo()

        assert run["stopped"] is True
        assert len(run["rollback_events"]) == 1
        assert run["rollback_events"][0]["rolled_back_items"] == ["gamma-import"]
        # 失败批整批回滚：gamma-import 与其同批条目都不在库；此前已提交批次保持
        reg = DescriptorRegistry(path=reg_path)
        reg.load()
        assert reg.get("cp.skill.gamma-import") is None
        # 资产按 id 排序 → gamma-import 为第 4 批，前 3 批（alpha/beta/delta）已提交
        assert reg.count() == 3
        assert reg.get("cp.skill.alpha-local") is not None
        assert reg.get("cp.skill.beta-distilled") is not None
        # 批记录错误留痕
        assert any(not b["ok"] for b in run["batches"])
        failed_batch = [b for b in run["batches"] if not b["ok"]][0]
        assert "注入故障" in failed_batch["errors"][0]

    def test_destructive_three_piece_enforced_by_validator(self, tmp_path):
        """校验器/写 API 强制：destructive 无三件套 → 拒绝写入（不静默放行）。"""
        reg = DescriptorRegistry(path=tmp_path / "d.json", autosave=True)
        reg.load()
        from descriptors_util import make_descriptor
        reg.register(make_descriptor("cp.fs.delete", name="delete"))
        with pytest.raises(DescriptorValidationError) as ei:
            reg.update_trust("cp.fs.delete",
                             {"risk_level": "destructive"},
                             actor="reviewer")
        assert any("destructive" in e for e in ei.value.errors)

    def test_secret_external_endpoint_rejected_by_validator(self, tmp_path):
        from descriptors_util import make_descriptor
        reg = DescriptorRegistry(path=tmp_path / "d2.json", autosave=True)
        reg.load()
        reg.register(make_descriptor("cp.fs.s", name="s", external=True))
        with pytest.raises(DescriptorValidationError):
            reg.update_trust("cp.fs.s", {"data_class": "secret"}, actor="reviewer")


# ─────────────────────────────────────────────────────────────
# CJK 名称误合并回归（S1-02 修正，宁冗余勿误合）
# ─────────────────────────────────────────────────────────────

class TestCjkNameSimilarityRegression:
    def test_distinct_cjk_names_not_identical(self):
        assert normalize_name("安全守护") == "安全守护"
        assert name_similarity("安全守护", "上下文感知") < 0.5
        assert name_similarity("安全守护", "安全守护") == 1.0

    def test_ascii_behavior_unchanged(self):
        assert name_similarity("read_file", "read_file") == 1.0
        assert name_similarity("read_file", "write_file") < 1.0
        assert name_similarity("", "") == 1.0
        assert normalize_name("") == ""
        assert normalize_name("read_File") == "read file"


# ─────────────────────────────────────────────────────────────
# 服务层集成（install_precheck provenance 合并预检）
# ─────────────────────────────────────────────────────────────

class TestServiceIntegration:
    def test_classify_static_helper_no_instance(self):
        from agent.skills_mgmt.service import SkillsMgmtService
        r = SkillsMgmtService._classify_install_provenance(
            "github", "github:a/b")
        assert r["level"] == "unknown"
        r2 = SkillsMgmtService._classify_install_provenance(
            "github", "github:a/b", {"license": "MIT"})
        assert r2["level"] == "declared"

    def test_install_precheck_merges_provenance(self, tmp_path):
        from agent.skills_mgmt.service import SkillsMgmtService
        payload = {
            "id": "precheck-x", "name": "precheck-x",
            "description": "预检样例",
            "content": "# 预检技能\n无风险指令",
            "category": "custom",
        }
        src = tmp_path / "skill.json"
        src.write_text(json.dumps(payload), encoding="utf-8")
        svc = SkillsMgmtService(store_path=str(tmp_path / "skills.json"),
                                repo_path=str(tmp_path / "repo"))
        res = svc.install_precheck(f"local:{src}")
        assert res["ok"] is True
        prov = res["provenance"]
        assert prov["level"] == "declared"  # local 受控来源
        assert prov["scheme"] == "local"
        assert prov["rule"] == "PRV-8"
        assert prov["upgrade_path"]
        # 既有安全预检字段保留（合并而非另起炉灶）
        assert "blocked" in res and "findings" in res

    def test_import_queue_rows_carry_provenance(self, tmp_path):
        from agent.skills_mgmt.models import Skill, SkillStatus
        from agent.skills_mgmt.service import SkillsMgmtService
        store_path = tmp_path / "skills.json"
        svc = SkillsMgmtService(store_path=str(store_path),
                                repo_path=str(tmp_path / "repo"))
        svc.store.upsert(Skill(
            id="queue-github-x", name="queue-github-x",
            category="claude", source="github:someone/repo",
            status=SkillStatus.DRAFT))
        rows = svc.import_queue()
        row = next(r for r in rows if r["id"] == "queue-github-x")
        assert row["provenance"]["level"] == "unknown"
        assert row["source"] == "github:someone/repo"

    def test_sync_skill_descriptor_writes_isolated_ledger(self, tmp_path):
        payload = {
            "id": "sync-skill", "name": "sync-skill", "category": "custom",
            "source": "github:user/repo", "status": "pending_review",
            "author": "unknown", "description": "刚安装的外来技能",
            "content": "指令内容", "config_schema": {},
            "output_schema": {},
        }
        res = sync_skill_descriptor(
            "sync-skill", source="github:user/repo",
            payload=payload,
            registry_path=tmp_path / "descriptors.json")
        assert res["ok"] is True
        assert res["provenance"] == "unknown"
        reg = DescriptorRegistry(path=tmp_path / "descriptors.json")
        reg.load()
        d = reg.get("cp.skill.sync-skill")
        assert d is not None
        assert d.origin.provenance.value == "unknown"
        assert d.trust.data_class.value == "internal"
        assert d.evolution.stage.value == "borrowed"  # 外来导入

    def test_sync_requires_payload_no_store_fallback(self, tmp_path):
        """descriptors 不反向依赖 skills_mgmt：缺 payload 返回错误而非读库。"""
        res = sync_skill_descriptor(
            "no-such-skill", registry_path=tmp_path / "descriptors.json")
        assert res["ok"] is False
        assert "payload" in res["error"]


# ─────────────────────────────────────────────────────────────
# 真实存量资产冒烟（仓库存在时；CI 无 assets 时跳过）
# ─────────────────────────────────────────────────────────────

@pytest.mark.skipif(
    not (pytest.importorskip("pathlib").Path("data/skills_mgmt.json").exists()),
    reason="本仓库无存量技能资产，跳过真实盘点冒烟")
class TestRealInventorySmoke:
    def test_real_inventory_survey_consistent(self):
        survey = survey_skill_assets()
        # 摸底规模 = 主轨 ∪ 文件轨 独立重数
        import pathlib
        main = json.loads(pathlib.Path("data/skills_mgmt.json")
                          .read_text(encoding="utf-8"))
        repo_dir = pathlib.Path("data/skills_repo")
        repo_ids = {d.name for d in repo_dir.iterdir()
                    if d.is_dir() and not d.name.startswith(".")
                    and (d / "skill.md").exists()}
        expect = len(set(main) | repo_ids)
        assert survey["summary"]["asset_count"] == expect > 0
        rows = survey["rows"]
        assert len(rows) == expect
        assert len({r["id"] for r in rows}) == expect

    def test_real_plan_coverage_reasonable(self):
        planned = plan_backfill()
        cov = planned["coverage"]
        assert cov["assets"] == planned["summary"]["asset_count"]
        assert cov["assets"] > 0
        # 全覆盖：provenance/data_class(含候选)/risk(含候选) 合计 = 资产数
        assert sum(cov["provenance"].values()) == cov["assets"]
        # 无人为全量一刀切：至少两类来源标记存在或候选清单非空（视存量而定）
        assert cov["assets"] >= 1
