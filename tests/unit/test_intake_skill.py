# -*- coding: utf-8 -*-
"""技能入轨脚本（scripts/intake_skill.py）的单元测试。

守四件事（对应 §11.32 C · N5 手工入轨时踩过的坑）：
1. **id 只做目录名等值匹配** —— 非法/穿越 id 既进不了路径也进不了 argv；
2. **回填残留后缀（自动整理）必须阻断** —— 否则会被永久冻进仓库（§11.21 第一次踩）；
3. **git 操作 argv 固定** —— 动态部分只经 cwd 传（幂等 + 不给注入面）；
4. **基线只改数字位** —— 且只认 _BASELINE_SPECS 里登记的那几处，不误伤别处。
"""
from __future__ import annotations

import importlib

import pytest

intake = importlib.import_module("scripts.intake_skill")


GOOD_SKILL = """---
id: demo-skill
name: demo-skill
description: Use when testing the intake script.
author: unknown
category: custom
source: external_agent
tags:
- external
content_type: markdown
enabled: true
version: 0.1.0
---

# Demo Skill

## 何时使用

- 测试入轨脚本时使用。

## 示例

> 以下示例摘自上文正文（逐字引用，未新增内容）。

demo
"""


def _write_skill(root, sid, text):
    d = root / sid
    d.mkdir(parents=True, exist_ok=True)
    (d / "skill.md").write_text(text, encoding="utf-8")
    return d


# ════════════════════════════════════════════════════════════
#  1. id → 目录：等值匹配，不拼路径
# ════════════════════════════════════════════════════════════

class TestResolveSkillDir:

    def test_按目录名等值匹配(self, tmp_path, monkeypatch):
        _write_skill(tmp_path, "demo-skill", GOOD_SKILL)
        monkeypatch.setattr(intake, "SKILLS_REPO", tmp_path)
        assert intake.resolve_skill_dir("demo-skill") == tmp_path / "demo-skill"

    @pytest.mark.parametrize("bad", ["..", "../etc", "demo/../x", "", "DEMO-SKILL"])
    def test_非法或大小写不符一律不匹配(self, tmp_path, monkeypatch, bad):
        _write_skill(tmp_path, "demo-skill", GOOD_SKILL)
        monkeypatch.setattr(intake, "SKILLS_REPO", tmp_path)
        assert intake.resolve_skill_dir(bad) is None

    def test_目录不存在时返回_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(intake, "SKILLS_REPO", tmp_path / "nope")
        assert intake.resolve_skill_dir("demo-skill") is None


# ════════════════════════════════════════════════════════════
#  2. 形状校验 + 残留后缀
# ════════════════════════════════════════════════════════════

class TestCheckSkill:

    def test_干净技能无_error(self, tmp_path):
        d = _write_skill(tmp_path, "demo-skill", GOOD_SKILL)
        info = intake.check_skill(d)
        assert info["errors"] == []
        assert info["name"] == "demo-skill"

    def test_缺_skill_md(self, tmp_path):
        d = tmp_path / "empty-skill"
        d.mkdir()
        assert intake.check_skill(d)["errors"] == ["缺少 skill.md"]

    @pytest.mark.parametrize("suffix", ["（自动整理）", "(自动整理)", "（整理）"])
    def test_残留后缀必须阻断(self, tmp_path, suffix):
        text = GOOD_SKILL.replace("name: demo-skill", "name: demo-skill" + suffix)
        d = _write_skill(tmp_path, "demo-skill", text)
        info = intake.check_skill(d)
        assert any("残留回填后缀" in e for e in info["errors"]), info
        assert any("redraft/apply" in e for e in info["errors"])

    def test_正常名字不被误判(self, tmp_path):
        text = GOOD_SKILL.replace("name: demo-skill", "name: demo-skill-v2").replace(
            "id: demo-skill", "id: demo-skill-v2")
        d = _write_skill(tmp_path, "demo-skill-v2", text)
        assert intake.check_skill(d)["errors"] == []


# ════════════════════════════════════════════════════════════
#  3. git 操作：argv 固定，cwd 隔离
# ════════════════════════════════════════════════════════════

class _R:
    returncode = 0
    stdout = ""
    stderr = ""


class TestGitCalls:

    def test_git_add_只用_cwd_定位(self, tmp_path, monkeypatch):
        d = _write_skill(tmp_path, "demo-skill", GOOD_SKILL)
        calls = []

        def fake_run(cmd, cwd, **kw):
            calls.append((list(cmd), cwd))
            return _R()

        monkeypatch.setattr(intake, "_run", fake_run)
        ok, err = intake.stage_skill(d)
        assert ok and err == ""
        assert calls == [(["git", "add", "--", "."], d)], (
            "argv 必须是固定形式（动态部分只经 cwd），否则就是注入面: %r" % (calls,))

    def test_is_tracked_同样固定_argv(self, tmp_path, monkeypatch):
        d = _write_skill(tmp_path, "demo-skill", GOOD_SKILL)
        calls = []

        def fake_run(cmd, cwd, **kw):
            calls.append((list(cmd), cwd))
            return _R()

        monkeypatch.setattr(intake, "_run", fake_run)
        assert intake.is_tracked(d) is True
        assert calls[0][0][:2] == ["git", "ls-files"]
        assert calls[0][1] == d

    def test_干跑不落盘(self, tmp_path, monkeypatch):
        _write_skill(tmp_path, "demo-skill", GOOD_SKILL)
        monkeypatch.setattr(intake, "SKILLS_REPO", tmp_path)
        monkeypatch.setattr(intake, "ROOT", tmp_path)
        monkeypatch.setattr(intake, "stage_skill", lambda d: pytest.fail("干跑不该 git add"))
        monkeypatch.setattr(intake, "derive_manifest", lambda ds: pytest.fail("干跑不该重派生"))
        code = intake.main(["demo-skill"])
        assert code == 0


# ════════════════════════════════════════════════════════════
#  4. 基线：算得对、只改登记处
# ════════════════════════════════════════════════════════════

def _fake_manifest():
    def ent(kind, name, enabled=True, loc="local"):
        return {"kind": kind, "tool_name": name, "enabled": enabled, "location": loc,
                "tool_type": "skill" if kind == "skill" else "tool"}
    return {"entries": [
        ent("skill", "s1"), ent("skill", "s2"), ent("skill", "s3", enabled=False),
        ent("tool", "t1"), ent("tool", "t2", loc="remote"),
        ent("tool", "t3", loc="remote"),
    ]}


class TestComputeBaselines:

    def test_口径与注册表一致(self):
        nums = intake.compute_baselines(_fake_manifest())
        assert nums == {
            "entries": 6, "stats_total": 6, "skill_total": 3, "tool_total": 3,
            "local": 4, "remote": 2, "enabled_skills": 2, "envelope_total": 5,
        }

    def test_空清单不炸(self):
        nums = intake.compute_baselines({"entries": []})
        assert nums["entries"] == 0 and nums["envelope_total"] == 0


#: 三个假用例文件分别承接 _BASELINE_SPECS 里登记的落点（路径必须逐字对上）。
SAMPLE_SPEC = '''# 假用例文件（test_capability_spec.py）
    assert len(manifest["entries"]) == 133
# 干扰项：这里也有 133，但不在登记表里，不许被改
UNRELATED = 133
'''

SAMPLE_CORE = '''# 假用例文件（test_capregistry_core.py）
        assert st["total"] == 133, f"能力总数应为 133，实际 {st['total']}"
        assert st["by_kind"] == {"skill": 42, "tool": 91}
        assert st["by_location"] == {"local": 112, "remote": 21}
        assert len(tools) == 91 and len(skills) == 41
        assert idx["primary"] == 133
        assert env["data"]["returned"] == env["data"]["total"] == 132
        assert env["data"]["total"] == 132
# 干扰项：这里也有 133，但不在登记表里，不许被改
UNRELATED = 133
'''

SAMPLE_CONFIRM = '''# 假用例文件（test_confirm_level.py）
        assert len(skills) == 42, len(skills)
UNRELATED = 42
'''


NEW_NUMS = {"entries": 135, "stats_total": 135, "skill_total": 44, "tool_total": 91,
            "local": 114, "remote": 21, "enabled_skills": 43, "envelope_total": 134}


class TestRewriteBaselines:

    def _prepare(self, tmp_path):
        unit = tmp_path / "tests" / "unit"
        unit.mkdir(parents=True)
        (unit / "test_capability_spec.py").write_text(SAMPLE_SPEC, encoding="utf-8")
        (unit / "test_capregistry_core.py").write_text(SAMPLE_CORE, encoding="utf-8")
        (unit / "test_confirm_level.py").write_text(SAMPLE_CONFIRM, encoding="utf-8")
        return tmp_path

    def _read(self, root, name):
        return (root / "tests" / "unit" / name).read_text(encoding="utf-8")

    def test_逐处替换且不碰干扰项(self, tmp_path):
        root = self._prepare(tmp_path)
        changes = intake.rewrite_baselines(NEW_NUMS, execute=True, root=root)
        spec = self._read(root, "test_capability_spec.py")
        core = self._read(root, "test_capregistry_core.py")
        confirm = self._read(root, "test_confirm_level.py")

        assert 'assert len(manifest["entries"]) == 135' in spec
        assert 'assert st["total"] == 135, f"能力总数应为 135' in core
        assert 'assert st["by_kind"] == {"skill": 44, "tool": 91}' in core
        assert 'assert st["by_location"] == {"local": 114, "remote": 21}' in core
        assert 'assert len(tools) == 91 and len(skills) == 43' in core
        assert 'assert idx["primary"] == 135' in core
        assert 'assert env["data"]["returned"] == env["data"]["total"] == 134' in core
        assert 'assert len(skills) == 44, len(skills)' in confirm
        # 干扰项保持原样（不在登记表里的同值数字不许被改）
        assert "UNRELATED = 133" in spec and "UNRELATED = 133" in core
        assert "UNRELATED = 42" in confirm
        # 每条改动都要能报出来（人 review 用）
        assert changes and all(c["old"] and c["new"] for c in changes)

    def test_幂等_第二次无改动(self, tmp_path):
        root = self._prepare(tmp_path)
        intake.rewrite_baselines(NEW_NUMS, execute=True, root=root)
        assert intake.rewrite_baselines(NEW_NUMS, execute=True, root=root) == []

    def test_干跑不写文件(self, tmp_path):
        root = self._prepare(tmp_path)
        changes = intake.rewrite_baselines(NEW_NUMS, execute=False, root=root)
        assert changes
        assert self._read(root, "test_capability_spec.py") == SAMPLE_SPEC
        assert self._read(root, "test_capregistry_core.py") == SAMPLE_CORE
        assert self._read(root, "test_confirm_level.py") == SAMPLE_CONFIRM


# ════════════════════════════════════════════════════════════
#  5. 参数层
# ════════════════════════════════════════════════════════════

class TestArgs:

    def test_非法_id_退出码_2(self, capsys):
        assert intake.main(["..\\evil"]) == 2
        assert "非法技能 id" in capsys.readouterr().out

    def test_fix_baselines_必须配_execute(self):
        with pytest.raises(SystemExit) as exc:
            intake.main(["demo-skill", "--fix-baselines"])
        assert exc.value.code == 2


# ════════════════════════════════════════════════════════════
#  6. 程序化入口（REST /api/skills-mgmt/intake/* 用的就是这两个函数）
# ════════════════════════════════════════════════════════════

class TestProgrammaticApi:

    def test_intake_one_拒绝非法_id(self):
        for bad in ["..", "../x", "", "Not-Valid"]:
            out = intake.intake_one(bad, execute=True)
            assert out["ok"] is False and out["stage"] == "validate", (bad, out)

    def test_intake_one_目录不存在(self):
        out = intake.intake_one("no-such-skill-xyz", execute=True)
        assert out["ok"] is False and "不存在" in out["error"]

    def test_intake_one_干跑不改动已入轨技能(self, tmp_path, monkeypatch):
        _write_skill(tmp_path, "demo-skill", GOOD_SKILL)
        monkeypatch.setattr(intake, "SKILLS_REPO", tmp_path)
        monkeypatch.setattr(intake, "is_tracked", lambda d: True)
        monkeypatch.setattr(intake, "stage_skill", lambda d: pytest.fail("已入轨不该 add"))
        out = intake.intake_one("demo-skill", execute=False)
        assert out["ok"] is True and out["mode"] == "dry-run" and out["tracked_before"] is True

    def test_pending_report_真实仓库_字段齐全(self):
        items = intake.pending_report()
        assert isinstance(items, list) and items, "真实文件轨不该为空"
        by_id = {i["skill_id"]: i for i in items}
        assert {"tracked", "errors", "warnings"} <= set(next(iter(items)))
        # humanizer-zh 已在 2026-10-31 入轨（本轮实测），应显示 tracked=True
        if "humanizer-zh" in by_id:
            assert by_id["humanizer-zh"]["tracked"] is True

    def test_tracked_skill_ids_读的是_git_index(self):
        ids = intake.tracked_skill_ids()
        assert isinstance(ids, set) and ids, "git 里应至少有已入轨的技能"
        assert "pdf" in ids and "humanizer-zh" in ids


# ════════════════════════════════════════════════════════════
#  6. 命令行加固（#666 py/command-line-injection）
# ════════════════════════════════════════════════════════════

class TestCmdlineHardening:
    """argv[0] 只认白名单；实参过形状校验；长文本不进 argv。"""

    def test_程序名不在白名单直接拒(self):
        with pytest.raises(ValueError):
            intake._run(["powershell", "-Command", "whoami"], intake.ROOT)
        with pytest.raises(ValueError):
            intake._run(["cmd.exe"], intake.ROOT)
        with pytest.raises(ValueError):
            intake._run([], intake.ROOT)

    def test_白名单认全路径与大小写(self):
        assert intake._resolve_program("git") == "git"
        assert intake._resolve_program(r"C:\\Program Files\\Git\\bin\\git.exe") == "git"
        assert intake._resolve_program("/usr/bin/git") == "git"
        assert intake._resolve_program("PYTHON.EXE") == intake.sys.executable
        assert intake._resolve_program("curl") is None

    def test_实参形状校验(self):
        assert intake._sanitize_args(["status", "--porcelain"]) == ["status", "--porcelain"]
        with pytest.raises(ValueError):
            intake._sanitize_args(["a\x00b"])
        with pytest.raises(ValueError):
            intake._sanitize_args(["x" * (intake._MAX_ARG_LEN + 1)])
        with pytest.raises(ValueError):
            intake._sanitize_args(["x"] * (intake._MAX_ARGS + 1))
        with pytest.raises(ValueError):
            intake._sanitize_args([123])

    def test_提交说明走_stdin_不进_argv(self, tmp_path, monkeypatch):
        """open_pr 的 commit 必须用 `-F -` + stdin_text，而不是 `-m <msg>`。"""
        calls = []

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_run(cmd, cwd, **kw):
            calls.append((list(cmd), kw))
            if list(cmd)[:2] == ["git", "rev-parse"]:
                _R.stdout = "master\n"
            return _R()

        monkeypatch.setattr(intake, "_run", fake_run)
        monkeypatch.setattr(intake.shutil, "which", lambda name: "/usr/bin/gh")
        monkeypatch.setattr(intake, "changed_paths", lambda root=None: ["data/skills_repo/demo-skill/skill.md"])
        monkeypatch.setattr(intake, "collect_manifest", lambda: {"entries": []})
        monkeypatch.setattr(intake, "compute_baselines", lambda m: {
            "entries": 1, "skill_total": 1, "tool_total": 0, "local": 1, "remote": 0,
            "envelope_total": 1})
        monkeypatch.setattr(intake, "_gh_pr_create", lambda *a, **k: _R())

        out = intake.open_pr("demo-skill", root=tmp_path)
        assert out["ok"] is True, out

        commits = [c for c, _kw in calls if c[:2] == ["git", "commit"]]
        assert commits == [["git", "commit", "-F", "-"]], commits
        assert any(kw.get("stdin_text") for c, kw in calls if c[:2] == ["git", "commit"]), "提交说明必须经 stdin"
        assert not any("\n" in part for c, _kw in calls for part in c), "argv 里不该出现多行文本"

    def test_pr_正文走_body_file(self, tmp_path, monkeypatch):
        seen = {}

        class _R:
            returncode = 0
            stdout = "https://example.test/pr/1\n"
            stderr = ""

        def fake_run(cmd, cwd, **kw):
            seen["cmd"] = list(cmd)
            body_path = list(cmd)[list(cmd).index("--body-file") + 1]
            seen["body"] = open(body_path, encoding="utf-8").read()
            return _R()

        monkeypatch.setattr(intake, "_run", fake_run)
        intake._gh_pr_create("/usr/bin/gh", "master", "t", "正文 with 中文\n第二行", tmp_path)
        assert "--body" not in seen["cmd"] and "--body-file" in seen["cmd"]
        assert "第二行" in seen["body"]


class TestLlmSections:
    """P5：LLM 补两节只写 CARD.md，验不过就如实记 needs_human。"""

    def test_干跑只预览不写盘(self, tmp_path, monkeypatch):
        d = _write_skill(tmp_path, "demo-skill", GOOD_SKILL)
        monkeypatch.setattr(intake, "SKILLS_REPO", tmp_path)
        monkeypatch.setattr(intake, "is_tracked", lambda _d: True)
        monkeypatch.setattr(intake, "llm_card_preview", lambda _d: {
            "ok": True, "stats": {"total": 3, "verified": 2}, "card_md": "# 卡片"})
        out = intake.intake_one("demo-skill", execute=False, llm_sections=True)
        assert out["ok"] is True
        assert out["llm_card"]["ok"] is True
        assert out["llm_card"]["stats"] == {"total": 3, "verified": 2}
        assert not (d / "CARD.md").exists(), "干跑不得写 CARD.md"

    def test_验不过写进_needs_human(self, tmp_path, monkeypatch):
        _write_skill(tmp_path, "demo-skill", GOOD_SKILL)
        monkeypatch.setattr(intake, "SKILLS_REPO", tmp_path)
        monkeypatch.setattr(intake, "is_tracked", lambda _d: True)
        monkeypatch.setattr(intake, "llm_card_preview", lambda _d: {
            "ok": False, "reason": "没有一条能对上原文（严格引用校验未通过）"})
        out = intake.intake_one("demo-skill", execute=False, llm_sections=True)
        assert out["ok"] is True, "补不上不该阻断入轨"
        assert out["llm_card"]["ok"] is False
        assert any("严格引用校验" in h for h in out["needs_human"])

    def test_pending_report_带_llm_可用标记(self, monkeypatch):
        monkeypatch.setattr(intake, "SKILLS_REPO", __import__("pathlib").Path(
            "/nonexistent-skills-repo"))
        assert intake.pending_report() == []
        monkeypatch.setattr(intake, "_llm_available", lambda: True)
        items = [i for i in intake.pending_report()]
        assert items == []


class TestQuotedEmpty:
    """`author: ''` 要当成"空"，否则自动补全永远补不上（gh-cli 2026-11-02 实测）。"""

    def test_带引号的空值会被补(self, tmp_path, monkeypatch):
        text = GOOD_SKILL.replace("author: unknown", "author: ''")
        d = _write_skill(tmp_path, "demo-skill", text)
        monkeypatch.setattr(intake, "SKILLS_REPO", tmp_path)
        out = intake.fix_front_matter(d, execute=True)
        assert "author" in out["fixed"]
        body = (d / "skill.md").read_text(encoding="utf-8")
        assert "author: yunshu-intake" in body and "author: ''" not in body
