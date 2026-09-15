"""防复发守卫：日期平移工具（``tests/_date_shift_plugin.py``）自身不得产生**伪影**

（TASK-S11-07「修残留日期敏感用例」的守卫部分；2026-09-14 建立）

════════════════════════════════════════════════════════════════════════
一、为什么必须有这条（实测误判，不是假想）
════════════════════════════════════════════════════════════════════════
S11-06 用「把今天整体平移 N 天」跑全量，得到 ``54 failed``，并据此把
``tests/unit/test_snapshot_comprehensive.py`` 判为"已证实是真炸弹"
（理由：不平移 92 passed，平移 +1 天 25 failed，+400 天同样失败）。

**该判定是错的。** S11-07 补跑了**缺失的对照**——只替换 ``datetime.date/datetime``、
**不平移任何时间**（``CP_DATE_SHIFT_CONTROL=1``，delta=0）::

    pytest tests/unit/test_snapshot_comprehensive.py -p tests._date_shift_plugin
      shift=0    → 92 passed
      control=1  → 25 failed / 67 passed      ← 一步都没挪，照样崩
      shift=+1   → 25 failed / 67 passed
      shift=+400 → 25 failed / 67 passed

⇒ 那 25 个失败**与"今天是哪天"无关**，是"替换 ``datetime.datetime`` 这个动作本身"
造成的**工具伪影**：``pickle.dumps(真实 datetime 实例)`` 会按名字回查
``datetime.datetime``（``pickle.save_global``），替换后该名字指向子类、而实例的类
仍是真实类 ⇒ ``PicklingError: it's not the same object as datetime.datetime``。
快照用例大量 ``pickle`` 时间戳，于是成片报红。

**教训**：没有 ``delta=0`` 对照的"平移后失败"，只能算**候选**，不能算炸弹。
本文件把该对照固化进机制里，防止再犯。

════════════════════════════════════════════════════════════════════════
二、守卫内容（机制级，跑得很快、不依赖真实时钟、不写盘）
════════════════════════════════════════════════════════════════════════
1. :func:`test_fake_classes_are_pickle_resolvable`
   —— 假类实例必须可 ``pickle`` 往返（这是被修掉的伪影根因）；
2. :func:`test_fake_now_returns_fake_class_instances`
   —— ``now()/today()`` 必须返回**假类**实例（返回"真实实例 + delta"会让真/假类混用，
      重新触发同一类伪影）；
3. :func:`test_fake_classes_impersonate_real_names`
   —— 假类对被测代码透明（``__module__``/``__qualname__``/``repr`` 与真实类逐字一致）
      —— 这也是 pickle 能按名解析的前提；
4. :func:`test_shift_still_actually_shifts`
   —— **正向对照**：工具不得退化成空转（``today()`` 必须真的差 N 天）；
5. :func:`test_alias_imports_are_patched`
   —— ``from datetime import datetime as dt`` 这类**别名导入**必须被替换
      （S11-06 的盲区：只按名字 ``date``/``datetime`` 替换 ⇒ 被测代码未被平移、
      测试被平移 ⇒ 又一次"时间口径不一致"的假失败）；
6. :func:`test_control_switch_is_zero_delta`
   —— ``CP_DATE_SHIFT_CONTROL=1`` 必须把 delta 强制为 0（对照开关的真值约束）。
"""

from __future__ import annotations

import datetime as _dt
import pickle
import sys
import types
from datetime import timedelta

import pytest

from tests import _date_shift_plugin as plugin


@pytest.fixture()
def restore_installed_datetime():
    """临时改写 ``datetime.datetime/date`` 后必须逐字还原（测试隔离）"""
    real_dt, real_date = _dt.datetime, _dt.date
    try:
        yield
    finally:
        _dt.datetime, _dt.date = real_dt, real_date


def test_fake_classes_are_pickle_resolvable(restore_installed_datetime):
    """假类实例必须可 pickle 往返 —— S11-06 的 25 个假失败就是栽在这里

    复刻 ``pickle.dumps(快照)`` 的路径：实例序列化时会按 ``obj.__class__`` 的
    ``__module__``/``__qualname__`` 回查模块属性，并**要求查到的对象就是那个类**。
    """
    fake_date, fake_datetime = plugin._make_fakes(0)
    # 装到 datetime 模块名下，模拟 pytest_configure 的效果（否则名字解析不到假类）
    _dt.datetime, _dt.date = fake_datetime, fake_date
    try:
        for value in (fake_datetime.now(), fake_date.today(),
                      fake_datetime.now() + timedelta(days=400)):
            assert pickle.loads(pickle.dumps(value)) == value
    finally:
        _dt.datetime, _dt.date = plugin._REAL_DATETIME, plugin._REAL_DATE


def test_fake_now_returns_fake_class_instances():
    """``now()/today()`` 必须返回**假类**实例（而不是"真实实例 + timedelta"）

    后者会与已替换的 ``datetime.datetime`` 口径不一致：实例是真实类、模块名却指向
    假类 ⇒ 一旦被 pickle 就崩（这正是需要被守住的回归）。
    """
    fake_date, fake_datetime = plugin._make_fakes(400)
    assert type(fake_datetime.now()) is fake_datetime
    assert type(fake_datetime.utcnow()) is fake_datetime
    assert type(fake_datetime.today()) is fake_datetime
    assert type(fake_date.today()) is fake_date
    # tz-aware 分支也要保持假类 + 保留 tzinfo（否则会把"日期炸弹"换成"时区炸弹"）
    aware = fake_datetime.now(_dt.timezone.utc)
    assert type(aware) is fake_datetime
    assert aware.tzinfo is _dt.timezone.utc


def test_fake_classes_impersonate_real_names():
    """假类对被测代码基本透明：``__module__``/``__qualname__`` 与真实类一致

    ``__module__``/``__qualname__`` 一致是 **pickle 能按名解析的前提**（见
    :func:`test_fake_classes_are_pickle_resolvable`）；
    ``str()``（ISO）逐字一致。

    ⚠️ 已知且**已披露**的差异：CPython 的 ``date.__repr__``/``datetime.__repr__``
    用的是 C 层 ``tp_name``，而 Python 子类的 ``tp_name`` 只有类名、不含模块前缀 ——
    所以 ``repr`` 会少一个 ``datetime.`` 前缀（``date(2026, 9, 14)`` vs
    ``datetime.date(2026, 9, 14)``）。``tp_name`` 无法从 Python 层改，本工具**不**
    伪造 ``__repr__``（伪造反而可能与 CPython 的实现漂移）。该差异只影响"断言 repr
    字符串"的用例，实测全量平移**未出现**由此引起的失败（S11-07 证据）；此处把差异
    **逐字钉住**，一旦将来差异扩大，守卫会立刻报红而不是悄悄漂移。
    """
    fake_date, fake_datetime = plugin._make_fakes(0)
    assert fake_date.__module__ == "datetime" and fake_date.__qualname__ == "date"
    assert fake_datetime.__module__ == "datetime"
    assert fake_datetime.__qualname__ == "datetime"

    real_date = plugin._REAL_DATE(2026, 9, 14)
    real_dt = plugin._REAL_DATETIME(2026, 9, 14, 22, 1, 2, 345678)
    fake_date_v = fake_date(2026, 9, 14)
    fake_dt_v = fake_datetime(2026, 9, 14, 22, 1, 2, 345678)

    # str() / 格式化（生产代码里最常用的口径）必须逐字一致
    assert str(fake_date_v) == str(real_date) == "2026-09-14"
    assert f"{fake_dt_v}" == f"{real_dt}"
    assert fake_date_v.isoformat() == real_date.isoformat()
    # repr() 只允许差一个模块前缀（见 docstring 的已知差异）
    assert repr(fake_date_v) == repr(real_date).replace("datetime.date(", "date(", 1)
    assert repr(fake_dt_v) == repr(real_dt).replace("datetime.datetime(", "datetime(", 1)


def test_shift_still_actually_shifts():
    """**正向对照**：工具不得退化成空转（改伪影不能把检测能力一起改没）"""
    fake_date, _ = plugin._make_fakes(400)
    assert fake_date.today() - plugin._REAL_DATE.today() == timedelta(days=400)
    fake_date_back, _ = plugin._make_fakes(-400)
    assert fake_date_back.today() - plugin._REAL_DATE.today() == timedelta(days=-400)
    fake_date_zero, _ = plugin._make_fakes(0)
    assert fake_date_zero.today() == plugin._REAL_DATE.today()


def test_alias_imports_are_patched():
    """别名导入（``from datetime import datetime as dt``）必须被替换（S11-06 盲区）

    按值判定只作用于本项目 ``agent.*`` 模块 —— 这里用 ``sys.modules`` 里的合成
    ``agent.*`` 模块复刻该场景；调用后**完整回滚**本次新增的替换项，避免污染进程。
    """
    mod = types.ModuleType("agent._probe_alias_imports_for_guard")
    mod.when = plugin._REAL_DATETIME          # 等价于 `from datetime import datetime as when`
    mod.day = plugin._REAL_DATE               # 等价于 `from datetime import date as day`
    sys.modules[mod.__name__] = mod

    fake_date, fake_datetime = plugin._make_fakes(0)
    saved_len = len(plugin._saved)
    try:
        plugin._patch_module_attrs(fake_date, fake_datetime)
        assert mod.when is fake_datetime, "datetime 的别名导入未被替换 ⇒ 被测代码没被平移"
        assert mod.day is fake_date, "date 的别名导入未被替换 ⇒ 被测代码没被平移"
    finally:
        while len(plugin._saved) > saved_len:      # 回滚本次所有替换
            target, name, real = plugin._saved.pop()
            try:
                setattr(target, name, real)
            except Exception:  # noqa: BLE001
                pass
        sys.modules.pop(mod.__name__, None)

    assert mod.when is plugin._REAL_DATETIME and mod.day is plugin._REAL_DATE


def test_control_switch_is_zero_delta():
    """``CP_DATE_SHIFT_CONTROL=1`` ⇒ delta 必须为 0（"只替换、不平移"的对照）"""
    if plugin._CONTROL:
        assert plugin._DELTA_DAYS == 0
        assert plugin._ACTIVE is True          # 对照模式必须仍然替换，否则等于没跑
    else:
        assert plugin._DELTA_DAYS == plugin._SHIFT
        assert plugin._ACTIVE == (plugin._SHIFT != 0)
