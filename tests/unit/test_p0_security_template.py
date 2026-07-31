"""P0 安全验证 workflow 模板的占位符替换逻辑单元测试。

验证维度（对应 4 个不变量）：
1. 占位符完整性 — 模板含全部 11 个 {{占位符}}
2. 替换彻底性 — 替换后无残留 {{...}}
3. YAML 有效性 — 替换后仍为合法 YAML
4. 结构完整性 — 替换后 5 个 job 齐全

Why: 模板被其他项目复用时，占位符替换是唯一的手工操作，必须保证
替换后产物可直接作为 GitHub Actions workflow 使用，不会因占位符
遗漏或 YAML 语法错误导致 CI 静默失败（呼应本次修复的"坑1"）。
"""

import re
from pathlib import Path

import pytest
import yaml

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / ".github" / "workflow-templates"
TEMPLATE_FILE = TEMPLATE_DIR / "p0-security.template.yml"
README_FILE = TEMPLATE_DIR / "README.md"

# 11 个占位符（与 README.md 占位符清单一一对应）
EXPECTED_PLACEHOLDERS = [
    "{{WORKFLOW_NAME}}",
    "{{WORKFLOW_FILE_NAME}}",
    "{{PYTHON_VERSION}}",
    "{{REGRESSION_TEST_FILE}}",
    "{{SCAN_SCRIPT}}",
    "{{PATCH_DIR}}",
    "{{PATCH_FILE}}",
    "{{EXPECTED_TEST_COUNT}}",
    "{{CROSS_MODULE_TEST_CLASSES}}",
    "{{PATCH_TEST_CLASS_1}}",
    "{{PATCH_TEST_CLASS_2}}",
]

# 示例替换值（模拟一个 Flask 认证服务项目的真实取值）
SAMPLE_VALUES = {
    "{{WORKFLOW_NAME}}": "P0 安全验证",
    "{{WORKFLOW_FILE_NAME}}": "p0-security",
    "{{PYTHON_VERSION}}": "3.11",
    "{{REGRESSION_TEST_FILE}}": "tests/regression/test_p0_security_fix.py",
    "{{SCAN_SCRIPT}}": "scripts/scan_sensitive_regex.py",
    "{{PATCH_DIR}}": "patches/p0_security",
    "{{PATCH_FILE}}": "p0_security_test_extension.patch",
    "{{EXPECTED_TEST_COUNT}}": "68",
    # 注意：多个测试类用空格分隔，每个为 file::Class 完整路径
    "{{CROSS_MODULE_TEST_CLASSES}}": (
        "tests/regression/test_p0_security_fix.py::TestCrossModuleConsistency "
        "tests/regression/test_p0_security_fix.py::TestSensitiveDataFilterBearerRegression"
    ),
    "{{PATCH_TEST_CLASS_1}}": "TestLoggingUtilsGreedyRegexRegression",
    "{{PATCH_TEST_CLASS_2}}": "TestCrossModuleConsistency",
}

# 预期的 5 个 job（与模板 Job 1~5 一一对应）
EXPECTED_JOBS = [
    "static-scan",
    "p0-security-tests",
    "patch-integrity",
    "cross-module-consistency",
    "p0-security-summary",
]


def _read_template() -> str:
    """读取模板原始内容。"""
    return TEMPLATE_FILE.read_text(encoding="utf-8")


def _render(values: dict | None = None) -> str:
    """用给定值替换模板占位符，返回渲染后的内容。

    Why: 模拟用户复用模板时的唯一手工操作（全局替换 {{...}}），
    用于验证替换产物是否可直接作为 workflow 使用。
    """
    values = values or SAMPLE_VALUES
    content = _read_template()
    for placeholder, value in values.items():
        content = content.replace(placeholder, value)
    return content


def _parse_workflow(rendered: str) -> dict:
    """解析渲染后的 YAML 为 dict。

    Why: GitHub Actions workflow 本质是 YAML，替换后必须保持 YAML 合法。
    注意 yaml.safe_load 会把键 'on' 解析为 Python True（YAML 1.1 布尔语义），
    访问时需用 data[True]。
    """
    return yaml.safe_load(rendered)


# =============================================================================
# 1. 占位符完整性测试
# =============================================================================
class TestPlaceholderIntegrity:
    """验证模板包含全部预期占位符。"""

    def test_template_file_exists(self):
        """模板文件存在，避免路径变更后测试空跑。"""
        assert TEMPLATE_FILE.exists(), f"模板文件不存在: {TEMPLATE_FILE}"

    def test_template_contains_all_placeholders(self):
        """模板必须包含全部 11 个占位符，缺一不可。"""
        content = _read_template()
        missing = [ph for ph in EXPECTED_PLACEHOLDERS if ph not in content]
        assert missing == [], f"模板缺少占位符: {missing}"

    def test_readme_lists_all_placeholders(self):
        """README 占位符清单必须与模板实际占位符一致，避免文档与代码漂移。"""
        readme = README_FILE.read_text(encoding="utf-8")
        # README 表格中占位符以 `{{NAME}}` 形式列出（含数字如 PATCH_TEST_CLASS_1）
        listed = set(re.findall(r"`(\{\{[A-Z0-9_]+\}\})`", readme))
        expected = set(EXPECTED_PLACEHOLDERS)
        assert listed == expected, (
            f"README 占位符清单与模板不一致: 缺少 {expected - listed}, "
            f"多余 {listed - expected}"
        )


# =============================================================================
# 2. 替换彻底性测试
# =============================================================================
class TestReplacementCompleteness:
    """验证替换后无占位符残留。"""

    def test_no_placeholder_remains_after_full_replacement(self):
        """全部占位符替换后，不得残留任何 {{...}}。"""
        rendered = _render()
        remaining = re.findall(r"\{\{[A-Z0-9_]+\}\}", rendered)
        assert remaining == [], f"替换后仍残留占位符: {remaining}"

    def test_partial_replacement_leaves_trace(self):
        """仅替换部分占位符时，未替换的应可被检测到（反向验证检测能力）。"""
        # 只替换一个占位符
        partial_values = {"{{WORKFLOW_NAME}}": "test"}
        rendered = _render(partial_values)
        # 用 set 去重：模板中同一占位符可能出现多次（如 {{PATCH_DIR}} 出现 10+ 次）
        remaining = set(re.findall(r"\{\{[A-Z0-9_]+\}\}", rendered))
        # 11 个占位符替换了 1 个，应剩 10 个唯一占位符
        assert len(remaining) == 10, f"部分替换后残留唯一占位符数异常: {len(remaining)}"


# =============================================================================
# 3. YAML 有效性测试
# =============================================================================
class TestYamlValidity:
    """验证替换后仍为合法 YAML。"""

    def test_rendered_yaml_is_valid(self):
        """替换后 YAML 可被 yaml.safe_load 解析，不为空。"""
        data = _parse_workflow(_render())
        assert data is not None, "替换后 YAML 解析为空"

    def test_rendered_workflow_name(self):
        """替换后 name 字段为示例值。"""
        data = _parse_workflow(_render())
        assert data["name"] == SAMPLE_VALUES["{{WORKFLOW_NAME}}"]

    def test_rendered_python_version(self):
        """替换后 env.PYTHON_VERSION 为示例值。"""
        data = _parse_workflow(_render())
        assert data["env"]["PYTHON_VERSION"] == SAMPLE_VALUES["{{PYTHON_VERSION}}"]

    def test_rendered_expected_test_count_in_script(self):
        """替换后脚本中包含预期测试数量（验证 shell 片段内的占位符也被替换）。"""
        rendered = _render()
        assert SAMPLE_VALUES["{{EXPECTED_TEST_COUNT}}"] in rendered


# =============================================================================
# 4. 结构完整性测试
# =============================================================================
class TestWorkflowStructure:
    """验证替换后 workflow 的 job 结构完整。"""

    def test_all_five_jobs_present(self):
        """替换后必须保留全部 5 个 job。"""
        data = _parse_workflow(_render())
        jobs = data["jobs"]  # jobs 是顶层键，不在 on: 下
        for job_id in EXPECTED_JOBS:
            assert job_id in jobs, f"替换后缺少 job: {job_id}"

    def test_summary_job_needs_all_four(self):
        """总结 job 的 needs 必须依赖前 4 个 job（防止漏配依赖）。"""
        data = _parse_workflow(_render())
        summary = data["jobs"]["p0-security-summary"]
        assert summary["needs"] == EXPECTED_JOBS[:4]

    def test_upload_artifact_uses_v4(self):
        """上传测试结果步骤必须用 upload-artifact@v4（呼应坑1：v3 已废弃）。"""
        rendered = _render()
        assert "actions/upload-artifact@v4" in rendered
        assert "actions/upload-artifact@v3" not in rendered

    def test_checkout_uses_v4(self):
        """检出代码必须用 checkout@v4（呼应坑1）。"""
        rendered = _render()
        assert "actions/checkout@v4" in rendered
        assert "actions/checkout@v3" not in rendered

    def test_pytest_timeout_installed_in_patch_job(self):
        """补丁完整性 job 必须安装 pytest-timeout（呼应坑3：--timeout 依赖）。"""
        rendered = _render()
        # 补丁完整性 job 的依赖安装行
        assert "pip install pytest pytest-timeout -q" in rendered

    def test_master_in_trigger_branches(self):
        """push 和 pull_request 的 branches 必须包含 master。"""
        data = _parse_workflow(_render())
        push_branches = data[True]["push"]["branches"]
        pr_branches = data[True]["pull_request"]["branches"]
        assert "master" in push_branches, "push.branches 缺少 master"
        assert "master" in pr_branches, "pull_request.branches 缺少 master"


# =============================================================================
# 5. 占位符语义正确性测试（防拼写漂移）
# =============================================================================
class TestPlaceholderSemantics:
    """验证占位符命名规范，防止未来增删占位符时拼写漂移。"""

    def test_all_placeholders_upper_SNAKE_CASE(self):
        """占位符必须为 {{UPPER_SNAKE_CASE}} 格式（命名规范一致性）。"""
        content = _read_template()
        # 含数字（如 PATCH_TEST_CLASS_1）的占位符也需匹配
        found = set(re.findall(r"\{\{[A-Z0-9_]+\}\}", content))
        for ph in found:
            # 必须全大写字母+数字+下划线，不以数字/下划线开头
            inner = ph.strip("{}")
            assert re.match(r"^[A-Z][A-Z0-9_]*[A-Z0-9]$", inner), (
                f"占位符命名不规范: {ph}（应为 UPPER_SNAKE_CASE）"
            )
