"""网络配置文件**测试地板**的守卫（2026-10-06）。

【解决什么】agent/network_config.py 的默认路径指向**仓库内**的
agent/data/network_config.json（.gitignore 忽略，CI 检出时不存在）。任何
NetworkConfigManager()（**不带** config_file=）在**读**的时候，只要文件不存在就会**创建**它；
单进程全量跑时这会制造跨用例污染：实测形态是
tests/unit/test_network_config_demo_no_side_effect.py 报「导入 ... 改写了真实配置文件」，
而那个演示脚本**并没有**写它（连续 3 次子进程导入，两处指纹逐字节不变）。

【为什么这次的地板与环境变量绑定】上一轮（PR #1022）用「导入期改进程级模块属性」做地板，
它在 CI 的 Shard 6 **没有生效**：地板自带的 3 条守卫变红、断言实测值仍是仓库路径，
且**没有**留下「未能重定向」的告警 —— 说明至少存在两种候选机制：
  ① 地板那次 import 当场失败（被 except 吞成一条可能不出现在日志里的 warning）；
  ② 地板装上了，但之后模块被重载/重新执行 ⇒ 模块级常量退回默认值。
环境变量 + agent/network_config.py 的**调用期解析**（default_config_file()）对两者**同时免疫**。

⇒ 本文件因此**不只断言结果**（构造落在临时目录），还断言**机制**：
   · 生效路径来自**环境变量**（不是模块常量）；
   · 该解析发生在**调用期**（改环境变量后立刻生效）；
   · 模块被**重新执行**（子进程里 importlib.reload）之后地板仍然成立 ——
     这正是上一轮失效的形态，故单独钉一条。
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.timeout(300)

ROOT = Path(__file__).resolve().parents[2]
ENV_NAME = "CP_NETWORK_CONFIG_FILE"
REPO_FILE = ROOT / "agent" / "data" / "network_config.json"
FLOOR_PREFIX = "pytest_netcfg_floor_"


class Test地板已装上:
    def test_环境变量已指向临时目录(self):
        """conftest 导入期必须把 CP_NETWORK_CONFIG_FILE 指到临时目录。

        【两个失败方向都写出来】
          · 变量缺失 ⇒ 地板根本没装 ⇒ 测试会创建/改写仓库内那份运行期配置；
          · 变量指向仓库内那份 ⇒ 地板装错了方向（等于没装，但更难发现）。
        """
        got = os.environ.get(ENV_NAME)
        assert got, (
            ENV_NAME + " 未设置 ⇒ NETCFG 地板没装上（tests/conftest.py 的导入期块）—— "
            "单测会创建/改写仓库内的 " + str(REPO_FILE)
        )
        assert str(REPO_FILE) != got, "地板把变量指成了仓库内那份文件（等于没装）"
        assert FLOOR_PREFIX in got, "变量不像地板临时目录（实测 " + got + "）—— 地板实现被改坏了"
        assert not str(got).startswith(str(ROOT)), (
            "地板目录落在仓库内（实测 " + got + "）—— 那仍会被 git/扫描器看见，不是隔离"
        )

    def test_生效路径经函数解析而不是模块常量(self):
        """**机制断言**：生效路径由 default_config_file() 解析，模块常量只是默认值。

        这一条是本次修法的核心：上一轮的地板改的是模块常量，而常量是**导入期快照**，
        重载即失效。现在常量与生效路径**解耦**，故重载不再有影响。
        """
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        nc = importlib.import_module("agent.network_config")
        assert str(nc.default_config_file()) == os.environ[ENV_NAME], (
            "default_config_file() 没有按环境变量解析（实测 " + str(nc.default_config_file())
            + "）—— 地板会失效"
        )
        assert str(getattr(nc, "_NETWORK_CONFIG_FILE")) == str(REPO_FILE), (
            "模块常量 _NETWORK_CONFIG_FILE 应当是**默认值**（仓库路径），实测 "
            + str(getattr(nc, "_NETWORK_CONFIG_FILE", None))
            + " —— 若它被改成地板路径，说明又回到「导入期改属性」那条路上去了"
        )

    def test_解析发生在调用期(self, monkeypatch, tmp_path):
        """把变量改到别处 ⇒ **下一次构造**立刻跟随（证明不是导入期快照）。"""
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from agent.network_config import NetworkConfigManager
        target = tmp_path / "another.json"
        monkeypatch.setenv(ENV_NAME, str(target))
        assert NetworkConfigManager()._config_file == target, (
            "改环境变量后新构造的 manager 没有跟随 ⇒ 解析发生在导入期而不是调用期"
        )


class Test构造与落盘:
    def test_不带参数的构造走地板目录(self):
        """最常见的污染形态：NetworkConfigManager()（无参）必须落在地板目录。"""
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from agent.network_config import NetworkConfigManager
        m = NetworkConfigManager()
        assert FLOOR_PREFIX in str(m._config_file), (
            "NetworkConfigManager() 的落盘路径不在地板目录（实测 " + str(m._config_file)
            + "）—— 它会在仓库里创建/改写真实配置文件，污染同分片的其它用例。"
        )

    def test_显式传路径的用法不受影响(self, tmp_path):
        """反向：地板只该改**默认值**，不该干预显式传参（大批用例依赖它）。"""
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from agent.network_config import NetworkConfigManager
        target = tmp_path / "explicit.json"
        m = NetworkConfigManager(config_file=str(target))
        assert str(m._config_file) == str(target), (
            "显式传入的 config_file 被地板改写了（实测 " + str(m._config_file) + "）—— 地板越界了。"
        )

    def test_仓库内那份配置不被测试创建或改写(self):
        """最直接的不变量：读一次默认配置不该动仓库里那份。

        【说明】若该文件**本来就存在**（开发机常见），本用例只断言它**不被本测试改动**，
        不为「它存在」这件事负责 —— 那是操作者自己放的（服务也在写它）。
        """
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        before = REPO_FILE.read_bytes() if REPO_FILE.exists() else None
        from agent.network_config import NetworkConfigManager
        NetworkConfigManager()._load()   # 该模块的读入口（读时若文件缺失会创建）
        after = REPO_FILE.read_bytes() if REPO_FILE.exists() else None
        assert before == after, (
            "调用 NetworkConfigManager()._load() 改动了仓库内的 " + str(REPO_FILE)
            + " —— 地板没拦住，测试正在写真实文件。"
        )


class Test模块被重新执行后地板仍成立:
    """**上一轮失效的那个形态**：模块被重载/重新执行 ⇒ 模块级常量退回默认值。

    【为什么用子进程】在**本进程**里 importlib.reload(agent.network_config) 会换掉类对象，
    可能影响同进程其它用例（本仓有大量"跨用例状态泄漏"的前车之鉴）。
    子进程里做同一件事既忠实又不留副作用 —— 与演示脚本守卫用的是同一个技法。
    """

    def test_reload_之后生效路径仍是地板(self, tmp_path):
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        floor = tmp_path / "reload_floor.json"
        code = (
            "import importlib, os, sys\n"
            "sys.path.insert(0, r'" + str(ROOT) + "')\n"
            "import agent.network_config as nc\n"
            "before = str(nc.default_config_file())\n"
            "importlib.reload(nc)\n"
            "after = str(nc.default_config_file())\n"
            "print('__PROBE__' + before + '|' + after + '|' + str(nc.NetworkConfigManager()._config_file))\n"
        )
        env = dict(os.environ)
        env[ENV_NAME] = str(floor)
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                              timeout=180, env=env)
        line = [ln for ln in proc.stdout.splitlines() if ln.startswith("__PROBE__")]
        assert line, (
            "子进程探针没有回结果 —— stdout=" + proc.stdout[-400:] + " stderr=" + proc.stderr[-400:]
        )
        before, after, constructed = line[-1][len("__PROBE__"):].split("|")
        assert before == str(floor) and after == str(floor), (
            "模块被 reload 之后生效路径变了（reload 前=" + before + "，reload 后=" + after
            + "，期望 " + str(floor) + "）—— 这正是上一轮「导入期改模块属性」失效的形态；"
            "环境变量 + 调用期解析**不该**受 reload 影响。"
        )
        assert constructed == str(floor), (
            "reload 之后构造出来的 manager 落到了 " + constructed + "（期望 " + str(floor)
            + "）⇒ 地板在重载后失效。"
        )
