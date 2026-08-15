#!/usr/bin/env python3
"""LLM_API_KEY 守护脚本 — 防 .env 中 LLM_API_KEY 被并行会话/脚本意外篡改

背景（2026-08-15 事故）:
- 并行会话 rebase/测试曾把仓库根 .env 覆盖为 sk-test / sk-secret 等占位 key
  （.env.backups/env.bak.* 有数百次覆盖记录），导致 app_server 启动即失败
  （LLMService 校验 api_key 长度 <10）或请求 401。
- .env 是唯一敏感数据来源（gitignore），env_config_manager 已提供写前备份 +
  跨进程锁，但缺少「key 合法性校验 + 自动恢复」的主动守护。

本脚本能力（【简易】最小充分解）:
1. --check: 校验 .env 中 LLM_API_KEY 是否合法（长度/前缀/占位符黑名单）
2. --restore: 非法时从 .env.backups 最近合法备份恢复 key（只替换 LLM_API_KEY 行，
   不触碰 .env 其他内容），并写审计日志
3. --watch N: 每 N 秒轮询，非法即恢复（常驻守护；建议配合计划任务/服务自启）

退出码: 0=key 合法(或已修复), 1=校验失败且未修复(不可用 key)
"""

import argparse
import re
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"
BACKUP_DIR = ENV_FILE.parent / ".env.backups"
AUDIT_LOG = REPO_ROOT / "data" / "health" / "guard_llm_api_key.log"

# 【不易】key 契约：DeepSeek key 形如 sk- 开头 + 32 hex 字符（总长 35）
# 放宽到「sk- 开头且长度 >= 20」以兼容其他厂商；黑名单兜底已知占位符
KEY_RE = re.compile(r"^sk-[\w-]{18,}$")
PLACEHOLDER = {
    "sk-test-1234567890abcdef",          # 并行会话测试 key
    "sk-test", "sk-secret", "sk-test-key",
    "sk-1234567890abcdef", "sk-real-key-123",
    "sk-real-key-original", "sk-instance-key-12345",
}
MIN_LEN = 20


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass  # 审计日志写失败不影响守护主流程


def read_env_key() -> str:
    """读取 .env 中 LLM_API_KEY 的值（首个匹配，去引号）"""
    if not ENV_FILE.exists():
        return ""
    for raw in ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == "LLM_API_KEY":
            return v.strip().strip("\"'")
    return ""


def key_valid(key: str) -> bool:
    """key 合法性校验：格式 + 长度 + 占位符黑名单"""
    if not key:
        return False
    if len(key) < MIN_LEN:
        return False
    if key in PLACEHOLDER:
        return False
    return bool(KEY_RE.match(key))


def find_recover_key() -> str:
    """从 .env.backups 找最近一个含合法 key 的备份，返回该 key（优先同名同前缀）"""
    if not BACKUP_DIR.exists():
        return ""
    current = read_env_key()
    # 按名称倒序（时间戳+序号即时间序）
    backups = sorted(BACKUP_DIR.glob("env.bak.*"), key=lambda p: p.name, reverse=True)
    for bp in backups:
        try:
            text = bp.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() != "LLM_API_KEY":
                continue
            cand = v.strip().strip("\"'")
            if key_valid(cand):
                # 首选与当前同前缀的 key（说明是同一把真 key 的历史备份）
                if current.startswith("sk-") and cand.startswith(current[:8]):
                    return cand
                return cand  # 否则返回最近合法 key
    return ""


def restore_key(new_key: str) -> bool:
    """把 .env 的 LLM_API_KEY 替换为 new_key（保留其余内容），写前留备份"""
    if not ENV_FILE.exists():
        return False
    lines = ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
    replaced = False
    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, _ = line.partition("=")
        if k.strip() == "LLM_API_KEY":
            indent = raw[: len(raw) - len(raw.lstrip())]
            lines[i] = f"{indent}LLM_API_KEY={new_key}"
            replaced = True
            break
    if not replaced:
        lines.append(f"LLM_API_KEY={new_key}")
    # 写前备份（复用现有备份目录约定）
    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        bpath = BACKUP_DIR / f"env.bak.{stamp}_guard"
        import shutil
        shutil.copy2(ENV_FILE, bpath)
    except OSError:
        pass
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def check(do_restore: bool) -> int:
    current = read_env_key()
    if key_valid(current):
        log(f"[CHECK] LLM_API_KEY 合法 (len={len(current)}, prefix={current[:9]}...)")
        return 0
    log(f"[CHECK] LLM_API_KEY 非法: {current[:15] or '(EMPTY)'}... (len={len(current)})")
    if not do_restore:
        log("[CHECK] 未启用 --restore，仅告警。可用 --restore 自动恢复。")
        return 1
    recover = find_recover_key()
    if not recover:
        log("[RESTORE] 备份中未找到合法 key，无法自动恢复（请人工填入真实 key）")
        return 1
    if restore_key(recover):
        log(f"[RESTORE] 已恢复 LLM_API_KEY → (len={len(recover)}, prefix={recover[:9]}...)")
        return 0
    log("[RESTORE] 恢复失败（.env 不可写）")
    return 1


def watch(interval: int, do_restore: bool) -> int:
    log(f"[WATCH] 守护启动: interval={interval}s restore={do_restore}")
    try:
        while True:
            rc = check(do_restore)
            time.sleep(max(1, interval))
            if rc != 0 and not do_restore:
                pass  # 持续告警
    except KeyboardInterrupt:
        log("[WATCH] 守护停止")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM_API_KEY 守护脚本")
    parser.add_argument("--check", action="store_true", help="仅检查一次")
    parser.add_argument("--restore", action="store_true", help="非法时从备份自动恢复")
    parser.add_argument("--watch", type=int, metavar="N",
                        help="常驻守护，每 N 秒检查一次")
    args = parser.parse_args()

    if args.watch:
        return watch(args.watch, args.restore)
    return check(args.restore)


if __name__ == "__main__":
    sys.exit(main())
