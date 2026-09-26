"""技能管理总服务 — 组合所有子服务为一个易用门面

提供:
    - SkillsMgmtService.create_via_ai / create_manual / install
    - SkillsMgmtService.review / search / get / list_all / delete
    - SkillsMgmtService.bump_version / list_versions / rollback_version
    - SkillsMgmtService.optimize_params / record_execution / set_enabled
    - SkillsMgmtService.health
"""

from __future__ import annotations
from typing import Any, Callable, Dict, List, Optional

from .lineage import (
    EvolutionArchive,
    EvolutionRecord,
    get_default_archive,
    print_lineage,
)
from .models import (
    Skill,
    SkillSearchParams,
    SkillSearchResult,
    SkillStatus,
    ReviewResult,
    ReviewStatus,
    ReviewFinding,
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
from .index_cache import SkillIndexCache
from .loader import SkillLoader, MatchResult
from .executor import SkillExecutor, ExecutionResult
from .context_injector import ContextInjector
from .output_guard import SkillOutputGuard


class SkillsMgmtService:
    """技能管理总服务 (单例建议)"""

    def __init__(self, *, store_path: Optional[str] = None,
                 llm_client: Optional[Any] = None,
                 http_timeout: int = 15,
                 review_thresholds: Optional[ReviewThresholds] = None,
                 repo_path: Optional[str] = None,
                 class_registry_path: Optional[str] = None):
        self.store = SkillStore(path=store_path)
        self.creator = SkillCreator(self.store, llm_client=llm_client,
                                    http_timeout=http_timeout)
        self.reviewer = SkillReviewer(thresholds=review_thresholds)
        self.searcher = SkillSearcher()
        self.enhancer = SkillEnhancer(self.store)
        # EVO-T1 谱系档案库：默认取全局单例，测试可注入隔离实例
        self._lineage_archive = get_default_archive()

        # 自动分类注册表（同层目录 skills_classes.json；测试可注入 tmp 路径）
        from .categorizer import SkillClassRegistry
        if class_registry_path is None and store_path:
            import os as _os
            class_registry_path = _os.path.join(
                _os.path.dirname(_os.path.abspath(store_path)),
                "skills_classes.json")
        self._class_registry = SkillClassRegistry(path=class_registry_path)

        # 三层架构组件
        self.file_store = SkillFileStore(repo_path=repo_path)
        # [变易] 技能索引缓存：预解析 front matter，启动时加载
        # （缓存加载失败自动降级为运行时解析，不影响功能；守【不易】接口不变）
        self.index_cache = SkillIndexCache(self.file_store)
        self.index_cache.load_on_startup()
        self.loader = SkillLoader(self.file_store)
        self.executor = SkillExecutor(self.file_store)
        self.injector = ContextInjector(self.loader)
        self._mcp_adapter = None  # 延迟初始化 MCP 适配器

        # EVO-T6 安全护栏组件（懒加载，测试可注入隔离实例）
        self._approval = None  # ApprovalFlow 实例（None=未接入审批流）
        self._auto_rollback = None  # AutoRollback 懒加载单例缓存

    @property
    def mcp_adapter(self):
        """延迟初始化 MCP 适配器 (首次访问时创建)"""
        if self._mcp_adapter is None:
            from .mcp_adapter import McpSkillAdapter
            self._mcp_adapter = McpSkillAdapter(self)
        return self._mcp_adapter

    # ─── EVO-T6 安全护栏网关（供 UI/CLI 消费）───

    @property
    def auto_rollback(self) -> "AutoRollback":
        """自动回滚懒加载单例（默认 restorer=None=仅记录不实际恢复）"""
        if self._auto_rollback is None:
            from .rollback import AutoRollback
            self._auto_rollback = AutoRollback(archive=self._lineage_archive)
        return self._auto_rollback

    def list_pending_approvals(self) -> List[Dict[str, Any]]:
        """列出全部待审批记录（未接入审批流时返回空列表）

        TASK-S2-03 §6.1：人工**查看**待审批项 = 介入（``view`` 权重 0，只计数不加权）
        ——查看是「审批疲劳/衰减率」的观测信号，记录在此真实发生点。
        埋点委托审批域 `ApprovalFlow.record_view`（服务层不导入 observability，
        避免与既有链路构成循环依赖，见 CI 架构规则 no_circular_dependency）。
        """
        if self._approval is None:
            return []
        rows = [
            {
                "record_id": r.record_id,
                "object_type": r.object_type,
                "object_id": r.object_id,
                "level": r.level,
                "state": r.state,
                "created_at": r.created_at,
            }
            for r in self._approval.list({"state": "pending_review"})
        ]
        self._record_approval_views(rows)
        return rows

    def _record_approval_views(self, rows: List[Dict[str, Any]]) -> None:
        """查看待审批项 → ACR 介入埋点（view=0；best-effort，绝不阻断列表查询）"""
        try:
            record_view = getattr(self._approval, "record_view", None)
            if record_view is None:
                return
            for row in rows or []:
                record_view(
                    str(row.get("record_id") or ""), actor="human",
                    detail={"object_type": row.get("object_type"),
                            "object_id": row.get("object_id"),
                            "level": row.get("level")})
        except Exception as e:  # noqa: BLE001 埋点不得影响查询
            logger.debug("[SkillsMgmt] approval view 埋点失败: %s", e)

    def approve_change(self, record_id: str, actor: str = "reviewer",
                       note: str = "") -> Dict[str, Any]:
        """审批通过网关（透传 ApprovalFlow.approve）"""
        if self._approval is None:
            raise RuntimeError("审批流未接入（_approval 未注入），无法审批")
        rec = self._approval.approve(record_id, actor=actor, note=note)
        return {"record_id": rec.record_id, "state": rec.state,
                "level": rec.level}

    def reject_change(self, record_id: str, actor: str = "reviewer",
                      reason: str = "") -> Dict[str, Any]:
        """驳回网关（透传 ApprovalFlow.reject，reason 必填）"""
        if self._approval is None:
            raise RuntimeError("审批流未接入（_approval 未注入），无法驳回")
        rec = self._approval.reject(record_id, actor=actor, reason=reason)
        return {"record_id": rec.record_id, "state": rec.state,
                "decision_reason": rec.decision_reason}

    def get_evolution_audit(self, days: int = 7) -> Dict[str, Any]:
        """进化审计视图网关（透传 dashboard.get_evolution_audit）"""
        from agent.health.dashboard import get_evolution_audit as _audit
        return _audit(days=days, archive=self._lineage_archive,
                      approvals=self._approval)

    def run_auto_rollback(self, object_id: str, version: str,
                          metrics: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """执行自动回滚判定网关（无基线安全返回 triggered=False）"""
        result = self.auto_rollback.check_and_rollback(
            object_id, version, metrics)
        return {
            "triggered": result.triggered,
            "reason": result.reason,
            "suppressed": result.suppressed,
            "halted": result.halted,
            "details": result.details,
        }

    # ─── 创建 ───

    def create_via_ai(self, *, name: str, intent: str,
                      category: str = "custom",
                      tags: Optional[list] = None) -> Skill:
        skill = self.creator.create_via_ai(
            name=name, intent=intent, category=category, tags=tags)
        self._advisory_assess(skill.id)   # 自动执行评审-评估（咨询性，不改状态）
        self._auto_classify(skill.id)     # 自动归类（新技能进分类注册表）
        return self._require(skill.id)    # 返回含自动评估报告的最新对象

    def create_manual(self, data: Dict[str, Any]) -> Skill:
        skill = self.creator.create_manual(data)
        self._advisory_assess(skill.id)   # 自动执行评审-评估（咨询性，不改状态）
        self._auto_classify(skill.id)     # 自动归类
        return self._require(skill.id)

    def install(self, source: str, *, force: bool = False) -> Skill:
        skill = self.creator.install(source, force=force)
        self._advisory_assess(skill.id)   # 外来技能进入即自动评审-评估
        self._auto_classify(skill.id)     # 外来技能自动归类（可自动新建类）
        # 吸收策略：与云枢自身功能重叠 ≠ 整包拒绝——保留技能并标记「增量吸收」
        # （原生未覆盖的增量内容照常可用；重叠提示由评估以 warn 呈现）
        dup = self.reject_native_duplicate(skill)
        if dup:
            self.absorb_overlap(skill.id, overlap=dup, source="install")
        # 【S1-02 接线】同步 Descriptor（provenance/trust 回填，advisory 不阻断）
        self._sync_descriptor_advisory(skill.id, source=source, skill=skill)
        return self._require(skill.id)

    def reject_native_duplicate(self, skill) -> Optional[str]:
        """检测技能是否与云枢自身功能重叠；返回重叠说明（None=无重叠）。

        注意：仅作「检测/提示」，不再删除或拒绝——调用方应改用 absorb_overlap
        按增量吸收保留（吸收优先策略）。
        """
        try:
            from .assessor import detect_native_duplicates
            hits = detect_native_duplicates(
                skill.name or "", skill.description or "", skill.content or "")
            if not hits:
                return None
            return "、".join(
                f"「{h['name']}」({h['id']})" for h in hits)
        except Exception:  # noqa: BLE001 检测失败不阻断（后续权威评审仍会提示）
            return None

    def absorb_overlap(self, skill_id: str, *,
                       overlap: str = "", source: str = "import") -> Skill:
        """吸收优先策略：与原生/已有能力重叠时不再整包拒绝。

        保留技能并打上吸收标记（absorbed / native-overlap），记录评估吸收事件，
        让「原生与已有技能未覆盖的增量」继续走评审-评估/合并流程吸收进来。
        """
        skill = self._require(skill_id)
        tags = list(skill.tags or [])
        add = ["absorbed"]
        if overlap:
            add.append("native-overlap")
        changed = False
        for t in add:
            if t not in tags:
                tags.append(t)
                changed = True
        if changed:
            skill.tags = tags
            skill.touch()
            self.store.upsert(skill)
        self._emit_assessment_event(
            skill_id, "absorb", "ok",
            f"{source}: 与已有/原生能力重叠，已按「增量吸收」保留（不再整包拒绝）。"
            + (f" 重叠项: {overlap}" if overlap else ""))
        return skill

    # ─── 云枢自动完成评审改进（QUAL_NO_SCHEMA / QUAL_NO_TAGS / DATA_COLLECT_SENSITIVE）───

    @staticmethod
    def _content_type_tags(content_type: str) -> List[str]:
        """按内容类型给出基础标签候选（保证至少有稳定标签可检索）。"""
        m = {
            "python": ["脚本", "python"],
            "javascript": ["脚本", "javascript"],
            "js": ["脚本", "js"],
            "shell": ["脚本", "shell"],
            "markdown": ["指令型"],
            "text": ["指令型"],
            "yaml": ["配置"],
            "json": ["配置"],
        }
        return list(m.get(str(content_type or "").lower(), ["指令型"]))

    def _derive_tags(self, skill, current: List[str]) -> List[str]:
        """由 现有标签 + 内容类型 + 自动分类 推导 2-5 个检索标签。"""
        import re as _re
        tags: List[str] = []
        for t in (current or []):
            s = str(t).strip()
            if s and s not in tags:
                tags.append(s)
        for t in self._content_type_tags(skill.content_type):
            if t not in tags:
                tags.append(t)
        try:
            cls = self._class_registry.assignment(f"asset:{skill.id}")
        except Exception:  # noqa: BLE001
            cls = None
        if cls and str(cls) != "未分类" and str(cls) not in tags:
            tags.append(str(cls))
        if len(tags) < 2:  # 兜底：名称派生的稳定 token
            name = str(skill.name or skill.id or "")
            tok = ""
            mm = _re.search(r"[a-zA-Z0-9][a-zA-Z0-9_\-]{2,}", name)
            if mm:
                tok = mm.group(0)
            else:
                cjk = _re.findall(r"[\u4e00-\u9fff]{2,}", name)
                if cjk:
                    tok = cjk[0]
            if tok and tok not in tags:
                tags.append(tok[:12])
        return tags[:5]

    @staticmethod
    def _build_config_schema(skill) -> Dict[str, Any]:
        """依 default_params 生成 config_schema（无参数则给自由任务描述型 schema）。"""
        dp = getattr(skill, "default_params", None) or {}
        if isinstance(dp, dict) and dp:
            props: Dict[str, Any] = {}
            for k, v in dp.items():
                if isinstance(v, bool):
                    t = "boolean"
                elif isinstance(v, (int, float)):
                    t = "number"
                else:
                    t = "string"
                props[str(k)] = {"type": t, "title": str(k)}
            return {"type": "object", "properties": props,
                    "additionalProperties": True}
        return {
            "type": "object",
            "properties": {},
            "additionalProperties": True,
            "description": "无固定参数：调用方可直接传入自由文本任务描述",
        }

    def apply_auto_fixes(self, skill_id: str,
                         codes: Optional[List[str]] = None) -> Dict[str, Any]:
        """云枢自动完成评审改进：补 config_schema / 标签 / is_sensitive 后重新评审。

        Args:
            skill_id: 技能 id
            codes: 限定修复哪些 code（缺省自动应用评审发现中的全部可修复项）
        """
        import json as _json
        skill = self._require(skill_id)
        fixable = {"QUAL_NO_SCHEMA", "QUAL_NO_TAGS", "DATA_COLLECT_SENSITIVE"}
        findings = list(skill.review.findings or []) if skill.review else []
        present = {f.code for f in findings}
        wanted = (set(codes) & fixable) if codes else (present & fixable)
        if not wanted:
            return {"ok": True, "applied": [], "applied_codes": [],
                    "already_fixed": [], "skipped": [], "refreshed": False}

        patch: Dict[str, Any] = {}
        applied: List[Dict[str, Any]] = []
        skipped: List[str] = []
        for code in sorted(wanted):
            if code == "DATA_COLLECT_SENSITIVE":
                if getattr(skill, "is_sensitive", False):
                    skipped.append(code)
                else:
                    patch["is_sensitive"] = True
                    applied.append({"code": code,
                                    "action": "标记 is_sensitive=true（隔离注入 + 合规处理）",
                                    "detail": "is_sensitive=true"})
            elif code == "QUAL_NO_TAGS":
                cur_tags = list(skill.tags or [])
                if len(cur_tags) >= 2:  # 已有 2-5 个标签即视为就绪
                    skipped.append(code)
                else:
                    new_tags = self._derive_tags(skill, cur_tags)
                    if set(new_tags) == set(cur_tags):
                        skipped.append(code)
                    else:
                        patch["tags"] = new_tags
                        applied.append({"code": code,
                                        "action": f"自动补齐检索标签 → {new_tags}",
                                        "detail": str(new_tags)})
            elif code == "QUAL_NO_SCHEMA":
                if getattr(skill, "config_schema", None):
                    skipped.append(code)
                else:
                    schema = self._build_config_schema(skill)
                    patch["config_schema"] = schema
                    applied.append({"code": code,
                                    "action": "自动生成 config_schema（供前端生成配置 UI）",
                                    "detail": _json.dumps(schema, ensure_ascii=False)[:200]})
        if not patch:
            return {"ok": True, "applied": [], "applied_codes": [],
                    "already_fixed": sorted(wanted), "skipped": skipped,
                    "refreshed": False}
        self.update(skill_id, patch)   # 持久化（含自动归类钩子）
        refreshed = False
        try:  # 重新权威评审-评估，刷新发现（修复项应消失）
            self.review_skill(skill_id)
            refreshed = True
        except Exception as e:  # noqa: BLE001 刷新失败不阻断返回
            logger.warning("[Service] 自动修复后重新评审失败 skill=%s: %s",
                           skill_id, e)
        return {"ok": True, "applied": applied,
                "applied_codes": [a["code"] for a in applied],
                "already_fixed": sorted(wanted - {a["code"] for a in applied}
                                        if not skipped else []),
                "skipped": skipped, "refreshed": refreshed}

    # ─── 外来安装预检 / 导入审核队列 ───

    def install_precheck(self, source: str) -> Dict[str, Any]:
        """外来安装「预检」：拉取来源 payload 并评估（重叠提示 / 安全合规阻断），不落库。

        与 install 相同的来源解析与抓取，失败返回 {ok: False, error}。
        吸收策略：与原生/已有能力重叠不再算阻断（blocked 仅指安全/合规 critical/error），
        重叠项会作为「增量吸收」提示返回（overlap_action="absorb"）。

        【S1-02 §2.3 合并预检】返回中并入 v7.2 provenance 分类（scheme/manifest →
        signed/declared/unknown + 升级路径），与既有安全预检合并而非另起炉灶
        （见 agent/descriptors/backfill.classify_install_provenance）。
        """
        try:
            scheme, payload = self.creator._installer.fetch_payload(source)  # noqa: SLF001
            payload.setdefault("source", source)
            skill = Skill.from_storage_dict(payload)
            from .assessor import SkillAssessor, detect_native_duplicates
            natives = detect_native_duplicates(
                skill.name or "", skill.description or "", skill.content or "")
            others = [s for s in self.store.list_all() if s.id != skill.id]
            assessment = SkillAssessor().assess(skill, others=others)
            provenance = self._classify_install_provenance(
                scheme, source, payload, category=skill.category)
            return {
                "ok": True,
                "scheme": scheme,
                "source": source,
                "skill_id": skill.id,
                "skill_name": skill.name or skill.id,
                "blocked": assessment.blocked,
                "overlap_action": "absorb" if natives else "",
                "native_dups": [
                    {"id": n["id"], "name": n["name"],
                     "matched": n.get("matched", [])} for n in natives],
                "findings": [
                    {"code": f.code, "severity": f.severity,
                     "category": f.category, "message": f.message}
                    for f in assessment.findings],
                "compatibility_score": assessment.compatibility_score,
                "provenance": provenance,
            }
        except Exception as e:  # noqa: BLE001 预检失败给出可读原因
            return {"ok": False, "source": source, "error": str(e)}

    @staticmethod
    def _classify_install_provenance(scheme: str, source: str,
                                     payload: Optional[Dict[str, Any]] = None,
                                     category: Any = None) -> Dict[str, Any]:
        """v7.2 provenance 分类（§2.3 manifest 合并预检）。

        懒加载 agent.descriptors.backfill（descriptors 包可被 skills_mgmt 复用，
        方向 skills_mgmt → descriptors，无环）；descriptors 不可用时降级
        unknown 提示，绝不阻断安装预检主流程（advisory）。
        """
        try:
            from agent.descriptors.backfill import classify_install_provenance
            cat = None
            if category is not None:
                cat = getattr(category, "value", category) \
                    if not isinstance(category, str) else category
            return classify_install_provenance(scheme, source,
                                               dict(payload or {}),
                                               category=cat)
        except Exception as e:  # noqa: BLE001 advisory
            logger.warning("[Service] provenance 分类失败(advisory): %s", e)
            return {"level": "unknown", "scheme": scheme, "source": source,
                    "evidence": [], "upgrade_path": "",
                    "rationale": "provenance 分类器不可用（降级 unknown）",
                    "rule": "PRV-X"}

    def _sync_descriptor_advisory(self, skill_id: str, source: str = "",
                                  skill: Any = None) -> None:
        """技能入库后同步 Descriptor（advisory：失败仅记日志，绝不阻断主流程）。

        Descriptor 台账与技能主轨同目录（隔离服务/测试 store 时随 tmp store 隔离，
        避免污染全局 data/descriptors.json——默认 store 时即全局台账路径）。

        【架构纪律】payload 在本方法内组装（主轨 skill → 文件轨 meta）后传入
        descriptors.backfill.sync_skill_descriptor——descriptors 包不反向依赖
        skills_mgmt（避免循环依赖，架构规则校验强制）。
        """
        try:
            from agent.descriptors.backfill import sync_skill_descriptor
            reg_path = None
            try:
                from pathlib import Path as _Path
                sp = getattr(self.store, "_path", None)
                if sp:
                    reg_path = str(_Path(sp).resolve().parent / "descriptors.json")
            except Exception:  # noqa: BLE001
                reg_path = None
            # 组装 payload：优先调用方持有的 Skill 对象 → 主轨 → 文件轨
            payload = None
            if skill is not None and hasattr(skill, "to_storage_dict"):
                try:
                    payload = skill.to_storage_dict()
                except Exception:  # noqa: BLE001
                    payload = None
            if payload is None:
                try:
                    rec = self.store.get(skill_id)
                    if rec is not None:
                        payload = rec.to_storage_dict()
                except Exception:  # noqa: BLE001
                    payload = None
            if payload is None:
                try:
                    meta = self.file_store.get_metadata(skill_id) or {}
                    if meta:
                        payload = dict(meta)
                        payload.setdefault("id", skill_id)
                except Exception:  # noqa: BLE001
                    payload = None
            sync_skill_descriptor(skill_id, source=source,
                                  actor="skill_install", payload=payload,
                                  registry_path=reg_path)
        except Exception as e:  # noqa: BLE001
            logger.warning("[Service] 技能 descriptor 同步失败(advisory) "
                           "skill=%s: %s", skill_id, e)

    def import_queue(self, limit: int = 200) -> List[Dict[str, Any]]:
        """外部导入·待人工放行队列：外来源（external_agent / scheme:…）的草稿，最新在前。"""
        prefixes = ("github:", "url:", "local:", "registry:", "http:", "https:")
        rows = []
        for s in self.store.list_all():
            status = getattr(s.status, "value", s.status)
            if status != SkillStatus.DRAFT.value:
                continue
            src = str(s.source or "")
            if src in ("", "manual") or src in ("ai_assisted", "workflow"):
                continue
            if src != "external_agent" and not src.startswith(prefixes):
                continue
            rv = s.review
            # 【S1-02】外来导入队列并入 v7.2 provenance 建议（合并预检，advisory）
            prov_scheme = next(
                (p.rstrip(":") for p in ("github:", "url:", "local:",
                                          "registry:", "market:")
                 if src.startswith(p)), src)
            provenance = self._classify_install_provenance(prov_scheme, src)
            rows.append({
                "id": s.id,
                "name": s.name or s.id,
                "description": s.description or "",
                "source": src,
                "content_type": getattr(s.content_type, "value", s.content_type),
                "enabled": bool(s.enabled),
                "status": status,
                "created_at": s.created_at or "",
                "provenance": provenance,
                "review": None if rv is None else {
                    "auto_assessed": bool(getattr(rv, "auto_assessed", False)),
                    "review_verdict": getattr(rv.review_verdict, "value",
                                              rv.review_verdict) or "",
                    "blocked": (getattr(rv, "review_verdict", "") or "") == "block",
                    # 兼容旧键（≤1 minor）：评审语义旧名 digest_verdict
                    "digest_verdict": getattr(rv.digest_verdict, "value",
                                              rv.digest_verdict) or "",
                },
            })
        rows.sort(key=lambda r: str(r.get("created_at", "")), reverse=True)
        return rows[:max(1, min(int(limit), 500))]

    def install_from_zip(self, zip_path: str) -> Dict[str, Any]:
        """从 zip 技能包安装到三层架构文件仓库

        Args:
            zip_path: zip 文件路径

        Returns:
            dict — {skill_id, name, version, scripts_count, provenance}
        """
        from .skill_manager import SkillManager
        mgr = SkillManager(repo_path=str(self.file_store.repo_path))
        skill_id = mgr.install_from_zip(zip_path)
        meta = self.file_store.get_metadata(skill_id) or {}
        scripts = self.file_store.list_scripts(skill_id)
        result = {
            "skill_id": skill_id,
            "name": meta.get("name", skill_id),
            "version": meta.get("version", "0.0.0"),
            "scripts_count": len(scripts),
        }
        # 【S1-02 接线】zip 外来技能同步 Descriptor（advisory 不阻断安装）
        self._sync_descriptor_advisory(skill_id, source="zip")
        return result

    # ─── 审核 ───

    def review(self, skill_id: str) -> ReviewResult:
        """审核指定技能 (与所有其他技能做重复检测)"""
        with traced_action("svc_review", skill_id=skill_id):
            skill = self._require(skill_id)
            others = [s for s in self.store.list_all() if s.id != skill_id]
            result = self.reviewer.review(skill, others=others)
            self._merge_script_review(skill, result)  # 脚本文件全维度审查并入
            self.store.upsert(skill)  # 持久化审核结果
            self._emit_assessment_event(
                skill_id, "review",
                getattr(result.review_verdict, "value", result.review_verdict)
                or getattr(result.status, "value", result.status),
                result.summary)
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

    # ─── 评审-评估（自动评估钩子 + 批量）───

    def _advisory_assess(self, skill_id: str) -> Optional[ReviewResult]:
        """新增/外来技能进入时自动执行扩展评估（权限/攻击面/数据合规 + 兼容性）。

        设计（守现有契约）：
            - 仅在技能尚无 review 时写入咨询性报告（status 保持 draft 等原语义），
              不改技能/审核状态——正式审核（review/review_skill）才是权威判据；
            - 自动钩子在 create_via_ai / create_manual / install / update 后触发，
              让“新增及现有技能”一进来就有评估报告可见。
        """
        try:
            skill = self._require(skill_id)
            if skill.review is not None:
                return skill.review
            from .assessor import SkillAssessor
            others = [s for s in self.store.list_all() if s.id != skill_id]
            assessment = SkillAssessor().assess(skill, others=others)
            skill.review = ReviewResult(
                status=ReviewStatus.PENDING,
                findings=assessment.findings,
                compatibility_score=assessment.compatibility_score,
                auto_assessed=True,
                review_verdict="block" if assessment.blocked else "ok",
                dimension_summary=assessment.dimension_summary,
                summary=("自动评审-评估：扩展评估未发现阻断项，可执行正式审核后发布"
                         if not assessment.blocked
                         else "自动评审-评估：扩展评估存在阻断项(权限/合规/兼容性)，待人工复核"),
            )
            self._merge_script_review(skill, skill.review)  # 脚本文件审查并入
            skill.touch()
            self.store.upsert(skill)
            self._emit_assessment_event(
                skill.id, "auto",
                getattr(skill.review.review_verdict, "value", skill.review.review_verdict),
                skill.review.summary)
            return skill.review
        except Exception as e:  # noqa: BLE001 评估失败不阻断创建/安装主流程
            logger.warning("[Service] advisory assess 失败 skill=%s: %s",
                           skill_id, e)
            return None

    # ─── 自动分类（新技能自动归类/建类；同处注册表与运行时共用）───

    def _auto_classify(self, skill_id: str) -> str:
        """新技能进入后自动归类（创建/安装/更新钩子调用；失败不阻断主流程）。

        若同一 id 也存在于运行时生态（rt:），自动把分类镜像过去保持两侧一致。
        """
        try:
            skill = self._require(skill_id)
            cls = self._class_registry.resolve(
                f"asset:{skill_id}",
                name=skill.name or skill_id,
                description=skill.description or "",
                content=skill.content or "",
                tags=list(skill.tags or []))
            try:
                self._class_registry.mirror(f"asset:{skill_id}",
                                            f"rt:{skill_id}")
            except Exception:  # noqa: BLE001 镜像失败不影响资产侧
                pass
            return cls
        except Exception as e:  # noqa: BLE001 分类是附加能力
            logger.debug("[Service] 自动分类失败 skill=%s: %s", skill_id, e)
            return "未分类"

    def _asset_dicts(self) -> List[Dict[str, Any]]:
        """把资产库技能转成分类引擎所需字段视图（不落库）。"""
        out = []
        for s in self.store.list_all():
            out.append({
                "id": s.id, "name": s.name or s.id,
                "description": s.description or "",
                "content": s.content or "",
                "content_type": getattr(s.content_type, "value", s.content_type),
                "tags": list(s.tags or []),
                "status": getattr(s.status, "value", s.status),
                "enabled": bool(s.enabled),
                "version": s.version or "",
            })
        return out

    def skill_classes(self) -> Dict[str, Any]:
        """资产库分类视图：先补齐未归类项（幂等落盘），再按类分组（倒序）。"""
        views = self._asset_dicts()
        for v in views:
            self._class_registry.resolve(
                f"asset:{v['id']}", name=v.get("name", ""),
                description=v.get("description", ""),
                content=v.get("content", ""), tags=v.get("tags"))
        return self._class_registry.group_summary(views, ns="asset")

    def run_auto_classify(self) -> Dict[str, Any]:
        """全量自动分类：只为尚未归类的技能判定（人工移动不受影响）。"""
        return self._class_registry.run_auto(self._asset_dicts(), ns="asset")

    def move_class(self, skill_id: str, class_name: str) -> Dict[str, Any]:
        """人工移动技能到指定类（种子类/已建自动类/未分类 均可）。"""
        from .categorizer import SEED_NAMES, UNCLASSIFIED
        skill = self._require(skill_id)
        target = str(class_name or "").strip()
        known = set(SEED_NAMES) | {UNCLASSIFIED}
        known |= set(self._class_registry.snapshot().get("auto_classes", {}))
        if target not in known:
            raise SkillMgmtError(
                f"未知分类: {target}（可用: {sorted(known)[:12]}…）")
        self._class_registry.assign(f"asset:{skill_id}", target)
        # 【不易】必须**同时钉住运行时键** `rt:<id>`，不能只 mirror 过去。
        #   根因（2026-09-19 实测）：`SkillClassRegistry.resolve()` 对**非 manual** 的既有
        #   归类允许"置信命中即覆盖"，而 `mirror` 只写值、不写 manual ⇒ 人工移动完，
        #   下一次 `GET /api/skills`（技能库页的数据源）走 `resolve('rt:*')` 时按关键词
        #   重新打分又把它覆盖回旧类：技能中心（读 asset 视图）显示新类，
        #   技能库页（读 rt 视图）仍显示旧类 —— 实测「writing-skills → 代码与工程」即如此。
        #   钉住 rt 后，`resolve` 第 353 行的 "key in manual ⇒ 原样返回" 才是真的生效。
        try:
            self._class_registry.assign(f"rt:{skill_id}", target)
        except Exception:  # noqa: BLE001 运行时键写失败不阻断资产侧已生效的人工移动
            pass
        logger.info("[Service] 技能 %s 人工移动至分类 %s", skill_id, target)
        return {"ok": True, "skill_id": skill_id, "class_name": target}

    def review_skill(self, skill_id: str) -> ReviewResult:
        """对单个技能执行权威「评审」= 完整审核链（三审 + 扩展评估）。

        即 review() 的语义化命名入口；扩展阻断项会把审核状态置 WARN、
        技能置 PENDING_REVIEW（发布门禁保持）。
        术语纪律（TASK-S0-01）：评审语义，与 v7.2 七态内化语义（Internalization）无关。
        """
        return self.review(skill_id)

    def digest_skill(self, skill_id: str) -> ReviewResult:
        """已废弃兼容别名（旧名 digest_skill，评审语义，与 v7.2 内化语义无关）。

        请改用 review_skill()（二者等价：均为权威评审，含三审 + 扩展评估）。
        """
        return self.review_skill(skill_id)

    def assess_all(self) -> Dict[str, Any]:
        """对“尚无审核结果的现有技能”批量执行咨询性自动评估。

        Returns:
            {total, assessed, blocked, with_review} — assessed 为本次新增评估数；
            blocked 为存在阻断项的技能数。已有人工/正式审核记录的不覆盖。
        """
        total = 0
        assessed = 0
        blocked = 0
        for s in self.store.list_all():
            total += 1
            if s.review is not None:
                continue
            r = self._advisory_assess(s.id)
            if r is not None:
                assessed += 1
                if r.review_verdict == "block":
                    blocked += 1
        return {"total": total, "assessed": assessed, "blocked": blocked}

    def digest_all(self) -> Dict[str, Any]:
        """已废弃兼容别名（旧名 digest_all，评审语义，与 v7.2 内化语义无关）。

        请改用 assess_all()：对尚无审核结果的现有技能批量执行咨询性自动评估。
        """
        return self.assess_all()

    def audit_log(self, limit: int = 100, skill_id: str = "",
                  offset: int = 0, since: str = "") -> List[Dict[str, Any]]:
        """读取人工复核/强制发布审计记录（供技能中心可视化；可按技能/分页/时段筛选）"""
        try:
            from .review_gate import read_audit_log
            return read_audit_log(limit=limit, skill_id=skill_id,
                                  offset=offset, since=since)
        except Exception as e:  # noqa: BLE001
            logger.warning("[Service] 读取审计日志失败: %s", e)
            return []

    # ─── 评估结果事件（轻量推送源：追加 data/skills_assessment_events.jsonl）───
    # 术语纪律（TASK-S0-01）：云枢旧 digest（评审语义）已更名 review/assess，
    # 事件文件新名为主；旧名 data/skills_digest_events.jsonl 只读兼容（≤1 minor）。

    def _emit_assessment_event(self, skill_id: str, kind: str, verdict: str,
                               summary: str = "") -> None:
        """记录一次评估结果事件（新评估/阻断等），供面板轮询展示。

        【TASK-S8-02】追加写改走 `log_archiver.append_jsonl_locked()`
        （持跨进程锁 + 单次 os.write，多进程不撕行）；载荷形状逐字不变
        （ts / kind / skill_id / verdict / summary），仍为 best-effort。
        """
        try:
            import json as _json
            from datetime import datetime as _dt
            from .log_archiver import active_events_file, append_jsonl_locked
            events_file = active_events_file()
            events_file.parent.mkdir(parents=True, exist_ok=True)
            rec = {
                "ts": _dt.now().isoformat(timespec="seconds"),
                "kind": kind,          # auto | review
                "skill_id": skill_id,
                "verdict": verdict or "",
                "summary": str(summary or "")[:400],
            }
            append_jsonl_locked(events_file, _json.dumps(rec, ensure_ascii=False))
            # S2-02：评估/评审结果事件同步进链（旧 JSONL 轨保持不变，双写过渡）
            try:
                from agent.audit import audit as _audit_facade
                _audit_facade.record(
                    f"skill.assess.{kind or 'unknown'}", actor="agent",
                    subject=f"skill:{skill_id}",
                    payload={"verdict": verdict or "", "summary": str(summary or "")[:400],
                             "legacy": str(events_file)},
                    source="agent", status=str(verdict or ""))
            except Exception as _ae:  # noqa: BLE001 审计失败不影响评估
                logger.debug("[Service] 链式审计留痕失败 skill=%s: %s", skill_id, _ae)
        except Exception as e:  # noqa: BLE001
            logger.debug("[Service] 评估事件写入失败 skill=%s: %s", skill_id, e)

    def assessment_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        """读取最近评估事件（最新在前；面板“评估动态”）。

        新文件不存在时兜底读旧名 live 文件（迁移失败/旧版本残留场景）。
        """
        try:
            import json as _json
            from .log_archiver import (
                archive_daily_file, active_events_file,
                LEGACY_DIGEST_EVENTS_BASENAME)
            events_file = active_events_file()
            archive_daily_file(events_file)  # 按日归档（进程内每日一次）
            candidates = [events_file]
            if not events_file.exists():
                legacy = events_file.parent / LEGACY_DIGEST_EVENTS_BASENAME
                if legacy.exists():
                    candidates = [legacy]
            records = []
            for ef in candidates:
                if not ef.exists():
                    continue
                with open(ef, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = _json.loads(line)
                        except Exception:  # 坏行跳过
                            continue
                        if isinstance(rec, dict):
                            records.append({
                                "ts": rec.get("ts", ""),
                                "kind": rec.get("kind", ""),
                                "skill_id": rec.get("skill_id", ""),
                                "verdict": rec.get("verdict", ""),
                                "summary": rec.get("summary", ""),
                            })
            return records[-max(1, limit):][::-1]
        except Exception as e:  # noqa: BLE001
            logger.warning("[Service] 读取评估事件失败: %s", e)
            return []

    def digest_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        """已废弃兼容别名（旧名 digest_events，评审语义，与 v7.2 内化语义无关）。

        请改用 assessment_events()：读取最近评估事件。
        """
        return self.assessment_events(limit=limit)

    def assessment_events_since(self, since: str = "",
                                timeout_ms: int = 20000) -> List[Dict[str, Any]]:
        """实时推送源（长轮询）：返回 ts > since 的新增评估事件（时间序）。

        请求方循环调用即可获得“服务端推送”体验；超时无新事件返回空列表。
        """
        try:
            import json as _json
            import time as _time
            from .log_archiver import (
                archive_daily_file, active_events_file,
                LEGACY_DIGEST_EVENTS_BASENAME)
            events_file = active_events_file()
            archive_daily_file(events_file)  # 按日归档（进程内每日一次）
            candidates = [events_file]
            if not events_file.exists():
                legacy = events_file.parent / LEGACY_DIGEST_EVENTS_BASENAME
                if legacy.exists():
                    candidates = [legacy]
            deadline = _time.time() + max(1000, min(int(timeout_ms), 25000)) / 1000.0
            while _time.time() < deadline:
                recs: List[Dict[str, Any]] = []
                for ef in candidates:
                    if not ef.exists():
                        continue
                    with open(ef, "r", encoding="utf-8", errors="ignore") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                rec = _json.loads(line)
                            except Exception:
                                continue
                            if not isinstance(rec, dict):
                                continue
                            if since and str(rec.get("ts", "")) <= since:
                                continue
                            recs.append({
                                "ts": rec.get("ts", ""),
                                "kind": rec.get("kind", ""),
                                "skill_id": rec.get("skill_id", ""),
                                "verdict": rec.get("verdict", ""),
                                "summary": rec.get("summary", ""),
                            })
                if recs:
                    return recs  # 时间序（文件顺序）
                _time.sleep(1)
            return []
        except Exception as e:  # noqa: BLE001
            logger.warning("[Service] 事件长轮询失败: %s", e)
            return []

    def digest_events_since(self, since: str = "",
                            timeout_ms: int = 20000) -> List[Dict[str, Any]]:
        """已废弃兼容别名（旧名 digest_events_since，评审语义，与 v7.2 内化语义无关）。

        请改用 assessment_events_since()：评估事件实时推送源（长轮询）。
        """
        return self.assessment_events_since(since=since, timeout_ms=timeout_ms)

    def merge_with_backup(self, src_id: str, dst_id: str,
                          strategy: str = "auto",
                          rebind_feedback: bool = True) -> Dict[str, Any]:
        """「安全合并」：合并前把两侧完整快照写入 sidecar，便于一键撤销。

        - 若 dst 存在版本链，先追加一个 pre-merge 版本标记（尽力而为，失败不阻断）；
        - sidecar: data/skill_merge_backups.jsonl
          （merge_id → src_snapshot + dst_snapshot + dst_before）。

        [F10] 记录里同时写两种 dst 快照，各有各的用处，**不是冗余**：
          - `dst_snapshot` = `dst.model_dump()`（**完整实体**）—— 供 `undo_merge` 在
            「保留方后来也被删除」时**整条重建**（判据 `dst_snapshot["id"] == dst_id`）；
          - `dst_before` = 下面那 8 个字段 —— 供「保留方仍存在」时**逐字段回滚内容**。
        只有 `dst_before` 时无法重建：它**不含 id / status / category / version /
        时间戳**，凭它造出的技能只能靠猜补齐主键与元数据。
        """
        import os as _os
        import json as _json
        import uuid as _uuid
        from datetime import datetime as _dt

        src = self._require(src_id)
        dst = self._require(dst_id)
        merge_id = f"{_uuid.uuid4().hex[:8]}-{int(_dt.now().timestamp())}"
        rec = {
            "merge_id": merge_id,
            "ts": _dt.now().isoformat(timespec="seconds"),
            "src_id": src_id, "dst_id": dst_id,
            "src_snapshot": src.model_dump(),
            # [F10] 保留方的**完整实体**快照：undo_merge 在「dst 也被删除」时靠它整条重建。
            # 旧记录没有这个字段（本卡之前写的 sidecar），undo_merge 对此**向前兼容**
            # ——读不到就退回「不重建」，绝不抛异常（见 undo_merge 的 dst 重建分支）。
            "dst_snapshot": dst.model_dump(),
            "dst_before": {
                k: getattr(dst, k) for k in (
                    "name", "description", "content", "content_type",
                    "enabled", "default_params", "config_schema", "tags")
            },
        }
        try:
            sidecar = _os.path.join(
                _os.path.dirname(_os.path.dirname(_os.path.dirname(
                    _os.path.abspath(__file__)))),
                "data", "skill_merge_backups.jsonl")
            _os.makedirs(_os.path.dirname(sidecar), exist_ok=True)
            with open(sidecar, "a", encoding="utf-8") as f:
                f.write(_json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as e:  # noqa: BLE001 备份失败不阻断合并（记录告警）
            logger.warning("[Service] 合并备份写入失败 %s→%s: %s",
                           src_id, dst_id, e)

        result = self.merge_duplicate_skills(
            src_id, dst_id, strategy=strategy,
            rebind_feedback=rebind_feedback)
        self._emit_assessment_event(
            src_id, "merge", "ok",
            f"安全合并：{src_id} → {dst_id}（备份 merge_id={merge_id}）")
        return {"merge_id": merge_id, **result}

    def list_merge_backups(self, limit: int = 50) -> List[Dict[str, Any]]:
        """列出最近的安全合并备份（不含完整快照，含名称与内容规模）"""
        import os as _os
        import json as _json
        sidecar = _os.path.join(
            _os.path.dirname(_os.path.dirname(_os.path.dirname(
                _os.path.abspath(__file__)))),
            "data", "skill_merge_backups.jsonl")
        records: List[Dict[str, Any]] = []
        if not _os.path.exists(sidecar):
            return records
        with open(sidecar, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = _json.loads(line)
                except Exception:
                    continue
                if not isinstance(item, dict):
                    continue
                snap = item.get("src_snapshot") or {}
                dst = item.get("dst_before") or {}
                records.append({
                    "merge_id": item.get("merge_id", ""),
                    "ts": item.get("ts", ""),
                    "src_id": item.get("src_id", ""),
                    "dst_id": item.get("dst_id", ""),
                    "src_name": snap.get("name") or item.get("src_id", ""),
                    "dst_name": dst.get("name") or item.get("dst_id", ""),
                    "src_content_len": len(str(snap.get("content", "") or "")),
                    "dst_content_len": len(str(dst.get("content", "") or "")),
                })
        return records[-max(1, limit):][::-1]

    def assessment_feed(self, limit: int = 100, offset: int = 0,
                        skill_id: str = "") -> List[Dict[str, Any]]:
        """“全部动态”聚合流：评估事件 + 人工复核审计，按时间倒序 + 分页。

        skill_id 非空时仅保留该技能（精确 id 或包含匹配）相关记录。
        术语纪律（TASK-S0-01）：记录 kind 由旧 digest 更名 assess（评审语义）。
        """
        need = max(1, limit) + max(0, offset)
        events = self.assessment_events(limit=need)
        audit = self.audit_log(limit=need)
        feed: List[Dict[str, Any]] = []
        for e in events:
            feed.append({"kind": "assess", "ts": e.get("ts", ""),
                         "skill_id": e.get("skill_id", ""),
                         "tag": e.get("verdict", ""),
                         "detail": e.get("summary", "")})
        for a in audit:
            feed.append({"kind": "audit", "ts": a.get("ts", ""),
                         "skill_id": a.get("skill_id", ""),
                         "tag": "强制发布",
                         "detail": f"复核人 {a.get('actor', '')}：{a.get('reason', '')}"})
        if skill_id:
            q = str(skill_id).strip().lower()
            feed = [r for r in feed if q in str(r.get("skill_id", "")).lower()]
        feed.sort(key=lambda r: str(r.get("ts", "")), reverse=True)
        return feed[offset: offset + max(1, limit)]

    def digest_feed(self, limit: int = 100, offset: int = 0,
                    skill_id: str = "") -> List[Dict[str, Any]]:
        """已废弃兼容别名（旧名 digest_feed，评审语义，与 v7.2 内化语义无关）。

        请改用 assessment_feed()：“全部动态”聚合流（评估事件 + 人工复核审计）。
        """
        return self.assessment_feed(limit=limit, offset=offset, skill_id=skill_id)

    def slash_commands(self) -> Dict[str, Any]:
        """斜杠命令注册表：把“已发布且启用”的技能注册为会话 `/skill:<id>` 命令。"""
        commands = []
        for s in self.store.list_all():
            status = getattr(s.status, "value", s.status)
            if not s.enabled or status not in ("published", "approved"):
                continue
            commands.append({
                "token": f"/skill:{s.id}",
                "id": s.id,
                "name": s.name or s.id,
                "description": (s.description or "")[:120],
                "kind": "skill",
                "content_type": getattr(s.content_type, "value", s.content_type),
                "category": getattr(s.category, "value", s.category),
                "version": s.version or "",
            })
        commands.sort(key=lambda c: c["token"])
        return {"total": len(commands), "commands": commands}

    #: [F9] `undo_merge` 在「dst 仍存在」情形下**必须保留现值**的治理类字段。
    #:
    #: 撤销合并是**内容回滚**，不得把合并前快照里的治理状态静默写回，否则会重现
    #: 「操作员停用 B → 撤销该次合并 → B 被静默重新启用」，而链上只有
    #: `skill.assess.merge-undo` 一条评估事件、没有 `skill.registry.set_enabled`
    #: 启停痕（这就是 F9）。
    #:
    #: 取舍口径（依据 `merge_with_backup` 的快照字段清单与 `update()` 白名单）：
    #:   - `enabled` —— 必列。它是 `dst_before` 的 8 个字段
    #:     （name/description/content/content_type/enabled/default_params/
    #:     config_schema/tags）里唯一的治理类字段，也是本仓唯一有启停审计动作族
    #:     （`skill.registry.set_enabled`）的字段；
    #:   - `is_sensitive` / `isolation_strategy` —— `update()` 白名单里单独标注的
    #:     「[变易] 敏感技能隔离」字段（决定技能**如何运行/注入**，不是正文内容），
    #:     与 `enabled` 同属「决定技能是否/如何运行」的治理决策 ⇒ 一并列入。
    #:     注意：它们**当前不在** `dst_before` 的字段清单内，故对本仓今天的快照
    #:     无可观测影响，属**零副作用的前瞻性防护**（快照将来若扩到全字段，
    #:     不会退化出同一类静默治理变更）；
    #:   - 未列入 `status` / `category` / `tags` / `version`：它们不是启用/隔离
    #:     治理决策（`status` 是评审工作流状态），且除 `tags` 外均不在快照字段
    #:     清单内；凭感觉扩大口径会掩盖将来「确实该恢复」的字段。
    #:
    #: 生效范围仅限「dst 仍存在」的分支：技能已被删除、要靠快照重建时没有「现有
    #: 治理状态」可保护，快照里的 `enabled` 等照常恢复（见 `undo_merge` 两个分支）。
    _UNDO_MERGE_KEEP_FIELDS = frozenset(
        {"enabled", "is_sensitive", "isolation_strategy"})

    def undo_merge(self, merge_id: str) -> Dict[str, Any]:
        """按 merge_id 撤销「安全合并」：恢复被删除技能 + 恢复保留方快照。

        [F9] 语义边界——本方法是**内容回滚**，不是治理状态回滚：
            - 情形一（dst 仍存在）：只把快照的内容字段套回现值，治理类字段
              `_UNDO_MERGE_KEEP_FIELDS`（至少含 `enabled`）保持操作员现值；
            - 情形二（src/dst 已被删除，只能用快照重建）：没有「现有治理状态」
              可保护，快照里的 `enabled` 等**照常恢复**，重建结果与合并前一致。

        [F10] 情形二的 **dst 侧**此前不可达（判据取错了快照，见下方注释），
        现读 `merge_with_backup` 同批写入的 `dst_snapshot` 重建；旧格式记录
        （无 `dst_snapshot`）退回本卡之前的行为且**不抛异常**。

        签名与返回结构不变：`{"ok", "merge_id", "restored", "note"}`。
        """
        import os as _os
        import json as _json

        sidecar = _os.path.join(
            _os.path.dirname(_os.path.dirname(_os.path.dirname(
                _os.path.abspath(__file__)))),
            "data", "skill_merge_backups.jsonl")
        rec = None
        if _os.path.exists(sidecar):
            with open(sidecar, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = _json.loads(line)
                    except Exception:
                        continue
                    if isinstance(item, dict) and item.get("merge_id") == merge_id:
                        rec = item
        if not rec:
            raise SkillMgmtError(f"未找到合并备份记录: {merge_id}")

        src_id = str(rec.get("src_id", ""))
        dst_id = str(rec.get("dst_id", ""))
        restored = []
        # [F10] 保留方已不存在、且记录里没有可用快照 ⇒ 无法自动重建（见下方 dst 分支）
        dst_rebuild_skipped = False
        # 1) 恢复被删除的 src（若已存在则跳过）
        if src_id:
            try:
                self._require(src_id)
            except SkillNotFoundError:
                snap = rec.get("src_snapshot")
                if isinstance(snap, dict):
                    skill = Skill.from_storage_dict(snap)
                    self.store.upsert(skill)
                    self._advisory_assess(src_id)
                    restored.append(src_id)
        # 2) 恢复 dst 到合并前快照（只回滚**内容**，不回滚**治理状态**）
        if dst_id:
            before = rec.get("dst_before") or {}
            try:
                cur = self._require(dst_id)
                data = cur.model_dump()
                # [F9] dst 仍存在 ⇒ 存在「现有治理状态」需要保护：快照里的治理类
                # 字段（_UNDO_MERGE_KEEP_FIELDS，至少含 enabled）一律跳过、保持现值。
                # 内容回滚不得顺带改启停/隔离状态 —— 这种连带变更既无声（链上不会
                # 出现 skill.registry.set_enabled 记录）也违背本方法的语义。
                for k, v in before.items():
                    if k in self._UNDO_MERGE_KEEP_FIELDS:
                        continue
                    data[k] = v
                skill = Skill.from_storage_dict(data)
                skill.touch()
                self.store.upsert(skill)
                self._advisory_assess(dst_id)
                restored.append(dst_id)
            except SkillNotFoundError:
                # 保留方也不存在（后续又被删除）→ 用快照完整重建。
                # 此时**没有「现有治理状态」可保护** ⇒ 快照里的 enabled 等一并恢复
                # （与上面的情形一相反，这里刻意不做保留字段过滤）；
                # `_UNDO_MERGE_KEEP_FIELDS` 的注释已写明它的生效范围**仅限「dst 仍存在」
                # 的分支，故这里不受它约束。
                #
                # [F10] 判据必须是**保留方自己的**快照：
                #   `merge_with_backup` 写的 `src_snapshot` 是 `src.model_dump()`，
                #   其 `id` **恒等于 `src_id`**；而 `merge_duplicate_skills` 明确禁止
                #   `src_id == dst_id` ⇒ 旧判据 `src_snapshot["id"] == dst_id` **恒为假**，
                #   本分支在真实合并记录下**不可达**，dst 被静默少恢复且不报错。
                #   故改读 `dst_snapshot`（保留方完整实体，`merge_with_backup` 同批写入）。
                snap = rec.get("dst_snapshot")
                if isinstance(snap, dict) and snap.get("id") == dst_id:
                    self.store.upsert(Skill.from_storage_dict(snap))
                    restored.append(dst_id)
                else:
                    # [F10] 向前兼容：本卡之前写入的 sidecar 记录**没有** `dst_snapshot`，
                    # 此时**原样退回本卡之前的行为**（判据 `src_snapshot["id"] == dst_id`）。
                    # 为什么不是「直接放弃重建」：退回旧行为对**真实**旧记录与本卡之前
                    # 逐字等价（旧判据在真实记录下恒为假 ⇒ 同样不重建），却保留了
                    # 「按旧语义刻意构造的记录」的既有行为面（F9 单测用例 6 就是这种记录），
                    # 改动面因此收窄为「只让新记录的那一半从不可达变为可达」。
                    legacy = rec.get("src_snapshot")
                    if isinstance(legacy, dict) and legacy.get("id") == dst_id:
                        self.store.upsert(Skill.from_storage_dict(legacy))
                        restored.append(dst_id)
                    else:
                        # 两条路都走不通 ⇒ 「不重建 + 明确留痕」，**不抛异常**。
                        # 为什么不拿 `dst_before` 的 8 个残字段拼一个技能：它不含
                        # id/status/category/version/时间戳，只能靠默认值猜补 ⇒ 会凭空
                        # 造出一个**看起来真实**的技能（status=draft、version=0.1.0、
                        # category=custom），比「少恢复一个」更糟 —— 与审计口径
                        # 「宁可少记，不可记假」同源：宁可少恢复，不可造假技能。
                        # 操作员仍可用 `dst_before` 的正文手工新建，且本方法把
                        # 「无法自动重建」同时写进日志与评估事件，不至于静默。
                        # 文案避开非 GBK 字符（如 U+21D2）：Windows 控制台日志流是 GBK，
                        # 实测 `⇒` 会让 logging 抛 UnicodeEncodeError 并把这条告警吞掉。
                        logger.warning(
                            "[Service] 撤销合并 %s：保留方 %s 已不存在，且该 sidecar 记录"
                            "没有 dst_snapshot（旧格式），其 src_snapshot.id 也不是该 dst"
                            " => 无法自动重建，需人工恢复（记录里 dst_before 有合并前正文）",
                            merge_id, dst_id)
                        dst_rebuild_skipped = True
        # [F10] 未能重建保留方时必须在事件里说清（否则就是「静默少恢复一个技能」）
        skip_note = (
            f"；⚠ 保留方 {dst_id} 已不存在，且该备份记录无可用快照"
            "（本卡之前的旧格式记录）⇒ 未能自动重建，需人工用记录里的 "
            "dst_before 正文恢复" if dst_rebuild_skipped else "")
        self._emit_assessment_event(
            dst_id, "merge-undo", "ok",
            f"撤销合并 {merge_id}：恢复 {'、'.join(restored) or '-'}"
            "（内容快照回滚，不回滚已有技能的治理状态）" + skip_note)
        return {"ok": True, "merge_id": merge_id,
                "restored": restored,
                "note": "已恢复 src 并回滚 dst 到合并前；治理状态（enabled 等）"
                        "不随内容回滚，如需恢复启用态请重新启停（该次启停会写入"
                        "启停审计）；建议重新评审-评估后决定去向。"}

    # ─── 自动修复建议（评审发现 → 对策 / AI patch 入口）───

    def suggest_fixes(self, skill_id: str) -> Dict[str, Any]:
        """把 skill 最新评审的阻断/警告发现映射为“修复建议”。

        规则映射（确定性）：可据此人工修改，或接 LLM 自动改写 content。
        Returns: {skill_id, fixes: [{code, severity, finding, fix}]}
        """
        skill = self._require(skill_id)
        fixes: List[Dict[str, Any]] = []
        seen: set = set()
        map_codes = {
            "SEC_CMD_INJECTION": "移除/替换危险 shell 命令（rm -rf / fork bomb 等），改白名单+参数化执行",
            "SEC_EVAL": "避免 eval/exec：改为显式分支或 ast.literal_eval 解析受控输入",
            "SEC_OS_SYSTEM": "用 subprocess 列表传参（勿 shell=True），并对命令做白名单",
            "SEC_POPEN": "改用 subprocess.run(cmd, shell=False, capture_output=True)",
            "SEC_XSS_INNERHTML": "禁止直接 innerHTML 赋值，改用 textContent 或转义",
            "SEC_XSS_DOC_WRITE": "移除 document.write，改用 DOM 构建/框架转义",
            "SEC_SQL_CONCAT": "SQL 一律参数化（? 占位 + 绑定参数），禁止字符串拼接",
            "SEC_HARDCODED_SECRET": "密钥移出代码：引用环境变量或统一密钥服务，禁止硬编码",
            "SEC_DANGEROUS_IMPORT": "移除 pickle/marshal 反序列化，改用 JSON 等安全格式",
            "SEC_NETWORK_BACKDOOR": "禁止监听 0.0.0.0 的服务端代码；确认网络用途",
            "SEC_PATH_TRAVERSAL": "路径先 resolve 并校验在允许目录内，禁止 ../ 拼接",
            "SEC_SSL_UNVERIFIED": "开启证书校验（移除 verify=False）；内网确需时限定主机",
            "SEC_ENV_EXFIL": "勿把环境变量/密钥随请求外发；需要时仅发送最小化脱敏字段",
            "SEC_OBFUSCATED": "改写为可读明文代码（去掉 base64/hex 拼接动态执行）",
            "SEC_DANGEROUS_DEP": "移除/替换危险依赖（keylogger/backdoor/reverse_shell 类）",
            "DATA_PII": "示例/内容中的手机号、身份证、邮箱改为脱敏占位（如 138****8000）",
            "DATA_SECRET_IN_PARAMS": "default_params 不存真实密钥：改引用环境/密钥服务",
            "DATA_COLLECT_SENSITIVE": "标记 is_sensitive=true 启用隔离注入，并补齐数据用途说明",
            "RSC_LOOP_NO_EXIT": "while True 增加退出条件/上限或改用有界循环",
            "RSC_SUBPROCESS_NO_TIMEOUT": "子进程调用增加 timeout 与错误处理",
            "RSC_NET_NO_TIMEOUT": "网络请求增加 timeout（如 timeout=10）",
            "QUAL_SHORT_DESC": "补充 ≥20 字中文说明：用途、何时触发、输入输出",
            "QUAL_THIN_CONTENT": "补充示例与边界说明，避免内容过短",
        }
        review = skill.review
        if review:
            for f in review.findings:
                code = str(f.code or "")
                if code in seen or code not in map_codes:
                    continue
                seen.add(code)
                fixes.append({
                    "code": code,
                    "severity": f.severity,
                    "finding": f.message,
                    "fix": map_codes[code],
                })
        return {"skill_id": skill_id, "fixes": fixes,
                "count": len(fixes),
                "note": "规则化对策；可对 content 手动应用后重新「评审-评估」"}

    # ─── 老技能整理（整理/补齐描述/清理/合并/拆分建议）───

    def curate_skills(self, dry_run: bool = True,
                      auto_clean: bool = False) -> Dict[str, Any]:
        """对存量技能一键体检：产出整理计划（可安全自动执行部分）。

        自动安全动作（auto_clean=True 时执行，dry_run=False 语义）：
            - 空中文说明 → 从内容/名称生成占位说明并写回
            - 长期停用且零使用（>30 天未更新）→ 标记 ARCHIVED（归档不删除）
        计划项（需人工/仍建议人工）：
            - 内容重复 → 建议合并（列出对方 id）
            - 内容超大且多 H2 主题 → 建议拆分
            - 名称/描述为非中文 → 建议补中文说明
        Returns: {total, plan: [...], applied: [...]}
        """
        from datetime import datetime as _dt  # noqa: F401 兼容旧调用
        applied: List[Dict[str, Any]] = []
        plan: List[Dict[str, Any]] = []
        all_skills = self.store.list_all()
        has_cjk = lambda s: any("\u4e00" <= ch <= "\u9fff" for ch in (s or ""))

        # 内容重复对（轻量：hash 相同 或 jaccard 高分，只记录不自动合并）
        dup_pairs: List[tuple] = []
        try:
            dup_pairs = self.reviewer.find_duplicates(all_skills, min_jaccard=0.75)
        except Exception:
            dup_pairs = []

        for s in all_skills:
            name = s.name or s.id
            issues: List[str] = []
            if not (s.description or "").strip():
                issues.append("缺少中文说明")
            elif not has_cjk(s.description):
                issues.append("说明非中文（建议补中文释义）")
            if not has_cjk(name) and not (s.description or "").strip():
                issues.append("名称与说明均非中文（建议中文命名/说明）")
            if s.content and len(s.content) > 12000:
                heads = len([l for l in s.content.splitlines()
                             if l.strip().startswith("## ") or l.strip().startswith("# ")])
                if heads >= 3:
                    issues.append(f"内容偏大且含 {heads} 个 H2/H1 主题，建议按主题拆分")
            if not s.enabled and (s.metrics.usage_count or 0) == 0:
                issues.append("长期停用且零使用（建议归档清理）")
            for d in dup_pairs:
                if d.get("skill_a") == s.id:
                    issues.append(f"与「{d.get('name_b', d.get('skill_b'))}」内容重复({d.get('jaccard')})，建议合并")
                elif d.get("skill_b") == s.id:
                    issues.append(f"与「{d.get('name_a', d.get('skill_a'))}」内容重复({d.get('jaccard')})，建议合并")

            if not issues:
                continue
            entry = {"id": s.id, "name": name, "status": getattr(s.status, "value", s.status),
                     "issues": issues}
            plan.append(entry)

            # 自动安全动作
            if auto_clean:
                did = False
                if not (s.description or "").strip():
                    # [不易] **本动作自 2026-09-26 起不再写库**（G1-B / M0）。
                    # 原实现把「正文首行或占位串」截 120 字后经
                    # `self.update(s.id, {"description": ...})` 写回**主轨**，是 M0 要冻的
                    # 第三条写路径（G1-B0 §2.7 E-2）。主轨 description 已不是事实源，
                    # 用它给 skill.md 补文案只会制造新的双描述冲突；且 update() 的白名单
                    # 已移除 "description" ⇒ 该调用现在必然被静默忽略（写不进去还可能
                    # 误报 applied）。故改为**显式登记为人工项**，不再自动补。
                    # 正确路径：在 data/skills_repo/<id>/skill.md 写 description_zh。
                    applied.append({
                        "id": s.id, "action": "补全中文说明(已改为人工)",
                        "detail": "主轨 description 写路径已冻结（G1-B/M0）；请在 "
                                  "data/skills_repo/%s/skill.md 写 description_zh" % s.id,
                    })
                if (not s.enabled and (s.metrics.usage_count or 0) == 0
                        and not did):
                    try:
                        s.status = SkillStatus.ARCHIVED
                        s.touch()
                        self.store.upsert(s)
                        applied.append({"id": s.id, "action": "归档（停用+零使用）"})
                    except Exception:
                        pass

        if auto_clean and applied:
            self._emit_assessment_event(
                "curate", "curate", "ok",
                f"老技能自动整理完成 {len(applied)} 项（补说明/归档）")

        return {"total": len(all_skills), "dry_run": dry_run,
                "auto_clean": auto_clean,
                "issues": len(plan), "plan": plan[:200],
                "applied_count": len(applied), "applied": applied[:200]}

    def suggest_redraft(self, skill_id: str,
                        use_llm: bool = False) -> Dict[str, Any]:
        """“再定义”草稿：LLM（可选）或确定性规则起草中文说明/展示名。

        草稿需人工确认后应用（应用即 update + 重新评审-评估）。
        use_llm=True 时尝试 LLM 起草，失败/不可用自动回退确定性草稿。
        """
        skill = self._require(skill_id)
        name = skill.name or skill.id
        desc = (skill.description or "").strip()
        lines = (skill.content or "").strip().splitlines()
        meaningful = [l.strip() for l in lines
                      if l.strip() and not l.strip().startswith(("#", "-", "```", "```"))]
        draft_desc = ""
        for l in meaningful:
            if len(l) >= 6:
                draft_desc = l
                break
        if not draft_desc:
            draft_desc = f"（自动再定义说明）{name}：基于内容重写的技能，请人工修订。"
        draft_desc = draft_desc[:160]
        has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in f"{name} {desc}")
        draft_name = name
        if not has_cjk:
            draft_name = f"{name}（{name}）" if len(name) > 16 else f"{name}（自动整理）"
        source = "rules"

        if use_llm:
            try:
                from agent.state_manager import get_llm_client
                llm = get_llm_client()
                if llm is not None:
                    prompt = (
                        "你是技能资产整理器。请依据技能内容把其整理为云枢中文技能说明，"
                        "只返回 JSON：{\"name\":\"中文展示名\",\"description\":\"中文说明(≤160字,含用途与触发时机)\"}\n"
                        f"当前 name={name}\ndescription={desc[:200]}\ncontent前600字:\n{(skill.content or '')[:600]}"
                    )
                    method = next((m for m in ("chat", "invoke", "complete", "generate")
                                  if hasattr(llm, m)), None)
                    if method:
                        resp = getattr(llm, method)(prompt)
                        import re as _re
                        m = _re.search(r"\{.*\}", str(resp), _re.S)
                        if m:
                            import json as _json
                            parsed = _json.loads(m.group(0))
                            nd = str(parsed.get("description", "") or "").strip()[:160]
                            nn = str(parsed.get("name", "") or "").strip()[:60]
                            if nd:
                                draft_desc = nd
                                source = "llm"
                            if nn and nn.lower() != name.lower():
                                draft_name = nn
            except Exception as e:  # noqa: BLE001 LLM 不可用/失败 → 规则草稿
                logger.info("[Service] redraft LLM 失败，回退规则草稿 skill=%s: %s",
                            skill_id, e)

        return {
            "skill_id": skill_id,
            "source": source,
            "current": {"name": name, "description": desc},
            "proposed": {"name": draft_name, "description": draft_desc},
            "note": "草稿需确认后应用：写回并自动重新评审-评估。",
        }

    # ─── 脚本文件全维度审查并入评估（第三层 scripts/*.py）───

    def _merge_script_review(self, skill: Skill,
                             result: ReviewResult) -> None:
        """把技能脚本文件（repo/<skill_id>/scripts/*.py）的 code_review 全维度
        与安装级威胁扫描并入同一份评审-评估报告。

        - 仅当脚本预检开关开启且有脚本文件时执行（纯 JSON 内容技能跳过）；
        - code_review 全维度（安全/性能/可维护性/API兼容性/测试）→ category=code
          CR_FILE_<维度>（安全 warn、其余 info）；
        - SecurityChecker 威胁扫描 → category=security SEC_FILE_SCRIPT
          （高风险按 block_on_high_risk_script 映射 error/warn，中/低→warn/info）；
        - 出现 error/critical 且当前审核为 PASSED → 降级 WARN / PENDING_REVIEW
          （发布门禁保持，须人工复核）；FAILED 维持原状。
        """
        try:
            from .assessor import (
                assess_flag, assess_int, assess_list, blocking_severities,
            )
            if not assess_flag("script_precheck_enabled", True):
                return
            allowed_ext = assess_list("script_languages", [".py"])
            names = [n for n in self.file_store.list_scripts(skill.id)
                     if any(n.lower().endswith(ext) for ext in allowed_ext)]
            if not names:
                return
        except Exception as e:  # noqa: BLE001
            logger.warning("[Service] 脚本审查跳过 skill=%s: %s", skill.id, e)
            return

        try:
            from agent.code_review import code_review
            from agent.extensions.security_checker import SkillSecurityChecker
        except Exception as e:  # noqa: BLE001
            logger.warning("[Service] 脚本审查器不可用: %s", e)
            return

        checker = SkillSecurityChecker()
        block_high = assess_flag("block_on_high_risk_script", True)
        sev_map = {"高风险": "error" if block_high else "warn",
                   "中风险": "warn", "低风险": "info"}
        cap_files = assess_int("max_script_files", 20)
        cap = assess_int("max_code_findings", 60)
        added: List[ReviewFinding] = []
        for name in (names or [])[:cap_files]:
            try:
                path = self.file_store.get_script_path(skill.id, name)
                if not path.exists():
                    continue
                content = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            try:
                cr = code_review(diff=content, dimensions=[
                    "安全", "性能", "可维护性", "API兼容性", "测试"])
                for dim in (cr or {}).get("dimensions", []) or []:
                    dimname = str(dim.get("dimension", ""))
                    if dimname not in ("安全", "性能", "可维护性", "API兼容性", "测试"):
                        continue
                    dimsev = "warn" if dimname == "安全" else "info"
                    for f in dim.get("findings", []) or []:
                        if len(added) >= cap:
                            break
                        desc = str(f.get("description", "") or "").strip()
                        if not desc:
                            continue
                        suggestion = str(f.get("suggestion", "") or "").strip()
                        message = f"[脚本审查·{name}·{dimname}] {desc}"
                        if suggestion:
                            message += f"。建议：{suggestion}"
                        line = f.get("line")
                        added.append(ReviewFinding(
                            severity=dimsev, category="code",
                            code=f"CR_FILE_{dimname}", message=message,
                            location=f"{name}@{line}" if line is not None else name))
                    if len(added) >= cap:
                        break
            except Exception:
                pass
            for hit in checker.scan_code_for_threats(content, name):
                if len(added) >= cap:
                    break
                added.append(ReviewFinding(
                    severity=sev_map.get(str(hit.get("severity", "")), "info"),
                    category="security", code="SEC_FILE_SCRIPT",
                    message=f"[脚本安装预检·{name}·{hit.get('category', '')}] "
                            f"{hit.get('pattern', '')}",
                    location=name))
            if len(added) >= cap:
                break

        if not added:
            return
        result.findings.extend(added)
        block_sev = blocking_severities()
        blocked = any(a.severity in block_sev for a in added)
        if blocked:
            result.review_verdict = "block"
            cur = getattr(result.status, "value", result.status)
            if cur == "passed":
                result.status = ReviewStatus.WARN
                skill.status = SkillStatus.PENDING_REVIEW.value
                result.summary = (result.summary or "审核通过") + \
                    "；脚本文件审查存在高风险代码(阻断项)，需人工复核"
                logger.info("[Service] 脚本审查阻断 skill=%s → PENDING_REVIEW", skill.id)
        elif not result.review_verdict:
            result.review_verdict = "ok"

    # ─── 发布（TASK-04 Step 3 强制审核链）───

    def publish(self, skill_id: str, *, force: bool = False,
                actor: str = "manual", reason: str = "") -> Skill:
        """发布技能：终态/驳回态拒绝 → 强制审核链 → 置 PUBLISHED

        Step 3 发布强制审核链：enforce_before_publish 默认 true（无 PASSED
        ReviewResult 禁止发布）；配置关闭或 force=True 显式豁免必须写审计日志
        （event=review_waiver_publish）。
        """
        from .exceptions import SkillReviewError

        skill = self._require(skill_id)
        if skill.status in (SkillStatus.PUBLISHED, SkillStatus.ARCHIVED,
                            SkillStatus.REJECTED):
            raise SkillReviewError("当前状态不可发布")
        from .review_gate import enforce_review
        enforce_review(skill, force=force, actor=actor, reason=reason)
        skill.status = SkillStatus.PUBLISHED
        skill.touch()
        self.store.upsert(skill)
        emit_metric("yunshu_skill_publish_total")
        logger.info("[Service] 技能已发布: %s (force=%s, actor=%s)",
                    skill_id, force, actor)
        return skill

    # ─── 搜索 ───

    def search(self, params: SkillSearchParams) -> SkillSearchResult:
        """管理页搜索（`GET /api/skills-mgmt/search`）

        【G1-C/R-d · 描述与展示同源】修复前这里是
        `self.searcher.search(self.store.list_all(), params)` —— 只喂**主轨**
        `Skill` 模型：它既没有 `description_zh`，`description` 又是主轨的历史副本。
        而 G1-B 之后管理页展示的是文件轨（`registry.as_legacy_rows()` 文件轨优先，
        UI 读 `description_zh || description`）⇒ 同一字段两个消费者看到两份文案
        （"**看到的**是文件轨文案、**搜到的**按主轨文案"）。

        修法（全部落在 `agent/skills_mgmt/` 内）：一次取好文件轨元数据索引交给
        搜索器，由 `SkillSearcher._match_score` 按"文件轨优先 + `description_zh`
        并入"计分 —— 与展示侧同一套口径，含同一个逃生开关。

        文件轨不可读时静默退化为旧行为（fail-soft）：搜索本身不该因元数据索引故障
        而不可用。
        """
        try:
            meta_index = self.file_store.load_metadata_index(refresh=False) or {}
        except Exception as e:  # noqa: BLE001 文件轨读失败不得让搜索不可用
            logger.warning("[Service] 搜索取文件轨描述失败，退化为仅主轨: %s", e)
            meta_index = {}
        return self.searcher.search(self.store.list_all(), params,
                                    meta_index=meta_index)

    def list_all(self) -> List[Skill]:
        return self.store.list_all()

    def get(self, skill_id: str) -> Skill:
        return self._require(skill_id)

    # ─── 增删改 ───

    def _record_update_audit(self, skill: Skill, updated: Skill,
                             changed: List[str]) -> None:
        """PATCH 字段变更写审计链（best-effort：绝不影响 update 本身）

        载荷**只记字段名，不记字段值**：description/content 等字段是用户原文，
        记值会把审计台账变成内容仓库（同 `agent/audit/facade.py:392-398` 的
        `record_redact_event` 只记 field_count/kinds 的先例），
        故此处只落字段名列表。

        - 一般字段变更 → 动作名 `skill.update`，payload 含 skill_id /
          changed（实际变更字段名列表，不含 enabled）/ origin；
        - 变更含 enabled → 复用启停动作族 `skill.registry.set_enabled`
          （`enhancer.record_enabled_audit`，与 SkillRegistry / SkillEnhancer
          同族），payload 含 origin=skills_mgmt.update、previous_enabled、
          enabled、track="main"（本通路写的是主轨 JSON），
          使按技能启停动作名检索时不会漏掉 PATCH 这条通路。

        Args:
            skill: 变更前的技能（`_require` 取出的旧值）。
            updated: 预备落库的新技能值。
            changed: 白名单内且实际发生变化的字段名（可含 enabled）。

        Note:
            审计失败（门面抛异常或返回 None）只写 WARNING 日志并返回，不向上抛
            —— 与 `enhancer.record_enabled_audit` 的既有 best-effort 约定一致，
            但**不完全静默**（门面返回 None 时同样留 WARNING）。
        """
        if "enabled" in changed:
            try:
                from .enhancer import (AUDIT_ACTION_ENABLED_SET,
                                       record_enabled_audit)
                record_enabled_audit(
                    AUDIT_ACTION_ENABLED_SET, str(updated.id),
                    previous_enabled=bool(getattr(skill, "enabled", True)),
                    enabled=bool(getattr(updated, "enabled", True)),
                    track="main", origin="skills_mgmt.update")
            except Exception as e:  # noqa: BLE001 审计失败不得影响更新
                logger.warning("[Service] 启停变更审计留痕失败（更新已生效）"
                               " skill=%s: %s", updated.id, e)

        plain = [f for f in changed if f != "enabled"]
        if not plain:
            return
        try:
            from agent.audit import audit as _audit_facade
            entry = _audit_facade.record(
                "skill.update", subject=f"skill:{updated.id}",
                payload={"skill_id": str(updated.id), "changed": plain,
                         "origin": "skills_mgmt.update"},
                source="agent", status="ok")
        except Exception as e:  # noqa: BLE001 审计失败不得影响更新
            logger.warning("[Service] 技能字段变更审计留痕失败（更新已生效）"
                           " skill=%s changed=%s: %s", updated.id, plain, e)
            return
        if entry is None:
            logger.warning("[Service] 技能字段变更审计未落链（门面返回 None："
                           "审计被关闭或静默失败）skill=%s changed=%s",
                           updated.id, plain)

    def update(self, skill_id: str, patch: Dict[str, Any]) -> Skill:
        """部分更新技能字段

        白名单字段实际发生变化时写审计链（见 `_record_update_audit`）：
        一般字段记动作名 `skill.update`（**只记字段名，不记字段值**）；
        `enabled` 变化复用启停动作族 `skill.registry.set_enabled`
        （origin=`skills_mgmt.update`、track=`main`）。
        同值不改（patch 值与现值相等）不算变更，不写记录。
        审计为 best-effort：失败只留 WARNING 日志，不影响更新与返回值。
        """
        skill = self._require(skill_id)
        data = skill.model_dump()
        # 白名单字段
        # [不易] **`description` 自 2026-09-26 起被移出白名单**（G1-B / M0：
        # 「先冻写路径再删数据」）。原因：技能的**唯一事实源**已收敛为
        # `data/skills_repo/<id>/skill.md` 的 front matter（G1-A §7.1 裁定），
        # 主轨 description 只是历史副本。若仍允许经本方法改它 ⇒
        # ① UI 编辑（PATCH /api/skills-mgmt/<id>）会重新制造出与 skill.md 冲突的
        #    第二份文案（G1-A C2 的 15/15 冲突就是这样来的）；
        # ② 主轨写入会经 `store.sync_to_legacy_skills_json()` 传染到 legacy 快照；
        # ③ `curate_skills(auto_clean=True)` 的「自动补全说明」会把它再写回来。
        # 改描述的正确入口：写 skill.md 的 `description`（英文，检索+模型用）
        # 与 `description_zh`（中文，UI 用）—— 由 `file_store.update_meta()` 落地。
        # 本键移出白名单后，patch 里的 "description" 被**静默忽略**（与其它白名单外
        # 键同语义，见 `test_skill_update_audit.py` 的 non-whitelisted 断言）。
        allowed = {"name", "tags", "content", "content_type",
                   "config_schema", "default_params", "dependencies",
                   "author", "enabled",
                   # [不易] `description_zh` **同样不放进来**：它是"中文展示文案"的
                   # 唯一副本，主轨若也能写它，就又会分裂成两份中文（正是本卡要消灭的
                   # 形态）。中文入口同样是 skill.md 的 `description_zh`。
                   # [变易] 敏感技能隔离字段透传（create/update 双向闭环）
                   "is_sensitive", "isolation_strategy"}
        for k, v in patch.items():
            if k in allowed:
                data[k] = v
        updated = Skill.from_storage_dict(data)
        updated.touch()
        # 白名单内的实际变更字段（含 enabled；同值不算变更 → 不写审计）
        changed = sorted(k for k in patch if k in allowed
                         and getattr(skill, k, None) != getattr(updated, k, None))
        self.store.upsert(updated)
        if changed:
            self._record_update_audit(skill, updated, changed)
        self._advisory_assess(updated.id)  # 修改后自动重新评估（尚无正式审核时）
        self._auto_classify(updated.id)    # 内容变化后自动重判分类（人工移动过的保留）
        return updated

    def delete(self, skill_id: str) -> bool:
        """删除技能（多轨同步，根治孤儿残留）。

        除主轨外同步清除：legacy(data/skills.json ×2)、文件轨
        (skills_repo/<id>/)、分类注册表、评估事件——否则 UI 会残留
        "该技能没有可查看的指令正文（仅运行时元数据）"的孤儿项。
        """
        if self.store.get(skill_id) is None:
            raise SkillNotFoundError(skill_id)
        from agent.skills_mgmt.cleanup import remove_skill_everywhere
        remove_skill_everywhere(self, skill_id)
        logger.info("[Service] 技能已删除(多轨): %s", skill_id)
        return True

    # ─── 清理能力（技能中心：孤儿扫描 / 无用淘汰）───

    def cleanup_report(self) -> Dict[str, Any]:
        """技能中心清理总览（只报告不删除）。"""
        from agent.skills_mgmt.cleanup import report
        return report(self)

    def cleanup_orphans(self, *, dry_run: bool = True) -> Dict[str, Any]:
        """清除孤儿残留（dry_run=True 只报告）。"""
        from agent.skills_mgmt.cleanup import cleanup_orphans
        return cleanup_orphans(self, dry_run=dry_run)

    def cleanup_unused(self, *, dry_run: bool = True,
                       unused_days: Optional[int] = None,
                       archived_days: Optional[int] = None) -> Dict[str, Any]:
        """物理删除无用技能（dry_run=True 只报告，默认保守）。"""
        from agent.skills_mgmt.cleanup import cleanup_unused
        kwargs: Dict[str, Any] = {}
        if unused_days is not None:
            kwargs["unused_days"] = unused_days
        if archived_days is not None:
            kwargs["archived_days"] = archived_days
        return cleanup_unused(self, dry_run=dry_run, **kwargs)

    # ─── 增强器代理 ───

    def bump_version(self, skill_id: str, kind: str, *,
                     changelog: str = "", content: Optional[str] = None,
                     eval_result: Optional[Dict[str, Any]] = None) -> VersionBump:
        return self.enhancer.bump_version(
            skill_id, kind, changelog=changelog, content=content,
            eval_result=eval_result)

    def list_versions(self, skill_id: str) -> List[SkillVersion]:
        return self.enhancer.list_versions(skill_id)

    def rollback_version(self, skill_id: str, target_version: str) -> Skill:
        return self.enhancer.rollback_version(skill_id, target_version)

    def optimize_params(self, skill_id: str,
                        feedback_summary: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self.enhancer.optimize_params(skill_id,
                                             feedback_summary=feedback_summary)

    def record_execution(self, skill_id: str, *,
                         success: bool, latency_ms: float,
                         feedback_rating: int = 0,
                         feedback_id: str = "",
                         trace_id: str = "",
                         params_used: Optional[Dict[str, Any]] = None,
                         # [变易] 全链路可观测性扩展：端到端评估得分（可选）
                         # 缺省 None 保证旧调用方不受影响（守不易）
                         eval_score: Optional[Dict[str, Any]] = None,
                         # [变易] Dynamic Few-shot 自动采集（可选）
                         # 仅 feedback_rating=5 且 success=True 且提供输入/输出文本时
                         # 才采集成功案例入示例库；缺省 None 不影响旧调用方（守不易）
                         input_text: Optional[str] = None,
                         output_text: Optional[str] = None) -> None:
        """记录一次技能执行并可选持久化端到端评估得分

        Args:
            eval_score: 可选评估得分 {task_success, instruction_followed,
                hallucination_detected, score}；提供时发射
                yunshu_skill_eval_score / yunshu_skill_hallucination_total
                指标并持久化到 trace span（失败不影响主流程）。
            input_text: 可选用户输入文本；feedback_rating=5 且 success=True 时
                自动采集为 Few-shot 示例（带去重）。
            output_text: 可选执行输出文本；同上，仅成功案例采集。
        """
        self.enhancer.record_execution(
            skill_id, success=success, latency_ms=latency_ms,
            feedback_rating=feedback_rating,
            feedback_id=feedback_id, trace_id=trace_id,
            params_used=params_used)

        # [变易] 可观测性扩展：发射 eval_score 指标并持久化 span
        # 内部全部 try/except，失败不影响主流程
        if eval_score is not None:
            from .observability import emit_eval_score_metric
            emit_eval_score_metric(skill_id, eval_score, trace_id=trace_id)
            # [Observability] INFO 级别：eval_score 详情，正式环境可观测
            logger.info(
                "[Observability] service.record_execution eval_score | skill_id=%s | "
                "trace_id=%s | task_success=%s | hallucination=%s | score=%s",
                skill_id, trace_id or "(none)",
                eval_score.get("task_success"),
                eval_score.get("hallucination_detected"),
                eval_score.get("score"),
            )

        # [变易] Dynamic Few-shot 示例自动采集：
        # 仅 rating=5 的成功案例（宁缺毋滥），失败不影响主流程
        if feedback_rating == 5 and success and input_text and output_text:
            try:
                self._collect_few_shot_example(
                    skill_id, input_text=input_text,
                    output_text=output_text, trace_id=trace_id,
                )
            except Exception as e:  # noqa: BLE001 采集失败不影响主流程
                logger.warning(
                    "[Service] few-shot 示例采集失败 skill_id=%s: %s",
                    skill_id, e,
                )

    # ─── 反馈绑定 ───

    def submit_skill_feedback(self, skill_id: str, *,
                               trace_id: str,
                               feedback_type: str,
                               rating: int = 0,
                               comment: str = "",
                               category: str = "other",
                               user_id: str = "",
                               session_id: str = "",
                               context: Optional[Dict[str, Any]] = None,
                               workflow_id: str = "") -> Dict[str, Any]:
        """提交针对某技能的用户反馈

        将 feedback 模块与 skills_mgmt 模块打通：
        - 反馈落库到 feedback 表（带 skill_id）
        - 触发 enhancer.record_execution 同步指标
        - 返回 feedback 记录 + 后续建议动作

        Raises:
            SkillNotFoundError: 技能不存在
            ValueError: 参数非法
        """
        with traced_action("svc_submit_skill_feedback",
                           skill_id=skill_id,
                           feedback_type=feedback_type,
                           rating=rating):
            # 先校验技能存在（边界显性化）
            self._require(skill_id)

            from agent.feedback import get_feedback_manager
            mgr = get_feedback_manager()
            record = mgr.submit_feedback(
                trace_id=trace_id,
                feedback_type=feedback_type,
                rating=rating,
                comment=comment,
                category=category,
                user_id=user_id,
                session_id=session_id,
                context=context or {"skill_id": skill_id},
                skill_id=skill_id,
                workflow_id=workflow_id,
            )

            # 同步更新技能指标
            self.enhancer.record_execution(
                skill_id,
                success=(feedback_type == "like"),
                latency_ms=0.0,
                feedback_rating=rating,
                feedback_id=record.feedback_id,
                trace_id=trace_id,
            )

            summary = mgr.get_skill_feedback_summary(skill_id)
            return {
                "feedback": record.to_dict(),
                "summary": summary,
            }

    def get_skill_feedback_summary(self, skill_id: str,
                                  days: int = 30) -> Dict[str, Any]:
        """获取技能反馈聚合统计"""
        self._require(skill_id)
        return self.enhancer.get_skill_feedback_summary(skill_id, days=days)

    def optimize_with_feedback(self, skill_id: str,
                               days: int = 30) -> Dict[str, Any]:
        """一键式：拉取反馈 + 触发参数优化"""
        self._require(skill_id)
        return self.enhancer.optimize_with_feedback(skill_id, days=days)

    # ─── 进化谱系只读路由（EVO-T1）───

    @property
    def _evolution_archive(self) -> EvolutionArchive:
        """当前谱系档案库（测试可注入隔离实例）"""
        return self._lineage_archive

    @_evolution_archive.setter
    def _evolution_archive(self, archive: EvolutionArchive) -> None:
        self._lineage_archive = archive

    def set_lineage_hook(self, hook: Optional[Callable[[dict], None]]) -> None:
        """注入外部谱系钩子（透传 enhancer，供审批/审计流复用）"""
        self.enhancer.set_lineage_hook(hook)

    def get_evolution_lineage(self, skill_id: str) -> List[EvolutionRecord]:
        """查询技能完整进化链（只读路由；无记录返回空列表，不抛异常）"""
        return self._lineage_archive.get_lineage(skill_id)

    def print_evolution_lineage(self, skill_id: str) -> str:
        """打印技能进化链文本（审计/CLI 用）"""
        return print_lineage(skill_id, self._lineage_archive)

    # ─── 进化循环网关（EVO-T3）───

    def _new_evolver(self) -> "OfflineEvolver":
        """构建共享同一档案库实例的进化器（延迟导入避免循环依赖）"""
        from .offline_evolver import OfflineEvolver
        return OfflineEvolver(
            self.store, self.enhancer, archive=self._evolution_archive)

    def _build_real_evaluator(self, skill: Any) -> Optional[Any]:
        """构建任务 2 真实评估器；失败返回 None（evolver 回退启发式）"""
        try:
            from .evaluator import get_default_evaluator
            return get_default_evaluator(skill)
        except Exception as e:  # noqa: BLE001 评估器不可用不阻塞进化
            logger.warning("[SkillsMgmtService] 真实评估器构建失败: %s", e)
            return None

    def evolve_skill(self, skill_id: str, *,
                     strategies: Optional[List[str]] = None,
                     trigger: str = "api") -> Dict[str, Any]:
        """手动触发单技能进化（默认接入真实评估器，EVO-T3）

        触发即写谱系（提交/拒绝/跳过均产生 EvolutionRecord）。

        Returns:
            dict — {skill_id, committed, decision, improvement, score,
                    new_version, parent_record_id, record_id, cost_tokens}
        """
        self._require(skill_id)
        from .offline_evolver import EvolutionStrategy
        strategies_enum = None
        if strategies:
            strategies_enum = [
                EvolutionStrategy(s) for s in strategies
                if EvolutionStrategy(s) in EvolutionStrategy]
        evolver = self._new_evolver()
        result = evolver.evolve_once(
            skill_id,
            strategies=strategies_enum,
            evaluator=self._build_real_evaluator(self.store.get(skill_id)),
            trigger=trigger)
        return {
            "skill_id": result.skill_id,
            "committed": result.committed,
            "decision": result.decision,
            "improvement": result.improvement,
            "score": result.score,
            "new_version": result.new_version,
            "parent_record_id": result.parent_record_id,
            "record_id": result.record_id,
            "cost_tokens": result.cost_tokens,
            "budget_breached": result.budget_breached,
            "error": result.error,
        }

    def evolve_batch(self, skill_ids: Optional[List[str]] = None, *,
                     max_rounds: int = 1,
                     trigger: str = "api") -> Dict[str, Any]:
        """手动触发批量进化（默认接入真实评估器，EVO-T3）

        Returns:
            dict — 批量报告摘要（含成本汇总与评分序列）
        """
        evolver = self._new_evolver()
        evaluator = None
        if skill_ids:
            try:
                evaluator = self._build_real_evaluator(
                    self.store.get(skill_ids[0]))
            except Exception as e:  # noqa: BLE001
                logger.warning("[SkillsMgmtService] 批量评估器构建失败: %s", e)
        else:
            candidates = evolver._select_candidates()
            if candidates:
                evaluator = self._build_real_evaluator(candidates[0])
        report = evolver.evolve_batch(
            skill_ids, max_rounds=max_rounds, evaluator=evaluator,
            trigger=trigger)
        return {
            "trigger": report.trigger,
            "total_skills": report.total_skills,
            "evolved_count": report.evolved_count,
            "skipped_count": report.skipped_count,
            "failed_count": report.failed_count,
            "avg_improvement": report.avg_improvement,
            "cost_tokens": report.cost_tokens,
            "total_duration_ms": report.total_duration_ms,
            "budget_usage_ratio": report.budget_usage_ratio,
            "budget_breached": report.budget_breached,
            "score_series": report.score_series,
            "finished_at": report.finished_at,
        }

    def schedule_evolution(self, cron_expr: str = "0 2 * * *", *,
                           skill_ids: Optional[List[str]] = None,
                           max_rounds: int = 1) -> Dict[str, Any]:
        """注册进化 cron 调度（EVO-T3，默认关闭 — 安全底线）

        EVOLUTION_SCHEDULE_ENABLED=false 时只返回 disabled 信息；
        开启需 .env 配置 EVOLUTION_SCHEDULE_ENABLED=true。
        """
        return self._new_evolver().schedule(
            cron_expr, skill_ids=skill_ids, max_rounds=max_rounds)

    def unschedule_evolution(self) -> bool:
        """注销进化 cron 调度"""
        return self._new_evolver().unschedule()

    # ─── 重复技能检测与合并 ───

    def list_duplicates(self, min_jaccard: float = 0.7) -> List[Dict[str, Any]]:
        """扫描整个技能库，列出 Jaccard≥阈值的重复对

        Args:
            min_jaccard: 最小相似度阈值（默认 0.7）

        Returns:
            [{skill_a, skill_b, name_a, name_b, jaccard,
              content_hash_match, recommend_action}, ...]
            recommend_action: "merge" | "review"
        """
        skills = self.store.list_all()
        return self.reviewer.find_duplicates(skills, min_jaccard=min_jaccard)

    def find_duplicates_for(self, skill_id: str,
                            min_jaccard: float = 0.7) -> List[Dict[str, Any]]:
        """找出与指定技能重复的其他技能"""
        target = self._require(skill_id)
        others = [s for s in self.store.list_all() if s.id != skill_id]
        return self.reviewer.find_duplicates_for(
            target, others, min_jaccard=min_jaccard,
        )

    def merge_duplicate_skills(self, src_id: str, dst_id: str, *,
                                strategy: str = "auto",
                                rebind_feedback: bool = True) -> Dict[str, Any]:
        """合并两个重复技能

        Args:
            src_id: 被合并方ID（将被删除）
            dst_id: 合并保留方ID
            strategy: auto | keep_dst | keep_src
            rebind_feedback: 是否同时改绑 feedback 表（默认 True）

        Returns:
            {merged_id, removed_id, merged_fields, version_added,
             feedback_rebound_count}

        Raises:
            SkillNotFoundError: 任一技能不存在
            ValueError: src_id == dst_id
        """
        with traced_action("svc_merge_skills",
                           src_id=src_id, dst_id=dst_id,
                           strategy=strategy):
            # 边界显性化
            self._require(src_id)
            self._require(dst_id)
            if src_id == dst_id:
                raise ValueError(
                    f"src_id 与 dst_id 不能相同: {src_id}"
                )

            feedback_mgr = None
            if rebind_feedback:
                try:
                    from agent.feedback import get_feedback_manager
                    feedback_mgr = get_feedback_manager()
                except Exception as e:
                    logger.warning(
                        "[Service] 获取 feedback_manager 失败，跳过反馈改绑: %s", e
                    )

            result = self.store.merge_skills(
                src_id, dst_id,
                strategy=strategy,
                feedback_manager=feedback_mgr,
            )

            try:
                from .observability import track_event
                track_event("skills_merged", {
                    "merged_id": result["merged_id"],
                    "removed_id": result["removed_id"],
                    "merged_fields": result["merged_fields"],
                    "strategy": strategy,
                })
            except Exception:
                pass
            return result

    def auto_merge_duplicates(self, min_jaccard: float = 0.85,
                               max_merges: int = 10) -> Dict[str, Any]:
        """自动合并高相似度技能对（Jaccard ≥ min_jaccard）

        仅合并 recommend_action == "merge" 的对，避免误伤 review 类。
        每次合并后重新扫描，因为合并会改变剩余技能的相似度关系。

        Args:
            min_jaccard: 自动合并阈值（默认 0.85，比 review 的 0.7 更严格）
            max_merges: 单次最多合并多少对（防止意外批量操作）

        Returns:
            {scanned_pairs, merged_pairs: [...], skipped: int}
        """
        with traced_action("svc_auto_merge_duplicates",
                           min_jaccard=min_jaccard,
                           max_merges=max_merges):
            merged_pairs: List[Dict[str, Any]] = []
            total_scanned = 0

            # 迭代合并：每次合并一对后重新扫描，直到没有可合并对或达到上限
            while len(merged_pairs) < max_merges:
                duplicates = self.list_duplicates(min_jaccard=min_jaccard)
                merge_candidates = [
                    d for d in duplicates
                    if d["recommend_action"] == "merge"
                ]
                total_scanned = max(total_scanned, len(duplicates))

                if not merge_candidates:
                    break

                # 取相似度最高的一对
                dup = merge_candidates[0]
                a, b = dup["skill_a"], dup["skill_b"]
                try:
                    result = self.merge_duplicate_skills(a, b)
                    merged_pairs.append(result)
                except Exception as e:
                    logger.warning(
                        "[Service] 自动合并失败 %s ↔ %s: %s",
                        a, b, e,
                    )
                    break

            return {
                "scanned_pairs": total_scanned,
                "merged_pairs": merged_pairs,
                "skipped": 0,
            }

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
                # [变易] 可观测性扩展：透传 retrieved_chunks 到 traced_action
                # observability 层会自动对 >50 项截断
                ctx["retrieved_chunks"] = result.retrieved_chunks
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
                           max_tokens=max_tokens, layer="1+2") as ctx:
            result = self.injector.build_context(
                intent, max_tokens=max_tokens, top_k=top_k,
                auto_load_instruction=auto_load_instruction,
                skill_id=skill_id,
            )
            # [变易] 可观测性扩展：透传 retrieved_chunks 到 traced_action
            ctx["retrieved_chunks"] = result.get("retrieved_chunks", [])
            return result

    def validate_llm_output(self, llm_output: str, *,
                            loaded_skills: List[str],
                            intent: str) -> Dict[str, Any]:
        """校验 LLM 输出 (供 orchestrator 在收到 LLM 回复后调用)

        [变易] 不阻塞主流程: 返回的 GuardResult.passed 始终 True,
        severity=critical 时由调用方决策重试或降级。

        检测项:
            - 幻觉检测: LLM 是否声称调用了未加载的技能
            - 格式校验: 期望 JSON 时是否合法
            - 合规校验: PII / 密钥 / Prompt Injection
            - 越界检测: 危险动作关键词

        Args:
            llm_output: LLM 回复文本
            loaded_skills: 已加载的技能 ID 列表 (来自 build_skill_context)
            intent: 用户原始意图

        Returns:
            dict — {passed, severity, findings, sanitized_output}
            findings: [{category, severity, message, location}, ...]
        """
        with traced_action("svc_validate_llm_output",
                           intent=intent[:80]) as ctx:
            guard = SkillOutputGuard()
            result = guard.validate_llm_output(
                llm_output, loaded_skills, intent,
            )
            ctx["severity"] = result.severity
            ctx["findings_count"] = len(result.findings)
            ctx["passed"] = result.passed
            logger.info(
                "[Service] validate_llm_output severity=%s findings=%d",
                result.severity, len(result.findings),
            )
            emit_metric("yunshu_skill_llm_guard_total",
                        value=1, kind="counter",
                        labels={"severity": result.severity})
            return result.to_dict()

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
                # [变易] 全链路可观测性字段统计：声明已沉淀的可观测性字段
                # 实际指标值通过 metrics 系统（Prometheus/BusinessMetricsCollector）
                # 与结构化日志聚合（Loki/ELK）查询，此处仅暴露字段元信息
                "observability": {
                    "fields": [
                        "retrieved_chunks",
                        "retrieval_precision_at_k",
                        "eval_score",
                        "user_feedback",
                    ],
                    "metrics": [
                        "yunshu_skill_retrieval_precision_at_k",
                        "yunshu_skill_eval_score",
                        "yunshu_skill_hallucination_total",
                    ],
                    "retrieved_chunks_max": 50,
                    "truncation_enabled": True,
                    "span_persistence": "structured_log",
                },
            },
            # 规模监控：当技能数增长到阈值时建议升级检索方式
            # 当前仅 TF-IDF，未来扩展点已在 match() 接口预留（use_vector/use_bm25/use_reranker）
            "scale_monitoring": {
                "total_skills": len(all_skills),
                "upgrade_threshold": 30,
                "upgrade_recommended": len(all_skills) >= 30,
                "current_method": "tfidf",
                "available_methods": ["tfidf"],  # 未来扩展: ["tfidf", "vector", "bm25", "reranker"]
            },
        }

    # ─── 内部 ───

    def _collect_few_shot_example(self, skill_id: str, *,
                                  input_text: str, output_text: str,
                                  trace_id: str = "") -> bool:
        """将一次成功执行采集为 Few-shot 示例（带去重）

        仅由 record_execution 在 feedback_rating=5 且 success=True 时调用；
        示例库写入失败由调用方兜底，不影响主流程。
        """
        import uuid
        from datetime import datetime
        from .few_shot_injector import FewShotInjector, FewShotExample

        injector = FewShotInjector()
        example = FewShotExample(
            example_id=f"ex_{uuid.uuid4().hex[:8]}",
            intent=input_text,
            input=input_text,
            output=output_text,
            rating=5,
            tags=[skill_id],
            created_at=datetime.now().isoformat(timespec="seconds"),
        )
        added = injector.add_example(skill_id, example)
        logger.info(
            "[Service] few-shot 示例%s采集 skill_id=%s trace_id=%s",
            "" if added else "（重复，跳过）",
            skill_id, trace_id or "(none)",
        )
        return added

    def _require(self, skill_id: str) -> Skill:
        skill = self.store.get(skill_id)
        if not skill:
            raise SkillNotFoundError(skill_id)
        return skill
