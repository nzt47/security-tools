"""
M2 里程碑验收测试 —— Gitleaks 误报豁免（guard_llm 占位符 + Profile.tsx 演示密码）

目标（对应 Gitleaks 治理方案 M2）：
  1. scripts/guard_llm_api_key.py 的 sk-* 占位符行必须带 gitleaks:allow 豁免注释（Gitleaks 误报清零）
  2. 占位符黑名单内容保持完整（识别假 key 的功能不破坏）
  3. yunshu-ui/src/pages/Profile.tsx 不再含硬编码演示密码，表单为空值占位

若未来有人移除豁免注释或重新引入硬编码密码，用例立即失败，保证 M2 不回归。
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GUARD_LLM = REPO_ROOT / "scripts" / "guard_llm_api_key.py"
PROFILE_TSX = REPO_ROOT / "yunshu-ui" / "src" / "pages" / "Profile.tsx"


def _extract_placeholder_block(src: str) -> list[tuple[int, str]]:
    """提取 PLACEHOLDER = { ... } 块内的（行号, 行内容）列表"""
    lines = src.splitlines()
    start = next(i for i, l in enumerate(lines) if re.match(r"PLACEHOLDER\s*=", l))
    block = []
    for i in range(start, len(lines)):
        block.append((i + 1, lines[i]))
        if "}" in lines[i]:
            break
    return block


def test_placeholder_sk_lines_all_exempted():
    """M2 核心：PLACEHOLDER 块内所有含 sk- 的占位符行必须带 gitleaks:allow（Gitleaks 误报豁免）"""
    src = GUARD_LLM.read_text(encoding="utf-8")
    block = _extract_placeholder_block(src)
    missing = [
        (lineno, line)
        for lineno, line in block
        if "sk-" in line and "gitleaks:allow" not in line
    ]
    assert not missing, f"guard_llm 存在未豁免的 sk- 占位符行（Gitleaks 将误报）: {missing}"


def test_placeholder_blacklist_kept():
    """占位符黑名单完整：识别假 key 的功能不受豁免注释影响"""
    src = GUARD_LLM.read_text(encoding="utf-8")
    for key in (
        "sk-test-1234567890abcdef",
        "sk-test",
        "sk-secret",
        "sk-1234567890abcdef",
        "sk-real-key-123",
        "sk-real-key-original",
        "sk-instance-key-12345",
    ):
        assert key in src, f"占位符 {key} 丢失，黑名单不完整"


def test_profile_no_hardcoded_password():
    """Profile.tsx 不应再含硬编码演示密码；表单应为空值占位"""
    src = PROFILE_TSX.read_text(encoding="utf-8")
    assert "password: '123456'" not in src, "Profile.tsx 仍含硬编码密码，M2 未完成"
    assert re.search(r"password:\s*''", src), "Profile.tsx 表单应为空值占位（password: ''）"
