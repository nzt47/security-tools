"""app_server 认证与日志装饰器

从 app_server.py 提取，供主文件及路由模块共享。

【TASK-S4-01 身份层升级（Owner 裁定 A3「令牌 → 用户映射」，S2-02 遗留 #1）】
    既有：`require_token` 只比对**共享令牌**（`FLASK_API_TOKEN`），UI actor 只能降级为
    `ui:<remote_addr>`。本任务按裁定 A3 落地**每使用者独立令牌**：

      - 配置表 `CP_UI_TOKENS=<token>:<name>,...`（或 `CP_UI_TOKENS_FILE`）映射
        token → actor 名；命中即 `identity_source=token_map`（**权威**）；
      - `require_token` 接受**共享令牌**或**映射表内的独立令牌**；
      - 令牌校验成功后把身份写入审计上下文（`set_ui_actor`），使该请求的全部审计
        记录归因到真实 actor（与 S2-03 埋点同源，口径一致）；
      - **映射表为空时完全回退既有行为**：未配置任何令牌 ⇒ 不校验（与升级前逐字
        一致）；仅配置共享令牌 ⇒ 只认共享令牌。**新机制绝不导致后台无法审批**。

    【不做】完整 session 登录体系（A1）、不信任反代注入头（A2）。
    actor 解析层可替换（`agent/security/identity.py::set_resolver`），供 P5 升级。

【配置（.env / 环境变量）】
    FLASK_API_TOKEN      共享 API 令牌（既有；保持原语义）
    CP_UI_TOKENS         每使用者令牌映射表（裁定 A3）
    CP_UI_TOKENS_FILE    映射表文件路径（每行一条）

【安全纪律】
    令牌原文**绝不**进入日志/审计/异常消息；只出现 sha256 指纹（`tok_...`）。
"""

import functools
import logging
import os
import secrets
from typing import Any, Dict, Optional, Tuple

from flask import request, jsonify

from agent.security.identity import (
    SRC_TOKEN_MAP,
    ResolvedIdentity,
    extract_bearer_token,
    resolve_identity,
)

logger = logging.getLogger(__name__)

# ── API 令牌 ──
# 【2026-10-01 修正语义】`_API_TOKEN` / `_API_TOKEN_ENABLED` 是**导入期快照**，
#   仅用于：启动日志、以及既有代码/端点上报「启用了没」这类**状态展示**。
#   **判定一律走运行期** `current_api_token()`（见 `authorize_token`）。
#   【此前的缺陷】把"是否启用"也交给这个导入期常量 ⇒ 进程启动时无令牌、之后经 `.env`
#   热重载填入令牌后，`authorize_token` 仍走 `SRC_NO_TOKEN_CONFIGURED` 的 fail-open，
#   实测 `authorize_token("totally-wrong")` 返回 `ok=True` —— **配了令牌却完全不校验**。
_API_TOKEN = os.environ.get("FLASK_API_TOKEN", "")
_API_TOKEN_ENABLED = bool(_API_TOKEN)

#: 【测试专用显式旁路钩子】把"停用共享令牌"这件事独立出来，不要再借 `_API_TOKEN_ENABLED`。
#:
#: 【为什么必须拆开】此前测试用 `monkeypatch.setattr(sa, "_API_TOKEN_ENABLED", False)` 模拟
#:   "未配置令牌"。于是同一个变量同时承担了两种语义：**导入期状态**（生产判定用）与
#:   **测试旁路**（开关用）。两种语义挤在一起，正是上面那个热重载 fail-open 的成因 ——
#:   让"改一个测试开关"和"改生产鉴权语义"变成了同一个动作。
#:   拆开后：生产只看运行期环境；测试用这个钩子，名字自解释、不可能被误用到生产路径。
_AUTH_DISABLED_FOR_TEST = False


def auth_disabled_for_test() -> bool:
    """是否被测试显式停用共享令牌（生产恒为 False）。"""
    return bool(_AUTH_DISABLED_FOR_TEST)

#: 身份来源口径：共享令牌（无用户区分）
SRC_SHARED_TOKEN = "shared_token"
#: 未配置任何令牌 ⇒ 不校验（既有行为）
SRC_NO_TOKEN_CONFIGURED = "no_token_configured"


def current_api_token() -> str:
    """当前共享令牌（运行期读环境变量，回退导入期取值）

    Why 运行期读：`.env` 由 `EnvConfigManager` 热重载（写入即更新 `os.environ`），
    导入期固化会让热更新失效。
    """
    return str(os.environ.get("FLASK_API_TOKEN", "") or _API_TOKEN or "")


def _candidate_tokens() -> list:
    """按优先级返回请求携带的令牌**候选**（`Authorization` 优先，其次 `X-API-Token`），去重保序。

    【为什么从"取一个"改成"取一串"】见 `authorize_request()` 的说明：管理后台的会话令牌
    也放在 `Authorization: Bearer` 里，两者会互相顶掉，必须允许回退。
    """
    out = []
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        t = auth_header[7:].strip()
        if t:
            out.append(t)
    x = str(request.headers.get("X-API-Token", "") or "").strip()
    if x and x not in out:
        out.append(x)
    return out


def _bearer_or_header_token() -> str:
    """从请求取**首选**令牌原文（保留旧签名：既有调用方仍只需一个值）。"""
    candidates = _candidate_tokens()
    return candidates[0] if candidates else ""


def authorize_request() -> Tuple[bool, str, str]:
    """校验请求携带的**任一**令牌：`Authorization` 优先，失败则回退 `X-API-Token`。

    【为什么必须回退 —— 实测缺陷】管理后台的会话令牌**也**写在 `Authorization: Bearer`
    （见 yunshu-ui/src/utils/request.ts:86 与 plugins/admin_api.py 的 `_token_username`）。
    旧实现"Authorization 存在就只看它、不认 X-API-Token"，于是 `enforce_all` 下：
      · 管理后台把**合法的管理会话令牌**放进 Authorization
      · 网关把该令牌当成"无效的 API 令牌"**拒掉**
      ⇒ **整个管理后台 401 不可用**（2026-10-01 实测：带管理令牌的 /api/user/info 一律 401）。
    前端因此无法同时满足两层：这是**必须先修的结构性冲突**，而不是前端写法问题。

    【为什么不削弱安全性】两个头携带的是**同一份**共享密钥或**同一张** token_map，
    `authorize_token()` 的校验逻辑与常量时间比较一字未动。回退只让"Authorization 另有用途"
    这一合法场景通过，**不会**让任何未经校验的请求通过：没有候选令牌时仍按空令牌走
    `authorize_token("")`（保持"完全未配置令牌 ⇒ 放行"的既有语义不变）。
    """
    candidates = _candidate_tokens()
    if not candidates:
        return authorize_token("")
    result = (False, "", "denied")
    for tok in candidates:
        result = authorize_token(tok)
        if result[0]:
            return result
    return result


def token_equal(presented: str, expected: str) -> bool:
    """常量时间令牌比较（**按字节**比较，对非 ASCII 安全）。

    【为什么不能直接 secrets.compare_digest(str, str)】对**含非 ASCII 字符**的 str，
    它抛 `TypeError: comparing strings with non-ASCII characters is not supported`。
    本仓该异常曾被鉴权闸门的 `except Exception: return None`（fail-open，见
    app_server.py 的 _api_auth_gate）**吞掉**，后果是：

        2026-10-01 实测（CP_API_AUTH_MODE=enforce_all）：
          GET /api/status  +  Authorization: Bearer <含 é 的任意串>   ->  200 + 完整响应体
          GET /api/status  +  X-API-Token: <含 é 的任意串>            ->  200 + 完整响应体
        对照：无令牌 -> 401；合法令牌 -> 200。

    ⇒ **任何在令牌头里塞一个非 ASCII 字节的请求，都会被当成通过**，
    即一次**完整的远程鉴权绕过**（不是"降级"或"弱校验"）。
    改成 bytes 比较后，非 ASCII 令牌只是"不相等"，走正常的 401 分支。

    【为什么吞掉自身异常】比较函数**绝不允许**抛异常：调用方（尤其闸门）若把它包在
    fail-open 的 try 里，任何异常都会变成放行。这里一律返回 False（=不通过）。
    """
    a = str(presented or "")
    b = str(expected or "")
    if not a or not b:
        return False
    try:
        return secrets.compare_digest(a.encode("utf-8", "surrogatepass"),
                                      b.encode("utf-8", "surrogatepass"))
    except Exception:  # noqa: BLE001 比较本身不得抛（见上）
        return False


def authorize_token(token: str) -> Tuple[bool, str, str]:
    """校验令牌 → (是否通过, actor, identity_source)

    规则（顺序即权限顺序）：
      1. 共享令牌匹配 ⇒ 通过（actor 留空，交由后续身份解析降级）；
      2. 映射表命中 ⇒ 通过 + **真实 actor**（`token_map`）；
      3. 二者皆未启用 ⇒ 通过（**既有行为**：未配置令牌即不校验）；
      4. 其余 ⇒ 拒绝。

    【2026-10-01 修正：共享令牌是否启用改为**运行期判定**】
    · 此前：`shared = current_api_token() if _API_TOKEN_ENABLED else ""`，
      `_API_TOKEN_ENABLED` 是导入期快照 ⇒ 启动时无令牌、事后经 `.env` 热重载填入令牌，
      `shared` 仍是空串，函数落到第 3 条「二者皆未启用 ⇒ 通过」⇒ **配了令牌却放行一切**。
      实测（复现脚本）：热重载后 `authorize_token("totally-wrong")` → `ok=True, source=no_token_configured`。
    · 现在：只看**此刻**环境里有没有令牌。这对运营动作是硬要求 ——
      "编辑 `.env` 轮换令牌"必须**立刻**生效，否则运维以为已强制校验、实际闸门全开且**静默**。
    · 测试旁路改用显式钩子 `_AUTH_DISABLED_FOR_TEST`（`auth_disabled_for_test()`），
      不再借 `_API_TOKEN_ENABLED`，避免"测试开关"与"生产语义"再被混为一谈。
    """
    presented = str(token or "")
    shared = "" if _AUTH_DISABLED_FOR_TEST else current_api_token()
    if shared and token_equal(presented, shared):
        return True, "", SRC_SHARED_TOKEN
    from agent.security.identity import current_token_map
    token_map = current_token_map()
    if presented and not token_map.empty:
        entry = token_map.lookup(presented)
        if entry is not None:
            return True, entry.actor, SRC_TOKEN_MAP
    if not shared and token_map.empty:
        # 未配置任何令牌：与升级前逐字一致（不校验）
        return True, "", SRC_NO_TOKEN_CONFIGURED
    return False, "", "denied"


def path_is_allowlisted(path: str, allowlist) -> bool:
    """豁免路径判定：**默认精确匹配**，只有显式以 /* 结尾才匹配其子路径。

    【为什么不能用 startswith 前缀匹配（首版缺陷，安全审计实测发现）】
    首版为 any(path.startswith(p) for p in allowlist)，于是默认豁免项 "/api/health"
    把 **/api/health/weights（PUT，改健康度权重）** 与
    **/api/health/score/calculate（POST）** 一并豁免 —— 而这两条恰恰**没有任何
    鉴权装饰器**（agent/server_routes/routes_health.py:244 / :173）
    ⇒ 逐路由装饰器与全局豁免**同时失效**，且**切到 enforce 也拦不住**。
    子路径豁免必须显式书写（如 "/api/health/*"），避免"加一个前缀 = 放开一片写接口"。

    放在本模块而非 app_server：本模块可被单测直接导入（app_server 导入期会构造引擎）。
    """
    for raw in allowlist or ():
        p = str(raw or "").strip()
        # 【2026-10-01 修：根路径此前无法豁免】
        #   原实现无条件 `rstrip("/")`，于是豁免项 "/" 被归一成空串并在下一行 `continue`
        #   被**静默丢弃** ⇒ 根路径永远进不了豁免集。
        #   平时无感（enforce 不覆盖 GET），但 `enforce_all` 档会连 `GET /` 一起拦 ——
        #   而生产的页面壳正挂在 `/` ⇒ **浏览器连壳和 JS 都加载不出来，
        #   用户根本进不到"输入令牌"的界面**（鸡生蛋）。实测已复现：
        #   enforce_all 下 `GET /`=401、`GET /chat`=401、`GET /static/js/*`=401。
        #   修法：只对"/"保留原样，其余仍按原口径去尾斜杠（不改既有语义）。
        if p != "/":
            p = p.rstrip("/")
        if not p:
            continue
        if p.endswith("/*"):
            base = p[:-2]
            if path == base or path.startswith(base + "/"):
                return True
        elif path == p:
            return True
    return False


#: 逐路由鉴权装饰器在**被包装函数上留下**的标记属性。
#:
#: 【为什么需要它：让"这个视图是否受令牌保护"成为可运行期查询的事实】
#:   在它之前，唯一能回答该问题的办法是**读源码**（AST 扫描）或用错误的令牌去探。
#:   前者会漏（插件经 plugin_api 延迟包装、装饰器定义散在 5 处），后者有副作用。
#:   2026-10-03 实测到它的必要性：`CP_API_AUTH_ALLOW` 里写着 `/api/diagnostics/metrics`，
#:   于是「豁免清单」与「任务/文档描述」都认定该端点无需令牌；而它其实带
#:   `@require_token`（routes_logging.py:828），活体实测 **401**。
#:   —— 豁免条目**被装饰器遮蔽**，不产生任何效果，却让所有读清单的人得出相反结论。
#:
#: 【纪律·与 GUARD_MARKERS 同级】**新增任何鉴权装饰器都必须设置本属性**，
#:   否则 find_shadowed_exemptions 会静默失明（与"新装饰器忘登记 GUARD_MARKERS"
#:   是同一类失效）。由 tests/unit/test_shadowed_exemption.py 的元守卫机械保证。
#:   经 functools.wraps 包装的装饰器**无需**显式复制：wraps 走
#:   `wrapper.__dict__.update(wrapped.__dict__)`，故标记会随包装链自动向上传递
#:   （@require_token 在 @log_request 内/外两种嵌套顺序都成立，已实测）。
REQUIRES_TOKEN_ATTR = "__requires_api_token__"


def is_token_guarded(view_fn) -> bool:
    """该视图函数是否带逐路由令牌装饰器（读标记属性，非源码扫描）。"""
    return bool(getattr(view_fn, REQUIRES_TOKEN_ATTR, False))


def find_shadowed_exemptions(allowlist, guarded_rules) -> list:
    """返回「豁免清单覆盖到、但**已被装饰器遮蔽**的端点」—— 影子豁免的检出点。

    【解决什么】豁免清单（`CP_API_AUTH_ALLOW`）与逐路由装饰器是**两套独立策略**，
    同一条路径可以同时命中两者。此时生效的是**装饰器**（它跑在视图里，闸门放行与否
    都拦不住它）⇒ 豁免条目**不产生任何效果**。

    这不危险（没有多放开任何东西），但**极具误导性**：它让
      · 读豁免清单的人以为"该端点无需令牌"；
      · 读文档/做审计的人据此推断"某页面可以 tokenless 消费它"；
      · 排障时按"它应该不需要令牌"去查错方向。
    本仓已有先例：H-5 的反面形态（漏装饰器 **且** 被豁免）靠
    find_allowed_write_endpoints 检出；本函数补的是**另一半**。

    【2026-10-03 实测】本机 `.env` 的 13 条豁免里，`/api/diagnostics/metrics`
    同时带 `@require_token`：活体 `GET` 无令牌返回 **401**（静态 AST 扫描独立得出同一结论）。
    它此前被记为「最后一个为了 tokenless 页面而保留的只读豁免」——该描述与事实不符。

    【与 find_allowed_write_endpoints 的分工】
      · 那个查「豁免放开了本该受保护的写端点」= **多放开了**（安全洞，H-5）；
      · 本函数查「豁免对已受保护的端点毫无作用」= **没放开还宣称放开了**（认知洞，H-7 同族）。
    两者都要，缺一面就会得出错误结论。

    Args:
        allowlist: 当前生效的豁免清单（字符串可迭代）
        guarded_rules: 可迭代的 (rule_string, methods)，**仅包含视图函数带令牌装饰器的规则**；
            典型来自 app.url_map.iter_rules() 经 is_token_guarded 过滤

    Returns:
        ["<path> (METHOD/METHOD)", ...]（按路径排序）；空列表 = 无影子豁免

    【为什么不报告 HEAD/OPTIONS】Flask 为每条规则自动补这两个方法
    （`provide_automatic_options`），它们对"这条豁免说明了什么"没有信息量，
    列出来只会让输出变长、真信号被淹。
    """
    auto = {"HEAD", "OPTIONS"}
    hits = []
    for rule, methods in guarded_rules:
        path = str(rule)
        if not path_is_allowlisted(path, allowlist):
            continue
        m = sorted({str(x).upper() for x in (methods or ())} - auto)
        hits.append(path if not m else f"{path} ({'/'.join(m)})")
    return sorted(hits)


def find_allowed_write_endpoints(allowlist, rules) -> list:
    """返回「豁免清单覆盖到的**变更型**端点」—— 审计 H-5 的机制化检出点。

    【为什么必须有这个函数】本模块 :205-211 已记录过一次同形事故：豁免项
    "/api/health" 的前缀匹配把 PUT /api/health/weights 与
    POST /api/health/score/calculate 一并放开。2026-10-03 审计 H-5 是**第二次**：
    /api/replay/upload 因「sendBeacon 无法携带自定义请求头」被整条豁免，
    而它自身又漏了 @require_token ⇒ 一个**写端点**完全无鉴权。
    两次成因完全相同：**看装饰器的人看不到豁免清单，看豁免清单的人不知道哪条是写路由。**
    故把两者求交，做成一个可被单测直接导入的纯函数。

    Args:
        allowlist: 当前生效的豁免清单（字符串可迭代）
        rules: 可迭代的 (rule_string, methods)；通常来自 app.url_map.iter_rules()

    Returns:
        ["<path> (METHOD/METHOD)", ...]（按路径排序）；空列表 = 无风险
    """
    mutating = {"POST", "PUT", "DELETE", "PATCH"}
    hits = []
    for rule, methods in rules:
        m = sorted({str(x).upper() for x in (methods or ())} & mutating)
        if not m:
            continue
        path = str(rule)
        if path_is_allowlisted(path, allowlist):
            hits.append(f"{path} ({'/'.join(m)})")
    return sorted(hits)


def require_token(f):
    """需要 API 令牌认证的装饰器（支持共享令牌 + 每使用者独立令牌）"""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        ok, actor, source = authorize_request()
        if not ok:
            logger.warning(
                "[Auth] 令牌校验失败 path=%s source=%s（未记录令牌原文）",
                request.path, source)
            return jsonify({"error": "未授权：缺少或无效的 API 令牌"}), 401
        if actor:
            _bind_identity(actor, source)
        return f(*args, **kwargs)
    # 【必须在 wraps 之后设置】wraps 会 `wrapper.__dict__.update(wrapped.__dict__)`，
    #   先设会被覆盖；后设则本标记成为最终视图函数上的可见事实，
    #   且外层再套 log_request 之类的 wraps 装饰器时标记继续向上传递。
    setattr(decorated, REQUIRES_TOKEN_ATTR, True)
    return decorated


def _bind_identity(actor: str, source: str) -> None:
    """把**已认证身份**暴露给本请求的后续代码（best-effort：失败不影响请求）

    【为什么只写 `flask.g`，而不写审计 ContextVar】
        `agent.audit.facade.set_ui_actor` 写的是 **ContextVar**，必须配一次复位，
        否则会泄漏到此后所有同线程请求（本任务全量回归实测：泄漏后
        `audit.facade.resolve_actor()` 把无 actor 的审计记录误归因到上一个请求的用户，
        2 例既有 `test_audit_facade` 用例因此失败）。而复位的唯一正确挂点是
        `teardown_request`（`after_this_request` 早于 `after_request`，会把 actor
        清在审计落账之前），Flask 3 又**禁止**在首个请求之后注册 teardown 钩子
        （实测报错：*The setup method 'teardown_request' can no longer be called on
        the application*）。故此处**刻意不写 ContextVar**：

        - 全局 UI 写路由的 actor 归因由 S2-02 的 `ui_middleware` 承担：其
          `before_request` 调 `resolve_ui_actor`（已统一走令牌映射表 ⇒ Bearer /
          `X-API-Token` 命中的真实 actor 自然生效），`teardown_request` 由它自己复位，
          生命周期闭合；
        - 路由内需要身份时读 `resolve_request_identity()`（本函数写入的 `g` 是最高
          优先来源）；审批动作即以该身份构造 `ActorContext`。

        回归守护：`test_s4_01_server_auth.py::test_authenticated_actor_is_exposed_to_routes`
        与 `test_no_ui_actor_contextvar_leak`。
    """
    try:
        from flask import g
        g._cp_identity = {"actor": actor, "identity_source": source}
    except Exception as e:  # noqa: BLE001 上下文绑定失败不得影响请求
        logger.debug("[Auth] 身份上下文绑定失败: %s", e)


def auth_status() -> Dict[str, Any]:
    """**当前鉴权配置状态**（只读；供 `/api/status` 与启动告警共用，TASK-06 §3 第 5 步）

    【为什么要暴露它】本部署实测**未配置任何令牌**（`.env` 未设 `CP_UI_TOKENS`、
    `FLASK_API_TOKEN` 亦为空）⇒ `authorize_token()` 走 `SRC_NO_TOKEN_CONFIGURED`
    分支**直接放行**（fail-open）。这件事此前**只能靠读代码发现**：
    没有任何端点或日志把它说出来 ⇒ 管理员会以为"端点已经鉴权了"。
    TASK-06 §3 第 5 步第 2 项要求"启动时明确告警 + 健康面暴露状态（**不阻断**）"，
    本函数是那两处的**唯一判据来源**（D1：不新建第二份口径 —— 判据复用
    `authorize_token()` 的同一组常量与同一套取值规则）。

    【为什么只读、不参与判定】迁移第 ① 步刻意**不改任何判定**：改成 fail-closed
    会当场 401 掉本机 UI 与全部脚本（E12 明确禁止）。状态暴露让"当前是开放的"
    变得可见，收口留到第 ④ 步（见 `docs/rfc/鉴权迁移.md`）。

    Returns:
        ``{"configured": bool, "source": str, "shared_token": bool, "token_map": bool,
        "token_map_size": int, "require_authoritative": bool, "note": str}``
        任何一步取不到值都**不抛异常**（降级为 ``configured=False`` 并写明原因）。
    """
    shared = False
    token_map_size = 0
    try:
        # 【2026-10-01】状态上报必须反映**运行期真相**：
        #   此前读导入期 `_API_TOKEN_ENABLED`，热重载配上令牌后本端点仍报
        #   `shared_token: false / configured: false` —— **状态端点在说谎**，
        #   而它正是运维用来判断"到底有没有启用鉴权"的地方。
        shared = bool(current_api_token())
    except Exception:  # noqa: BLE001 读环境变量失败 ⇒ 按"未配置"（更保守）
        shared = False
    try:
        from agent.security.identity import current_token_map
        tm = current_token_map()
        token_map_size = 0 if tm.empty else len(tm)
    except Exception:  # noqa: BLE001
        token_map_size = 0
    configured = bool(shared or token_map_size)
    try:
        require_auth = str(os.environ.get(
            "CP_APPROVAL_REQUIRE_AUTHORITATIVE", "") or "").strip().lower() in (
                "1", "true", "yes", "on")
    except Exception:  # noqa: BLE001
        require_auth = False
    return {
        "configured": configured,
        "source": SRC_SHARED_TOKEN if shared else (
            SRC_TOKEN_MAP if token_map_size else SRC_NO_TOKEN_CONFIGURED),
        "shared_token": shared,
        "token_map": bool(token_map_size),
        "token_map_size": int(token_map_size),
        "require_authoritative": require_auth,
        "note": ("" if configured else
                 "**未配置任何令牌 ⇒ 端点不做校验（fail-open）**；"
                 "迁移路径见 docs/rfc/鉴权迁移.md"),
    }


def resolve_request_identity(*, session_id: str = "") -> ResolvedIdentity:
    """解析当前请求的执行体身份（**路由侧唯一入口**）

    优先级：**已认证令牌的 actor**（映射表命中）> 身份头/Cookie > 令牌指纹 >
    `ui:<remote_addr>` 降级。返回 `ResolvedIdentity`（含 `degraded` / `authority`），
    供审批矩阵与审计共同使用（口径一致）。
    """
    headers = dict(request.headers)
    cookies = dict(request.cookies)
    remote_addr = request.remote_addr or ""
    token = _bearer_or_header_token()
    claimed: Dict[str, Any] = {}
    try:
        from flask import g
        claimed = getattr(g, "_cp_identity", None) or {}
    except Exception:  # noqa: BLE001 无请求上下文
        claimed = {}
    actor = str(claimed.get("actor") or "")
    identity = resolve_identity(
        actor=actor, token=token, headers=headers, cookies=cookies,
        remote_addr=remote_addr, session_id=session_id)
    return identity


def log_request(show_body=True, show_response=True):
    """接口日志装饰器"""
    def decorator(f):
        @functools.wraps(f)
        def decorated(*args, **kwargs):
            import time
            start_time = time.time()
            endpoint = f.__name__

            logs = []
            logs.append(f"[REQUEST] 接口: {endpoint}")
            logs.append(f"[REQUEST] 方法: {request.method}")
            logs.append(f"[REQUEST] 路径: {request.path}")
            logs.append(f"[REQUEST] 查询参数: {dict(request.args)}")

            if show_body and request.method in ['POST', 'PUT', 'PATCH']:
                try:
                    body = request.get_json() if request.is_json else request.form.to_dict()
                    body_str = str(body)[:200] + ('...' if len(str(body)) > 200 else '')
                    logs.append(f"[REQUEST] 请求体: {body_str}")
                except Exception:
                    logs.append(f"[REQUEST] 请求体: 无法解析")

            try:
                response = f(*args, **kwargs)
                response_time = (time.time() - start_time) * 1000
                logs.append(f"[RESPONSE] 状态码: {response[1] if isinstance(response, tuple) else 200}")
                logs.append(f"[RESPONSE] 耗时: {response_time:.2f}ms")
                if show_response:
                    resp_body = response[0].get_data(as_text=True) if isinstance(response, tuple) and hasattr(response[0], 'get_data') else str(response)[:200]
                    logs.append(f"[RESPONSE] 内容: {resp_body[:200]}")
                logger.info("\n".join(logs))
                return response
            except Exception as e:
                response_time = (time.time() - start_time) * 1000
                logger.error("[ERROR] 接口 %s 异常: %s (耗时: %.2fms)", endpoint, e, response_time)
                raise
        return decorated
    return decorator


__all__ = [
    "require_token", "log_request", "authorize_token", "current_api_token",
    "resolve_request_identity", "extract_bearer_token",
    "SRC_SHARED_TOKEN", "SRC_NO_TOKEN_CONFIGURED",
]
