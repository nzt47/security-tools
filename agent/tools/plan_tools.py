"""计划清单工具（todo_write）— 让模型把"现在做到哪一步"写进一个自己看得见的状态

【任务定位】
    前三个工具原语各解决一个"单步做对"的问题：``grep`` 找到内容（``search_tools.py``）、
    ``edit`` 改对片段（同文件）、``delegate`` 把子任务交出去（``subagent_tools.py``）。
    但长任务真正高频的失败模式不是单步做错，而是**中途跑偏**：做到第 6 步时已经忘了
    第 2 步定下的约束，把已完成的事重做一遍，或者收尾时漏掉最初承诺的那一项；
    审计侧看到的也只有一串工具调用，看不到阶段边界。根因是模型侧**缺一个可见的
    计划状态** —— 本模块补上这一个原语，只做"状态的搬运与呈现"，不做计划生成。

【不易（三条硬约束）】
    1. **整体替换式写入**（不是增量追加）：每次调用提交的就是**完整清单**，服务端整份
       覆盖。DSH / Claude 的同类原语也是这个语义：增量语义下模型必须精确记住"上一版
       清单长什么样"才能算出差量，一旦记错（漏一项、状态写反）就发生**状态漂移**且
       无法自愈；整体替换把"当前计划"的权威副本交回模型手里，每次调用都是一次自纠偏。
    2. **校验先于写入**：非法入参一律 ``{"ok": False, "error": ...}`` 且**不改动已存状态**
       —— 否则一次笔误就把上一份合法清单抹掉，模型连回退的锚点都没了。
    3. **内存态、按会话隔离、绝不落盘**：本仓有 ``clean-runtime-noise`` 提交钩子会还原
       运行时统计噪声，新增数据文件另有维护成本；计划清单是**短期草稿**，进程重启即失效
       是可接受的代价。会话条目数亦有界，长跑进程不因会话增长而无界膨胀。

【变易】
    schema 必须是**静态字面量**：``scripts/migrate_tools_to_yaml.py`` 用 AST ``literal_eval``
    抽取 name/description/schema（不执行工具代码），动态拼装的 schema 会被抽成空壳，
    令 ``data/tool_definitions/todo_write.yaml`` 与检索索引（``data/tool_index.json`` 的
    ``parameter_names``）缺参数信息。
    会话键取自既有 Trace 上下文（见 ``_current_session_key``），取不到即退回进程级默认键
    —— **任何情况下都不因为拿不到会话键而报错**。

【简易】
    纯标准库（``threading`` + ``collections``）：不读文件、不调 shell、不写审计、
    不做权限校验（纯内存草稿，无副作用）；异常一律在 handler 内收口为
    ``{"ok": False, "error": ...}``，绝不抛回工具循环。
"""

from __future__ import annotations

import collections
import logging
import threading
from typing import Any, List, Optional, Tuple

from agent import tools as _tools

logger = logging.getLogger(__name__)

#: 合法状态值 → Markdown 复选框标记（**键序即 counts/枚举的对外顺序**）
#:
#: ``in_progress`` 用 ``~`` 而非标准 Markdown 复选框的空格/x —— 这是刻意的：
#: "进行中"必须在**纯文本**里与"待办"可区分，否则模型读回自己写的清单时分不清
#: 哪一项正在跑（GitHub 只认空格与 x，渲染侧多一个符号没有副作用）。
_STATUS_MARKS = {
    "pending": " ",
    "in_progress": "~",
    "completed": "x",
}

#: 合法状态值（schema 的 enum 必须与本元组逐字一致，由单测守门）
_VALID_STATUSES: Tuple[str, ...] = tuple(_STATUS_MARKS)

#: 单次提交的条目上限（schema 再小也要有上界：清单是给模型自己看的，不是数据通道）
_MAX_TODOS = 50

#: 单条 ``content`` 的字符上限
_MAX_CONTENT_CHARS = 500

#: ``rendered`` 的总长度上限（超出即截断并标记）
_MAX_RENDER_CHARS = 4000

#: 会话条目上限：超出后按**最早写入**淘汰（FIFO），保证长跑进程内存有界
_MAX_SESSIONS = 32

#: 取不到会话键时的进程级默认键（CLI / 单会话 / 无 Trace 上下文场景）
_DEFAULT_SESSION_KEY = "default"

#: 截断标记（``rendered`` 超长时追加；写明完整内容仍在 ``todos`` 字段里可取）
_TRUNCATE_MARK = "\n...（清单过长，已截断至 {limit} 字符；完整内容见 todos 字段）"

#: 会话键 → 清单（元素恒为 ``{"content", "status"}``）
#:
#: ``OrderedDict`` 而非普通 dict：淘汰需要"最早写入"的顺序，普通 dict 的迭代序也是
#: 插入序，但这里把意图写在类型上，``popitem(last=False)`` 才是显式的 FIFO 语义。
#: **刻意不做 LRU**（命中/写入时都不 ``move_to_end``）：计划清单属于"最近开始的会话"，
#: 让老会话因为一次读取就续命，会让淘汰顺序变得难以推理。重写已存在的键**不改变**
#: 它的位次（第一次写入的时刻就是它的年龄）。
_STATE: "collections.OrderedDict[str, List[dict]]" = collections.OrderedDict()

#: 保护 ``_STATE`` 的锁：工具可能被并发调用（同进程多会话 / 多线程）
_STATE_LOCK = threading.Lock()


# ════════════════════════════════════════════════════════════
#  会话键 — 优先复用既有 Trace 上下文
# ════════════════════════════════════════════════════════════

def _current_session_key() -> str:
    """当前会话键：取既有 Trace 上下文的 ``subject_id``，取不到退回进程级默认键

    ``agent/orchestrator/orchestrator.py:205`` 在任务级 Trace 起点把 ``subject_id``
    写成会话 ID（``sess_*``，与 ``SessionManager`` 对齐），该 ContextVar 在整轮对话内
    有效 —— 因此同一会话的**多次工具调用**拿到同一个键，正是计划清单需要的作用域。

    三点处理：
    - **惰性导入**：``agent.observability.trace_v2`` 是较重的观测模块，工具注册发生在
      编排器初始化期，此处避免它在模块导入期就被拖进依赖链（与 ``search_tools`` 的
      惰性导入同一理由）。
    - **只用现成 accessor，绝不新建会话体系**：本模块不引入第二份会话登记表。
      （同义的公开 accessor 还有 ``agent.observability.events.trace_fields()``，
      这里直接用 ``TraceContext.current()`` 少一层转发。）
    - **绝不因取不到而报错**：无上下文 / 无属性 / 导入失败一律退化为
      ``_DEFAULT_SESSION_KEY``。拿不到会话键的最坏后果只是"清单落到一个共享默认槽位"，
      不能升级成"工具调用失败"（调用侧 ``_resolve_session_key`` 另有一层兜底，
      防的是本函数自身抛异常的情形）。
    """
    try:
        from agent.observability.trace_v2 import TraceContext  # noqa: PLC0415 惰性导入
        ctx = TraceContext.current()
        subject = str(getattr(ctx, "subject_id", "") or "").strip()
        if subject:
            return subject
    except Exception:  # noqa: BLE001 观测上下文不可用不影响写入
        pass
    return _DEFAULT_SESSION_KEY


def _reset_state() -> None:
    """清空全部会话的计划清单（**仅供测试隔离使用**）

    生产路径不需要它：清单本就是内存草稿，进程退出即随之消失。
    """
    with _STATE_LOCK:
        _STATE.clear()


def _resolve_session_key(session_key: Optional[str]) -> str:
    """把「显式键 / accessor 取回的键」归一化为**一定可用**的会话键（绝不抛异常）

    【不易】会话键解析属于**纯侧路**：它失败最多让清单落到默认槽位，
    绝不能升级为"工具调用失败"。因此这一层与 ``_current_session_key`` 内部各自兜底
    —— 单测会 monkeypatch accessor 令其抛异常，正是为了守住这条边界。
    """
    if session_key is None:
        try:
            session_key = _current_session_key()
        except Exception:  # noqa: BLE001 accessor 异常 → 退默认键（不阻断写入）
            logger.debug("[todo_write] 会话键 accessor 异常，退回默认键", exc_info=True)
            session_key = _DEFAULT_SESSION_KEY
    key = str(session_key).strip()
    return key or _DEFAULT_SESSION_KEY


# ════════════════════════════════════════════════════════════
#  校验与渲染（纯函数；不触达任何状态）
# ════════════════════════════════════════════════════════════

def _validate_todos(todos: Any) -> Tuple[Optional[List[dict]], Optional[str]]:
    """校验并归一化入参 → ``(清单, 错误信息)``，两者恰有一个为 None

    【不易】只接受 schema 声明的形状（``todos`` 数组 + 每项 ``{content, status}``）：
    多余的键**丢弃**（schema 只有这两个键，多出来的说明调用方误解了契约；丢弃保证内部
    状态形状恒定，"读回来的一定是写进去的那两个字段"）。``content`` 去首尾空白后存储。

    【不易】本函数**只判定、不写状态**：调用方拿到错误就直接返回，已存清单分毫不动。
    """
    if not isinstance(todos, list):
        return None, ("todos 必须是数组（每项形如 "
                      '{"content": "...", "status": "pending"}）')
    if len(todos) > _MAX_TODOS:
        return None, f"清单最多 {_MAX_TODOS} 项，本次提交 {len(todos)} 项"
    normalized: List[dict] = []
    for idx, item in enumerate(todos, start=1):
        if not isinstance(item, dict):
            return None, (f"第 {idx} 项不是对象（应为 "
                          '{"content": "...", "status": "pending"}）')
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            return None, f"第 {idx} 项缺少非空 content（content 必须是非空字符串）"
        content = content.strip()
        if len(content) > _MAX_CONTENT_CHARS:
            return None, (f"第 {idx} 项 content 超过 {_MAX_CONTENT_CHARS} 字符"
                          f"（实际 {len(content)} 字符）")
        status = item.get("status")
        if status not in _VALID_STATUSES:
            return None, (f"第 {idx} 项 status 非法: {status!r}；"
                          f"只能是 {'/'.join(_VALID_STATUSES)} 之一")
        normalized.append({"content": content, "status": status})
    return normalized, None


def _render(todos: List[dict]) -> str:
    """把清单渲染为 Markdown 复选框列表（``- [ ]`` / ``- [~]`` / ``- [x]``）

    为什么不只是回显 ``todos`` 数组：数组要模型自己在脑内重建"哪些还没做"，
    而 Markdown 清单是**一眼可读的阶段边界**，也便于模型原样贴进回复或审计。
    超长即截断并标记 —— 清单再长也只是给模型看的状态摘要，把上限 50×500 字符
    全灌回上下文得不偿失（``todos`` 字段仍带完整内容）。
    """
    rendered = "\n".join(f"- [{_STATUS_MARKS[t['status']]}] {t['content']}" for t in todos)
    if len(rendered) > _MAX_RENDER_CHARS:
        rendered = rendered[:_MAX_RENDER_CHARS] + _TRUNCATE_MARK.format(limit=_MAX_RENDER_CHARS)
    return rendered


# ════════════════════════════════════════════════════════════
#  写入实现体
# ════════════════════════════════════════════════════════════

def write_todos(todos: Any, session_key: Optional[str] = None) -> dict:
    """``todo_write`` 的实现体（由工具包装；测试与内部亦可直调）

    Args:
        todos: **完整**计划清单（整体替换语义，不是增量）；``[]`` 表示清空清单。
        session_key: 会话键；``None`` 时经 ``_resolve_session_key()`` 现取
            （现取失败即退默认键；显式传入用于测试隔离验证）。

    Returns:
        成功 ``{"ok": True, "todos", "counts", "rendered"}``；
        同时有 >1 项 ``in_progress`` 时额外带 ``"warning"``（**不拒绝**：模型可能确实
        在并行推进，本原语只提示"建议只保留一项"，是否收敛归模型判断）；
        失败 ``{"ok": False, "error"}``，且已存状态**不变**。
    """
    # ── 步骤 1：校验前置（零副作用；不合格就不碰状态）──
    normalized, error = _validate_todos(todos)
    if normalized is None:  # 与 error 恒同时出现；判 None 是为了让类型收窄（mypy）
        logger.warning("[todo_write] 拒绝非法清单: %s", error)
        return {"ok": False, "error": error or "todos 校验失败"}

    key = _resolve_session_key(session_key)

    # ── 步骤 2：整体替换 + 有界淘汰（同锁内完成，避免并发下"写一半被淘汰"）──
    with _STATE_LOCK:
        _STATE[key] = normalized
        while len(_STATE) > _MAX_SESSIONS:
            # FIFO：淘汰最早写入的会话（重写已存在的键不改变其位次，故这里无需 move_to_end）
            evicted, _ = _STATE.popitem(last=False)
            logger.debug("[todo_write] 会话条目超上限，淘汰最早写入的会话: %s", evicted)

    counts = {status: 0 for status in _VALID_STATUSES}
    for item in normalized:
        counts[item["status"]] += 1

    result = {
        "ok": True,
        "todos": normalized,
        "counts": counts,
        "rendered": _render(normalized),
    }
    if counts["in_progress"] > 1:
        result["warning"] = f"同时有 {counts['in_progress']} 项处于 in_progress，建议只保留一项"
    logger.info("[todo_write] 会话 %s 清单已整体替换: %d 项 %s",
                key, len(normalized), counts)
    return result


# ════════════════════════════════════════════════════════════
#  工具注册
# ════════════════════════════════════════════════════════════

def register_all(dl):
    """注册计划清单工具（``todo_write``）

    Args:
        dl: DigitalLife / LifecycleManager 实例（工具注册方传入的 self）；
            本工具是纯内存草稿，**不取 dl 的任何属性** —— 保留该形参只为与其它
            ``register_all(dl)`` 模块保持同一接线形状（归分类为 ``core``，
            见 ``agent/tool_router.py`` 的 ``_DEFAULT_TOOL_CATEGORIES``）。
    """

    @_tools.register("todo_write",
        "把当前任务的计划清单写下来（todo list / plan / 计划、待办清单）。"
        "**整体替换式**：本次提交的 todos 就是完整清单（不是增量追加），"
        "空数组 [] 表示清空。每项 {\"content\": 做什么, \"status\": "
        "\"pending\"|\"in_progress\"|\"completed\"}；建议同时只保留一项 in_progress，"
        "完成一项就把它改成 completed 再整体提交一次。"
        "多步任务每推进一步更新一次，用它当自我纠偏的锚点（长任务缺一个模型可见的"
        "计划状态，会导致多步任务中途跑偏）。状态仅保存在当前会话内存中，不落盘。"
        "Write the todo list, plan steps, task checklist",
        schema={
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "description": "完整计划清单（整体替换，不是增量追加）；空数组 [] 表示清空清单",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {
                                "type": "string",
                                "description": "该步要做什么，一句话（不超过 500 字符）",
                            },
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                                "description": ("pending 待办 / in_progress 进行中"
                                                "（建议同时只留一项）/ completed 已完成"),
                            },
                        },
                        "required": ["content", "status"],
                    },
                },
            },
            "required": ["todos"],
        })
    def _todo_write(**kwargs):
        """计划清单写入入口（参数见 schema；异常一律收口为 ok=False）"""
        try:
            return write_todos(kwargs.get("todos"))
        except Exception as e:  # noqa: BLE001 工具链路上绝不外抛异常
            logger.error("[todo_write] 写入异常: %s", e, exc_info=True)
            return {"ok": False, "error": f"写入计划清单异常: {e}"}


__all__ = ["register_all"]
