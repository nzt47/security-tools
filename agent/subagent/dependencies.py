"""bundle 离线依赖清单（P5 / portability：可带走要能说清"缺什么、能不能断网跑"）

【解决什么（不这样会怎样）】
    `bundle` 此前只回答"这个分身是谁、怎么装、密钥引用长什么样"，不回答
    **"把它拿到另一台机器上，依赖够不够"**。于是"可带走"只是一句口号：
    换机/断网时才发现缺包，而 bundle 本身没有任何证据说它缺。

    本模块采集**声明依赖（pyproject.toml）**与**本机实装（importlib.metadata）**，
    产出一份诚实的 `environment` 段，由导出端点注入 bundle、由导入端点对拍回显。

【三条"不谎报"硬纪律（每条都有守卫）】
    1. **包名绝不作为 dict 键**：一律 `list[{name, required, installed, status}]`。
       否则 `tokenizers` 这类真实包名会命中 `credentials.py` 的"可疑键名"正则
       （含 token）⇒ 导出被密钥闸误判为 `ManifestSecretLeak`。这是本模块第一风险。
    2. **判不了就标 unknown / missing，永不默认 ok**：没查到实装 ⇒ `missing`；
       没有 packaging 或版本区间解析失败 ⇒ `unknown`；pyproject 读不到 ⇒ `unreadable`。
    3. **`offline_ready` 恒为 false（本增量）**：只有包里真带了可安装物（wheelhouse）
       才可能为 true；本模块 `artifacts.mode="none"`，故"全部依赖都装好了"也**不**
       声称离线就绪 —— `satisfied_locally`（导出机装了）与 `offline_ready`（包能自足）
       是两件事。

【不联网、不 subprocess、不 import 被测包】
    只读 pyproject.toml + importlib.metadata；不调 pip、不 HTTP、不 import 依赖本身。
    `scripts/predownload_model.py`（会下载 HF 模型）**不得**被本模块调用。

【依赖纪律】标准库 + 惰性 `packaging`（缺失即降级为 unknown，不抛）。
"""

from __future__ import annotations

import logging
import os
import platform
import re
from importlib import metadata as importlib_metadata
from typing import Any, Dict, List, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "CANONICAL_SOURCE",
    "PYPROJECT_PATH",
    "ARTIFACTS_MODE_NONE",
    "STATUS_OK",
    "STATUS_MISSING",
    "STATUS_VERSION_MISMATCH",
    "STATUS_UNKNOWN",
    "canonical_name",
    "read_declared_dependencies",
    "read_python_requirement",
    "installed_versions",
    "satisfies",
    "build_dependency_manifest",
    "offline_ready",
    "check_against_bundle",
    "validate_environment",
]

#: 声明依赖的权威来源（requirements.txt 仓内自认残缺，不作权威）
CANONICAL_SOURCE = "pyproject.toml [project].dependencies"

#: 无打包物（未 vendoring wheel）——本增量的诚实取值
ARTIFACTS_MODE_NONE = "none"

STATUS_OK = "ok"
STATUS_MISSING = "missing"
STATUS_VERSION_MISMATCH = "version_mismatch"
STATUS_UNKNOWN = "unknown"

#: 仓库根：本文件在 agent/subagent/ 下 ⇒ 上溯三层
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PYPROJECT_PATH = os.path.join(_REPO_ROOT, "pyproject.toml")

_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9_.\-]*)\s*(.*)$")


def canonical_name(name: str) -> str:
    """PEP 503 归一化：小写、把 [-_.] 连续段折叠成单个 "-"（与 metadata 对拍用）"""
    return re.sub(r"[-_.]+", "-", str(name or "").strip().lower())


# ════════════════════════════════════════════════════════════
#  读取声明 / 实装
# ════════════════════════════════════════════════════════════


def read_declared_dependencies(pyproject_path: Optional[str] = None
                               ) -> Tuple[List[Dict[str, str]], str]:
    """读 pyproject `[project].dependencies` → (`[{name, specifier}]`, source_status)

    读不到 / 解析失败 ⇒ `([], "unreadable")`，**绝不抛**（导出不能因此 500）。
    """
    path = pyproject_path or PYPROJECT_PATH
    try:
        import tomllib
    except Exception as e:  # noqa: BLE001 Python<3.11
        logger.warning("[Dependencies] tomllib 不可用（按 unreadable 处理）: %s", e)
        return [], "unreadable"
    try:
        with open(path, "rb") as fh:
            doc = tomllib.load(fh)
        deps = doc["project"]["dependencies"]
    except Exception as e:  # noqa: BLE001 文件缺失/结构不符
        logger.warning("[Dependencies] 读取声明依赖失败（按 unreadable 处理）: %s", e)
        return [], "unreadable"
    if not isinstance(deps, list):
        return [], "unreadable"
    out: List[Dict[str, str]] = []
    for item in deps:
        text = str(item or "").strip()
        if not text:
            continue
        match = _NAME_RE.match(text)
        if not match:
            continue
        out.append({"name": match.group(1), "specifier": match.group(2).strip()})
    return out, "ok"


def read_python_requirement(pyproject_path: Optional[str] = None) -> str:
    """读 `[project].requires-python`；读不到返回空串（不猜）"""
    path = pyproject_path or PYPROJECT_PATH
    try:
        import tomllib
        with open(path, "rb") as fh:
            doc = tomllib.load(fh)
        return str(doc["project"].get("requires-python") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def installed_versions() -> Dict[str, str]:
    """本机实装分布 → `{canonical_name: version}`（不 import 被测包、不联网）"""
    out: Dict[str, str] = {}
    try:
        for dist in importlib_metadata.distributions():
            meta = getattr(dist, "metadata", None)
            name = meta["Name"] if meta is not None else None
            if not name:
                continue
            out[canonical_name(str(name))] = str(getattr(dist, "version", "") or "")
    except Exception as e:  # noqa: BLE001 环境异常按"查不到"处理
        logger.warning("[Dependencies] 枚举实装分布失败（按空表处理）: %s", e)
    return out


def satisfies(version: str, specifier: str) -> Optional[bool]:
    """`version` 是否满足 PEP 440 区间 `specifier`

    `None` = **判不了**（packaging 缺失 / 区间含环境标记 / 版本非法）——调用方必须把它
    标成 `unknown`，不得当成 ok。
    """
    spec = str(specifier or "").split(";")[0].strip()
    if not spec:
        return True
    try:
        from packaging.specifiers import SpecifierSet
        from packaging.version import Version
    except Exception:  # noqa: BLE001 packaging 未装
        return None
    try:
        return Version(str(version)) in SpecifierSet(spec)
    except Exception:  # noqa: BLE001 解析失败
        return None


# ════════════════════════════════════════════════════════════
#  构造 environment 段
# ════════════════════════════════════════════════════════════


def _python_section(required: str, running: str) -> Dict[str, Any]:
    verdict = satisfies(running, required) if required else None
    if not required:
        status = STATUS_UNKNOWN
    elif verdict is True:
        status = STATUS_OK
    elif verdict is False:
        status = STATUS_VERSION_MISMATCH
    else:
        status = STATUS_UNKNOWN
    return {"required": str(required or ""), "running": str(running or ""), "status": status}


def build_dependency_manifest(*, declared: Optional[List[Mapping[str, Any]]] = None,
                              installed: Optional[Mapping[str, str]] = None,
                              expected_python: str = "",
                              running_python: str = "",
                              captured_at: str = "",
                              source_status: str = "") -> Dict[str, Any]:
    """构造 `environment` 段（纯函数；可注入 declared/installed 便于测试）

    结构见模块 docstring。**任何异常都收敛为 unreadable/unknown，绝不抛到导出层。**
    """
    if declared is None:
        declared, read_status = read_declared_dependencies()
    else:
        read_status = str(source_status or "ok")
    inst = dict(installed) if installed is not None else installed_versions()
    expected = str(expected_python or read_python_requirement() or "")
    running = str(running_python or platform.python_version())

    items: List[Dict[str, Any]] = []
    counts = {"declared": 0, STATUS_OK: 0, STATUS_MISSING: 0,
              STATUS_VERSION_MISMATCH: 0, STATUS_UNKNOWN: 0}
    for dep in declared or ():
        name = str(dep.get("name") or "").strip()
        if not name:
            continue
        spec = str(dep.get("specifier") or "")
        got = inst.get(canonical_name(name))
        if not got:
            status, installed_value = STATUS_MISSING, None
        else:
            verdict = satisfies(str(got), spec)
            if verdict is True:
                status = STATUS_OK
            elif verdict is False:
                status = STATUS_VERSION_MISMATCH
            else:
                status = STATUS_UNKNOWN
            installed_value = str(got)
        items.append({"name": name, "required": spec,
                      "installed": installed_value, "status": status})
        counts["declared"] += 1
        counts[status] = counts.get(status, 0) + 1

    from datetime import datetime, timezone

    manifest: Dict[str, Any] = {
        "source": CANONICAL_SOURCE,
        "source_status": read_status,
        "captured_at": str(captured_at or datetime.now(timezone.utc).isoformat(timespec="seconds")),
        "python": _python_section(expected, running),
        "items": items,
        "counts": counts,
        "satisfied_locally": (counts[STATUS_MISSING] == 0
                              and counts[STATUS_VERSION_MISMATCH] == 0),
        "artifacts": {"mode": ARTIFACTS_MODE_NONE, "count": 0},
        "offline_ready": False,
        "reason": ("仅采集声明依赖与本机实装，未打包 wheel：不代表目标机可离线安装；"
                   "satisfied_locally 只说明导出机装了，不等于可带走"),
    }
    manifest["offline_ready"] = offline_ready(manifest)
    return manifest


def offline_ready(manifest: Mapping[str, Any]) -> bool:
    """离线就绪判定（**硬判**）：包里没有可安装物（artifacts.mode=none）即恒 false

    只有"真带了 wheelhouse（mode!=none 且 count>0）且无 missing/version_mismatch
    且 python 版本匹配"时才是 true。本增量 artifacts.mode 恒 none ⇒ 恒 false。
    """
    env = manifest if isinstance(manifest, Mapping) else {}
    artifacts = env.get("artifacts") if isinstance(env.get("artifacts"), Mapping) else {}
    if str(artifacts.get("mode") or ARTIFACTS_MODE_NONE) == ARTIFACTS_MODE_NONE:
        return False
    try:
        if int(artifacts.get("count") or 0) <= 0:
            return False
    except (TypeError, ValueError):
        return False
    counts = env.get("counts") if isinstance(env.get("counts"), Mapping) else {}
    for key in (STATUS_MISSING, STATUS_VERSION_MISMATCH):
        try:
            if int(counts.get(key) or 0) > 0:
                return False
        except (TypeError, ValueError):
            return False
    python = env.get("python") if isinstance(env.get("python"), Mapping) else {}
    return str(python.get("status") or "") == STATUS_OK


def check_against_bundle(environment: Any) -> Dict[str, Any]:
    """导入端对拍：在**到达端**核对 bundle 声明的依赖是否满足（只读、fail-soft）

    返回 `{satisfied, missing, version_mismatch, python_ok, offline_ready, reason}`。
    导入成功 ≠ 可离线跑：`offline_ready` 恒 false，`satisfied` 只说明到达端这台机器装齐了。
    """
    env = environment if isinstance(environment, Mapping) else {}
    items = env.get("items") if isinstance(env.get("items"), list) else []
    inst = installed_versions()
    missing: List[str] = []
    mismatch: List[str] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        got = inst.get(canonical_name(name))
        if not got:
            missing.append(name)
            continue
        verdict = satisfies(str(got), str(item.get("required") or ""))
        if verdict is False:
            mismatch.append(name)
    python = env.get("python") if isinstance(env.get("python"), Mapping) else {}
    required = str(python.get("required") or "")
    running = platform.python_version()
    py_verdict = satisfies(running, required) if required else None
    python_ok = py_verdict is not False
    satisfied = (not missing) and (not mismatch) and python_ok
    reason = ("到达端依赖满足" if satisfied else
              "到达端缺失/版本不符：missing=%s mismatch=%s python_ok=%s"
              % (missing, mismatch, python_ok))
    return {"satisfied": bool(satisfied), "missing": missing,
            "version_mismatch": mismatch, "python_ok": bool(python_ok),
            "offline_ready": False, "reason": reason}


def validate_environment(environment: Any) -> List[str]:
    """校验 `environment` 段（问题清单，空 = 通过）；供 bundle.validate_bundle 复用"""
    if not isinstance(environment, Mapping):
        return ["environment 必须是对象"]
    problems: List[str] = []
    if environment.get("source_status") not in ("ok", "unreadable"):
        problems.append("environment.source_status 必须是 ok / unreadable")
    if "offline_ready" in environment and not isinstance(environment["offline_ready"], bool):
        problems.append("environment.offline_ready 必须是布尔")
    items = environment.get("items")
    if items is not None:
        if not isinstance(items, list):
            problems.append("environment.items 必须是列表")
        else:
            for idx, item in enumerate(items):
                if not isinstance(item, Mapping):
                    problems.append("environment.items[%d] 必须是对象" % idx)
                    continue
                for key in ("name", "status"):
                    if not str(item.get(key) or "").strip():
                        problems.append("environment.items[%d].%s 不得为空" % (idx, key))
    return problems
