"""
docker-entrypoint.sh 修复后 case 1) 分支逻辑的等价模拟器与故障注入验证。

【不易】不变量:exit 1 必须有「OUTPUT_FILE 存在 + summary.HIGH > 0」双证据
        才判定为 high_risk_detected;否则一律 exit 3 (E_SCAN_CRASHED)。
【变易】本模拟器用 Python 重现 entrypoint 内 `python3 -c` 解析逻辑,
        与修复后 shell case 1) 分支判定等价(解析函数逐行对照)。
【简易】四类场景覆盖:真 HIGH / 报告缺失(PermissionError) / HIGH=0 / JSON 损坏。

用法:
    python test_entrypoint_logic.py            # 跑全部场景,exit 0=全 PASS
    python test_entrypoint_logic.py --verbose  # 详细输出
"""
import argparse
import json
import os
import sys
import tempfile
from typing import Callable, Optional


# ── 解析函数:逐行对照修复后 entrypoint 的 `python3 -c` 内联代码 ──────────
def parse_high_count(output_file: str) -> int:
    """
    等价于 entrypoint 修复后:
        HIGH_COUNT=$(python3 -c "
        import json
        try:
            d = json.load(open('$OUTPUT_FILE'))
            print(d.get('summary', {}).get('HIGH', 0))
        except Exception:
            print(0)
        " 2>/dev/null || echo "0")
    """
    if not output_file or not os.path.exists(output_file):
        return 0
    try:
        with open(output_file, encoding='utf-8') as f:
            d = json.load(f)
        return int(d.get('summary', {}).get('HIGH', 0))
    except Exception:
        return 0


# ── 判定函数:对照修复后 entrypoint case 1) 分支 ─────────────────────────
def judge_exit_one(output_file: str) -> tuple[int, str, str, int]:
    """
    模拟修复后 case 1) 分支。
    返回 (exit_code, result, reason, high_count)
    - high_count > 0 → (1, 'blocked', 'high_risk_detected', high_count)
    - 否则           → (3, 'error',   'E_SCAN_CRASHED',     0)
    """
    high_count = parse_high_count(output_file)
    # entrypoint: if [ "$HIGH_COUNT" -gt 0 ] 2>/dev/null; then
    if high_count > 0:
        return (1, 'blocked', 'high_risk_detected', high_count)
    else:
        return (3, 'error', 'E_SCAN_CRASHED', 0)


# ── Mock 报告生成 ────────────────────────────────────────────────────────
def make_report(path: str, high: int, medium: int = 0, low: int = 0) -> None:
    """生成结构合法的 mock 扫描报告"""
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    d = {
        'summary': {'HIGH': high, 'MEDIUM': medium, 'LOW': low},
        'findings': [f'finding_{i}' for i in range(high + medium + low)],
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(d, f)


def make_corrupted_report(path: str) -> None:
    """生成损坏的 JSON 文件(模拟报告写入中途崩溃)"""
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write('{"summary": {"HIGH": broken, incomplete')


# ── 场景定义 ─────────────────────────────────────────────────────────────
Scenario = tuple[str, str, Callable[[str], None], tuple[int, str, str]]

SCENARIOS: list[Scenario] = [
    (
        'real_high',
        '真实 HIGH 风险(报告存在 + HIGH=3)→ 应 exit 1 阻断',
        lambda p: make_report(p, high=3, medium=5, low=44),
        (1, 'blocked', 'high_risk_detected'),
    ),
    (
        'no_report_permission_error',
        '故障注入:PermissionError 崩溃(报告文件不存在)→ 应 exit 3,不误判 HIGH',
        lambda p: None,  # 不创建文件
        (3, 'error', 'E_SCAN_CRASHED'),
    ),
    (
        'zero_high_all_low',
        '报告存在但 HIGH=0(全 LOW,即 develop 4db85572 真实情况)→ 应 exit 3',
        lambda p: make_report(p, high=0, medium=0, low=52),
        (3, 'error', 'E_SCAN_CRASHED'),
    ),
    (
        'corrupted_json',
        '边界:报告文件存在但 JSON 损坏 → 应 exit 3(parse 失败兜底 0)',
        lambda p: make_corrupted_report(p),
        (3, 'error', 'E_SCAN_CRASHED'),
    ),
]


def run_scenarios(verbose: bool = False) -> int:
    """运行全部场景,返回失败数"""
    failures = 0
    print('=' * 72)
    print('docker-entrypoint.sh case 1) 修复后逻辑 — 故障注入验证')
    print('=' * 72)
    print()

    for name, desc, setup, expected in SCENARIOS:
        with tempfile.TemporaryDirectory() as td:
            report_path = os.path.join(td, 'kwarg-high-risk-report.json')
            # 执行 setup(可能不创建文件 = 故障注入)
            setup(report_path)
            # no_report 场景:确保文件确实不存在
            if name == 'no_report_permission_error' and os.path.exists(report_path):
                os.remove(report_path)

            actual = judge_exit_one(report_path)
            ok = actual[:3] == expected
            status = 'PASS' if ok else 'FAIL'
            if not ok:
                failures += 1

            print(f'[{status}] 场景: {name}')
            print(f'      描述: {desc}')
            print(f'      报告存在: {os.path.exists(report_path)}')
            if verbose or not ok:
                print(f'      期望: exit={expected[0]} result={expected[1]} reason={expected[2]}')
                print(f'      实际: exit={actual[0]} result={actual[1]} reason={actual[2]} high_count={actual[3]}')
            print()

    total = len(SCENARIOS)
    passed = total - failures
    print('=' * 72)
    print(f'汇总: {passed}/{total} PASS, {failures} FAIL')
    print('=' * 72)
    return failures


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='entrypoint case 1) 逻辑故障注入验证')
    parser.add_argument('--verbose', action='store_true', help='详细输出每个场景的期望/实际值')
    args = parser.parse_args()
    sys.exit(0 if run_scenarios(verbose=args.verbose) == 0 else 1)
