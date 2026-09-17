"""动态工具持久化 —— 让云枢自生成的工具能活过重启

【修复的是什么】
    `lifecycle_manager.py:1005-1012` 一直在调
        `agent.tools.init_dynamic_tools_persistence(path)`
        `agent.tools.load_dynamic_tools()`
    但这两个函数**在 `agent/tools/__init__.py` 里根本不存在**（550 行里 0 个 def），
    于是每次启动都抛 `AttributeError` → 被 `except Exception` 吞成一条 warning。
    后果：`generate_persistent` 明明把工具写进了 `agent/tools/custom/`，
    重启后却再也加载不回来 —— 云枢"自己造的工具"用完即失（评估报告 §4.4c）。

【本模块做什么】
    1. 扫描 `agent/tools/custom/**/*.py`，按文件路径导入并调用其 `register_all()`
    2. 维护一份 JSON 索引（元数据台账），供 UI / 自省查询
    3. 为每个动态工具**补写 `data/tool_definitions/<name>.yaml`**
       —— 没有它，主线装配器会 fail-closed 拒绝该工具（见 agent/lines/assembler.py），
          自生成的工具就等于"注册了但永远轮不到"

【保守默认（不易）】
    自生成工具执行的是 LLM 写的代码，能力边界未知 ⇒ 默认声明为
        plane=act, effect=execute, risk=high
    想放宽必须显式传参。安全侧从严是这里唯一正确的偏向。
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CUSTOM_TOOLS_DIR = os.path.join(_ROOT, "agent", "tools", "custom")
TOOL_DEFS_DIR = os.path.join(_ROOT, "data", "tool_definitions")

_LOCK = threading.RLock()
_index_path: Optional[str] = None
_loaded_modules: Dict[str, str] = {}

#: 自生成工具的保守治理默认值
#: risk 定为 critical（而非 high）的理由：自生成工具的**函数体不受 AST 白名单约束**
#: （白名单只校验顶层语句，见 agent/tools/tool_generator.py:29），生成即拥有完整
#: Python 能力。这与 `generate_tool` 本身的定级一致（data/tool_definitions/
#: generate_tool.yaml: risk: critical），也因此自动落入 needs_approval ⇒ 必审。
DEFAULT_PLANE = "act"
DEFAULT_EFFECT = "execute"
DEFAULT_RISK = "critical"


def init_dynamic_tools_persistence(index_path: Optional[str] = None) -> str:
    """初始化动态工具持久化（设置元数据台账路径）

    Args:
        index_path: JSON 台账路径；缺省 `data/dynamic_tools.json`

    Returns:
        实际使用的路径（调用方据此记日志）
    """
    global _index_path
    path = index_path or os.path.join(_ROOT, "data", "dynamic_tools.json")
    with _LOCK:
        _index_path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
    logger.info("[动态工具] 持久化已初始化: %s", path)
    return path


def _ledger_path() -> str:
    return _index_path or os.path.join(_ROOT, "data", "dynamic_tools.json")


def _read_ledger() -> Dict[str, Any]:
    try:
        with open(_ledger_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {"version": 1, "tools": {}}
    except (OSError, ValueError):
        return {"version": 1, "tools": {}}


def _write_ledger(data: Dict[str, Any]) -> None:
    path = _ledger_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)   # 原子替换


# ════════════════════════════════════════════════════════════
#  治理声明：为动态工具补写 YAML
# ════════════════════════════════════════════════════════════

def ensure_yaml_definition(
    name: str,
    description: str,
    schema: Optional[dict] = None,
    *,
    plane: str = DEFAULT_PLANE,
    effect: str = DEFAULT_EFFECT,
    risk: str = DEFAULT_RISK,
    tags: Optional[List[str]] = None,
    category: str = "extension",
    overwrite: bool = False,
) -> Optional[str]:
    """确保动态工具有 `data/tool_definitions/<name>.yaml`

    【为什么必须】主线装配器对"无 plane/effect 声明"的工具 **fail-closed 拒绝**。
    不写这份 YAML，自生成的工具虽然注册成功，却永远不会被装配进任何主线。

    Returns:
        写入的路径；已存在且 overwrite=False 时返回既有路径；失败返回 None
    """
    os.makedirs(TOOL_DEFS_DIR, exist_ok=True)
    path = os.path.join(TOOL_DEFS_DIR, f"{name}.yaml")
    if os.path.exists(path) and not overwrite:
        return path

    import yaml
    doc = {
        "name": name,
        "category": category,
        "description": (description or "")[:500],
        "deprecated": False,
        "version": "1.0.0",
        "plane": plane,
        "effect": effect,
        "risk": risk,
        "tags": list(tags or ["generated"]),
        "schema": schema or {"type": "object", "properties": {}},
        "examples": [],
    }
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            yaml.safe_dump(doc, f, allow_unicode=True, sort_keys=False)
        os.replace(tmp, path)
        logger.info("[动态工具] 已补写治理声明: %s（plane=%s effect=%s risk=%s）",
                    name, plane, effect, risk)
        return path
    except (OSError, Exception) as e:  # noqa: BLE001
        logger.warning("[动态工具] 治理声明写入失败 %s: %s", name, e)
        return None


# ════════════════════════════════════════════════════════════
#  验证门：生成之后、写入之前，先真的跑一次
# ════════════════════════════════════════════════════════════
# 【为什么需要（docs/工具能力补全路线.md §三 遗留项）】
#   原先 `generate_persistent` 一旦把代码写进 agent/tools/custom/ 就"即时生效"，
#   中间没有任何门：生成的函数哪怕一调用就抛异常，也会被注册、被装配、被模型选中，
#   直到真正调用时才炸。对自主演进的 Agent 来说这是最坏的一类缺陷——
#   它"学会了"一个坏工具却毫无察觉，还会把它带进后续所有推理。
#
# 【门的语义】
#   1) AST 顶层语句安全白名单（复用 tool_generator._validate_tool_code_safety）
#      —— 不通过就**拒绝**（fail-closed：不安全代码不得落盘）
#   2) 在 spawn 子进程里带超时地真正**调用一次**生成的函数
#      —— 抛异常或超时就拒绝（能捕获语法外的运行期错误与死循环）
#   3) 校验器本身不可用 → **放行**并标注原因（fail-open：门坏了不该让功能瘫痪）
#
# 【为什么允许 fail-open】这道门是"质量门"而不是"安全边界"；安全边界在
#   `_validate_tool_code_safety`（顶层语句白名单）与调用期的 tool_gate/审批。
#   门自身出故障时若 fail-closed，会把"生成工具"这个能力整个锁死——那比漏检更严重。
#
# 【这道门**不能**替代什么（边界必须说清，别当成已经安全了）】
#   `_validate_tool_code_safety` 的 docstring 明确写着：**函数体内部的循环不受限制**
#   （只校验顶层语句），且顶层 `import` / `ImportFrom` 是**刻意允许**的
#   （生成工具通常需要 import）。实测确认：顶层 `import os` 与函数体内的 `eval(...)`
#   都能通过校验。
#   而本门的调用探针只判"调用是否抛异常/超时"，看不出函数体里干了什么危险事。
#   ⇒ **一个函数体里做危险操作的生成工具能通过这道门。**
#   覆盖这一层的不是本门，而是治理声明：
#     自生成工具默认 `plane=act / effect=execute / risk=critical`
#     ⇒ `ToolMeta.needs_approval` 为真 ⇒ 调用前需人工确认（见 DEFAULT_RISK 处注释）。
#   换言之：**门的职责是"别把坏掉的工具收进来"，不是"保证收进来的工具安全"。**

_VERIFY_NOTE = "verification"


def verify_generated_tool(
    name: str,
    code: str,
    *,
    sample_args: Optional[Dict[str, Any]] = None,
    timeout_sec: float = 5.0,
    probe_call: bool = True,
) -> tuple[bool, str]:
    """验证一个待持久化的自生成工具是否真的可用

    Args:
        name: 工具函数名（生成代码里定义的函数）
        code: 生成出来的 Python 代码
        sample_args: 可选的调用探针参数；None 表示尝试无参调用
        timeout_sec: 子进程执行超时
        probe_call: 是否真的调用一次（False 则只做安全校验）

    Returns:
        (ok, detail)。ok=False 时 detail 说明原因。
    """
    if not name or not str(name).strip():
        return False, "工具名为空"
    if not code or not str(code).strip():
        return False, "生成代码为空"

    try:
        from agent.tools.tool_generator import (
            _exec_with_timeout,
            _validate_tool_code_safety,
        )
    except Exception as e:  # noqa: BLE001 门不可用 ⇒ fail-open
        return True, f"校验器不可用（{type(e).__name__}），跳过验证（fail-open）"

    # ── ① 语法与函数存在性（**必须最先做**，fail-closed）──
    # 【为什么排在最前】语法错误是确定性损坏，不存在"可能只是校验器的问题"这一说。
    #   若放在安全校验之后，`_validate_tool_code_safety` 自己会因 SyntaxError 抛异常，
    #   于是被 fail-open 分支放行 —— 实测复现过这个顺序缺陷（"语法错误"用例曾被判通过）。
    #   能自己判定的确定性缺陷，不要交给会 fail-open 的第三方校验器去判。
    import ast as _ast
    try:
        tree = _ast.parse(code)
    except SyntaxError as e:
        logger.warning("[动态工具] %s 语法错误，拒绝持久化: %s", name, e)
        return False, f"语法错误: {e}"
    defined = {n.name for n in _ast.walk(tree)
               if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef))}
    if name not in defined:
        return False, f"代码里未定义函数 {name}（已定义: {sorted(defined) or '无'}）"

    # ── ② 安全校验（fail-closed）──
    try:
        safe, why = _validate_tool_code_safety(code)
    except Exception as e:  # noqa: BLE001
        return True, f"安全校验异常（{type(e).__name__}），跳过验证（fail-open）"
    if not safe:
        logger.warning("[动态工具] %s 未通过安全校验，拒绝持久化: %s", name, why)
        return False, f"安全校验未通过: {why}"

    if not probe_call:
        return True, "安全校验通过（未做调用探针）"

    # ── ③ 真正调用一次（在 spawn 子进程里，带超时）──
    probe = code + "\n\n" + f"{name}(**{dict(sample_args or {})!r})\n"
    try:
        ok, err = _exec_with_timeout(probe, timeout_sec)
    except Exception as e:  # noqa: BLE001
        return True, f"探针执行异常（{type(e).__name__}），跳过验证（fail-open）"

    if ok:
        return True, "安全校验 + 调用探针均通过"

    # 无参探针常见的"签名需要参数"不算缺陷：无法自动构造实参，故放行并标注。
    low = str(err or "").lower()
    _needs_args_markers = (
        "required positional argument",
        "missing", "required argument", "unexpected keyword",
        "takes", "positional argument",
    )
    if sample_args is None and any(m in low for m in _needs_args_markers):
        return True, f"函数需要参数，未做调用探针（签名: {str(err)[:120]}）"

    logger.warning("[动态工具] %s 调用探针失败，拒绝持久化: %s", name, str(err)[:200])
    return False, f"调用探针失败: {str(err)[:200]}"

def persist_dynamic_tool(
    name: str,
    description: str = "",
    schema: Optional[dict] = None,
    *,
    source: str = "generated",
    module_path: str = "",
    plane: str = DEFAULT_PLANE,
    effect: str = DEFAULT_EFFECT,
    risk: str = DEFAULT_RISK,
    category: str = "extension",
    code: Optional[str] = None,
    sample_args: Optional[Dict[str, Any]] = None,
    verify: bool = True,
    force: bool = False,
) -> bool:
    """把动态工具登记进台账，并补写治理声明 YAML

    Args:
        code: 生成出来的 Python 代码。给了它才会走**验证门**（见 verify_generated_tool）。
        sample_args: 调用探针参数（可选）
        verify: 是否过验证门（默认开）
        force: 验证失败时是否仍然持久化（默认 False ⇒ 拒绝）

    【不易】验证不通过时**默认拒绝持久化**：不写台账、不写治理 YAML。
            理由见 verify_generated_tool 上方注释——坏工具被静默接纳，
            比没有这个工具危险得多（它会"学会"一个坏工具并带进后续所有推理）。
            要绕过必须显式 force=True，且会记进台账备查。
    """
    verification: Dict[str, Any] = {"ok": True, "detail": "未验证（未提供代码）", "skipped": True}
    if verify and code:
        ok, detail = verify_generated_tool(name, code, sample_args=sample_args)
        verification = {"ok": bool(ok), "detail": detail, "skipped": False}
        if not ok and not force:
            logger.warning("[动态工具] %s 验证未通过，拒绝持久化: %s", name, detail)
            # 仍记一条"被拒"台账，便于审计"它生成过什么但没通过"
            with _LOCK:
                data = _read_ledger()
                data.setdefault("rejected", {})[name] = {
                    "name": name, "description": description,
                    "reason": detail, "rejected_at": time.time(),
                }
                try:
                    _write_ledger(data)
                except OSError:
                    pass
            return False

    with _LOCK:
        data = _read_ledger()
        data.setdefault("tools", {})[name] = {
            "name": name,
            "description": description,
            "source": source,
            "module_path": module_path,
            "plane": plane,
            "effect": effect,
            "risk": risk,
            "category": category,
            "registered_at": time.time(),
            _VERIFY_NOTE: verification,
        }
        try:
            _write_ledger(data)
        except OSError as e:
            logger.warning("[动态工具] 台账写入失败 %s: %s", name, e)
            return False
    ensure_yaml_definition(name, description, schema, plane=plane, effect=effect,
                           risk=risk, category=category)
    return True


def forget_dynamic_tool(name: str) -> bool:
    """从台账移除（不删文件、不注销注册表条目）"""
    with _LOCK:
        data = _read_ledger()
        tools = data.get("tools", {})
        if name not in tools:
            return False
        del tools[name]
        try:
            _write_ledger(data)
        except OSError:
            return False
    return True


def list_persisted() -> List[Dict[str, Any]]:
    """台账里的全部动态工具元数据"""
    return list((_read_ledger().get("tools") or {}).values())


# ════════════════════════════════════════════════════════════
#  加载
# ════════════════════════════════════════════════════════════

def _iter_custom_modules() -> List[str]:
    out: List[str] = []
    if not os.path.isdir(CUSTOM_TOOLS_DIR):
        return out
    for dirpath, _dirnames, filenames in os.walk(CUSTOM_TOOLS_DIR):
        for fn in sorted(filenames):
            if fn.endswith(".py") and not fn.startswith("__"):
                out.append(os.path.join(dirpath, fn))
    return out


def _import_module_from_path(path: str):
    """按文件路径导入（不要求父目录是包）"""
    mod_name = f"yunshu_custom_tool_{os.path.splitext(os.path.basename(path))[0]}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法为 {path} 构造 import spec")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_dynamic_tools() -> int:
    """扫描并加载全部持久化的动态工具

    每个 `agent/tools/custom/**/*.py` 都应导出 `register_all(dl=None)`。
    逐个隔离：**单个工具加载失败不影响其余工具**（守主链路稳定）。

    Returns:
        成功加载的工具数
    """
    with _LOCK:
        files = _iter_custom_modules()
    if not files:
        logger.info("[动态工具] 无持久化工具（%s 为空）", CUSTOM_TOOLS_DIR)
        return 0

    loaded = 0
    newly_registered: List[str] = []
    for path in files:
        name = os.path.splitext(os.path.basename(path))[0]
        try:
            from agent import tools as _treg
            _before = set(_treg._registry.keys())
            module = _import_module_from_path(path)
            register_all = getattr(module, "register_all", None)
            if not callable(register_all):
                logger.warning("[动态工具] %s 未导出 register_all（跳过）", name)
                continue
            register_all()
            _after = set(_treg._registry.keys())
            added = sorted(_after - _before)
            with _LOCK:
                _loaded_modules[name] = path
            loaded += 1
            newly_registered.extend(added)
            logger.info("[动态工具] 已加载: %s（注册 %d 个工具: %s）", name, len(added), added)

            # 用注册表差值反推"这个模块注册了哪些工具"，为每个补治理声明。
            # 【为什么用差值而不是让模块自报】模块文件是 generate_persistent 生成的，
            # 它只写 `register_all`，不带元数据；且历史文件可能已存在，
            # 差值法对"新增/改名"都成立，不需要改生成器。
            for tool_name in added:
                entry = _read_ledger().get("tools", {}).get(tool_name, {})
                ensure_yaml_definition(
                    tool_name,
                    entry.get("description", f"云枢自生成并持久化的工具（来自 {name}）"),
                    plane=entry.get("plane", DEFAULT_PLANE),
                    effect=entry.get("effect", DEFAULT_EFFECT),
                    risk=entry.get("risk", DEFAULT_RISK),
                )
                persist_dynamic_tool(
                    tool_name,
                    entry.get("description", f"云枢自生成并持久化的工具（来自 {name}）"),
                    module_path=path,
                    plane=entry.get("plane", DEFAULT_PLANE),
                    effect=entry.get("effect", DEFAULT_EFFECT),
                    risk=entry.get("risk", DEFAULT_RISK),
                )
        except Exception as e:  # noqa: BLE001  单个工具失败不阻断其余
            logger.warning("[动态工具] 加载失败 %s: %s", name, e)

    # 为台账里有、但缺 YAML 的条目补写治理声明（老数据迁移）
    try:
        for entry in list_persisted():
            n = entry.get("name")
            if n and not os.path.exists(os.path.join(TOOL_DEFS_DIR, f"{n}.yaml")):
                ensure_yaml_definition(
                    n, entry.get("description", ""),
                    plane=entry.get("plane", DEFAULT_PLANE),
                    effect=entry.get("effect", DEFAULT_EFFECT),
                    risk=entry.get("risk", DEFAULT_RISK),
                )
    except Exception as e:  # noqa: BLE001
        logger.debug("[动态工具] 补写治理声明时异常（忽略）: %s", e)

    logger.info("[动态工具] 加载完成: %d/%d", loaded, len(files))
    return loaded


def reset_dynamic_tools_state() -> None:
    """清空本模块的内存状态（测试隔离用；不删文件、不注销工具）"""
    global _index_path
    with _LOCK:
        _index_path = None
        _loaded_modules.clear()


__all__ = [
    "init_dynamic_tools_persistence",
    "load_dynamic_tools",
    "persist_dynamic_tool",
    "forget_dynamic_tool",
    "list_persisted",
    "ensure_yaml_definition",
    "reset_dynamic_tools_state",
    "CUSTOM_TOOLS_DIR",
    "DEFAULT_PLANE",
    "DEFAULT_EFFECT",
    "DEFAULT_RISK",
]
