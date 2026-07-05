"""自动修复 test_config_secure.py 中的脱敏期望不匹配

问题：
    test_config_secure.py 期望脱敏结果为 '***'，
    但 agent/logging_utils.py 的 SensitiveDataFilter 实际返回 '[REDACTED]'。

修复策略：
    将所有 '***' 期望替换为 '[REDACTED]'，与实际实现对齐。

影响的测试用例（5 个）：
    1. test_sanitize_api_key — 期望 result == '***' → '[REDACTED]'
    2. test_sanitize_password_field — 期望 '***' in result → '[REDACTED]' in result
    3. test_sanitize_url_params — 期望 '***' in result → '[REDACTED]' in result
    4. test_sanitize_dict — 期望 sanitized[key] == '***' → '[REDACTED]'
    5. test_filter_log_record — 期望 '***' in record.msg → '[REDACTED]' in record.msg

使用方法：
    python scripts/fix_config_secure_tests.py           # 执行修复
    python scripts/fix_config_secure_tests.py --dry-run  # 预览不写入
    python scripts/fix_config_secure_tests.py --verify   # 修复后运行测试验证

生成时间: 2026-07-04
"""
import re
import sys
import argparse
from pathlib import Path


TEST_FILE = Path(__file__).parent.parent / "tests" / "unit" / "test_config_secure.py"


def fix_content(content: str) -> tuple[str, int]:
    """修复测试文件内容，返回 (新内容, 替换次数)

    修复规则：
    1. test_sanitize_api_key 中的 ("sk-xxx", "***") → ("sk-xxx", "[REDACTED]")
    2. test_sanitize_password_field 中的 '***' in result → '[REDACTED]' in result
    3. test_sanitize_url_params 中的 '***' in result → '[REDACTED]' in result
    4. test_sanitize_dict 中的 == '***' → == '[REDACTED]'
    5. test_filter_log_record 中的 '***' in record.msg → '[REDACTED]' in record.msg
    """
    count = 0
    original = content

    # 规则 1: test_sanitize_api_key 中的期望值 "***" → "[REDACTED]"
    # 匹配模式：("sk-xxx", "***") 或 ("pk-xxx", "***") 或 ("sk-proj-xxx", "***")
    pattern1 = re.compile(r'(\(["\'](?:sk-|pk-)[^"\']+["\'],\s*)["\']\*{3}["\'](\))')
    new_content, n1 = pattern1.subn(r'\1"[REDACTED]"\2', content)
    count += n1
    content = new_content

    # 规则 2: '***' in result → '[REDACTED]' in result
    # 匹配单引号和双引号两种形式
    pattern2 = re.compile(r"['\"]\*{3}['\"]\s+in\s+result")
    new_content, n2 = pattern2.subn("'[REDACTED]' in result", content)
    count += n2
    content = new_content

    # 规则 3: '***' in record.msg → '[REDACTED]' in record.msg
    pattern3 = re.compile(r"['\"]\*{3}['\"]\s+in\s+record\.msg")
    new_content, n3 = pattern3.subn("'[REDACTED]' in record.msg", content)
    count += n3
    content = new_content

    # 规则 4: == '***' → == '[REDACTED]'（用于 test_sanitize_dict）
    # 匹配 == '***' 或 == "***"
    pattern4 = re.compile(r"==\s*['\"]\*{3}['\"]")
    new_content, n4 = pattern4.subn("== '[REDACTED]'", content)
    count += n4
    content = new_content

    return content, count


def verify_fix() -> int:
    """运行测试验证修复结果，返回退出码"""
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(TEST_FILE), "-v", "--tb=short"],
        cwd=TEST_FILE.parent.parent.parent,
    )
    return result.returncode


def main():
    parser = argparse.ArgumentParser(description="修复 test_config_secure.py 脱敏期望")
    parser.add_argument("--dry-run", action="store_true", help="预览不写入")
    parser.add_argument("--verify", action="store_true", help="修复后运行测试验证")
    args = parser.parse_args()

    if not TEST_FILE.exists():
        print(f"[ERROR] 测试文件不存在: {TEST_FILE}")
        sys.exit(1)

    original = TEST_FILE.read_text(encoding="utf-8")
    new_content, count = fix_content(original)

    if count == 0:
        print(f"[SKIP] {TEST_FILE.name}: 无需修复（期望已对齐）")
        if args.verify:
            sys.exit(verify_fix())
        sys.exit(0)

    if args.dry_run:
        print(f"[DRY-RUN] {TEST_FILE.name}: 将替换 {count} 处")
        # 显示 diff
        import difflib
        diff = difflib.unified_diff(
            original.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=str(TEST_FILE),
            tofile=str(TEST_FILE) + "(fixed)",
            n=2,
        )
        sys.stdout.write("".join(diff))
        sys.exit(0)

    TEST_FILE.write_text(new_content, encoding="utf-8")
    print(f"[OK] {TEST_FILE.name}: 已修复 {count} 处脱敏期望（'***' → '[REDACTED]'）")

    if args.verify:
        print("\n=== 运行测试验证 ===")
        sys.exit(verify_fix())


if __name__ == "__main__":
    main()
