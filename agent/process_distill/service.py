"""过程蒸馏门面服务 — 一条调用完成 素材→蒸馏→合并→固化。

用法：
    from agent.process_distill import ProcessDistillService
    svc = ProcessDistillService()
    # 从知识库 wiki 检索蒸馏并固化（workflow + skill）
    r = svc.distill(query="git gc 维护复盘", artifacts=["workflow", "skill"])
    # 或直接蒸馏一个外部 SKILL.md 目录
    r = svc.distill(paths=["path/to/skills/xxx"], artifacts=["skill"])

LLM：默认从 .env（LLM_PROVIDER/LLM_API_KEY/LLM_MODEL/LLM_BASE_URL）构建
      LLMService；可注入 llm（duck-typing: chat(messages, system_prompt=...)）。
      LLM 缺失/失败 → 规则提取降级，不抛异常。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from agent.process_distill import sources
from agent.process_distill.distiller import distill_parallel
from agent.process_distill.merge import merge_results
from agent.process_distill.models import DistillMaterial
from agent.process_distill.solidify import (
    solidify_to_skill,
    solidify_to_workflow,
)

logger = logging.getLogger(__name__)

# 允许的固化产物
_ARTIFACTS = {"workflow", "skill"}


def _ensure_env_loaded() -> None:
    """把项目根 .env 加载进 os.environ（幂等；app_server 已加载时无副作用）。

    云枢 .env 由 agent.env_config_manager 管理，独立脚本/工具 handler
    直接 import 本模块时 os.environ 可能还没有 .env 内容。
    """
    try:
        from agent.env_config_manager import get_env_config_manager
        get_env_config_manager().reload()
    except Exception:  # noqa: BLE001  .env 加载失败不阻断（回退系统环境变量）
        pass


def build_default_llm() -> Optional[Any]:
    """从 .env 构建默认 LLMService；配置缺失返回 None（触发规则降级）。"""
    _ensure_env_loaded()
    api_key = (os.environ.get("LLM_API_KEY", "")
               or os.environ.get("DEEPSEEK_API_KEY", ""))
    if not api_key:
        return None
    try:
        from memory.llm_service import LLMService
        return LLMService(
            provider=os.environ.get("LLM_PROVIDER", "deepseek").strip().lower(),
            api_key=api_key,
            model=os.environ.get("LLM_MODEL", "deepseek-v4-flash"),
            base_url=(os.environ.get("LLM_BASE_URL", "")
                      or os.environ.get("DEEPSEEK_BASE_URL", "")),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[PD] 默认 LLM 构建失败，走规则降级: %s", e)
        return None


class ProcessDistillService:
    """过程蒸馏门面服务"""

    def __init__(self, *, llm: Any = None, use_default_llm: bool = True,
                 wf_svc: Optional[Any] = None,
                 skills_svc: Optional[Any] = None):
        # llm 显式传 None 且 use_default_llm=False → 禁用 LLM（强制规则降级，
        # 测试/离线场景用）；默认自动从 .env 构建。
        if llm is not None:
            self._llm = llm
        elif use_default_llm:
            self._llm = build_default_llm()
        else:
            self._llm = None
        self._wf_svc = wf_svc
        self._skills_svc = skills_svc

    # ─── 内部服务解析（懒加载） ───

    def _wf_service(self):
        if self._wf_svc is None:
            from agent.state_manager import get_workflow_learning_service
            self._wf_svc = get_workflow_learning_service()
        return self._wf_svc

    def _skills_service(self):
        if self._skills_svc is None:
            from agent.state_manager import get_skills_mgmt_service
            self._skills_svc = get_skills_mgmt_service()
        return self._skills_svc

    def distill_materials(self, materials: List[DistillMaterial],
                          available_tools: Optional[List[str]] = None,
                          max_workers: int = 4) -> Any:
        """素材列表 → 并行蒸馏 → 合并产物（DistilledProcess）。"""
        raw = distill_parallel(materials, self._llm,
                               available_tools=available_tools,
                               max_workers=max_workers)
        return merge_results(raw["results"])

    # ─── 主入口 ───

    def distill(self, *, query: str = "",
                paths: Optional[List[str]] = None,
                artifacts: Optional[List[str]] = None,
                top_k: int = 5,
                max_workers: int = 4,
                available_tools: Optional[List[str]] = None,
                session_id: str = "process-distill",
                ) -> Dict[str, Any]:
        """从知识库/路径素材蒸馏并固化为指定产物。

        Args:
            query: 知识库 wiki 检索关键词（可空，与 paths 可同时用）。
            paths: 素材文件/目录路径列表（可空）。
            artifacts: 固化产物列表，取值 workflow/skill（默认 both）。
            top_k: wiki 检索召回数。
            max_workers: 并行子代理数。
            available_tools: 工具白名单（默认取云枢已注册工具）。
            session_id: workflow 来源会话标识。

        Returns:
            {ok, materials, process, artifacts: {workflow?: {...}, skill?: {...}}}
        """
        if not query and not paths:
            raise ValueError("query 与 paths 至少提供一个（无输入素材无法蒸馏）")

        mats = sources.collect_materials(query=query, paths=paths, top_k=top_k)
        if not mats:
            return {
                "ok": False,
                "error": "未检索到任何素材",
                "query": query,
                "paths": paths or [],
            }

        proc = self.distill_materials(mats,
                                      available_tools=available_tools,
                                      max_workers=max_workers)
        want = artifacts if artifacts else ["workflow", "skill"]
        want = [a for a in want if a in _ARTIFACTS]

        outs: Dict[str, Any] = {}
        if "workflow" in want:
            outs["workflow"] = solidify_to_workflow(
                proc, wf_svc=self._wf_service(),
                available_tools=available_tools, session_id=session_id)
        if "skill" in want:
            outs["skill"] = solidify_to_skill(proc, skills_svc=self._skills_service())

        return {
            "ok": True,
            "query": query,
            "paths": paths or [],
            "llm_used": self._llm is not None,
            "materials": [m.to_dict() for m in mats],
            "process": proc.to_dict(),
            "artifacts": outs,
        }

    # ─── 健康/能力说明 ───

    def health(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "llm_configured": self._llm is not None,
            "artifacts": sorted(_ARTIFACTS),
            "usage": (
                "distill(query=..., paths=..., artifacts=['workflow','skill'])"
            ),
        }
