"""persona 单源化：注入文本的正本在 skill.md front matter

【背景】`DigitalLifePersonaMixin._SKILL_PROMPTS` 曾是 persona 段的**唯一**来源，
与 skill.md（描述、启停、内容都在那儿）分家。2026-10-19 把正文写进 front matter
`persona:`，代码改为「front matter 优先、内置表兜底」。

本测试钉三件事：

1. **不漂移**：7 个内置技能的 front matter persona 必须与兜底表逐字一致
   （否则"改了一边忘了另一边"，注入内容会随部署路径悄悄变化）；
2. **文件轨优先**：persona 有值时，注入的就是文件轨内容（不是兜底表）；
3. **兜底可用**：文件轨读不到时回退兜底表（新部署/文件被清理也不至于人格消失）。
"""
from __future__ import annotations

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


class TestFrontMatterIsTheSource:
    def test_persona_is_in_meta_whitelist(self):
        # 不在白名单 ⇒ parse() 看不见、update_meta 写不进（会静默回退硬编码表）
        assert "persona" in _META_FIELDS

    @pytest.mark.parametrize("sid", sorted(DigitalLifePersonaMixin._SKILL_PROMPTS))
    def test_front_matter_matches_fallback_text(self, sid):
        assert (REPO / sid / "skill.md").is_file(), f"缺少 {sid}/skill.md"
        assert _read_persona(sid).strip() == DigitalLifePersonaMixin._SKILL_PROMPTS[sid].strip(), (
            f"{sid} 的 front matter persona 与兜底表不一致 —— 单源化要求两者逐字相同"
        )


class TestInjection:
    def test_file_track_persona_wins(self, monkeypatch):
        import agent.skills_mgmt.registry as reg_mod

        class _FakeRegistry:
            def list_enabled_ids(self):
                return ["self_reflection"]

            def get_persona(self, skill_id):
                return "## 自省反思（来自 skill.md）\n自定义注入正文。" if skill_id == "self_reflection" else ""

        monkeypatch.setattr(reg_mod, "SkillRegistry", _FakeRegistry)

        host = _Persona()
        host._cached_skill_instructions = None
        text = host._build_skill_instructions()

        assert "来自 skill.md" in text
        assert DigitalLifePersonaMixin._SKILL_PROMPTS["self_reflection"] not in text
        assert host._loaded_skill_ids == ["self_reflection"]

    def test_falls_back_when_file_track_missing(self, monkeypatch):
        import agent.skills_mgmt.registry as reg_mod

        class _EmptyRegistry:
            def list_enabled_ids(self):
                return ["self_reflection"]

            def get_persona(self, skill_id):
                return ""

        monkeypatch.setattr(reg_mod, "SkillRegistry", _EmptyRegistry)

        host = _Persona()
        host._cached_skill_instructions = None
        text = host._build_skill_instructions()

        assert DigitalLifePersonaMixin._SKILL_PROMPTS["self_reflection"] in text

    def test_registry_failure_still_falls_back(self, monkeypatch):
        import agent.skills_mgmt.registry as reg_mod

        class _BoomRegistry:
            def list_enabled_ids(self):
                return ["self_reflection"]

            def get_persona(self, skill_id):
                raise RuntimeError("file track broken")

        monkeypatch.setattr(reg_mod, "SkillRegistry", _BoomRegistry)

        host = _Persona()
        host._cached_skill_instructions = None
        text = host._build_skill_instructions()

        assert DigitalLifePersonaMixin._SKILL_PROMPTS["self_reflection"] in text
