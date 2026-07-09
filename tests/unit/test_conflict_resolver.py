"""ConflictResolver 单元测试 — skill.md 冲突自动解决

覆盖维度:
- 冲突标记解析: _extract_conflict_sides
- 字段级合并: version/tags/无冲突/单侧/不可合并
- detect/categorize/auto_resolve/resolve_all
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.skills_mgmt.conflict_resolver import ConflictResolver
from agent.skills_mgmt.git_sync import (
    ConflictFile,
    GitSync,
    SyncResult,
)


# ═══════════════════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def mock_git_sync(tmp_path):
    """Mock GitSync, repo_path 指向 tmp_path"""
    git = MagicMock(spec=GitSync)
    git.repo_path = tmp_path
    git.add = MagicMock()
    return git


@pytest.fixture
def resolver(mock_git_sync):
    return ConflictResolver(mock_git_sync)


def _make_conflict(path="skill.md"):
    return ConflictFile(
        path=path,
        skill_id=path.split("/")[0] if "/" in path else path,
        conflict_type="both_modified",
        resolution=None,
    )


# ═══════════════════════════════════════════════════════════════════
#  1. 冲突标记解析
# ═══════════════════════════════════════════════════════════════════

class TestExtractConflictSides:
    """测试 _extract_conflict_sides"""

    def test_basic_conflict_markers(self):
        content = "line1\n<<<<<<< HEAD\nours line\n=======\ntheirs line\n>>>>>>> branch\nline2\n"
        ours, theirs = ConflictResolver._extract_conflict_sides(content)
        assert ours is not None
        assert theirs is not None
        assert "ours line" in ours
        assert "theirs line" in theirs
        assert "line1" in ours
        assert "line1" in theirs
        assert "line2" in ours
        assert "line2" in theirs

    def test_no_conflict_markers(self):
        ours, theirs = ConflictResolver._extract_conflict_sides("no conflict here")
        assert ours is None
        assert theirs is None

    def test_multiple_conflict_blocks(self):
        content = (
            "line1\n"
            "<<<<<<< HEAD\nours1\n=======\ntheirs1\n>>>>>>> b1\n"
            "shared\n"
            "<<<<<<< HEAD\nours2\n=======\ntheirs2\n>>>>>>> b2\n"
        )
        ours, theirs = ConflictResolver._extract_conflict_sides(content)
        assert "ours1" in ours and "ours2" in ours
        assert "theirs1" in theirs and "theirs2" in theirs
        assert "shared" in ours and "shared" in theirs

    def test_empty_conflict_block(self):
        content = "<<<<<<< HEAD\n=======\ntheirs only\n>>>>>>> branch\n"
        ours, theirs = ConflictResolver._extract_conflict_sides(content)
        assert ours is not None
        assert "theirs only" in theirs


# ═══════════════════════════════════════════════════════════════════
#  2. 字段级合并
# ═══════════════════════════════════════════════════════════════════

class TestMergeFrontMatter:
    """测试 _merge_front_matter"""

    def test_no_conflict_same_values(self, resolver):
        ours = {"id": "test", "name": "Test", "version": "1.0.0"}
        theirs = {"id": "test", "name": "Test", "version": "1.0.0"}
        merged, unresolvable = resolver._merge_front_matter(ours, theirs)
        assert merged == ours
        assert not unresolvable

    def test_single_side_field_from_theirs(self, resolver):
        ours = {"id": "test"}
        theirs = {"id": "test", "author": "bob"}
        merged, unresolvable = resolver._merge_front_matter(ours, theirs)
        assert merged["author"] == "bob"
        assert not unresolvable

    def test_single_side_field_from_ours(self, resolver):
        ours = {"id": "test", "description": "desc"}
        theirs = {"id": "test"}
        merged, unresolvable = resolver._merge_front_matter(ours, theirs)
        assert merged["description"] == "desc"
        assert not unresolvable

    def test_version_takes_higher(self, resolver):
        ours = {"version": "1.2.0"}
        theirs = {"version": "1.1.0"}
        merged, _ = resolver._merge_front_matter(ours, theirs)
        assert merged["version"] == "1.2.0"

    def test_version_takes_higher_reverse(self, resolver):
        ours = {"version": "1.0.0"}
        theirs = {"version": "2.0.0"}
        merged, _ = resolver._merge_front_matter(ours, theirs)
        assert merged["version"] == "2.0.0"

    def test_tags_merged_and_deduped(self, resolver):
        ours = {"tags": ["pdf", "parse"]}
        theirs = {"tags": ["pdf", "ocr"]}
        merged, _ = resolver._merge_front_matter(ours, theirs)
        assert "pdf" in merged["tags"]
        assert "parse" in merged["tags"]
        assert "ocr" in merged["tags"]
        assert len(merged["tags"]) == 3

    def test_unresolvable_name_field(self, resolver):
        ours = {"name": "PDF解析"}
        theirs = {"name": "PDF解析器"}
        _, unresolvable = resolver._merge_front_matter(ours, theirs)
        assert unresolvable

    def test_mixed_resolvable_and_unresolvable(self, resolver):
        ours = {"version": "1.2.0", "name": "PDF解析"}
        theirs = {"version": "1.1.0", "name": "PDF解析器", "author": "bob"}
        merged, unresolvable = resolver._merge_front_matter(ours, theirs)
        assert unresolvable
        assert merged["version"] == "1.2.0"
        assert merged["author"] == "bob"

    def test_empty_dicts(self, resolver):
        merged, unresolvable = resolver._merge_front_matter({}, {})
        assert merged == {}
        assert not unresolvable


class TestHigherVersion:
    """测试 _higher_version"""

    def test_patch_difference(self):
        assert ConflictResolver._higher_version("1.0.0", "1.0.1") == "1.0.1"

    def test_minor_difference(self):
        assert ConflictResolver._higher_version("1.0.0", "1.1.0") == "1.1.0"

    def test_major_difference(self):
        assert ConflictResolver._higher_version("2.0.0", "1.9.9") == "2.0.0"

    def test_equal_versions(self):
        assert ConflictResolver._higher_version("1.0.0", "1.0.0") == "1.0.0"

    def test_different_length(self):
        assert ConflictResolver._higher_version("1.0", "1.0.1") == "1.0.1"

    def test_invalid_version(self):
        result = ConflictResolver._higher_version("invalid", "1.0.0")
        assert result == "1.0.0"


# ═══════════════════════════════════════════════════════════════════
#  3. detect 方法
# ═══════════════════════════════════════════════════════════════════

class TestDetect:
    """测试 detect 方法"""

    def test_detect_returns_conflicts_from_sync_result(self, resolver):
        sync_result = SyncResult(
            success=False, action="pull", branch="main",
            commits=[], changed_files=[],
            conflicts=[
                ConflictFile(path="skill.md", skill_id="test",
                             conflict_type="both_modified", resolution=None),
                ConflictFile(path="scripts/main.py", skill_id="test",
                             conflict_type="both_modified", resolution=None),
            ],
            error=None,
        )
        conflicts = resolver.detect(sync_result)
        assert len(conflicts) == 2
        assert conflicts[0].path == "skill.md"

    def test_detect_empty_conflicts(self, resolver):
        sync_result = SyncResult(
            success=True, action="pull", branch="main",
            commits=[], changed_files=[], conflicts=[], error=None,
        )
        assert resolver.detect(sync_result) == []

    def test_detect_returns_copy(self, resolver):
        sync_result = SyncResult(
            success=False, action="pull", branch="main",
            commits=[], changed_files=[],
            conflicts=[ConflictFile(path="x.md", skill_id="x",
                                    conflict_type="both_modified", resolution=None)],
            error=None,
        )
        result = resolver.detect(sync_result)
        result.clear()
        assert len(sync_result.conflicts) == 1


# ═══════════════════════════════════════════════════════════════════
#  4. categorize 方法
# ═══════════════════════════════════════════════════════════════════

class TestCategorize:
    """测试 categorize 方法"""

    def _setup_status_mock(self, mock_git_sync, status_output):
        result = MagicMock()
        result.stdout = status_output
        mock_git_sync._run_git.return_value = result

    def test_content_conflict_uu(self, mock_git_sync, resolver):
        self._setup_status_mock(mock_git_sync, "UU skill.md\n")
        assert resolver.categorize(_make_conflict("skill.md")) == "content_conflict"

    def test_add_add_aa(self, mock_git_sync, resolver):
        self._setup_status_mock(mock_git_sync, "AA new_skill/skill.md\n")
        assert resolver.categorize(_make_conflict("new_skill/skill.md")) == "add_add"

    def test_modify_delete_au(self, mock_git_sync, resolver):
        self._setup_status_mock(mock_git_sync, "AU skill.md\n")
        assert resolver.categorize(_make_conflict("skill.md")) == "modify_delete"

    def test_default_content_conflict(self, mock_git_sync, resolver):
        self._setup_status_mock(mock_git_sync, "")
        assert resolver.categorize(_make_conflict("skill.md")) == "content_conflict"


# ═══════════════════════════════════════════════════════════════════
#  5. auto_resolve 方法
# ═══════════════════════════════════════════════════════════════════

class TestAutoResolve:
    """测试 auto_resolve 方法"""

    def test_auto_resolve_version_tags_success(self, mock_git_sync, resolver, tmp_path):
        """version 和 tags 冲突可自动合并"""
        conflict_file = tmp_path / "skill.md"
        conflict_file.write_text(
            "---\n"
            "id: pdf_parser\n"
            "<<<<<<< HEAD\n"
            "version: 1.2.0\n"
            "tags: [pdf, parse]\n"
            "=======\n"
            "version: 1.1.0\n"
            "author: bob\n"
            "tags: [pdf, ocr]\n"
            ">>>>>>> branch\n"
            "enabled: true\n"
            "---\n\n"
            "# PDF Parser\n",
            encoding="utf-8",
        )
        conflict = _make_conflict("skill.md")
        assert resolver.auto_resolve(conflict) is True

        merged = conflict_file.read_text(encoding="utf-8")
        assert "1.2.0" in merged
        assert "bob" in merged
        assert "ocr" in merged
        assert "parse" in merged
        mock_git_sync.add.assert_called_once_with(paths=["skill.md"])

    def test_auto_resolve_name_conflict_fails(self, mock_git_sync, resolver, tmp_path):
        """name 字段冲突不可自动合并"""
        conflict_file = tmp_path / "skill.md"
        conflict_file.write_text(
            "---\n"
            "id: test\n"
            "<<<<<<< HEAD\n"
            "name: PDF解析\n"
            "=======\n"
            "name: PDF解析器\n"
            ">>>>>>> branch\n"
            "---\n",
            encoding="utf-8",
        )
        conflict = _make_conflict("skill.md")
        assert resolver.auto_resolve(conflict) is False
        mock_git_sync.add.assert_not_called()

    def test_auto_resolve_no_markers_returns_true(self, mock_git_sync, resolver, tmp_path):
        """文件无冲突标记视为已解决"""
        conflict_file = tmp_path / "skill.md"
        conflict_file.write_text("---\nid: test\n---\n", encoding="utf-8")
        conflict = _make_conflict("skill.md")
        assert resolver.auto_resolve(conflict) is True

    def test_auto_resolve_file_not_exists(self, mock_git_sync, resolver, tmp_path):
        """文件不存在返回 False"""
        conflict = _make_conflict("nonexistent.md")
        assert resolver.auto_resolve(conflict) is False

    def test_auto_resolve_body_takes_longer(self, mock_git_sync, resolver, tmp_path):
        """body 取较长侧"""
        conflict_file = tmp_path / "skill.md"
        conflict_file.write_text(
            "---\n"
            "id: test\n"
            "version: 1.0.0\n"
            "---\n\n"
            "<<<<<<< HEAD\n"
            "# 短\n"
            "=======\n"
            "# 这是一个较长的标题\n"
            "## 详细说明\n"
            ">>>>>>> branch\n",
            encoding="utf-8",
        )
        conflict = _make_conflict("skill.md")
        assert resolver.auto_resolve(conflict) is True

        merged = conflict_file.read_text(encoding="utf-8")
        assert "较长的标题" in merged
        assert "详细说明" in merged


class TestResolveAll:
    """测试 resolve_all 方法"""

    def test_resolve_all_mixed(self, mock_git_sync, resolver, tmp_path):
        """部分可合并，部分不可"""
        # 可合并的文件
        file1 = tmp_path / "a.md"
        file1.write_text(
            "---\nid: a\n<<<<<<< HEAD\nversion: 1.1.0\n=======\nversion: 1.0.0\n>>>>>>> b\n---\n",
            encoding="utf-8",
        )
        # 不可合并的文件
        file2 = tmp_path / "b.md"
        file2.write_text(
            "---\nid: b\n<<<<<<< HEAD\nname: A\n=======\nname: B\n>>>>>>> b\n---\n",
            encoding="utf-8",
        )

        conflicts = [_make_conflict("a.md"), _make_conflict("b.md")]
        resolved, unresolved = resolver.resolve_all(conflicts)
        assert len(resolved) == 1
        assert len(unresolved) == 1
        assert resolved[0].path == "a.md"
        assert unresolved[0].path == "b.md"
