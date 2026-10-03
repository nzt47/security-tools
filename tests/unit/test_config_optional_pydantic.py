# pydantic 缺席时 config 模块必须仍可导入（CI 运行 37081484445 回归）。
#
# 为什么单独立一个文件：
#   2026-10-03 CI 作业 'Reranker Hot Reload & Log Verification'（只装 pytest、无项目依赖）
#   49 个用例在 setup 阶段全部 ERROR，根因是 config.py 在 pydantic 缺席时**导入期即崩**：
#       config.py:53  class LLMConfig(BaseModel) -> NameError: name 'BaseModel' is not defined
#   触发链：tests/unit/conftest.py 的自动 fixture patch
#       agent.orchestrator.lifecycle_manager._MEMORY_AVAILABLE
#       -> 该模块顶部 from config import MEMORY_TOKEN_LIMIT_DEFAULT
#       -> import config -> NameError。
#
# 为什么用子进程而不是 monkeypatch：
#   本机装了 pydantic，进程内屏蔽无法验证『模块级导入期』行为；
#   只有在真的 import 不到 pydantic 的独立解释器里跑，才能复现 CI 现场。

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

_CHILD_LINES = [
    'import os',
    'import sys',
    '',
    'class _Blocker:',
    '    def find_spec(self, name, path=None, target=None):',
    "        if name == 'pydantic' or name.startswith('pydantic.'):",
    "            raise ImportError('blocked by test')",
    '        return None',
    '',
    'sys.meta_path.insert(0, _Blocker())',
    "sys.path.insert(0, os.environ['YUNSHU_REPO_ROOT'])",
    '',
    'import config',
    "assert config._PYDANTIC_AVAILABLE is False, 'blocker 失效：pydantic 仍被导入'",
    "print('MARK:import_ok')",
    '',
    "errs = config.validate_config({'memory': {'token_limit': 123}})",
    "assert isinstance(errs, list) and errs, '降级校验没有产出任何问题'",
    "print('MARK:basic_validation_ok')",
    '',
    "for _name in ('MemoryConfig', 'LLMConfig', 'ConfigModel'):",
    "    assert hasattr(config, _name), '缺少占位类: ' + _name",
    'try:',
    '    config.MemoryConfig()',
    'except RuntimeError:',
    "    print('MARK:fail_loud_ok')",
    'else:',
    "    raise AssertionError('占位基类被实例化却没报错（会静默产出假配置）')",
    '',
    'import agent.orchestrator.lifecycle_manager as _lm',
    'assert _lm._MEMORY_TOKEN_LIMIT_DEFAULT == config.MEMORY_TOKEN_LIMIT_DEFAULT',
    "print('MARK:lifecycle_manager_ok')",
]

_CHILD_SCRIPT = '\n'.join(_CHILD_LINES) + '\n'


def _run_child(tmp_path):
    script = tmp_path / 'check_config_without_pydantic.py'
    script.write_text(_CHILD_SCRIPT, encoding='utf-8')
    env = dict(os.environ)
    env['YUNSHU_REPO_ROOT'] = str(REPO_ROOT)
    env['PYTHONUTF8'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    env.pop('PYTHONPATH', None)
    return subprocess.run(
        [sys.executable, str(script)],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=300,
    )


class TestConfigWithoutPydantic:
    def test_config_module_is_importable(self, tmp_path):
        proc = _run_child(tmp_path)
        detail = 'STDOUT:\n%s\nSTDERR:\n%s' % (proc.stdout, proc.stderr)
        assert proc.returncode == 0, '子进程失败（exit=%s）\n%s' % (proc.returncode, detail)
        assert 'MARK:import_ok' in proc.stdout, detail

    def test_falls_back_to_basic_validation(self, tmp_path):
        proc = _run_child(tmp_path)
        assert proc.returncode == 0, 'STDOUT:\n%s\nSTDERR:\n%s' % (proc.stdout, proc.stderr)
        assert 'MARK:basic_validation_ok' in proc.stdout

    def test_placeholder_models_fail_loud(self, tmp_path):
        proc = _run_child(tmp_path)
        assert proc.returncode == 0, 'STDOUT:\n%s\nSTDERR:\n%s' % (proc.stdout, proc.stderr)
        assert 'MARK:fail_loud_ok' in proc.stdout

    def test_lifecycle_manager_import_survives(self, tmp_path):
        """CI 真实触发点：conftest patch 该模块 -> 顶层 import config。"""
        proc = _run_child(tmp_path)
        assert proc.returncode == 0, 'STDOUT:\n%s\nSTDERR:\n%s' % (proc.stdout, proc.stderr)
        assert 'MARK:lifecycle_manager_ok' in proc.stdout


class TestConfigWithPydantic:
    def test_models_are_real_when_pydantic_installed(self):
        pytest.importorskip('pydantic')
        import config

        assert config._PYDANTIC_AVAILABLE is True, '本机应装了 pydantic；此处只作对照'
        model = config.MemoryConfig()
        assert isinstance(model, config.MemoryConfig)
        assert model.token_limit == config.MEMORY_TOKEN_LIMIT_DEFAULT
        # 只断言「token_limit 本身被接受」：缺节告警属另一套规则（见 _basic_validation），
        # 与本文件要守的『pydantic 缺席时模块仍可导入』无关，不在此处耦合。
        errs = config.validate_config({'memory': {'token_limit': config.MEMORY_TOKEN_LIMIT_DEFAULT}})
        assert not [e for e in errs if 'token_limit' in str(e)], errs
