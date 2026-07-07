"""技能管理数据模型 (Pydantic v2)

设计原则:
    - 单一数据源: Skill 模型贯穿创建/审核/搜索/增强全流程
    - 向后兼容: 与 data/skills.json 旧字段对齐 (id/name/enabled/description/params)
    - 可观测: 所有模型支持 to_dict()/from_dict()，便于日志记录
"""

from __future__ import annotations
import enum
import re
from datetime import datetime
from typing import Optional, List, Dict, Any, Union
from pydantic import BaseModel, Field, field_validator, ConfigDict


# ──────────────────────────────────────────────
# 枚举
# ──────────────────────────────────────────────

class SkillCategory(str, enum.Enum):
    """技能来源分类"""
    BUILTIN = "builtin"          # 内置 (云枢官方)
    CUSTOM = "custom"            # 用户自定义
    CLAUDE = "claude"            # Claude Code 兼容技能
    COMMUNITY = "community"      # 社区/市场
    MCP = "mcp"                  # MCP 服务型技能
    AI_GENERATED = "ai_generated"  # AI 辅助生成


class SkillStatus(str, enum.Enum):
    """技能生命周期状态"""
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


class ReviewStatus(str, enum.Enum):
    """审核状态"""
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    WARN = "warn"


class ContentType(str, enum.Enum):
    """技能内容类型"""
    MARKDOWN = "markdown"
    PYTHON = "python"
    JAVASCRIPT = "javascript"
    YAML = "yaml"
    JSON = "json"
    SHELL = "shell"
    TEXT = "text"


# ──────────────────────────────────────────────
# 子模型
# ──────────────────────────────────────────────

# SemVer 简单校验: MAJOR.MINOR.PATCH[-prerelease]
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z\-\.]+)?(?:\+[0-9A-Za-z\-\.]+)?$")


class SkillVersion(BaseModel):
    """技能版本记录"""
    version: str = Field(..., description="语义化版本号")
    content: str = Field("", description="该版本内容快照")
    changelog: str = Field("", description="变更说明")
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    created_by: str = Field("system", description="版本创建者")
    hash: str = Field("", description="内容哈希 (用于变更检测)")

    @field_validator("version")
    @classmethod
    def _validate_semver(cls, v: str) -> str:
        if not _SEMVER_RE.match(v):
            raise ValueError(f"非法语义化版本号: {v} (应为 MAJOR.MINOR.PATCH)")
        return v


class ReviewFinding(BaseModel):
    """单条审核发现"""
    severity: str = Field(..., description="info/warn/error/critical")
    category: str = Field(..., description="duplicate/security/quality")
    code: str = Field(..., description="具体问题码")
    message: str
    location: Optional[str] = None  # 文件:行号 或 字段路径


class ReviewResult(BaseModel):
    """审核结果"""
    status: ReviewStatus = ReviewStatus.PENDING
    score: float = Field(0.0, ge=0.0, le=100.0, description="综合质量评分")
    findings: List[ReviewFinding] = Field(default_factory=list)

    # 三大维度细分
    duplicate_score: float = Field(0.0, ge=0.0, le=100.0,
                                   description="重复度 (0=完全原创, 100=完全重复)")
    duplicate_with: List[str] = Field(default_factory=list,
                                      description="疑似重复的技能ID列表")
    security_score: float = Field(100.0, ge=0.0, le=100.0,
                                  description="安全评分 (100=无风险)")
    quality_score: float = Field(0.0, ge=0.0, le=100.0,
                                 description="质量评分")

    reviewed_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    reviewed_by: str = Field("auto")
    summary: str = ""

    model_config = ConfigDict(use_enum_values=True)


class SkillMetrics(BaseModel):
    """技能运行时指标"""
    usage_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    success_rate: float = Field(0.0, ge=0.0, le=1.0)
    avg_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    last_used_at: Optional[str] = None
    last_latency_ms: Optional[float] = None
    # 参数级追踪 — 用于 Item 4 自动参数迭代优化
    # 键是参数组合的 8 位哈希，值结构: {params, success, failure, total_latency_ms, last_used_at}
    param_stats: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    # 黑名单参数组合 — 历史成功率极低，自动迭代时跳过
    avoid_params: List[Dict[str, Any]] = Field(default_factory=list)

    def record(self, success: bool, latency_ms: float,
               params_used: Optional[Dict[str, Any]] = None) -> None:
        """记录一次执行

        Args:
            success: 是否成功
            latency_ms: 延迟毫秒
            params_used: 本次使用的参数组合 (None 表示用 default_params)
        """
        self.usage_count += 1
        if success:
            self.success_count += 1
        else:
            self.failure_count += 1
        # 滚动平均 (简化版；生产可替换为真实的 P95 计算)
        self.avg_latency_ms = (
            (self.avg_latency_ms * (self.usage_count - 1) + latency_ms)
            / self.usage_count
        )
        self.last_latency_ms = latency_ms
        self.last_used_at = datetime.now().isoformat()
        if self.usage_count > 0:
            self.success_rate = self.success_count / self.usage_count
        # 参数级追踪
        if params_used is not None:
            self._record_param_stats(success, latency_ms, params_used)

    def _record_param_stats(self, success: bool, latency_ms: float,
                            params: Dict[str, Any]) -> None:
        """记录单条参数组合的执行情况"""
        import hashlib
        import json as _json
        try:
            key_str = _json.dumps(params, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            key_str = str(sorted(params.items()))
        key = hashlib.md5(key_str.encode("utf-8")).hexdigest()[:8]
        stat = self.param_stats.get(key, {
            "params": params,
            "success": 0,
            "failure": 0,
            "total_latency_ms": 0.0,
            "last_used_at": None,
        })
        stat["success"] = stat.get("success", 0) + (1 if success else 0)
        stat["failure"] = stat.get("failure", 0) + (0 if success else 1)
        stat["total_latency_ms"] = stat.get("total_latency_ms", 0.0) + latency_ms
        stat["last_used_at"] = datetime.now().isoformat()
        self.param_stats[key] = stat


# ──────────────────────────────────────────────
# 主模型
# ──────────────────────────────────────────────

class Skill(BaseModel):
    """技能主模型 — 贯穿全生命周期"""
    # 标识
    id: str = Field(..., min_length=1, max_length=128,
                    description="技能唯一ID (kebab-case)")
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field("", max_length=2000)

    # 分类
    category: SkillCategory = SkillCategory.CUSTOM
    tags: List[str] = Field(default_factory=list)
    status: SkillStatus = SkillStatus.DRAFT
    enabled: bool = True

    # 版本
    version: str = "0.1.0"
    versions: List[SkillVersion] = Field(default_factory=list)

    # 作者/来源
    author: str = "unknown"
    source: str = "manual"  # manual / ai_assisted / github:user/repo / url:... / local:...
    source_url: str = ""
    install_path: str = ""

    # 内容
    content: str = Field("", description="技能主体内容 (markdown/code)")
    content_type: ContentType = ContentType.MARKDOWN
    config_schema: Dict[str, Any] = Field(
        default_factory=dict,
        description="参数 JSON Schema (用于校验 default_params)"
    )
    output_schema: Dict[str, Any] = Field(
        default_factory=dict,
        description="输出 JSON Schema (用于 ExecutionResult 后置验证门控)"
    )
    default_params: Dict[str, Any] = Field(default_factory=dict)
    dependencies: List[Union[str, Dict[str, Any]]] = Field(
        default_factory=list,
        description="Python/系统依赖 — 支持纯字符串(无版本约束)或带版本约束的 dict",
    )

    # 审核
    review: Optional[ReviewResult] = None

    # 指标
    metrics: SkillMetrics = Field(default_factory=SkillMetrics)

    # 时间戳
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    installed_at: Optional[str] = None

    model_config = ConfigDict(use_enum_values=True)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9][a-z0-9_\-]*$", v):
            raise ValueError(
                "技能ID必须为 kebab_case: 小写字母/数字/下划线/连字符，"
                f"且以字母或数字开头 (got: {v})"
            )
        return v

    @field_validator("version")
    @classmethod
    def _validate_version(cls, v: str) -> str:
        if not _SEMVER_RE.match(v):
            raise ValueError(f"非法版本号: {v} (应为 MAJOR.MINOR.PATCH)")
        return v

    def touch(self) -> None:
        """更新 updated_at 时间戳"""
        self.updated_at = datetime.now().isoformat()

    def to_storage_dict(self) -> Dict[str, Any]:
        """转换为可序列化到 JSON 的字典"""
        return self.model_dump()

    @classmethod
    def from_storage_dict(cls, data: Dict[str, Any]) -> "Skill":
        """从存储字典恢复 (容忍旧字段缺失)"""
        # 兼容旧 data/skills.json 的简单结构
        if "category" not in data:
            data["category"] = "builtin" if data.get("builtin") else "custom"
        if "version" not in data:
            data["version"] = "0.1.0"
        if "content_type" not in data:
            data["content_type"] = "markdown"
        # 旧字段映射
        if "params" in data and "default_params" not in data:
            data["default_params"] = data["params"]
        return cls(**data)


# ──────────────────────────────────────────────
# 搜索
# ──────────────────────────────────────────────

class SkillSearchParams(BaseModel):
    """技能搜索参数"""
    query: str = Field("", description="全文搜索 (名称/描述/标签)")
    categories: List[SkillCategory] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    statuses: List[SkillStatus] = Field(default_factory=list)
    enabled_only: bool = False
    min_quality_score: float = Field(0.0, ge=0.0, le=100.0)
    sort_by: str = Field("updated_at",
                         description="updated_at/usage_count/quality_score/name")
    sort_desc: bool = True
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=200)

    model_config = ConfigDict(use_enum_values=True)


class SkillSearchResult(BaseModel):
    """技能搜索结果"""
    items: List[Skill]
    total: int
    page: int
    page_size: int
    elapsed_ms: float
