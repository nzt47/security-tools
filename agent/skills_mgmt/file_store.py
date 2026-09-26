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
import re
import shutil
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import yaml

from .observability import logger, emit_metric, traced_action
from agent.logging_utils import log_dict

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
    "config_schema", "output_schema",
    # [变易] 敏感技能隔离字段（须持久化，否则 skill.md 往返丢失标记）
    "is_sensitive", "isolation_strategy",
    # [G1-B0/S0 前置 2026-09-25] 中文展示文案（UI 用；`description` 保留英文供检索+模型）。
    # 【为什么必须在白名单里】不在 ⇒ `update_meta` 写它被**静默忽略**、`parse()` 也看不见，
    # 于是 G1 的「回填 description_zh」这一步在数据层根本落不了地（G1-B0 实测：文件 0 字节变化）。
    # 【与 F1b 的关系】F1b 之前 `update_meta` 会连白名单外字段一起静默删除 ⇒ 写了也会被下次启停抹掉；
    # F1b 之后 `patch_front_matter` 原样保留，故本行为该步骤的唯一前置（实测 monkeypatch 打通三件事：
    # parse 可见 / load_metadata_index 带出 / update_meta 可写）。
    # 【为什么 description 不改成中文】G1-B0 实测：技能侧「含典型触发句式」覆盖会从 17/23 掉到 13/23，
    # 因为 `Use when ...` 正是这批**英文**描述的触发句式载体 ⇒ 中文只能另存一列。
    "description_zh",
}


def _trace_id() -> str:
    return uuid.uuid4().hex[:16]


def _meta_value_equal(a: Any, b: Any) -> bool:
    """元数据值的宽松相等判定（供「值未变则不改写该行」使用）

    异构类型的 __eq__ 可能抛错，此时按「不相等」处理（宁可多写一行，不静默跳过）。
    """
    try:
        return bool(a == b)
    except Exception:  # noqa: BLE001
        return False


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
    def serialize(meta: Dict[str, Any], body: str = "",
                  *, only_meta_fields: bool = True) -> str:
        """序列化为 skill.md 文本

        Args:
            only_meta_fields: 【F1b-C】是否只写 `_META_FIELDS` 白名单内的键。
                - True（默认，历史契约）：白名单外的键**不写进文件**。适用于
                  "把既有 front matter 重新序列化"的场景（此时白名单是"允许保留什么"）。
                - False：原样写出 `meta` 的**全部**键。适用于 `create()` 的初始内容
                  —— 新建技能时不存在"文件现状"可裁剪，白名单外的键若也丢弃就是
                  **静默数据丢失**（F1b 修的是 update 侧，本参数修的是 create 侧）。
        """
        # 只写白名单字段（only_meta_fields=False 时按调用方给的原样写）
        filtered = ({k: v for k, v in meta.items() if k in _META_FIELDS}
                    if only_meta_fields else dict(meta))
        yaml_block = yaml.safe_dump(
            filtered, allow_unicode=True, default_flow_style=False,
            sort_keys=False,
        ).strip()
        parts = [_FRONT_MATTER_SEP, yaml_block, _FRONT_MATTER_SEP]
        if body:
            parts.append("")
            parts.append(body)
        return "\n".join(parts)

    # ──────────────────────────────────────────────
    #  最小侵入改写（update_meta 专用）
    # ──────────────────────────────────────────────

    # front matter 顶层键行：列 0 起的 `key:`。
    # 缩进行（块值/嵌套结构）与注释行不以 [A-Za-z0-9_] 开头 ⇒ 不会被误判为顶层键。
    _FM_KEY_RE = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_\-.]*)[ \t]*:")

    @classmethod
    def _find_fm_key(cls, fm_lines: List[str],
                     key: str) -> Optional[Tuple[int, int]]:
        """定位 front matter 中顶层键 key 占用的 [start, end) 行范围

        块值（嵌套 dict/list、块标量）的续行以空白缩进开头 ⇒ 一并纳入替换范围，
        否则替换后会残留孤儿续行（实测：多行标量的折行形如
        `description: 'a` + 空行 + `  b'`，空行也属于块内）。
        列 0 的下一键与注释行视为块外，因此它们不会被误删。

        Returns: (start, end)；键不存在时返回 None
        """
        for i, line in enumerate(fm_lines):
            m = cls._FM_KEY_RE.match(line)
            if not m or m.group(1) != key:
                continue
            j = i + 1
            while j < len(fm_lines):
                if fm_lines[j][:1] in (" ", "\t"):
                    j += 1
                    continue
                if not fm_lines[j].strip():
                    # 空行：仅当其后的第一个非空行是缩进续行时才算块内
                    k = j
                    while k < len(fm_lines) and not fm_lines[k].strip():
                        k += 1
                    if k < len(fm_lines) and fm_lines[k][:1] in (" ", "\t"):
                        j = k
                        continue
                break
            return i, j
        return None

    @classmethod
    def patch_front_matter(cls, content: str, patch: Dict[str, Any],
                           new_body: Optional[str] = None) -> str:
        """最小侵入式改写 skill.md —— 只重写被 patch 的顶层键所在行

        与 serialize() 的「整文件重排」不同，本方法逐字节保留未被 patch 的内容：
            - 白名单外的既有字段（如人工维护的 unknown_custom_field）：原样保留
            - YAML 注释：原样保留
            - 未被 patch 的键：行内容、键顺序、引号风格、`tags: [a, b, c]` 行内列表、
              缩进、行尾终止符（LF / CRLF）全部不变
            - 值语义未变的键（含 patch 里显式给出的同值）：整行不动 ⇒ 重复启停不产生噪声 diff

        白名单 _META_FIELDS 的语义在这里被明确拆成两件事：
            - 【允许改什么】只作用于 patch：patch 里白名单外的键一律忽略（与修复前一致）；
            - 【保留什么】以文件现状为准：不再按白名单裁剪既有内容。
        修复前 update_meta 走 serialize()，白名单同时被当成「保留什么」，
        于是白名单外的字段与全部注释被静默删除（审计 F1b）。

        Args:
            content: skill.md 原文
            patch: 待更新字段（仅 _META_FIELDS 内的键生效）
            new_body: 非 None 时替换 Markdown body

        Returns:
            改写后的 skill.md 文本；只要有实际改动，末尾保证以换行结尾（POSIX 文本约定）

        Raises:
            SkillFileError: front matter 未闭合 / YAML 非法（与 parse 一致）
        """
        lines = content.splitlines(keepends=True)
        eol = "\r\n" if lines[:1] and lines[0].endswith("\r\n") else "\n"

        has_fm = bool(lines) and lines[0].strip() == _FRONT_MATTER_SEP
        if has_fm:
            end_idx = None
            for i in range(1, len(lines)):
                if lines[i].strip() == _FRONT_MATTER_SEP:
                    end_idx = i
                    break
            if end_idx is None:
                from .exceptions import SkillFileError, ErrorCode
                raise SkillFileError(
                    "skill.md front matter 未闭合（缺少结束的 ---）",
                    code=ErrorCode.MD_NO_FRONTMATTER,
                )
            open_line: str = lines[0]
            fm: List[str] = lines[1:end_idx]
            close_line: str = lines[end_idx]
            tail: List[str] = lines[end_idx + 1:]
        else:
            # 无 front matter：正文整体保留，元数据部分新建（不 strip 正文，避免吞掉人工格式）
            open_line, fm, close_line, tail = "", [], "", list(lines)

        current = cls.parse(content)[0]  # 白名单过滤后的现值，用于「值未变则不动」

        chunks: List[Tuple[str, str]] = []
        for key, value in patch.items():
            if key not in _META_FIELDS:
                # 【允许改什么】的边界：白名单外的 patch 键不写进文件。
                # 注意这不等于删除：文件里既有的同名字段仍按现状保留。
                logger.debug(log_dict({'module_name': 'file_store', 'action': 'update_meta.ignored_key', 'key': str(key)[:64]}))
                continue
            if key in current and _meta_value_equal(current[key], value):
                continue  # 值语义未变 ⇒ 一个字节都不动
            yaml_text = yaml.safe_dump(
                {key: value}, allow_unicode=True, default_flow_style=False,
                sort_keys=False,
            ).strip()
            chunk = "".join(
                ln if ln.endswith(("\n", "\r")) else ln + eol
                for ln in yaml_text.splitlines(keepends=True)
            )
            if chunk:
                chunks.append((key, chunk))

        if not chunks and new_body is None:
            return content  # 无事可做：原样返回，不触碰文件

        for key, chunk in chunks:
            chunk_lines = chunk.splitlines(keepends=True)
            span = cls._find_fm_key(fm, key)
            if span is None:
                fm.extend(chunk_lines)  # 新键追加到 front matter 末尾（与 serialize 的键序一致）
            else:
                fm[span[0]:span[1]] = chunk_lines

        if new_body is not None:
            # 保留原 body 前的空行分隔，然后写入新正文
            leading: List[str] = []
            for ln in tail:
                if ln.strip():
                    break
                leading.append(ln)
            if not leading and has_fm:
                leading = [eol]
            text = new_body.rstrip("\r\n")
            tail = leading + ([text + eol] if text else [])

        if has_fm:
            result = "".join([open_line] + fm + [close_line] + tail)
        else:
            gap = [eol] if tail and tail[0].strip() else []
            result = "".join(
                [_FRONT_MATTER_SEP + eol] + fm + [_FRONT_MATTER_SEP + eol] + gap + tail
            )

        # 末尾换行：有实际改动时保证以换行结尾，避免 No newline at end of file 噪声
        if result and not result.endswith(("\n", "\r")):
            result += eol
        return result

    # ════════════════════════════════════════════════════════════
    #  agentskills.io 标准兼容(双向适配)
    # ════════════════════════════════════════════════════════════

    _AGENTSILLSIO_FIELD_MAP = {
        "id": "name",
        "name": "title",
        "description": "description",
        "version": "version",
        "author": "author",
        "tags": "tags",
    }

    _YUNSHU_RESERVED_FIELDS = (
        "category", "content_type", "default_params",
        "dependencies", "config_schema", "output_schema",
        "source", "source_url", "enabled", "status",
        # [变易] 敏感标记须在 agentskills.io 往返中保留（安全边界守不易）
        "is_sensitive", "isolation_strategy",
    )

    @classmethod
    def to_agentskills_io(cls, skill_data: Dict[str, Any],
                          *, body: str = "") -> str:
        """把云枢 Skill 字段序列化为 agentskills.io 标准 SKILL.md"""
        import enum

        def _coerce(o):
            if isinstance(o, enum.Enum):
                return o.value
            raise TypeError(f"不可序列化: {type(o).__name__}")

        try:
            clean = json.loads(json.dumps(
                skill_data, default=_coerce, ensure_ascii=False,
            ))
        except (TypeError, ValueError):
            clean = skill_data

        front: Dict[str, Any] = {}
        for yunshu_key, std_key in cls._AGENTSILLSIO_FIELD_MAP.items():
            val = clean.get(yunshu_key)
            if val not in (None, "", [], {}):
                front[std_key] = val

        if "license" not in front:
            front["license"] = "MIT"

        yunshu_extra: Dict[str, Any] = {}
        for key in cls._YUNSHU_RESERVED_FIELDS:
            val = clean.get(key)
            if val not in (None, "", [], {}):
                yunshu_extra[key] = val
        if yunshu_extra:
            front["_yunshu"] = yunshu_extra

        yaml_block = yaml.safe_dump(
            front, allow_unicode=True, default_flow_style=False,
            sort_keys=False,
        ).strip()
        parts = [_FRONT_MATTER_SEP, yaml_block, _FRONT_MATTER_SEP]

        instruction = body or clean.get("content", "")
        if instruction:
            parts.append("")
            parts.append(instruction)
        return "\n".join(parts)

    @classmethod
    def from_agentskills_io(cls, text: str) -> Dict[str, Any]:
        """解析 agentskills.io 标准 SKILL.md → 云枢 Skill 字段 dict"""
        if not text.strip():
            return {}

        lines = text.splitlines()
        if not lines or lines[0].strip() != _FRONT_MATTER_SEP:
            return {"content": text.strip()}

        end_idx = None
        for i in range(1, len(lines)):
            if lines[i].strip() == _FRONT_MATTER_SEP:
                end_idx = i
                break
        if end_idx is None:
            from .exceptions import SkillFileError, ErrorCode
            raise SkillFileError(
                "agentskills.io front matter 未闭合(缺少结束的 ---)",
                code=ErrorCode.MD_NO_FRONTMATTER,
            )

        yaml_block = "\n".join(lines[1:end_idx])
        body = "\n".join(lines[end_idx + 1:]).strip()

        try:
            std_meta = yaml.safe_load(yaml_block) or {}
            if not isinstance(std_meta, dict):
                from .exceptions import SkillFileError, ErrorCode
                raise SkillFileError(
                    f"agentskills.io front matter 根节点必须是对象, "
                    f"got {type(std_meta).__name__}",
                    code=ErrorCode.MD_YAML_ERROR,
                )
        except yaml.YAMLError as e:
            from .exceptions import SkillFileError, ErrorCode
            raise SkillFileError(
                f"agentskills.io YAML 解析失败: {e}",
                code=ErrorCode.MD_YAML_ERROR,
            ) from e

        yunshu_data: Dict[str, Any] = {}
        for yunshu_key, std_key in cls._AGENTSILLSIO_FIELD_MAP.items():
            val = std_meta.pop(std_key, None)
            if val is not None:
                yunshu_data[yunshu_key] = val

        yunshu_extra = std_meta.pop("_yunshu", {})
        if isinstance(yunshu_extra, dict):
            for key in cls._YUNSHU_RESERVED_FIELDS:
                if key in yunshu_extra:
                    yunshu_data[key] = yunshu_extra[key]

        if "license" in std_meta:
            logger.debug("[agentskills.io] 丢弃未映射字段 license=%s",
                         std_meta["license"])

        if body:
            yunshu_data["content"] = body

        yunshu_data.setdefault("category", "community")
        yunshu_data.setdefault("content_type", "markdown")
        yunshu_data.setdefault("version", "0.1.0")

        return yunshu_data


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
        # [变易] 写入钩子列表 — skill.md 变更时通知外部订阅者（如向量索引增量更新）
        # 守 project_memory 硬约束：钩子触发在锁外执行，避免回调阻塞拖垮写操作
        self._write_hooks: List[Callable[[str, str], None]] = []
        # [变易] 技能索引缓存（SkillIndexCache）— 由缓存构造时挂载（SkillFileStore 接口不变）
        self._index_cache: Optional[Any] = None
        self._ensure_repo()

    # ──────────────────────────────────────────────
    #  写入钩子（供向量索引等外部订阅者增量同步）
    # ──────────────────────────────────────────────

    def register_write_hook(self, callback: Callable[[str, str], None]) -> None:
        """注册写入钩子 — skill.md create/update/delete 后触发

        Args:
            callback: 签名 (skill_id: str, action: str) -> None
                      action ∈ {"create", "update", "delete"}

        【不易】钩子在写操作锁外触发，回调内禁止持锁（守 project_memory 硬约束）
        【简易】单个钩子失败不影响主流程和其他钩子（_notify_hooks 内 try/except）
        """
        self._write_hooks.append(callback)

    def _notify_hooks(self, skill_id: str, action: str) -> None:
        """锁外触发所有写入钩子（单个失败不阻断）

        【不易】必须在 with self._lock 块外调用 — 回调可能含 I/O（如向量编码），
                锁内触发会导致整个模块卡死（project_memory 教训）
        """
        for hook in list(self._write_hooks):
            try:
                hook(skill_id, action)
            except Exception as e:  # noqa: BLE001
                logger.warning(log_dict({'module_name': 'file_store', 'action': 'write_hook.failed', 'skill_id': skill_id, 'hook_action': action, 'error': str(e)[:200]}))

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

        # [变易] 技能索引缓存优先 — 命中缓存跳过全量扫描/解析（未挂载时走原逻辑）
        if self._index_cache is not None:
            index = self._index_cache.get_all_metadata(refresh=refresh)
            elapsed = (time.time() - t0) * 1000
            logger.info(log_dict({'module_name': 'file_store', 'action': 'load_metadata_index.cache', 'skill_count': len(index), 'cache_type': type(self._index_cache).__name__}))
            return index

        with self._lock:
            if self._meta_index is not None and not refresh:
                elapsed = (time.time() - t0) * 1000
                logger.info(log_dict({'module_name': 'file_store', 'action': 'load_metadata_index.cached', 'skill_count': len(self._meta_index)}))
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
                    logger.warning(log_dict({'module_name': 'file_store', 'action': 'load_metadata_index.skip', 'skill_dir': entry.name, 'error': str(e)}))

            self._meta_index = index
            elapsed = (time.time() - t0) * 1000
            logger.info(log_dict({'module_name': 'file_store', 'action': 'load_metadata_index.ok', 'skill_count': len(index)}))
            emit_metric("yunshu_skill_metadata_index_count",
                        value=len(index), kind="gauge",
                        labels={"success": "true"})
            return index

    def get_metadata(self, skill_id: str) -> Optional[Dict[str, Any]]:
        """获取单个技能的元数据（第一层，不读 body）"""
        # [变易] 挂载缓存时走单技能 mtime/hash 校验（失效即回源），未挂载走原逻辑
        if self._index_cache is not None:
            return self._index_cache.get_metadata(skill_id)
        index = self.load_metadata_index()
        return index.get(skill_id)

    def _invalidate_index_cache(self, skill_id: str) -> None:
        """写入/删除操作后同步失效技能索引缓存（锁外调用）

        【不易】仅内存状态变更 + 防御式兜底（缓存异常不影响写操作主流程）
        """
        cache = getattr(self, "_index_cache", None)
        if cache is None:
            return
        try:
            cache.invalidate(skill_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(log_dict({'module_name': 'file_store', 'action': 'index_cache.invalidate.failed', 'skill_id': skill_id, 'error': str(e)[:200]}))

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
            logger.info(log_dict({'module_name': 'file_store', 'action': 'load_instruction.ok', 'skill_id': skill_id, 'body_chars': len(body)}))
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
            # [变易] F1b-C①：初始内容**以调用方给出的 meta 为准**，不再按 _META_FIELDS 裁剪。
            # 修复前 serialize() 把白名单外的键静默丢弃，而 create() 是新文件**唯一**的
            # 内容来源（不像 update_meta 还有"文件现状"可回退）⇒ 传进来的字段直接消失。
            # 白名单在 create 侧不再兼任"保留什么"，只剩"允许改什么"（update 侧语义）。
            md_content = SkillMDParser.serialize(meta, instruction,
                                                 only_meta_fields=False)
            # [不易] F1b-C②：newline="" ⇒ 不做换行翻译，落盘字节 == serialize() 的文本。
            # 修复前 write_text 默认 newline=None，Windows 会把 \n 翻成 \r\n ⇒ 同一个
            # create() 在 Windows / Linux 产出**不同字节**（.index/cache.json 存的是原始
            # 字节 md5 ⇒ 平台间 hash 不通用）。现统一为 LF —— 与 git 仓库里 skill.md 的
            # blob 形态一致（core.autocrlf=true 下工作区的 CRLF 只是 checkout 产物）。
            with (skill_dir / _SKILL_MD).open("w", encoding="utf-8",
                                              newline="") as _fp:
                _fp.write(md_content)
            _outside = sorted(k for k in meta if k not in _META_FIELDS)
            if _outside:
                logger.info(log_dict({'module_name': 'file_store',
                                      'action': 'create.meta_outside_whitelist',
                                      'skill_id': skill_id, 'keys': _outside}))

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
            logger.info(log_dict({'module_name': 'file_store', 'action': 'create.ok', 'skill_id': skill_id, 'scripts': list(scripts.keys()) if scripts else [], 'temp_files': list(temp_files.keys()) if temp_files else []}))
            created_skill_dir = skill_dir

        # [变易] 锁外触发写入钩子 — 守 project_memory 硬约束（锁内禁外部回调）
        # 让向量索引等订阅者增量 upsert 新技能
        self._notify_hooks(skill_id, "create")
        # [变易] 同步失效技能索引缓存（下一次访问时回源重解析）
        self._invalidate_index_cache(skill_id)
        return created_skill_dir

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
        """更新技能元数据和使用说明

        签名与返回语义不变（返回 None；技能不存在 → SkillNotFoundError；
        front matter 非法 → SkillFileError，异常语义与修复前一致）。

        [变易] 修复 F1b 的静默数据丢失：改写走 SkillMDParser.patch_front_matter()
        的**最小侵入**路径 —— 只重写被 patch 的顶层键所在行，文件其余部分
        （白名单外的既有字段、YAML 注释、引号风格、`tags: [...]` 行内列表、键顺序、
        行尾终止符、末尾换行）逐字节保留。
        修复前这里是 parse → serialize 的整文件重排：_META_FIELDS 白名单被同时
        当成「保留什么」，导致白名单外字段与全部注释被静默删除，
        且每次技能启停都产生格式噪声 diff（审计 F1b）。

        说明：文件按原文的行尾终止符（LF/CRLF）写回，不做换行翻译
        （Path.write_text 在 Windows 上会把 \n 翻成 \r\n）。
        """
        with self._lock:
            # 技能存在性 + front matter 可解析性校验（异常语义与修复前一致）
            self._read_md(skill_id)
            md_path = self._skill_dir(skill_id) / _SKILL_MD
            # newline="" ⇒ 不做换行翻译：CRLF 原样读入，才能在写回时逐字节保留
            with md_path.open("r", encoding="utf-8", newline="") as fp:
                original = fp.read()
            md_content = SkillMDParser.patch_front_matter(
                original, patch, new_instruction)
            with md_path.open("w", encoding="utf-8", newline="") as fp:
                fp.write(md_content)
            self._meta_index = None

        # [变易] 锁外触发写入钩子 — 守 project_memory 硬约束（锁内禁外部回调）
        # 让向量索引订阅者感知 meta/body 变更并 upsert
        self._notify_hooks(skill_id, "update")
        # [变易] 同步失效技能索引缓存（下一次访问时回源重解析）
        self._invalidate_index_cache(skill_id)

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
            logger.info(log_dict({'module_name': 'file_store', 'action': 'delete.ok', 'skill_id': skill_id}))

        # [变易] 锁外触发写入钩子 — 守 project_memory 硬约束（锁内禁外部回调）
        # 让向量索引订阅者感知删除事件并清理对应向量
        self._notify_hooks(skill_id, "delete")
        # [变易] 同步失效技能索引缓存（下一次访问时回源重解析）
        self._invalidate_index_cache(skill_id)
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
