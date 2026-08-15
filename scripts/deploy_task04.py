#!/usr/bin/env python3
"""TASK-04 知识→技能沉淀管道 一键应用/验证脚本

用途（Why）: 后续环境（新机器/新分支/新部署）应用 TASK-04 时，
一键核对代码、接线、配置、测试是否全部就位，避免手工逐项检查遗漏。

应用面（TASK-04 变更说明 §2）:
    1. 连接器  agent/knowledge/skill_bridge.py + CLI convert-cards
    2. 调度    agent/skills_mgmt/precipitate.py（默认关闭，安全底线）
    3. 审核链  agent/skills_mgmt/review_gate.py + service.publish + HTTP 路由
    4. 配置    config.yaml（learning.precipitate_* / skills_mgmt.review.*）

用法:
    python scripts/deploy_task04.py               # 全量校验 + 跑 3 个新测试文件
    python scripts/deploy_task04.py --skip-tests  # 仅静态校验（秒级，CI 快速门禁）
    python scripts/deploy_task04.py --check-only  # 同 --skip-tests（别名，兼容旧习惯）

退出码: 0 = 全部就绪；1 = 存在缺失项（任一 FAIL）。

【不易】约束: 只读校验 + 可选测试执行，不修改任何业务代码与配置；
          不改 .env / config.yaml（缺失时仅报告并给出修复指引）。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ─── 检查清单（TASK-04 交付物） ──────────────────────────────────

# 1) 新增模块文件（缺 = 代码未同步/未合并）
SOURCE_FILES = [
    ("agent/knowledge/skill_bridge.py", "Step1 连接器模块"),
    ("agent/skills_mgmt/precipitate.py", "Step2 沉淀调度模块"),
    ("agent/skills_mgmt/review_gate.py", "Step3 强制审核链模块"),
]
# 2) 测试文件（缺 = 测试未同步）
TEST_FILES = [
    "tests/unit/test_knowledge_skill_bridge.py",
    "tests/unit/test_precipitate_scheduler.py",
    "tests/unit/test_review_enforcement.py",
]
# 3) 接线标记（既有文件内的关键追加点；缺 = 被并行会话覆盖/合并遗漏）
WIRING_MARKERS = [
    ("agent/knowledge/__main__.py", "convert-cards", "CLI convert-cards 子命令"),
    ("agent/skills_mgmt/service.py", "enforce_review", "service.publish 强制审核接线"),
    ("agent/server_routes/routes_skills_mgmt.py", "skills-mgmt/<skill_id>/publish",
     "HTTP publish 路由"),
]
# 4) config.yaml 关键键（缺 = 配置未同步）
CONFIG_KEYS = [
    ("learning", "precipitate_enabled", "沉淀调度开关（默认 false）"),
    ("learning", "precipitate.interval_hours", "沉淀间隔（默认 24h）"),
    ("learning", "precipitate.audit_file", "沉淀审计日志路径"),
    ("skills_mgmt", "review.enforce_before_publish", "发布强制审核（默认 true）"),
    ("skills_mgmt", "review.audit_file", "豁免发布审计日志路径"),
]

# 运行时开关指引（输出提示用）
RUNTIME_SWITCHES = {
    "LEARNING_PRECIPITATE_ENABLED": "learning.precipitate_enabled（默认 false：不注册沉淀调度）",
    "LEARNING_PRECIPITATE_INTERVAL_HOURS": "learning.precipitate.interval_hours（默认 24）",
    "SKILLS_REVIEW_ENFORCE_PUBLISH": "skills_mgmt.review.enforce_before_publish（默认 true：强制审核）",
}


def log(status: str, msg: str) -> None:
    print(f"  [{status:4}] {msg}")


def check_file(path: str, desc: str) -> bool:
    p = ROOT / path
    ok = p.exists()
    log("PASS" if ok else "FAIL", f"{desc}: {path}" if ok else f"{desc} 缺失: {path}")
    return ok


def check_marker(path: str, marker: str, desc: str) -> bool:
    p = ROOT / path
    if not p.exists():
        log("FAIL", f"{desc} 文件缺失: {path}")
        return False
    ok = marker in p.read_text(encoding="utf-8", errors="replace")
    log("PASS" if ok else "FAIL", f"{desc}: {path} 含 '{marker}'"
        if ok else f"{desc}: {path} 未找到 '{marker}'（可能被并行会话覆盖）")
    return ok


def check_config() -> bool:
    cfg_path = ROOT / "config.yaml"
    if not cfg_path.exists():
        log("FAIL", f"config.yaml 不存在: {cfg_path}")
        return False
    try:
        import yaml
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001 配置解析失败即视为异常
        log("FAIL", f"config.yaml 解析失败: {e}")
        return False

    all_ok = True
    for section, key, desc in CONFIG_KEYS:
        # 支持点分键（precipitate.interval_hours → learning.precipitate.interval_hours）
        node: object = cfg.get(section, {}) or {}
        for part in key.split("."):
            if not isinstance(node, dict):
                node = {}
                break
            node = node.get(part)
        found = node is not None
        all_ok &= found
        log("PASS" if found else "FAIL",
            f"config.{section}.{key} 已配置（{desc}）" if found
            else f"config.{section}.{key} 缺失（{desc}）")
    return all_ok


def run_tests() -> bool:
    """运行 3 个 TASK-04 新测试文件；以汇总行判定（项目教训：勿只信 rc）。"""
    log("INFO", f"运行测试: {' '.join(TEST_FILES)}")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", *TEST_FILES, "-q", "-p", "no:randomly"],
            capture_output=True, text=True, encoding="utf-8",
            cwd=str(ROOT), timeout=300,
        )
    except subprocess.TimeoutExpired:
        log("FAIL", "测试超时（300s）")
        return False
    out = proc.stdout + proc.stderr
    # 汇总行形如 "== 19 passed in 12.69s =="；无 passed 即未完整执行
    import re
    summary = re.findall(r"==\s+(\d+)\s+passed.*?==", out)
    if not summary:
        log("FAIL", f"无 pytest 汇总行（rc={proc.returncode}），疑似被强杀:\n{out[-800:]}")
        return False
    passed = int(summary[-1])
    ok = proc.returncode == 0 and passed > 0
    log("PASS" if ok else "FAIL", f"测试汇总: {passed} passed (rc={proc.returncode})")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(
        description="TASK-04 一键应用/验证（只读校验 + 可选测试）")
    ap.add_argument("--skip-tests", action="store_true",
                    help="跳过测试执行（仅静态校验，秒级）")
    ap.add_argument("--check-only", action="store_true",
                    help="同 --skip-tests（别名）")
    args = ap.parse_args()

    print("=== TASK-04 部署校验 ===")
    ok = True

    print("[1/4] 新增模块文件")
    for path, desc in SOURCE_FILES:
        ok &= check_file(path, desc)

    print("[2/4] 接线标记")
    for path, marker, desc in WIRING_MARKERS:
        ok &= check_marker(path, marker, desc)

    print("[3/4] 配置键")
    ok &= check_config()

    print("[4/4] 测试")
    if args.skip_tests or args.check_only:
        log("SKIP", "已跳过测试执行（--skip-tests）")
    else:
        ok &= run_tests()

    print("=== 运行时开关指引 ===")
    for env, desc in RUNTIME_SWITCHES.items():
        print(f"  {env}  →  {desc}")

    print(f"\n结果: {'ALL PASS' if ok else 'HAS FAILURES'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
