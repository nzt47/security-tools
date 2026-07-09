"""ConflictResolver — skill.md 冲突自动解决器

设计原则:
    - 不易: 复用 SkillMDParser 解析/序列化, 不破坏 skill.md 格式契约
    - 变易: 字段级合并策略可扩展 (_merge_field 分派)
    - 简易: 仅处理 skill.md; body 取较长侧, 不做行级合并

合并规则:
    - 无冲突字段: 直接取值
    - 单侧存在字段: 取存在侧
    - 双方不同字段:
        version: 取较高 semver
        tags: 合并去重
        其他: 标记需人工, auto_resolve 返回 False
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .file_store import SkillMDParser
from .git_sync import ConflictFile, GitSync, GitSyncError, SyncResult

__all__ = ["ConflictResolver"]

logger = logging.getLogger(__name__)


class ConflictResolver:
    """skill.md 冲突解决器

    组合 GitSync 使用，不继承。通过 GitSync 的 repo_path 读写文件，
    通过 _run_git 调用 git status 获取冲突分类。
    """

    def __init__(self, git_sync: GitSync):
        self._git = git_sync

    # ──────────────────────────────────────────────
    #  公开 API
    # ──────────────────────────────────────────────

    def detect(self, sync_result: SyncResult) -> List[ConflictFile]:
        """从 SyncResult 提取冲突文件列表"""
        return list(sync_result.conflicts)

    def categorize(self, conflict: ConflictFile) -> str:
        """分类冲突类型

        通过 git status --porcelain 的 XY 状态码判断:
            UU: content_conflict (双方都修改了同一文件同一区域)
            AA: add_add (双方新增同名文件)
            AU/UA/DU/UD: modify_delete (一方修改一方删除)
        """
        result = self._git._run_git("status", "--porcelain")
        target = conflict.path
        for line in result.stdout.splitlines():
            if len(line) < 3:
                continue
            status_code = line[:2]
            file_path = line[3:].strip().strip('"')
            if file_path == target:
                if status_code == "UU":
                    return "content_conflict"
                if status_code == "AA":
                    return "add_add"
                if status_code in ("AU", "UA", "DU", "UD"):
                    return "modify_delete"
                return "content_conflict"
        return "content_conflict"

    def auto_resolve(self, conflict: ConflictFile) -> bool:
        """尝试自动解决冲突

        流程:
            1. 读取冲突文件内容
            2. 提取 ours/theirs 两侧 (解析 <<<<<<< ======= >>>>>>> 标记)
            3. 分别解析 front matter
            4. 字段级合并 front matter
            5. body 取较长侧
            6. 若全部可合并 → 写回 + git add → True
            7. 否则 → False (需人工介入)
        """
        file_path = self._git.repo_path / conflict.path
        if not file_path.exists():
            logger.warning("冲突文件不存在: %s", conflict.path)
            return False

        content = file_path.read_text(encoding="utf-8")
        ours, theirs = self._extract_conflict_sides(content)

        if ours is None or theirs is None:
            logger.info("无冲突标记，可能已解决: %s", conflict.path)
            return True

        ours_meta, ours_body = SkillMDParser.parse(ours)
        theirs_meta, theirs_body = SkillMDParser.parse(theirs)

        merged_meta, has_unresolvable = self._merge_front_matter(ours_meta, theirs_meta)
        if has_unresolvable:
            logger.info("存在无法自动合并的字段: %s", conflict.path)
            return False

        merged_body = ours_body if len(ours_body) >= len(theirs_body) else theirs_body
        merged_content = SkillMDParser.serialize(merged_meta, merged_body)

        file_path.write_text(merged_content, encoding="utf-8")
        self._git.add(paths=[conflict.path])
        logger.info("冲突自动解决成功: %s", conflict.path)
        return True

    def resolve_all(self, conflicts: List[ConflictFile]) -> Tuple[List[ConflictFile], List[ConflictFile]]:
        """批量自动解决冲突

        Returns: (resolved, unresolved)
        """
        resolved: List[ConflictFile] = []
        unresolved: List[ConflictFile] = []
        for conflict in conflicts:
            if self.auto_resolve(conflict):
                resolved.append(conflict)
            else:
                unresolved.append(conflict)
        return resolved, unresolved

    # ──────────────────────────────────────────────
    #  冲突标记解析
    # ──────────────────────────────────────────────

    @staticmethod
    def _extract_conflict_sides(content: str) -> Tuple[Optional[str], Optional[str]]:
        """从冲突标记中提取 ours 和 theirs 完整内容

        冲突标记格式:
            <<<<<<< HEAD
            (ours content)
            =======
            (theirs content)
            >>>>>>> branch_name

        非冲突区域的内容同时出现在 ours 和 theirs 中。
        """
        if "<<<<<<<" not in content:
            return None, None

        ours_lines: List[str] = []
        theirs_lines: List[str] = []
        state = "normal"

        for line in content.splitlines(keepends=True):
            if line.startswith("<<<<<<<"):
                state = "ours"
            elif line.startswith("=======") and state == "ours":
                state = "theirs"
            elif line.startswith(">>>>>>>") and state == "theirs":
                state = "normal"
            else:
                if state == "normal":
                    ours_lines.append(line)
                    theirs_lines.append(line)
                elif state == "ours":
                    ours_lines.append(line)
                elif state == "theirs":
                    theirs_lines.append(line)

        return "".join(ours_lines), "".join(theirs_lines)

    # ──────────────────────────────────────────────
    #  字段级合并
    # ──────────────────────────────────────────────

    def _merge_front_matter(
        self, ours: Dict[str, Any], theirs: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], bool]:
        """字段级合并 front matter

        Returns: (merged_meta, has_unresolvable)
        """
        merged: Dict[str, Any] = {}
        all_keys = set(ours.keys()) | set(theirs.keys())
        has_unresolvable = False

        for key in all_keys:
            ours_val = ours.get(key)
            theirs_val = theirs.get(key)

            if ours_val == theirs_val:
                if ours_val is not None:
                    merged[key] = ours_val
            elif ours_val is None:
                merged[key] = theirs_val
            elif theirs_val is None:
                merged[key] = ours_val
            else:
                resolved = self._merge_field(key, ours_val, theirs_val)
                if resolved is not None:
                    merged[key] = resolved
                else:
                    has_unresolvable = True
                    merged[key] = ours_val

        return merged, has_unresolvable

    def _merge_field(self, key: str, ours: Any, theirs: Any) -> Optional[Any]:
        """尝试自动合并单个字段

        version: 取较高 semver
        tags: 合并去重
        其他: None (无法自动合并)
        """
        if key == "version" and isinstance(ours, str) and isinstance(theirs, str):
            return self._higher_version(ours, theirs)
        if key == "tags" and isinstance(ours, list) and isinstance(theirs, list):
            merged = list(ours)
            for item in theirs:
                if item not in merged:
                    merged.append(item)
            return merged
        return None

    @staticmethod
    def _higher_version(v1: str, v2: str) -> str:
        """返回较高的 semver 版本"""
        def parse(v: str) -> List[int]:
            try:
                return [int(p) for p in v.split(".")]
            except (ValueError, AttributeError):
                return [0]

        p1 = parse(v1)
        p2 = parse(v2)
        max_len = max(len(p1), len(p2))
        p1 += [0] * (max_len - len(p1))
        p2 += [0] * (max_len - len(p2))
        return v1 if p1 >= p2 else v2
