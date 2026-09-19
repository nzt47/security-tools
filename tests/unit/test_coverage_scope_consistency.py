#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""覆盖率口径一致性守卫（D1 交付物 · 2026-09-19）

## 为什么需要这个测试

本仓历史上出现过**三个互不可比的覆盖率数字**（49.08% / 77.20% / 门禁 40%），
根因不是"测错了"，而是**口径声明在多处、且互相矛盾**：

| 声明处 | 内容 | 是否生效 |
|---|---|---|
| `pyproject.toml [tool.coverage.run].source` | 9 个包 | ✅ 生效（报告期） |
| `pytest.ini [coverage:run].source` | 6 个包 | ❌ 死配置（coverage 不读 pytest.ini）—— 已删除 |
| `ci.yml` 分片的 `--cov=agent` | 1 个包 | ✅ 生效（**采集期**，最终决定分母） |
| `scripts/run_authoritative_coverage.py --packages` | 单包即可 | ✅ 生效（子集口径，用于快速冒烟） |

关键机制（实测，见 `docs/closeout/COVERAGE_SCOPE_20260919.md`）：
**未执行文件是采集期由 `--cov=` 扫描出来的，报告期不会按 pyproject 的 source 补扫。**
⇒ 只写 `--cov=agent` 时，pyproject 声明的 9 包口径会静默退化成 1 包口径。

本测试把这个"静默退化"变成**红灯**：任何一处口径声明与被门禁约束的口径不一致，
CI 立即失败，而不是等到有人拿两个不可比的数字做结论。

## 断言的边界（诚实声明）

本测试做的是**静态一致性**检查，不是覆盖率数值检查：
- ✅ 能做：`--cov=` 列表 == pyproject `source` 列表；死配置不再复活；口径标签齐全。
- ❌ 不做：不断言覆盖率数值、不跑全量。数值断言由
  `scripts/check_coverage_regression.py` 负责（它带口径一致性硬校验）。
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"
PYTEST_INI = REPO_ROOT / "pytest.ini"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"

#: 权威口径（与 pyproject.toml 的 source 必须逐字一致）。
#: Why 硬编码在测试里：如果测试也从 pyproject 读期望值，那"把 pyproject 改错"
#: 就检测不出来了（自证）。期望值必须是独立写死的一份。
EXPECTED_PACKAGES = [
    "agent",
    "sensor",
    "memory",
    "planning",
    "persona",
    "core",
    "cognitive",
    "lifetrace",
    "utils",
]


def _load_module(name: str, relpath: str):
    """按仓库既有约定从 scripts/ 加载非包模块（见 tests/unit/test_scripts_quality_gate.py）。"""
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _toml() -> dict:
    import tomllib

    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)


class TestPyprojectIsSingleSourceOfTruth:
    """pyproject.toml 是口径的唯一真相源"""

    def test_source_is_exactly_the_nine_expected_packages(self):
        src = _toml()["tool"]["coverage"]["run"]["source"]
        assert src == EXPECTED_PACKAGES, (
            f"覆盖率口径变了：{src}\n"
            f"若这是有意的口径变更，必须同时：① 更新本测试的 EXPECTED_PACKAGES；"
            f"② 更新 .github/workflows/ci.yml 分片的 --cov= 列表；"
            f"③ 重建 coverage_baseline.json；④ 在 docs/closeout/COVERAGE_SCOPE_20260919.md 记录。"
        )

    def test_omit_covers_tests_and_scripts(self):
        omit = _toml()["tool"]["coverage"]["run"]["omit"]
        # Why 必须是 */tests/* 而不是 tests/*：coverage 数据里是绝对路径（CI 上为
        # /home/runner/...），前缀模式匹配不到 —— 2026-08-09 实测 omit 失效（38.02%）。
        assert "*/tests/*" in omit, f"omit 必须含 */tests/*（前缀模式在绝对路径下失效）：{omit}"
        assert "*/scripts/*" in omit

    def test_branch_is_not_enabled(self):
        """分支覆盖当前是关的 —— 这是历史 49.08%/77.20% 的共同口径，不能被悄悄打开。

        Why 单独断言：`pytest.ini` 里那份**已经删掉的**死配置写着 `branch = True`，
        而真正生效的 pyproject 里没有 branch 键。若有人照着那份死配置"修"pyproject，
        分母会变、历史基线全部失效 —— 本断言就是拦这个。
        """
        run_cfg = _toml()["tool"]["coverage"]["run"]
        assert run_cfg.get("branch", False) is False, (
            "分支覆盖被打开了：这会改变 lines-valid 与百分比，历史基线（77.20%）不可比。"
            "若确实要开，必须重建基线并更新本文档。"
        )


class TestDeadConfigStaysDead:
    """pytest.ini 里那三节死配置不得复活"""

    def test_pytest_ini_has_no_coverage_sections(self):
        text = PYTEST_INI.read_text(encoding="utf-8")
        found = re.findall(r"^\[coverage:[^\]]+\]", text, flags=re.MULTILINE)
        assert not found, (
            f"pytest.ini 里又出现了 coverage 配置节：{found}\n"
            "coverage.py 的配置发现顺序是 .coveragerc → .coveragerc.toml → setup.cfg → "
            "tox.ini → pyproject.toml，**不含 pytest.ini** ⇒ 这些节永远不生效，"
            "只会变成第三份互相矛盾的口径声明。请写进 pyproject.toml。"
        )


class TestCiCollectionMatchesDeclaredScope:
    """CI 分片的采集口径必须与 pyproject 声明的口径一致（这是本次 D1 的核心修复）"""

    @staticmethod
    def _shard_cov_args() -> list[str]:
        text = CI_YML.read_text(encoding="utf-8")
        # 只取真正跑 pytest 分片的那一段：以 `--cov-report=xml` 为锚点向前找 --cov=
        anchors = [m.start() for m in re.finditer(r"--cov-report=xml", text)]
        assert anchors, "ci.yml 里找不到 --cov-report=xml 锚点（CI 分片命令结构变了，请更新本测试）"
        block = text[max(0, anchors[0] - 4000): anchors[0]]
        # Why 必须先剔掉注释行：说明性注释里会写 `--cov=agent`（正是在解释"不能只写
        # 这个"），正则若把注释也算进去，就会得到一份永远等于期望值的假列表。
        code_lines = [
            ln for ln in block.splitlines() if not ln.lstrip().startswith("#")
        ]
        return re.findall(r"--cov=([A-Za-z_][\w.]*)", "\n".join(code_lines))

    def test_ci_shards_cover_exactly_the_declared_packages(self):
        covs = self._shard_cov_args()
        assert sorted(covs) == sorted(EXPECTED_PACKAGES), (
            f"CI 分片的 --cov= 列表 = {sorted(covs)}\n"
            f"pyproject 声明的口径 = {sorted(EXPECTED_PACKAGES)}\n"
            "Why 这必须相等：coverage 的未执行文件是**采集期**由 --cov= 扫描出来的，"
            "报告期不会再按 pyproject 的 source 补扫（实测：source=[pkg_a,pkg_b] 时 "
            "`coverage run --source=pkg_a runner.py` 后 report 里 pkg_b 完全消失）。"
            "⇒ 这里缺哪个包，那个包就**根本没进分母**，声明形同虚设。"
        )

    def test_ci_shard_stage_does_not_gate(self):
        """分片阶段必须 --cov-fail-under=0（门槛只在合并后的 coverage-check job）。"""
        text = CI_YML.read_text(encoding="utf-8")
        assert "--cov-fail-under=0" in text, (
            "分片阶段缺少 --cov-fail-under=0：单分片只测约 1/6 代码，设门槛必然误报。"
        )


class TestAuthoritativeRunnerReadsPyproject:
    """权威采集脚本的口径必须来自 pyproject，而不是自己再写一份"""

    def test_read_declared_packages_matches_pyproject(self):
        mod = _load_module("_cov_auth_runner", "scripts/run_authoritative_coverage.py")
        assert mod.read_declared_packages() == EXPECTED_PACKAGES

    def test_chunk_helpers_are_deterministic_and_complete(self):
        mod = _load_module("_cov_auth_runner2", "scripts/run_authoritative_coverage.py")
        files = [f"tests/unit/test_{i:03d}.py" for i in range(23)]
        chunks = [mod.chunk_of(files, 10, i) for i in range(10)]
        merged = sorted(f for c in chunks for f in c)
        assert merged == sorted(files), "分块必须无重无漏（否则覆盖率数据不全）"

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("===== 1 failed, 2 passed in 3.00s =====\n", True),
            ("no tests ran in 0.01s\n", True),
            ("+++++++++++ Timeout +++++++++++\nStack of MainThread\n", False),
            ("", False),
        ],
    )
    def test_chunk_completed_detection(self, tmp_path, text, expected):
        """块完整性判定：只看 rc 会把"被强杀丢了一批文件"误判成"有一批失败"。"""
        mod = _load_module("_cov_auth_runner3", "scripts/run_authoritative_coverage.py")
        log = tmp_path / "chunk.log"
        log.write_text(text, encoding="utf-8")
        ok, _ = mod.chunk_completed(log)
        assert ok is expected


class TestRegressionGateExistsAndGuardsScope:
    """「覆盖率不得下降」断言必须存在，且必须带口径硬校验"""

    def test_module_loads_and_declares_exit_codes(self):
        mod = _load_module("_cov_regression", "scripts/check_coverage_regression.py")
        assert callable(mod.main)
        assert callable(mod.compare_scope)
        assert callable(mod.load_measurement)

    def test_compare_scope_rejects_package_mismatch(self):
        mod = _load_module("_cov_regression2", "scripts/check_coverage_regression.py")
        base = {"packages": EXPECTED_PACKAGES, "branches_valid": 0}
        cur_one_pkg = {"packages": ["agent"], "branches_valid": 0}
        same, why = mod.compare_scope(base, cur_one_pkg)
        assert same is False and "包列表不同" in why, why

    def test_compare_scope_rejects_branch_toggle(self):
        mod = _load_module("_cov_regression3", "scripts/check_coverage_regression.py")
        base = {"packages": EXPECTED_PACKAGES, "branches_valid": 0}
        cur_branch = {"packages": EXPECTED_PACKAGES, "branches_valid": 123}
        same, why = mod.compare_scope(base, cur_branch)
        assert same is False and "分支覆盖" in why, why

    def test_compare_scope_accepts_identical_scope(self):
        mod = _load_module("_cov_regression4", "scripts/check_coverage_regression.py")
        base = {"packages": EXPECTED_PACKAGES, "branches_valid": 0}
        cur = {"packages": list(reversed(EXPECTED_PACKAGES)), "branches_valid": 0}
        same, why = mod.compare_scope(base, cur)
        assert same is True, why
