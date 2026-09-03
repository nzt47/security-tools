"""技能评审-消化扩展评估器（Digest Assessor）

对“新增/外来的技能”做自动“评审-消化”的扩展维度检查，覆盖基础三审
（reviewer.py：重复/安全正则/质量）之外的清单项：

安全维度（category="security"，规则化启发式 + 人工复核边界）：
    - 权限校验：声明与实际使用的敏感能力（env/网络/文件/子进程）是否一致
      → SEC_ENV_ACCESS / SEC_POPEN / SEC_PATH_TRAVERSAL
    - 攻击面排查：混淆执行、证书校验关闭、疑似外传 env → SEC_OBFUSCATED /
      SEC_SSL_UNVERIFIED / SEC_ENV_EXFIL
    - 数据合规：PII（手机号/身份证/邮箱）与密钥入参、敏感数据收集提示
      → DATA_PII / DATA_SECRET_IN_PARAMS / DATA_COLLECT_SENSITIVE

兼容性维度（category="compatibility"）：
    - 与原生/已有功能冲突：名称冲突 → NAT_NAME_CLASH / NAT_RESERVED_ID
    - 操作重叠（引发困惑/不稳定）：触发面/描述相似 → OVL_OPERATION_OVERLAP
    - 资源竞争/性能：无超时子进程与网络、无出口死循环 → RSC_*（仅 CODE 内容）
    - 交互冲突：与已启用技能共享触发词/工具声明 → INT_SHARED_TRIGGER
    - 与已安装技能重复：给出合并建议（沿用 DuplicateDetector）→ DUP_MERGE_RECOMMEND

定位与边界：
    - 全部为**确定性规则**（可单测、无 LLM 依赖）；输出 ReviewFinding，
      沿用 severity/category/code 结构；critical/error 视为“阻断项”，
      触发 review 状态降级到 WARN（须人工复核），发布门禁保持不变。
    - 规则是启发式护栏而非安全认证；代码级/多文件脚本的深度审计由
      extensions/security_checker + agent/code_review 在安装/脚本层负责。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .models import Skill, ReviewFinding, ContentType

# ═══════════════════════════════════════════════════════════════
#  工具
# ═══════════════════════════════════════════════════════════════

# 视为“代码类”的内容类型：对其做代码模式检查（其余类型跳过，避免误报）
_CODE_TYPES = {ContentType.PYTHON, ContentType.JAVASCRIPT, ContentType.SHELL}

_PATTERNS = {
    # —— 权限 / 攻击面 ——
    "env_access": re.compile(r"\b(?:os\.environ|getenv|environ\[)", re.I),
    "popen": re.compile(r"\bos\.popen\s*\(", re.I),
    "path_traversal": re.compile(r"(?:open|read_text|write_text|os\.path\.join)\s*\([^)]*\.\./", re.I),
    "ssl_unverified": re.compile(r"verify\s*=\s*False|_create_unverified_context", re.I),
    "net_client": re.compile(r"\b(?:requests|httpx|urllib\.request|http\.client|socket)\b", re.I),
    "b64": re.compile(r"(?:b64decode|base64\.decode|unhexlify|bytes\.fromhex)", re.I),
    "dynamic_exec": re.compile(r"\b(?:eval|exec|compile)\s*\(", re.I),
    # —— 数据合规（PII/敏感数据）——
    "cn_phone": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "cn_idcard": re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "collect_personal": re.compile(r"(手机号|电话号码|身份证|邮箱|通讯录|定位|地理位置|隐私|个人信息|银行卡)", re.I),
}
# 疑似密钥参数名（default_params 键或值）
_SECRET_KEY_RE = re.compile(r"(api[_-]?key|secret|token|password|passwd|credential)", re.I)
_SECRET_VALUE_RE = re.compile(r"(sk-[A-Za-z0-9]{8,}|[A-Za-z0-9_-]{24,})")

# 名称/触发面 token 化（与 reviewer 保持一致的轻量实现，避免重导入开销）
_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+")


def _tokens(text: str) -> set:
    out = set(_TOKEN_RE.findall((text or "").lower()))
    for ch in (text or ""):
        if "\u4e00" <= ch <= "\u9fff":
            out.add(ch)
    return out


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _finding(severity: str, category: str, code: str, message: str,
             location: Optional[str] = None) -> ReviewFinding:
    return ReviewFinding(severity=severity, category=category,
                         code=code, message=message, location=location)


# ═══════════════════════════════════════════════════════════════
#  安全/数据合规子评估
# ═══════════════════════════════════════════════════════════════

def _assess_security_and_compliance(skill: Skill) -> List[ReviewFinding]:
    """权限 / 攻击面 / 数据合规 规则检查（启发式，人工复核边界外注明）"""
    findings: List[ReviewFinding] = []
    content = skill.content or ""
    is_code = skill.content_type in _CODE_TYPES
    scanned = content if is_code else ""

    # ── 权限校验（声明 vs 使用：无权限字段时以“需人工确认”信息级提示）──
    if is_code and _PATTERNS["env_access"].search(scanned):
        findings.append(_finding(
            "info", "security", "SEC_ENV_ACCESS",
            "内容读取环境变量(os.environ/getenv)——请确认不会泄露本机密钥；"
            "若确需访问系统密钥，应走统一密钥服务而非直接读取", "content"))
    if is_code and _PATTERNS["popen"].search(scanned):
        findings.append(_finding(
            "warn", "security", "SEC_POPEN",
            "使用 os.popen——无 shell 隔离，存在命令注入风险，建议改 subprocess 列表传参", "content"))
    if is_code and _PATTERNS["path_traversal"].search(scanned):
        findings.append(_finding(
            "warn", "security", "SEC_PATH_TRAVERSAL",
            "疑似拼接 ../ 路径访问——存在目录穿越风险，请校验并归一化路径", "content"))

    # ── 攻击面排查 ──
    if is_code and _PATTERNS["ssl_unverified"].search(scanned):
        findings.append(_finding(
            "warn", "security", "SEC_SSL_UNVERIFIED",
            "关闭了 TLS 证书校验(verify=False)——中间人风险，仅在可信内网且明确需要时使用", "content"))
    env_used = is_code and _PATTERNS["env_access"].search(scanned)
    if env_used and _PATTERNS["net_client"].search(scanned):
        findings.append(_finding(
            "warn", "security", "SEC_ENV_EXFIL",
            "同时读取环境变量并发起网络请求——请人工确认不存在密钥/敏感数据外传", "content"))
    if is_code and _PATTERNS["b64"].search(scanned) and _PATTERNS["dynamic_exec"].search(scanned):
        findings.append(_finding(
            "warn", "security", "SEC_OBFUSCATED",
            "存在 base64 解码与动态执行(eval/exec)组合——疑似混淆代码，需人工审查", "content"))

    # ── 数据合规（法规/标准：PII 处理、敏感默认参数、收集告知）──
    blob = "\n".join([content, skill.description or "",
                      " ".join(skill.tags)])
    pii_hits = 0
    if _PATTERNS["cn_phone"].search(blob) or _PATTERNS["cn_idcard"].search(blob) \
            or _PATTERNS["email"].search(blob):
        pii_hits = 1
        findings.append(_finding(
            "warn", "security", "DATA_PII",
            "内容/示例疑似包含个人敏感信息(手机号/身份证/邮箱)——存储、日志与训练处理须脱敏，"
            "并符合数据合规要求", "content"))
    params_blob = " ".join(f"{k}={v}" for k, v in (skill.default_params or {}).items())
    secret_in_params = False
    for k, v in (skill.default_params or {}).items():
        sv = str(v)
        if _SECRET_KEY_RE.search(str(k)) and sv:
            secret_in_params = True
            break
        if sv and _SECRET_VALUE_RE.match(sv) and _SECRET_VALUE_RE.match(sv).group(0) == sv.strip():
            secret_in_params = True
            break
    if secret_in_params:
        findings.append(_finding(
            "warn", "security", "DATA_SECRET_IN_PARAMS",
            "default_params 含疑似密钥类参数——默认值不应存真实密钥，请改为引用环境/密钥服务", "default_params"))
    if _PATTERNS["collect_personal"].search(blob) and not skill.is_sensitive:
        findings.append(_finding(
            "info", "security", "DATA_COLLECT_SENSITIVE",
            "技能描述/内容涉及个人数据收集——建议标记 is_sensitive=true 以启用隔离注入与合规处理",
            "is_sensitive"))
    _ = pii_hits  # 计数已并入 finding，保留占位便于扩展统计
    _ = params_blob
    return findings


# ═══════════════════════════════════════════════════════════════
#  代码级审查（接入 agent.code_review）
# ═══════════════════════════════════════════════════════════════

def _code_review_dimensions(skill: Skill) -> List[str]:
    """按技能类型选择 code_review 维度：
    - 所有代码类：安全 / 性能
    - python/js/shell：+ 可维护性
    - python/js 且定义了函数/类/导出（对外 API）：+ API兼容性
    - python/js 且含测试形态（def test_/pytest/unittest/describe/it/…）：+ 测试
    """
    ct = skill.content_type
    if ct not in _CODE_TYPES:
        return []
    content = skill.content or ""
    dims = ["安全", "性能", "可维护性"]
    api_like = bool(re.search(
        r"\b(?:def\s+\w+\s*\(|class\s+\w+|function\s+\w*\(|"
        r"export\s+(?:default\s+)?(?:function|const|class)|"
        r"async\s+function\s+\w*\()", content))
    test_like = bool(re.search(
        r"(?:def\s+test_|\bpytest\b|\bunittest\b|describe\s*\(|\bit\s*\(|\btest\s*\(|go test)",
        content))
    if api_like and ct in (ContentType.PYTHON, ContentType.JAVASCRIPT):
        dims.append("API兼容性")
    if test_like and ct in (ContentType.PYTHON, ContentType.JAVASCRIPT):
        dims.append("测试")
    return dims


def _assess_code_review(skill: Skill) -> List[ReviewFinding]:
    """对 CODE 类内容运行 code_review（按类型选择维度），并入 digest。

    code_review.code_review(diff=<content>) 支持纯文本审查（无文件系统依赖）；
    输出为咨询性发现（category="code"，安全维度 warn、其余 info），
    与 reviewer 的正则安全扫描互补（后者负责关键阻断）。
    """
    if skill.content_type not in _CODE_TYPES or not (skill.content or "").strip():
        return []
    try:
        from agent.code_review import code_review
        result = code_review(diff=skill.content, dimensions=_code_review_dimensions(skill))
    except Exception:
        return []
    findings: List[ReviewFinding] = []
    for dim in (result or {}).get("dimensions", []) or []:
        dimension = str(dim.get("dimension", ""))
        if dimension not in ("安全", "性能", "可维护性", "API兼容性", "测试"):
            continue
        severity = "warn" if dimension == "安全" else "info"
        for f in dim.get("findings", []) or []:
            desc = str(f.get("description", "") or "").strip()
            if not desc:
                continue
            suggestion = str(f.get("suggestion", "") or "").strip()
            message = f"[代码审查·{dimension}] {desc}"
            if suggestion:
                message += f"。建议：{suggestion}"
            line = f.get("line")
            location = f"content@{line}" if line is not None else "content"
            findings.append(_finding(
                severity, "code", f"CR_{dimension}", message, location))
    return findings


# 外来安装 scheme（触发安装级安全预检并入 digest）
_EXTERNAL_SCHEMES = ("github:", "url:", "local:", "registry:", "zip", "mcp:")
_EXTERNAL_CATEGORIES = ("claude", "community", "mcp")


def _is_external(skill: Skill) -> bool:
    source = str(skill.source or "").strip().lower()
    if any(source.startswith(s) for s in _EXTERNAL_SCHEMES):
        return True
    category = str(getattr(skill.category, "value", skill.category) if not isinstance(skill.category, str) else skill.category)
    return (category or "").lower() in _EXTERNAL_CATEGORIES


def _assess_external_precheck(skill: Skill) -> List[ReviewFinding]:
    """把「外来技能安装安全预检」（extensions.security_checker）结果并入 digest。

    仅对来自 github/url/local/registry/zip/mcp 或 claude/community 类别的外来技能
    执行（自建/手写技能不受此严格门控约束）。映射：高风险→error（阻断，须人工
    复核）、中风险→warn、低风险→info；代码内容走 scan_code_for_threats，
    描述另做权限/数据合规关键词预检。
    """
    if not _is_external(skill):
        return []
    findings: List[ReviewFinding] = []
    sev_map = {"高风险": "error", "中风险": "warn", "低风险": "info"}
    try:
        from agent.extensions.security_checker import SkillSecurityChecker
        checker = SkillSecurityChecker()
        code = skill.content or ""
        if code.strip():
            for f in checker.scan_code_for_threats(code, f"{skill.id}"):
                findings.append(_finding(
                    sev_map.get(str(f.get("severity", "")), "info"),
                    "security", "SEC_EXT_INSTALL",
                    f"[外来技能安装预检·{f.get('category', '')}] "
                    f"{f.get('pattern', '')}",
                    f"content@{f.get('location', '')}"))
        # 权限/数据合规关键词预检（与描述联动）
        info = {"description": skill.description or "", "name": skill.name or ""}
        for issue in (checker.check_permissions(info) or []) + (checker.check_data_compliance(info) or []):
            findings.append(_finding(
                sev_map.get(str(issue.get("severity", "")), "info"),
                "security", "SEC_EXT_INSTALL",
                f"[外来技能安装预检·{issue.get('category', '')}] {issue.get('message', '')}",
                "description"))
    except Exception:
        # 预检器不可用时不阻断主评估
        return []
    return findings


# ═══════════════════════════════════════════════════════════════
#  兼容性/资源/交互子评估
# ═══════════════════════════════════════════════════════════════

# 从内容/描述抽取“触发词/工具声明”（slash 命令、tool:xx、调用 xx 等）
_TRIGGER_RE = re.compile(r"(?:^|\s)(?:slash|命令|tool|工具|函数|调用)[：:\s]*([a-z][a-z0-9_\-]{2,})", re.I)


def _claimed_triggers(skill: Skill) -> set:
    text = f"{skill.name} {skill.description or ''} {skill.content or ''}"
    out = set(m.group(1).lower() for m in _TRIGGER_RE.finditer(text))
    if skill.tags:
        out.update(t.lower() for t in skill.tags if re.match(r"^[a-z][a-z0-9_\-]{2,}$", t))
    return out


def _assess_compatibility(skill: Skill, others: List[Skill],
                          reserved: Optional[List[str]] = None) -> List[ReviewFinding]:
    """原生冲突 / 操作重叠 / 资源竞争 / 交互冲突 / 重复建议"""
    findings: List[ReviewFinding] = []
    content = skill.content or ""
    is_code = skill.content_type in _CODE_TYPES
    scanned = content if is_code else ""

    # 1) 与原生/保留 ID 冲突
    if reserved and skill.id in reserved:
        findings.append(_finding(
            "error", "compatibility", "NAT_RESERVED_ID",
            f"ID '{skill.id}' 与系统保留标识冲突，请更换命名", "id"))

    # 2) 与已装技能名称冲突 / 操作重叠 / 交互冲突
    own_tokens = _tokens(f"{skill.name} {skill.description}")
    own_triggers = _claimed_triggers(skill)
    overlap_ids: List[str] = []
    clash_ids: List[str] = []
    share_ids: List[str] = []
    for other in others:
        if other.id == skill.id:
            continue
        o_name = (other.name or "").lower()
        if o_name and o_name == (skill.name or "").lower() and other.id != skill.id:
            clash_ids.append(other.id)
        if o_name == skill.id or (skill.name or "").lower() == other.id:
            # 名字与对方 id 互换式冲突
            clash_ids.append(other.id)
            continue
        sim = _jaccard(own_tokens, _tokens(f"{other.name} {other.description}"))
        if sim >= 0.45:
            overlap_ids.append(f"{other.id}({sim:.0%})")
        shared = own_triggers & _claimed_triggers(other)
        if other.enabled and shared:
            share_ids.append(f"{other.id}:{','.join(sorted(shared)[:3])}")
    if clash_ids:
        findings.append(_finding(
            "warn", "compatibility", "NAT_NAME_CLASH",
            f"与其他技能重名/ID互撞：{', '.join(clash_ids)}——易混淆或互相覆盖", "name"))
    if overlap_ids:
        findings.append(_finding(
            "warn", "compatibility", "OVL_OPERATION_OVERLAP",
            f"与以下技能操作面重叠：{', '.join(overlap_ids[:5])}——可能让用户困惑或触发不稳定",
            "description"))
    if share_ids:
        findings.append(_finding(
            "warn", "compatibility", "INT_SHARED_TRIGGER",
            f"与已启用技能共享触发词/工具声明：{', '.join(share_ids[:5])}——存在交互抢占冲突风险",
            "content"))

    # 3) 资源竞争/性能（仅代码内容）
    if is_code:
        if re.search(r"\bwhile\s+True\b", scanned) and "break" not in scanned:
            findings.append(_finding(
                "warn", "compatibility", "RSC_LOOP_NO_EXIT",
                "存在 while True 且未见到 break——可能死循环耗尽资源，请加退出条件/超时", "content"))
        sub_calls = re.findall(r"\bsubprocess\.(?:run|Popen|call|check_output|check_call)\s*\(", scanned)
        if sub_calls and "timeout" not in scanned:
            findings.append(_finding(
                "warn", "compatibility", "RSC_SUBPROCESS_NO_TIMEOUT",
                f"存在 {len(sub_calls)} 处子进程调用且未见 timeout——挂起会阻塞请求，请设超时", "content"))
        net_calls = re.findall(
            r"\b(?:requests\.(?:get|post|put|delete|request)|httpx\.(?:get|post|put)|urllib\.request\.urlopen)\s*\(",
            scanned)
        if net_calls and "timeout" not in scanned:
            findings.append(_finding(
                "warn", "compatibility", "RSC_NET_NO_TIMEOUT",
                f"存在 {len(net_calls)} 处网络调用且未见 timeout——无超时会占用资源/拖慢响应", "content"))

    # 4) 与已安装技能重复 → 合并建议（轻量复用内容相似）
    try:
        from .reviewer import SkillReviewer
        suspects = SkillReviewer().find_duplicates_for(skill, others)
    except Exception:
        suspects = []
    if suspects:
        tops = suspects[:3]
        findings.append(_finding(
            "info", "duplicate", "DUP_MERGE_RECOMMEND",
            "与已安装技能存在高度内容重复，建议合并："
            + "; ".join(f"{s['other_id']}({s['jaccard']:.0%})" for s in tops),
            "content"))
    return findings


# ═══════════════════════════════════════════════════════════════
#  门面
# ═══════════════════════════════════════════════════════════════

@dataclass
class DigestAssessment:
    """评审-消化扩展评估结果"""
    findings: List[ReviewFinding] = field(default_factory=list)
    compatibility_score: float = 100.0
    blocked: bool = False          # 存在 critical/error 级阻断项
    dimension_summary: Dict[str, Any] = field(default_factory=dict)


_PENALTY = {"critical": 40, "error": 20, "warn": 8, "info": 2}
_BLOCK_SEV = {"critical", "error"}


class SkillDigestAssessor:
    """评审-消化扩展评估器门面"""

    def assess(self, skill: Skill, others: Optional[List[Skill]] = None,
               reserved: Optional[List[str]] = None) -> DigestAssessment:
        """对单个技能执行扩展评估（安全/合规 + 兼容性）

        Returns:
            DigestAssessment: findings（含 security/compatibility/duplicate 三类
            扩展发现）、compatibility_score（100 - 扣分）、blocked（是否有阻断项）。
        """
        others = others or []
        findings: List[ReviewFinding] = []

        sec_findings = _assess_security_and_compliance(skill)
        compat_findings = _assess_compatibility(skill, others, reserved=reserved)
        code_findings = _assess_code_review(skill)
        ext_findings = _assess_external_precheck(skill)
        findings.extend(sec_findings)
        findings.extend(compat_findings)
        findings.extend(code_findings)
        findings.extend(ext_findings)

        # 兼容性评分仅按 compatibility 类扣分
        compat_penalty = 0
        for f in compat_findings:
            compat_penalty += _PENALTY.get(f.severity, 0)
        compat_score = max(0.0, 100.0 - compat_penalty)

        # 阻断项：security/compatibility 扩展发现中 critical/error
        blocked = any(f.severity in _BLOCK_SEV for f in findings)

        # 分维度摘要（供 UI/报告）
        summary: Dict[str, Any] = {}
        for f in findings:
            bucket = summary.setdefault(f.category, {})
            bucket["count"] = bucket.get("count", 0) + 1
            bucket.setdefault("severities", []).append(f.severity)
        for bucket in summary.values():
            bucket["severities"] = sorted(set(bucket["severities"]))

        return DigestAssessment(
            findings=findings,
            compatibility_score=round(compat_score, 1),
            blocked=blocked,
            dimension_summary=summary,
        )
