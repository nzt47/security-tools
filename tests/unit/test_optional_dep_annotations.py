# 可选依赖缺失时，模块仍必须可导入（注解求值类 NameError 的回归守护）。
#
# 背景：仓库里有多个『try: import X / except ImportError 只置标志』的可选依赖模式。
# 但如果同一个模块在**函数注解**里直接用 X（例如 -> Optional[X.Response]），
# 那么 def 执行时仍要求值注解 ⇒ 缺依赖时报的是 NameError 而不是 ImportError，
# 连兜底分支都走不到，模块在导入期直接崩。
# 2026-10-03 独立核验扫出并修复了两处真实隐患：
#   sensor/ocr_sensor.py            (np.ndarray)        裸环境 NameError: name 'np' is not defined
#   scripts/observability_post_deploy.py (requests.Response) 裸环境 NameError: name 'requests' is not defined
# 修法统一为 `from __future__ import annotations`（注解字符串化，运行期行为不变）。

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FUTURE_IMPORT = 'from __future__ import annotations'

DEFERRED_ANNOTATION_TARGETS = [
    'sensor/ocr_sensor.py',
    'scripts/observability_post_deploy.py',
]


@pytest.mark.parametrize('rel_path', DEFERRED_ANNOTATION_TARGETS)
def test_module_defers_annotations(rel_path):
    src = (REPO_ROOT / rel_path).read_text(encoding='utf-8')
    assert FUTURE_IMPORT in src, (
        '%s 用可选依赖做函数注解却未延迟求值；缺依赖时会 NameError（见文件头说明）' % rel_path
    )


def _run_blocked(tmp_path, block_name, code):
    blocker = tmp_path / 'blocker'
    blocker.mkdir(exist_ok=True)
    (blocker / ('%s.py' % block_name)).write_text(
        "raise ImportError('blocked by test')\n", encoding='utf-8'
    )
    script = tmp_path / 'probe.py'
    script.write_text(code, encoding='utf-8')
    env = dict(os.environ)
    env['PYTHONPATH'] = str(blocker)
    env['PYTHONUTF8'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
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


def test_ocr_sensor_imports_without_numpy(tmp_path):
    code = (
        'import importlib.util, os, sys\n'
        'sys.path.insert(0, os.getcwd())\n'
        'spec = importlib.util.spec_from_file_location("ocr_probe", "sensor/ocr_sensor.py")\n'
        'mod = importlib.util.module_from_spec(spec)\n'
        'spec.loader.exec_module(mod)\n'
        'assert mod.HAS_NUMPY is False, "屏蔽失效"\n'
        'print("MARK:ocr_ok")\n'
    )
    proc = _run_blocked(tmp_path, 'numpy', code)
    detail = 'STDOUT:\n%s\nSTDERR:\n%s' % (proc.stdout, proc.stderr)
    assert proc.returncode == 0, '缺 numpy 时 ocr_sensor 导入失败\n%s' % detail
    assert 'MARK:ocr_ok' in proc.stdout, detail


def test_post_deploy_script_imports_without_requests(tmp_path):
    code = (
        'import importlib.util\n'
        'spec = importlib.util.spec_from_file_location("obs_probe", "scripts/observability_post_deploy.py")\n'
        'mod = importlib.util.module_from_spec(spec)\n'
        'spec.loader.exec_module(mod)\n'
        'assert mod.REQUESTS_AVAILABLE is False, "屏蔽失效"\n'
        'print("MARK:obs_ok")\n'
    )
    proc = _run_blocked(tmp_path, 'requests', code)
    detail = 'STDOUT:\n%s\nSTDERR:\n%s' % (proc.stdout, proc.stderr)
    assert proc.returncode == 0, '缺 requests 时部署后验证脚本导入失败\n%s' % detail
    assert 'MARK:obs_ok' in proc.stdout, detail
