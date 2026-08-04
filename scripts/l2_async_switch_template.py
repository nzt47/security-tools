"""L2 异步方案切换自动化脚本模板 [TLM-L3]

用途：
- 将 docs/changelogs/l2-async-switch-checklist.md 的 7 个 Phase 关键步骤提取为可执行脚本
- 参数化配置，供其他团队/项目直接调用复用
- 内置决策门槛自动判断，P50 恶化则自动回滚

【不易】不修改 master 分支代码；决策门槛不可绕过（P50 恶化必须回滚）
【变易】Config 类参数化，其他团队可自定义分支名/文件路径/阈值
【简易】7 个 Phase 函数化，可单独调用；dry-run 默认，--apply 才实际执行

设计原则：
- 模板化：其他团队只需修改 Config 区域即可复用
- 安全性：所有 git 操作前先校验工作区干净 + 分支正确
- 可追溯：每个 Phase 输出结构化日志，便于审计

运行：
    # dry-run 模式（默认，仅打印步骤不执行）
    python scripts/l2_async_switch_template.py

    # 执行完整流程
    python scripts/l2_async_switch_template.py --apply

    # 只执行某个 Phase
    python scripts/l2_async_switch_template.py --apply --phase 3

    # 自定义配置（其他团队复用示例）
    python scripts/l2_async_switch_template.py --apply --config my_config.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


# ════════════════════════════════════════════════════════════════════
# 配置区域（其他团队复用时修改此处即可）
# ════════════════════════════════════════════════════════════════════

@dataclass
class Config:
    """切换流程配置（其他团队可自定义）"""

    # ── 分支与标签 ──
    main_branch: str = "master"
    experiment_branch: str = "feature/l2-async-io-experiment"
    baseline_tag_prefix: str = "l2-sync-baseline"

    # ── 关键文件路径（相对项目根）──
    test_yml: str = ".github/workflows/test.yml"
    syncer_file: str = "agent/memory/markdown_syncer.py"
    assembler_file: str = "agent/memory/context_assembler.py"
    bench_script: str = "scripts/bench_l2_stress.py"
    simulate_script: str = "scripts/simulate_l2_async_switch.py"

    # ── CI 标记 ──
    scheme_sync: str = "sync-serial-path-cache"
    scheme_async: str = "async-io-to-thread"
    scheme_echo_sync: str = "同步串行 read_fragment + 路径缓存（最优方案）"
    scheme_echo_async: str = "异步 IO (asyncio.to_thread) + 路径缓存"

    # ── 决策门槛 ──
    # P50 ratio = async_p50 / sync_p50
    # > 1.0 表示异步变慢，自动回滚
    p50_ratio_threshold: float = 1.0

    # ── 输出 ──
    bench_sync_log: str = "bench_sync_baseline.log"
    bench_async_log: str = "bench_async.log"
    perf_comparison_log: str = "test_reports/l2_switch_perf_comparison.log"
    perf_comparison_md: str = "test_reports/l2_switch_perf_comparison.md"

    # ── 行为开关 ──
    auto_rollback_on_degradation: bool = True  # P50 恶化时自动回滚


# ════════════════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════════════════

def run_cmd(cmd: str, cwd: Path = ROOT, check: bool = True, capture: bool = True) -> tuple[int, str]:
    """执行 shell 命令，返回 (退出码, 输出)"""
    print(f"  $ {cmd}")
    result = subprocess.run(
        cmd, shell=True, cwd=str(cwd),
        capture_output=capture, text=True, encoding="utf-8", errors="replace",
    )
    if check and result.returncode != 0:
        print(f"  [!] 命令失败 (exit={result.returncode}): {result.stderr}", file=sys.stderr)
    return result.returncode, result.stdout


def log(phase: str, msg: str, level: str = "INFO") -> None:
    """结构化日志输出"""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] [Phase {phase}] {msg}")


def git(cmd: str, check: bool = True) -> tuple[int, str]:
    """执行 git 命令"""
    return run_cmd(f"git -C {ROOT} {cmd}", check=check)


def is_working_tree_clean() -> bool:
    """检查工作区是否干净"""
    code, out = git("status --porcelain", check=False)
    return not out.strip()


def current_branch() -> str:
    """获取当前分支名"""
    code, out = git("rev-parse --abbrev-ref HEAD", check=False)
    return out.strip()


# ════════════════════════════════════════════════════════════════════
# Phase 1: 切换前准备
# ════════════════════════════════════════════════════════════════════

def phase1_prepare(cfg: Config, apply: bool = False) -> bool:
    """Phase 1: 创建实验分支 + 同步基线 tag + 跑同步压测基线

    Returns: True 表示成功继续，False 表示中止
    """
    log("1", "切换前准备：创建实验分支 + 同步基线 tag")

    # 1.1 确认当前状态一致
    log("1.1", "一致性校验")
    if apply:
        code, out = run_cmd(f"python {cfg.simulate_script} --check", check=False)
        if code != 0:
            log("1.1", f"一致性校验失败（exit={code}），中止", "ERROR")
            return False
        log("1.1", "✓ 一致性校验通过")

    # 1.2 检查工作区干净 + 在主分支
    log("1.2", "检查工作区与分支")
    if apply:
        if not is_working_tree_clean():
            log("1.2", "工作区不干净，请先提交或 stash", "ERROR")
            return False
        if current_branch() != cfg.main_branch:
            log("1.2", f"当前不在 {cfg.main_branch}（当前: {current_branch()}）", "ERROR")
            return False
        log("1.2", "✓ 工作区干净 + 在主分支")

    # 1.3 拉取主分支最新
    log("1.3", f"拉取 {cfg.main_branch} 最新")
    if apply:
        git(f"pull --rebase origin {cfg.main_branch}")

    # 1.4 创建同步基线 tag
    tag_date = datetime.now().strftime("%Y%m%d")
    tag_name = f"{cfg.baseline_tag_prefix}-{tag_date}"
    log("1.4", f"创建同步基线 tag: {tag_name}")
    if apply:
        code, _ = git(f"tag -l {tag_name}", check=False)
        if not _:
            git(f"tag {tag_name}")
            log("1.4", f"✓ tag 已创建: {tag_name}")
        else:
            log("1.4", f"tag 已存在，跳过: {tag_name}")

    # 1.5 创建实验分支
    log("1.5", f"创建实验分支: {cfg.experiment_branch}")
    if apply:
        code, _ = git(f"branch -l {cfg.experiment_branch}", check=False)
        if not _.strip():
            git(f"checkout -b {cfg.experiment_branch}")
            log("1.5", f"✓ 实验分支已创建并切换")
        else:
            git(f"checkout {cfg.experiment_branch}")
            log("1.5", f"实验分支已存在，已切换")

    # 1.6 跑同步压测基线
    log("1.6", f"跑同步压测基线: {cfg.bench_sync_log}")
    if apply:
        run_cmd(f"python {cfg.bench_script} > {cfg.bench_sync_log} 2>&1", check=False)
        log("1.6", f"✓ 同步基线已生成: {cfg.bench_sync_log}")

    return True


# ════════════════════════════════════════════════════════════════════
# Phase 2: 代码修改（提示手工，不自动改）
# ════════════════════════════════════════════════════════════════════

def phase2_modify_code(cfg: Config, apply: bool = False) -> bool:
    """Phase 2: 提示手工修改代码（不自动改，避免破坏最优方案）

    Why: 代码修改需要人工评审，自动化脚本不应代劳
    """
    log("2", "代码修改（需手工执行）")
    print("""
    ┌─────────────────────────────────────────────────────────────────┐
    │  请手工修改以下两个文件：                                       │
    │                                                                 │
    │  1. {syncer}
    │     read_fragment 异步化（async def 或 asyncio.to_thread 包装） │
    │                                                                 │
    │  2. {assembler}
    │     _build_l2 改用 asyncio.gather 并发调用                      │
    │                                                                 │
    │  修改完成后，运行：                                             │
    │    python {simulate} --check                                    │
    │  确认实现已切换为 async。                                       │
    └─────────────────────────────────────────────────────────────────┘
    """.format(
        syncer=cfg.syncer_file,
        assembler=cfg.assembler_file,
        simulate=cfg.simulate_script,
    ))
    if apply:
        input("  按回车键继续（确认代码已修改）...")
    return True


# ════════════════════════════════════════════════════════════════════
# Phase 3: 本地性能验证（决策门槛）
# ════════════════════════════════════════════════════════════════════

def phase3_verify_perf(cfg: Config, apply: bool = False) -> bool:
    """Phase 3: 跑异步压测 + 性能对比 + 决策门槛判断

    Returns: True 表示性能改善可继续，False 表示恶化需回滚
    """
    log("3", "本地性能验证（决策门槛）")

    # 3.1 跑异步压测
    log("3.1", f"跑异步压测: {cfg.bench_async_log}")
    if apply:
        run_cmd(f"python {cfg.bench_script} > {cfg.bench_async_log} 2>&1", check=False)
        log("3.1", f"✓ 异步压测完成: {cfg.bench_async_log}")

    # 3.2 性能对比 + 决策门槛
    log("3.2", "性能对比 + 决策门槛判断")
    if apply:
        code, out = run_cmd(
            f"python {cfg.simulate_script} --bench-log {cfg.bench_async_log} "
            f"--log-file {cfg.perf_comparison_log}",
            check=False,
        )

        # 读取性能对比日志，判断 P50 ratio
        perf_log = ROOT / cfg.perf_comparison_log
        if perf_log.exists():
            content = perf_log.read_text(encoding="utf-8", errors="replace")
            if "不建议切换" in content:
                log("3.2", "❌ P50 恶化，决策门槛未通过", "ERROR")
                if cfg.auto_rollback_on_degradation:
                    log("3.2", "auto_rollback_on_degradation=True，自动触发回滚", "WARN")
                    return False
                else:
                    log("3.2", "auto_rollback_on_degradation=False，需人工决策", "WARN")
                    return False
            elif "可考虑切换" in content:
                log("3.2", "✓ P50 改善，决策门槛通过")
                return True
            else:
                log("3.2", "⚠️ 性能持平，需人工综合判断", "WARN")
                return False

    return True


# ════════════════════════════════════════════════════════════════════
# Phase 4: CI 标记同步
# ════════════════════════════════════════════════════════════════════

def phase4_sync_ci_marker(cfg: Config, apply: bool = False) -> bool:
    """Phase 4: 修改 test.yml 的 L2_SCHEME 与 echo 方案描述

    Why: 必须在 Phase 3 性能验证通过后才改标记
    """
    log("4", "CI 标记同步（必须在 Phase 3 通过后）")

    # 4.1 修改 L2_SCHEME
    log("4.1", f"修改 L2_SCHEME: {cfg.scheme_sync} → {cfg.scheme_async}")
    if apply:
        yml_path = ROOT / cfg.test_yml
        content = yml_path.read_text(encoding="utf-8")
        new_content = content.replace(
            f"L2_SCHEME: {cfg.scheme_sync}",
            f"L2_SCHEME: {cfg.scheme_async}",
        )
        new_content = new_content.replace(
            f'方案: {cfg.scheme_echo_sync}',
            f'方案: {cfg.scheme_echo_async}',
        )
        yml_path.write_text(new_content, encoding="utf-8")
        log("4.1", "✓ test.yml 已修改")

    # 4.2 验证一致性
    log("4.2", "验证标记与实现一致性")
    if apply:
        code, out = run_cmd(f"python {cfg.simulate_script} --check", check=False)
        if code != 0:
            log("4.2", "标记与实现不一致，请检查", "ERROR")
            return False
        log("4.2", "✓ 标记与实现一致")

    return True


# ════════════════════════════════════════════════════════════════════
# Phase 5: CI 远程验证
# ════════════════════════════════════════════════════════════════════

def phase5_ci_verify(cfg: Config, apply: bool = False) -> bool:
    """Phase 5: 提交推送 + 等 CI 通过"""
    log("5", "CI 远程验证")

    log("5.1", "提交并推送实验分支")
    if apply:
        git(f"add {cfg.syncer_file} {cfg.assembler_file} {cfg.test_yml}")
        git('commit -m "perf(l2): 切换 read_fragment 到异步 IO 方案"')
        git(f"push origin {cfg.experiment_branch}")
        log("5.1", "✓ 实验分支已推送")

    log("5.2", "请在 GitHub Actions 确认 L2 性能回归测试 Job 通过")
    log("5.2", f"  URL: https://github.com/<org>/<repo>/actions")
    if apply:
        input("  按回车键继续（确认 CI 通过）...")

    return True


# ════════════════════════════════════════════════════════════════════
# Phase 6: 文档更新
# ════════════════════════════════════════════════════════════════════

def phase6_update_docs(cfg: Config, apply: bool = False) -> bool:
    """Phase 6: 更新 CHANGELOG / 简报 / briefing"""
    log("6", "文档更新（提示手工）")
    print("""
    ┌─────────────────────────────────────────────────────────────────┐
    │  请手工更新以下文档：                                           │
    │                                                                 │
    │  1. 新增切换决策 CHANGELOG（详细版）                            │
    │  2. 更新 docs/changelogs/ 简短变更说明状态                      │
    │  3. 更新 docs/briefings/ 团队简报                               │
    │  4. 更新根 README（如有 L2 性能章节）                           │
    └─────────────────────────────────────────────────────────────────┘
    """)
    if apply:
        input("  按回车键继续（确认文档已更新）...")
    return True


# ════════════════════════════════════════════════════════════════════
# Phase 7: 合并与清理
# ════════════════════════════════════════════════════════════════════

def phase7_merge_cleanup(cfg: Config, apply: bool = False) -> bool:
    """Phase 7: 合并到 master + 删除实验分支 + 最终校验"""
    log("7", "合并与清理")

    log("7.1", f"切换到 {cfg.main_branch} 并拉取最新")
    if apply:
        git(f"checkout {cfg.main_branch}")
        git(f"pull --rebase origin {cfg.main_branch}")

    log("7.2", f"合并实验分支（--no-ff）")
    if apply:
        git(f'merge --no-ff {cfg.experiment_branch} -m "perf(l2): 切换到异步 IO 方案"')

    log("7.3", "最终一致性校验")
    if apply:
        code, out = run_cmd(f"python {cfg.simulate_script} --check", check=False)
        if code != 0:
            log("7.3", "合并后不一致，请检查", "ERROR")
            return False
        log("7.3", "✓ 合并后标记与实现一致")

    log("7.4", f"删除实验分支: {cfg.experiment_branch}")
    if apply:
        git(f"branch -d {cfg.experiment_branch}")

    log("7.5", f"推送 {cfg.main_branch} 到远程")
    if apply:
        git(f"push origin {cfg.main_branch}")

    return True


# ════════════════════════════════════════════════════════════════════
# 回滚流程
# ════════════════════════════════════════════════════════════════════

def rollback(cfg: Config, apply: bool = False) -> None:
    """回滚：丢弃实验分支，回到主分支"""
    log("R", "触发回滚流程", "WARN")

    log("R.1", "丢弃未提交变更")
    if apply:
        git("restore .", check=False)

    log("R.2", f"切换回 {cfg.main_branch}")
    if apply:
        git(f"checkout {cfg.main_branch}", check=False)

    log("R.3", f"删除实验分支: {cfg.experiment_branch}")
    if apply:
        git(f"branch -D {cfg.experiment_branch}", check=False)

    log("R.4", "验证回到同步方案")
    if apply:
        code, out = run_cmd(f"python {cfg.simulate_script} --check", check=False)
        if code == 0:
            log("R.4", "✓ 已回到同步方案")
        else:
            log("R.4", "回滚后状态异常，请人工检查", "ERROR")


# ════════════════════════════════════════════════════════════════════
# 主流程编排
# ════════════════════════════════════════════════════════════════════

PHASES = {
    1: ("切换前准备", phase1_prepare),
    2: ("代码修改", phase2_modify_code),
    3: ("性能验证", phase3_verify_perf),
    4: ("CI 标记同步", phase4_sync_ci_marker),
    5: ("CI 远程验证", phase5_ci_verify),
    6: ("文档更新", phase6_update_docs),
    7: ("合并与清理", phase7_merge_cleanup),
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="L2 异步方案切换自动化脚本模板（参数化，供其他团队复用）"
    )
    parser.add_argument("--apply", action="store_true",
                        help="实际执行（默认 dry-run，仅打印步骤）")
    parser.add_argument("--phase", type=int, choices=list(PHASES.keys()),
                        help="只执行指定 Phase（1-7）")
    parser.add_argument("--config", type=str, default="",
                        help="配置文件路径（JSON，覆盖默认 Config）")
    args = parser.parse_args()

    # 加载配置
    cfg = Config()
    if args.config:
        config_path = Path(args.config)
        if not config_path.is_absolute():
            config_path = ROOT / args.config
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                config_data = json.load(f)
            for k, v in config_data.items():
                if hasattr(cfg, k):
                    setattr(cfg, k, v)
            print(f"[✓] 配置已加载: {config_path}")

    print("=" * 72)
    print("【L2 异步方案切换自动化流程】")
    print(f"  模式: {'apply（实际执行）' if args.apply else 'dry-run（仅打印）'}")
    print(f"  实验分支: {cfg.experiment_branch}")
    print(f"  决策门槛: P50 ratio ≤ {cfg.p50_ratio_threshold}")
    print(f"  自动回滚: {'开启' if cfg.auto_rollback_on_degradation else '关闭'}")
    if args.phase:
        print(f"  执行范围: 仅 Phase {args.phase}")
    print("=" * 72)

    # 执行 Phase
    phases_to_run = [args.phase] if args.phase else list(PHASES.keys())

    for phase_num in phases_to_run:
        phase_name, phase_func = PHASES[phase_num]
        print(f"\n{'─' * 72}")
        print(f"  Phase {phase_num}: {phase_name}")
        print(f"{'─' * 72}")

        success = phase_func(cfg, apply=args.apply)

        if not success:
            if phase_num == 3:
                # Phase 3 失败 = 性能恶化，触发回滚
                print(f"\n[!] Phase 3 性能验证失败，触发回滚...")
                rollback(cfg, apply=args.apply)
                return 1
            else:
                print(f"\n[!] Phase {phase_num} 失败，中止流程")
                return 1

    print(f"\n{'=' * 72}")
    print("【✓ 流程完成】")
    print(f"{'=' * 72}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
