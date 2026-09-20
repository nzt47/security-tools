"""`agent.capregistry` —— 云枢**能力层**的统一门面（TASK-05）

## 本包是什么

v1.4 的战略判据只有一条：**把 agent loop 关掉，这套东西还能不能被人、被 CI、
被别的系统用起来。** 本包交付这条判据的三块拼图：

| 模块 | 作用 |
|---|---|
| `view.py` | `CapabilityRegistry`：进程内、**只读**、`CapabilityRecord` 的派生视图 |
| `loader.py` | `Loader`：`location` 的**唯一分叉点**（Local/Stdio/SSE/HTTP 四实现） |
| `invoke.py` | 统一调用入口：HTTP/CLI/模型三条链路**同一个执行收口**（必过 `tools.call()`） |
| `errors.py` | 14 个统一错误码 + **唯一**异常映射点 + 异常脱敏 |
| `contract.py` | `input_schema`/`result_schema` 校验 + CI 可凭 `status` 阻断 |
| `modelcaps.py` | 模型 tool-calling 能力探测（`/capabilities/tools?model=` 的裁剪依据） |
| `call_sites.py` | 调用路径普查的**显式例外表**（`scripts/audit_call_paths.py` 消费） |
| `toolset_hash.py` | `toolset_hash` 与「hash 变 ⇒ 重建会话；**health 变 ⇒ 只过滤**」（TASK-08 E7 / v1.4 §11） |
| `pruning.py` | **裁剪保护**：`risk >= high` 或 `confirm_level >= L2` 的工具不参与裁剪（TASK-08 E8 / v1.4 §7） |

## 命名（**不撞名**）

仓库里已有 **7 个** `registry.py`（`agent/lines/`、`skills_mgmt/`、`settings/`、
`prompt_manager/`、`workflow_engine/`、`descriptors/`，以及 `agent/modules_registry.py`）。
故本包**不叫** `registry`，也**不新增**第 8 个 `registry.py`：
统一 Registry 的门面类叫 `CapabilityRegistry`，实现文件叫 `view.py`。

## 回滚（TASK-05 §6）

全部新代码在本命名空间内；不导入即等于不存在。
HTTP 面由 `CP_CAPABILITY_API_ENABLED` 控制，置 0 ⇒ `/capabilities/*` 返回 404，
平台行为与改动前完全一致。
"""

from __future__ import annotations

from .errors import (CODE_META, ERROR_CODES, RETRYABLE_CODES, CapabilityError,
                     CapabilityResult, err_result, from_exception, ok_result,
                     redact, to_llm_safe)
from .invoke import (IDENTITIES, IDENTITY_HUMAN, IDENTITY_LLM,
                     IDENTITY_SERVICE_ACCOUNT, IDENTITY_SYSTEM,
                     invoke_capability, invoke_envelope,
                     set_preauthorization_hook)
from .loader import (Handle, HttpLoader, Loader, LoaderManager, LoaderState,
                     LocalLoader, SseLoader, StdioLoader, get_loader_manager,
                     reset_loader_manager)
from .modelcaps import model_capability, supports_tool_calling
from .pruning import (PROTECTED_CONFIRM_LEVELS, PROTECTED_CONFIRM_RANK,
                      PROTECTED_RISKS, BudgetPlan, ProtectionVerdict,
                      is_prune_protected, lookup_protection, plan_token_budget,
                      prune_tool_defs_for_budget)
from .spec import CALLABLE_BY, IMPL_STATUS, CapabilityRecord, derive_callable_by
from .toolset_hash import (EXCLUDED_FIELDS, HASHED_FIELDS, RebuildDecision,
                           SessionToolset, ToolsetSnapshot,
                           compute_toolset_hash, describe_hash_scope)
from .view import (CapabilityRegistry, build_registry, get_registry,
                   reset_registry)

__all__ = [
    # spec
    "CapabilityRecord", "CALLABLE_BY", "IMPL_STATUS", "derive_callable_by",
    # registry
    "CapabilityRegistry", "build_registry", "get_registry", "reset_registry",
    # loader
    "Loader", "LoaderManager", "LoaderState", "Handle",
    "LocalLoader", "StdioLoader", "SseLoader", "HttpLoader",
    "get_loader_manager", "reset_loader_manager",
    # errors
    "ERROR_CODES", "CODE_META", "RETRYABLE_CODES", "CapabilityError",
    "CapabilityResult", "err_result", "ok_result", "from_exception",
    "redact", "to_llm_safe",
    # invoke
    "IDENTITIES", "IDENTITY_LLM", "IDENTITY_HUMAN", "IDENTITY_SYSTEM",
    "IDENTITY_SERVICE_ACCOUNT", "invoke_capability", "invoke_envelope",
    "set_preauthorization_hook",
    # modelcaps（TASK-05）
    "model_capability", "supports_tool_calling",
    # toolset_hash（TASK-08 E7）
    "HASHED_FIELDS", "EXCLUDED_FIELDS", "SessionToolset", "ToolsetSnapshot",
    "RebuildDecision", "compute_toolset_hash", "describe_hash_scope",
    # pruning（TASK-08 E8）
    "PROTECTED_RISKS", "PROTECTED_CONFIRM_LEVELS", "PROTECTED_CONFIRM_RANK",
    "ProtectionVerdict", "BudgetPlan", "is_prune_protected",
    "lookup_protection", "plan_token_budget", "prune_tool_defs_for_budget",
]
