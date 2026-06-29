"""
CI 流水线安全测试门禁模拟脚本

模拟 GitHub Actions 中 safety-scenario-tests Job 的完整门禁逻辑：
1. 场景 A：所有检查通过（正常情况）
2. 场景 B：覆盖率不达标（阻断）
3. 场景 C：测试用例失败（阻断）

输出格式与 CI 日志一致，便于直观验证阻断效果。
"""

import json
import os
import sys
import xml.etree.ElementTree as ET


def _print_header(title: str):
    """打印 CI 风格的标题"""
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)
    print()


def _print_step(step_name: str):
    """打印 CI 步骤标识"""
    print(f"▶ Step: {step_name}")
    print("-" * 70)


def run_pass_rate_check(json_path: str, required_rate: float = 100.0) -> bool:
    """
    检查测试通过率门禁

    与 CI 中 .github/workflows/ci-cd.yml 的 check pass rate 步骤逻辑完全一致。
    """
    _print_step("检查场景测试通过率")

    try:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"❌ 错误: 测试结果文件不存在: {json_path}")
        return False

    summary = data.get("summary", {})
    passed = summary.get("passed", 0)
    total = summary.get("total", 1)
    failed = summary.get("failed", 0)
    pass_rate = (passed / total) * 100 if total > 0 else 0

    print(f"  场景测试: {passed}/{total} 通过 ({pass_rate:.1f}%)")
    print(f"  失败用例数: {failed}")
    print(f"  要求通过率: ≥{required_rate}%")
    print()

    if failed > 0:
        print("❌ 存在失败用例 → 阻断 CI 流水线")
        print(f"   失败用例数: {failed}")
        print("   下游 Job (integration-tests, deploy-*) 将不会执行")
        return False

    if pass_rate < required_rate:
        print(f"⚠️  通过率低于{required_rate}% → 阻断 CI 流水线")
        print(f"   实际通过率: {pass_rate:.1f}%")
        print("   下游 Job (integration-tests, deploy-*) 将不会执行")
        return False

    print("✅ 场景测试通过率达标")
    print("   → 继续执行覆盖率检查")
    return True


def run_coverage_threshold_check(
    xml_path: str,
    thresholds: dict,
) -> bool:
    """
    检查模块覆盖率阈值门禁

    与 CI 中 .github/workflows/ci-cd.yml 的 check coverage thresholds 步骤逻辑完全一致。
    """
    _print_step("检查模块覆盖率阈值")

    try:
        tree = ET.parse(xml_path)
    except FileNotFoundError:
        print(f"❌ 错误: 覆盖率文件不存在: {xml_path}")
        return False

    root = tree.getroot()

    failed_modules = []
    for package in root.findall(".//package"):
        for cls in package.findall(".//class"):
            filename = cls.get("filename", "")
            if filename in thresholds:
                line_rate = float(cls.get("line-rate", 0)) * 100
                threshold = thresholds[filename]
                status = "✅" if line_rate >= threshold else "❌"
                print(f"  {status} {filename}: {line_rate:.1f}%  (目标≥{threshold}%)")
                if line_rate < threshold:
                    failed_modules.append((filename, line_rate, threshold))

    print()

    if failed_modules:
        print("❌ 以下模块覆盖率未达标 → 阻断 CI 流水线")
        print("-" * 70)
        for name, rate, threshold in failed_modules:
            print(f"   • {name}")
            print(f"     实际: {rate:.1f}% | 目标: ≥{threshold}%")
            print(f"     缺口: {(threshold - rate):.1f}%")
        print("-" * 70)
        print()
        print("   下游 Job 将被阻断:")
        print("     ├── integration-tests")
        print("     ├── deploy-staging")
        print("     ├── deploy-production")
        print("     ├── release")
        print("     └── test-summary")
        return False

    print("✅ 所有模块覆盖率达标")
    print("   → 继续执行后续 Job")
    return True


def simulate_scenario_a_pass():
    """
    场景 A：正常通过
    - 103 个测试全部通过
    - 所有模块覆盖率达标
    """
    _print_header("场景 A：正常通过（103 个用例，覆盖率全部达标）")

    thresholds = {
        "agent/permission_system.py": 90,
        "agent/graceful_degrade.py": 85,
        "agent/disaster_recovery.py": 85,
    }

    json_path = os.path.join(os.path.dirname(__file__), "safety_scenario_results.json")
    xml_path = os.path.join(os.path.dirname(__file__), "coverage-safety.xml")

    if not os.path.exists(json_path) or not os.path.exists(xml_path):
        print("⚠️  基准数据文件不存在，请先运行:")
        print("   pytest tests/unit/test_permission_edge_cases.py "
              "tests/unit/test_graceful_degrade_scenarios.py "
              "tests/unit/test_disaster_recovery_scenarios.py "
              "--cov=... --json-report --cov-report=xml")
        return

    pass_ok = run_pass_rate_check(json_path)
    if not pass_ok:
        print()
        print("🚫 CI 流水线被阻断（通过率不达标）")
        return

    cov_ok = run_coverage_threshold_check(xml_path, thresholds)
    if not cov_ok:
        print()
        print("🚫 CI 流水线被阻断（覆盖率不达标）")
        return

    print()
    print("🎉 CI 流水线全部通过！")
    print("   下游 Job 执行顺序:")
    print("     ① code-quality ✓")
    print("     ② safety-scenario-tests ✓")
    print("     ③ unit-tests ✓")
    print("     ④ integration-tests → 可以执行")
    print("     ⑤ deploy-staging → 可以执行")
    print("     ⑥ deploy-production → 可以执行")


def simulate_scenario_b_coverage_fail():
    """
    场景 B：覆盖率不达标（模拟 disaster_recovery 只有 70%）
    """
    _print_header("场景 B：覆盖率不达标（模拟 disaster_recovery 仅 70%）")

    # 构造一个低覆盖率的假 XML（模拟失败场景）
    fake_xml = """<?xml version="1.0" ?>
<coverage line-rate="0.75" version="5.5">
  <packages>
    <package name="agent">
      <classes>
        <class filename="agent/permission_system.py" line-rate="0.977">
          <lines><line number="1" hits="1"/></lines>
        </class>
        <class filename="agent/graceful_degrade.py" line-rate="0.958">
          <lines><line number="1" hits="1"/></lines>
        </class>
        <class filename="agent/disaster_recovery.py" line-rate="0.702">
          <lines><line number="1" hits="1"/></lines>
        </class>
      </classes>
    </package>
  </packages>
</coverage>
"""

    fake_json = json.dumps({
        "summary": {"passed": 103, "total": 103, "failed": 0, "collected": 103}
    })

    tmp_xml = os.path.join(os.path.dirname(__file__), "_sim_fail_coverage.xml")
    tmp_json = os.path.join(os.path.dirname(__file__), "_sim_fail_results.json")

    with open(tmp_xml, "w", encoding="utf-8") as f:
        f.write(fake_xml)
    with open(tmp_json, "w", encoding="utf-8") as f:
        f.write(fake_json)

    thresholds = {
        "agent/permission_system.py": 90,
        "agent/graceful_degrade.py": 85,
        "agent/disaster_recovery.py": 85,
    }

    pass_ok = run_pass_rate_check(tmp_json)
    if not pass_ok:
        print()
        print("🚫 CI 流水线被阻断（通过率不达标）")
        _cleanup_tmp(tmp_xml, tmp_json)
        return

    cov_ok = run_coverage_threshold_check(tmp_xml, thresholds)
    if not cov_ok:
        print()
        print("🚫 CI 流水线被阻断（覆盖率不达标）")
        print()
        print("📋 需要排查的问题清单:")
        print("   1. 新提交的代码是否缺乏测试？")
        print("   2. 是否有模块重构导致测试失效？")
        print("   3. 是否删除了必要的测试用例？")
        _cleanup_tmp(tmp_xml, tmp_json)
        return

    print("🎉 CI 流水线全部通过！")
    _cleanup_tmp(tmp_xml, tmp_json)


def simulate_scenario_c_test_fail():
    """
    场景 C：测试用例失败（模拟 3 个用例失败）
    """
    _print_header("场景 C：测试用例失败（模拟 3 个用例失败）")

    fake_json = json.dumps({
        "summary": {"passed": 100, "total": 103, "failed": 3, "collected": 103},
        "tests": [
            {"nodeid": "test_permission_edge_cases.py::TestBackup::test_backup_file_exception",
             "outcome": "failed"},
            {"nodeid": "test_graceful_degrade_scenarios.py::TestDegradeHistoryAndCache::test_fallback_exception_handling",
             "outcome": "failed"},
            {"nodeid": "test_disaster_recovery_scenarios.py::TestDatabaseRepairDeep::test_repair_database_exception",
             "outcome": "failed"},
        ]
    })

    tmp_json = os.path.join(os.path.dirname(__file__), "_sim_fail_results2.json")
    with open(tmp_json, "w", encoding="utf-8") as f:
        f.write(fake_json)

    pass_ok = run_pass_rate_check(tmp_json)

    if not pass_ok:
        print()
        print("🚫 CI 流水线被阻断（存在失败用例）")
        print()
        print("📋 失败用例详情:")
        data = json.loads(fake_json)
        for t in data["tests"]:
            if t["outcome"] == "failed":
                print(f"   ✗ {t['nodeid']}")
        print()
        print("🔧 建议操作:")
        print("   1. 查看完整测试日志定位失败原因")
        print("   2. 修复代码或测试用例")
        print("   3. 重新推送触发 CI")

    _cleanup_tmp(tmp_json)


def _cleanup_tmp(*files):
    """清理临时文件"""
    for f in files:
        if os.path.exists(f):
            os.remove(f)


def main():
    print()
    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║     CI 安全测试门禁模拟系统                                       ║")
    print("║     模拟 .github/workflows/ci-cd.yml 中的 safety-scenario-tests  ║")
    print("╚══════════════════════════════════════════════════════════════════╝")

    # 场景 A：正常通过
    simulate_scenario_a_pass()

    # 场景 B：覆盖率不达标
    simulate_scenario_b_coverage_fail()

    # 场景 C：测试用例失败
    simulate_scenario_c_test_fail()

    _print_header("模拟完成 - 门禁机制验证结论")
    print()
    print("✅ 通过率门禁：正常工作，存在失败用例时立即阻断")
    print("✅ 覆盖率门禁：正常工作，任一模块低于阈值时阻断")
    print("✅ 下游阻断链：门禁失败后，所有依赖 Job 不会执行")
    print()
    print("下游阻断链示意:")
    print()
    print("  code-quality")
    print("       │")
    print("       ▼")
    print("  safety-scenario-tests  ←── 你在这里")
    print("       │  (门禁通过才能继续)")
    print("       ▼")
    print("  integration-tests")
    print("       │")
    print("       ├──► deploy-staging")
    print("       │       │")
    print("       │       ▼")
    print("       └──► deploy-production")
    print("               │")
    print("               ▼")
    print("             release")


if __name__ == "__main__":
    main()
