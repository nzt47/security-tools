"""技能管理总服务 — 组合所有子服务为一个易用门面

提供:
    - SkillsMgmtService.create_via_ai / create_manual / install
    - SkillsMgmtService.review / search / get / list_all / delete
    - SkillsMgmtService.bump_version / list_versions / rollback_version
    - SkillsMgmtService.optimize_params / record_execution / set_enabled
    - SkillsMgmtService.health
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional

from .models import (
    Skill,
    SkillSearchParams,
    SkillSearchResult,
    SkillStatus,
    ReviewResult,
    SkillVersion,
)
from .exceptions import (
    SkillNotFoundError,
    SkillMgmtError,
    ErrorCode,
)
from .observability import logger, traced_action, emit_metric
from .store import SkillStore
from .creator import SkillCreator
from .reviewer import SkillReviewer, ReviewThresholds
from .searcher import SkillSearcher
from .enhancer import SkillEnhancer, VersionBump, IntegrationHook
from .file_store import SkillFileStore
from .loader import SkillLoader, MatchResult
from .executor import SkillExecutor, ExecutionResult
from .context_injector import ContextInjector


class SkillsMgmtService:
    """技能管理总服务 (单例建议)"""

    def __init__(self, *, store_path: Optional[str] = None,
                 llm_client: Optional[Any] = None,
                 http_timeout: int = 15,
                 review_thresholds: Optional[ReviewThresholds] = None,
                 repo_path: Optional[str] = None):
        self.store = SkillStore(path=store_path)
        self.creator = SkillCreator(self.store, llm_client=llm_client,
                                    http_timeout=http_timeout)
        self.reviewer = SkillReviewer(thresholds=review_thresholds)
        self.searcher = SkillSearcher()
        self.enhancer = SkillEnhancer(self.store)

        # 三层架构组件
        self.file_store = SkillFileStore(repo_path=repo_path)
        self.loader = SkillLoader(self.file_store)
        self.executor = SkillExecutor(self.file_store)
        self.injector = ContextInjector(self.loader)

    # ─── 创建 ───

    def create_via_ai(self, *, name: str, intent: str,
                      category: str = "custom",
                      tags: Optional[list] = None) -> Skill:
        return self.creator.create_via_ai(
            name=name, intent=intent, category=category, tags=tags)

    def create_manual(self, data: Dict[str, Any]) -> Skill:
        return self.creator.create_manual(data)

    def install(self, source: str, *, force: bool = False) -> Skill:
        return self.creator.install(source, force=force)

    def install_from_zip(self, zip_path: str) -> Dict[str, Any]:
        """从 zip 技能包安装到三层架构文件仓库

        Args:
            zip_path: zip 文件路径

        Returns:
            dict — {skill_id, name, version, scripts_count}
        """
        from .skill_manager import SkillManager
        mgr = SkillManager(repo_path=str(self.file_store.repo_path))
        skill_id = mgr.install_from_zip(zip_path)
        meta = self.file_store.get_metadata(skill_id) or {}
        scripts = self.file_store.list_scripts(skill_id)
        return {
            "skill_id": skill_id,
            "name": meta.get("name", skill_id),
            "version": meta.get("version", "0.0.0"),
            "scripts_count": len(scripts),
        }

    # ─── 审核 ───

    def review(self, skill_id: str) -> ReviewResult:
        """审核指定技能 (与所有其他技能做重复检测)"""
        with traced_action("svc_review", skill_id=skill_id):
            skill = self._require(skill_id)
            others = [s for s in self.store.list_all() if s.id != skill_id]
            result = self.reviewer.review(skill, others=others)
            self.store.upsert(skill)  # 持久化审核结果
            return result

    def review_all_pending(self) -> List[Dict[str, Any]]:
        """批量审核所有 pending_review 状态的技能"""
        results = []
        for s in self.store.list_all():
            if s.status == SkillStatus.PENDING_REVIEW.value:
                try:
                    r = self.review(s.id)
                    results.append({
                        "skill_id": s.id, "status": r.status, "score": r.score,
                    })
                except SkillMgmtError as e:
                    results.append({"skill_id": s.id, "error": e.message})
        return results

    # ─── 搜索 ───

    def search(self, params: SkillSearchParams) -> SkillSearchResult:
        return self.searcher.search(self.store.list_all(), params)

    def list_all(self) -> List[Skill]:
        return self.store.list_all()

    def get(self, skill_id: str) -> Skill:
        return self._require(skill_id)

    # ─── 增删改 ───

    def update(self, skill_id: str, patch: Dict[str, Any]) -> Skill:
        """部分更新技能字段"""
        skill = self._require(skill_id)
        data = skill.model_dump()
        # 白名单字段
        allowed = {"name", "description", "tags", "content", "content_type",
                   "config_schema", "default_params", "dependencies",
                   "author", "enabled"}
        for k, v in patch.items():
            if k in allowed:
                data[k] = v
        updated = Skill.from_storage_dict(data)
        updated.touch()
        self.store.upsert(updated)
        return updated

    def delete(self, skill_id: str) -> bool:
        ok = self.store.remove(skill_id)
        if not ok:
            raise SkillNotFoundError(skill_id)
        logger.info("[Service] 技能已删除: %s", skill_id)
        return True

    # ─── 增强器代理 ───

    def bump_version(self, skill_id: str, kind: str, *,
                     changelog: str = "", content: Optional[str] = None) -> VersionBump:
        return self.enhancer.bump_version(
            skill_id, kind, changelog=changelog, content=content)

    def list_versions(self, skill_id: str) -> List[SkillVersion]:
        return self.enhancer.list_versions(skill_id)

    def rollback_version(self, skill_id: str, target_version: str) -> Skill:
        return self.enhancer.rollback_version(skill_id, target_version)

    def optimize_params(self, skill_id: str) -> Dict[str, Any]:
        return self.enhancer.optimize_params(skill_id)

    def record_execution(self, skill_id: str, *,
                         success: bool, latency_ms: float) -> None:
        self.enhancer.record_execution(
            skill_id, success=success, latency_ms=latency_ms)

    def set_enabled(self, skill_id: str, enabled: bool) -> Skill:
        return self.enhancer.set_enabled(skill_id, enabled)

    def register_hook(self, hook: IntegrationHook) -> None:
        self.enhancer.register_hook(hook)

    # ─── 三层架构代理 (Layer 1/2/3) ───

    def match_skills(self, intent: str, *, top_k: int = 5,
                     enabled_only: bool = True,
                     min_score: float = 0.01) -> MatchResult:
        """Layer 1: 意图匹配 — 在元数据索引上做快速检索

        Args:
            intent: 用户意图文本 (自然语言或关键词)
            top_k: 返回前 K 个匹配结果
            enabled_only: 是否仅返回启用状态的技能
            min_score: 最低匹配分阈值 (低于此值过滤掉)

        Returns:
            MatchResult — 包含 matches 列表与统计信息

        Raises:
            SkillMgmtError: 匹配过程出错时抛出
        """
        with traced_action("svc_match_skills", intent=intent[:80],
                           top_k=top_k, layer=1) as ctx:
            try:
                result = self.loader.match(
                    intent, top_k=top_k,
                    enabled_only=enabled_only,
                    min_score=min_score,
                )
                ctx["matched"] = len(result.matches)
                ctx["elapsed_ms"] = result.elapsed_ms
                ctx["estimated_tokens"] = result.estimated_total_tokens
                logger.info("[Service] Layer1 match intent='%s' → %d 命中, %.2fms",
                            intent[:40], len(result.matches), result.elapsed_ms)
                return result
            except SkillMgmtError:
                raise
            except Exception as e:
                logger.error("[Service] Layer1 match 失败: %s", e)
                raise SkillMgmtError(
                    f"意图匹配失败: {e}",
                    code=ErrorCode.INTERNAL_ERROR,
                    details={"intent": intent[:200]},
                ) from e

    def load_skill_instruction(self, skill_id: str) -> Dict[str, Any]:
        """Layer 2: 按需加载技能使用说明 (skill.md 正文)

        仅在 Layer 1 命中后才应调用此方法，避免无谓加载。

        Args:
            skill_id: 技能ID

        Returns:
            dict — {skill_id, instruction, estimated_tokens, layer}

        Raises:
            SkillNotFoundError: 技能不存在
            SkillFileError: skill.md 读取失败
        """
        with traced_action("svc_load_instruction", skill_id=skill_id, layer=2):
            return self.loader.load_instruction(skill_id)

    def execute_skill_script(self, skill_id: str,
                             script_name: str = "main.py",
                             params: Optional[Dict[str, Any]] = None,
                             timeout: Optional[float] = None) -> ExecutionResult:
        """Layer 3: 沙箱执行技能脚本

        脚本在独立子进程中执行，stdin 接收 JSON 参数，stdout 返回 JSON 结果。
        代码不进入 LLM 上下文，只有执行结果进入。

        Args:
            skill_id: 技能ID
            script_name: 脚本文件名 (必须位于技能的 scripts/ 目录)
            params: 传入脚本的 JSON 参数
            timeout: 执行超时秒数 (None=使用默认 30s)

        Returns:
            ExecutionResult — 包含 success/result/error/duration_ms

        Raises:
            SkillNotFoundError: 技能/脚本不存在
            SkillExecutionError: 执行超时或失败
        """
        with traced_action("svc_execute_script", skill_id=skill_id,
                           script=script_name, layer=3) as ctx:
            result = self.executor.execute(
                skill_id, script_name=script_name,
                params=params, timeout=timeout,
            )
            ctx["success"] = result.success
            ctx["duration_ms"] = result.duration_ms
            ctx["exit_code"] = result.exit_code
            # 埋点: 脚本执行成功率/延迟
            try:
                emit_metric("yunshu_skill_script_exec_total",
                            value=1,
                            labels={"success": str(result.success).lower(),
                                    "skill_id": skill_id},
                            kind="counter")
                emit_metric("yunshu_skill_script_latency_ms",
                            value=result.duration_ms,
                            labels={"skill_id": skill_id},
                            kind="histogram")
            except Exception:  # noqa: BLE001 埋点失败不影响主流程
                pass
            if not result.success:
                logger.warning(
                    "[Service] Layer3 exec %s/%s 失败 exit=%s dur=%.0fms",
                    skill_id, script_name, result.exit_code, result.duration_ms)
            else:
                logger.info(
                    "[Service] Layer3 exec %s/%s 成功 dur=%.0fms",
                    skill_id, script_name, result.duration_ms)
            return result

    def build_skill_context(self, intent: str, *,
                            max_tokens: int = 6000,
                            top_k: int = 5,
                            auto_load_instruction: bool = False,
                            skill_id: Optional[str] = None) -> Dict[str, Any]:
        """一站式构建 LLM 上下文 (Layer 1 + Layer 2)

        流程:
            1. Layer 1: 元数据匹配 → 返回 top_k 候选技能
            2. (可选) Layer 2: 若指定 skill_id 或 auto_load_instruction，
               则按需加载该技能的使用说明

        Args:
            intent: 用户意图
            max_tokens: 上下文 token 预算上限
            top_k: Layer 1 返回的最大候选数
            auto_load_instruction: 是否自动加载 top-1 技能的说明
            skill_id: 显式指定要加载说明的技能ID (优先于 auto_load_instruction)

        Returns:
            dict — {prompt, matches, instruction?, estimated_tokens, layers_used}
        """
        with traced_action("svc_build_context", intent=intent[:80],
                           max_tokens=max_tokens, layer="1+2"):
            return self.injector.build_context(
                intent, max_tokens=max_tokens, top_k=top_k,
                auto_load_instruction=auto_load_instruction,
                skill_id=skill_id,
            )

    def get_layer_summary(self) -> Dict[str, Any]:
        """三层架构统计摘要 — 供前端可视化与 /health 使用"""
        return self.loader.get_layer_summary()

    def list_skill_scripts(self, skill_id: str) -> List[Dict[str, Any]]:
        """列出技能的脚本文件 (Layer 3 元信息，不加载代码)"""
        return self.loader.list_scripts(skill_id)

    def list_skill_temp_files(self, skill_id: str) -> List[Dict[str, Any]]:
        """列出技能的 temp/ 文件"""
        return self.loader.list_temp_files(skill_id)

    # ─── 健康检查 ───

    def health(self) -> Dict[str, Any]:
        """健康检查 (供 /api/skills-mgmt/health 调用)"""
        store_health = self.store.health()
        all_skills = self.store.list_all()
        # 三层架构健康状态
        try:
            file_store_health = self.file_store.health()
        except Exception as e:  # noqa: BLE001
            file_store_health = {"ok": False, "error": str(e)}
        try:
            executor_health = self.executor.health()
        except Exception as e:  # noqa: BLE001
            executor_health = {"ok": False, "error": str(e)}
        try:
            layer_summary = self.loader.get_layer_summary()
        except Exception as e:  # noqa: BLE001
            layer_summary = {"error": str(e)}
        return {
            "ok": store_health.get("ok", False) and file_store_health.get("ok", False),
            "module": "skills_mgmt",
            "version": "1.1.0",  # 三层架构版本
            "store": store_health,
            "three_layer": {
                "file_store": file_store_health,
                "executor": executor_health,
                "layer_summary": layer_summary,
            },
            "stats": {
                "total": len(all_skills),
                "enabled": sum(1 for s in all_skills if s.enabled),
                "approved": sum(
                    1 for s in all_skills
                    if s.status == SkillStatus.APPROVED.value),
                "pending_review": sum(
                    1 for s in all_skills
                    if s.status == SkillStatus.PENDING_REVIEW.value),
                "rejected": sum(
                    1 for s in all_skills
                    if s.status == SkillStatus.REJECTED.value),
            },
        }

    # ─── 内部 ───

    def _require(self, skill_id: str) -> Skill:
        skill = self.store.get(skill_id)
        if not skill:
            raise SkillNotFoundError(skill_id)
        return skill
