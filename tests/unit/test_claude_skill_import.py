"""Claude 技能包导入（~/.claude/skills → 技能仓库）单元测试

【回归什么】2026-10-18 取消 claude_skill 扩展类型后，Claude 技能包不能再"装进
~/.claude/skills 就算完"，必须转成云枢技能资产（manifest.json + skill.md）。
本测试钉住转换口径：front matter 取 name/description、正文进 skill.md、
scripts/*.py 一并带上、逐条失败不阻断整批。
"""
import json
import zipfile
from pathlib import Path

from agent.skills_mgmt.claude_import import (
    _slug,
    import_claude_skills,
    scan_claude_skills,
)


def _write_skill(root: Path, dir_name: str, text: str, scripts=None) -> Path:
    d = root / dir_name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(text, encoding="utf-8")
    if scripts:
        (d / "scripts").mkdir()
        for name, body in scripts.items():
            (d / "scripts" / name).write_text(body, encoding="utf-8")
    return d


class _FakeSvc:
    """只记录传给 install_from_zip 的 zip 内容（不落真仓库）"""

    def __init__(self):
        self.calls = []

    def install_from_zip(self, zip_path):
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            manifest = json.loads(zf.read("manifest.json"))
            skill_md = zf.read("skill.md").decode("utf-8")
        self.calls.append({"names": names, "manifest": manifest,
                           "skill_md": skill_md})
        return {
            "skill_id": manifest["id"],
            "name": manifest["name"],
            "scripts_count": sum(1 for n in names if n.startswith("scripts/")),
        }


FRONT_MATTER_SKILL = """---
name: PDF Toolkit
description: Extract text from PDF files.
---

# PDF Toolkit

Use this when the user asks to read a PDF.
"""

PLAIN_SKILL = """# Plain Skill

No front matter here, just body.
"""


class TestSlug:
    def test_slug_lowercases_and_folds_invalid_chars(self):
        assert _slug("PDF-Toolkit") == "pdf-toolkit"
        assert _slug("My Skill v2") == "my-skill-v2"
        assert _slug("__weird__name__") == "weird-name"

    def test_slug_rejects_empty(self):
        assert _slug("!!!") == ""
        assert _slug("") == ""


class TestScan:
    def test_scan_reads_front_matter_and_body(self, tmp_path):
        _write_skill(tmp_path, "pdf-toolkit", FRONT_MATTER_SKILL)
        _write_skill(tmp_path, "plain-skill", PLAIN_SKILL)

        items = {i["id"]: i for i in scan_claude_skills(tmp_path)}

        assert set(items) == {"pdf-toolkit", "plain-skill"}
        pdf = items["pdf-toolkit"]
        assert pdf["ready"] is True
        assert pdf["name"] == "PDF Toolkit"
        assert pdf["description"] == "Extract text from PDF files."
        # 无 front matter ⇒ 用第一个标题/首段兜底，不因缺元数据而拒绝导入
        plain = items["plain-skill"]
        assert plain["ready"] is True
        assert plain["name"] == "Plain Skill"
        assert plain["description"] == "No front matter here, just body."

    def test_scan_skips_dirs_without_skill_md(self, tmp_path):
        (tmp_path / "not-a-skill").mkdir()
        (tmp_path / "not-a-skill" / "README.md").write_text("x", encoding="utf-8")
        assert scan_claude_skills(tmp_path) == []

    def test_scan_missing_base_dir_is_empty_not_error(self, tmp_path):
        assert scan_claude_skills(tmp_path / "nope") == []

    def test_scan_marks_unusable_dir_name(self, tmp_path):
        _write_skill(tmp_path, "!!!", FRONT_MATTER_SKILL)
        items = scan_claude_skills(tmp_path)
        assert len(items) == 1
        assert items[0]["ready"] is False
        assert "ID" in items[0]["error"]


class TestImport:
    def test_import_builds_manifest_and_skill_md(self, tmp_path):
        _write_skill(tmp_path, "pdf-toolkit", FRONT_MATTER_SKILL,
                     scripts={"run.py": "print('hi')\n"})
        svc = _FakeSvc()

        result = import_claude_skills(svc, base_dir=tmp_path)

        assert result["ok"] is True
        assert result["total"] == 1
        assert [i["id"] for i in result["imported"]] == ["pdf-toolkit"]
        assert result["failed"] == []

        call = svc.calls[0]
        manifest = call["manifest"]
        assert manifest["id"] == "pdf-toolkit"
        assert manifest["name"] == "PDF Toolkit"
        assert manifest["description"] == "Extract text from PDF files."
        # category=claude 会命中外来技能安全预检（assessor._EXTERNAL_CATEGORIES）
        assert manifest["category"] == "claude"
        assert manifest["source"] == "claude-skills"
        assert "scripts/run.py" in call["names"]
        # skill.md = 云枢 front matter + Claude 正文（原 front matter 已剥离）
        assert call["skill_md"].startswith("---")
        assert "name: PDF Toolkit" in call["skill_md"]
        assert "Use this when the user asks to read a PDF." in call["skill_md"]
        # 原 Claude front matter 已剥离，只剩云枢自己写的一对分隔符
        assert call["skill_md"].count("---") == 2

    def test_import_only_selected_ids(self, tmp_path):
        _write_skill(tmp_path, "pdf-toolkit", FRONT_MATTER_SKILL)
        _write_skill(tmp_path, "plain-skill", PLAIN_SKILL)
        svc = _FakeSvc()

        result = import_claude_skills(svc, ids=["plain-skill"], base_dir=tmp_path)

        assert result["total"] == 1
        assert [i["id"] for i in result["imported"]] == ["plain-skill"]

    def test_one_bad_candidate_does_not_block_others(self, tmp_path):
        _write_skill(tmp_path, "good-skill", FRONT_MATTER_SKILL)
        _write_skill(tmp_path, "!!!", FRONT_MATTER_SKILL)
        svc = _FakeSvc()

        result = import_claude_skills(svc, base_dir=tmp_path)

        assert result["ok"] is False  # 有失败 ⇒ 显式 ok=False（不假装全绿）
        assert [i["id"] for i in result["imported"]] == ["good-skill"]
        assert [f["id"] for f in result["failed"]] == ["!!!"]

    def test_install_failure_is_reported_per_skill(self, tmp_path):
        _write_skill(tmp_path, "boom", FRONT_MATTER_SKILL)

        class _BoomSvc:
            def install_from_zip(self, zip_path):
                raise RuntimeError("磁盘满了")

        result = import_claude_skills(_BoomSvc(), base_dir=tmp_path)

        assert result["ok"] is False
        assert result["failed"][0]["id"] == "boom"
        assert "磁盘满了" in result["failed"][0]["error"]
