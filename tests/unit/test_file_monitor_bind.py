# -*- coding: utf-8 -*-
"""file_monitor 监听地址回归锁。

【为什么值得一个测试】原实现是 `app.run(host="0.0.0.0")` 硬编码，
而该服务 5 条路由**全部无鉴权**，dashboard 会吐出仓库文件清单、git 作者与提交日期、
覆盖率分布。绑 0.0.0.0 即把这些暴露给同网段任意设备。
这种"默认值"缺陷不会被功能测试发现（功能全都正常），只能靠断言默认值来锁。
"""
from __future__ import annotations

import importlib
import os

import pytest


@pytest.fixture()
def fm(monkeypatch):
    """按需重载模块 —— HOST 在模块导入期从环境变量读取。"""
    def _load(**env):
        for k, v in env.items():
            if v is None:
                monkeypatch.delenv(k, raising=False)
            else:
                monkeypatch.setenv(k, v)
        import file_monitor
        return importlib.reload(file_monitor)
    yield _load
    # 复原，避免污染同进程的其他用例
    os.environ.pop("CP_FILE_MONITOR_HOST", None)
    import file_monitor
    importlib.reload(file_monitor)


def test_default_host_is_loopback(fm):
    m = fm(CP_FILE_MONITOR_HOST=None)
    assert m.HOST == "127.0.0.1", "无鉴权的 dashboard 绝不能默认监听所有网卡"


def test_host_can_be_overridden_explicitly(fm):
    m = fm(CP_FILE_MONITOR_HOST="0.0.0.0")
    assert m.HOST == "0.0.0.0", "确需跨机访问时必须能显式覆盖"


def test_run_uses_module_host_not_hardcoded(fm):
    """锁死 main() 里不能再出现硬编码地址（回归到 host=\"0.0.0.0\" 即失败）。"""
    import inspect
    m = fm(CP_FILE_MONITOR_HOST=None)
    src = inspect.getsource(m.main)
    assert 'host="0.0.0.0"' not in src and "host='0.0.0.0'" not in src
    assert "host=HOST" in src
    assert "port=PORT" in src


def test_routes_are_unauthenticated_documented_risk(fm):
    """把"无鉴权"这一事实固定下来。

    本测试**不是**说无鉴权是对的，而是：只要还有一条路由无鉴权，
    默认监听地址就必须保持回环。日后若加了鉴权，此测试会提醒你同步更新上面的决策注释。
    """
    m = fm(CP_FILE_MONITOR_HOST=None)
    rules = sorted(str(r) for r in m.app.url_map.iter_rules() if r.endpoint != "static")
    assert "/api/dashboard" in rules and "/api/config" in rules
    guarded = [r for r in rules if "auth" in r.lower()]
    assert not guarded, "出现鉴权路由后，请重新评估默认监听地址并更新本测试的说明"
