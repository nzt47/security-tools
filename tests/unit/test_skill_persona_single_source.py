"""persona 单源化（收尾）：注入文本只剩 skill.md front matter 一份

【背景】#1218 把正文写进 front matter，但代码里仍留着 `_SKILL_PROMPTS` 当兜底。
兜底即第二事实源：改了 skill.md 却忘了改表，注入内容会随部署路径悄悄变化。
本 PR 删掉兜底表，只保留**有序 id 列表** `_PERSONA_SKILL_IDS`。

本测试钉四件事：

1. **只有一份真相**：类上不得再有 `_SKILL_PROMPTS`（防止兜底表被顺手加回来）；
2. **数据前置**：7 个 persona 技能的 skill.md 必须有**非空** front matter persona；
3. **注入来源**：注入正文就是 front matter（换掉文件轨，注入跟着变）；
4. **缺失可观测**：正文缺失/读失败 ⇒ 跳过该段并告警，不静默塞替代文本。
"""
from __future__ import annotations

import logging
import pathlib

import pytest

from agent.digital_life_persona import DigitalLifePersonaMixin
from agent.skills_mgmt.file_store import SkillMDParser, _META_FIELDS

ROOT = pathlib.Path(__file__).resolve().parents[2]
REPO = ROOT / "data" / "skills_repo"


class _Persona(DigitalLifePersonaMixin):
    """最小宿主：只测 _build_skill_instructions 的技能段"""

    def _current_skill_pack(self):
        return None


def _read_persona(skill_id: str) -> str:
    text = (REPO / skill_id / "skill.md").read_text(encoding="utf-8")
    meta, _body = SkillMDParser.parse(text)
    return str(meta.get("persona") or "")


class TestSingleSource:
    def test_no_fallback_table_left(self):
        assert not hasattr(DigitalLifePersonaMixin, "_SKILL_PROMPTS"), (
            "兜底表又回来了：单源化要求正文只从 skill.md 的 persona 字段读")

    def test_persona_is_in_meta_whitelist(self):
        # 不在白名单 ⇒ parse() 看不见、update_meta 写不进（注入会静默少一段）
        assert "persona" in _META_FIELDS

    def test_ids_are_unique_and_ordered(self):
        ids = DigitalLifePersonaMixin._PERSONA_SKILL_IDS
        assert len(ids) == 7 and len(set(ids)) == 7, ids
        assert ids[0] == "self_reflection", (
            "顺序是注入文本可复现性的一部分（skillpack.filter 保序依赖它），不要随意改")

    @pytest.mark.parametrize("sid", DigitalLifePersonaMixin._PERSONA_SKILL_IDS)
    def test_every_persona_skill_has_front_matter_text(self, sid):
        assert (REPO / sid / "skill.md").is_file(), f"缺少 {sid}/skill.md"
        assert _read_persona(sid).strip(), (
            f"{sid} 的 skill.md 没有 persona 正文 —— 该段会被跳过（日志里会有告警）")


class TestInjection:
    def test_front_matter_is_what_gets_injected(self, monkeypatch):
        import agent.skills_mgmt.registry as reg_mod

        class _FakeRegistry:
            def list_enabled_ids(self):
                return ["self_reflection"]

            def get_persona(self, skill_id):
                return "## 自省反思（来自 skill.md）\n自定义注入正文。"

        monkeypatch.setattr(reg_mod, "SkillRegistry", _FakeRegistry)
        host = _Persona()
        host._cached_skill_instructions = None
        text = host._build_skill_instructions()

        assert "来自 skill.md" in text
        assert host._loaded_skill_ids == ["self_reflection"]

    def test_missing_persona_skips_segment_and_warns(self, monkeypatch, caplog):
        import agent.skills_mgmt.registry as reg_mod

        class _EmptyRegistry:
            def list_enabled_ids(self):
                return ["self_reflection", "voice_interaction"]

            def get_persona(self, skill_id):
                return "" if skill_id == "self_reflection" else "## 语音交互\n有正文。"

        monkeypatch.setattr(reg_mod, "SkillRegistry", _EmptyRegistry)
        host = _Persona()
        host._cached_skill_instructions = None
        with caplog.at_level(logging.WARNING):
            text = host._build_skill_instructions()

        assert "语音交互" in text
        assert "自省反思" not in text, "缺正文时不得注入任何替代文本"
        assert host._loaded_skill_ids == ["voice_interaction"], (
            "没注入的 id 不得进 _loaded_skill_ids（否则幻觉校验会拿错的基准）")
        assert "self_reflection" in caplog.text, "缺 persona 必须在日志里看得见（不得静默）"

    def test_registry_failure_skips_segment_without_raising(self, monkeypatch):
        import agent.skills_mgmt.registry as reg_mod

        class _BoomRegistry:
            def list_enabled_ids(self):
                return ["self_reflection"]

            def get_persona(self, skill_id):
                raise RuntimeError("file track broken")

        monkeypatch.setattr(reg_mod, "SkillRegistry", _BoomRegistry)
        host = _Persona()
        host._cached_skill_instructions = None

        assert host._build_skill_instructions() == ""
        assert host._loaded_skill_ids == []
