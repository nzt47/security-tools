"""agent.extensions.security_checker 模块单元测试

覆盖内容：
1. SecurityAssessment 等级/分数演进（高风险→BLOCK、中/低风险→WARNING、分数取 min）
2. CompatibilityAnalysis 冲突/重叠/警告记录
3. scan_code_for_threats 危险模式扫描（命中与未命中）
4. scan_directory 目录扫描（正常与读取异常）
5. check_permissions / check_data_compliance 关键词命中
6. assess_security 来源分支（local/github/url/其他）+ 临时目录扫描 + 建议生成
7. analyze_compatibility 无 store / 名称冲突 / 功能重叠 / 系统原生冲突
8. perform_full_check 完整检查（可安装与阻止安装）
9. _create_security_checker / get_security_checker 单例与降级路径
10. 模块内置自测函数 test_security_checker 真实执行
"""
import logging
from pathlib import Path

import pytest

import agent.extensions.security_checker as sc_module
from agent.extensions.security_checker import (
    SecurityAssessment,
    CompatibilityAnalysis,
    SkillSecurityChecker,
    _create_security_checker,
    _trace_id,
)


# ---------------------------------------------------------------------------
# _trace_id
# ---------------------------------------------------------------------------
def test_trace_id_length_and_uniqueness():
    """_trace_id 返回 16 位十六进制且每次生成不同"""
    ids = {_trace_id() for _ in range(100)}
    assert all(len(i) == 16 for i in ids)
    assert len(ids) == 100


# ---------------------------------------------------------------------------
# SecurityAssessment
# ---------------------------------------------------------------------------
def test_security_assessment_defaults():
    """默认等级 PASS、分数 100、无问题无建议"""
    assessment = SecurityAssessment()
    assert assessment.level == "PASS"
    assert assessment.score == 100
    assert assessment.issues == []
    assert assessment.suggestions == []


def test_add_issue_high_risk_sets_block():
    """高风险问题 → 等级 BLOCK、分数降为 40"""
    assessment = SecurityAssessment()
    assessment.add_issue("代码执行", "检测到危险模式", "高风险", "subprocess.run('x')")
    assert assessment.level == "BLOCK"
    assert assessment.score == 40
    assert assessment.issues[0]["category"] == "代码执行"
    assert assessment.issues[0]["code_snippet"] == "subprocess.run('x')"


def test_add_issue_medium_risk_sets_warning():
    """中风险问题 → 等级 WARNING、分数降为 70"""
    assessment = SecurityAssessment()
    assessment.add_issue("网络请求", "存在网络请求", "中风险")
    assert assessment.level == "WARNING"
    assert assessment.score == 70


def test_add_issue_low_risk_sets_warning():
    """低风险问题 → 等级 WARNING、分数降为 90"""
    assessment = SecurityAssessment()
    assessment.add_issue("数据处理", "可能收集数据", "低风险")
    assert assessment.level == "WARNING"
    assert assessment.score == 90


def test_add_issue_score_keeps_minimum():
    """高风险后再加低/中风险，等级与分数保持最低值（min 分支）"""
    assessment = SecurityAssessment()
    assessment.add_issue("c", "m1", "高风险")
    assessment.add_issue("c", "m2", "低风险")
    assert assessment.level == "BLOCK"
    assert assessment.score == 40
    assessment.add_issue("c", "m3", "中风险")
    assert assessment.level == "BLOCK"
    assert assessment.score == 40


def test_add_issue_medium_after_warning_keeps_level():
    """已为 WARNING 时再加中风险，等级不再变化（if level == PASS 的 False 分支）"""
    assessment = SecurityAssessment()
    assessment.add_issue("c", "m1", "中风险")
    assessment.add_issue("c", "m2", "中风险")
    assert assessment.level == "WARNING"
    assert assessment.score == 70


def test_add_suggestion_and_to_dict():
    """add_suggestion 与 to_dict 序列化"""
    assessment = SecurityAssessment()
    assessment.add_suggestion("请审查代码")
    data = assessment.to_dict()
    assert data["level"] == "PASS"
    assert data["score"] == 100
    assert data["suggestions"] == ["请审查代码"]
    assert data["issues"] == []


# ---------------------------------------------------------------------------
# CompatibilityAnalysis
# ---------------------------------------------------------------------------
def test_compatibility_analysis_defaults():
    """默认兼容、无冲突无重叠无警告"""
    analysis = CompatibilityAnalysis()
    assert analysis.compatible is True
    assert analysis.conflicts == []
    assert analysis.overlaps == []
    assert analysis.warnings == []


def test_add_conflict():
    """添加冲突 → compatible 变为 False"""
    analysis = CompatibilityAnalysis()
    analysis.add_conflict("old", "new", "名称冲突")
    assert analysis.compatible is False
    assert analysis.conflicts == [
        {"existing_skill": "old", "new_skill": "new", "reason": "名称冲突"}
    ]


def test_add_overlap():
    """添加功能重叠记录"""
    analysis = CompatibilityAnalysis()
    analysis.add_overlap("old", "new", "网络功能")
    assert analysis.overlaps == [
        {"existing_skill": "old", "new_skill": "new", "feature": "网络功能"}
    ]


def test_add_warning_and_to_dict():
    """添加警告并验证 to_dict 序列化"""
    analysis = CompatibilityAnalysis()
    analysis.add_warning("存在潜在风险")
    data = analysis.to_dict()
    assert data["compatible"] is True
    assert data["warnings"] == ["存在潜在风险"]
    assert data["conflicts"] == []
    assert data["overlaps"] == []


# ---------------------------------------------------------------------------
# SkillSecurityChecker.scan_code_for_threats
# ---------------------------------------------------------------------------
def test_scan_code_for_threats_detects_multiple_patterns():
    """扫描代码命中多种危险模式：subprocess/os.system/exec/eval/open 写文件"""
    code = """
import subprocess
subprocess.run("cmd", shell=True)
os.system("echo hi")
exec("print(1)")
eval("1+1")
with open("f", "w") as fp:
    fp.write("x")
"""
    checker = SkillSecurityChecker()
    findings = checker.scan_code_for_threats(code, "evil.py")
    categories = {f["category"] for f in findings}
    assert "代码执行" in categories
    assert "写文件" in categories
    # 每个 finding 都带位置与片段
    for f in findings:
        assert f["location"] == "evil.py"
        assert f["snippet"]
        assert f["severity"]


def test_scan_code_for_threats_no_match():
    """安全代码扫描不到任何威胁"""
    checker = SkillSecurityChecker()
    findings = checker.scan_code_for_threats("def greet(name):\n    return f'hi {name}'")
    assert findings == []


def test_scan_code_for_threats_snippet_clip_at_start():
    """模式出现在代码开头时片段裁剪不越界（max(0, ...) 分支）"""
    checker = SkillSecurityChecker()
    findings = checker.scan_code_for_threats("exec('x')", "t.py")
    assert any(f["pattern"].startswith("exec") for f in findings)


# ---------------------------------------------------------------------------
# SkillSecurityChecker.scan_directory
# ---------------------------------------------------------------------------
def test_scan_directory_finds_python_files(tmp_path):
    """扫描目录递归发现 .py 文件中的威胁"""
    sub = tmp_path / "nested"
    sub.mkdir()
    (sub / "bad.py").write_text("import os\nos.system('rm')", encoding="utf-8")
    (sub / "ok.txt").write_text("import os\nos.system('rm')", encoding="utf-8")
    checker = SkillSecurityChecker()
    findings = checker.scan_directory(tmp_path)
    assert len(findings) >= 1
    assert all("bad.py" in f["location"] for f in findings)


def test_scan_directory_read_error_logs_warning(tmp_path, monkeypatch, caplog):
    """读取文件失败时记录 warning 并继续（异常分支）"""
    (tmp_path / "bad.py").write_text("x", encoding="utf-8")

    def boom(self, *args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "read_text", boom)
    checker = SkillSecurityChecker()
    with caplog.at_level(logging.WARNING, logger="agent.extensions.security_checker"):
        findings = checker.scan_directory(tmp_path)
    assert findings == []
    assert any("扫描文件失败" in str(r.msg) for r in caplog.records)


# ---------------------------------------------------------------------------
# SkillSecurityChecker.check_permissions / check_data_compliance
# ---------------------------------------------------------------------------
def test_check_permissions_hits_sensitive_keyword():
    """描述含敏感权限关键词（password）时返回中风险问题"""
    checker = SkillSecurityChecker()
    issues = checker.check_permissions({"description": "需要访问用户 password 进行验证"})
    assert len(issues) >= 1
    assert all(i["severity"] == "中风险" for i in issues)
    assert all("password" in i["message"] for i in issues)


def test_check_permissions_miss():
    """描述不含敏感关键词时不返回问题"""
    checker = SkillSecurityChecker()
    assert checker.check_permissions({"description": "简单的问候功能"}) == []


def test_check_data_compliance_hits_keyword():
    """描述含数据关键词（收集）时返回低风险问题并带建议"""
    checker = SkillSecurityChecker()
    issues = checker.check_data_compliance({"description": "收集用户数据用于分析"})
    assert len(issues) >= 1
    assert issues[0]["severity"] == "低风险"
    assert "建议确认技能的数据处理政策" in issues[0]["suggestion"]


def test_check_data_compliance_miss():
    """描述不含数据关键词时不返回问题"""
    checker = SkillSecurityChecker()
    assert checker.check_data_compliance({"description": "纯离线计算"}) == []


# ---------------------------------------------------------------------------
# SkillSecurityChecker.assess_security
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "source, expected_suggestion",
    [
        ("local:/path/to/skill", "本地技能来源"),
        ("github:user/repo", "GitHub来源"),
        ("url:https://example.com/skill.zip", "URL来源"),
    ],
)
def test_assess_security_source_suggestions(source, expected_suggestion):
    """不同来源类型生成对应来源建议，且安全技能等级为 PASS"""
    checker = SkillSecurityChecker()
    assessment = checker.assess_security(source, {"name": "s", "description": "普通功能"})
    assert assessment.level == "PASS"
    assert any(expected_suggestion in s for s in assessment.suggestions)


def test_assess_security_unknown_source_no_suggestion():
    """未知来源不追加来源建议（三个分支均未命中）"""
    checker = SkillSecurityChecker()
    assessment = checker.assess_security("unknown-source", {"name": "s", "description": "普通功能"})
    assert assessment.level == "PASS"
    assert not any("来源" in s for s in assessment.suggestions)


def test_assess_security_with_permission_issues():
    """描述含敏感权限关键词时，assess_security 将权限问题并入评估结果（权限循环体分支）"""
    checker = SkillSecurityChecker()
    assessment = checker.assess_security(
        "local:/test", {"name": "s", "description": "需要读取 password 完成验证"}
    )
    assert assessment.level == "WARNING"
    assert any(i["category"] == "权限请求" for i in assessment.issues)


def test_assess_security_with_temp_dir_dangerous(tmp_path):
    """临时目录含危险代码 → BLOCK + 不建议安装建议"""
    (tmp_path / "danger.py").write_text("import subprocess\nsubprocess.run('x')", encoding="utf-8")
    checker = SkillSecurityChecker()
    assessment = checker.assess_security("local:/test", {"name": "s", "description": "普通"}, tmp_path)
    assert assessment.level == "BLOCK"
    assert any("不建议安装" in s for s in assessment.suggestions)
    assert assessment.score == 40


def test_assess_security_without_temp_dir():
    """不提供临时目录时跳过代码扫描"""
    checker = SkillSecurityChecker()
    assessment = checker.assess_security("local:/test", {"name": "s", "description": "普通"})
    assert assessment.level == "PASS"


def test_assess_security_warning_suggestion():
    """数据合规低风险触发 WARNING → 追加审查建议"""
    checker = SkillSecurityChecker()
    assessment = checker.assess_security("local:/test", {"description": "收集用户数据"})
    assert assessment.level == "WARNING"
    assert any("仔细审查" in s for s in assessment.suggestions)


# ---------------------------------------------------------------------------
# SkillSecurityChecker.analyze_compatibility
# ---------------------------------------------------------------------------
class FakeStore:
    """测试用扩展存储桩（仅实现 list_all）"""

    def __init__(self, skills):
        self._skills = skills

    def list_all(self, ext_type):
        return self._skills


def test_analyze_compatibility_no_store():
    """无扩展存储时跳过检查并给出警告，仍视为兼容"""
    checker = SkillSecurityChecker()
    analysis = checker.analyze_compatibility({"name": "s", "description": "普通"})
    assert analysis.compatible is True
    assert any("跳过兼容性检查" in w for w in analysis.warnings)


def test_analyze_compatibility_name_conflict_and_overlap():
    """有存储时：同名技能 → 冲突并跳过；不同名但功能重叠 → overlap + 系统原生警告"""
    store = FakeStore(
        [
            # 同名技能：触发名称冲突后 continue，不产生重叠
            {"name": "网络技能", "description": "与关键词完全无关的纯文本描述"},
            # 不同名技能：与新技能功能重叠
            {"name": "旧网络工具", "description": "网络请求 http api"},
        ]
    )
    checker = SkillSecurityChecker(store)
    analysis = checker.analyze_compatibility(
        {"name": "网络技能", "description": "处理网络请求 http"}
    )
    assert analysis.compatible is False
    assert len(analysis.conflicts) == 1
    assert analysis.conflicts[0]["reason"] == "名称冲突"
    assert len(analysis.overlaps) == 1
    assert analysis.overlaps[0]["feature"] == "网络功能"
    assert any("系统原生的【网络功能】" in w for w in analysis.warnings)


def test_analyze_compatibility_custom_feature_default_name(monkeypatch):
    """SYSTEM_FEATURES 中出现 feature_names 未收录的键 → 使用键本身作为显示名（get 默认值分支）"""
    monkeypatch.setattr(
        sc_module,
        "SYSTEM_FEATURES",
        {**sc_module.SYSTEM_FEATURES, "custom_feat": ["独角兽"]},
    )
    store = FakeStore([{"name": "旧技能", "description": "独角兽相关功能"}])
    checker = SkillSecurityChecker(store)
    analysis = checker.analyze_compatibility({"name": "新技能", "description": "独角兽相关功能"})
    assert len(analysis.overlaps) == 1
    assert analysis.overlaps[0]["feature"] == "custom_feat"
    assert any("custom_feat" in w for w in analysis.warnings)


# ---------------------------------------------------------------------------
# SkillSecurityChecker.perform_full_check
# ---------------------------------------------------------------------------
def test_perform_full_check_install_allowed(tmp_path):
    """安全技能完整检查 → 可安装"""
    (tmp_path / "safe.py").write_text("def f():\n    return 1", encoding="utf-8")
    checker = SkillSecurityChecker()
    result = checker.perform_full_check(
        "local:/skill", {"name": "安全技能", "description": "普通功能"}, tmp_path
    )
    assert result["can_install"] is True
    assert result["security"]["level"] == "PASS"
    assert result["compatibility"]["compatible"] is True
    assert result["skill_name"] == "安全技能"
    assert result["source"] == "local:/skill"
    assert "timestamp" in result


def test_perform_full_check_blocked(tmp_path):
    """危险技能完整检查 → 阻止安装"""
    (tmp_path / "bad.py").write_text("import os\nos.system('x')", encoding="utf-8")
    checker = SkillSecurityChecker()
    result = checker.perform_full_check(
        "local:/skill", {"name": "危险技能", "description": "普通功能"}, tmp_path
    )
    assert result["can_install"] is False
    assert result["security"]["level"] == "BLOCK"


# ---------------------------------------------------------------------------
# 工厂函数与全局实例
# ---------------------------------------------------------------------------
def test_create_security_checker_with_store():
    """工厂函数透传 extension_store"""
    store = FakeStore([])
    checker = _create_security_checker({"extension_store": store})
    assert isinstance(checker, SkillSecurityChecker)
    assert checker._store is store


def test_create_security_checker_no_config():
    """工厂函数无配置时也可创建（(config or {}).get 分支）"""
    checker = _create_security_checker()
    assert isinstance(checker, SkillSecurityChecker)
    assert checker._store is None


def test_get_security_checker_singleton_path():
    """单例可用时通过 SingletonManager 获取，多次调用返回同一实例"""
    c1 = sc_module.get_security_checker()
    c2 = sc_module.get_security_checker()
    assert isinstance(c1, SkillSecurityChecker)
    assert c1 is c2


def test_get_security_checker_fallback_path(monkeypatch):
    """单例不可用时走模块级全局变量降级路径（fallback 分支）"""
    monkeypatch.setattr(sc_module, "_SINGLETON_AVAILABLE", False)
    monkeypatch.setattr(sc_module, "_security_checker", None)
    c1 = sc_module.get_security_checker()
    c2 = sc_module.get_security_checker()
    assert isinstance(c1, SkillSecurityChecker)
    assert c1 is c2


# ---------------------------------------------------------------------------
# 模块内置自测函数（真实执行，覆盖模块自带测试逻辑）
# ---------------------------------------------------------------------------
def test_module_self_test():
    """真实执行模块内置 test_security_checker()，所有断言必须通过"""
    from agent.extensions.security_checker import test_security_checker

    test_security_checker()
