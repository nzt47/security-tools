"""L2 异步方案切换模拟与一致性验证脚本 [TLM-L3]

用途：
- dry-run 模式（默认）：读取当前文件状态，分析"同步 vs 异步"特征，验证 L2_SCHEME 标记与代码实现是否一致
- --simulate 模式：在内存中模拟切换后的状态，输出切换前后对比报告（不修改任何文件）
- --check 模式：仅检查当前一致性，退出码 0=一致 / 1=不一致（适合 CI 集成）

【不易】不变量：
- 不修改任何源代码文件（dry-run only）
- 标记（L2_SCHEME）必须与实现（read_fragment / _build_l2）一致
- 检测特征基于代码字符串模式匹配，不执行代码

【变易】检测维度：
- test.yml: L2_SCHEME 环境变量值
- markdown_syncer.py: read_fragment 是否含 async/asyncio.to_thread
- context_assembler.py: _build_l2 是否含 asyncio.gather/await asyncio.to_thread

【简易】单文件脚本，无外部依赖（仅标准库），输出结构化报告

运行：
    python scripts/simulate_l2_async_switch.py             # dry-run + 模拟切换
    python scripts/simulate_l2_async_switch.py --check     # 仅检查一致性（CI 用）
    python scripts/simulate_l2_async_switch.py --simulate  # 输出切换前后对比

退出码：
    0 = 当前标记与实现一致
    1 = 当前标记与实现不一致（标记撒谎）
    2 = 文件缺失或解析失败
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

# 项目根目录
ROOT = Path(__file__).resolve().parent.parent

# 关键文件路径
TEST_YML = ROOT / ".github" / "workflows" / "test.yml"
MARKDOWN_SYNCER = ROOT / "agent" / "memory" / "markdown_syncer.py"
CONTEXT_ASSEMBLER = ROOT / "agent" / "memory" / "context_assembler.py"

# 方案标识（必须与 test.yml 中 L2_SCHEME 值一致）
SCHEME_SYNC = "sync-serial-path-cache"
SCHEME_ASYNC = "async-io-to-thread"


# ── 数据模型 ──

@dataclass
class ImplementationState:
    """代码实现状态（基于字符串模式匹配）"""
    read_fragment_is_async: bool = False  # read_fragment 是否为 async 或含 asyncio.to_thread
    build_l2_is_concurrent: bool = False  # _build_l2 是否用 asyncio.gather 并发
    read_fragment_evidence: str = ""      # 证据行
    build_l2_evidence: str = ""           # 证据行

    @property
    def implementation_scheme(self) -> Literal["sync", "async", "unknown"]:
        """根据代码特征推断实现方案"""
        if self.read_fragment_is_async and self.build_l2_is_concurrent:
            return "async"
        if not self.read_fragment_is_async and not self.build_l2_is_concurrent:
            return "sync"
        return "unknown"  # 混合状态（危险）


@dataclass
class CIState:
    """CI 标记状态"""
    l2_scheme: str = ""        # L2_SCHEME 环境变量值
    scheme_echo: str = ""      # echo "方案: ..." 内容
    line_number: int = 0       # L2_SCHEME 所在行号


@dataclass
class ConsistencyReport:
    """一致性报告"""
    ci_state: CIState = field(default_factory=CIState)
    impl_state: ImplementationState = field(default_factory=ImplementationState)
    is_consistent: bool = False
    issues: list[str] = field(default_factory=list)

    def add_issue(self, msg: str) -> None:
        self.issues.append(msg)


# ── 解析器 ──

def parse_ci_state(yml_path: Path) -> CIState:
    """解析 test.yml 中的 L2_SCHEME 标记

    检测点：
    - L2_SCHEME: <value> 环境变量
    - echo "方案: ..." 中文描述
    """
    state = CIState()
    if not yml_path.exists():
        return state

    content = yml_path.read_text(encoding="utf-8", errors="replace")
    lines = content.split("\n")

    # 提取 L2_SCHEME 值
    for i, line in enumerate(lines, start=1):
        # 匹配 "L2_SCHEME: value"（允许引号与空格）
        m = re.match(r"\s*L2_SCHEME:\s*[\"']?([^\"'\s#]+)[\"']?\s*(?:#.*)?$", line)
        if m:
            state.l2_scheme = m.group(1)
            state.line_number = i
            break

    # 提取 echo "方案: ..." 内容
    scheme_match = re.search(r'echo\s+"方案:\s*([^"]+)"', content)
    if scheme_match:
        state.scheme_echo = scheme_match.group(1).strip()

    return state


def parse_implementation_state(syncer_path: Path, assembler_path: Path) -> ImplementationState:
    """解析代码实现状态

    检测特征：
    - read_fragment 异步化：async def read_fragment 或 read_fragment 内含 asyncio.to_thread
    - _build_l2 并发化：含 asyncio.gather 或 await asyncio.to_thread(...read_fragment...)
    """
    state = ImplementationState()

    # ── 检测 read_fragment ──
    if syncer_path.exists():
        content = syncer_path.read_text(encoding="utf-8", errors="replace")
        lines = content.split("\n")

        # 找到 read_fragment 定义行
        for i, line in enumerate(lines):
            # 匹配 def read_fragment 或 async def read_fragment
            m = re.match(r"\s*(async\s+)?def\s+read_fragment\s*\(", line)
            if m:
                is_async_def = m.group(1) is not None
                # 检测方法体内是否含 asyncio.to_thread（向后扫描 50 行）
                body = "\n".join(lines[i:i+50])
                has_to_thread = "asyncio.to_thread" in body and "read_fragment" in body
                if is_async_def or has_to_thread:
                    state.read_fragment_is_async = True
                    if is_async_def:
                        state.read_fragment_evidence = f"L{i+1}: {line.strip()} (async def)"
                    else:
                        state.read_fragment_evidence = f"L{i+1}: 方法体内含 asyncio.to_thread"
                else:
                    state.read_fragment_evidence = f"L{i+1}: {line.strip()} (同步 def)"
                break

    # ── 检测 _build_l2 ──
    if assembler_path.exists():
        content = assembler_path.read_text(encoding="utf-8", errors="replace")
        lines = content.split("\n")

        # 找到 _build_l2 定义行
        for i, line in enumerate(lines):
            m = re.match(r"\s*async\s+def\s+_build_l2\s*\(", line)
            if m:
                # 检测方法体内是否含 asyncio.gather（向后扫描 80 行）
                body = "\n".join(lines[i:i+80])
                has_gather = "asyncio.gather" in body
                has_to_thread = "asyncio.to_thread" in body and "read_fragment" in body
                if has_gather or has_to_thread:
                    state.build_l2_is_concurrent = True
                    if has_gather:
                        state.build_l2_evidence = f"L{i+1}: _build_l2 内含 asyncio.gather"
                    else:
                        state.build_l2_evidence = f"L{i+1}: _build_l2 内含 asyncio.to_thread(read_fragment)"
                else:
                    state.build_l2_evidence = f"L{i+1}: _build_l2 串行 for 循环调用 read_fragment"
                break

    return state


# ── 一致性校验 ──

def check_consistency(ci_state: CIState, impl_state: ImplementationState) -> ConsistencyReport:
    """校验 CI 标记与代码实现的一致性

    一致性规则：
    - L2_SCHEME=sync-serial-path-cache ↔ read_fragment 同步 + _build_l2 串行
    - L2_SCHEME=async-io-to-thread ↔ read_fragment 异步 + _build_l2 并发
    - 混合状态（一个 async 一个 sync）→ 永远不一致
    """
    report = ConsistencyReport(ci_state=ci_state, impl_state=impl_state)
    impl_scheme = impl_state.implementation_scheme

    # 文件缺失检查
    if not ci_state.l2_scheme:
        report.add_issue("test.yml 中未找到 L2_SCHEME 环境变量")
    if impl_scheme == "unknown":
        report.add_issue(
            f"代码实现处于混合状态：read_fragment={'async' if impl_state.read_fragment_is_async else 'sync'}, "
            f"_build_l2={'concurrent' if impl_state.build_l2_is_concurrent else 'serial'}"
        )

    # 一致性矩阵
    expected_impl = {
        SCHEME_SYNC: "sync",
        SCHEME_ASYNC: "async",
    }.get(ci_state.l2_scheme)

    if expected_impl is None:
        report.add_issue(f"L2_SCHEME 值未识别: {ci_state.l2_scheme}（期望: {SCHEME_SYNC} 或 {SCHEME_ASYNC}）")
    elif impl_scheme != "unknown" and expected_impl != impl_scheme:
        report.add_issue(
            f"标记与实现不一致：L2_SCHEME={ci_state.l2_scheme} 暗示实现={expected_impl}, "
            f"但实际实现={impl_scheme}"
        )

    report.is_consistent = len(report.issues) == 0
    return report


# ── 模拟切换 ──

def simulate_switch(
    current_ci: CIState,
    current_impl: ImplementationState,
) -> dict:
    """模拟切换到异步方案后的状态（不修改文件）

    Returns: 切换前后对比字典
    """
    target_scheme = SCHEME_ASYNC if current_ci.l2_scheme == SCHEME_SYNC else SCHEME_SYNC

    # 模拟切换后的 CI 状态
    simulated_ci = CIState(
        l2_scheme=target_scheme,
        scheme_echo=(
            "异步 IO (asyncio.to_thread) + 路径缓存"
            if target_scheme == SCHEME_ASYNC
            else "同步串行 read_fragment + 路径缓存（最优方案）"
        ),
        line_number=current_ci.line_number,
    )

    # 模拟切换后的实现状态
    if target_scheme == SCHEME_ASYNC:
        simulated_impl = ImplementationState(
            read_fragment_is_async=True,
            build_l2_is_concurrent=True,
            read_fragment_evidence="(模拟) async def read_fragment 或 asyncio.to_thread 包装",
            build_l2_evidence="(模拟) await asyncio.gather(*[asyncio.to_thread(read_fragment, key) ...])",
        )
    else:
        simulated_impl = ImplementationState(
            read_fragment_is_async=False,
            build_l2_is_concurrent=False,
            read_fragment_evidence="(模拟) def read_fragment（同步 glob + open）",
            build_l2_evidence="(模拟) for r in vec_results: syncer.read_fragment(key)",
        )

    simulated_report = check_consistency(simulated_ci, simulated_impl)

    return {
        "target_scheme": target_scheme,
        "simulated_ci": simulated_ci,
        "simulated_impl": simulated_impl,
        "simulated_report": simulated_report,
    }


# ── 报告输出 ──

def print_report(report: ConsistencyReport, verbose: bool = True) -> None:
    """打印一致性报告"""
    ci = report.ci_state
    impl = report.impl_state

    print("=" * 72)
    print("【L2 方案标记与实现一致性报告】")
    print("=" * 72)

    print(f"\n── CI 标记（test.yml）──")
    print(f"  L2_SCHEME:    {ci.l2_scheme or '(未找到)'}  (L{ci.line_number})")
    print(f"  方案描述:     {ci.scheme_echo or '(未找到)'}")

    print(f"\n── 代码实现 ──")
    print(f"  read_fragment: {'async' if impl.read_fragment_is_async else 'sync'}")
    print(f"    证据: {impl.read_fragment_evidence or '(未检测到)'}")
    print(f"  _build_l2:     {'concurrent' if impl.build_l2_is_concurrent else 'serial'}")
    print(f"    证据: {impl.build_l2_evidence or '(未检测到)'}")
    print(f"  实现推断:      {impl.implementation_scheme}")

    print(f"\n── 一致性校验 ──")
    status = "✅ 一致" if report.is_consistent else "❌ 不一致"
    print(f"  状态: {status}")
    if report.issues:
        print(f"  问题列表:")
        for i, issue in enumerate(report.issues, 1):
            print(f"    {i}. {issue}")


def print_switch_simulation(
    current: ConsistencyReport,
    simulation: dict,
) -> None:
    """打印切换模拟对比"""
    print("\n" + "=" * 72)
    print("【切换模拟：sync ↔ async】")
    print("=" * 72)

    target = simulation["target_scheme"]
    print(f"\n  目标方案: {target}")
    print(f"\n  切换前:")
    print(f"    L2_SCHEME:         {current.ci_state.l2_scheme}")
    print(f"    read_fragment:     {'async' if current.impl_state.read_fragment_is_async else 'sync'}")
    print(f"    _build_l2:         {'concurrent' if current.impl_state.build_l2_is_concurrent else 'serial'}")

    sim_ci = simulation["simulated_ci"]
    sim_impl = simulation["simulated_impl"]
    sim_report = simulation["simulated_report"]

    print(f"\n  切换后（预期）:")
    print(f"    L2_SCHEME:         {sim_ci.l2_scheme}")
    print(f"    方案描述:          {sim_ci.scheme_echo}")
    print(f"    read_fragment:     {'async' if sim_impl.read_fragment_is_async else 'sync'}")
    print(f"    _build_l2:         {'concurrent' if sim_impl.build_l2_is_concurrent else 'serial'}")

    sim_status = "✅ 一致" if sim_report.is_consistent else "❌ 不一致"
    print(f"\n  切换后一致性: {sim_status}")

    print(f"\n── 切换必改清单（最小集）──")
    print(f"  1. test.yml L{current.ci_state.line_number}: L2_SCHEME: {current.ci_state.l2_scheme} → {sim_ci.l2_scheme}")
    print(f"  2. test.yml: echo \"方案: ...\" → echo \"方案: {sim_ci.scheme_echo}\"")
    print(f"  3. markdown_syncer.py: read_fragment 异步化（async def 或 asyncio.to_thread 包装）")
    print(f"  4. context_assembler.py: _build_l2 改用 asyncio.gather 并发调用")
    print(f"\n  【不易】顺序约束：先改实现（3、4）→ 再改标记（1、2），避免标记撒谎")


# ── 主流程 ──

def main() -> int:
    parser = argparse.ArgumentParser(
        description="L2 异步方案切换模拟与一致性验证（dry-run，不修改任何文件）"
    )
    parser.add_argument(
        "--check", action="store_true",
        help="仅检查当前一致性，退出码 0=一致 / 1=不一致（适合 CI 集成）",
    )
    parser.add_argument(
        "--simulate", action="store_true",
        help="输出切换前后对比模拟报告",
    )
    args = parser.parse_args()

    # 文件存在性检查
    missing = [p for p in [TEST_YML, MARKDOWN_SYNCER, CONTEXT_ASSEMBLER] if not p.exists()]
    if missing:
        print(f"[error] 文件缺失: {missing}", file=sys.stderr)
        return 2

    # 解析当前状态
    ci_state = parse_ci_state(TEST_YML)
    impl_state = parse_implementation_state(MARKDOWN_SYNCER, CONTEXT_ASSEMBLER)
    report = check_consistency(ci_state, impl_state)

    # --check 模式：精简输出，仅退出码
    if args.check and not args.simulate:
        if report.is_consistent:
            print(f"[✓] 一致：L2_SCHEME={ci_state.l2_scheme}, 实现={impl_state.implementation_scheme}")
            return 0
        else:
            print(f"[✗] 不一致:", file=sys.stderr)
            for issue in report.issues:
                print(f"  - {issue}", file=sys.stderr)
            return 1

    # 默认 dry-run + 报告
    print_report(report)

    # 模拟切换
    if args.simulate or not args.check:
        simulation = simulate_switch(ci_state, impl_state)
        print_switch_simulation(report, simulation)

    # 退出码
    return 0 if report.is_consistent else 1


if __name__ == "__main__":
    sys.exit(main())
