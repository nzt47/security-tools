"""技能创建器 — AI 辅助 / 手动 / 多格式安装

三种创建模式:
    1. AI 辅助生成 (ai_assisted): 调用云枢已有 LLM 能力生成技能骨架
    2. 手动开发 (manual): 直接提交完整 Skill 数据
    3. 多格式安装 (install): 支持
       - github:user/repo
       - url:https://...
       - local:/path/to/skill
       - registry:openclaw/foo (复用 agent/extensions/market.py)

设计原则:
    - 边界显性化: 安装源不可达/格式不支持/解析失败均抛 SkillInstallError
    - 防连点: 创建期间锁住 skill_id，避免并发重复创建
    - 可观测: 全程结构化日志 + 业务指标
"""

from __future__ import annotations
import hashlib
import json
import os
import re
import threading
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Dict, Optional

from .models import (
    Skill,
    SkillVersion,
    SkillCategory,
    SkillStatus,
    ContentType,
)
from .exceptions import (
    SkillAlreadyExistsError,
    SkillValidationError,
    SkillInstallError,
    ErrorCode,
)
from .observability import logger, emit_metric, track_event, traced_action
from .store import SkillStore


# ──────────────────────────────────────────────
# AI 辅助生成
# ──────────────────────────────────────────────

_AI_SKILL_TEMPLATE = """# {name}

> {description}

## 适用场景
{use_cases}

## 触发条件
{triggers}

## 执行步骤
{steps}

## 参数说明
{params}

## 示例
{examples}

## 注意事项
- 此技能由 AI 辅助生成，请人工核对后再发布。
- 边界情况应显式抛出业务错误码 (按可观测性约束)。
"""


class AIAssistedGenerator:
    """AI 辅助技能生成器

    复用云枢已有的 LLM 客户端；若 LLM 不可用则降级为模板生成。
    """

    def __init__(self, llm_client: Optional[Any] = None):
        self._llm = llm_client

    def generate(self, *, name: str, intent: str, category: str = "custom",
                 tags: Optional[list] = None) -> Skill:
        """根据意图生成技能骨架"""
        with traced_action("skill_ai_generate", name=name, intent=intent) as ctx:
            track_event("skill_ai_generate_start", {"name": name, "intent": intent})

            # 构造 LLM 提示
            prompt = (
                f"你是一个技能设计助手。请根据以下意图生成一个云枢技能骨架:\n"
                f"技能名称: {name}\n"
                f"意图: {intent}\n"
                f"分类: {category}\n"
                f"请输出 markdown 格式，包含: 适用场景、触发条件、执行步骤、"
                f"参数说明、示例。"
            )

            content = self._call_llm(prompt) or self._template_fallback(
                name=name, intent=intent
            )

            skill = Skill(
                id=self._derive_id(name),
                name=name,
                description=intent[:2000],
                category=SkillCategory.AI_GENERATED,
                tags=tags or ["ai_generated"],
                status=SkillStatus.DRAFT,
                source="ai_assisted",
                author="ai_assistant",
                content=content,
                content_type=ContentType.MARKDOWN,
                version="0.1.0",
            )
            ctx["skill_id"] = skill.id
            emit_metric("yunshu_skill_create_total",
                        labels={"success": "true", "mode": "ai"},
                        kind="counter")
            return skill

    def _call_llm(self, prompt: str) -> Optional[str]:
        """调用云枢 LLM 客户端 (失败返回 None 走降级)"""
        if not self._llm:
            return None
        try:
            # 期望 LLM 客户端提供 .chat(prompt) -> str 接口
            if hasattr(self._llm, "chat"):
                return self._llm.chat(prompt)
            if hasattr(self._llm, "complete"):
                return self._llm.complete(prompt)
        except Exception as e:  # noqa: BLE001
            logger.warning("[AI生成] LLM 调用失败，降级到模板: %s", e)
        return None

    @staticmethod
    def _template_fallback(*, name: str, intent: str) -> str:
        return _AI_SKILL_TEMPLATE.format(
            name=name,
            description=intent,
            use_cases=f"- {intent}",
            triggers=f"- 用户提到『{name}』相关关键词",
            steps="1. 解析输入\n2. 执行核心逻辑\n3. 返回结构化结果",
            params="| 参数 | 类型 | 必填 | 说明 |\n|------|------|------|------|\n| input | string | 是 | 输入文本 |",
            examples=f"```\n输入: {intent}\n输出: (待补充)\n```",
        )

    @staticmethod
    def _derive_id(name: str) -> str:
        """从名称推导合法 ID (kebab-case)"""
        s = re.sub(r"[^a-zA-Z0-9]+", "-", name.lower()).strip("-")
        if not s:
            s = "skill"
        if not s[0].isalnum():
            s = "s-" + s
        return s[:64]


# ──────────────────────────────────────────────
# 多格式安装器
# ──────────────────────────────────────────────

class SkillInstaller:
    """多格式技能安装器"""

    SUPPORTED_SCHEMES = ("github", "url", "local", "registry")

    def __init__(self, store: SkillStore, *, http_timeout: int = 15):
        self._store = store
        self._http_timeout = http_timeout

    def install(self, source: str, *, force: bool = False) -> Skill:
        """从指定来源安装技能

        Args:
            source: 来源字符串
                - github:user/repo[/path]
                - url:https://example.com/skill.json
                - local:/path/to/skill.json
                - registry:openclaw/foo
            force: 是否覆盖已存在的同 ID 技能
        """
        with traced_action("skill_install", source=source, force=force) as ctx:
            track_event("skill_install_start", {"source": source})
            scheme, rest = self._parse_source(source)

            if scheme == "github":
                payload = self._from_github(rest)
            elif scheme == "url":
                payload = self._from_url(rest)
            elif scheme == "local":
                payload = self._from_local(rest)
            elif scheme == "registry":
                payload = self._from_registry(rest)
            else:
                raise SkillInstallError(
                    f"不支持的安装来源: {scheme}",
                    code=ErrorCode.INSTALL_FORMAT_UNSUPPORTED,
                    details={"source": source, "scheme": scheme},
                )

            payload.setdefault("source", source)
            payload.setdefault("status", SkillStatus.PENDING_REVIEW.value)
            if "installed_at" not in payload:
                payload["installed_at"] = datetime.now().isoformat()
            skill = Skill.from_storage_dict(payload)

            existing = self._store.get(skill.id)
            if existing and not force:
                raise SkillAlreadyExistsError(skill.id)

            self._store.upsert(skill)
            ctx["skill_id"] = skill.id
            emit_metric("yunshu_skill_install_total",
                        labels={"success": "true", "scheme": scheme},
                        kind="counter")
            logger.info("[Installer] 安装成功: %s (scheme=%s)", skill.id, scheme)
            return skill

    # ─── 解析 ───

    @staticmethod
    def _parse_source(source: str) -> tuple:
        if ":" not in source:
            raise SkillInstallError(
                f"非法来源格式: {source} (应为 scheme:rest)",
                code=ErrorCode.INSTALL_FORMAT_UNSUPPORTED,
            )
        scheme, rest = source.split(":", 1)
        scheme = scheme.lower().strip()
        rest = rest.strip()
        if scheme not in SkillInstaller.SUPPORTED_SCHEMES:
            raise SkillInstallError(
                f"不支持的 scheme: {scheme} (支持: {SkillInstaller.SUPPORTED_SCHEMES})",
                code=ErrorCode.INSTALL_FORMAT_UNSUPPORTED,
            )
        return scheme, rest

    # ─── github ───

    def _from_github(self, rest: str) -> Dict[str, Any]:
        """从 GitHub 安装 — rest = user/repo[/path]"""
        parts = rest.split("/", 2)
        if len(parts) < 2:
            raise SkillInstallError(
                f"非法 github 来源: {rest} (应为 user/repo[/path])",
                code=ErrorCode.INSTALL_FORMAT_UNSUPPORTED,
            )
        user, repo = parts[0], parts[1]
        path = parts[2] if len(parts) > 2 else "skill.json"
        # raw.githubusercontent.com
        url = f"https://raw.githubusercontent.com/{user}/{repo}/HEAD/{path}"
        return self._fetch_json(url, source=f"github:{rest}")

    # ─── url ───

    def _from_url(self, rest: str) -> Dict[str, Any]:
        if not rest.startswith(("http://", "https://")):
            raise SkillInstallError(
                f"url 来源必须是 http(s) URL: {rest}",
                code=ErrorCode.INSTALL_FORMAT_UNSUPPORTED,
            )
        return self._fetch_json(rest, source=f"url:{rest}")

    # ─── local ───

    def _from_local(self, rest: str) -> Dict[str, Any]:
        # 兼容 Windows 路径 /root 或 C:\... 或 /C:/...
        path = rest
        if path.startswith("/") and len(path) > 2 and path[2] == ":":
            path = path[1:]
        if not os.path.exists(path):
            raise SkillInstallError(
                f"本地路径不存在: {path}",
                code=ErrorCode.INSTALL_SOURCE_UNREACHABLE,
                details={"path": path},
            )
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except json.JSONDecodeError as e:
            raise SkillInstallError(
                f"本地文件 JSON 解析失败: {e}",
                code=ErrorCode.INSTALL_FAILED,
            ) from e
        payload.setdefault("install_path", path)
        return payload

    # ─── registry ───

    def _from_registry(self, rest: str) -> Dict[str, Any]:
        """从注册表安装 — 复用 agent/extensions/market.py"""
        try:
            from agent.extensions.market import ExtensionMarket  # noqa: WPS433
            market = ExtensionMarket()
            item = market.fetch(rest)
            if not item:
                raise SkillInstallError(
                    f"注册表中未找到技能: {rest}",
                    code=ErrorCode.INSTALL_SOURCE_UNREACHABLE,
                )
            return item
        except ImportError:
            raise SkillInstallError(
                "注册表功能未启用 (agent.extensions.market 未安装)",
                code=ErrorCode.INSTALL_FORMAT_UNSUPPORTED,
            )
        except SkillInstallError:
            raise
        except Exception as e:  # noqa: BLE001
            raise SkillInstallError(
                f"注册表获取失败: {e}",
                code=ErrorCode.INSTALL_FAILED,
            ) from e

    # ─── HTTP 工具 ───

    def _fetch_json(self, url: str, *, source: str) -> Dict[str, Any]:
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Yunshu-SkillInstaller/1.0"})
            with urllib.request.urlopen(req, timeout=self._http_timeout) as resp:
                if resp.status >= 400:
                    raise SkillInstallError(
                        f"HTTP {resp.status}: {url}",
                        code=ErrorCode.INSTALL_SOURCE_UNREACHABLE,
                        details={"url": url, "status": resp.status},
                    )
                data = resp.read().decode("utf-8")
        except urllib.error.URLError as e:
            raise SkillInstallError(
                f"网络请求失败: {e}",
                code=ErrorCode.INSTALL_SOURCE_UNREACHABLE,
                details={"url": url},
            ) from e
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as e:
            raise SkillInstallError(
                f"响应 JSON 解析失败: {e}",
                code=ErrorCode.INSTALL_FAILED,
            ) from e
        payload.setdefault("source", source)
        payload.setdefault("source_url", url)
        return payload


# ──────────────────────────────────────────────
# 创建门面
# ──────────────────────────────────────────────

class SkillCreator:
    """技能创建门面 — 统一三种创建入口"""

    def __init__(self, store: SkillStore, *, llm_client: Optional[Any] = None,
                 http_timeout: int = 15):
        self._store = store
        self._ai = AIAssistedGenerator(llm_client=llm_client)
        self._installer = SkillInstaller(store, http_timeout=http_timeout)
        self._create_locks: Dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _get_lock(self, skill_id: str) -> threading.Lock:
        with self._locks_guard:
            if skill_id not in self._create_locks:
                self._create_locks[skill_id] = threading.Lock()
            return self._create_locks[skill_id]

    # ─── AI 辅助 ───

    def create_via_ai(self, *, name: str, intent: str,
                      category: str = "custom",
                      tags: Optional[list] = None) -> Skill:
        """AI 辅助生成"""
        skill = self._ai.generate(name=name, intent=intent,
                                  category=category, tags=tags)
        return self._commit_new_skill(skill)

    # ─── 手动 ───

    def create_manual(self, data: Dict[str, Any]) -> Skill:
        """手动创建 — 直接接受 Skill 字典"""
        if not data.get("id"):
            raise SkillValidationError("缺少必填字段: id")
        if not data.get("name"):
            raise SkillValidationError("缺少必填字段: name")
        data.setdefault("source", "manual")
        data.setdefault("status", SkillStatus.DRAFT.value)
        skill = Skill.from_storage_dict(data)
        return self._commit_new_skill(skill)

    # ─── 安装 ───

    def install(self, source: str, *, force: bool = False) -> Skill:
        """从外部来源安装"""
        return self._installer.install(source, force=force)

    # ─── 内部 ───

    def _commit_new_skill(self, skill: Skill) -> Skill:
        """落盘新技能 (带防连点锁)"""
        lock = self._get_lock(skill.id)
        with lock:
            existing = self._store.get(skill.id)
            if existing:
                raise SkillAlreadyExistsError(skill.id)
            # 初始版本快照
            skill.versions = [SkillVersion(
                version=skill.version,
                content=skill.content,
                changelog="初始版本",
                created_by=skill.author,
                hash=hashlib.sha256(
                    skill.content.encode("utf-8")).hexdigest()[:16],
            )]
            skill.touch()
            self._store.upsert(skill)
            # 同步到 legacy
            self._store.sync_to_legacy_skills_json()
            track_event("skill_created", {
                "skill_id": skill.id, "source": skill.source,
                "category": skill.category,
            })
            emit_metric("yunshu_skill_create_total",
                        labels={"success": "true",
                                "mode": skill.source},
                        kind="counter")
            logger.info("[Creator] 技能已创建: %s (source=%s)",
                        skill.id, skill.source)
            return skill
