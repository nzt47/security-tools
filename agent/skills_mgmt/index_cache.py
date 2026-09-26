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
    - **主轨补位**（【C1 修(5)】）：注册表并集 = 主轨 data/skills_mgmt.json ∪ 文件轨
      data/skills_repo/*/skill.md，而本缓存过去只扫文件轨 ⇒ 主轨独有的 7 个技能在
      Layer-1 **结构上不可召回**，且向量腿也不会编码它们（审计 Q3 §3.3）。
      现改为：主轨条目以 "main_track" 独立分区持久化，且**只补位、不覆盖**文件轨
      已有 id（现有 23 条的 Layer-1 语义零变化）。

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
import os
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

# ── 【C1 修(5)】主轨（data/skills_mgmt.json）补位 ──────────────────────────────
# 主轨与文件轨仓库同处 <root>/data/ ⇒ 取 SkillFileStore.repo_path 的父目录。
# 非标准布局可用 SKILLS_INDEX_MAIN_TRACK_PATH 显式指定；SKILLS_INDEX_MAIN_TRACK=0
# 可整体关闭补位（回滚开关）。
_MAIN_TRACK_FILE = "skills_mgmt.json"
_MAIN_TRACK_PATH_ENV = "SKILLS_INDEX_MAIN_TRACK_PATH"
_MAIN_TRACK_ENABLED_ENV = "SKILLS_INDEX_MAIN_TRACK"


def _trace_id() -> str:
    return uuid.uuid4().hex[:16]


def _env_flag(name: str, default: bool = True) -> bool:
    """读取布尔环境变量：0/false/no/off 视为 False，其余非空值视为 True"""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


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
        # ── 【C1 修(5)】主轨补位状态 ──
        # _main_cache: 主轨全部 id -> metadata（合并视图里文件轨优先覆盖重合 id）
        # _main_meta: 主轨文件级 {mtime, hash, path, count}（失效判据）
        # _merged: 文件轨 ∪ 主轨补位 的合并视图（get_all_metadata 的返回值，
        #          未变化时保持同一对象，守 loader 倒排索引的 id() 契约）
        self._main_track_enabled = _env_flag(_MAIN_TRACK_ENABLED_ENV, default=True)
        self._main_track_path = self._resolve_main_track_path(file_store)
        self._main_cache: Dict[str, Dict[str, Any]] = {}
        self._main_meta: Dict[str, Any] = {}
        self._merged: Dict[str, Dict[str, Any]] = {}
        # 合并视图脏标记：只有内容真的变化时才替换 _merged 对象（守 id() 契约）
        self._merged_dirty = True
        # [变易] 挂载到 file_store — 写入/删除操作后同步失效（SkillFileStore 接口不变）
        # SkillFileStore.__init__ 已预置 self._index_cache = None，直接赋值即可
        self.fs._index_cache = self

    @staticmethod
    def _resolve_main_track_path(file_store: SkillFileStore) -> Optional[Path]:
        """解析主轨文件路径：env 显式指定 > repo_path 的父目录/skills_mgmt.json

        【不易】只做路径推导，不读文件、不抛异常（文件不存在由刷新逻辑按"无主轨"处理）
        """
        explicit = os.environ.get(_MAIN_TRACK_PATH_ENV)
        if explicit and str(explicit).strip():
            return Path(str(explicit).strip())
        try:
            return Path(file_store.repo_path).resolve().parent / _MAIN_TRACK_FILE
        except Exception:  # noqa: BLE001
            return None

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

        main_track = data.get("main_track", {})
        main_track_meta = data.get("main_track_meta", {})

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
            # 【C1 修(5)】主轨补位条目：老缓存文件没有 main_track/main_track_meta 两键
            # ⇒ 视为"主轨尚未索引"，首个 get_all_metadata 的增量校验会刷新并 persist
            # （升级路径，不改 cache_version，旧读者读新文件也只忽略新键）
            self._main_cache = {
                sid: self._main_record_from_cache(sid, m)
                for sid, m in (main_track or {}).items()
                if isinstance(sid, str) and isinstance(m, dict)
            } if isinstance(main_track, dict) else {}
            self._main_meta = dict(main_track_meta) if isinstance(main_track_meta, dict) else {}
            self._merged_dirty = True
            self._rebuild_merged_locked()

        elapsed = (time.time() - t0) * 1000
        logger.info(log_dict({'module_name': 'index_cache', 'action': 'load_on_startup.ok', 'skill_count': len(self._cache), 'main_track_count': len(self._main_cache), 'cache_version': self.CACHE_VERSION}))

    def get_metadata(self, skill_id: str) -> Optional[Dict[str, Any]]:
        """获取技能元数据（命中缓存则返回，否则回源）

        命中判定：skill.md 的 mtime 与内容 hash 均未变。
        文件轨没有该技能 → 回主轨补位（【C1 修(5)】主轨独有技能以前直接返回 None）。
        两轨都没有 → 清理缓存并返回 None。
        """
        with self._lock:
            md_path = self._skill_md_path(skill_id)
            if md_path.exists():
                if self._entry_valid(skill_id, md_path):
                    return self._cache.get(skill_id)
                # 缓存失效 → 回源重解析
                return self._parse_and_store(skill_id, md_path)
            # 文件轨不存在 → 主轨补位（懒刷新：首次访问时按 mtime+hash 判定是否需读盘）
            if self._main_track_enabled and not self._main_meta:
                self._refresh_main_track_locked()
            main_meta = self._main_cache.get(skill_id)
            if main_meta is not None:
                return main_meta
            self._cache.pop(skill_id, None)
            self._cache_meta.pop(skill_id, None)
            return None

    def get_all_metadata(self, *, refresh: bool = False) -> Dict[str, Dict[str, Any]]:
        """获取全量元数据索引（文件轨 ∪ 主轨补位）

        - refresh=True：强制全量重建（与 load_metadata_index(refresh=True) 语义一致）
        - 缓存为空：全量重建（首次访问，懒加载触发点）
        - 其余：增量校验（stat + hash），仅重解析变化的文件
        - **主轨补位**（【C1 修(5)】）：主轨独有 id 一并进入返回值；文件轨已有 id 时
          以文件轨为准（不覆盖 ⇒ 现有 Layer-1 语义零变化）

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
        return self._merged

    def get_main_track_metadata(self) -> Dict[str, Dict[str, Any]]:
        """主轨条目（只读副本，含与文件轨重合的 id）

        【接线状态 · 实测 2026-09-25】**生产调用方 = 0**（全仓仅测试引用）。
        它**不是**召回路径：真正可召回的是 get_all_metadata() 的合并视图
        （重合 id 以**文件轨**为准），且该视图只在**挂了本缓存**的 SkillFileStore 上
        生效 —— 裸 SkillFileStore（= SkillLoader() 默认形态）看不到主轨独有技能，
        见 scripts/verify_index_drift.py 的 S2 pathA 判据与报告 C1.md §13.4。
        本方法仅供巡检/运维核对使用，**请勿据此判断"已可召回"**。
        """
        with self._lock:
            if self._main_track_enabled and not self._main_meta:
                self._refresh_main_track_locked()
            return dict(self._main_cache)

    def invalidate(self, skill_id: str) -> None:
        """失效单个技能的缓存（技能更新时调用）

        【不易】仅内存状态变更，无 I/O、无外部回调（守持锁不 I/O 约束）
        【变易】由 SkillFileStore 写入/删除操作后调用（挂载时）
        """
        with self._lock:
            self._cache.pop(skill_id, None)
            self._cache_meta.pop(skill_id, None)
            # 合并视图里可能仍留有旧值 → 置脏，下次 get_all_metadata 重建
            self._merged_dirty = True

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
                # 【C1 修(5)】主轨补位条目**独立分区**（不混进 skills）：
                # ① S1 巡检门只校验 skills vs 文件轨，混入会把主轨独有技能误报成"多余"；
                # ② 旧版读者读到多余键只会忽略，向后兼容。
                "main_track": {
                    sid: self._sanitize_meta(m)
                    for sid, m in self._main_cache.items()
                },
                "main_track_meta": dict(self._main_meta),
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
        self._merged_dirty = True
        return meta

    def _rebuild_locked(self) -> None:
        """全量重建（调用方需持锁）：扫描仓库，解析全部 skill.md

        【简易】先清空再经 _parse_and_store 逐技能填充（与增量校验共用解析逻辑）
        【C1 修(5)】随后强制刷新主轨补位并重建合并视图
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
        self._refresh_main_track_locked(force=True)
        self._rebuild_merged_locked()

    # ──────────────────────────────────────────────
    #  主轨补位（【C1 修(5)】）
    # ──────────────────────────────────────────────

    @staticmethod
    def _main_record_to_meta(sid: str, record: Dict[str, Any]) -> Dict[str, Any]:
        """主轨记录 → 与文件轨 front matter 同构的元数据 dict

        【不易】只取 _META_FIELDS 白名单字段 —— 主轨记录里的 content / versions /
                metrics / review 等大字段一律不进索引（否则 cache.json 会膨胀数十倍）
        【变易】tags 主轨存 list；为字符串（历史脏数据）时按逗号切分
        """
        meta: Dict[str, Any] = {"id": sid}
        for key in _META_FIELDS:
            if key == "id":
                continue
            if key in record:
                meta[key] = record[key]
        meta["id"] = sid
        if not meta.get("name"):
            meta["name"] = sid
        tags = meta.get("tags")
        if isinstance(tags, str):
            meta["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
        elif not isinstance(tags, list):
            meta["tags"] = []
        meta.setdefault("description", "")
        meta.setdefault("category", "")
        meta.setdefault("version", "")
        meta.setdefault("enabled", True)
        # _track 是**内存标记**（不在 _META_FIELDS 内）⇒ persist 时被 _sanitize_meta 剔除，
        # 载入时由 _main_record_from_cache 重新贴回
        meta["_track"] = "main"
        return meta

    @staticmethod
    def _main_record_from_cache(sid: str, meta: Dict[str, Any]) -> Dict[str, Any]:
        """从持久化缓存恢复主轨条目（补回 _track 标记）"""
        out = dict(meta)
        out["id"] = out.get("id") or sid
        out["_track"] = "main"
        return out

    def _refresh_main_track_locked(self, *, force: bool = False) -> bool:
        """刷新主轨补位条目（调用方需持锁）；返回是否发生变化

        【不易】只**补位**：文件轨已有的 id 不覆盖 ⇒ 现有 23 条的 Layer-1 语义零变化
                （"主轨优先"的收敛属于 G1「描述唯一事实源」，不在本卡范围）
        【变易】失效判据 = 主轨文件的 mtime + md5（与文件轨同一套双校验思路）
        【简易】主轨不存在/不可读/结构非法 ⇒ 视作"无主轨"，清空补位，不影响文件轨
        """
        if not self._main_track_enabled:
            if self._main_cache or self._main_meta:
                logger.info(log_dict({'module_name': 'index_cache', 'action': 'main_track.disabled', 'dropped': len(self._main_cache)}))
                self._main_cache, self._main_meta = {}, {}
                self._merged_dirty = True
                return True
            return False

        path = self._main_track_path
        try:
            exists = path is not None and Path(path).exists()
        except OSError:
            exists = False
        if not exists:
            if self._main_cache or self._main_meta:
                logger.warning(log_dict({'module_name': 'index_cache', 'action': 'main_track.missing', 'path': str(path), 'dropped': len(self._main_cache)}))
                self._main_cache, self._main_meta = {}, {}
                self._merged_dirty = True
                return True
            return False

        try:
            stat = Path(path).stat()
            mtime = stat.st_mtime
            digest = hashlib.md5(Path(path).read_bytes()).hexdigest()
        except OSError as e:
            logger.warning(log_dict({'module_name': 'index_cache', 'action': 'main_track.stat_failed', 'path': str(path), 'error': str(e)[:200]}))
            return False

        if not force and self._main_meta.get("mtime") == mtime \
                and self._main_meta.get("hash") == digest:
            return False

        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.warning(log_dict({'module_name': 'index_cache', 'action': 'main_track.parse_failed', 'path': str(path), 'error': str(e)[:200]}))
            return False
        if not isinstance(data, dict):
            logger.warning(log_dict({'module_name': 'index_cache', 'action': 'main_track.invalid_structure', 'path': str(path), 'type': type(data).__name__}))
            return False

        entries: Dict[str, Dict[str, Any]] = {}
        for key, record in data.items():
            if not isinstance(record, dict):
                continue
            sid = str(record.get("id") or key)
            entries[sid] = self._main_record_to_meta(sid, record)
        self._main_cache = entries
        self._main_meta = {
            "mtime": mtime, "hash": digest, "path": str(path), "count": len(entries),
        }
        self._merged_dirty = True
        logger.info(log_dict({'module_name': 'index_cache', 'action': 'main_track.refreshed', 'count': len(entries), 'path': str(path)}))
        return True

    def _rebuild_merged_locked(self) -> None:
        """重建合并视图（调用方需持锁）：主轨补位打底，文件轨覆盖其上

        【不易】文件轨优先 ⇒ 与合并前的 23 条取值**逐字一致**；
                仅在文件轨缺失该 id 时才可能出现主轨条目（= 审计 Q3 S2 的 7 项缺口）；
                且**仅在脏标记置位时替换对象**（未变化必须返回同一 dict，
                否则 loader 的倒排索引会每次全量重建）
        【简易】dict 合并，O(N)
        """
        if not self._merged_dirty and self._merged:
            return
        merged: Dict[str, Dict[str, Any]] = dict(self._main_cache)
        merged.update(self._cache)
        self._merged = merged
        self._merged_dirty = False

    def _validate_all_locked(self) -> bool:
        """增量校验（调用方需持锁）：返回是否有变化

        - 未变化技能：mtime + hash 校验通过 → 直接命中缓存
        - 变化/新增技能：回源重解析
        - 已删除技能：清理缓存条目
        - **主轨文件变化**（【C1 修(5)】）：一并刷新补位条目
        - 有变化时替换为新 dict 对象，让 loader 的 id(index) 失效检测
          触发倒排索引重建（守 loader._get_inverted_index 契约）
        """
        changed = self._refresh_main_track_locked()
        self._rebuild_merged_locked()
        try:
            entries = list(Path(self.fs.repo_path).iterdir())
        except OSError:
            return changed
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
                self._merged_dirty = True
                changed = True
        if changed:
            self._cache = dict(self._cache)
            self._rebuild_merged_locked()
        return changed

    @staticmethod
    def _sanitize_meta(meta: Dict[str, Any]) -> Dict[str, Any]:
        """仅保留 front matter 白名单字段（剔除调用方运行时注入字段）"""
        return {k: v for k, v in meta.items() if k in _META_FIELDS or k == "id"}
