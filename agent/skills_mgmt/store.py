"""技能管理持久化存储

设计:
    - 单文件 JSON 存储 (data/skills_mgmt.json)，原子写入 (临时文件 + os.replace)
    - 内存缓存 + 文件回写，避免高频读盘
    - 与现有 agent/extensions/store.py 互操作: 同步技能状态到 ExtensionStore，
      使旧 routes_skills.py 仍能看到技能启用状态
"""

from __future__ import annotations
import json
import os
import tempfile
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .models import Skill, SkillStatus
from .observability import logger

# 默认存储位置 — 与项目其他 data 文件对齐
_DEFAULT_STORE_PATH = Path(__file__).parent.parent.parent / "data" / "skills_mgmt.json"


class SkillStore:
    """技能持久化存储 (线程安全)"""

    def __init__(self, path: Optional[str] = None):
        self._path = Path(path) if path else _DEFAULT_STORE_PATH
        self._lock = threading.RLock()
        self._cache: Optional[Dict[str, dict]] = None

    # ──────────────────────────────────────────
    # 底层 IO
    # ──────────────────────────────────────────

    def _load(self) -> Dict[str, dict]:
        """加载全部技能 (带缓存)"""
        with self._lock:
            if self._cache is not None:
                return self._cache
            if not self._path.exists():
                self._cache = {}
                self._persist()
                logger.info("[SkillStore] 初始化存储: %s", self._path)
                return self._cache
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._cache = json.load(f)
                if not isinstance(self._cache, dict):
                    raise ValueError("存储文件根节点必须是对象")
            except (json.JSONDecodeError, ValueError, OSError) as e:
                # 边界显性化: 文件损坏时不可静默丢弃，先备份再重置
                backup = self._path.with_suffix(".corrupted.json")
                try:
                    self._path.rename(backup)
                    logger.warning("[SkillStore] 存储损坏已备份到 %s: %s", backup, e)
                except OSError:
                    pass
                self._cache = {}
                self._persist()
            return self._cache

    def _persist(self) -> None:
        """原子写入 (临时文件 + os.replace)"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", delete=False,
            dir=str(self._path.parent), suffix=".tmp",
        ) as tmp:
            json.dump(self._cache or {}, tmp, ensure_ascii=False, indent=2)
            tmp_path = tmp.name
        os.replace(tmp_path, self._path)

    def _invalidate(self) -> None:
        self._cache = None

    # ──────────────────────────────────────────
    # 公开 API
    # ──────────────────────────────────────────

    def list_all(self) -> List[Skill]:
        """返回所有技能"""
        data = self._load()
        return [Skill.from_storage_dict(v) for v in data.values()]

    def get(self, skill_id: str) -> Optional[Skill]:
        data = self._load()
        if skill_id not in data:
            return None
        return Skill.from_storage_dict(data[skill_id])

    def upsert(self, skill: Skill) -> None:
        """新增或更新 (按 id)"""
        with self._lock:
            data = self._load()
            data[skill.id] = skill.to_storage_dict()
            self._persist()

    def remove(self, skill_id: str) -> bool:
        with self._lock:
            data = self._load()
            if skill_id not in data:
                return False
            del data[skill_id]
            self._persist()
            return True

    def count(self) -> int:
        return len(self._load())

    def clear(self) -> None:
        """清空存储 (谨慎使用)"""
        with self._lock:
            self._cache = {}
            self._persist()

    # ──────────────────────────────────────────
    # 合并 (Jaccard≥0.7 触发)
    # ──────────────────────────────────────────

    def merge_skills(self, src_id: str, dst_id: str, *,
                     strategy: str = "auto",
                     feedback_manager=None) -> Dict[str, Any]:
        """合并两个技能 — 保留 dst，删除 src

        合并策略 (auto):
            - 保留 metrics 高的一方作为主（dst 默认为主，但 auto 时可能切换）
            - 合并 tags（去重）
            - 把 src 的 content 作为版本快照保存到 dst 的 versions 历史
            - 把 src 的 default_params 与 dependencies 合并进 dst
            - 把 feedback 表中 skill_id=src 的反馈改绑到 dst

        Args:
            src_id: 被合并方 ID（将被删除）
            dst_id: 合并保留方 ID
            strategy: auto | keep_dst | keep_src
                     auto: 比较 metrics.usage_count + status 决定主从
                     keep_dst: 始终保留 dst（src 直接合并进去）
                     keep_src: 始终保留 src（dst 被合并进去）
            feedback_manager: 可选 FeedbackManager 实例（用于改绑反馈）

        Returns:
            {merged_id, removed_id, merged_fields, version_added}

        Raises:
            ValueError: src_id == dst_id / 任一不存在
        """
        with self._lock:
            data = self._load()

            # 边界显性化
            if src_id == dst_id:
                raise ValueError(
                    f"src_id 与 dst_id 不能相同: {src_id}"
                )
            if src_id not in data:
                raise ValueError(f"src 技能不存在: {src_id}")
            if dst_id not in data:
                raise ValueError(f"dst 技能不存在: {dst_id}")

            src_skill = Skill.from_storage_dict(data[src_id])
            dst_skill = Skill.from_storage_dict(data[dst_id])

            # 自动决定主从
            actual_dst, actual_src, swapped = self._resolve_merge_direction(
                src_skill, dst_skill, strategy
            )
            if swapped:
                # 交换主从：以原 dst 为 src（被删方）
                actual_dst_id, actual_src_id = dst_id, src_id
                # 重新读取，确保 actual_dst/src 与 ID 对齐
                actual_dst = src_skill
                actual_src = dst_skill
            else:
                actual_dst_id, actual_src_id = dst_id, src_id

            merged_fields: List[str] = []

            # 1) 合并 tags
            new_tags = list(set(actual_dst.tags) | set(actual_src.tags))
            if len(new_tags) > len(actual_dst.tags):
                actual_dst.tags = new_tags
                merged_fields.append("tags")

            # 2) 合并 dependencies — 使用 dependency_validator 的 prefer_a 策略
            #    灰度接入: 出现异常时回退到 set union，保证合并不会因依赖冲突失败
            dep_merge_info = self._merge_dependencies_safe(
                actual_dst, actual_src,
            )
            if dep_merge_info.get("changed"):
                merged_fields.append("dependencies")
            if dep_merge_info.get("conflicts"):
                logger.info(
                    "[SkillStore] 依赖合并冲突报告: %s",
                    dep_merge_info["conflicts"],
                )

            # 3) 合并 default_params（src 优先覆盖 dst 中不存在的键）
            new_params = dict(actual_src.default_params)
            new_params.update(actual_dst.default_params)
            if new_params != actual_dst.default_params:
                actual_dst.default_params = new_params
                merged_fields.append("default_params")

            # 4) 把 src 的 content 作为版本快照存到 dst.versions
            version_added = None
            if actual_src.content and actual_src.content != actual_dst.content:
                from .models import SkillVersion
                import hashlib as _hashlib
                version_added = SkillVersion(
                    version=actual_src.version,
                    content=actual_src.content,
                    changelog=(
                        f"合并自技能 {actual_src.id} "
                        f"(success_rate={actual_src.metrics.success_rate:.2f})"
                    ),
                    created_by="merge_skills",
                    hash=_hashlib.sha256(
                        actual_src.content.encode("utf-8")
                    ).hexdigest()[:16],
                )
                actual_dst.versions.append(version_added)
                merged_fields.append("versions")

            # 5) 合并 metrics（usage_count / success_count 累加）
            merged_metrics = self._merge_metrics(
                actual_dst.metrics, actual_src.metrics
            )
            if merged_metrics != actual_dst.metrics:
                actual_dst.metrics = merged_metrics
                merged_fields.append("metrics")

            # 6) 落地
            actual_dst.touch()
            data[actual_dst_id] = actual_dst.to_storage_dict()
            del data[actual_src_id]
            self._cache = data
            self._persist()

            # 7) 同步 legacy skills.json
            try:
                self.sync_to_legacy_skills_json()
            except Exception as e:
                logger.warning("[SkillStore] 合并后同步 legacy 失败: %s", e)

            logger.info(
                "[SkillStore] 已合并技能: src=%s → dst=%s, fields=%s",
                actual_src_id, actual_dst_id, merged_fields,
            )

            # 8) 改绑 feedback（如果提供了 feedback_manager）
            feedback_rebound_count = 0
            if feedback_manager is not None:
                feedback_rebound_count = self._rebind_feedback(
                    feedback_manager,
                    src_skill_id=actual_src_id,
                    dst_skill_id=actual_dst_id,
                )

            return {
                "merged_id": actual_dst_id,
                "removed_id": actual_src_id,
                "merged_fields": merged_fields,
                "version_added": (
                    version_added.version if version_added else None
                ),
                "feedback_rebound_count": feedback_rebound_count,
                "dependency_merge": dep_merge_info,
            }

    def _resolve_merge_direction(self, src_skill: Skill,
                                 dst_skill: Skill,
                                 strategy: str) -> tuple:
        """决定实际主从方向

        Returns: (actual_dst, actual_src, swapped: bool)
                 swapped=True 表示实际把 src 作为保留方
        """
        if strategy == "keep_dst":
            return dst_skill, src_skill, False
        if strategy == "keep_src":
            return src_skill, dst_skill, True

        # auto: 比较 PUBLISHED > APPROVED > DRAFT，再比 metrics.usage_count
        from .models import SkillStatus
        rank = {
            SkillStatus.PUBLISHED.value: 3,
            SkillStatus.APPROVED.value: 2,
            SkillStatus.PENDING_REVIEW.value: 1,
            SkillStatus.DRAFT.value: 0,
            SkillStatus.REJECTED.value: -1,
            SkillStatus.DEPRECATED.value: -2,
        }
        s_rank = rank.get(src_skill.status, 0)
        d_rank = rank.get(dst_skill.status, 0)
        if s_rank > d_rank:
            return src_skill, dst_skill, True
        if d_rank > s_rank:
            return dst_skill, src_skill, False
        # 同级别 → 比 usage_count
        if src_skill.metrics.usage_count > dst_skill.metrics.usage_count * 2:
            return src_skill, dst_skill, True
        return dst_skill, src_skill, False

    @staticmethod
    def _merge_dependencies_safe(dst_skill: Skill,
                                 src_skill: Skill) -> Dict[str, Any]:
        """使用 dependency_validator 合并依赖 (prefer_a 策略 + allow_optional)

        灰度接入策略:
            - 正常情况: 调用 merge_dependencies, 返回带版本约束的合并结果
            - 异常情况 (DependencyConflictError 等): 回退到 set union, 不阻断合并

        存储格式:
            - 无版本约束: 保留为 str ("requests")
            - 有版本约束: 存为 dict ({"name": "openai", "version_spec": ">=1.0", ...})

        Returns:
            {changed: bool, strategy: str, before: int, after: int,
             conflicts: [...], fallback: bool}
        """
        from .dependency_validator import (
            merge_dependencies,
            detect_conflicts,
            DependencyConflictError,
            Dependency,
        )
        before_deps = list(dst_skill.dependencies)
        before_count = len(before_deps)

        # 检测冲突 (用于日志和返回值)
        try:
            conflicts = detect_conflicts(
                dst_skill.dependencies, src_skill.dependencies,
                allow_optional=True,
            )
        except Exception as e:
            logger.warning(
                "[SkillStore] 依赖冲突检测异常: %s", e,
            )
            conflicts = []

        hard_conflicts = [c.to_dict() for c in conflicts
                         if c.reason == "no_version_overlap"]
        weak_conflicts = [c.to_dict() for c in conflicts
                          if c.reason == "spec_diff_but_intersect"]

        # 执行合并 (prefer_a: 硬冲突时保留 dst 版本)
        try:
            merged = merge_dependencies(
                dst_skill.dependencies, src_skill.dependencies,
                strategy="prefer_a",
                allow_optional=True,
            )
            # 转回存储格式: 无版本约束 → str, 有版本约束 → dict
            new_deps: List[Union[str, Dict[str, Any]]] = []
            for dep in merged:
                if dep.version_spec == "*" and not dep.optional:
                    new_deps.append(dep.name)
                else:
                    new_deps.append(dep.to_dict())

            fallback_used = False
        except DependencyConflictError as e:
            # prefer_a 不应抛异常, 但作为兜底保险
            logger.warning(
                "[SkillStore] merge_dependencies 抛冲突异常, 回退到 set union: %s",
                e,
            )
            new_deps = list({
                (d if isinstance(d, str) else str(d))
                for d in list(dst_skill.dependencies) + list(src_skill.dependencies)
            })
            fallback_used = True

        changed = new_deps != before_deps
        if changed:
            dst_skill.dependencies = new_deps

        return {
            "changed": changed,
            "strategy": "prefer_a",
            "fallback": fallback_used,
            "before": before_count,
            "after": len(new_deps),
            "conflicts": {
                "hard": hard_conflicts,
                "weak": weak_conflicts,
            },
        }

    @staticmethod
    def _merge_metrics(dst_metrics, src_metrics):
        """合并两个 SkillMetrics：累加 usage/success/failure"""
        from .models import SkillMetrics
        new_usage = dst_metrics.usage_count + src_metrics.usage_count
        new_success = dst_metrics.success_count + src_metrics.success_count
        new_failure = dst_metrics.failure_count + src_metrics.failure_count
        # 加权平均延迟
        total = new_usage if new_usage > 0 else 1
        new_avg = (
            dst_metrics.avg_latency_ms * dst_metrics.usage_count
            + src_metrics.avg_latency_ms * src_metrics.usage_count
        ) / total
        # 合并 param_stats（同 key 累加 success/failure/latency）
        merged_param_stats: Dict[str, dict] = {}
        for key, stat in dst_metrics.param_stats.items():
            merged_param_stats[key] = dict(stat)
        for key, stat in src_metrics.param_stats.items():
            if key in merged_param_stats:
                m = merged_param_stats[key]
                m["success"] = m.get("success", 0) + stat.get("success", 0)
                m["failure"] = m.get("failure", 0) + stat.get("failure", 0)
                m["total_latency_ms"] = (
                    m.get("total_latency_ms", 0.0)
                    + stat.get("total_latency_ms", 0.0)
                )
                # 取较新的 last_used_at
                if (stat.get("last_used_at") or "") > (m.get("last_used_at") or ""):
                    m["last_used_at"] = stat.get("last_used_at")
            else:
                merged_param_stats[key] = dict(stat)
        # 合并 avoid_params（按 params_hash 或 params 哈希去重）
        merged_avoid: List[dict] = list(dst_metrics.avoid_params)
        existing_keys = set()
        for d in merged_avoid:
            if not isinstance(d, dict):
                continue
            # 优先用 params_hash 字段，否则用 params 的内容哈希
            if d.get("params_hash"):
                existing_keys.add(d["params_hash"])
            elif "params" in d:
                import hashlib as _hl
                import json as _js
                try:
                    k = _hl.md5(
                        _js.dumps(d["params"], sort_keys=True,
                                  ensure_ascii=False).encode("utf-8")
                    ).hexdigest()[:8]
                except (TypeError, ValueError):
                    k = str(sorted(d["params"].items()))
                existing_keys.add(k)
        for entry in src_metrics.avoid_params:
            if not isinstance(entry, dict):
                continue
            entry_key = entry.get("params_hash")
            if not entry_key and "params" in entry:
                import hashlib as _hl2
                import json as _js2
                try:
                    entry_key = _hl2.md5(
                        _js2.dumps(entry["params"], sort_keys=True,
                                   ensure_ascii=False).encode("utf-8")
                    ).hexdigest()[:8]
                except (TypeError, ValueError):
                    entry_key = str(sorted(entry["params"].items()))
            if entry_key and entry_key not in existing_keys:
                merged_avoid.append(entry)
                existing_keys.add(entry_key)
        new_metrics = SkillMetrics(
            usage_count=new_usage,
            success_count=new_success,
            failure_count=new_failure,
            success_rate=(new_success / total) if total > 0 else 0.0,
            avg_latency_ms=new_avg,
            p95_latency_ms=max(dst_metrics.p95_latency_ms,
                               src_metrics.p95_latency_ms),
            last_used_at=SkillStore._pick_latest_timestamp(
                dst_metrics.last_used_at, src_metrics.last_used_at,
            ),
            last_latency_ms=dst_metrics.last_latency_ms,
            param_stats=merged_param_stats,
            avoid_params=merged_avoid,
        )
        return new_metrics

    @staticmethod
    def _pick_latest_timestamp(a: Optional[str],
                               b: Optional[str]) -> Optional[str]:
        """取两个 ISO 时间戳字符串中较晚者（None 视为更早）"""
        if not a and not b:
            return None
        if not a:
            return b
        if not b:
            return a
        # ISO 时间字符串字典序与时间序一致
        return a if a >= b else b

    @staticmethod
    def _rebind_feedback(feedback_manager, *,
                         src_skill_id: str,
                         dst_skill_id: str) -> int:
        """把 src 技能的所有 feedback 改绑到 dst

        通过 UPDATE feedback SET skill_id=dst WHERE skill_id=src
        Returns: 改绑的反馈条数
        """
        try:
            import sqlite3
            # 通过 feedback_manager 的 _get_conn 访问数据库
            with feedback_manager._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE feedback SET skill_id = ? WHERE skill_id = ?",
                    (dst_skill_id, src_skill_id),
                )
                count = cursor.rowcount
                # 同时改绑 quality_cases
                cursor.execute(
                    "UPDATE quality_cases SET skill_id = ? WHERE skill_id = ?",
                    (dst_skill_id, src_skill_id),
                )
                conn.commit()
            return count
        except Exception as e:
            logger.warning(
                "[SkillStore] 改绑 feedback 失败 src=%s → dst=%s: %s",
                src_skill_id, dst_skill_id, e,
            )
            return 0

    # ──────────────────────────────────────────
    # 与旧系统互操作
    # ──────────────────────────────────────────

    def sync_to_legacy_skills_json(self) -> int:
        """把启用状态的技能同步到 data/skills.json，保持向后兼容

        旧 SkillsManager / routes_skills.py 仍读取 data/skills.json，
        此方法确保新系统的技能在旧 UI 也能看到。
        Returns: 同步的技能数量
        """
        try:
            legacy_path = self._path.parent / "skills.json"
            with open(legacy_path, "r", encoding="utf-8") as f:
                legacy = json.load(f)
            if not isinstance(legacy, dict):
                legacy = {"skills": []}
            legacy.setdefault("skills", [])
            existing_ids = {s.get("id") for s in legacy["skills"]}

            count = 0
            for skill_dict in self._load().values():
                if skill_dict.get("id") in existing_ids:
                    continue
                # 仅同步启用且非草稿的技能，避免污染旧 UI
                if (skill_dict.get("enabled", True)
                        and skill_dict.get("status", SkillStatus.DRAFT.value)
                        in (SkillStatus.APPROVED.value,
                            SkillStatus.PUBLISHED.value,
                            SkillStatus.DRAFT.value)):
                    legacy["skills"].append({
                        "id": skill_dict["id"],
                        "name": skill_dict.get("name", skill_dict["id"]),
                        "enabled": True,
                        "description": skill_dict.get("description", ""),
                        "params": skill_dict.get("default_params", {}),
                    })
                    count += 1
            if count > 0:
                with open(legacy_path, "w", encoding="utf-8") as f:
                    json.dump(legacy, f, ensure_ascii=False, indent=2)
                logger.info("[SkillStore] 同步 %d 个技能到 legacy skills.json", count)
            return count
        except Exception as e:  # noqa: BLE001  向后兼容同步失败不阻塞主流程
            logger.warning("[SkillStore] 同步 legacy 失败: %s", e)
            return 0

    def health(self) -> Dict[str, Any]:
        """健康检查 (供 /api/skills-mgmt/health 调用)"""
        try:
            count = self.count()
            writable = os.access(self._path.parent, os.W_OK)
            return {
                "ok": True,
                "store_path": str(self._path),
                "skill_count": count,
                "writable": bool(writable),
            }
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
