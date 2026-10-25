# -*- coding: utf-8 -*-
"""技能双轨对账脚本（交接单 §8 陷阱 26）。

陷阱 26 的形态：主轨（data/skills_mgmt.json）里某条技能被运行期清理掉，
文件轨（data/skills_repo/<id>/skill.md）还在 ⇒ 孤儿 ⇒ H-3 用例转红。
本脚本让这件事**在部署后就能只读体检**，而不是等到用例红才被发现。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import check_skills_dual_track as checker  # noqa: E402


def _skill(root: Path, sid: str, *, with_md: bool = True) -> None:
    d = root / sid
    d.mkdir(parents=True, exist_ok=True)
    if with_md:
        (d / "skill.md").write_text(f"---\nid: {sid}\n---\n", encoding="utf-8")


def test_file_track_skips_dot_dirs_and_missing_md(tmp_path):
    _skill(tmp_path, "alpha")
    _skill(tmp_path, "beta", with_md=False)
    (tmp_path / ".index").mkdir()
    (tmp_path / ".index" / "skill.md").write_text("x", encoding="utf-8")
    track = checker.load_file_track(tmp_path)
    assert set(track) == {"alpha"}, "点目录（.index/.migration）与缺 skill.md 的目录都不算条目"


def test_main_track_supports_mapping_and_list(tmp_path):
    mapping = tmp_path / "m.json"
    mapping.write_text(json.dumps({"a": {"id": "a"}, "b": {"id": "b"}}), encoding="utf-8")
    assert set(checker.load_main_track(mapping)) == {"a", "b"}

    listing = tmp_path / "l.json"
    listing.write_text(json.dumps([{"id": "c"}, {"nope": 1}]), encoding="utf-8")
    assert set(checker.load_main_track(listing)) == {"c"}

    broken = tmp_path / "bad.json"
    broken.write_text("{not json", encoding="utf-8")
    assert checker.load_main_track(broken) == {}, "损坏的主轨按空处理（不抛）"


def test_orphans_and_main_only():
    assert checker.find_orphans({"a": "", "b": ""}, {"a": {}}) == ["b"]
    assert checker.find_main_only({"a": ""}, {"a": {}, "z": {}}) == ["z"]


def test_cli_exit_codes(tmp_path, capsys):
    repo = tmp_path / "skills_repo"
    _skill(repo, "alpha")
    _skill(repo, "orphan")
    main = tmp_path / "skills_mgmt.json"
    main.write_text(json.dumps({"alpha": {"id": "alpha"}}), encoding="utf-8")

    rc = checker.main(["--skills-repo", str(repo), "--main-track", str(main), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["orphans"] == ["orphan"] and out["ok"] is False

    main.write_text(json.dumps({"alpha": {"id": "alpha"}, "orphan": {"id": "orphan"}}),
                    encoding="utf-8")
    rc = checker.main(["--skills-repo", str(repo), "--main-track", str(main)])
    assert rc == 0
