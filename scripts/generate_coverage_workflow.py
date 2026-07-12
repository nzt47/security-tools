#!/usr/bin/env python3
"""生成带覆盖率数据文件修复的 GitHub Actions workflow。

核心修复：actions/upload-artifact@v4 对隐藏文件（.coverage）上传不稳定，
通过"改名中转"四步法解决：改名上传 → 下载到根目录 → 改名还原 → combine → report。

用法示例：
    python generate_coverage_workflow.py \\
        --package myapp \\
        --tests tests/unit/ \\
        --python 3.10 3.11 3.12 \\
        --threshold 40 \\
        --output .github/workflows/coverage-ci.yml

零依赖，仅需 Python 3.8+ 标准库。
"""

import argparse
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="生成带覆盖率数据文件修复的 GitHub Actions workflow",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--package",
        required=True,
        help="被测包名，须与 pip install -e . 安装的包名一致（如 agent / myapp / src）",
    )
    parser.add_argument(
        "--tests",
        default="tests/",
        help="测试目录路径（默认 tests/）",
    )
    parser.add_argument(
        "--python",
        nargs="+",
        default=["3.10", "3.11", "3.12"],
        metavar="VERSION",
        help="单元测试 Python 版本矩阵（默认 3.10 3.11 3.12）",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=40,
        help="覆盖率阈值百分比，低于则 CI 失败（默认 40）",
    )
    parser.add_argument(
        "--project-name",
        default=None,
        help="workflow 名称前缀（默认从包名推导，如 myapp CI）",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="输出文件路径（默认 .github/workflows/coverage-ci.yml）",
    )
    parser.add_argument(
        "--pytest-args",
        default=None,
        help='pytest 额外参数（默认 "-v --tb=short --cov-report=term-missing -m \\"not slow and not skip_ci\\" --timeout=300"）',
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="输出文件已存在时覆盖",
    )
    return parser.parse_args()


WORKFLOW_TEMPLATE = """# __PROJECT_NAME__
# 自动生成 by scripts/generate_coverage_workflow.py
# 覆盖率数据文件修复：.coverage 隐藏文件改名中转（upload-artifact@v4 兼容）

name: __PROJECT_NAME__

on:
  push:
    branches: [main, master, develop, 'release/**']
  pull_request:
    branches: [main, master, develop]

env:
  PYTHON_VERSION: '__PYTHON_PRIMARY__'
  COVERAGE_THRESHOLD: __THRESHOLD__

jobs:
  # ============================================================================
  # 单元测试 + 覆盖率数据上传
  # ============================================================================
  unit-tests:
    name: 单元测试 (Python ${{ matrix.python-version }})
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        python-version:
__MATRIX__
    steps:
      - name: 检出代码
        uses: actions/checkout@v4

      - name: 设置Python环境
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
          cache: 'pip'

      - name: 安装依赖
        run: |
          python -m pip install --upgrade pip
          pip install pytest pytest-cov pytest-xdist pytest-mock pytest-timeout pytest-asyncio pytest-randomly
          pip install -e .

      - name: 运行单元测试
        run: |
          pytest __TESTS__/ \\
            --cov=__PACKAGE__ \\
            --cov-report=xml \\
            --cov-report=html \\
            __PYTEST_EXTRA__

      # 关键修复 1：把隐藏文件 .coverage 改名为非隐藏文件
      # Why: actions/upload-artifact@v4 对 . 开头的隐藏文件上传不稳定
      - name: 准备覆盖率数据
        run: |
          cp .coverage coverage_raw.data
          ls -la coverage_raw.data coverage.xml

      # 关键修复 2：上传三个产物（原始 SQLite 数据 + XML 报告 + HTML 报告）
      - name: 上传覆盖率报告
        uses: actions/upload-artifact@v4
        with:
          name: coverage-report-py${{ matrix.python-version }}
          path: |
            htmlcov/
            coverage.xml
            coverage_raw.data
          retention-days: 30

      - name: 上传测试结果
        uses: actions/upload-artifact@v4
        if: always()
        with:
          name: test-results-unit-py${{ matrix.python-version }}
          path: test-results/
          retention-days: 30

  # ============================================================================
  # 覆盖率检查（依赖单元测试通过）
  # ============================================================================
  coverage-check:
    name: 覆盖率检查
    runs-on: ubuntu-latest
    needs: [unit-tests]
    steps:
      - name: 检出代码
        uses: actions/checkout@v4

      - name: 设置Python环境
        uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}
          cache: 'pip'

      - name: 安装依赖
        run: |
          python -m pip install --upgrade pip
          pip install pytest pytest-cov pytest-timeout
          pip install -e .

      # 关键修复 3：下载到 path: .（当前目录），让文件直接落在根目录
      - name: 下载覆盖率报告
        uses: actions/download-artifact@v4
        with:
          name: coverage-report-py${{ env.PYTHON_VERSION }}
          path: .

      - name: 检查覆盖率
        run: |
          echo "=== 检查覆盖率 ==="
          ls -la coverage_raw.data coverage.xml || true

          # 关键修复 4：把非隐藏名还原成 .coverage，coverage 命令才能识别
          cp coverage_raw.data .coverage

          # 合并覆盖率数据（多 Python 版本矩阵场景）
          # Why: 单版本时 "No data to combine" 是正常的，|| true 容错
          coverage combine || true

          # 生成覆盖率报告（输出表格供人看）
          coverage report

          # 检查是否达标（阈值断言，低于 N% 则退出码非 0）
          coverage report --fail-under=${{ env.COVERAGE_THRESHOLD }}

      - name: 生成覆盖率报告
        run: |
          coverage html -d test_reports/htmlcov
          coverage xml -o test_reports/coverage.xml

      - name: 上传完整覆盖率报告
        uses: actions/upload-artifact@v4
        with:
          name: full-coverage-report
          path: test_reports/
"""


def render_matrix(versions: list[str]) -> str:
    """渲染 Python 版本矩阵 YAML 列表。"""
    return "\n".join(f"          - '{v}'" for v in versions)


def render_workflow(args: argparse.Namespace) -> str:
    project_name = args.project_name or f"{args.package} CI"
    pytest_extra = args.pytest_args or (
        '-v --tb=short --cov-report=term-missing '
        '-m "not slow and not skip_ci" --timeout=300'
    )

    content = WORKFLOW_TEMPLATE
    content = content.replace("__PROJECT_NAME__", project_name)
    content = content.replace("__PYTHON_PRIMARY__", args.python[0])
    content = content.replace("__THRESHOLD__", str(args.threshold))
    content = content.replace("__MATRIX__", render_matrix(args.python))
    content = content.replace("__TESTS__", args.tests.rstrip("/"))
    content = content.replace("__PACKAGE__", args.package)
    content = content.replace("__PYTEST_EXTRA__", pytest_extra)
    return content


def main() -> int:
    args = parse_args()

    output_path = Path(args.output or ".github/workflows/coverage-ci.yml")

    if output_path.exists() and not args.force:
        print(f"错误: 输出文件已存在: {output_path}", file=sys.stderr)
        print("使用 --force 覆盖，或指定 --output 其他路径", file=sys.stderr)
        return 1

    content = render_workflow(args)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")

    print(f"已生成: {output_path}")
    print()
    print("生成内容摘要:")
    print(f"  项目名:       {args.project_name or f'{args.package} CI'}")
    print(f"  被测包名:     {args.package}")
    print(f"  测试目录:     {args.tests}")
    print(f"  Python 矩阵:  {', '.join(args.python)}")
    print(f"  覆盖率阈值:   {args.threshold}%")
    print()
    print("后续操作:")
    print(f"  1. 检查文件: cat {output_path}")
    print("  2. 确认包名与 setup.py / pyproject.toml 中的 name 一致")
    print("  3. 确认测试目录存在且包含测试文件")
    print("  4. 提交并推送以触发 CI:")
    print(f"     git add {output_path} && git commit -m 'ci: 添加覆盖率检查workflow' && git push")
    print()
    print("如需调整参数重新生成:")
    print(f"  python {sys.argv[0]} --package {args.package} --tests {args.tests} "
          f"--python {' '.join(args.python)} --threshold {args.threshold} --force")

    return 0


if __name__ == "__main__":
    sys.exit(main())
