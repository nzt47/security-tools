"""demos/run_evolution_demo.py 控制台编码「假红」回归守卫（L41）

缺陷：脚本在 cp936/GBK 输出编码下打印含装饰字符（✓ U+2713 / ⊘ U+2298 / ✗ U+2717）的
      批量进化报告时抛 UnicodeEncodeError 并以**非零码退出**，
      把真实结论掩盖成"失败"——本仓库已多次出现的「假红」缺陷类。

实测（同一台中文 Windows，均为子进程退出码）：
  修复前 PYTHONIOENCODING=gbk  → exit=1，栈顶 demos/run_evolution_demo.py:214
                                print(f"      状态:     {status}")
  修复前 PYTHONIOENCODING=utf-8 → exit=0
  修复后 两种编码               → exit=0

判据（可判红）：受控输出编码下子进程退出码必须为 0。修复前该断言实测为 1。

副作用隔离：本 demo 会写 data/evolution_archive*.jsonl（.gitignore 内）。
      本测试用既有配置项 EVOLUTION_ARCHIVE_PATH / EVOLUTION_ARCHIVE_OLD_PATH
      把档案重定向到 tmp_path，并断言重定向确实生效 ⇒ 测试不会污染仓库 data/。

Why 用"重定向是否生效"而不是"仓库文件是否未变"做隔离断言：
      后者会被并行会话（本仓库常有并行任务跑同一 demo）写档案带成假红，
      那正是本缺陷类要消灭的东西。

运行：
    python -m pytest tests/unit/test_run_evolution_demo_encoding_guard.py -q
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEMO = PROJECT_ROOT / "demos" / "run_evolution_demo.py"
PY = sys.executable

# 直接决定 sys.stdout 的编码：gbk 复现 cp936 控制台，utf-8 是既有正常路径
IO_ENCODINGS = ["gbk", "utf-8"]


@pytest.mark.unit
@pytest.mark.p2
@pytest.mark.parametrize("io_encoding", IO_ENCODINGS)
def test_demo_exits_zero_regardless_of_stdout_encoding(tmp_path, io_encoding):
    """受控输出编码下 demo 必须以 0 退出（GBK 亦不得崩）。"""
    active = tmp_path / "evolution_archive.jsonl"
    old = tmp_path / "evolution_archive_old.jsonl"

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = io_encoding
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    env["EVOLUTION_ARCHIVE_PATH"] = str(active)
    env["EVOLUTION_ARCHIVE_OLD_PATH"] = str(old)

    proc = subprocess.run(
        [PY, str(DEMO)],
        cwd=str(PROJECT_ROOT),
        env=env,
        capture_output=True,
        timeout=300,
    )

    assert proc.returncode == 0, (
        "demo 在 PYTHONIOENCODING=%s 下以 %d 退出（控制台编码「假红」复现）\n"
        "stderr 尾部:\n%s"
    ) % (io_encoding, proc.returncode,
         proc.stderr.decode("utf-8", "replace")[-800:])

    # 隔离自证：重定向必须生效，否则本测试的谱系写入会落到仓库 data/
    assert active.exists(), (
        "档案未重定向到 tmp_path（EVOLUTION_ARCHIVE_PATH 未生效）："
        "此状态下 demo 的谱系写入会污染仓库 data/，测试隔离方式必须先修"
    )
    assert "demo-search-optimize" in active.read_text(encoding="utf-8")
