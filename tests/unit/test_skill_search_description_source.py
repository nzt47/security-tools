# -*- coding: utf-8 -*-
"""G1-C / R-d 单测：管理页「搜索」必须与「展示」用**同一份**描述（文件轨）。

## 修的是哪条残留

G1-B 登记的 R-d：「展示读文件轨、搜索没跟上 ⇒ **看到的是新文案、搜到的按旧文案**」。

实测的消费链（修复前）：

    GET /api/skills-mgmt/search            （yunshu-ui/src/lib/skillsApi.ts:253）
      → SkillsMgmtService.search()          agent/skills_mgmt/service.py
      → SkillSearcher.search(self.store.list_all(), params)
                            ^^^^^^^^^^^^^^^^^^^^^^^ 只喂**主轨 Skill 模型**
      → _match_score(skill, …) → _tokenize(skill.description)   ← 主轨文案

而展示链是：

    GET /api/skills → app_server._reg().as_legacy_rows()   （plugins/skills.py:172 `_skills_mgr.get_all()`）
      → registry.as_legacy_rows()  描述 = **文件轨优先** + `description_zh`
      → yunshu-ui 渲染 `description_zh || description`

⇒ 同一字段两个消费者看到两份文案。

## 修法（全部落在 agent/skills_mgmt/ 内）

    `SkillSearcher.search(skills, params, meta_index=…)` 新增可选入参：文件轨元数据索引
    （`file_store.load_metadata_index()` 的返回值），由 `SkillsMgmtService.search()`
    **一次**取好传入。`_match_score` 描述侧改为：

        desc_text = (文件轨 description 若可用且开关=1，否则主轨) + " " + 文件轨 description_zh

    · 与 `registry.as_legacy_rows()` **同口径**（含同一个逃生开关
      `CP_SKILL_DESC_FROM_FILE_TRACK`）；
    · `meta_index=None`（默认）⇒ 行为与修复前**逐字相同**（向后兼容）。

本文件全部断言走**生产入口**（`SkillsMgmtService.search` / `SkillSearcher.search`），
隔离服务的 store/repo 都在 tmp 下，绝不触碰生产 data/。
"""

import pytest

from agent.skills_mgmt import SkillsMgmtService
from agent.skills_mgmt.models import SkillSearchParams
from agent.skills_mgmt.searcher import SkillSearcher, _desc_from_file_track


@pytest.fixture
def iso_svc(tmp_path):
    return SkillsMgmtService(
        store_path=str(tmp_path / "skills_mgmt.json"),
        repo_path=str(tmp_path / "skills_repo"),
    )


def _seed(svc, sid, *, main_desc, file_desc="", file_zh="", name=None):
    """两条轨各写一份描述：主轨 = 历史副本，文件轨 = 唯一事实源"""
    if file_desc or file_zh:
        svc.file_store.create(
            sid, {"id": sid, "name": name or sid, "description": file_desc,
                  "description_zh": file_zh, "enabled": True},
            instruction="# body")
    skill = svc.creator.create_manual({
        "id": sid, "name": name or sid, "description": main_desc,
        "content": "# x", "content_type": "markdown"})
    svc.store.upsert(skill)
    return skill


def _ids(svc, q, **kw):
    r = svc.search(SkillSearchParams(query=q, page_size=100, **kw))
    return [s.id for s in r.items]


# ════════════════════════════════════════════════════════════════════
#  R-d-1  开关口径必须与 registry 一致（否则"展示与搜索一起回滚"是假的）
# ════════════════════════════════════════════════════════════════════

class TestEscapeHatchParity:
    """searcher 是 registry 开关的**本地副本**（本包"模块独立性"约定）⇒ 必须同语义。"""

    @pytest.mark.parametrize("value,expected", [
        (None, True), ("", True), ("1", True), ("true", True), ("YES", True),
        ("0", False), ("false", False), ("no", False), ("OFF", False),
    ])
    def test_matches_registry_semantics(self, monkeypatch, value, expected):
        from agent.skills_mgmt.registry import _desc_from_file_track as reg_sw
        if value is None:
            monkeypatch.delenv("CP_SKILL_DESC_FROM_FILE_TRACK", raising=False)
        else:
            monkeypatch.setenv("CP_SKILL_DESC_FROM_FILE_TRACK", value)
        assert _desc_from_file_track() is expected
        assert reg_sw() is expected, (
            "searcher 与 registry 的逃生开关语义分叉 ⇒ 回滚时会只回滚一半")


# ════════════════════════════════════════════════════════════════════
#  R-d-2  搜索命中的必须是"看到的那份"文案
# ════════════════════════════════════════════════════════════════════

class TestSearchUsesDisplayedDescription:

    #: 三段互不重叠的文本 —— 保证"命中/不命中"只能由文案来源解释
    MAIN_ONLY = "legacycopy"
    FILE_EN = "observablelogs"
    FILE_ZH = "可观测性中文说明"

    def test_file_track_english_is_searchable(self, iso_svc):
        _seed(iso_svc, "rd-en", main_desc=self.MAIN_ONLY,
              file_desc=f"Use when {self.FILE_EN} are needed",
              file_zh=self.FILE_ZH)
        assert "rd-en" in _ids(iso_svc, self.FILE_EN), (
            "文件轨 description 搜不到 ⇒ 搜索仍按主轨文案（R-d 未修）")

    def test_description_zh_is_searchable(self, iso_svc):
        """管理页真正显示的是 description_zh（skills.tsx 读 zh‖en）⇒ 必须可搜"""
        _seed(iso_svc, "rd-zh", main_desc=self.MAIN_ONLY,
              file_desc=f"Use when {self.FILE_EN} are needed",
              file_zh=self.FILE_ZH)
        assert "rd-zh" in _ids(iso_svc, "可观测性"), (
            "description_zh 搜不到 ⇒ 用户看到中文却搜不到它")

    def test_stale_main_track_copy_is_no_longer_matched(self, iso_svc):
        """唯一源收敛的**必然推论**：主轨那份历史副本不再参与打分。

        这条是"没修"与"修了"最锋利的分界：修复前 MAIN_ONLY 命中、文件轨文案不命中。
        """
        _seed(iso_svc, "rd-stale", main_desc=self.MAIN_ONLY,
              file_desc=f"Use when {self.FILE_EN} are needed",
              file_zh=self.FILE_ZH)
        assert "rd-stale" not in _ids(iso_svc, self.MAIN_ONLY), (
            "废弃的主轨副本仍能命中 ⇒ 描述又有两个源")

    def test_backward_compatible_when_no_index(self, iso_svc):
        """`meta_index=None`（未接线/旧调用方）⇒ 逐字退回旧行为"""
        from agent.skills_mgmt.searcher import SkillSearcher
        _seed(iso_svc, "rd-compat", main_desc=self.MAIN_ONLY,
              file_desc=f"Use when {self.FILE_EN} are needed",
              file_zh=self.FILE_ZH)
        skills = iso_svc.store.list_all()
        p = SkillSearchParams(query=self.MAIN_ONLY, page_size=100)
        assert [s.id for s in SkillSearcher().search(skills, p).items] == ["rd-compat"]
        p2 = SkillSearchParams(query=self.FILE_EN, page_size=100)
        assert [s.id for s in SkillSearcher().search(skills, p2).items] == []

    def test_escape_hatch_zero_restores_main_track_english(self, iso_svc, monkeypatch):
        """开关=0 ⇒ 英文侧回主轨（与 as_legacy_rows 一致），中文侧仍取文件轨"""
        monkeypatch.setenv("CP_SKILL_DESC_FROM_FILE_TRACK", "0")
        _seed(iso_svc, "rd-hatch", main_desc=self.MAIN_ONLY,
              file_desc=f"Use when {self.FILE_EN} are needed",
              file_zh=self.FILE_ZH)
        # 断言的镜像映像是 as_legacy_rows，不是"我期望"
        from agent.skills_mgmt.registry import SkillRegistry
        row = next(r for r in SkillRegistry(service=iso_svc).as_legacy_rows()
                   if r["id"] == "rd-hatch")
        assert row["description"] == self.MAIN_ONLY
        assert "rd-hatch" in _ids(iso_svc, self.MAIN_ONLY), (
            "开关=0 时搜索未跟随回滚 ⇒ 搜索与展示口径分叉")
        assert "rd-hatch" in _ids(iso_svc, "可观测性"), "中文文案被回滚弄丢了"

    def test_no_file_entity_keeps_main_track(self, iso_svc):
        """文件轨无实体的技能（global-core-principles 一类）行为完全不变"""
        _seed(iso_svc, "rd-fileless", main_desc=self.MAIN_ONLY)
        assert "rd-fileless" in _ids(iso_svc, self.MAIN_ONLY)


# ════════════════════════════════════════════════════════════════════
#  R-d-3  生产数据面上的实测（不隔离）
# ════════════════════════════════════════════════════════════════════

class TestRealRepoSearchMatchesDisplay:

    def _svc(self):
        return SkillsMgmtService()

    def test_migrated_skills_are_searchable_by_file_track_text(self):
        """G1-C 迁移的 5 条：文件轨英文描述里的词必须能搜到"""
        svc = self._svc()
        cases = {
            "testing-anti-patterns": "mock",
            "code-observability": "observable",
            "engineering-test-delivery": "audit",
            "frontend-state-sync": "abortcontroller",
            "self-explanatory-ui": "hierarchy",
        }
        miss = {sid: q for sid, q in cases.items() if sid not in _ids(svc, q)}
        assert miss == {}, f"迁移后的技能按文件轨文案搜不到: {miss}"

    def test_search_and_display_report_the_same_text(self):
        """同一 id：搜索打分用的文案 == 展示用的文案（逐条比对，不抽样）"""
        svc = self._svc()
        from agent.skills_mgmt.file_store import SkillFileStore
        from agent.skills_mgmt.registry import SkillRegistry
        meta = SkillFileStore().load_metadata_index(refresh=False)
        rows = {r["id"]: r for r in SkillRegistry().as_legacy_rows()}
        dual = [sid for sid in meta if sid in rows]
        assert dual, "没有双轨技能，断言退化"
        bad = [sid for sid in dual
               if str(rows[sid].get("description") or "")
               != str(meta[sid].get("description") or "")
               or str(rows[sid].get("description_zh") or "")
               != str(meta[sid].get("description_zh") or "")]
        assert bad == [], f"展示与唯一源不一致: {bad}"
        # 搜索侧：把入参索引换成"被打乱的文件轨描述"，命中集必须变化 ⇒
        # 证明搜索**确实**在用文件轨，而不是碰巧命中
        twisted = {sid: dict(m) for sid, m in meta.items()}
        for m in twisted.values():
            m["description"] = "zzzznotpresent"
            m["description_zh"] = ""
        p = SkillSearchParams(query="observable", page_size=100)
        assert "code-observability" in [
            s.id for s in SkillSearcher().search(svc.store.list_all(), p,
                                                 meta_index=meta).items]
        assert "code-observability" not in [
            s.id for s in SkillSearcher().search(svc.store.list_all(), p,
                                                 meta_index=twisted).items], (
            "换了索引命中集不变 ⇒ 这条断言是空转的（搜索没在读文件轨）")
