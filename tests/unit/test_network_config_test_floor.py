"""网络配置**测试地板**守卫（2026-10-05）。

【解决什么】`agent/network_config.py::_NETWORK_CONFIG_FILE` 默认指向仓库内的
`agent/data/network_config.json`（被 .gitignore 忽略）。任何 `NetworkConfigManager()`
（不带 `config_file=`）在**读**时只要文件不存在就会**创建**它。

单进程全量跑时这会制造跨用例污染，实测形态是：

    tests/unit/test_network_config_demo_no_side_effect.py::
      test_importing_demo_script_does_not_touch_real_files
    AssertionError: 导入 ... 改写了真实配置文件：[agent/data/network_config.json]

而那个演示脚本**并没有**写它（实测连续 3 次子进程导入，两处指纹逐字节不变）——
真正写它的是同分片里别处不带 `config_file=` 的构造。该用例只在"目标文件存在"时才跑，
所以它的红/绿取决于"同分片还有谁动过它" ⇒ **它测的不是它想测的那件事**。

【本文件钉什么】`tests/conftest.py` 里那条 **NETCFG-FLOOR**：把默认路径重定向到临时目录，
与本仓既有的 `CP_ENV_FILE` 地板同口径（"重定向真实 I/O，而不是 mock 掉写入"）。
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.timeout(300)

#: 地板临时目录的前缀（与 conftest 中保持一致）。
FLOOR_PREFIX = "pytest_netcfg_floor_"


class Test网络配置地板:
    def test_默认路径已被重定向到临时目录(self):
        """conftest 导入期必须已把默认路径指到临时目录，而不是仓库内那份。

        【失败方向有两个，都要能看出来】
          · 仍是 `agent/data/network_config.json` ⇒ 地板没生效 ⇒ 测试会写真实文件；
          · 路径变了但不像临时目录 ⇒ 地板实现被改坏。
        """
        from agent.network_config import _NETWORK_CONFIG_FILE
        got = str(_NETWORK_CONFIG_FILE)
        norm = got.replace("\\", "/")
        assert "/agent/data/" not in norm, (
            "网络配置默认路径仍指向仓库内的 agent/data/network_config.json（实测 " + got
            + "）—— NETCFG-FLOOR 没生效，测试会创建/改写真实文件。"
        )
        assert FLOOR_PREFIX in got, (
            "默认路径不是地板临时目录（实测 " + got + "）—— 地板实现可能被改坏。"
        )

    def test_不带参数的构造走临时路径(self):
        """最常见的污染形态：`NetworkConfigManager()`（无参）必须落在临时目录。"""
        from agent.network_config import NetworkConfigManager
        m = NetworkConfigManager()
        assert FLOOR_PREFIX in str(m._config_file), (
            "NetworkConfigManager() 的落盘路径不在地板临时目录（实测 " + str(m._config_file)
            + "）—— 它会在仓库里创建/改写真实配置文件，污染同分片的其它用例。"
        )

    def test_显式传路径的用法不受影响(self, tmp_path):
        """反向：地板只该改**默认值**，不该干预显式传参（大批用例依赖它）。"""
        from agent.network_config import NetworkConfigManager
        target = tmp_path / "explicit.json"
        m = NetworkConfigManager(config_file=str(target))
        assert str(m._config_file) == str(target), (
            "显式传入的 config_file 被地板改写了（实测 " + str(m._config_file)
            + "）—— 地板越界了，大量用例会因此写到意料之外的地方。"
        )

    def test_仓库内那份配置不被测试创建(self):
        """最直接的不变量：跑测试不该在仓库里留下这份文件。

        【说明】若该文件**本来就存在**（开发机常见），本用例只断言它**不被本测试改动**，
        不为"它存在"这件事负责 —— 那是操作者自己放的。
        """
        repo_file = Path(__file__).resolve().parents[2] / "agent" / "data" / "network_config.json"
        before = repo_file.read_bytes() if repo_file.exists() else None
        from agent.network_config import NetworkConfigManager
        NetworkConfigManager()._load()   # 该模块的读入口（读时若文件缺失会创建）
        after = repo_file.read_bytes() if repo_file.exists() else None
        assert before == after, (
            "调用 NetworkConfigManager()._load() 改动了仓库内的 " + str(repo_file)
            + "—— 地板没拦住，测试正在写真实文件。"
        )
