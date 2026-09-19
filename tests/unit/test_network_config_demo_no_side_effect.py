"""回归守护：`tests/test_network_config_integration.py` 导入/收集期不得改写真实配置文件。

背景（2026-09-20 事故，与 2026-08-16 同因复发）：
该文件是**手动演示脚本**（0 个 test 函数），却在模块顶层执行
`NetworkConfigManager().update(...)`，而 pytest 收集 `tests/` 时会 import 它，于是
1. 用例 key `sk-test-1234567890abcdef` 被写进**仓库根真实 `.env`**（`LLM_API_KEY`），
   把部署级真 key 覆盖为占位符 ⇒ 服务重启后 key 校验失败 / 请求 401；
2. `agent/data/network_config.json` 被改写为 `openai/gpt-4` + 空 endpoint
   ⇒ 运行期 `configure_llm` 拿到不支持的模型名而 400。

本用例在**独立子进程**中导入该模块（与 pytest 收集的真实路径一致），断言两处
指纹在导入前后完全不变。若有人把模块级副作用改回来，本用例立即变红。
"""

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_TARGETS = (
    REPO_ROOT / ".env",
    REPO_ROOT / "agent" / "data" / "network_config.json",
)
_IMPORT_MODULE = (
    "import importlib.util\n"
    "spec = importlib.util.spec_from_file_location("
    "'_netcfg_demo', r'{path}')\n"
    "module = importlib.util.module_from_spec(spec)\n"
    "spec.loader.exec_module(module)\n"
)


def _fingerprint(path: Path) -> str | None:
    """返回文件内容指纹；文件不存在时返回 None（CI 无 .env 的情形）。"""
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_importing_demo_script_does_not_touch_real_files():
    """导入演示脚本后，`.env` 与 `network_config.json` 内容必须逐字节不变。"""
    existing = [p for p in _TARGETS if p.exists()]
    if not existing:
        pytest.skip("仓库既无 .env 也无 network_config.json（纯 CI 检出），无需守护")

    before = {p: _fingerprint(p) for p in _TARGETS}

    demo = REPO_ROOT / "tests" / "test_network_config_integration.py"
    proc = subprocess.run(
        [sys.executable, "-c", _IMPORT_MODULE.format(path=demo)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
    )
    # 不校验 returncode：修好后的模块在非 __main__ 导入路径上会主动 skip（退出码非 0 属预期），
    # 本用例只关心「有没有副作用」这一真正不变量。
    assert proc.returncode in (0, 1), (
        f"导入演示脚本异常退出 rc={proc.returncode}\n"
        f"stdout={proc.stdout[-500:]}\nstderr={proc.stderr[-500:]}"
    )

    changed = [
        str(p.relative_to(REPO_ROOT))
        for p in _TARGETS
        if before[p] is not None and _fingerprint(p) != before[p]
    ]
    assert not changed, (
        "导入 tests/test_network_config_integration.py 改写了真实配置文件："
        f"{changed}——该文件不得在模块级执行写操作（见文件头事故说明）"
    )
