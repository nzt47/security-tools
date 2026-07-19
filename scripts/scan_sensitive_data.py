#!/usr/bin/env python3
"""敏感信息检测 pre-commit hook

防止 API key / 密码 / 私钥等敏感信息误提交到 Git 历史。

设计原则（三义）:
- 【不易】拦截已知敏感模式（API key / 私钥 / 密码），阻断提交
- 【变易】支持白名单（测试 mock / 示例值），避免误报
- 【简易】单文件脚本，无第三方依赖，exit code 1 阻断提交

使用:
    pre-commit install
    pre-commit run scan-sensitive-data --all-files

退出码:
    0 - 未检测到敏感信息（或仅白名单匹配）
    1 - 检测到敏感信息，阻断提交
"""
import re
import sys
from pathlib import Path

# 敏感信息检测规则（按类型分组）
SENSITIVE_PATTERNS = [
    # API Keys
    (
        'API_KEY',
        re.compile(r'sk-[a-zA-Z0-9]{20,}'),
        'OpenAI/DeepSeek style API key (sk-xxx...)',
    ),
    (
        'API_KEY',
        re.compile(r'AIza[a-zA-Z0-9_-]{35}'),
        'Google API key (AIza...)',
    ),
    (
        'API_KEY',
        re.compile(r'gh[pousr]_[A-Za-z0-9]{36,}'),
        'GitHub token (ghp_/ghs_/gho_...)',
    ),
    (
        'API_KEY',
        re.compile(r'xox[baprs]-[A-Za-z0-9-]+'),
        'Slack token (xox...)',
    ),
    # Private Keys
    (
        'PRIVATE_KEY',
        re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----'),
        'PEM private key block',
    ),
    # Passwords (硬编码)
    (
        'PASSWORD',
        re.compile(r'(?i:password)\s*[=:]\s*["\x27][^"\x27]{8,}["\x27]'),
        'Hardcoded password (password = "xxx")',
    ),
    (
        'PASSWORD',
        re.compile(r'(?i:passwd)\s*[=:]\s*["\x27][^"\x27]{8,}["\x27]'),
        'Hardcoded password (passwd = "xxx")',
    ),
    # Database connection strings
    (
        'DB_URL',
        re.compile(r'(?:postgres|mysql|mongodb|redis)://[^:\s]+:[^@\s]+@'),
        'Database URL with credentials',
    ),
]

# 敏感文件名（直接拦截）
SENSITIVE_FILES = {
    '.encryption_key',
    '.secure_config.json',
    '.env.production',
    '.env.local',
    'id_rsa',
    'id_ecdsa',
    'id_ed25519',
}

# 白名单：路径子串匹配（不区分大小写）
# 注意：'test_' 会匹配路径中任意位置含 'test_' 的文件，包括 'tmp_test_xxx'
# 因此测试文件白名单改用文件名前缀检查（见 is_whitelisted 函数）
WHITELIST_PATHS = {
    'tests/',
    '_test.py',
    '.example',
    '.sample',
    'conftest.py',
    '__pycache__',
    '.pyc',
    'node_modules/',
    '.git/',
    # 文档目录（说明性内容，含示例值）
    'docs/',
    # 部署配置（密码通常用环境变量引用 ${VAR}）
    'docker-compose',
    'deploy/',
    'docker/',
    # 清理指南脚本本身（含旧 key 引用用于指导清理）
    'scripts/_fix_deepseek_guide.py',
    # 备份文件
    '.backups/',
    '_backup',
}

# 白名单：文件名前缀（只匹配文件名以这些前缀开头的情况）
WHITELIST_FILENAME_PREFIXES = {
    'test_',
    'test.',
}

# 白名单：这些值不视为敏感（mock / 测试值）
WHITELIST_VALUES = {
    'sk-test',
    'sk-xxx',
    'sk-NEW_KEY_HERE',
    'sk-abcdefghijklmnopqrstuv123456',
    'sk-abcdefghijklmnopqrstuvwxyz',
    'sk-1234567890abcdef',
    'sk-1***cdef',
    '***REMOVED_API_KEY***',
    '***REMOVED_GLITCHTIP_PWD***',
    'admin123',
    'password123',
    'test_password',
    'your_api_key_here',
    'YOUR_API_KEY',
    'REPLACE_ME',
}


def is_whitelisted(filepath: str) -> bool:
    """检查文件路径是否在白名单中

    匹配规则:
    1. 路径子串匹配（WHITELIST_PATHS）— 匹配路径中任意位置
    2. 文件名前缀匹配（WHITELIST_FILENAME_PREFIXES）— 只匹配文件名开头
    """
    path_lower = filepath.lower().replace('\\', '/')

    # 路径子串匹配
    for pattern in WHITELIST_PATHS:
        if pattern.lower() in path_lower:
            return True

    # 文件名前缀匹配（只检查文件名本身，不含目录）
    filename = Path(filepath).name.lower()
    for prefix in WHITELIST_FILENAME_PREFIXES:
        if filename.startswith(prefix.lower()):
            return True

    return False


def is_whitelisted_value(value: str) -> bool:
    """检查匹配值是否在白名单中"""
    for wv in WHITELIST_VALUES:
        if wv in value:
            return True
    return False


def scan_file(filepath: Path) -> list:
    """扫描单个文件，返回敏感信息匹配列表

    Returns:
        list of (line_no, category, description, matched_text)
    """
    findings = []

    # 敏感文件名直接拦截
    if filepath.name in SENSITIVE_FILES:
        findings.append((0, 'SENSITIVE_FILE', f'敏感文件名: {filepath.name}', filepath.name))
        return findings

    # 白名单路径跳过
    if is_whitelisted(str(filepath)):
        return findings

    try:
        content = filepath.read_text(encoding='utf-8', errors='ignore')
    except Exception:
        return findings

    for line_no, line in enumerate(content.splitlines(), 1):
        for category, pattern, desc in SENSITIVE_PATTERNS:
            for match in pattern.finditer(line):
                matched_text = match.group()
                # 白名单值跳过
                if is_whitelisted_value(matched_text):
                    continue
                findings.append((line_no, category, desc, matched_text))

    return findings


def main():
    """主入口：扫描所有暂存的文件"""
    # pre-commit 传入暂存文件列表作为参数
    files = sys.argv[1:] if len(sys.argv) > 1 else []

    if not files:
        # 手动运行时扫描整个仓库（排除 .git）
        files = [str(p) for p in Path('.').rglob('*') if p.is_file() and '.git' not in str(p)]

    all_findings = []
    for filepath in files:
        p = Path(filepath)
        if not p.exists() or not p.is_file():
            continue
        findings = scan_file(p)
        if findings:
            all_findings.append((filepath, findings))

    if all_findings:
        print('🚫 检测到敏感信息，提交已阻断！\n', file=sys.stderr)
        print('请在提交前移除或脱敏以下敏感内容：\n', file=sys.stderr)
        for filepath, findings in all_findings:
            print(f'📄 {filepath}', file=sys.stderr)
            for line_no, category, desc, matched in findings:
                location = f'L{line_no}' if line_no > 0 else '文件名'
                # 显示匹配内容时部分脱敏
                display = matched[:8] + '***' + matched[-4:] if len(matched) > 12 else '***'
                print(f'   [{category}] {location} - {desc}', file=sys.stderr)
                print(f'   匹配: {display}', file=sys.stderr)
            print('', file=sys.stderr)
        print('修复建议:', file=sys.stderr)
        print('  1. 将敏感值移到 .env 文件（已加入 .gitignore）', file=sys.stderr)
        print('  2. 使用 os.getenv() 从环境变量读取', file=sys.stderr)
        print('  3. 测试用例使用 mock 值（如 sk-test）', file=sys.stderr)
        print('  4. 如确需提交，在脚本 WHITELIST_VALUES 中添加白名单', file=sys.stderr)
        sys.exit(1)
    else:
        print('✅ 未检测到敏感信息', file=sys.stderr)
        sys.exit(0)


if __name__ == '__main__':
    main()
