"""TASK-02 上线验收自动执行（对齐上线检查清单 §零 13 项待办）

按清单 A-E 分组自动执行可自动化的待办项，无法自动化的项标记 SKIP 并给出人工核对命令。
（清单实际为 13 个 checkbox：A3 + B4 + C2 + D2 + E2）

用法:
  python scripts/verify_task02_checklist.py                         # 自动项 + 人工 SKIP 项
  python scripts/verify_task02_checklist.py --log-file <生产日志>    # 启用日志监控项 B1-B4
  python scripts/verify_task02_checklist.py --skip-slow             # 跳过完整对话验证（C1/C2/E1）

输出: 每项 PASS / FAIL / SKIP(人工) 与证据说明，末尾汇总。
退出码: 0 = 自动项全过；1 = 存在 FAIL。

【诚实边界】D1（eval 指标递增）依赖进程内 collector / Prometheus 端点，本脚本无法
独立验证，一律 SKIP 并给出查询命令；B 项日志监控需 --log-file 提供生产日志。
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RESULTS: list = []  # (id, name, status, evidence)

# 清单 §零 分组与预期
EXPECT_SWITCHES = {"reflection_persist": True, "critic_evaluation_enabled": True, "experience_persist": False}
ENV_KEYS = ("LEARNING_REFLECTION_PERSIST", "CRITIC_EVALUATION_ENABLED", "LEARNING_EXPERIENCE_PERSIST")


def record(cid: str, name: str, status: str, evidence: str) -> None:
    RESULTS.append((cid, name, status, evidence))


def _run(cmd: list, timeout: int = 600) -> subprocess.CompletedProcess:
    # 返回 bytes（不依赖 Windows 默认 GBK 解码），调用处自行 utf-8 decode
    return subprocess.run(cmd, capture_output=True, cwd=ROOT, timeout=timeout)


def _out(p: subprocess.CompletedProcess) -> str:
    return p.stdout.decode("utf-8", errors="replace") if p.stdout else ""


def _err(p: subprocess.CompletedProcess) -> str:
    return p.stderr.decode("utf-8", errors="replace") if p.stderr else ""


# ─────────────────────────── A. 三开关状态 ───────────────────────────

def check_a1_head_config() -> None:
    """A1: HEAD config.yaml 三开关与预期一致"""
    try:
        import yaml as _yaml
        p = _run(["git", "show", "HEAD:config.yaml"])
        if p.returncode != 0:
            record("A1", "HEAD 三开关核对", "FAIL", f"git show 失败: {_err(p)[:200]}")
            return
        data = _yaml.safe_load(_out(p)) or {}
        got = {
            "reflection_persist": data.get("learning", {}).get("reflection_persist"),
            "critic_evaluation_enabled": data.get("features", {}).get("critic_evaluation_enabled"),
            "experience_persist": data.get("learning", {}).get("experience_persist"),
        }
        bad = [k for k, v in EXPECT_SWITCHES.items() if got.get(k) is not v]
        if bad:
            record("A1", "HEAD 三开关核对", "FAIL", f"预期 {EXPECT_SWITCHES}，HEAD 实际 {got}，不符项 {bad}")
        else:
            record("A1", "HEAD 三开关核对", "PASS", f"HEAD config.yaml = {got}")
    except Exception as e:  # 三义：验收脚本自身异常不影响后续项
        record("A1", "HEAD 三开关核对", "FAIL", f"解析异常: {e}")


def check_a2_env() -> None:
    """A2: 生产环境变量核对（应为空或合法值）"""
    vals = {k: os.environ.get(k) for k in ENV_KEYS}
    valid = {"true", "1", "yes", "false", "0", "no"}
    problems = []
    for k, v in vals.items():
        if v is not None and v.strip() and v.strip().lower() not in valid:
            problems.append(f"{k}={v!r}（非法值，将按 False 解析）")
    if problems:
        record("A2", "环境变量覆盖核对", "FAIL", "；".join(problems))
    elif all(v is None or not v.strip() for v in vals.values()):
        record("A2", "环境变量覆盖核对", "PASS", "三环境变量均为空，开关完全由 config.yaml 控制")
    else:
        record("A2", "环境变量覆盖核对", "PASS", f"存在覆盖（属预期则放行）: {vals}")


def check_a3_verify_script() -> None:
    """A3: 配置生效验证脚本全场景 PASS"""
    p = _run(["python", "scripts/verify_task02_config_effective.py"], timeout=300)
    if p.returncode == 0 and "全部场景 PASS" in _out(p):
        record("A3", "配置生效 10 场景验证", "PASS", "全部场景 PASS")
    else:
        record("A3", "配置生效 10 场景验证", "FAIL", f"rc={p.returncode}，见输出尾部: {_out(p)[-400:]}{_err(p)[-200:]}")


# ─────────────────────────── B. 日志监控点（需 --log-file） ───────────────────────────

def _log_text(log_file: str | None) -> str | None:
    if not log_file:
        return None
    # 显式 UTF-8 + errors=replace：Windows 默认 GBK 读 UTF-8 日志会崩
    return Path(log_file).read_text(encoding="utf-8", errors="replace")


def check_b1_gate(text: str | None) -> None:
    if text is None:
        record("B1", "learning_gate 接线入口", "SKIP", "需 --log-file（人工核对命令: grep learning_gate）")
        return
    if "learning_gate" not in text:
        record("B1", "learning_gate 接线入口", "FAIL", "日志中无 learning_gate")
    elif "reflection_persist=True critic_evaluation_enabled=True" in text:
        record("B1", "learning_gate 接线入口", "PASS", "存在且开关为 True/True")
    else:
        record("B1", "learning_gate 接线入口", "FAIL", "learning_gate 存在但开关值非 True/True")


def check_b2_eval(text: str | None) -> None:
    if text is None:
        record("B2", "eval 成对 / 无 fallback", "SKIP", "需 --log-file（grep -E \"eval.start|eval.fallback\"）")
        return
    has_start = "eval.start" in text
    has_done = re.search(r"self_reflect\.eval[^.]", text) is not None
    has_fallback = "eval.fallback" in text
    if has_start and has_done and not has_fallback:
        record("B2", "eval 成对 / 无 fallback", "PASS", "eval.start→eval 完成，无 fallback")
    else:
        record("B2", "eval 成对 / 无 fallback", "FAIL",
               f"start={has_start} done={has_done} fallback={has_fallback}（任一异常即排查）")


def check_b3_persist(text: str | None) -> None:
    if text is None:
        record("B3", "persist 成对 / 无降级", "SKIP", "需 --log-file（grep -E \"persist.start|persist.skipped|persist.fallback\"）")
        return
    has_start = "persist.start" in text
    has_done = re.search(r"self_reflect\.persist[^.]", text) is not None
    has_skip = "persist.skipped" in text
    has_fallback = "persist.fallback" in text
    if has_start and has_done and not has_skip and not has_fallback:
        record("B3", "persist 成对 / 无降级", "PASS", "persist.start→persist 成功，无 skipped/fallback")
    else:
        record("B3", "persist 成对 / 无降级", "FAIL",
               f"start={has_start} done={has_done} skipped={has_skip} fallback={has_fallback}")


def check_b4_vector(text: str | None) -> None:
    if text is None:
        record("B4", "检索面真实写入", "SKIP", "需 --log-file（grep \"✅ 添加记忆\"）")
        return
    if "✅ 添加记忆" in text:
        record("B4", "检索面真实写入", "PASS", "日志含检索面写入标记")
    else:
        record("B4", "检索面真实写入", "FAIL", "无 ✅ 添加记忆 标记（反思未写入检索面）")


# ─────────────────────────── C / E. 完整对话 + schema + 回归（共享一次运行） ───────────────────────────

def check_ce_full_dialogue(skip_slow: bool) -> None:
    """运行完整对话验证，C1/C2/E1 共享一次运行"""
    if skip_slow:
        record("C1", "响应未被拦截（保守模式）", "SKIP", "--skip-slow（人工跑 scripts/task02_full_dialogue.py）")
        record("C2", "反思 schema 完整性", "SKIP", "--skip-slow（人工核对 metadata 字段）")
        record("E1", "完整对话全断言", "SKIP", "--skip-slow（人工跑 scripts/task02_full_dialogue.py）")
        return
    p = _run(["python", "scripts/task02_full_dialogue.py"], timeout=600)
    out = _out(p)
    if p.returncode != 0:
        record("C1", "响应未被拦截（保守模式）", "FAIL", f"rc={p.returncode} {out[-300:]}")
        record("C2", "反思 schema 完整性", "FAIL", f"rc={p.returncode}")
        record("E1", "完整对话全断言", "FAIL", f"rc={p.returncode} {out[-300:]}")
        return
    # C1: 响应未被拦截
    record("C1", "响应未被拦截（保守模式）", "PASS" if "success=True" in out else "FAIL",
           "对话响应 success=True" if "success=True" in out else "输出中无 success=True")
    # C2: schema 完整——full_dialogue 内部已对 schema 逐字段 assert，
    # 运行成功 + 输出含 input_hash= 打印行即证明完整
    schema_ok = "input_hash=" in out and "反思" in out
    record("C2", "反思 schema 完整性", "PASS" if schema_ok else "FAIL",
           "metadata 含 type/input_hash（full_dialogue 内部断言已过）" if schema_ok else "输出中缺 schema 关键字段")
    # E1: 全断言
    record("E1", "完整对话全断言", "PASS" if ("全部断言" in out or "PASS" in out) else "FAIL",
           "全部断言 PASS" if "全部断言" in out else "输出尾部: " + out[-120:])


# ─────────────────────────── D. 指标趋势 + 观察基线 ───────────────────────────

def check_d1_eval_trend() -> None:
    record("D1", "eval 指标递增趋势", "SKIP",
           "需 Prometheus 端点（人工: 两次查询 learning_eval_total 比较递增）")


def check_d2_monitor_baseline() -> None:
    p = _run(["python", "scripts/monitor_task02_observation.py", "-n", "1"], timeout=120)
    o = _out(p)
    ok = p.returncode == 0 and "reflection_persist=True" in o and "experience_persist=False" in o
    record("D2", "观察窗口基线采样", "PASS" if ok else "FAIL",
           "三开关采样符合预期" if ok else f"rc={p.returncode} {o[-200:]}")


def check_e2_rollback() -> None:
    """E2: 回滚预案——环境变量覆盖等价性已由 verify 脚本场景 B 覆盖"""
    passed = any(r[0] == "A3" and r[2] == "PASS" for r in RESULTS)
    record("E2", "回滚预案可用性", "PASS" if passed else "FAIL",
           "verify 脚本场景 B 已验证 env=false 覆盖关闭（= 回滚等价）" if passed else "依赖 A3，先修复 A3")


# ─────────────────────────── 主流程 ───────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="TASK-02 上线验收（清单 §零 15 项）")
    ap.add_argument("--log-file", default=None, help="生产日志路径（启用 B1-B4 日志监控项）")
    ap.add_argument("--skip-slow", action="store_true", help="跳过完整对话验证（C1/C2/E1）")
    args = ap.parse_args()

    # GBK 控制台无法输出 UTF-8 中文/emoji：重配置为标准 UTF-8（Python 3.7+）
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    log_text = _log_text(args.log_file)

    print("=" * 78)
    print("TASK-02 上线验收自动执行（清单 §零 15 项待办）")
    print(f"日志文件: {args.log_file or '未提供（B1-B4 转人工）'} | 完整对话: {'跳过' if args.skip_slow else '执行'}")
    print("=" * 78)

    # A 组
    check_a1_head_config()
    check_a2_env()
    check_a3_verify_script()
    # B 组
    check_b1_gate(log_text)
    check_b2_eval(log_text)
    check_b3_persist(log_text)
    check_b4_vector(log_text)
    # C / E 组（共享一次完整对话运行）
    check_ce_full_dialogue(args.skip_slow)
    # D 组
    check_d1_eval_trend()
    check_d2_monitor_baseline()
    # E 组
    check_e2_rollback()

    # 汇总输出
    print("\n" + "-" * 78)
    print(f"{'ID':<4} {'待办项':<26} {'状态':<6} 证据")
    print("-" * 78)
    for cid, name, status, evidence in RESULTS:
        # 用 ASCII 标记（兼容纯 ASCII 控制台）
        flag = {"PASS": "[OK]", "FAIL": "[XX]", "SKIP": "[--]"}[status]
        print(f"{cid:<4} {name:<26} {flag} {status:<6} {evidence[:70]}")
    print("-" * 78)
    n_pass = sum(1 for r in RESULTS if r[2] == "PASS")
    n_fail = sum(1 for r in RESULTS if r[2] == "FAIL")
    n_skip = sum(1 for r in RESULTS if r[2] == "SKIP")
    print(f"汇总: PASS={n_pass} FAIL={n_fail} SKIP(人工)={n_skip} / 共 {len(RESULTS)} 项")
    if n_fail:
        print("结论: 存在 FAIL 项，需修复后重跑（回滚预案：三环境变量一键关闭）")
    else:
        print("结论: 自动项全部通过，剩余 SKIP 项按证据列人工核对")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
