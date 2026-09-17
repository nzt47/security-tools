"""代码检查工具（run_lint）— 把"改完自己看一眼"变成一次可调用的检查

【任务定位】
    本仓有 ``.ruff_cache`` / ``.mypy_cache`` / ``pyproject.toml`` 里的 ruff 配置，
    却**没有任何 lint 工具**：``humanize_zh`` 是**文风**检测、``data_format_detect``
    是**数据格式**识别，两者都不是代码检查。于是"改完顺手 lint 一下"只能退化成
    ``shell_execute`` 拼命令行 —— 而 shell 桶限流 0.2/s，在多域请求里会被饿死
    （与 A 档 ``git`` / ``run_tests`` 的处境一致）。见 ``docs/工具能力补全路线.md`` B 档。

【不易】
    1. **ok 的语义是"检查跑起来了"，不是"代码干净"**：
       linter 发现问题是**它的正常工作**（返回码非零），把它当 ``ok=False`` 会让
       调用方分不清"代码有问题"和"工具坏了"。故：跑起来 → ``ok=True`` ＋
       ``clean`` 表示是否零问题 ＋ ``error_count``；跑不起来（未安装/超时/无法启动）
       → ``ok=False`` ＋ ``error``。
    2. **永远带输出尾部**：即使解析不出任何计数（工具版本换了输出格式），
       ``output_tail`` 仍在 —— 调用方还能从原文里读到究竟发生了什么。
       这是本工具"不会静默骗人"的底线（对比：只回 ``error_count: 0`` 而实际是解析失败）。
    3. **argv 列表 + ``shell=False``，永不拼接 shell 字符串**：``path`` 来自模型，
       且一律先解析到项目根目录内（越界拒绝），与 ``run_tests`` / ``apply_patch`` 同一策略。
    4. **优雅降级**：ruff 未安装（本仓 Python 环境就只有 mypy，没有 ruff）不是错误状态，
       而是"换一个工具"或"明确告知未安装"，绝不抛异常、绝不无声通过。

【变易】
    schema 必须是**静态字面量**（``scripts/migrate_tools_to_yaml.py`` 用 AST
    ``literal_eval`` 抽取）：``data/tool_definitions/run_lint.yaml`` 的
    description/schema 与本模块 ``@_tools.register`` 的实参逐字节一致
    （单测 ``test_all_descriptions_match`` / ``test_all_schemas_match`` 守门）。
    超时上限、输出上限、auto 的优先级顺序是这里的常量。

【简易】
    纯标准库（``subprocess`` / ``importlib.util`` / ``re`` / ``sys``）：
    不新增依赖，不写审计，权限闸门用 ``getattr`` 守卫（缺失时 fail-open）。
"""

from __future__ import annotations

import importlib.util
import logging
import os
import re
import subprocess
import sys
import time
from typing import Any, Optional, Tuple

from agent import tools as _tools

logger = logging.getLogger(__name__)

#: 支持的检查器（``auto`` 的优先级即本元组顺序：ruff 快、mypy 慢但查类型）
_TOOLS = ("ruff", "mypy")

#: 超时：默认 180 秒，上限 600 秒（全仓 mypy 可能跑很久，但仍必须有上界）
_TIMEOUT_DEFAULT = 180
_TIMEOUT_CAP = 600

#: 输出截断：默认保留尾部 8000 字符，上限 200000
_OUTPUT_DEFAULT = 8000
_OUTPUT_CAP = 200000

#: ANSI 转义（子进程一般因 NO_COLOR 不出色，仍兜一层）
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

#: ruff / mypy 的公共汇总行：``Found 3 errors in 1 file``
_FOUND_ERRORS_RE = re.compile(r"Found (\d+)\s+errors?\b", re.IGNORECASE)

#: mypy 全绿时的汇总行
_MYPY_SUCCESS_RE = re.compile(r"Success:\s*no issues found", re.IGNORECASE)

#: ruff 的诊断行：``path.py:12:5: F401 `os` imported but unused``
_RUFF_DIAG_RE = re.compile(r"^\S.*?:\d+:\d+:\s", re.MULTILINE)

#: mypy 的诊断行：``path.py:12: error: ...``（捕获严重级别）
_MYPY_DIAG_RE = re.compile(r"^.*?:\d+:\s*(error|warning|note):", re.MULTILINE)

#: 警告计数（两个工具都可能因配置问题打出 ``warning:``）
_WARNING_RE = re.compile(r"\bwarning\b\s*:", re.IGNORECASE)


# ════════════════════════════════════════════════════════════
#  基础工具（路径 / 可用性 / 权限）
# ════════════════════════════════════════════════════════════

def _repo_root() -> str:
    """项目根目录（``agent/tools/lint_tools.py`` → 上溯三级）"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _as_int(value: Any, default: int, low: Optional[int] = None,
            high: Optional[int] = None) -> int:
    """宽松取整（非数字退回默认值；再按上下限夹紧）"""
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    if low is not None:
        result = max(low, result)
    if high is not None:
        result = min(high, result)
    return result


def _is_installed(module: str) -> bool:
    """模块是否可导入（``find_spec`` 失败一律按"未安装"处理）"""
    try:
        return importlib.util.find_spec(module) is not None
    except Exception:  # noqa: BLE001 探测失败按未安装处理（保守）
        return False


def _resolve_in_root(path: str) -> Tuple[Optional[str], Optional[str]]:
    """把 ``path`` 解析到项目根目录内 → ``(绝对路径, 错误)``

    与 ``agent/tools/test_tools.py`` 的 ``_resolve_in_root`` 同一策略：
    ``abspath`` 之后再比前缀，``normcase`` 兼容 Windows 大小写不敏感。
    """
    if not isinstance(path, str) or not path.strip():
        path = "."
    raw = path.strip()
    full = os.path.abspath(raw if os.path.isabs(raw) else os.path.join(_repo_root(), raw))
    root = os.path.abspath(_repo_root())
    full_c, root_c = os.path.normcase(full), os.path.normcase(root)
    if full_c != root_c and not full_c.startswith(root_c + os.sep):
        return None, f"检查路径越出项目根目录，已拒绝: {raw}"
    return full, None


def _permission_denied(dl, action: str, context: str):
    """权限闸门（可选依赖，缺失时放行）

    ``dl`` 未必暴露 ``_permission``，故用 ``getattr`` 守卫；
    校验器自身抛异常时按放行处理（与 ``agent/tools/test_tools.py`` 同一策略）。
    """
    check = getattr(getattr(dl, "_permission", None), "check_action", None)
    if not callable(check):
        return None
    try:
        result = check(action, context)
    except Exception as e:  # noqa: BLE001 校验故障不阻断
        logger.warning("[run_lint] 权限校验异常，按放行处理: %s — %s", action, e)
        return None
    if isinstance(result, dict):
        allowed, reason = result.get("allowed"), result.get("reason", "")
    else:
        allowed, reason = getattr(result, "allowed", None), getattr(result, "reason", "")
    if allowed is False:
        return {"ok": False, "error": f"权限系统拒绝: {reason}", "blocked": True}
    return None


def _build_argv(tool: str, target: str) -> list:
    """构造 argv（**列表**形式，永不拼 shell 字符串）

    - ``ruff``：``python -m ruff check <target>``
    - ``mypy``：``python -m mypy <target>``
    用 ``sys.executable`` 而非裸 ``ruff``/``mypy``：保证跑的是**当前解释器环境**里
    装的那个（本仓同时存在系统 Python 与 ``venv/``，裸命令可能指向另一个）。
    """
    if tool == "ruff":
        return [sys.executable, "-m", "ruff", "check", target]
    return [sys.executable, "-m", "mypy", target]


def _parse_counts(tool: str, output: str) -> Tuple[int, int, str]:
    """从输出里解析 ``(error_count, warning_count, summary)``

    【不易】三级兜底，任何一级都不返回"看起来成功的假 0 而不说明"：
      1. 汇总行 ``Found N errors``（ruff 与 mypy 共有，最可靠）；
      2. mypy 的 ``Success: no issues found`` ⇒ 0；
      3. 逐行数诊断（ruff 的 ``path:line:col:`` / mypy 的 ``path:line: error:``）。
    解析不出时 ``error_count`` 为 0，但 ``summary`` 会取最后一行非空输出 ——
    调用方据此能看出"这是没解析出来"而不是"真的干净"。
    """
    summary = ""
    found = _FOUND_ERRORS_RE.search(output)
    success = _MYPY_SUCCESS_RE.search(output)
    if found:
        errors = int(found.group(1))
        summary = found.group(0).strip()
    elif tool == "mypy" and success:
        errors = 0
        summary = success.group(0).strip()
    elif tool == "mypy":
        severities = _MYPY_DIAG_RE.findall(output)
        errors = sum(1 for s in severities if s.lower() == "error")
        summary = _last_non_empty_line(output)
    else:
        errors = len(_RUFF_DIAG_RE.findall(output))
        summary = _last_non_empty_line(output)

    warnings = len(_WARNING_RE.findall(output))
    return errors, warnings, summary


def _last_non_empty_line(text: str) -> str:
    """最后一行非空输出（摘要兜底）"""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


# ════════════════════════════════════════════════════════════
#  实现体
# ════════════════════════════════════════════════════════════

def run_lint(path: Any = ".", tool: Any = "auto",
             timeout_sec: Any = _TIMEOUT_DEFAULT,
             max_output_chars: Any = _OUTPUT_DEFAULT,
             dl: Any = None) -> dict:
    """``run_lint`` 的实现体（由工具包装；测试与内部链路亦可直调）

    Args:
        path: 要检查的文件/目录（相对项目根目录，默认 ``"."`` 全仓）。
        tool: ``ruff`` / ``mypy`` / ``auto``；``auto`` 按 ``_TOOLS`` 顺序取第一个已安装的。
        timeout_sec: 超时秒数，默认 180，上限 600。
        max_output_chars: ``output_tail`` 保留的字符数，默认 8000。
        dl: 宿主实例；**仅**用于权限闸门（``None`` 时跳过）。

    Returns:
        跑起来了 ``{"ok": True, "tool", "clean", "error_count", "warning_count",
        "summary", "output_tail", ...}``；
        没跑起来 ``{"ok": False, "error": ...}``（工具未安装 / 超时 / 无法启动 / 路径越界）。
        **``ok=True`` 不代表代码干净** —— 干净与否看 ``clean`` 与 ``error_count``。
    """
    full, path_error = _resolve_in_root(path)
    if path_error or not full:
        logger.warning("[run_lint] 路径拒绝: %s", path_error)
        return {"ok": False, "error": path_error or "检查路径非法"}
    if not os.path.exists(full):
        return {"ok": False, "error": f"检查路径不存在: {path}"}

    installed = {name: _is_installed(name) for name in _TOOLS}

    requested = str(tool or "auto").strip().lower()
    if requested not in ("auto",) + _TOOLS:
        logger.warning("[run_lint] tool %r 非法，按 auto 处理", tool)
        requested = "auto"

    if requested == "auto":
        selected = next((name for name in _TOOLS if installed[name]), None)
        if selected is None:
            return {
                "ok": False,
                "error": (f"ruff 与 mypy 均未安装：当前 Python 环境（{sys.executable}）里"
                          "既没有 ruff 也没有 mypy，请先 pip install ruff（或 mypy）"),
                "available": installed,
            }
    else:
        selected = requested
        if not installed[selected]:
            extra = "；也可以改用已安装的 mypy" if installed["mypy"] else ""
            return {
                "ok": False,
                "error": (f"{selected} 未安装：当前 Python 环境（{sys.executable}）里没有 "
                          f"{selected}，请先 pip install {selected}{extra}"),
                "available": installed,
                "hint": f"pip install {selected}",
            }

    timeout_value = _as_int(timeout_sec, _TIMEOUT_DEFAULT, low=1, high=_TIMEOUT_CAP)
    output_limit = _as_int(max_output_chars, _OUTPUT_DEFAULT, low=200, high=_OUTPUT_CAP)

    target = str(path).strip() or "."
    argv = _build_argv(selected, target)

    denied = _permission_denied(dl, "run_lint", f"运行 {selected} 检查: {target}")
    if denied:
        return denied

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["NO_COLOR"] = "1"
    env["PYTHONUNBUFFERED"] = "1"

    started = time.time()
    try:
        proc = subprocess.run(  # noqa: S603 argv 列表 + shell=False
            argv,
            cwd=_repo_root(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_value,
            env=env,
            shell=False,
        )
    except subprocess.TimeoutExpired as e:
        partial = ""
        for chunk in (getattr(e, "stdout", None), getattr(e, "stderr", None)):
            if chunk:
                partial += chunk if isinstance(chunk, str) else chunk.decode("utf-8", "replace")
        partial = _ANSI_RE.sub("", partial)
        return {
            "ok": False,
            "error": f"{selected} 超时（>{timeout_value} 秒）已被终止",
            "tool": selected,
            "timeout_sec": timeout_value,
            "duration_sec": round(time.time() - started, 2),
            "output_tail": partial[-output_limit:],
            "command": argv,
        }
    except OSError as e:
        return {"ok": False, "error": f"无法启动 {selected}: {e}", "tool": selected,
                "command": argv}

    duration = round(time.time() - started, 2)
    raw_output = proc.stdout or ""
    if proc.stderr:
        raw_output += ("\n" if raw_output else "") + proc.stderr
    output = _ANSI_RE.sub("", raw_output)

    error_count, warning_count, summary = _parse_counts(selected, output)
    if not summary:
        summary = _last_non_empty_line(output)

    return {
        "ok": True,                      # 跑起来了（发现问题不算失败，见模块说明【不易】1）
        "tool": selected,
        "clean": error_count == 0,
        "error_count": error_count,
        "warning_count": warning_count,
        "summary": summary,
        "returncode": proc.returncode,
        "duration_sec": duration,
        "command": argv,
        "cwd": _repo_root(),
        "timeout_sec": timeout_value,
        "output_tail": output[-output_limit:],
        "output_truncated": len(output) > output_limit,
    }


# ════════════════════════════════════════════════════════════
#  工具注册
# ════════════════════════════════════════════════════════════

def register_all(dl):
    """注册代码检查工具（``run_lint``）

    Args:
        dl: DigitalLife / LifecycleManager 实例。用于权限闸门（``getattr`` 守卫，
        缺失时 fail-open）；其余不取 dl 的任何属性。
    """

    @_tools.register("run_lint",
        "对代码跑静态检查 / 类型检查（代码检查、lint、静态检查、跑 lint、ruff、mypy、"
        "typecheck、类型检查）。tool=auto 时优先用 ruff（快），未安装则退回 mypy；"
        "两者都不可用时返回明确的错误信息而不是抛异常。"
        "解析输出给出 error_count / warning_count / summary，并且**总是**附带截断后的"
        "输出尾部 output_tail（即使计数解析失败也能看到原文）。"
        "ok=True 只表示检查**跑起来了**，代码是否干净看 clean 字段与 error_count —— "
        "非零退出码意味着发现了问题，不算工具失败。"
        "Run a linter / static checker on code, run ruff check or mypy, typecheck",
        schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "要检查的文件或目录（相对项目根目录），默认 \".\" 检查整个仓库",
                },
                "tool": {
                    "type": "string",
                    "enum": ["ruff", "mypy", "auto"],
                    "description": "检查器：ruff 快（风格+常见错误）/ mypy 查类型 / auto 自动选择，默认 auto",
                },
                "timeout_sec": {
                    "type": "integer",
                    "description": "超时秒数，默认 180，最大 600",
                },
                "max_output_chars": {
                    "type": "integer",
                    "description": "输出尾部保留的字符数，默认 8000",
                },
            },
        })
    def _run_lint(**kwargs):
        """代码检查入口（参数见 schema；异常一律收口为 ok=False）"""
        try:
            return run_lint(
                kwargs.get("path") or ".",
                tool=kwargs.get("tool") or "auto",
                timeout_sec=kwargs.get("timeout_sec", _TIMEOUT_DEFAULT),
                max_output_chars=kwargs.get("max_output_chars", _OUTPUT_DEFAULT),
                dl=dl,
            )
        except Exception as e:  # noqa: BLE001 工具链路上绝不外抛异常
            logger.error("[run_lint] 检查异常: %s", e, exc_info=True)
            return {"ok": False, "error": f"代码检查异常: {e}"}


__all__ = ["register_all", "run_lint", "_TOOLS", "_TIMEOUT_CAP", "_OUTPUT_CAP"]
