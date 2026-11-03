# -*- coding: utf-8 -*-
"""合规与安全审核（S4）：规则 + 证据行号，**block 不需要人拍板**。

口径（设计文档 §9）：每条发现必须带 文件 + 行号 + 证据片段，不许写「疑似」。
block 级：凭据/密钥、危险指令；warn 级：外部脚本下载、许可缺失、体量过大。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

#: (规则名, 级别, 正则, 说明)
_RULES = (
    ("credential.token", "block",
     re.compile(r"(gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,})"),
     "疑似明文凭据/令牌"),
    ("credential.assignment", "block",
     re.compile(r"(?i)(api[_-]?key|secret|password|passwd|access[_-]?token)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{12,}"),
     "疑似把密钥写进赋值"),
    ("danger.rm_root", "block", re.compile(r"rm\s+-rf\s+(/|~|\*|C:\\\\)"), "递归删除根/家目录"),
    ("danger.pipe_shell", "block",
     re.compile(r"(curl|wget)\s+[^\n|]+\|\s*(sudo\s+)?(ba)?sh\b"), "下载后直接管道给 shell"),
    ("danger.chmod_777", "warn", re.compile(r"chmod\s+(-R\s+)?777"), "777 权限"),
    ("danger.powershell_enc", "block",
     re.compile(r"powershell(\.exe)?[^\n]*-e(nc|ncodedcommand)?\s"), "PowerShell 编码命令"),
    ("danger.eval", "warn", re.compile(r"\beval\s*\("), "eval 执行字符串"),
    ("danger.shell_true", "warn", re.compile(r"shell\s*=\s*True"), "subprocess shell=True"),
    ("supply.remote_script", "warn",
     re.compile(r"https?://\S+\.(sh|ps1|bat|cmd|exe|zip|tar\.gz)"), "从外链下载可执行/脚本"),
)

#: 许可线索（有任一即视为已声明；没有则 warn，不 block）
_LICENSE_HINTS = ("license", "licensed", "许可", "授权", "MIT", "Apache", "BSD", "CC BY", "GPL")

#: 体量阈值：超过就建议「能力卡 + 原文归档」而不是整篇进库（设计文档 §1）
_MAX_INLINE_CHARS = 8000




#: 整包扫描的边界：跳过这些目录 / 只看这些后缀 / 单文件上限
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
_SCAN_EXTS = {".py", ".sh", ".ps1", ".bat", ".cmd", ".js", ".ts", ".tsx", ".jsx",
              ".md", ".yaml", ".yml", ".json", ".toml", ".ini", ".env"}
_MAX_SCAN_FILES = 200
_MAX_SCAN_BYTES = 256 * 1024


def review_artifact(artifact, name: str = "") -> Dict[str, object]:
    """**整包**审核：目录里的脚本/配置一起扫，不只扫技能正文（S3，2026-11-03）。

    【为什么必须整包】外来插件常把真正危险的东西放在 `scripts/*.sh` / `setup.py` 里，
    正文只是一段说明。只读 skill.md 等于「审了封面没审内容」。
    【边界】只看文本类后缀、单文件 ≤ 256KB、最多 200 个文件；跳过 .git/node_modules 等。
    扫描范围写进返回值（scanned / skipped），让"没扫"这件事本身可见。
    """
    p = Path(artifact)
    if not p.is_dir():
        text = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
        out = review_text(text, name=name or str(artifact))
        out["scanned"] = [p.name]
        out["skipped"] = []
        return out

    findings: List[Dict[str, object]] = []
    scanned: List[str] = []
    skipped: List[str] = []
    for child in sorted(p.rglob("*")):
        if len(scanned) >= _MAX_SCAN_FILES:
            skipped.append("（达到 %d 个文件上限，其余未扫）" % _MAX_SCAN_FILES)
            break
        if not child.is_file():
            continue
        if any(part in _SKIP_DIRS for part in child.parts):
            continue
        if child.suffix.lower() not in _SCAN_EXTS:
            continue
        try:
            if child.stat().st_size > _MAX_SCAN_BYTES:
                skipped.append("%s（超过 %dKB）" % (child.name, _MAX_SCAN_BYTES // 1024))
                continue
            text = child.read_text(encoding="utf-8", errors="replace")
        except OSError:
            skipped.append(child.name)
            continue
        rel = str(child.relative_to(p))
        # 只记**相对路径**：这份 review 会落进归档 review.json 并显示在界面上，
        # 没有理由把本机绝对路径带出去。
        scanned.append(rel)
        if child.suffix.lower() == ".md":
            one = review_text(text, name=rel)
            for f in one["findings"]:
                f["file"] = rel       # 整包审核的证据必须能定位到**哪个文件**
                findings.append(f)
        else:
            # 代码类文件不跑「许可缺失/体量」这两条（它们只对正文有意义）
            for f in review_text(text, name=rel)["findings"]:
                if str(f["rule"]).startswith(("compliance.", "size.")):
                    continue
                f["file"] = rel
                findings.append(f)

    order = {"block": 0, "warn": 1, "info": 2}
    findings.sort(key=lambda f: (order.get(str(f["severity"]), 9), str(f.get("file") or "")))
    if any(f["severity"] == "block" for f in findings):
        verdict = "block"
    elif any(f["severity"] == "warn" for f in findings):
        verdict = "warn"
    else:
        verdict = "pass"
    return {"verdict": verdict, "findings": findings, "file": name or str(artifact),
            "scanned": scanned, "skipped": skipped}

def review_text(text: str, name: str = "artifact") -> Dict[str, object]:
    """跑全部规则，返回 {verdict, findings:[{rule,severity,line,evidence,note}]}。"""
    lines = text.splitlines()
    findings: List[Dict[str, object]] = []
    for rule, severity, pattern, note in _RULES:
        for i, line in enumerate(lines, start=1):
            m = pattern.search(line)
            if not m:
                continue
            findings.append({"rule": rule, "severity": severity, "line": i,
                             "evidence": m.group(0)[:80], "note": note})
            break  # 同一规则只报首处，避免刷屏

    if not any(h.lower() in text.lower() for h in _LICENSE_HINTS):
        findings.append({"rule": "compliance.license_missing", "severity": "warn", "line": 0,
                         "evidence": "", "note": "正文没有许可/授权声明"})
    if len(text) > _MAX_INLINE_CHARS:
        findings.append({"rule": "size.too_large", "severity": "info", "line": 0,
                         "evidence": "%d 字符" % len(text),
                         "note": "超过 %d 字符：建议只落能力卡 + 原文归档" % _MAX_INLINE_CHARS})

    order = {"block": 0, "warn": 1, "info": 2}
    findings.sort(key=lambda f: order.get(str(f["severity"]), 9))
    if any(f["severity"] == "block" for f in findings):
        verdict = "block"
    elif any(f["severity"] == "warn" for f in findings):
        verdict = "warn"
    else:
        verdict = "pass"
    return {"verdict": verdict, "findings": findings, "file": name}
