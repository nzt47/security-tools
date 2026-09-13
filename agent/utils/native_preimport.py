# -*- coding: utf-8 -*-
"""原生扩展导入顺序固化 —— 进程入口级的崩溃规避（S11-01）

【现象（本模块为什么存在）】
    Windows 上，若 ``pyarrow`` 的原生初始化（加载
    ``pyarrow/lib.cp312-win_amd64.pyd`` → ``pyarrow/arrow.dll``）**发生得太晚**
    —— 即在进程已经加载 torch / onnxruntime / sklearn / pandas 等一批重型原生库
    **之后** —— 会触发 ``0xC0000005 ACCESS_VIOLATION``（进程退出码 -1073741819）。
    进程被系统直接终止：没有 Python 异常、没有 traceback、没有 pytest 汇总行。

【双证据（S10-05 固化，2026-09-13）】
    · Windows 应用程序错误日志：错误模块 = ``site-packages\\pyarrow\\arrow.dll``，
      异常代码 = ``0xc0000005``；
    · faulthandler 当前线程栈（自下而上）::

        agent/orchestrator/lifecycle_manager.py:118  import sentence_transformers
          → sentence_transformers/util/__init__.py:26 → util/retrieval.py:14
          → util/similarity.py:9                     import sklearn
          → sklearn/utils/fixes.py:19                import pandas
          → pandas/compat/__init__.py:28 → compat/pyarrow.py:12  import pyarrow
          → pyarrow/__init__.py:71（加载 .pyd → arrow.dll 原生初始化）
          → ACCESS_VIOLATION

    这是 Windows 上已知的「原生 DLL 加载顺序 / 地址空间」缺陷，**不是**被测功能
    缺陷：同一用例单独跑 47/47 通过、连跑 3 次稳定。

【处置（不易：本模块是**唯一实现**）】
    在进程最干净的时刻，按固定顺序完成同一批原生栈的导入::

        numpy → pyarrow → pandas → sklearn

    该顺序与 ``sklearn.utils.fixes`` 的真实依赖链一致，保证 pyarrow 在任何重型
    原生库之前完成原生初始化；此后 sklearn / pandas 的 ``import pyarrow`` 命中
    ``sys.modules``，原生初始化不再发生，崩溃路径不可达。

    **S11-01 的变更点**：本模块由 ``tests/integration/conftest.py`` 的
    ``_pin_native_import_order()`` **提升**而来（同一份实现，不是第二份）。
    定位从「测试收集期」升级为「进程入口期」，调用点三处共用：
      · ``app_server.py``                —— 进程入口最前（长寿命生产进程）；
      · ``tests/conftest.py``            —— tests/unit 与 tests/integration 共用
        （S10-05 遗留 #6：unit 历史上也崩过）；
      · ``tests/integration/conftest.py`` —— 保留 S10-05 的原调用位（改为引用本模块）。

    **禁止复制第二份**：``tests/unit/test_native_preimport.py`` 用 AST 机械断言
    调用点走的是本模块，且旧实现的 ``_NATIVE_IMPORT_ORDER`` 字面量不再存在。

【失败姿态（不易：不引入新的硬依赖）】
    任何一步导入失败只记 WARNING 并**继续**，绝不抛异常、绝不阻断启动与测试收集。
    环境缺件应表现为用例失败/跳过或向量检索降级，而不是整个进程起不来
    （守【不易】主链路：规避逻辑不得引入新的硬依赖）。

【可关闭（变易）】
    ``CP_NATIVE_PREIMPORT_ENABLED=0``（亦接受 false/no/off）可整支关闭。
    关闭后行为 = 本模块出现之前的旧行为（该预导入不发生）。未设置 = 开启。
    开关已登记 ``agent/settings/registry.py``（零缺口硬守卫会机械核对读取点）。

【为什么默认开启（口径声明）】
    对 ``tests/**`` 路径：默认开启 = **与现状等价**（S10-05 以来该预导入本就无条件
    执行）。对 ``app_server.py``：默认开启是 S11-01 的**交付目的本身**（此前生产
    进程没有这道保护），设 0 可一键回到"不预导入"的旧行为。此口径已在交付报告
    「口径变更」一节显式声明。
"""

from __future__ import annotations

import importlib
import logging
import os
import sys
import time
from typing import Dict, List, Sequence, Tuple

logger = logging.getLogger(__name__)

#: 固化顺序：numpy → pyarrow → pandas → sklearn
#: 【不易】顺序**不可**随意重排：必须让 pyarrow 先于任何重型原生库完成原生初始化，
#:         且与 sklearn.utils.fixes 的真实依赖链（sklearn→pandas→pyarrow）一致。
NATIVE_IMPORT_ORDER: Tuple[str, ...] = ("numpy", "pyarrow", "pandas", "sklearn")

#: 开关名（唯一读取点见本模块 is_enabled；登记见 agent/settings/registry.py）
_ENV_KEY = "CP_NATIVE_PREIMPORT_ENABLED"

#: 视为"关闭"的取值（与仓库既有 `_env_flag` 口径逐字一致）
_FALSY = ("0", "false", "no", "off", "")

#: 预导入总状态（enabled / disabled:...）——放进结果字典便于一次性上报
STATUS_KEY = "__status__"
STATUS_ENABLED = "enabled"
STATUS_DISABLED = "disabled:%s=0" % _ENV_KEY

#: 每个模块的实际结果：``ok:<耗时>ms`` / ``cached`` / ``failed:<原因>``
#: 【不易】只记录**首次**观测，重复调用不覆盖 —— 否则第二个调用点把真实耗时
#:         覆写成 "cached"，报告里就再也看不到"崩溃路径是否真被规避"（假绿灯）。
PREIMPORT_RESULTS: Dict[str, str] = {}


def is_enabled() -> bool:
    """开关是否开启（未设置 = 开启）。"""
    raw = os.environ.get(_ENV_KEY)
    if raw is None:
        return True
    return raw.strip().lower() not in _FALSY


def pin_native_import_order(extra: Sequence[str] = ()) -> Dict[str, str]:
    """按固定顺序预导入原生栈，使 pyarrow 在进程干净时完成原生初始化。

    Args:
        extra: 追加在固定栈**之后**的模块名（如 ``("sentence_transformers",)``）。
            留空即只跑固定栈；追加项保证仍在 pyarrow 之后导入，不改变固化顺序。

    Returns:
        本进程累计的每模块结果（``PREIMPORT_RESULTS`` 的副本）。

    幂等：同一进程内重复调用只补做"尚未记录过"的模块，已记录项不重跑、不覆写。
    任何一步失败只降级告警，不抛异常（见模块 docstring「失败姿态」）。
    """
    pending: List[str] = [
        name
        for name in tuple(NATIVE_IMPORT_ORDER) + tuple(extra)
        if name not in PREIMPORT_RESULTS
    ]
    if not pending:
        return dict(PREIMPORT_RESULTS)

    if not is_enabled():
        PREIMPORT_RESULTS[STATUS_KEY] = STATUS_DISABLED
        logger.info(
            "[S11-01] 原生扩展预导入已由 %s=0 关闭（回到旧行为）", _ENV_KEY
        )
        return dict(PREIMPORT_RESULTS)

    PREIMPORT_RESULTS[STATUS_KEY] = STATUS_ENABLED
    for _name in pending:
        if _name in sys.modules:
            PREIMPORT_RESULTS[_name] = "cached"
            continue
        _t0 = time.time()
        try:
            importlib.import_module(_name)
        except Exception as _e:  # pragma: no cover - 仅环境缺件时走到
            PREIMPORT_RESULTS[_name] = "failed:%s" % _e
            logger.warning(
                "[S11-01] 原生扩展预导入失败（降级，不阻断启动/收集）: %s: %s",
                _name,
                _e,
            )
        else:
            _elapsed_ms = (time.time() - _t0) * 1000
            PREIMPORT_RESULTS[_name] = "ok:%.1fms" % _elapsed_ms
            logger.debug(
                "[S11-01] 原生扩展预导入 %s 完成（%.1fms）", _name, _elapsed_ms
            )
    return dict(PREIMPORT_RESULTS)


def report_line() -> str:
    """人读单行摘要 —— 供启动日志与 pytest 报告头复用同一口径。

    Why（不可省）：规避逻辑若静默失败，报告里看不出「崩溃路径是否真的被规避」，
    等于假绿灯。此函数把每一步的实际状态（ok/耗时、cached、failed:原因）显式
    打印，使「被检查的对象不会从报告里消失」。
    """
    if not PREIMPORT_RESULTS:
        return "[S11-01] 原生扩展导入顺序固化: （未执行）"
    status = PREIMPORT_RESULTS.get(STATUS_KEY, "")
    if status == STATUS_DISABLED:
        return "[S11-01] 原生扩展导入顺序固化: 已关闭（%s=0）" % _ENV_KEY
    detail = ", ".join(
        "%s=%s" % (k, v) for k, v in PREIMPORT_RESULTS.items() if k != STATUS_KEY
    )
    return "[S11-01] 原生扩展导入顺序固化: %s（顺序 %s）" % (
        detail or "（未执行）",
        " → ".join(NATIVE_IMPORT_ORDER),
    )
