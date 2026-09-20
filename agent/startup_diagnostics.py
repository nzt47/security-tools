"""启动期降级诊断 —— 让「降级启动」不再静默（TASK-08 子工作流 D / E1g）

【不易（为什么需要这个模块）】
    `app_server.py` 在末尾注册若干**可选**组件；模块缺失时它**故意不阻断启动**
    （这是正确的：可选组件不该让整机起不来）。但问题在于"降级"这件事**没有出口**：

        try:
            from agent.api_gateway_flask import register_gateway as reg_gateway
            reg_gateway(app)
        except ImportError:
            logger.debug("API 网关适配层未安装（...缺失，跳过）")

    失败被压到 `debug` 级 ⇒ 生产日志（INFO 及以上）里**一行都没有**。
    后果：一台「缺了 API 网关」的机器与一台「一切正常」的机器，
    从外部**完全不可区分** —— 运维只能靠人去记得"这台本该有网关"。
    这正是 E1g 说的「静默失败」的真实形态。

    【重要更正（实测，与任务书描述不符）】任务书依据 `server_health.log`
    称"5 个路由模块静默加载失败"。实测该日志写于 2026-08-28，而 `app_server.py`
    最后修改于 2026-09-20 —— **日志记录的是一个旧版本的启动过程**；按当前源码，
    那 5 个里已有 4 个不复现（模块被删除或改名/改家）。当前唯一仍缺失的是
    `agent/api_gateway_flask`。详见本任务验收报告。

【变易（本模块的两件事）】
    1. **登记**：把每一次启动期降级记进一张表（含模块、用途、功能影响）；
    2. **出报**：启动末尾汇总成**一条结构化 ERROR/WARNING**，并暴露给健康面。
    另外提供 `audit_expected_modules()`：用 AST 从 `app_server.py` 机械提取所有
    「本应能导入」的模块并逐个验证可导入性 —— 这样**将来新增**的任何一处静默
    降级都会被自动发现，而不是靠人记得。

【不变（D4 纪律：本模块绝不允许阻断启动）】
    本模块所有对外函数**都不抛异常**：任何内部错误都吞掉并退化为"无诊断"。
    诊断的价值远低于"服务能起来"，故这里的选择是明确的单向降级。
"""

from __future__ import annotations

import ast
import importlib.util
import threading
from typing import Any, Dict, List, Optional, Tuple

#: 降级记录表。用锁保护：启动期可能有多线程（后台预热线程）同时登记。
_DEGRADATIONS: List[Dict[str, Any]] = []
_LOCK = threading.Lock()


def record_degradation(module: str, *, purpose: str = "", impact: str = "",
                       error: str = "", kind: str = "module_missing") -> None:
    """登记一次启动期降级（**不抛异常**）

    Args:
        module: 涉及的模块/组件（如 `agent.api_gateway_flask`）
        purpose: 该组件**本该**提供什么（人读）
        impact: 缺失导致的**功能影响**（人读；这是排查时最需要的一栏）
        error: 原始错误摘要
        kind: `module_missing`（模块不存在）| `import_error`（导入期报错）|
              `register_error`（已导入但注册失败）
    """
    try:
        with _LOCK:
            # 同一模块重复登记时保留首次（cause 更有信息量），只累加次数
            for item in _DEGRADATIONS:
                if item["module"] == module and item["kind"] == kind:
                    item["count"] += 1
                    return
            _DEGRADATIONS.append({
                "module": module, "kind": kind, "purpose": purpose,
                "impact": impact, "error": error, "count": 1,
            })
    except Exception:  # noqa: BLE001  诊断登记失败绝不影响启动
        pass


def degradations() -> Tuple[Dict[str, Any], ...]:
    """当前降级记录（**副本**；不外泄内部可变对象）"""
    try:
        with _LOCK:
            return tuple(dict(item) for item in _DEGRADATIONS)
    except Exception:  # noqa: BLE001
        return ()


def reset_degradations() -> None:
    """清空记录（**仅供测试**使用；生产不在运行期清理）"""
    try:
        with _LOCK:
            _DEGRADATIONS.clear()
    except Exception:  # noqa: BLE001
        pass


def is_degraded() -> bool:
    """本次启动是否发生过降级（健康面/自检可直接读这一条）"""
    try:
        with _LOCK:
            return bool(_DEGRADATIONS)
    except Exception:  # noqa: BLE001
        return False


def summary() -> Dict[str, Any]:
    """结构化摘要（可安全序列化：只含本模块自建的普通数据）"""
    items = degradations()
    return {
        "degraded": bool(items),
        "count": len(items),
        "modules": [item["module"] for item in items],
        "items": list(items),
    }


def emit_startup_report(logger: Any) -> Dict[str, Any]:
    """把降级汇总成**一条结构化告警**（在启动末尾调用一次）

    【为什么是 WARNING/ERROR 而不是 debug】
        这正是 E1g 的修复点：降级必须在默认日志级别下**可见**。
        有降级 ⇒ ERROR（运维必须知道这台机器少了个能力）；
        无降级 ⇒ INFO（留一条"已核对"的正向证据，便于对比日志）。

    【为什么只记录、不在此处启动告警管理器】
        告警管理器自身的启动在启动期是有成本的、且可能失败。启动链路里再去
        拉起一个子系统，等于把"诊断"变成新的启动失败源 —— 违背 D4。故这里只
        在**已经存在**管理器时才顺带通知，否则只出结构化日志与健康面信号。
    """
    report = summary()
    try:
        if not report["degraded"]:
            logger.info("[启动诊断] 无降级：全部预期模块/组件均正常")
            return report
        detail = "; ".join(
            f"{item['module']}({item['kind']})" for item in report["items"])
        logger.error(
            "[启动诊断] 启动期发生 %d 项降级，服务已继续启动（降级不阻断）：%s",
            report["count"], detail)
        for item in report["items"]:
            logger.error(
                "[启动诊断] 降级明细 module=%s kind=%s 用途=%s 功能影响=%s 原因=%s",
                item["module"], item["kind"], item["purpose"] or "(未登记)",
                item["impact"] or "(未登记)", item["error"] or "(无)")
    except Exception:  # noqa: BLE001  出报失败不得影响启动
        pass
    return report


def audit_expected_modules(app_server_path: Any,
                           ) -> List[Dict[str, str]]:
    """用 AST 从 `app_server.py` 机械提取「本应可导入」的模块并验证

    【为什么用 AST 而不是 try 逐个 import】
        真的去 import 会产生副作用（注册路由、连数据库、改全局状态），
        在审计里执行是不可接受的。`importlib.util.find_spec` 只做定位，
        **不执行模块**，因此是安全的只读检查。

    【它检测什么】`app_server.py` 里每一处 `from agent.X import ...` /
        `import agent.X`。这些正是"若缺失就静默降级"的候选点。
        实测该文件有 30+ 处这类按模块显式 `try/except` 的注册块 ——
        结构上没有任何循环遍历它们（任务书猜想的"遍历路由模块的循环"并不存在），
        所以只有机械提取才能保证不漏。

    Returns:
        缺失模块列表（每项含 module / lineno / 说明）；全部可导入时返回 []。
        任何解析/IO 异常都返回 []（审计失败不得阻断启动）。
    """
    missing: List[Dict[str, str]] = []
    try:
        source = open(app_server_path, "r", encoding="utf-8").read()
        tree = ast.parse(source)
    except Exception:  # noqa: BLE001  解析失败 ⇒ 不出结论
        return missing

    seen: set = set()
    for node in ast.walk(tree):
        targets: List[str] = []
        if isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith("agent"):
                targets.append(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("agent"):
                    targets.append(alias.name)
        for name in targets:
            if name in seen:
                continue
            seen.add(name)
            try:
                found = importlib.util.find_spec(name) is not None
            except Exception:  # noqa: BLE001  父包缺失等 ⇒ 视为不可导入
                found = False
            if not found:
                missing.append({
                    "module": name,
                    "lineno": str(getattr(node, "lineno", 0)),
                    "hint": f"app_server.py:{getattr(node, 'lineno', 0)} 引用了不存在的模块 {name}",
                })
    return missing


def audit_and_record(app_server_path: Any) -> List[Dict[str, str]]:
    """审计 + 把结果登记进降级表（启动末尾一次性调用）"""
    missing = audit_expected_modules(app_server_path)
    for item in missing:
        record_degradation(item["module"], kind="module_missing",
                           purpose="app_server.py 启动期引用的模块",
                           impact="该模块承载的路由/能力整体不可用（线上 404）",
                           error=item["hint"])
    return missing
