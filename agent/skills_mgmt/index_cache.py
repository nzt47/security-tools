"""技能索引缓存 — 预解析 front matter，启动时加载

背景:
    原 SkillFileStore.load_metadata_index() 每次冷启动都扫描 data/skills_repo/
    并解析全部 skill.md front matter，运行时重复开销。本模块把"解析结果"预解析
    为分块索引并持久化，服务启动时一次加载。

设计:
    - 内存缓存：skill_id -> metadata dict（仅 front matter，不缓存 body）
    - 持久化：data/skills_repo/.index/cache.json（cache_version 版本化）
    - 失效策略：skill.md 的 mtime 变化 → 失效；
               内容 hash 变化 → 失效（即使 mtime 未变，防文件被覆盖回去）；
               缓存文件损坏 → 全量重建

防御性要求:
    - 缓存加载/持久化失败 → 降级为运行时解析（不影响功能）
    - 并发安全：缓存读写加锁（RLock）
    - 索引失效时必须能回源重新解析（守【不易】）

【不易】SkillFileStore 对外接口不变；缓存可回源；不缓存 skill.md body
【变易】mtime + hash 双重失效；持久化格式版本化；挂载方式可选
【简易】一次解析，多次命中；增量校验只重解析变化的文件
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from .file_store import _META_FIELDS, SkillFileStore, SkillMDParser
from .observability import logger
from agent.logging_utils import log_dict

# ════════════════════════════════════════════════════════════
#  常量
# ════════════════════════════════════════════════════════════

_SKILL_MD = "skill.md"
_INDEX_DIR = ".index"
_CACHE_FILE = "cache.json"
_CACHE_VERSION = "1.0"


def _trace_id() -> str:
    return uuid.uuid4().hex[:16]


class SkillIndexCache:
    """技能索引缓存 — 预解析 front matter，启动时加载

    设计:
        - 内存缓存：skill_id -> metadata dict
        - 持久化：data/skills_repo/.index/cache.json
        - 失效策略：基于 skill.md 的 mtime + hash 校验
    """

    CACHE_VERSION = _CACHE_VERSION

    def __init__(self, file_store: SkillFileStore):
        self.fs = file_store
        self._cache: Dict[str, Dict[str, Any]] = {}
        # skill_id -> {mtime, hash}：mtime/hash 任一变化即视为缓存失效
        self._cache_meta: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._cache_path = Path(file_store.repo_path) / _INDEX_DIR / _CACHE_FILE
        # [变易] 挂载到 file_store — 写入/删除操作后同步失效（SkillFileStore 接口不变）
        # SkillFileStore.__init__ 已预置 self._index_cache = None，直接赋值即可
        self.fs._index_cache = self

    # ──────────────────────────────────────────────
    #  启动加载 / 全量重建 / 持久化
    # ──────────────────────────────────────────────

    def load_on_startup(self) -> None:
        """启动时加载缓存（优先读持久化文件，失败则全量重建）

        【不易】缓存文件损坏/版本不匹配 → 全量重建（索引失效必须回源）
        【变易】缓存文件缺失 → 懒加载（首个访问触发全量解析，启动零阻塞）
        【简易】加载后不立即校验，首个 get_all_metadata 做增量校验
        """
        t0 = time.time()
        tid = _trace_id()
        try:
            data = self._read_cache_file()
        except Exception as e:  # noqa: BLE001  缓存损坏 → 降级重建
            logger.warning(log_dict({'module_name': 'index_cache', 'action': 'load_on_startup.cache_corrupt', 'error': str(e)[:200], 'fallback': 'rebuild_from_source'}))
            self.rebuild()
            return

        if data is None:
            # 缓存文件缺失（首次运行/首次部署）→ 懒加载，首个访问触发解析
            logger.info(log_dict({'module_name': 'index_cache', 'action': 'load_on_startup.cache_missing', 'cache_path': str(self._cache_path), 'fallback': 'lazy_build_on_first_access'}))
            return

        if data.get("cache_version") != self.CACHE_VERSION:
            # 版本不匹配 → 全量重建
            logger.info(log_dict({'module_name': 'index_cache', 'action': 'load_on_startup.version_mismatch', 'cached_version': data.get('cache_version'), 'expected_version': self.CACHE_VERSION, 'fallback': 'rebuild_from_source'}))
            self.rebuild()
            return

        skills = data.get("skills", {})
        meta_info = data.get("meta", {})
        if not isinstance(skills, dict) or not isinstance(meta_info, dict):
            # 结构非法 → 全量重建
            logger.warning(log_dict({'module_name': 'index_cache', 'action': 'load_on_startup.invalid_structure', 'fallback': 'rebuild_from_source'}))
            self.rebuild()
            return

        with self._lock:
            # 仅保留合法条目（损坏的单项丢弃，由增量校验回源）
            self._cache = {
                sid: m for sid, m in skills.items()
                if isinstance(sid, str) and isinstance(m, dict)
            }
            self._cache_meta = {
                sid: info for sid, info in meta_info.items()
                if isinstance(sid, str) and isinstance(info, dict)
                and "mtime" in info and "hash" in info
            }

        elapsed = (time.time() - t0) * 1000
        logger.info(log_dict({'module_name': 'index_cache', 'action': 'load_on_startup.ok', 'skill_count': len(self._cache), 'cache_version': self.CACHE_VERSION}))

    def get_metadata(self, skill_id: str) -> Optional[Dict[str, Any]]:
        """获取技能元数据（命中缓存则返回，否则回源）

        命中判定：skill.md 的 mtime 与内容 hash 均未变。
        技能已删除 → 清理缓存并返回 None。
        """
        with self._lock:
            md_path = self._skill_md_path(skill_id)
            if not md_path.exists():
                self._cache.pop(skill_id, None)
                self._cache_meta.pop(skill_id, None)
                return None
            if self._entry_valid(skill_id, md_path):
                return self._cache.get(skill_id)
            # 缓存失效 → 回源重解析
            return self._parse_and_store(skill_id, md_path)

    def get_all_metadata(self, *, refresh: bool = False) -> Dict[str, Dict[str, Any]]:
        """获取全量元数据索引

        - refresh=True：强制全量重建（与 load_metadata_index(refresh=True) 语义一致）
        - 缓存为空：全量重建（首次访问，懒加载触发点）
        - 其余：增量校验（stat + hash），仅重解析变化的文件

        返回值始终是同一 dict 对象（未变化时），保证 loader 的
        倒排索引 id() 绑定有效（守 loader._get_inverted_index 契约）。
        """
        with self._lock:
            if refresh or not self._cache:
                self._rebuild_locked()
                changed = True
            else:
                changed = self._validate_all_locked()
        if changed:
            self.persist()
        return self._cache

    def invalidate(self, skill_id: str) -> None:
        """失效单个技能的缓存（技能更新时调用）

        【不易】仅内存状态变更，无 I/O、无外部回调（守持锁不 I/O 约束）
        【变易】由 SkillFileStore 写入/删除操作后调用（挂载时）
        """
        with self._lock:
            self._cache.pop(skill_id, None)
            self._cache_meta.pop(skill_id, None)

    def rebuild(self) -> None:
        """全量重建缓存（扫描仓库 + 解析全部 front matter）并持久化"""
        t0 = time.time()
        tid = _trace_id()
        with self._lock:
            self._rebuild_locked()
        self.persist()
        logger.info(log_dict({'module_name': 'index_cache', 'action': 'rebuild.ok', 'skill_count': len(self._cache)}))

    def persist(self) -> None:
        """持久化到磁盘（原子写入：临时文件 + replace）

        【防御】写失败不影响内存缓存（降级为运行时解析，不影响功能）
        【防御】只持久化 front matter 白名单字段，剔除运行时注入字段
               （_dir / skill_id / scripts 等，防止调用方污染进入磁盘缓存）
        """
        payload = None
        with self._lock:
            payload = {
                "cache_version": self.CACHE_VERSION,
                "skills": {
                    sid: self._sanitize_meta(m)
                    for sid, m in self._cache.items()
                },
                "meta": dict(self._cache_meta),
            }
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._cache_path.with_suffix(".json.tmp")
            tmp_path.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8",
            )
            tmp_path.replace(self._cache_path)
        except Exception as e:  # noqa: BLE001  持久化失败不影响内存缓存
            logger.warning(log_dict({'module_name': 'index_cache', 'action': 'persist.failed', 'cache_path': str(self._cache_path), 'error': str(e)[:200], 'fallback': 'runtime_parsing'}))

    # ──────────────────────────────────────────────
    #  内部：校验 / 解析 / 重建
    # ──────────────────────────────────────────────

    def _read_cache_file(self) -> Optional[Dict[str, Any]]:
        """读取持久化缓存文件；文件缺失返回 None，损坏抛异常（由调用方重建）"""
        if not self._cache_path.exists():
            return None
        with open(self._cache_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _skill_md_path(self, skill_id: str) -> Path:
        """获取 skill.md 路径（带路径越界防护，不校验 id 格式）

        越界（如 skill_id 含 ..）时返回仓库内必然不存在的路径，
        get_metadata 将按"技能不存在"处理并清理缓存。
        """
        repo_resolved = Path(self.fs.repo_path).resolve()
        candidate = (repo_resolved / skill_id / _SKILL_MD).resolve()
        try:
            candidate.relative_to(repo_resolved)
        except ValueError:
            return repo_resolved / _SKILL_MD
        return candidate

    def _entry_valid(self, skill_id: str, md_path: Path) -> bool:
        """校验缓存项是否有效：mtime 与内容 hash 均未变化

        【不易】hash 校验即使 mtime 未变也执行（防文件被覆盖回去）
        """
        info = self._cache_meta.get(skill_id)
        if info is None or skill_id not in self._cache:
            return False
        try:
            st = md_path.stat()
        except OSError:
            return False
        if st.st_mtime != info.get("mtime"):
            return False
        try:
            digest = hashlib.md5(md_path.read_bytes()).hexdigest()
        except OSError:
            return False
        return digest == info.get("hash")

    def _parse_and_store(self, skill_id: str, md_path: Path) -> Optional[Dict[str, Any]]:
        """解析单个 skill.md 的 front matter 并写入缓存；失败返回 None

        【不易】解析失败仅跳过该技能（与 load_metadata_index 语义一致），
                不抛异常、不阻断整体索引
        """
        try:
            content = md_path.read_text(encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            logger.warning(log_dict({'module_name': 'index_cache', 'action': 'parse.read_failed', 'skill_id': skill_id, 'error': str(e)[:200]}))
            return None
        try:
            meta, _body = SkillMDParser.parse(content)
        except Exception as e:  # noqa: BLE001
            logger.warning(log_dict({'module_name': 'index_cache', 'action': 'parse.skipped', 'skill_id': skill_id, 'error': str(e)[:200]}))
            return None
        if not meta.get("id"):
            meta["id"] = skill_id
        try:
            mtime = md_path.stat().st_mtime
        except OSError:
            mtime = 0.0
        # 【不易】hash 必须与 _entry_valid 同源（read_bytes 原始字节）：
        # read_text 在 Windows 上会做 universal newline 转换（\r\n→\n），
        # 若此处用 content.encode() 而校验用 read_bytes，两者永不相等 → 缓存永失效
        try:
            digest = hashlib.md5(md_path.read_bytes()).hexdigest()
        except OSError:
            digest = ""
        self._cache[skill_id] = meta
        self._cache_meta[skill_id] = {"mtime": mtime, "hash": digest}
        return meta

    def _rebuild_locked(self) -> None:
        """全量重建（调用方需持锁）：扫描仓库，解析全部 skill.md

        【简易】先清空再经 _parse_and_store 逐技能填充（与增量校验共用解析逻辑）
        """
        self._cache = {}
        self._cache_meta = {}
        try:
            entries = list(Path(self.fs.repo_path).iterdir())
        except OSError:
            entries = []
        for entry in entries:
            if not entry.is_dir() or entry.name.startswith(("_", ".")):
                continue
            md_path = entry / _SKILL_MD
            if not md_path.exists():
                continue
            self._parse_and_store(entry.name, md_path)

    def _validate_all_locked(self) -> bool:
        """增量校验（调用方需持锁）：返回是否有变化

        - 未变化技能：mtime + hash 校验通过 → 直接命中缓存
        - 变化/新增技能：回源重解析
        - 已删除技能：清理缓存条目
        - 有变化时替换为新 dict 对象，让 loader 的 id(index) 失效检测
          触发倒排索引重建（守 loader._get_inverted_index 契约）
        """
        changed = False
        try:
            entries = list(Path(self.fs.repo_path).iterdir())
        except OSError:
            return False
        current_ids = set()
        for entry in entries:
            if not entry.is_dir() or entry.name.startswith(("_", ".")):
                continue
            md_path = entry / _SKILL_MD
            if not md_path.exists():
                continue
            skill_id = entry.name
            current_ids.add(skill_id)
            if self._entry_valid(skill_id, md_path):
                continue
            # 缓存失效 → 回源重解析
            if self._parse_and_store(skill_id, md_path) is not None:
                changed = True
        # 已删除技能清理
        for skill_id in list(self._cache):
            if skill_id not in current_ids:
                self._cache.pop(skill_id, None)
                self._cache_meta.pop(skill_id, None)
                changed = True
        if changed:
            self._cache = dict(self._cache)
        return changed

    @staticmethod
    def _sanitize_meta(meta: Dict[str, Any]) -> Dict[str, Any]:
        """仅保留 front matter 白名单字段（剔除调用方运行时注入字段）"""
        return {k: v for k, v in meta.items() if k in _META_FIELDS or k == "id"}
