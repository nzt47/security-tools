"""技能审核器 — 三重审核

1. 重复检测 (DuplicateDetector): Jaccard 相似度 + 内容哈希
2. 安全扫描 (SecurityScanner): 正则规则覆盖命令注入/XSS/SQL注入/危险导入/硬编码密钥
3. 质量评估 (QualityAssessor): 文档/错误处理/参数校验/测试覆盖 多维评分

设计原则:
    - 边界显性化: 高危安全问题抛 SkillSecurityError
    - 可观测: 每次审核输出结构化日志 + 业务指标
    - 可配置: 阈值与规则可通过 config.yaml 调整
"""

from __future__ import annotations
import hashlib
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .models import (
    Skill,
    ReviewResult,
    ReviewFinding,
    ReviewStatus,
    SkillStatus,
    ContentType,
)
from .exceptions import SkillSecurityError, SkillReviewError
from .observability import logger, emit_metric, traced_action


# ──────────────────────────────────────────────
# 1. 重复检测
# ──────────────────────────────────────────────

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+")


def _tokenize(text: str) -> set:
    """简单分词 (中英文混合，按字符与单词切分)"""
    if not text:
        return set()
    tokens = set(_TOKEN_RE.findall(text.lower()))
    # 中文按单字
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            tokens.add(ch)
    return tokens


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


class DuplicateDetector:
    """重复检测器"""

    def __init__(self, threshold: float = 0.7):
        self.threshold = threshold

    def detect(self, skill: Skill, others: List[Skill]) -> Tuple[float, List[str]]:
        """返回 (重复度0-100, 疑似重复的技能ID列表)"""
        own_hash = _content_hash(skill.content)
        own_tokens = _tokenize(
            f"{skill.name} {skill.description} {skill.content}"
        )
        duplicate_with: List[str] = []
        max_sim = 0.0

        for other in others:
            if other.id == skill.id:
                continue
            # 完全相同 → 100
            if own_hash and _content_hash(other.content) == own_hash:
                duplicate_with.append(other.id)
                max_sim = 1.0
                continue
            sim = _jaccard(own_tokens, _tokenize(
                f"{other.name} {other.description} {other.content}"
            ))
            if sim >= self.threshold:
                duplicate_with.append(other.id)
                if sim > max_sim:
                    max_sim = sim

        return max_sim * 100.0, duplicate_with


# ──────────────────────────────────────────────
# 2. 安全扫描
# ──────────────────────────────────────────────

# 安全规则 — 灵感来源: 项目 dangerous_commands.json + OWASP Top 10
_SECURITY_PATTERNS: List[Dict[str, Any]] = [
    # 命令注入 / 危险 shell
    {"id": "SEC_CMD_INJECTION", "severity": "critical",
     "pattern": re.compile(r"\b(?:rm\s+-rf\s+/|mkfs|dd\s+if=|:()\{\s*:\|:&\s*\};:)", re.I),
     "msg": "危险 shell 命令 (rm -rf / fork bomb 等)"},
    {"id": "SEC_EVAL", "severity": "error",
     "pattern": re.compile(r"\b(?:eval|exec)\s*\(", re.I),
     "msg": "使用 eval/exec — 可能有代码注入风险"},
    {"id": "SEC_OS_SYSTEM", "severity": "warn",
     "pattern": re.compile(r"\b(?:os\.system|subprocess\.call.*shell\s*=\s*True)\b", re.I),
     "msg": "使用 os.system 或 shell=True — 命令注入风险"},

    # XSS
    {"id": "SEC_XSS_INNERHTML", "severity": "error",
     "pattern": re.compile(r"\.innerHTML\s*=", re.I),
     "msg": "直接赋值 innerHTML — XSS 风险"},
    {"id": "SEC_XSS_DOC_WRITE", "severity": "error",
     "pattern": re.compile(r"document\.write\s*\(", re.I),
     "msg": "document.write — XSS 风险"},

    # SQL 注入
    {"id": "SEC_SQL_CONCAT", "severity": "error",
     "pattern": re.compile(r"(?:execute|query|cursor\.execute)\s*\(\s*[\"'`]?\s*.*?\+\s*\w+", re.I),
     "msg": "SQL 字符串拼接 — 注入风险"},

    # 硬编码密钥
    {"id": "SEC_HARDCODED_SECRET", "severity": "warn",
     "pattern": re.compile(r"(?:api[_-]?key|secret|token|password|passwd)\s*[=:]\s*[\"'`][^\"'`]{8,}[\"'`]", re.I),
     "msg": "疑似硬编码密钥/口令"},

    # 危险导入
    {"id": "SEC_DANGEROUS_IMPORT", "severity": "warn",
     "pattern": re.compile(r"\b(?:import\s+pickle|from\s+pickle|import\s+marshal|from\s+marshal)\b", re.I),
     "msg": "导入 pickle/marshal — 反序列化攻击风险"},

    # 网络后门
    {"id": "SEC_NETWORK_BACKDOOR", "severity": "error",
     "pattern": re.compile(r"socket\.socket\s*\(\s*socket\.AF_INET.*SOCK_STREAM.*\)\.bind\s*\(\s*\(['\"]0\.0\.0\.0['\"]", re.I | re.S),
     "msg": "监听 0.0.0.0 — 可能是后门"},
]


class SecurityScanner:
    """安全扫描器"""

    def __init__(self, *, block_on_critical: bool = True):
        self.block_on_critical = block_on_critical

    def scan(self, skill: Skill) -> Tuple[float, List[ReviewFinding]]:
        """返回 (安全评分 0-100, findings 列表)"""
        findings: List[ReviewFinding] = []
        critical_count = 0
        error_count = 0
        warn_count = 0

        # 扫描内容
        text_to_scan = "\n".join([
            skill.content or "",
            skill.description or "",
            skill.name or "",
        ])
        for rule in _SECURITY_PATTERNS:
            for m in rule["pattern"].finditer(text_to_scan):
                findings.append(ReviewFinding(
                    severity=rule["severity"],
                    category="security",
                    code=rule["id"],
                    message=rule["msg"],
                    location=f"content@{m.start()}",
                ))
                if rule["severity"] == "critical":
                    critical_count += 1
                elif rule["severity"] == "error":
                    error_count += 1
                elif rule["severity"] == "warn":
                    warn_count += 1

        # 危险依赖检查
        for dep in skill.dependencies:
            dep_lower = dep.lower()
            if any(d in dep_lower for d in ("keylogger", "backdoor", "reverse_shell")):
                findings.append(ReviewFinding(
                    severity="critical", category="security",
                    code="SEC_DANGEROUS_DEP",
                    message=f"危险依赖: {dep}",
                    location="dependencies",
                ))
                critical_count += 1

        # 评分计算: 100 - 30*critical - 15*error - 5*warn
        score = max(0.0, 100.0 - 30 * critical_count - 15 * error_count - 5 * warn_count)

        # 高危直接抛错 (按硬约束要求)
        if self.block_on_critical and critical_count > 0:
            raise SkillSecurityError(
                f"技能 {skill.id} 含 {critical_count} 个严重安全风险，已阻止发布",
                findings=[f.model_dump() for f in findings if f.severity == "critical"],
            )

        return score, findings


# ──────────────────────────────────────────────
# 3. 质量评估
# ──────────────────────────────────────────────

class QualityAssessor:
    """质量评估器 — 多维度评分"""

    def assess(self, skill: Skill) -> Tuple[float, List[ReviewFinding]]:
        findings: List[ReviewFinding] = []
        score = 0.0

        # 文档 (30 分)
        if skill.description and len(skill.description) >= 20:
            score += 15
        else:
            findings.append(ReviewFinding(
                severity="warn", category="quality", code="QUAL_SHORT_DESC",
                message="描述过短 (<20 字符)，建议补充用途与使用场景",
                location="description",
            ))
        if skill.content and len(skill.content) >= 100:
            score += 15
        else:
            findings.append(ReviewFinding(
                severity="warn", category="quality", code="QUAL_THIN_CONTENT",
                message="内容过短 (<100 字符)，建议补充示例与说明",
                location="content",
            ))

        # 参数 schema (20 分)
        if skill.config_schema:
            score += 10
            if "properties" in skill.config_schema:
                score += 10
        else:
            findings.append(ReviewFinding(
                severity="info", category="quality", code="QUAL_NO_SCHEMA",
                message="未提供参数 JSON Schema，建议补充以便前端生成配置 UI",
                location="config_schema",
            ))

        # 错误处理 (20 分) — 仅适用于代码类内容
        if skill.content_type in (ContentType.PYTHON, ContentType.JAVASCRIPT):
            if re.search(r"\btry\s*[:{]", skill.content):
                score += 10
            else:
                findings.append(ReviewFinding(
                    severity="warn", category="quality",
                    code="QUAL_NO_TRY_CATCH",
                    message="代码内容缺少 try/except 错误处理",
                    location="content",
                ))
            if re.search(r"\braise\s+\w+|throw\s+new\s+\w+", skill.content):
                score += 10
            else:
                findings.append(ReviewFinding(
                    severity="info", category="quality",
                    code="QUAL_NO_RAISE",
                    message="建议在边界处显式抛出业务错误",
                    location="content",
                ))
        else:
            # 非代码内容，错误处理分给文档
            score += 20

        # 标签 (10 分)
        if len(skill.tags) >= 2:
            score += 10
        elif skill.tags:
            score += 5
        else:
            findings.append(ReviewFinding(
                severity="info", category="quality", code="QUAL_NO_TAGS",
                message="未设置标签，建议添加 2-5 个标签便于检索",
                location="tags",
            ))

        # 版本/作者 (10 分)
        if skill.version and skill.version != "0.0.0":
            score += 5
        if skill.author and skill.author != "unknown":
            score += 5

        # 依赖说明 (10 分)
        if not skill.dependencies:
            score += 10  # 无依赖本身是好实践
        else:
            # 有依赖但声明了 — 也给分
            score += 5
            findings.append(ReviewFinding(
                severity="info", category="quality", code="QUAL_HAS_DEPS",
                message=f"声明了 {len(skill.dependencies)} 个依赖，"
                        "请在文档中说明用途",
                location="dependencies",
            ))

        return min(100.0, score), findings


# ──────────────────────────────────────────────
# 4. 审核门面
# ──────────────────────────────────────────────

@dataclass
class ReviewThresholds:
    """审核阈值 (可从 config 注入)"""
    duplicate_max: float = 60.0     # 重复度 > 60% 标记为重复
    security_min: float = 70.0      # 安全分 < 70 标记为不通过
    quality_min: float = 50.0       # 质量分 < 50 标记为低质量
    overall_min: float = 60.0       # 综合分 < 60 不通过


class SkillReviewer:
    """技能审核器门面 — 协调三大子审核器"""

    def __init__(self, thresholds: Optional[ReviewThresholds] = None):
        self.thresholds = thresholds or ReviewThresholds()
        self.dup_detector = DuplicateDetector(
            threshold=self.thresholds.duplicate_max / 100.0
        )
        self.security_scanner = SecurityScanner(block_on_critical=True)
        self.quality_assessor = QualityAssessor()

    def review(self, skill: Skill, others: Optional[List[Skill]] = None) -> ReviewResult:
        """执行完整审核流程"""
        with traced_action("skill_review", skill_id=skill.id) as ctx:
            others = others or []
            findings: List[ReviewFinding] = []

            # 1) 重复检测
            dup_score, dup_with = self.dup_detector.detect(skill, others)
            if dup_score >= self.thresholds.duplicate_max:
                findings.append(ReviewFinding(
                    severity="error", category="duplicate",
                    code="DUPLICATE_HIGH",
                    message=f"与已有技能重复度 {dup_score:.1f}% (>= {self.thresholds.duplicate_max}%)",
                    location=",".join(dup_with) if dup_with else None,
                ))
            elif dup_score >= 30.0:
                findings.append(ReviewFinding(
                    severity="warn", category="duplicate",
                    code="DUPLICATE_SUSPECT",
                    message=f"与 {dup_with} 疑似重复 ({dup_score:.1f}%)",
                ))

            # 2) 安全扫描
            try:
                sec_score, sec_findings = self.security_scanner.scan(skill)
                findings.extend(sec_findings)
            except SkillSecurityError as e:
                # 严重安全问题直接返回 failed
                ctx["blocked"] = True
                emit_metric("yunshu_skill_review_total",
                            labels={"failure": "true", "reason": "security"},
                            kind="counter")
                result = ReviewResult(
                    status=ReviewStatus.FAILED,
                    score=0.0,
                    findings=[ReviewFinding(
                        severity="critical", category="security",
                        code="SEC_BLOCKED", message=str(e),
                    )],
                    duplicate_score=dup_score,
                    duplicate_with=dup_with,
                    security_score=0.0,
                    quality_score=0.0,
                    summary=f"安全审核未通过: {e}",
                )
                skill.review = result
                skill.status = SkillStatus.REJECTED
                logger.warning("[Reviewer] 技能 %s 因安全风险被拒绝", skill.id)
                return result

            # 3) 质量评估
            qual_score, qual_findings = self.quality_assessor.assess(skill)
            findings.extend(qual_findings)

            # 4) 综合评分 (加权: 安全 50% + 质量 30% + 原创性 20%)
            originality = 100.0 - dup_score
            overall = (sec_score * 0.5 + qual_score * 0.3 + originality * 0.2)

            # 5) 决策
            failed = (
                dup_score >= self.thresholds.duplicate_max
                or sec_score < self.thresholds.security_min
                or qual_score < self.thresholds.quality_min
                or overall < self.thresholds.overall_min
            )
            status = ReviewStatus.FAILED if failed else ReviewStatus.PASSED

            summary_parts = []
            if dup_score >= self.thresholds.duplicate_max:
                summary_parts.append(f"重复度过高 ({dup_score:.0f}%)")
            if sec_score < self.thresholds.security_min:
                summary_parts.append(f"安全分过低 ({sec_score:.0f})")
            if qual_score < self.thresholds.quality_min:
                summary_parts.append(f"质量分过低 ({qual_score:.0f})")
            summary = "；".join(summary_parts) if summary_parts else "审核通过"

            result = ReviewResult(
                status=status,
                score=overall,
                findings=findings,
                duplicate_score=dup_score,
                duplicate_with=dup_with,
                security_score=sec_score,
                quality_score=qual_score,
                summary=summary,
            )

            # 写回技能状态
            skill.review = result
            if status == ReviewStatus.PASSED:
                skill.status = SkillStatus.APPROVED
                emit_metric("yunshu_skill_review_total",
                            labels={"success": "true"}, kind="counter")
            else:
                skill.status = SkillStatus.REJECTED
                emit_metric("yunshu_skill_review_total",
                            labels={"failure": "true", "reason": "quality"},
                            kind="counter")

            ctx["score"] = overall
            ctx["status"] = status.value
            return result
