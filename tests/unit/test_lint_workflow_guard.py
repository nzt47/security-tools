#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lint_workflow_guard.py 单元测试。

覆盖目标:
  - 0 反模式场景: 模式 A/B/C 正解 + 非 workflow_run 触发 + 缺信号
  - 1 反模式场景: 三信号(conclusion + exit 1 + != success)同时命中
  - 多反模式 / 解析错误 / 退出码 0/1/2

Why: 守护 lint 脚本判定逻辑不被回归破坏,确保 CI 阻塞门禁可靠。
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

# 让测试能 import scripts/lint_workflow_guard.py(非包模块)
SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import lint_workflow_guard as lint  # noqa: E402


# ── 测试 fixtures ─────────────────────────────────────────────────────
def write_workflow(tmp_path: Path, content: str, name: str = "test.yml") -> Path:
    """写一个 workflow 文件到 tmp_path,返回路径。"""
    p = tmp_path / name
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


# ── workflow 样例 ─────────────────────────────────────────────────────
# 反模式: 三信号同时命中(把"跳过"误标"失败")
ANTI_PATTERN_WORKFLOW = """\
name: Bad Guard
on:
  workflow_run:
    workflows: ["Upstream"]
    types: [completed]
jobs:
  guard:
    runs-on: ubuntu-latest
    if: always()
    steps:
      - name: check
        run: |
          CONCLUSION="${{ github.event.workflow_run.conclusion }}"
          if [[ "${CONCLUSION}" != "success" ]]; then
            exit 1
          fi
"""

# 模式 A 正解: outputs 串联,守卫永不 exit 1
MODE_A_WORKFLOW = """\
name: Good Guard A
on:
  workflow_run:
    workflows: ["Upstream"]
    types: [completed]
jobs:
  guard:
    runs-on: ubuntu-latest
    if: always()
    outputs:
      should_run: ${{ steps.check.outputs.should_run }}
    steps:
      - name: check
        run: |
          CONCLUSION="${{ github.event.workflow_run.conclusion }}"
          if [[ "${CONCLUSION}" == "success" ]]; then
            echo "should_run=true" >> "$GITHUB_OUTPUT"
          else
            echo "should_run=false" >> "$GITHUB_OUTPUT"
          fi
"""

# 模式 B 正解: if 条件跳过(skipped 非 failed)
MODE_B_WORKFLOW = """\
name: Good Guard B
on:
  workflow_run:
    workflows: ["Upstream"]
    types: [completed]
jobs:
  check:
    runs-on: ubuntu-latest
    if: github.event.workflow_run.conclusion == 'success'
    steps:
      - run: echo "run tests"
"""

# 模式 C 正解: 失败通知器,仅在 failure 时触发
MODE_C_WORKFLOW = """\
name: Failure Notifier
on:
  workflow_run:
    workflows: ["Upstream"]
    types: [completed]
jobs:
  notify:
    runs-on: ubuntu-latest
    if: github.event.workflow_run.conclusion == 'failure'
    steps:
      - run: echo "notify failure"
"""

# 非 workflow_run 触发(push),即便 run 块含 exit 1 也不扫描
NON_WORKFLOW_RUN = """\
name: Push CI
on:
  push:
    branches: [master]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: |
          if [[ "${{ some.conclusion }}" != "success" ]]; then
            exit 1
          fi
"""


# ── scan_file 测试 ────────────────────────────────────────────────────
class TestScanFile:
    """scan_file 判定逻辑测试。"""

    def test_anti_pattern_detected(self, tmp_path: Path):
        """1 反模式: 三信号同时命中应被检出,issue 含 job/step 名。"""
        p = write_workflow(tmp_path, ANTI_PATTERN_WORKFLOW)
        issues = lint.scan_file(p)
        assert len(issues) == 1
        kind, job, step = issues[0]
        assert kind == "anti_pattern"
        assert job == "guard"
        assert step == "check"

    def test_mode_a_no_anti_pattern(self, tmp_path: Path):
        """0 反模式: 模式 A(outputs 串联,无 exit 1)不命中。"""
        p = write_workflow(tmp_path, MODE_A_WORKFLOW)
        assert lint.scan_file(p) == []

    def test_mode_b_no_anti_pattern(self, tmp_path: Path):
        """0 反模式: 模式 B(if 条件跳过,conclusion 不在 run 块)不命中。"""
        p = write_workflow(tmp_path, MODE_B_WORKFLOW)
        assert lint.scan_file(p) == []

    def test_mode_c_no_anti_pattern(self, tmp_path: Path):
        """0 反模式: 模式 C(失败通知器,if 限定 failure)不命中。"""
        p = write_workflow(tmp_path, MODE_C_WORKFLOW)
        assert lint.scan_file(p) == []

    def test_non_workflow_run_not_scanned(self, tmp_path: Path):
        """0 反模式: 非 workflow_run 触发直接跳过,即便含 exit 1。"""
        p = write_workflow(tmp_path, NON_WORKFLOW_RUN)
        assert lint.scan_file(p) == []

    def test_missing_exit_1_not_anti_pattern(self, tmp_path: Path):
        """0 反模式: 有 conclusion + != success 但缺 exit 1,三信号缺一不命中。"""
        workflow = """\
name: No Exit
on:
  workflow_run:
    workflows: ["Up"]
    types: [completed]
jobs:
  guard:
    runs-on: ubuntu-latest
    steps:
      - name: check
        run: |
          CONCLUSION="${{ github.event.workflow_run.conclusion }}"
          if [[ "${CONCLUSION}" != "success" ]]; then
            echo "skip"
          fi
"""
        p = write_workflow(tmp_path, workflow)
        assert lint.scan_file(p) == []

    def test_missing_conclusion_ref_not_anti_pattern(self, tmp_path: Path):
        """0 反模式: 有 exit 1 + != success 但缺 workflow_run.conclusion 不命中。"""
        workflow = """\
name: No Conclusion Ref
on:
  workflow_run:
    workflows: ["Up"]
    types: [completed]
jobs:
  guard:
    runs-on: ubuntu-latest
    steps:
      - run: |
          STATUS="bad"
          if [[ "${STATUS}" != "success" ]]; then
            exit 1
          fi
"""
        p = write_workflow(tmp_path, workflow)
        assert lint.scan_file(p) == []

    def test_not_success_variants_all_detected(self, tmp_path: Path):
        """1 反模式: != success 三种写法(无引号/双引号/单引号)均命中。"""
        base = """\
name: Variants
on:
  workflow_run:
    workflows: ["Up"]
    types: [completed]
jobs:
  {job}:
    runs-on: ubuntu-latest
    steps:
      - run: |
          C="${{ github.event.workflow_run.conclusion }}"
          if [[ "$C" {cmp} ]]; then exit 1; fi
"""
        for cmp in ['!= success', '!= "success"', "!= 'success'"]:
            job = "j_" + cmp.replace('"', "q").replace("'", "p").replace(" ", "_")
            p = write_workflow(tmp_path, base.format(job=job, cmp=cmp), name=f"{job}.yml")
            issues = lint.scan_file(p)
            assert len(issues) == 1, f"未检出反模式写法: {cmp}"
            assert issues[0][0] == "anti_pattern"

    def test_multiple_anti_patterns(self, tmp_path: Path):
        """多反模式: 两个 job 各含反模式,检出 2 处。"""
        workflow = """\
name: Multi
on:
  workflow_run:
    workflows: ["Up"]
    types: [completed]
jobs:
  guard1:
    runs-on: ubuntu-latest
    steps:
      - name: s1
        run: |
          C="${{ github.event.workflow_run.conclusion }}"
          if [[ "$C" != "success" ]]; then exit 1; fi
  guard2:
    runs-on: ubuntu-latest
    steps:
      - name: s2
        run: |
          C="${{ github.event.workflow_run.conclusion }}"
          [ "$C" != "success" ] && exit 1
"""
        p = write_workflow(tmp_path, workflow)
        issues = lint.scan_file(p)
        assert len(issues) == 2
        assert {i[1] for i in issues} == {"guard1", "guard2"}

    def test_parse_error(self, tmp_path: Path):
        """解析错误: 非法 YAML 返回 parse_error,不抛异常。"""
        p = tmp_path / "bad.yml"
        p.write_text("name: [unclosed\n  : invalid", encoding="utf-8")
        issues = lint.scan_file(p)
        assert len(issues) == 1
        assert issues[0][0] == "parse_error"

    def test_empty_file_no_error(self, tmp_path: Path):
        """空文件: safe_load 返回 None,返回空 issue 列表。"""
        p = tmp_path / "empty.yml"
        p.write_text("", encoding="utf-8")
        assert lint.scan_file(p) == []

    def test_unnamed_step_uses_placeholder(self, tmp_path: Path):
        """1 反模式: step 无 name 时用 <unnamed> 占位。"""
        workflow = """\
name: Unnamed
on:
  workflow_run:
    workflows: ["Up"]
    types: [completed]
jobs:
  guard:
    runs-on: ubuntu-latest
    steps:
      - run: |
          C="${{ github.event.workflow_run.conclusion }}"
          [ "$C" != "success" ] && exit 1
"""
        p = write_workflow(tmp_path, workflow)
        issues = lint.scan_file(p)
        assert len(issues) == 1
        assert issues[0][2] == "<unnamed>"


# ── main 测试(退出码) ─────────────────────────────────────────────────
class TestMain:
    """main 入口测试,验证退出码契约。"""

    def test_exit_0_clean(self, tmp_path: Path):
        """退出码 0: 干净目录(多个正解 workflow)。"""
        write_workflow(tmp_path, MODE_A_WORKFLOW, "a.yml")
        write_workflow(tmp_path, MODE_B_WORKFLOW, "b.yml")
        write_workflow(tmp_path, MODE_C_WORKFLOW, "c.yml")
        assert lint.main([str(tmp_path)]) == 0

    def test_exit_1_anti_pattern(self, tmp_path: Path):
        """退出码 1: 发现反模式(CI 应阻塞)。"""
        write_workflow(tmp_path, ANTI_PATTERN_WORKFLOW)
        assert lint.main([str(tmp_path)]) == 1

    def test_exit_2_parse_error(self, tmp_path: Path):
        """退出码 2: 解析错误优先于反模式。"""
        (tmp_path / "bad.yml").write_text("name: [unclosed", encoding="utf-8")
        assert lint.main([str(tmp_path)]) == 2

    def test_parse_error_takes_precedence(self, tmp_path: Path):
        """退出码 2: 同时有反模式+解析错误时,解析错误优先(脚本自身问题)。"""
        write_workflow(tmp_path, ANTI_PATTERN_WORKFLOW, "bad.yml")
        (tmp_path / "broken.yml").write_text("name: [unclosed", encoding="utf-8")
        assert lint.main([str(tmp_path)]) == 2

    def test_default_path(self, tmp_path: Path, monkeypatch):
        """默认路径 .github/workflows: 在临时目录下构造干净 workflow。"""
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "clean.yml").write_text(MODE_A_WORKFLOW, encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        assert lint.main([]) == 0

    def test_nonexistent_path_returns_2(self):
        """不存在路径返回 2(脚本自身错误)。"""
        assert lint.main(["/no/such/path/xyz_123"]) == 2

    def test_mixed_clean_and_anti_returns_1(self, tmp_path: Path):
        """退出码 1: 干净+反模式混合,只要存在反模式即阻塞。"""
        write_workflow(tmp_path, MODE_A_WORKFLOW, "clean.yml")
        write_workflow(tmp_path, ANTI_PATTERN_WORKFLOW, "bad.yml")
        assert lint.main([str(tmp_path)]) == 1
