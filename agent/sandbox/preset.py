"""权限预设（E0-1）：一个选择同时决定「沙箱写边界」与「审批姿态建议」。

【为什么要有预设，而不是继续堆 env】现状是 `CP_SANDBOX_SHELL_MODE`（危险命令模式匹配）+
文件工具各自的路径判断 + 插件 AppContainer 三套口径，运维说不清「现在到底能写哪里」。
DSH 的 `permission-presets` 把这件事收敛成**一个枚举**：`read-only` / `workspace-write` /
`full-access`；本模块是云枢的对应物。

【与 `CP_SANDBOX_SHELL_MODE` 的关系】正交，不要合并：
- `CP_SANDBOX_SHELL_MODE` 管「命令串像不像危险命令」（影子/强制），有历史灰度语义；
- 本预设管「写操作的边界在哪」（策略围栏 + Windows 受限令牌）。
两者都生效时取交集（预设拒绝的，模式匹配再放行也不会执行）。

【默认 full-access 的理由】这是机制落地不是行为变更：CI（干净 worktree、无覆盖层）与现网
不该因为一次合并突然改变写行为。生产切 `workspace-write` 是设置中心的一次 B 级操作（hot 生效，
reset 即回滚），由 Owner 拍板。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

#: 主开关（B 级 enum，hot 生效；读取点必须逐条字面量，见 settings/registry 零缺口门禁）
PRESET_ENV = "CP_PERMISSION_PRESET"
#: 沙箱工作区根（C 级只读路径；空 = 仓库根）。给探针/多工作区部署留一个显式口子。
ROOT_ENV = "CP_PERMISSION_SANDBOX_ROOT"

READ_ONLY = "read-only"
WORKSPACE_WRITE = "workspace-write"
FULL_ACCESS = "full-access"
PRESETS: Tuple[str, ...] = (READ_ONLY, WORKSPACE_WRITE, FULL_ACCESS)
DEFAULT_PRESET = FULL_ACCESS

#: 预设 → 审批姿态**建议**（E0-1 只暴露，不改审批链；E1-1 计划模式才接线）
_APPROVAL_POLICY = {
    READ_ONLY: "strict",
    WORKSPACE_WRITE: "standard",
    FULL_ACCESS: "minimal",
}

_WARNED_INVALID = False


def repo_root() -> Path:
    """仓库根（`agent/` 的上一级）；不依赖进程 cwd（deploy/测试的 cwd 不总是仓库根）。"""
    return Path(__file__).resolve().parents[2]


def sandbox_root() -> Path:
    """沙箱工作区根：`CP_PERMISSION_SANDBOX_ROOT` 优先，否则仓库根。"""
    raw = (os.environ.get(ROOT_ENV) or "").strip()
    if raw:
        try:
            return Path(raw).expanduser().resolve()
        except (OSError, ValueError):
            logger.warning("[sandbox] %s 不是合法路径，回落到仓库根", ROOT_ENV)
    return repo_root()


def permission_preset() -> str:
    """当前权限预设（非法值回落 full-access 并告警一次，绝不静默当成更严/更松的档）。"""
    raw = str(os.environ.get(PRESET_ENV, DEFAULT_PRESET) or "").strip().lower()
    if raw in PRESETS:
        return raw
    global _WARNED_INVALID
    if not _WARNED_INVALID:
        _WARNED_INVALID = True
        logger.warning("[sandbox] %s=%r 不是合法预设 %s，回落到 %s",
                       PRESET_ENV, raw, list(PRESETS), DEFAULT_PRESET)
    return DEFAULT_PRESET


def temp_root() -> Path:
    """沙箱私有临时根（受限子进程的 TEMP/TMP/HOME 落这里）。

    为什么不用系统 TEMP：受限令牌只对显式授权的对象可写，系统 TEMP 是全局共享目录，
    给它递归授权等于把别人的临时文件也放进写允许表。
    """
    return Path(__file__).resolve().parents[1] / "data" / "sandbox" / "tmp"


def writable_roots(preset: Optional[str] = None) -> Tuple[Path, ...]:
    """该预设下的**可写根**（策略围栏与 shell ACL 授权共用同一集合，防止两套口径漂移）。

    - `read-only`：空（连临时目录也不授权，与 DSH 一致）；
    - `workspace-write`：工作区根 + 私有临时根；
    - `full-access`：不限（返回空元组，调用方据此跳过围栏，不要把它当成「无处可写」）。
    """
    mode = preset or permission_preset()
    if mode == WORKSPACE_WRITE:
        return (sandbox_root(), temp_root())
    return ()


def approval_policy(preset: Optional[str] = None) -> str:
    """审批姿态建议（strict / standard / minimal）。E0-1 仅暴露，不执行。"""
    return _APPROVAL_POLICY.get(preset or permission_preset(), "standard")


def is_confined(preset: Optional[str] = None) -> bool:
    """是否需要 OS/策略围栏（full-access 不围栏）。"""
    return (preset or permission_preset()) != FULL_ACCESS


def status() -> Dict[str, Any]:
    """给 API/探针看的预设事实（不要在这里做重活：会被设置页/健康检查调用）。"""
    mode = permission_preset()
    roots = writable_roots(mode)
    return {
        "preset": mode,
        "default_preset": DEFAULT_PRESET,
        "confined": mode != FULL_ACCESS,
        "writable_roots": [str(p) for p in roots],
        "sandbox_root": str(sandbox_root()),
        "temp_root": str(temp_root()),
        "approval_policy": approval_policy(mode),
        "approval_policy_wired": False,
        "env": PRESET_ENV,
        "root_env": ROOT_ENV,
    }


def audit(event: str, *, subject: str = "", payload: Optional[Dict[str, Any]] = None) -> None:
    """预设相关审计（best-effort：审计失败不影响主流程，但会留 WARNING）。

    事件名约定：`sandbox_preset`（切档/读取事实）、`sandbox_fs_denied`（文件围栏拒绝）、
    `sandbox_shell_confined` / `sandbox_shell_denied`（子进程围栏）、`sandbox_unavailable`。
    """
    try:
        from agent.audit.facade import audit as _audit
        _audit.record(event, actor="sandbox", subject=subject or event,
                      payload=dict(payload or {}), source="agent")
    except Exception as exc:  # noqa: BLE001  审计是旁路，不阻断主流程
        logger.debug("[sandbox] 审计写入失败(%s): %s", event, exc)


__all__ = [
    "PRESET_ENV", "ROOT_ENV", "READ_ONLY", "WORKSPACE_WRITE", "FULL_ACCESS",
    "PRESETS", "DEFAULT_PRESET", "repo_root", "sandbox_root", "permission_preset",
    "temp_root", "writable_roots", "approval_policy", "is_confined", "status", "audit",
]

