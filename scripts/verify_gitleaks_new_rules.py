#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模拟 gitleaks 新规则匹配，验证不会误报现有已跟踪源码（临时验证脚本）"""
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 从 gitleaks 配置直接读取 allowlist，避免手写列表漂移
CFG = tomllib.loads((ROOT / ".github" / "gitleaks-config.toml").read_text(encoding="utf-8"))
ALLOW_PATHS = CFG["allowlist"].get("paths", [])
ALLOW_REGEXES = CFG["allowlist"].get("regexes", [])

# 新增规则 8-12 的正则（与 .github/gitleaks-config.toml 保持同步）
NEW_RULES = {
    "openai-api-key": r"\b(sk|sk-ant)-[A-Za-z0-9_\-]{20,}\b",
    "aws-access-key-id": r"\bAKIA[0-9A-Z]{16}\b",
    "github-pat": r"\bghp_[A-Za-z0-9]{20,}\b",
    "generic-api-token": r"(?i)\b(client_secret|client-secret|access_token|access-token|api_token|api-token)\s*[:=]\s*['\"][A-Za-z0-9_\-]{20,}['\"]",
    "pem-private-key": r"-----BEGIN (RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----",
}

ALLOW_REGEX_COMPILED = [re.compile(r) for r in ALLOW_REGEXES]


def path_allowed(rel: str) -> bool:
    return any(re.search(p, rel) for p in ALLOW_PATHS)


def line_allowed(line: str) -> bool:
    return any(r.search(line) for r in ALLOW_REGEX_COMPILED)


def main() -> int:
    # 获取 git 跟踪文件列表
    out = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, cwd=ROOT
    )
    files = [f for f in out.stdout.splitlines() if f]

    findings = []
    for rel in files:
        if path_allowed(rel):
            continue
        p = ROOT / rel
        if not p.is_file():
            continue
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for lineno, line in enumerate(content.splitlines(), 1):
            if line_allowed(line):
                continue
            for rule_id, pattern in NEW_RULES.items():
                if re.search(pattern, line):
                    findings.append((rel, lineno, rule_id, line.strip()[:120]))

    if findings:
        print(f"❌ 发现 {len(findings)} 处可能误报：")
        for rel, lineno, rule, line in findings:
            print(f"  {rel}:{lineno} [{rule}] {line}")
        return 1
    print("✅ 全部通过：新增 5 条规则对现有已跟踪源码无误报")
    return 0


if __name__ == "__main__":
    sys.exit(main())
