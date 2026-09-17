"""工具注册模块 — Git 版本控制（单一 `git` 工具 + action 枚举）

工程闭环里「改完代码要能看 diff、要能提交」是刚需，而此前云枢只能靠
``shell_execute`` 拼字符串调 git —— 那正是最不该走 shell 的一类调用：

    - 提交信息里的引号/分号/反引号会变成 shell 语法（注入面）；
    - 分支名与路径里的空格会被拆词；
    - 中文路径在 Windows 控制台下编码不可控。

本模块用 ``subprocess.run(argv=list)`` 直接调用 git 二进制，**永不 shell=True**。

【不易】
    - 写操作（add/commit/checkout/stash）必须显式 ``confirm=True`` 才执行：
      这是"模型能跑 git"与"模型能悄悄重写工作区"之间的那条线。
    - git 不在 PATH 时优雅降级（返回 ``git_available: False``），不抛异常。
    - 输出统一截断到 20KB，避免一次 ``git diff`` 把上下文挤爆。
【变易】只读动作与写动作清单是数据（``_READ_ONLY_ACTIONS`` / ``_MUTATING_ACTIONS``）。
【简易】一个工具 + 一个 action 枚举，优于 9 个近义工具（省 token、路由更稳）。
"""
import logging
import os
import shutil
import subprocess

from agent import tools as _tools

logger = logging.getLogger(__name__)

#: 只读动作：不改变仓库状态
_READ_ONLY_ACTIONS = ("status", "diff", "log", "branch", "show")
#: 写动作：必须 confirm=True
_MUTATING_ACTIONS = ("add", "commit", "checkout", "stash")
_ACTIONS = _READ_ONLY_ACTIONS + _MUTATING_ACTIONS

_MAX_OUTPUT_CHARS = 20 * 1024
_TIMEOUT_CAP_SEC = 120
_DEFAULT_TIMEOUT_SEC = 60

#: git 可执行文件路径缓存（None 表示"查过但没有"）
_GIT_EXE_CACHE: dict = {}


def _repo_root() -> str:
    """项目根目录（agent/tools/git_tools.py → 上溯三级）"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _find_git():
    """定位 git 二进制；找不到返回 None。结果缓存，避免每次调用都扫 PATH。"""
    if "path" not in _GIT_EXE_CACHE:
        try:
            _GIT_EXE_CACHE["path"] = shutil.which("git")
        except Exception:  # noqa: BLE001
            _GIT_EXE_CACHE["path"] = None
    return _GIT_EXE_CACHE["path"]


def _truncate(text, limit: int = _MAX_OUTPUT_CHARS):
    """截断长文本，返回 (文本, 是否截断)。"""
    if not text:
        return "", False
    if len(text) <= limit:
        return text, False
    return text[:limit] + f"\n...[输出已截断，原文共 {len(text)} 字符，仅保留前 {limit} 字符]", True


def _permission_denied(dl, action: str, context: str):
    """写动作的权限闸门（可选依赖，缺失时放行）。"""
    check = getattr(getattr(dl, "_permission", None), "check_action", None)
    if not callable(check):
        return None
    try:
        result = check(action, context)
    except Exception as e:  # noqa: BLE001 校验故障不阻断（confirm 闸门仍在）
        logger.warning("[git_tools] 权限校验异常，按放行处理: %s — %s", action, e)
        return None
    if isinstance(result, dict):
        allowed, reason = result.get("allowed"), result.get("reason", "")
    else:
        allowed, reason = getattr(result, "allowed", None), getattr(result, "reason", "")
    if allowed is False:
        return {"ok": False, "error": f"权限系统拒绝: {reason}", "blocked": True}
    return None


def _as_int(value, default, low=None, high=None) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    if low is not None:
        result = max(low, result)
    if high is not None:
        result = min(high, result)
    return result


def _norm_paths(paths):
    """把 paths 参数规整为字符串列表。"""
    if paths is None:
        return []
    if isinstance(paths, str):
        return [paths] if paths else []
    if isinstance(paths, (list, tuple)):
        return [str(p) for p in paths if str(p or "").strip()]
    return []


def register_all(dl):
    """注册 git 工具

    Args:
        dl: DigitalLife 实例（用于权限闸门；缺失时按放行处理）
    """

    @_tools.register("git", "执行 git 版本控制操作。action 取值：status（工作区状态）、diff（代码改动）、log（提交历史）、branch（分支列表）、show（查看某次提交）、add（暂存文件）、commit（提交）、checkout（切换分支/检出文件）、stash（储藏改动）。add/commit/checkout/stash 属于写操作，必须显式传 confirm=true 才会执行。Run git commands, version control operations, git status diff log commit", schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                # 注意：此处必须是**字面量**列表。schema 要能被 AST 静态抽取
                # （scripts/migrate_tools_to_yaml.py）并与 YAML 逐字对齐，
                # 写成 list(_ACTIONS) 会让抽取结果为 None，YAML 与代码从此漂移。
                "enum": [
                    "status", "diff", "log", "branch", "show",
                    "add", "commit", "checkout", "stash",
                ],
                "description": "要执行的 git 动作：status/diff/log/branch/show 为只读；add/commit/checkout/stash 为写操作（需 confirm）",
            },
            "cwd": {"type": "string", "description": "仓库工作目录，默认项目根目录；相对路径按项目根目录解析"},
            "paths": {
                "type": "array", "items": {"type": "string"},
                "description": "限定文件/目录列表（add 必填；diff/log/checkout 可选）",
            },
            "message": {"type": "string", "description": "commit 的提交信息（commit 必填）；stash 时作为储藏说明"},
            "target": {"type": "string", "description": "checkout 的目标（分支名/提交/标签）；show 的修订版本，默认 HEAD"},
            "staged": {"type": "boolean", "description": "diff 是否查看已暂存的改动（等价 git diff --staged），默认 false"},
            "count": {"type": "integer", "description": "log 返回的提交条数，默认 20，范围 1-200"},
            "stat": {"type": "boolean", "description": "show 是否只输出变更统计（--stat），默认 false"},
            "stash_action": {
                "type": "string",
                "enum": ["push", "pop", "apply", "list", "show"],
                "description": "stash 子命令，默认 push（储藏）；pop/apply 用于恢复储藏，list/show 用于查看",
            },
            "timeout_sec": {"type": "integer", "description": "执行超时秒数，默认 60，最大 120"},
            "confirm": {"type": "boolean", "description": "写操作确认开关：add/commit/checkout/stash 必须传 true，否则拒绝执行"},
        },
        "required": ["action"],
    })
    def _git(**kwargs):
        try:
            action = str(kwargs.get("action") or "").strip().lower()
            if not action:
                return {"ok": False, "error": "请提供要执行的 git 动作（action）", "actions": list(_ACTIONS)}
            if action not in _ACTIONS:
                return {
                    "ok": False,
                    "error": f"不支持的 action: {action}",
                    "actions": list(_ACTIONS),
                }

            paths = _norm_paths(kwargs.get("paths"))
            message = kwargs.get("message") or ""
            target = kwargs.get("target") or ""
            staged = bool(kwargs.get("staged", False))
            stat = bool(kwargs.get("stat", False))
            count = _as_int(kwargs.get("count", 20), 20, low=1, high=200)
            stash_action = str(kwargs.get("stash_action") or "push").strip().lower()
            timeout_sec = _as_int(kwargs.get("timeout_sec", _DEFAULT_TIMEOUT_SEC),
                                  _DEFAULT_TIMEOUT_SEC, low=1, high=_TIMEOUT_CAP_SEC)

            repo_root = _repo_root()
            cwd = kwargs.get("cwd") or repo_root
            cwd = str(cwd)
            if not os.path.isabs(cwd):
                cwd = os.path.join(repo_root, cwd)
            cwd = os.path.abspath(cwd)
            if not os.path.isdir(cwd):
                return {"ok": False, "error": f"工作目录不存在: {cwd}", "cwd": cwd}

            # ── 写操作的确认闸门（在碰 git 之前先拦）──
            if action in _MUTATING_ACTIONS and kwargs.get("confirm") is not True:
                return {
                    "ok": False,
                    "error": f"git {action} 是写操作，需要人工确认：请在参数中显式传 confirm=true 后重试",
                    "action": action,
                    "confirm_required": True,
                }
            if action in _MUTATING_ACTIONS:
                denied = _permission_denied(dl, f"git:{action}", f"执行 git {action}（目录 {cwd}）")
                if denied:
                    return denied

            # ── 组装 argv（永远 list 形式，绝不 shell=True）──
            argv = None
            if action == "status":
                argv = ["status", "--short", "--branch"] + paths
            elif action == "diff":
                argv = ["diff"]
                if staged:
                    argv.append("--staged")
                if paths:
                    argv += ["--"] + paths
            elif action == "log":
                argv = ["log", f"-n{count}", "--date=short",
                        "--pretty=format:%h %ad %an %s"]
                if paths:
                    argv += ["--"] + paths
            elif action == "branch":
                argv = ["branch", "-a", "-v", "--no-color"]
            elif action == "show":
                argv = ["show"]
                if stat:
                    argv.append("--stat")
                argv.append(target or "HEAD")
            elif action == "add":
                if not paths:
                    return {"ok": False, "error": "git add 需要提供要暂存的文件或目录（paths）"}
                argv = ["add", "--"] + paths
            elif action == "commit":
                if not str(message).strip():
                    return {"ok": False, "error": "git commit 需要提供提交信息（message）"}
                argv = ["commit", "-m", str(message)]
                if paths:
                    argv += ["--"] + paths
            elif action == "checkout":
                if not target:
                    return {"ok": False, "error": "git checkout 需要提供目标（target）：分支名、提交或标签"}
                argv = ["checkout", str(target)]
                if paths:
                    argv += ["--"] + paths
            elif action == "stash":
                if stash_action not in ("push", "pop", "apply", "list", "show"):
                    return {"ok": False, "error": f"不支持的 stash_action: {stash_action}"}
                argv = ["stash", stash_action]
                if stash_action == "push" and str(message).strip():
                    argv += ["-m", str(message)]
                if stash_action == "push" and paths:
                    argv += ["--"] + paths

            if not argv:
                return {"ok": False, "error": f"内部错误：action {action} 未生成命令"}

            git_exe = _find_git()
            if not git_exe:
                return {
                    "ok": False,
                    "error": "未找到 git 可执行文件：PATH 中没有 git，请先安装 git 或将其加入 PATH",
                    "git_available": False,
                    "action": action,
                }

            full_argv = [git_exe, "--no-pager", "-c", "core.quotepath=false"] + argv
            env = dict(os.environ)
            env["GIT_PAGER"] = "cat"
            env["GIT_TERMINAL_PROMPT"] = "0"  # 禁止交互式凭据提示挂住工具

            try:
                proc = subprocess.run(
                    full_argv,
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_sec,
                    env=env,
                    shell=False,  # noqa: S603 显式声明：argv 列表 + 无 shell
                )
            except subprocess.TimeoutExpired:
                return {
                    "ok": False,
                    "error": f"git {action} 超时（>{timeout_sec} 秒）已被终止",
                    "action": action,
                    "timeout_sec": timeout_sec,
                }
            except FileNotFoundError:
                return {
                    "ok": False,
                    "error": "git 可执行文件无法启动（FileNotFoundError）",
                    "git_available": False,
                    "action": action,
                }
            except OSError as e:
                return {"ok": False, "error": f"启动 git 失败: {e}", "action": action}

            stdout, cut_out = _truncate(proc.stdout or "")
            stderr, cut_err = _truncate(proc.stderr or "")
            result = {
                "ok": proc.returncode == 0,
                "action": action,
                "argv": full_argv,
                "cwd": cwd,
                "returncode": proc.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "truncated": bool(cut_out or cut_err),
            }
            if proc.returncode != 0:
                reason = (stderr or stdout or "").strip().splitlines()
                result["error"] = (
                    f"git {action} 失败（退出码 {proc.returncode}）: "
                    f"{reason[0] if reason else '无输出'}"
                )
            return result
        except Exception as e:  # noqa: BLE001 绝不让异常逃逸到工具循环
            logger.warning("[git_tools] git 工具异常: %s", e)
            return {"ok": False, "error": f"git 工具执行失败: {e}"}
