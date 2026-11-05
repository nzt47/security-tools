"""受控软件安装通道（software_probe / software_install）

【为什么不是"恢复 software_*"】
    2026-09-17 的 `36e4a09b1` 把旧的 software_search/install/list/uninstall 整族退役：
    底层 `SoftwareManager` 是机器生成的空壳，`install()` 恒返回 `True`、
    `_installed_software` 从不写入 ⇒ **返回成功却什么都没装**。模型据此认为软件已装好
    并继续下一步，比"没有这个工具"更糟（判据见 `docs/工具集评估与重分类报告.md` §4.4a）。
    本模块**不复用旧实现、不复用旧名字**，只承接能力诉求：
      · `software_probe`  只读探测（本机卸载登记表，永不改机器状态）；
      · `software_install` 受控安装（winget 优先 / choco 备选，四道闸）；
      · 不做 uninstall —— 删软件的风险高于装，且没有可回滚语义（要卸由人来）。

【不易】四道闸缺一不可：
    1. 白名单源：`config/software_allowlist.json` 里**精确等值**的 package_id
       （不做前缀/模糊匹配 —— 那是"装错包"的常见来源）；
    2. 显式确认：`confirm=true` 才执行，否则只回"将执行什么"（argv 预览）；
    3. 退出码：非 0 一律 `ok=false`，不解释成"可能装上了"；
    4. 事后探测：装完**再查一次**登记表，版本/安装态对不上就 `ok=false`
       —— 这一条才是对"谎报成功"的根治（旧实现的病根就在这里）。
    另外：安装器是**常量名查表 + shutil.which 解析**（用户串不进 argv[0]，与
    §11.36 E 的 `py/command-line-injection` 同口径）；硬超时防交互挂死；每次 install 落审计。

【变易】
    · 白名单是**数据文件**不是环境变量（避免 settings 注册表出现新读取点）；
      默认 `packages: []` ⇒ 未登记时 install 恒被判 `not-in-allowlist`；
    · 安装器参数表 `_INSTALL_ARGS` 是数据：winget 走 `--id .. --exact`，
      choco 走 `--exact`，两者都带静默与免交互开关；
    · 超时 `_INSTALL_TIMEOUT`、日志尾巴长度是这里的常量。

【简易】纯标准库（json/os/shutil/subprocess/time + winreg 在 Windows 上按需导入），
    不新增依赖；注册入口沿用本仓惯用的 `register_all(dl)`。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from typing import Any, Dict, Iterator, List, Optional

from agent import tools as _tools

logger = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 白名单（数据文件；不是 env ⇒ 不需要进 agent/settings/registry.py）
_ALLOWLIST_PATH = os.path.join(_ROOT, "config", "software_allowlist.json")

#: 安装器白名单：**键→常量名**，argv[0] 永远来自这张表（再经 shutil.which 解析）
_INSTALLERS = {"winget": "winget", "choco": "choco"}

#: 每个安装器固定参数（数据表；package_id 只作为**值**出现在数组里，不进 argv[0]）
_INSTALL_ARGS = {
    "winget": ("install", "--id", "{id}", "--exact", "--silent",
               "--accept-package-agreements", "--accept-source-agreements"),
    "choco": ("install", "{id}", "--exact", "-y", "--no-progress", "--limit-output"),
}

#: 硬超时（秒）：winget 下载大包可能几分钟，但必须有上界
_INSTALL_TIMEOUT = 900

#: 返回里保留的日志尾巴长度（字符）
_LOG_TAIL = 2000

#: Windows 卸载登记表的三个位置（常量；不拼接任何调用方输入）
_UNINSTALL_KEYS = (
    ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKLM", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKCU", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
)


def _load_allowlist() -> Dict[str, Dict[str, Any]]:
    """读白名单：返回 `{package_id.lower(): entry}`。

    文件缺失/损坏一律当"空白名单"（fail-closed：没有白名单就装不了任何东西）。
    """
    try:
        with open(_ALLOWLIST_PATH, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        logger.debug("[软件] 白名单读取失败（按空处理）: %s", type(exc).__name__)
        return {}
    items = doc.get("packages") if isinstance(doc, dict) else None
    out: Dict[str, Dict[str, Any]] = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("id") or "").strip()
        if not pid:
            continue
        out[pid.lower()] = {
            "id": pid,
            "installer": str(item.get("installer") or "winget").strip().lower(),
            "probe": [str(x) for x in (item.get("probe") or [pid])],
        }
    return out


def _match_allowlist(package_id: str) -> Optional[Dict[str, Any]]:
    """精确等值匹配（大小写不敏感）；不做前缀/子串 —— 防"模糊匹配装错包"。"""
    if not isinstance(package_id, str):
        return None
    key = package_id.strip().lower()
    if not key:
        return None
    return _load_allowlist().get(key)


def _resolve_installer(kind: str) -> Optional[str]:
    """把 installer 名映射成**常量名**再解析成可执行路径（认不出返回 None）。"""
    name = _INSTALLERS.get(str(kind or "").strip().lower())
    if name is None:
        return None
    return shutil.which(name)


def _iter_installed() -> Iterator[Dict[str, str]]:
    """逐个产出卸载登记表条目（Windows 注册表；其它平台产出空）。

    【为什么读注册表而不是 `winget list`】`winget list` 输出是给人看的表格，
    解析口径随版本漂移；注册表里 `DisplayName`/`DisplayVersion`/`InstallLocation`
    是稳定字段，而且**只读**、不启动任何进程。
    """
    if os.name != "nt":
        return
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover - 非 Windows 环境走不到
        return
    hives = {"HKLM": winreg.HKEY_LOCAL_MACHINE, "HKCU": winreg.HKEY_CURRENT_USER}
    for hive_name, subkey in _UNINSTALL_KEYS:
        hive = hives.get(hive_name)
        if hive is None:
            continue
        try:
            root = winreg.OpenKey(hive, subkey)
        except OSError:
            continue
        try:
            count = winreg.QueryInfoKey(root)[0]
            for i in range(count):
                try:
                    child_name = winreg.EnumKey(root, i)
                except OSError:
                    continue
                try:
                    child = winreg.OpenKey(root, child_name)
                except OSError:
                    continue
                with child:
                    item = {"source": "registry:" + hive_name}
                    for field, out_key in (("DisplayName", "name"),
                                           ("DisplayVersion", "version"),
                                           ("InstallLocation", "path")):
                        try:
                            value, _kind = winreg.QueryValueEx(child, field)
                        except OSError:
                            continue
                        if isinstance(value, str) and value.strip():
                            item[out_key] = value.strip()
                    if item.get("name"):
                        yield item
        finally:
            root.Close()


def _probe_names(names: List[str]) -> Dict[str, Any]:
    """按给定名字列表探测：先精确等值，再退化到"包含"（并如实回匹配到的显示名）。"""
    wanted = [str(n).strip().lower() for n in names if str(n or "").strip()]
    if not wanted:
        return {"ok": True, "installed": False, "version": "", "path": "",
                "source": "", "matched_display_name": "", "reason": "empty-name"}
    items = list(_iter_installed())
    for matcher, label in ((lambda low, w: low == w, "exact"),
                           (lambda low, w: w in low, "contains")):
        for item in items:
            low = str(item.get("name") or "").strip().lower()
            if not low:
                continue
            if any(matcher(low, w) for w in wanted):
                return {
                    "ok": True,
                    "installed": True,
                    "version": str(item.get("version") or ""),
                    "path": str(item.get("path") or ""),
                    "source": str(item.get("source") or ""),
                    "matched_display_name": str(item.get("name") or ""),
                    "match": label,
                }
    return {"ok": True, "installed": False, "version": "", "path": "",
            "source": "", "matched_display_name": "", "match": ""}


def software_probe(name: str = "") -> Dict[str, Any]:
    """只读探测：软件是否已安装 + 版本 + 安装路径 + 登记来源。

    永远可以安全调用（不启动安装器、不写文件、不改配置）。
    """
    if os.name != "nt":
        return {"ok": False, "installed": False, "version": "", "path": "",
                "source": "", "matched_display_name": "",
                "reason": "unsupported-platform"}
    try:
        return _probe_names([name])
    except Exception as exc:  # noqa: BLE001 探测失败如实回失败，不假装"未安装"
        logger.debug("[软件] 探测失败: %s", type(exc).__name__)
        return {"ok": False, "installed": False, "version": "", "path": "",
                "source": "", "matched_display_name": "",
                "reason": "probe-failed:" + type(exc).__name__}


def _audit_install(package_id: str, *, installer: str, exit_code: int,
                   installed_after: bool, verified: bool, elapsed_ms: int,
                   denied_reason: str) -> None:
    """install 落审计（package_id / 退出码 / 是否验证通过 / 耗时）。

    这里没有任何凭据，故照常记录原文；失败不影响安装判定（best-effort）。
    """
    try:
        from agent.audit.facade import audit
        audit.record(
            "tool.software_install",
            actor="tools.software_tools",
            subject="software:" + (package_id or "-"),
            payload={
                "installer": installer,
                "exit_code": int(exit_code),
                "installed_after": bool(installed_after),
                "verified": bool(verified),
                "elapsed_ms": int(elapsed_ms),
                "denied_reason": denied_reason,
            },
            source="agent",
        )
    except Exception as exc:  # noqa: BLE001 审计失败不影响安装结果
        logger.debug("[软件] 安装审计写入失败: %s", type(exc).__name__)


def _tail(text: str) -> str:
    return str(text or "")[-_LOG_TAIL:]


def software_install(package_id: str = "", *, confirm: bool = False) -> Dict[str, Any]:
    """受控安装（winget 优先 / choco 备选）。四道闸见模块文档。"""
    started = time.monotonic()
    entry = _match_allowlist(package_id)
    if entry is None:
        return {
            "ok": False, "package_id": str(package_id or ""), "installer": "",
            "exit_code": -1, "installed_before": False, "installed_after": False,
            "version_before": "", "version_after": "", "changed": False,
            "verified": False, "elapsed_ms": 0, "log_tail": "",
            "denied_reason": "not-in-allowlist",
            "allowlist_size": len(_load_allowlist()),
        }

    entry_id = entry["id"]
    installer = entry["installer"]
    program = _resolve_installer(installer)
    if program is None:
        return {
            "ok": False, "package_id": entry_id, "installer": installer,
            "exit_code": -1, "installed_before": False, "installed_after": False,
            "version_before": "", "version_after": "", "changed": False,
            "verified": False, "elapsed_ms": 0, "log_tail": "",
            "denied_reason": "installer-not-found",
        }

    argv = [program, *[part.format(id=entry_id) for part in _INSTALL_ARGS[installer]]]

    if not confirm:
        # 无确认只回"将执行什么"（不跑子进程、不改机器状态）
        return {
            "ok": False, "package_id": entry_id, "installer": installer,
            "exit_code": -1, "installed_before": bool(
                _probe_names(entry["probe"])["installed"]),
            "installed_after": False, "version_before": "", "version_after": "",
            "changed": False, "verified": False,
            "elapsed_ms": int((time.monotonic() - started) * 1000), "log_tail": "",
            "denied_reason": "confirm-required",
            "plan": {"installer": installer, "package_id": entry_id,
                     "command": " ".join(argv)},
        }

    before = _probe_names(entry["probe"])
    exit_code = -1
    log_tail = ""
    denied = ""
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_INSTALL_TIMEOUT,
            shell=False,
        )
        exit_code = int(getattr(proc, "returncode", -1) or 0)
        log_tail = _tail((proc.stdout or "") + (proc.stderr or ""))
    except subprocess.TimeoutExpired as exc:
        denied = "timeout"
        log_tail = _tail(str(getattr(exc, "output", "") or ""))
    except OSError as exc:
        denied = "installer-failed:" + type(exc).__name__

    after = _probe_names(entry["probe"])
    installed_after = bool(after["installed"])
    changed = installed_after and (
        not before["installed"] or after["version"] != before["version"])

    ok = exit_code == 0 and installed_after
    if not ok and not denied:
        if exit_code != 0:
            denied = "install-failed"
        elif not installed_after:
            denied = "not-installed-after"

    elapsed_ms = int((time.monotonic() - started) * 1000)
    _audit_install(entry_id, installer=installer, exit_code=exit_code,
                   installed_after=installed_after, verified=ok,
                   elapsed_ms=elapsed_ms, denied_reason=denied)
    return {
        "ok": bool(ok),
        "package_id": entry_id,
        "installer": installer,
        "exit_code": exit_code,
        "installed_before": bool(before["installed"]),
        "installed_after": installed_after,
        "version_before": str(before["version"]),
        "version_after": str(after["version"]),
        "changed": bool(changed),
        "verified": bool(installed_after),
        "elapsed_ms": elapsed_ms,
        "log_tail": log_tail,
        "denied_reason": denied,
    }


__all__ = ["register_all", "software_probe", "software_install"]


def register_all(dl) -> None:
    """注册两个软件通道工具（schema/description 必须与 YAML 逐字一致）"""

    @_tools.register("software_probe", "只读探测本机是否已安装某个软件（版本与安装路径），查询卸载登记表，不启动安装器、不改机器状态。Probe installed software version and path", schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "软件显示名（如 Git、Python、Node.js）"},
        },
        "required": ["name"],
    })
    def _software_probe(**kwargs):
        return software_probe(kwargs.get("name", ""))

    @_tools.register("software_install", "受控安装软件（默认停用，需 Owner 启用）。只接受 config/software_allowlist.json 里精确等值的 package_id；必须显式传 confirm=true 才执行，否则只返回将要执行的命令；winget 优先、choco 备选；装完会再探测一次核对安装态与版本，对不上就如实返回 ok=false。Install software through a controlled allowlist", schema={
        "type": "object",
        "properties": {
            "package_id": {"type": "string", "description": "白名单里的精确包 id（如 Git.Git）"},
            "confirm": {"type": "boolean", "description": "必须显式传 true 才会执行；缺省只返回将要执行的命令"},
        },
        "required": ["package_id"],
    })
    def _software_install(**kwargs):
        return software_install(kwargs.get("package_id", ""),
                                confirm=bool(kwargs.get("confirm", False)))
