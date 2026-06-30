"""文件系统存储层 — 每个技能一个目录（三层架构物理基础）

目录结构:
    data/skills_repo/
    ├── my_skill/
    │   ├── skill.md          # YAML front matter(元数据·第一层) + Markdown body(使用说明·第二层)
    │   ├── scripts/          # 执行脚本(工具资源层·第三层)
    │   │   └── main.py
    │   └── temp/             # 业务模板
    └── another_skill/
        ├── skill.md
        ├── scripts/
        └── temp/

skill.md 格式:
    ---
    id: my-skill
    name: 我的技能
    description: 简短描述（约 100 token，第一层匹配用）
    category: custom
    tags: [pdf, parse]
    version: 1.0.0
    enabled: true
    status: approved
    author: yunshu
    content_type: markdown
    ---

    # 使用说明（第二层，按需加载）

    ## 参数
    - file_path: 文件路径

    ## 示例
    ...

设计原则:
    - 三层分离: 元数据(front matter) / 使用说明(body) / 脚本(scripts/) 物理分离
    - 按需加载: 第一层只读 front matter，第二层才读 body，第三层才执行脚本
    - 可观测: 所有操作输出结构化日志 (trace_id, module_name, action, duration_ms)
    - 边界显性化: 文件损坏/权限/路径越界 → 抛出带业务码的 Error
    - 与现有 JSON 存储互操作: 支持 from_legacy_skill() 迁移
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from .observability import logger, emit_metric, traced_action

# ════════════════════════════════════════════════════════════
#  常量
# ════════════════════════════════════════════════════════════

_DEFAULT_REPO_PATH = Path(__file__).parent.parent.parent / "data" / "skills_repo"
_SKILL_MD = "skill.md"
_SCRIPTS_DIR = "scripts"
_TEMP_DIR = "temp"
_FRONT_MATTER_SEP = "---"

# 允许出现在 front matter 中的字段（白名单）
_META_FIELDS = {
    "id", "name", "description", "category", "tags", "version",
    "enabled", "status", "author", "source", "source_url",
    "content_type", "default_params", "dependencies",
}


def _trace_id() -> str:
    return uuid.uuid4().hex[:16]


# ════════════════════════════════════════════════════════════
#  skill.md 解析 / 序列化
# ════════════════════════════════════════════════════════════

class SkillMDParser:
    """解析 / 序列化 skill.md 文件（YAML front matter + Markdown body）

    第一层（元数据）和第二层（使用说明）物理分离：
        - front matter → 元数据字典（约 100 token）
        - body → 使用说明字符串（按需加载）
    """

    @staticmethod
    def parse(content: str) -> Tuple[Dict[str, Any], str]:
        """解析 skill.md 内容 → (元数据字典, 使用说明 body)

        边界显性化:
            - 缺少 front matter → SkillFileError(MD_NO_FRONTMATTER)
            - YAML 解析失败 → SkillFileError(MD_YAML_ERROR)
        """
        if not content.strip():
            return {}, ""

        # 必须以 --- 开头才认为是合法 front matter
        lines = content.splitlines()
        if not lines or lines[0].strip() != _FRONT_MATTER_SEP:
            # 无 front matter，整体视为 body
            return {}, content.strip()

        # 找结束的 ---
        end_idx = None
        for i in range(1, len(lines)):
            if lines[i].strip() == _FRONT_MATTER_SEP:
                end_idx = i
                break

        if end_idx is None:
            from .exceptions import SkillFileError, ErrorCode
            raise SkillFileError(
                f"skill.md front matter 未闭合（缺少结束的 ---）",
                code=ErrorCode.MD_NO_FRONTMATTER,
            )

        yaml_block = "\n".join(lines[1:end_idx])
        body = "\n".join(lines[end_idx + 1:]).strip()

        try:
            meta = yaml.safe_load(yaml_block) or {}
            if not isinstance(meta, dict):
                from .exceptions import SkillFileError, ErrorCode
                raise SkillFileError(
                f"front matter 根节点必须是对象，got {type(meta).__name__}",
                code=ErrorCode.MD_YAML_ERROR,
            )
            # 过滤白名单字段
            meta = {k: v for k, v in meta.items() if k in _META_FIELDS}
            return meta, body
        except yaml.YAMLError as e:
            from .exceptions import SkillFileError, ErrorCode
            raise SkillFileError(
                f"YAML 解析失败: {e}",
                code=ErrorCode.MD_YAML_ERROR,
            )

    @staticmethod
    def serialize(meta: Dict[str, Any], body: str = "") -> str:
        """序列化为 skill.md 文本"""
        # 只写白名单字段
        filtered = {k: v for k, v in meta.items() if k in _META_FIELDS}
        yaml_block = yaml.safe_dump(
            filtered, allow_unicode=True, default_flow_style=False,
            sort_keys=False,
        ).strip()
        parts = [_FRONT_MATTER_SEP, yaml_block, _FRONT_MATTER_SEP]
        if body:
            parts.append("")
            parts.append(body)
        return "\n".join(parts)


# ════════════════════════════════════════════════════════════
#  文件系统存储
# ════════════════════════════════════════════════════════════

class SkillFileStore:
    """技能文件系统存储 — 每个技能一个目录

    三层物理分离:
        - 第一层（元数据）: skill.md 的 front matter
        - 第二层（使用说明）: skill.md 的 body
        - 第三层（工具资源）: scripts/ 目录

    线程安全: 使用 RLock 保护写操作。
    """

    def __init__(self, repo_path: Optional[str] = None):
        self._repo = Path(repo_path) if repo_path else _DEFAULT_REPO_PATH
        self._lock = threading.RLock()
        self._meta_index: Optional[Dict[str, Dict[str, Any]]] = None
        self._ensure_repo()

    # ──────────────────────────────────────────────
    #  仓库管理
    # ──────────────────────────────────────────────

    def _ensure_repo(self) -> None:
        """确保仓库目录存在"""
        self._repo.mkdir(parents=True, exist_ok=True)

    @property
    def repo_path(self) -> Path:
        return self._repo

    def _skill_dir(self, skill_id: str) -> Path:
        """获取技能目录路径（带路径越界检查）"""
        self._validate_skill_id(skill_id)
        # resolve() 防止路径穿越攻击
        skill_dir = (self._repo / skill_id).resolve()
        try:
            skill_dir.relative_to(self._repo.resolve())
        except ValueError:
            from .exceptions import SkillFileError, ErrorCode
            raise SkillFileError(
                f"路径越界: {skill_id}",
                code=ErrorCode.PATH_TRAVERSAL,
            )
        return skill_dir

    @staticmethod
    def _validate_skill_id(skill_id: str) -> None:
        import re
        if not re.match(r"^[a-z0-9][a-z0-9_\-]*$", skill_id):
            from .exceptions import SkillValidationError, ErrorCode
            raise SkillValidationError(
                f"技能ID必须为 kebab_case: {skill_id}",
                code=ErrorCode.INVALID_SKILL_ID,
            )

    # ──────────────────────────────────────────────
    #  第一层：元数据（front matter）
    # ──────────────────────────────────────────────

    def load_metadata_index(self, *, refresh: bool = False) -> Dict[str, Dict[str, Any]]:
        """扫描所有技能目录，构建元数据索引（第一层）

        只读取 skill.md 的 front matter，不读 body — 约 100 token/技能。
        结果缓存在内存，refresh=True 强制刷新。

        Returns: {skill_id: {name, description, category, tags, ...}}
        """
        t0 = time.time()
        tid = _trace_id()

        with self._lock:
            if self._meta_index is not None and not refresh:
                elapsed = (time.time() - t0) * 1000
                logger.info(json.dumps({
                    "trace_id": tid, "module_name": "file_store",
                    "action": "load_metadata_index.cached",
                    "duration_ms": round(elapsed, 2),
                    "skill_count": len(self._meta_index),
                }, ensure_ascii=False))
                return self._meta_index

            index: Dict[str, Dict[str, Any]] = {}
            for entry in self._repo.iterdir():
                if not entry.is_dir() or entry.name.startswith("_") or entry.name.startswith("."):
                    continue
                md_path = entry / _SKILL_MD
                if not md_path.exists():
                    continue
                try:
                    content = md_path.read_text(encoding="utf-8")
                    meta, _body = SkillMDParser.parse(content)
                    if "id" not in meta:
                        meta["id"] = entry.name
                    meta["_dir"] = str(entry)
                    index[meta["id"]] = meta
                except Exception as e:
                    # 边界显性化：单个技能解析失败不影响整体索引
                    logger.warning(json.dumps({
                        "trace_id": tid, "module_name": "file_store",
                        "action": "load_metadata_index.skip",
                        "skill_dir": entry.name,
                        "error": str(e),
                    }, ensure_ascii=False))

            self._meta_index = index
            elapsed = (time.time() - t0) * 1000
            logger.info(json.dumps({
                "trace_id": tid, "module_name": "file_store",
                "action": "load_metadata_index.ok",
                "duration_ms": round(elapsed, 2),
                "skill_count": len(index),
            }, ensure_ascii=False))
            emit_metric("yunshu_skill_metadata_index_count",
                        value=len(index), kind="gauge",
                        labels={"success": "true"})
            return index

    def get_metadata(self, skill_id: str) -> Optional[Dict[str, Any]]:
        """获取单个技能的元数据（第一层，不读 body）"""
        index = self.load_metadata_index()
        return index.get(skill_id)

    # ──────────────────────────────────────────────
    #  第二层：使用说明（skill.md body）
    # ──────────────────────────────────────────────

    def load_instruction(self, skill_id: str) -> str:
        """按需加载技能的完整使用说明（第二层）

        只在第一层匹配到技能后才调用。
        Returns: Markdown body 文本
        """
        t0 = time.time()
        tid = _trace_id()
        skill_dir = self._skill_dir(skill_id)
        md_path = skill_dir / _SKILL_MD

        if not md_path.exists():
            from .exceptions import SkillNotFoundError
            raise SkillNotFoundError(skill_id)

        try:
            content = md_path.read_text(encoding="utf-8")
            _meta, body = SkillMDParser.parse(content)
            elapsed = (time.time() - t0) * 1000
            logger.info(json.dumps({
                "trace_id": tid, "module_name": "file_store",
                "action": "load_instruction.ok",
                "duration_ms": round(elapsed, 2),
                "skill_id": skill_id,
                "body_chars": len(body),
            }, ensure_ascii=False))
            return body
        except (SkillNotFoundError,):
            raise
        except Exception as e:
            from .exceptions import SkillFileError, ErrorCode
            raise SkillFileError(
                f"读取使用说明失败 [{skill_id}]: {e}",
                code=ErrorCode.MD_READ_ERROR,
            )

    # ──────────────────────────────────────────────
    #  第三层：工具资源（scripts/ + temp/）
    # ──────────────────────────────────────────────

    def list_scripts(self, skill_id: str) -> List[str]:
        """列出技能的所有脚本文件名（第三层）"""
        skill_dir = self._skill_dir(skill_id)
        scripts_dir = skill_dir / _SCRIPTS_DIR
        if not scripts_dir.exists():
            return []
        return [f.name for f in scripts_dir.iterdir()
                if f.is_file() and f.suffix == ".py"]

    def get_script_path(self, skill_id: str, script_name: str) -> Path:
        """获取脚本完整路径（带安全检查）"""
        self._validate_script_name(script_name)
        skill_dir = self._skill_dir(skill_id)
        script_path = (skill_dir / _SCRIPTS_DIR / script_name).resolve()
        # 路径越界检查
        try:
            script_path.relative_to(skill_dir.resolve())
        except ValueError:
            from .exceptions import SkillFileError, ErrorCode
            raise SkillFileError(
                f"脚本路径越界: {script_name}",
                code=ErrorCode.PATH_TRAVERSAL,
            )
        if not script_path.exists():
            from .exceptions import SkillNotFoundError
            raise SkillNotFoundError(f"{skill_id}/scripts/{script_name}")
        return script_path

    def list_temp_files(self, skill_id: str) -> List[str]:
        """列出技能的业务模板文件"""
        skill_dir = self._skill_dir(skill_id)
        temp_dir = skill_dir / _TEMP_DIR
        if not temp_dir.exists():
            return []
        return [f.name for f in temp_dir.iterdir() if f.is_file()]

    def get_temp_path(self, skill_id: str, filename: str) -> Path:
        """获取业务模板文件路径"""
        # 防止路径穿越
        if "/" in filename or "\\" in filename or ".." in filename:
            from .exceptions import SkillFileError, ErrorCode
            raise SkillFileError(
                f"非法文件名: {filename}",
                code=ErrorCode.PATH_TRAVERSAL,
            )
        skill_dir = self._skill_dir(skill_id)
        temp_path = skill_dir / _TEMP_DIR / filename
        if not temp_path.exists():
            from .exceptions import SkillNotFoundError
            raise SkillNotFoundError(f"{skill_id}/temp/{filename}")
        return temp_path

    @staticmethod
    def _validate_script_name(name: str) -> None:
        import re
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*\.py$", name):
            from .exceptions import SkillValidationError, ErrorCode
            raise SkillValidationError(
                f"脚本名必须为合法 Python 文件名: {name}",
                code=ErrorCode.INVALID_SCRIPT_NAME,
            )

    # ──────────────────────────────────────────────
    #  CRUD：创建 / 读取 / 更新 / 删除
    # ──────────────────────────────────────────────

    def create(self, skill_id: str, meta: Dict[str, Any],
               instruction: str = "",
               scripts: Optional[Dict[str, str]] = None,
               temp_files: Optional[Dict[str, bytes]] = None) -> Path:
        """创建技能目录结构

        Args:
            skill_id: 技能ID
            meta: 元数据字典（写入 front matter）
            instruction: 使用说明（写入 body）
            scripts: {filename: content} 脚本文件
            temp_files: {filename: bytes} 模板文件

        Returns: 技能目录路径
        """
        t0 = time.time()
        tid = _trace_id()

        with self._lock:
            skill_dir = self._skill_dir(skill_id)
            if skill_dir.exists():
                from .exceptions import SkillAlreadyExistsError
                raise SkillAlreadyExistsError(skill_id)

            skill_dir.mkdir(parents=True)
            (skill_dir / _SCRIPTS_DIR).mkdir()
            (skill_dir / _TEMP_DIR).mkdir()

            # 写 skill.md
            meta = {**meta, "id": skill_id}
            md_content = SkillMDParser.serialize(meta, instruction)
            (skill_dir / _SKILL_MD).write_text(md_content, encoding="utf-8")

            # 写脚本
            if scripts:
                for fname, code in scripts.items():
                    self._validate_script_name(fname)
                    (skill_dir / _SCRIPTS_DIR / fname).write_text(
                        code, encoding="utf-8")

            # 写模板
            if temp_files:
                for fname, data in temp_files.items():
                    if "/" in fname or "\\" in fname or ".." in fname:
                        continue
                    (skill_dir / _TEMP_DIR / fname).write_bytes(data)

            self._meta_index = None  # 失效缓存

            elapsed = (time.time() - t0) * 1000
            logger.info(json.dumps({
                "trace_id": tid, "module_name": "file_store",
                "action": "create.ok",
                "duration_ms": round(elapsed, 2),
                "skill_id": skill_id,
                "scripts": list(scripts.keys()) if scripts else [],
                "temp_files": list(temp_files.keys()) if temp_files else [],
            }, ensure_ascii=False))
            return skill_dir

    def read(self, skill_id: str) -> Tuple[Dict[str, Any], str,
                                            List[str], List[str]]:
        """读取技能完整信息（三层全部加载）

        Returns: (元数据, 使用说明, 脚本列表, 模板列表)
        """
        skill_dir = self._skill_dir(skill_id)
        if not skill_dir.exists():
            from .exceptions import SkillNotFoundError
            raise SkillNotFoundError(skill_id)

        md_path = skill_dir / _SKILL_MD
        content = md_path.read_text(encoding="utf-8")
        meta, body = SkillMDParser.parse(content)
        meta["id"] = skill_id

        scripts = self.list_scripts(skill_id)
        temp_files = self.list_temp_files(skill_id)
        return meta, body, scripts, temp_files

    def update_meta(self, skill_id: str, patch: Dict[str, Any],
                    new_instruction: Optional[str] = None) -> None:
        """更新技能元数据和使用说明"""
        with self._lock:
            meta, body = self._read_md(skill_id)
            meta.update({k: v for k, v in patch.items() if k in _META_FIELDS})
            if new_instruction is not None:
                body = new_instruction
            md_content = SkillMDParser.serialize(meta, body)
            skill_dir = self._skill_dir(skill_id)
            (skill_dir / _SKILL_MD).write_text(md_content, encoding="utf-8")
            self._meta_index = None

    def add_script(self, skill_id: str, filename: str, code: str) -> None:
        """添加/更新脚本"""
        with self._lock:
            self._validate_script_name(filename)
            skill_dir = self._skill_dir(skill_id)
            scripts_dir = skill_dir / _SCRIPTS_DIR
            scripts_dir.mkdir(exist_ok=True)
            (scripts_dir / filename).write_text(code, encoding="utf-8")

    def add_temp_file(self, skill_id: str, filename: str, data: bytes) -> None:
        """添加/更新业务模板"""
        with self._lock:
            if "/" in filename or "\\" in filename or ".." in filename:
                from .exceptions import SkillValidationError, ErrorCode
                raise SkillValidationError(
                f"非法文件名: {filename}",
                code=ErrorCode.INVALID_FILENAME,
            )
            skill_dir = self._skill_dir(skill_id)
            temp_dir = skill_dir / _TEMP_DIR
            temp_dir.mkdir(exist_ok=True)
            (temp_dir / filename).write_bytes(data)

    def delete(self, skill_id: str) -> bool:
        """删除技能目录（连同所有脚本和模板）"""
        t0 = time.time()
        tid = _trace_id()
        with self._lock:
            skill_dir = self._skill_dir(skill_id)
            if not skill_dir.exists():
                return False
            shutil.rmtree(skill_dir)
            self._meta_index = None
            elapsed = (time.time() - t0) * 1000
            logger.info(json.dumps({
                "trace_id": tid, "module_name": "file_store",
                "action": "delete.ok",
                "duration_ms": round(elapsed, 2),
                "skill_id": skill_id,
            }, ensure_ascii=False))
            return True

    # ──────────────────────────────────────────────
    #  与现有 Skill 模型互操作
    # ──────────────────────────────────────────────

    def from_legacy_skill(self, skill_meta: Dict[str, Any],
                          content: str = "") -> Path:
        """从现有 JSON 存储的 Skill 字典迁移到文件系统

        Args:
            skill_meta: Skill.model_dump() 的字典
            content: 技能主体内容（作为使用说明 body）
        """
        skill_id = skill_meta.get("id", "")
        if not skill_id:
            from .exceptions import SkillValidationError, ErrorCode
            raise SkillValidationError(
                "迁移失败: 缺少 id 字段",
                code=ErrorCode.INVALID_SKILL_ID,
            )

        # 如果目录已存在，先备份
        skill_dir = self._skill_dir(skill_id)
        if skill_dir.exists():
            backup = skill_dir.with_suffix(".bak")
            if backup.exists():
                shutil.rmtree(backup)
            shutil.move(str(skill_dir), str(backup))

        # 构建元数据
        meta = {
            "id": skill_id,
            "name": skill_meta.get("name", skill_id),
            "description": skill_meta.get("description", ""),
            "category": skill_meta.get("category", "custom"),
            "tags": skill_meta.get("tags", []),
            "version": skill_meta.get("version", "0.1.0"),
            "enabled": skill_meta.get("enabled", True),
            "status": skill_meta.get("status", "draft"),
            "author": skill_meta.get("author", "unknown"),
            "source": skill_meta.get("source", "manual"),
            "content_type": skill_meta.get("content_type", "markdown"),
        }
        if skill_meta.get("default_params"):
            meta["default_params"] = skill_meta["default_params"]
        if skill_meta.get("dependencies"):
            meta["dependencies"] = skill_meta["dependencies"]

        return self.create(skill_id, meta, instruction=content or "")

    # ──────────────────────────────────────────────
    #  健康检查
    # ──────────────────────────────────────────────

    def health(self) -> Dict[str, Any]:
        """健康检查（供 /api/skills-mgmt/health 调用）"""
        try:
            index = self.load_metadata_index()
            writable = os.access(self._repo, os.W_OK)
            total_scripts = 0
            for skill_id in index:
                total_scripts += len(self.list_scripts(skill_id))
            return {
                "ok": True,
                "repo_path": str(self._repo),
                "skill_count": len(index),
                "total_scripts": total_scripts,
                "writable": bool(writable),
                "layer": "file_system",
            }
        except Exception as e:
            return {"ok": False, "error": str(e), "layer": "file_system"}

    # ──────────────────────────────────────────────
    #  内部
    # ──────────────────────────────────────────────

    def _read_md(self, skill_id: str) -> Tuple[Dict[str, Any], str]:
        """读取 skill.md → (元数据, body)"""
        skill_dir = self._skill_dir(skill_id)
        md_path = skill_dir / _SKILL_MD
        if not md_path.exists():
            from .exceptions import SkillNotFoundError
            raise SkillNotFoundError(skill_id)
        content = md_path.read_text(encoding="utf-8")
        meta, body = SkillMDParser.parse(content)
        meta["id"] = skill_id
        return meta, body
