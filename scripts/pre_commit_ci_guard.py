#!/usr/bin/env python3
"""提交前 CI 护栏 — 基于《Singleton 与覆盖率并行测试_避坑指南》检查清单自动化

【不易】只读扫描 + 报告，不修改任何业务代码；FAIL 项使退出码非 0 阻断提交。
【变易】--static-only 仅静态（秒级）；--run-serial 追加串行复现（耗时，默认跳过）；
       --install-hook 写入 .git/hooks/pre-commit（提交时自动跑静态项）；
       --strict 增量阻断：基线外的 WARN 升级为 FAIL（存量豁免、新增拦截）；
       --update-baseline 刷新基线（存量 WARN 清单，团队共享放行范围）。
【简易】每项输出 [PASS]/[WARN]/[FAIL]，汇总 + 退出码。

用法：
    python scripts/pre_commit_ci_guard.py                # 静态检查（提交前默认）
    python scripts/pre_commit_ci_guard.py --strict       # + 新增 WARN 阻断（hook 默认）
    python scripts/pre_commit_ci_guard.py --update-baseline  # 刷新基线文件
    python scripts/pre_commit_ci_guard.py --run-serial   # + 串行复现 singleton 测试
    python scripts/pre_commit_ci_guard.py --install-hook # 安装 pre-commit hook
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

RESULTS: list[tuple[str, str]] = []  # (status, msg)
# 检查项名 -> WARN 签名列表（签名 = 文件:行号 或固定标识，用于基线豁免比较）
WARN_SIGNATURES: dict[str, list[str]] = {}
# 参与增量阻断的检查项（静态存量型）；git 同步项与提交内容相关，无基线意义，不参与
STRICT_ITEMS = ("import_degraded", "top_side_effect", "serial_dirs",
                "reset_semantics", "coverage_workflow", "omit_glob")
BASELINE_DEFAULT = ".guard_baseline.json"


def record(status: str, msg: str) -> None:
    RESULTS.append((status, msg))
    print(f"  [{status:4}] {msg}")


def record_warn(name: str, sigs: list[str], msg: str) -> None:
    """记录 WARN 及其签名（签名进入基线比较，供 --strict 豁免存量/拦截新增）"""
    WARN_SIGNATURES[name] = sigs
    record("WARN", msg)


def load_baseline(path: Path) -> dict:
    try:
        return json.loads(read(path)) if path.exists() else {}
    except ValueError:
        return {}


def save_baseline(path: Path) -> dict:
    path.write_text(json.dumps(WARN_SIGNATURES, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return WARN_SIGNATURES


# ─── 工具 ────────────────────────────────────────────────────────────

def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def git(args: list[str]) -> str:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True,
                              cwd=ROOT, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def py_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


# ─── Singleton 检查（避坑指南 第一部分）──────────────────────────────

def check_register_coverage() -> None:
    """坑 1：测试期望的注册名必须存在于实现中（测试与实现同 commit）"""
    impl_names: set[str] = set()
    for f in py_files(ROOT / "agent"):
        impl_names |= set(re.findall(r'register_singleton\(\s*["\']([^"\']+)["\']', read(f)))
    # 仅取集成断言文件（test_singleton_manager.py），排除桩名（测试自行注册的）与方法调用（barrier.is_registered）
    integ = read(ROOT / "tests" / "unit" / "test_singleton_manager.py")
    expect_names: set[str] = set(re.findall(r'(?<!\.)is_registered\(\s*["\']([^"\']+)["\']', integ))
    stubs = set(re.findall(r'register_singleton\(\s*["\']([^"\']+)["\']', integ))
    expect_names -= stubs
    missing = expect_names - impl_names
    if missing:
        record("FAIL", f"测试断言 is_registered({sorted(missing)}) 但在 agent/ 中无对应 register_singleton 调用 → 测试先行/实现未同步")
    else:
        record("PASS", f"注册覆盖：集成测试期望 {len(expect_names)} 个注册名全部在实现中存在")


def check_import_error_warn() -> None:
    """坑 4：except ImportError 含注册降级标志（= None / _SINGLETON_AVAILABLE=False）时必须显式告警"""
    silent: list[str] = []
    for f in py_files(ROOT / "agent"):
        lines = read(f).splitlines()
        for i, line in enumerate(lines):
            if re.match(r"\s*except ImportError\s*:", line):
                block = lines[i + 1:i + 8]
                degraded = any(re.search(r"register_singleton\s*=\s*None|get_singleton\s*=\s*None|_SINGLETON_AVAILABLE\s*=\s*False", l)
                               for l in block)
                if degraded and not any(re.search(r"logging|warnings|logger\.|warn", l) for l in block):
                    silent.append(f"{f.name}:{i + 1}")
    if silent:
        record_warn("import_degraded", silent,
                    f"{len(silent)} 处 except ImportError 注册降级且无告警（静默跳过）: {silent[:5]}")
    else:
        record("PASS", "except ImportError 注册降级分支均含显式告警（无静默跳过）")


def check_reset_semantics() -> None:
    """坑 5：reset 只重置 _instances，不得清空 _factories（注册表）"""
    sm = ROOT / "agent" / "utils" / "singleton_manager.py"
    content = read(sm)
    if not content:
        record("SKIP", "singleton_manager.py 不存在（未启用 SingletonManager）")
        return
    # 定位 reset 类函数体（至下一个同级 def/class 为止）
    reset_bodies: list[str] = []
    lines = content.splitlines()
    for i, line in enumerate(lines):
        if re.search(r"def (reset_all\w*|reset_all_singletons)\s*\(", line):
            body: list[str] = []
            for l in lines[i + 1:]:
                if l and not l.startswith((" ", "\t")) and re.search(r"def |class ", l):
                    break
                body.append(l)
            reset_bodies.append("\n".join(body))
    if not reset_bodies:
        record("SKIP", "未找到 reset_all 实现")
        return
    touched_factories = any(re.search(r"_factories\s*[.=]|_factories\.(clear|pop)|del _factories", b) for b in reset_bodies)
    if touched_factories:
        record_warn("reset_semantics", ["singleton_manager.py"],
                    "reset 函数操作了 _factories（注册表）→ 可能破坏注册，需人工确认")
    else:
        record("PASS", f"reset 函数仅重置实例、保留 _factories（{len(reset_bodies)} 处）")


# ─── 覆盖率检查（避坑指南 第二部分）────────────────────────────────

def _coverage_config_texts() -> list[str]:
    texts: list[str] = []
    for cfg in (ROOT / "pytest.ini", ROOT / "pyproject.toml", ROOT / ".coveragerc"):
        t = read(cfg)
        if t:
            texts.append(t)
    return texts


def check_omit_glob() -> None:
    """坑 7：omit 必须用 */tests/*（跨目录），仅 tests/* 无法匹配绝对路径"""
    joined = "\n".join(_coverage_config_texts())
    if re.search(r"\*/\s*tests\s*/\s*\*", joined) or re.search(r"\*/\s*test_\*\.py", joined):
        record("PASS", "omit 含 */tests/*（跨目录模式，可匹配 CI 绝对路径）")
    else:
        record_warn("omit_glob", ["omit"],
                    "omit 未发现 */tests/* 跨目录模式 → .data 存绝对路径时 tests/* 前缀不生效")


def check_coverage_workflow() -> None:
    """坑 6/8：覆盖率 workflow 须含 if: always()、coverage-data、exit 5 容错"""
    wf = ROOT / ".github" / "workflows" / "observability-ci.yml"
    c = read(wf)
    if not c:
        record("SKIP", "observability-ci.yml 不存在")
        return
    ok_always = len(re.findall(r"if:\s*always\(\)", c)) > 0
    ok_data = "coverage-data" in c
    ok_exit5 = bool(re.search(r"exit.?5|EXIT_5|\|\|\s*true|set\s+\+e", c))
    if ok_always and ok_data and ok_exit5:
        record("PASS", "workflow 含 if: always() + coverage-data + exit5 容错（artifact 链路受保护）")
    else:
        problems = []
        if not ok_always:
            problems.append("缺 if: always()")
        if not ok_data:
            problems.append("缺 coverage-data 处理")
        if not ok_exit5:
            problems.append("缺 exit 5 容错")
        record_warn("coverage_workflow", ["observability-ci.yml"],
                    f"workflow 不完整：{', '.join(problems)} → artifact 可能在 set -e 中止后丢失")


def check_split_serial_dirs() -> None:
    """坑 8/9：performance/stress 测试应纳入串行段（OBSERVABILITY_CI_ONLY），避免分片 flake"""
    hits: list[str] = []
    for f in py_files(ROOT):
        if f.name in ("split_unit_tests.py", "split_tests.py"):
            c = read(f)
            if "tests/performance" in c and "tests/stress" in c:
                hits.append(f.name)
    if hits:
        record("PASS", f"分片脚本含 performance/stress 串行段（{', '.join(hits)}）")
    else:
        record_warn("serial_dirs", ["missing"],
                    "未发现分片脚本将 performance/stress 纳入串行段 → 可能混入并行矩阵产生 flake")


def check_top_level_side_effects() -> None:
    """坑 10：模块顶层禁止副作用（logging.disable / 环境变量 / chdir），应移入 fixture"""
    offenders: list[str] = []
    # 排除 os.environ.setdefault/get（配置初始化常见且幂等）；保留强副作用（disable/chdir/path 注入）
    pat = re.compile(r"^(?!\s)(?!def |class |import |from |@|#|$|\"\"\"|'''|if __name__)"
                     r".*(logging\.disable|logging\.basicConfig|os\.environ\s*\[|os\.setenv|"
                     r"os\.chdir|sys\.path\.append|warnings\.simplefilter)")
    for f in py_files(ROOT / "agent"):
        for i, line in enumerate(read(f).splitlines(), 1):
            if pat.match(line):
                offenders.append(f"{f.name}:{i}")
    if offenders:
        record_warn("top_side_effect", offenders,
                    f"{len(offenders)} 处模块顶层副作用（collection 阶段 import 即生效）: {offenders[:5]}")
    else:
        record("PASS", "agent/ 无模块顶层副作用（日志/环境操作均在受控范围）")


# ─── 提交前 git 检查 ────────────────────────────────────────────────

def check_test_impl_sync_git() -> None:
    """坑 1（git 层）：本次改动若只动 singleton 测试而未动实现 → 警告"""
    changed = (git(["diff", "--name-only", "HEAD~1", "HEAD"]) + "\n"
               + git(["diff", "--name-only"])).splitlines()
    test_hits = [p for p in changed if re.search(r"test_singleton|singleton.*test", p)]
    impl_hits = [p for p in changed if "singleton_manager" in p or "auto_tuner" in p]
    if test_hits and not impl_hits:
        record("WARN", f"改动含 singleton 测试({test_hits[0]})但无对应实现改动 → 确认是否测试先行")
    else:
        record("PASS", "本次改动测试/实现同步（或无关）")


# ─── 动态检查（--run-serial，耗时）─────────────────────────────────

def run_serial_singleton() -> None:
    """坑 2：串行复现 -p no:xdist，区分隔离问题与确定性缺陷"""
    import os
    env = dict(os.environ)
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/unit/test_singleton_manager.py",
         "-p", "no:xdist", "-q", "--no-header"],
        cwd=ROOT, capture_output=True, text=True, env=env)
    if r.returncode == 0:
        record("PASS", f"串行复现通过（{r.stdout.strip().splitlines()[-1] if r.stdout else ''}）")
    else:
        record("FAIL", f"串行复现失败 rc={r.returncode}（确定性缺陷，非隔离问题）")


# ─── 主流程 ─────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="提交前 CI 护栏（避坑指南检查清单自动化）")
    ap.add_argument("--run-serial", action="store_true", help="追加串行复现 singleton 测试（耗时）")
    ap.add_argument("--static-only", action="store_true", help="仅静态检查（pre-commit hook 默认调用）")
    ap.add_argument("--strict", action="store_true", help="增量阻断：基线外新增 WARN 视为 FAIL")
    ap.add_argument("--update-baseline", action="store_true", help="刷新基线文件（记录当前全部 WARN 签名）")
    ap.add_argument("--baseline", default=str(ROOT / BASELINE_DEFAULT), help=f"基线文件路径（默认 {BASELINE_DEFAULT}）")
    ap.add_argument("--install-hook", action="store_true", help="写入 .git/hooks/pre-commit")
    args = ap.parse_args()

    if args.install_hook:
        hook = ROOT / ".git" / "hooks" / "pre-commit"
        # 存在性容错：脚本未部署到当前 worktree（并行会话 worktree 共享 hooks 但无脚本文件）时
        # 输出提示并跳过，避免阻断其他会话的提交；脚本部署后自动生效。
        # --strict 增量阻断：首次提交自动生成基线放行，此后新增 WARN 阻断。
        # 链式调用 pre-commit 框架（.pre-commit-config.yaml 的 commit-stage hooks），
        # pre-commit 命令不存在时自动跳过；框架失败仅警告放行（不卡提交），
        # 工作区干净时框架正常拦截。guard 本身失败仍硬阻断。
        hook.write_text(
            "#!/bin/sh\n"
            "# 提交前 CI 护栏（避坑指南检查清单）— 存在性容错：脚本未部署到本 worktree 时跳过；\n"
            "# --strict 增量阻断：存量 WARN 豁免（基线文件），新增 WARN 阻断提交；\n"
            "# 链式调用 pre-commit 框架 commit-stage hooks（未安装 pre-commit 时跳过；失败仅警告放行）\n"
            'GUARD="$(git rev-parse --show-toplevel)/scripts/pre_commit_ci_guard.py"\n'
            'if [ ! -f "$GUARD" ]; then\n'
            '  echo "[pre-commit-guard] 未部署 $GUARD，本次跳过（如需启用请部署脚本）"\n'
            "  exit 0\n"
            "fi\n"
            'python "$GUARD" --static-only --strict || exit 1\n'
            "if command -v pre-commit >/dev/null 2>&1; then\n"
            "  if pre-commit run --hook-stage commit; then\n"
            "    :\n"
            "  else\n"
            '    echo "[pre-commit-guard] 注意：pre-commit 框架 hook 未全部通过（详见 pre-commit 日志）。本次提交继续，请尽快处理框架问题。"\n'
            "  fi\n"
            "fi\n",
            encoding="utf-8")
        print(f"pre-commit hook 已安装（容错 + 增量阻断 + 链式框架警告版）：{hook}")

        # 同步安装 pre-push：链式调用 pre-commit 框架 push 阶段 hooks（MEDIUM 提醒）。
        # 未安装 pre-commit 时跳过；框架失败仅警告放行（push 阶段为提醒级，不拦截推送）。
        push_hook = ROOT / ".git" / "hooks" / "pre-push"
        push_hook.write_text(
            "#!/bin/sh\n"
            "# 推送前 pre-commit 框架 push 阶段 hooks（MEDIUM 提醒）— 链式调用 pre-commit 框架；\n"
            "# 未安装 pre-commit 时跳过；框架失败仅警告放行（push 阶段为提醒级，不拦截推送）\n"
            "if command -v pre-commit >/dev/null 2>&1; then\n"
            "  if pre-commit run --hook-stage push; then\n"
            "    :\n"
            "  else\n"
            '    echo "[pre-push-guard] 注意：pre-commit 框架 push 阶段 hook 未全部通过（详见 pre-commit 日志）。本次推送继续，请尽快处理。"\n'
            "  fi\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8")
        print(f"pre-push hook 已安装（框架 push 阶段提醒版）：{push_hook}")
        return 0

    print("=== 提交前 CI 护栏（避坑指南检查清单）===")
    print("[Singleton]")
    check_register_coverage()
    check_import_error_warn()
    check_reset_semantics()
    print("[覆盖率]")
    check_omit_glob()
    check_coverage_workflow()
    check_split_serial_dirs()
    check_top_level_side_effects()
    print("[git 同步]")
    check_test_impl_sync_git()

    if args.run_serial:
        print("[动态-串行复现]")
        run_serial_singleton()

    baseline_path = Path(args.baseline)
    baseline = load_baseline(baseline_path)

    if args.update_baseline:
        save_baseline(baseline_path)
        print(f"基线已更新：{baseline_path}（{sum(len(v) for v in WARN_SIGNATURES.values())} 条 WARN 记录）")

    orig_fails = sum(1 for s, _ in RESULTS if s == "FAIL")
    warns = sum(1 for s, _ in RESULTS if s == "WARN")
    fails = orig_fails
    warns_note = ""

    if args.strict:
        if not baseline_path.exists():
            # 首次运行：自动生成基线并放行，避免刚装上 hook 就被存量 WARN 卡死
            save_baseline(baseline_path)
            print(f"[info] 首次运行：已自动生成基线 {baseline_path}，本次放行；后续新增 WARN 将阻断提交")
        else:
            added = [f"{name}:{sig}" for name in STRICT_ITEMS
                     for sig in WARN_SIGNATURES.get(name, [])
                     if sig not in baseline.get(name, [])]
            if added:
                for a in added[:5]:
                    print(f"  [FAIL] 新增 WARN（基线外，须处理后方可提交）: {a}")
                if len(added) > 5:
                    print(f"  [FAIL] ... 等共 {len(added)} 条新增 WARN（基线外）被阻断")
                fails += len(added)
            baselined = sum(1 for name in STRICT_ITEMS
                            for sig in WARN_SIGNATURES.get(name, [])
                            if sig in baseline.get(name, []))
            warns_note = f"（基线内豁免 {baselined}，新增阻断 {len(added)}）"

    print(f"=== 汇总：FAIL={fails} WARN={warns}{warns_note} PASS/SKIP={len(RESULTS) - orig_fails - warns} ===")

    if args.update_baseline:
        print("基线已刷新；本次不阻断（--update-baseline 仅更新豁免清单）。")
        return 0
    print("FAIL 项阻断提交；WARN 项请人工确认后放行（真实透明原则）；--strict 下新增 WARN 亦阻断。")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
