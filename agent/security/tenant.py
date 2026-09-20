"""`tenant_id` 的**服务端派生**（唯一权威；TASK-06 §3 第 4 步）

## 为什么需要这个模块

TASK-00 §0.4 与 TASK-06 §2.3 记录了同一个缺陷：

> 活的 `tenant_id` = workspace-hash，**且可由客户端在 query/body 指定**
> （`routes_ui_panels.py:286,544`）。

信任边界错在哪里：单机单用户下它恒等于 `default`，看起来无害；但一旦开启多租户，
客户端只要传别人的租户 id 就能读到/回滚别人的数据 —— 那是**跨租户越权**。

TASK-04（提交 `807401ba`）修了 `routes_ui_panels.py` 的两处。而 TASK-06 复核时发现
**TASK-05 新写的 `routes_capabilities.py` 又把同一个模式复制了三遍**
（`:156` GET query、`:230` POST body、`:276` GET query）——
这正是"修了一处、新代码又长出来"的典型：**缺的不是补丁，是一个共用的派生入口**。

## 设计（三条纪律）

1. **派生，不是常量**。生效值来自 `agent.observability.trace_v2.derive_workspace_id(os.getcwd())`
   —— 仓库**活的**租户语义就是 workspace-hash（`agent/orchestrator/orchestrator.py:211-228`：
   workspace(repository) = 逻辑租户）。TASK-06 §5 明确把"把 `tenant_id` 改成常量 `"default"`
   而不建立派生机制"列为**不通过** —— 那样"预留接入点"就是空话。
2. **客户端值只作待校验声明**。`tenant_id_with_declaration()` 把客户端传的值登记为
   `declared` 并**留痕告警**，但它**不参与任何判定**。于是既不丢失"客户端想操作哪个
   租户"的意图，又堵住了越权面。
3. **不接入 `agent/multi_tenant.py`**。它是零生产 import 的孤岛死代码（TASK-06 §5 选 A：
   保留 + 标注未接入），接入属越界。

## 为什么不新增环境变量

D5 要求新增开关必须登记 `agent/settings/registry.py`（零缺口守卫）。
本模块不需要可配置性 —— 派生规则就是"由 workspace 决定"，没有合法的第二种取值。
故**零新配置项**。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Tuple

logger = logging.getLogger(__name__)

__all__ = ["DEFAULT_TENANT_ID", "server_tenant_id", "capability_tenant_id",
           "tenant_id_with_declaration", "capability_tenant_with_declaration",
           "tenant_matches"]

#: 单机单用户下的租户占位值（与 `agent/lines/models.py::DEFAULT_TENANT_ID` 同义）
#: 【D1】值必须与那边一致；由 `tests/unit/test_tenant_isolation.py` 对拍锁死
#: （不直接 import 是为了避免 `agent.security` → `agent.lines` 的依赖边：
#: `agent.lines.callability` 已在 import `agent.security` 域的东西，反向 import 会成环）。
DEFAULT_TENANT_ID = "default"


def server_tenant_id() -> str:
    """服务端派生的 `tenant_id` —— **绝不从请求参数取**

    【派生失败怎么办】回落到 `default` 并**不抛异常**：面板/能力接口不该因为
    一次 workspace 推导失败而 500（D4 的同一取舍）。回落是**保守**的：
    它让调用方拿到"最小可见范围"的租户，而不是某个客户端想要的范围。
    """
    try:
        from agent.observability.trace_v2 import derive_workspace_id  # noqa: PLC0415
        return str(derive_workspace_id(os.getcwd()) or "") or DEFAULT_TENANT_ID
    except Exception as e:  # noqa: BLE001
        logger.debug("[tenant] workspace 派生失败（回落 %s）: %s: %s",
                     DEFAULT_TENANT_ID, type(e).__name__, e)
        return DEFAULT_TENANT_ID


def capability_tenant_id() -> str:
    """**能力平面**（Registry / 能力清单 / 审批审计 / 配额键）使用的租户键

    ## 【🔴 2026-09-20 修复的真实回归：能力平面不能直接用 workspace-hash】

    把 `routes_capabilities.py` 的三处查询改成 `server_tenant_id()`（workspace-hash）后，
    实测 `tests/unit/test_capregistry_callpaths_routes.py` **4 条变红**：
    `/capabilities/tools` 返回 **0 条**（应 114）、`describe` 对任何名字 **404**。
    根因不是过滤写错，而是**两个平面的租户值本就不同**：

    | 平面 | 租户值 | 键由谁构造 |
    |---|---|---|
    | 记忆/数据平面 | **workspace-hash**（`agent/memory/tenancy.py:343`、`orchestrator:211-228`） | 运行期派生 |
    | 能力平面 | **`default`**（`data/tool_definitions/*.yaml` 不带 `tenant_id` ⇒ `ToolMeta` 用 `DEFAULT_TENANT_ID`） | 声明期（入库） |

    ⇒ 用 workspace-hash 去查按 `default` 建键的 Registry，**必然全空**。
    这正是"同一字段在两个平面含义不同"的经典事故（也说明"服务端派生"必须
    **按平面分别定义**，不能一把梭）。

    ## 为什么能力平面**不能**改成 workspace-hash

    三个理由，任一条都足以否决：
      1. **可复现性（硬约束）**：`data/capability_manifest.json` 是**入库产物**，
         而 `tests/unit/test_tool_callability.py::TestManifest::test_清单只依赖入库数据`
         要求它只由入库数据派生。workspace-hash 依赖**本机仓库路径**
         ⇒ 把它写进清单会让清单在 CI / 他人机器上必然不一致（假失败）。
      2. **D1 单一真相源**：能力键的权威是**声明**（YAML + `lines.models`），
         不是"这台机器在哪"。把运行期路径混进声明键，等于让同一份能力在不同机器上
         有不同 `capability_id`。
      3. **TASK-06 §3 第 4 步要的是"客户端不可伪造"，不是"值必须来自 workspace"**：
         真正要堵的是"客户端传 `tenant_id=<他人工作区hash>` 就读到别人的数据"。
         现在生效值来自**服务端**（本函数），客户端值只作待校验声明 / 不一致即拒 ⇒
         越权面已经堵住。

    ## 将来接多租户时改哪里（这就是"预留接入点"的具体含义）

    只改**本函数一处**：让它在多租户部署下返回当前请求所属的租户
    （例如由 SA token 的 `tenant_id` claim 或会话身份派生），并把
    `data/tool_definitions/*.yaml` 的 `tenant_id` 声明补齐。
    Registry 键、清单字段、审计载荷、配额键**都已经带 `tenant_id` 维度**
    （`tests/unit/test_tenant_isolation.py::TestCrossTenantIsolation` 逐维验证），
    故不需要改任何键形状 —— 这正是"架构上预留接入点"要的效果。

    【与 `server_tenant_id()` 的关系】后者仍是**记忆/数据平面**的派生值
    （workspace-hash），两者**不是同一个东西**，不得互相替换
    （互换即复现上面那 4 条红）。
    """
    try:
        from agent.lines.models import DEFAULT_TENANT_ID as _declared  # noqa: PLC0415
        return str(_declared or "").strip() or DEFAULT_TENANT_ID
    except Exception as e:  # noqa: BLE001 声明层不可用 ⇒ 回落本模块常量（两者已对拍）
        logger.debug("[tenant] 能力平面租户解析失败（回落 %s）: %s: %s",
                     DEFAULT_TENANT_ID, type(e).__name__, e)
        return DEFAULT_TENANT_ID


def capability_tenant_with_declaration(declared: Any = "") -> Tuple[str, str]:
    """能力平面的 `(生效值, 客户端声明值)` —— 语义同 :func:`tenant_id_with_declaration`

    单列一个函数而不是复用后者：两者生效值的**来源平面不同**（见
    :func:`capability_tenant_id`）。混用是上面那 4 条红测试的直接原因。
    """
    effective = capability_tenant_id()
    text = str(declared or "").strip()
    if text and text != effective:
        logger.warning(
            "[tenant] 客户端指定了能力平面 tenant_id=%r，已按**服务端值** %r 处理"
            "（客户端值仅登记为待校验声明，不参与判定）", text, effective)
    return effective, text


def tenant_id_with_declaration(declared: Any = "") -> Tuple[str, str]:
    """返回 `(生效值, 客户端声明值)`（**记忆/数据平面**；生效值 = workspace-hash 派生）

    生效值**永远**来自服务端派生；客户端传的值只被登记为"待校验声明"并留痕告警，
    不参与任何判定。

    【为什么要返回声明值而不是丢掉它】两个理由：
      ① 排障：客户端"以为自己在操作租户 X"而实际生效 Y，这件事必须可见；
      ② 审计：`tenant_id_declared` 进审计载荷，使"谁试图访问别的租户"可被检出 ——
         丢掉它就等于丢掉越权**企图**的证据（越权未遂同样是安全信号）。
    """
    effective = server_tenant_id()
    text = str(declared or "").strip()
    if text and text != effective:
        logger.warning(
            "[tenant] 客户端指定了 tenant_id=%r，已按**服务端派生值** %r 处理"
            "（客户端值仅登记为待校验声明，不参与判定）", text, effective)
    return effective, text


def tenant_matches(declared: Any, effective: Any = "") -> bool:
    """客户端声明是否与生效租户一致（**供需要"显式拒绝"而非"静默纠正"的调用方**）

    两种处置都合理，取决于端点语义：
      · **纠正式**（默认，见 `tenant_id_with_declaration`）：按派生值处理 + 留痕。
        适合"客户端传了个自己都不确定的值"的读接口。
      · **拒绝式**（本函数）：声明与派生值不符 ⇒ 调用方返回 `denied`。
        适合"客户端明确声称要操作租户 X"的写接口 —— 静默纠正会让它以为写到了 X。

    TASK-06 §3 第 4 步第 1 项把两种都写进了要求：
    "客户端传入值仅作为'待校验声明'；与服务端派生值不符 ⇒ `denied`"。
    故本模块**两种都提供**，由端点按语义选择，而不是替它决定。
    """
    text = str(declared or "").strip()
    if not text:
        return True                     # 未声明 = 不构成冲突
    return text == (str(effective or "").strip() or server_tenant_id())
