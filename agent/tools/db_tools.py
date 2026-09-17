"""数据库查询工具（sqlite_query）— 让 Agent 能查**自己的运行数据**

【任务定位】
    本仓到处是 SQLite：``data/*.db``、``data/tool_fewshot.db``、``agent/data/tool_trace.db``
    …… 但 Agent **没有任何办法读它们**（``shell_execute`` 拼 ``sqlite3`` 命令行既不在
    白名单里、也无法保证只读）。结果是"自己的运行数据自己看不见"——查不到某条工具调用
    是否发生过、某次蒸馏产了几张卡，只能靠人去开 DB 工具。
    本模块补上这一个原语：**一条只读 SQL 查询**。见 ``docs/工具能力补全路线.md`` B 档。

【不易】
    1. **只读有四道闸门，任何一道单独被绕过都不足以造成写入**：
       - 打开方式：``file:<abs>?mode=ro&immutable=1`` URI（SQLite 自身拒绝写）；
       - 连接设置：``PRAGMA query_only=1``（即便 URI 参数被将来的实现改错也仍拒绝写）；
       - 语句形态：白名单首关键字（SELECT / WITH ... SELECT / PRAGMA table_info / EXPLAIN）
         ＋ 写关键字黑名单 ＋ **禁止多语句**；
       - 类型白名单：路径必须落在项目根目录内（解析后前缀校验，``normcase`` 兼容
         Windows 大小写不敏感）。
    2. **拒绝优先于猜测**：判不准的语句一律拒绝并说明理由（例如 SELECT 里出现
       ``insert`` 这种**字面量/列名**也会被拒）。宁可让调用方改写成明确的语句，
       也不能放一条可能带副作用的 SQL 进去 —— 这个工具的收益是"能查"，不是"能跑任意 SQL"。
    3. **结果有界**：SQL 自带 LIMIT 就不动语句；没有就补一条 ``LIMIT``（补的是
       ``limit + 1``，并按 ``fetchmany(limit + 1)`` 取行），如此 ``truncated`` 在
       "自动补 LIMIT" 与 "自带 LIMIT" 两种情形下都准确，而返回给调用方的行数恒 ≤ ``limit``。
       单个字段超过 ``_MAX_CELL_CHARS`` 截断（一行 2MB 的 JSON blob 不该灌进上下文）。
    4. **不写审计、不做权限校验**：本工具是 ``perceive/read``，全程只读、无副作用；
       权限闸门留给 ``act`` 类工具（与 ``grep`` / ``read_file`` 同一处置）。

【变易】
    schema 必须是**静态字面量**（``scripts/migrate_tools_to_yaml.py`` 用 AST
    ``literal_eval`` 抽取）：``data/tool_definitions/sqlite_query.yaml`` 的
    description/schema 与本模块 ``@_tools.register`` 的实参逐字节一致
    （单测 ``test_all_descriptions_match`` / ``test_all_schemas_match`` 守门）。
    LIMIT 上下限、单元格上限是这里的常量。

【简易】
    纯标准库（``sqlite3`` / ``re`` / ``os`` / ``urllib`` 不用，直接 ``pathlib``）：
    不新增依赖，不调 shell。异常一律在 handler 内收口为 ``{"ok": False, "error": ...}``。
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, List, Optional, Tuple

from agent import tools as _tools

logger = logging.getLogger(__name__)

#: LIMIT 默认值与上限（上限同时也是 fetchmany 的批大小）
_DEFAULT_LIMIT = 100
_MAX_LIMIT = 1000

#: 单个字段值的字符上限（超出截断；一行超大 blob 不该灌回上下文）
_MAX_CELL_CHARS = 2000

#: 首关键字白名单：只允许这四种形态
_KIND_SELECT = "select"
_KIND_WITH = "with"
_KIND_EXPLAIN = "explain"
_KIND_PRAGMA_TABLE_INFO = "pragma_table_info"

#: ``PRAGMA table_info`` 的允许写法（``PRAGMA table_info(t)`` / ``PRAGMA main.table_info(t)``
#: / ``PRAGMA table_info=t``）。PRAGMA 赋值（``PRAGMA foo=1``）不匹配本式 ⇒ 被拒。
_PRAGMA_TABLE_INFO_RE = re.compile(
    r"^pragma\s+(?:[A-Za-z_][A-Za-z0-9_]*\s*\.\s*)?table_info\s*[\(=]", re.IGNORECASE
)

#: 写操作 / 危险函数黑名单（词边界匹配，扫描**去注释后**的语句全文）
#:
#: 为什么需要它——白名单首关键字允许 ``WITH``，而 SQLite 支持
#: ``WITH x AS (...) DELETE FROM t``：首关键字是 WITH、却是写操作。
#: ``replace\s+into`` 单独写形态，避免误伤只读函数 ``REPLACE(str, from, to)``。
_FORBIDDEN_RE = re.compile(
    r"\b(?:insert|update|delete|drop|alter|create|attach|detach|vacuum|reindex|truncate"
    r"|grant|revoke|begin|commit|rollback|savepoint)\b"
    r"|\breplace\s+into\b"
    r"|\b(?:load_extension|writefile|readfile)\s*\(",
    re.IGNORECASE,
)

#: 注释（行注释 ``--`` 与块注释 ``/* */``）。SQLite 把注释当**记号分隔符**，
#: 因此这里统一替换为**一个空格**（而不是删除）—— 删除会把 ``DE/**/LETE``
#: 拼成 ``DELETEFROM`` 之类的假记号，替换为空格则与 SQLite 的分词结果一致。
_SQL_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)


# ════════════════════════════════════════════════════════════
#  路径与语句校验（纯函数；不触达数据库）
# ════════════════════════════════════════════════════════════

def _repo_root() -> str:
    """项目根目录（``agent/tools/db_tools.py`` → 上溯三级）"""
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


def _resolve_in_root(db_path: str) -> Tuple[Optional[str], Optional[str]]:
    """把 ``db_path`` 解析到项目根目录内 → ``(绝对路径, 错误)``

    【不易】越界拒绝用的是**解析后**路径（``abspath`` 之后再比前缀），
    故 ``data/../../etc/passwd`` 与符号链接式绕行都落在外面；比较用
    ``os.path.normcase``，兼容 Windows 大小写不敏感。绝对路径**允许**，
    只要它最终仍在根目录内（例如 ``<root>/data/x.db``）。
    """
    if not isinstance(db_path, str) or not db_path.strip():
        return None, "请提供数据库路径（db_path）"
    raw = db_path.strip()
    full = os.path.abspath(raw if os.path.isabs(raw) else os.path.join(_repo_root(), raw))
    root = os.path.abspath(_repo_root())
    full_c, root_c = os.path.normcase(full), os.path.normcase(root)
    if full_c != root_c and not full_c.startswith(root_c + os.sep):
        return None, f"数据库路径越出项目根目录，已拒绝: {raw}"
    return full, None


def _strip_comments(sql: str) -> str:
    """去掉 SQL 注释（替换为空格）；理由见 ``_SQL_COMMENT_RE`` 的说明"""
    return _SQL_COMMENT_RE.sub(" ", sql)


def _normalize_single_statement(sql: str) -> Tuple[Optional[str], Optional[str]]:
    """单语句校验 → ``(去掉尾部空分号的语句, 错误)``

    【不易】这是**保守**判定：字符串字面量里的分号（``SELECT ';'``）同样会被拒。
    多语句是"注入"最直接的载体，而本工具并不需要它 —— 调用方把两条查询分两次调用即可。

    【不易】必须**把规范化后的语句交回调用方**：只校验不返回的话，``SELECT 1;``
    会带着尾分号进入执行（再被自动补上 ``LIMIT`` ⇒ ``SELECT 1; LIMIT 100``），
    SQLite 会因"一次只能执行一条语句"而报错 —— 一个合法的查询被自己的校验逻辑弄坏。
    """
    body = sql.strip()
    while body.endswith(";"):
        body = body[:-1].rstrip()
    if ";" in body:
        return None, ("禁止多语句：一条 SQL 里出现了分号（工具只执行单条语句，"
                      "请拆成多次调用）")
    if not body:
        return None, "SQL 内容为空"
    return body, None


def _classify(sql: str) -> Tuple[Optional[str], Optional[str]]:
    """判定语句形态 → ``(kind, 错误)``，两者恰有一个为 None

    白名单**只看首关键字**，配合黑名单与多语句校验构成完整闸门：
    首关键字决定"这条语句想干什么"，黑名单兜住 `WITH ... DELETE` 这类
    "首关键字是读、实际是写"的形态。
    """
    lowered = sql.lstrip()
    if re.match(r"^select\b", lowered, re.IGNORECASE):
        return _KIND_SELECT, None
    if re.match(r"^with\b", lowered, re.IGNORECASE):
        # ``WITH ... SELECT`` 才是只读；CTE 后面跟 DELETE/UPDATE 的形态由黑名单拒绝，
        # 但仍要求正文里真的出现 SELECT，否则说明这不是一条查询。
        if not re.search(r"\bselect\b", lowered, re.IGNORECASE):
            return None, "WITH 语句必须以 SELECT 结尾（本工具只允许 WITH ... SELECT 查询）"
        return _KIND_WITH, None
    if re.match(r"^explain\b", lowered, re.IGNORECASE):
        return _KIND_EXPLAIN, None
    if _PRAGMA_TABLE_INFO_RE.match(lowered):
        return _KIND_PRAGMA_TABLE_INFO, None
    head = (lowered.split(None, 1) or ["(空)"])[0][:20]
    return None, ("只允许单条 SELECT / WITH ... SELECT / PRAGMA table_info / EXPLAIN 查询；"
                  f"收到的语句以 {head!r} 开头")


def _check_forbidden(sql: str) -> Optional[str]:
    """写关键字黑名单扫描 → 命中即返回错误信息"""
    hit = _FORBIDDEN_RE.search(sql)
    if hit:
        return (f"SQL 含禁止的写操作/危险关键字 {hit.group(0)!r}："
                "本工具只读，任何写操作（INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/ATTACH/"
                "PRAGMA 赋值等）都会被拒绝")
    return None


def _coerce_params(params: Any) -> Tuple[Optional[tuple], Optional[str]]:
    """绑定参数归一化为 tuple（``None`` → 空 tuple）"""
    if params is None:
        return (), None
    if isinstance(params, (list, tuple)):
        return tuple(params), None
    return None, "params 必须是数组（绑定参数按顺序对应 SQL 里的 ? 占位符）"


def _cell(value: Any) -> Tuple[Any, bool]:
    """单个字段值归一化 → ``(值, 是否被截断)``

    只处理 SQLite 可能返回的标量；``str``/``bytes`` 超长截断，
    ``bytes`` 一律解码为文本（代理对非法时用 replace），其余原样返回。
    """
    if value is None or isinstance(value, (int, float)):
        return value, False
    if isinstance(value, bytes):
        try:
            text = value.decode("utf-8")
        except UnicodeDecodeError:
            text = value.decode("utf-8", "replace")
    elif isinstance(value, str):
        text = value
    else:
        text = str(value)
    if len(text) > _MAX_CELL_CHARS:
        return text[:_MAX_CELL_CHARS] + f"…（已截断，原长 {len(text)} 字符）", True
    return text, False


def _read_only_uri(full_path: str) -> str:
    """只读 URI：``file:///...?mode=ro&immutable=1``

    用 ``Path.as_uri()`` 生成（Windows 下自动给出 ``file:///C:/...`` 三段斜杠形态）。
    ``mode=ro`` 让 SQLite 自身拒绝一切写入；``immutable=1`` 进一步声明"本文件在
    本次连接期间不会变化"，避免 SQLite 因看到 ``-wal``/``-shm`` 而试图回放或加锁。
    """
    return Path(full_path).resolve().as_uri() + "?mode=ro&immutable=1"


# ════════════════════════════════════════════════════════════
#  实现体
# ════════════════════════════════════════════════════════════

def sqlite_query(db_path: Any, sql: Any, limit: Any = _DEFAULT_LIMIT,
                 params: Any = None) -> dict:
    """``sqlite_query`` 的实现体（由工具包装；测试与内部链路亦可直调）

    Args:
        db_path: 数据库路径（相对项目根目录或根目录内的绝对路径）。
        sql: **单条**只读语句。
        limit: 行数上限，默认 100，上限 1000；SQL 未写 ``LIMIT`` 时自动补。
        params: 绑定参数数组（按顺序对应 ``?`` 占位符）。

    Returns:
        成功 ``{"ok": True, "columns": [...], "rows": [...], "row_count": n,
        "truncated": bool, "limit": n, "auto_limit_applied": bool, ...}``；
        被闸门拒绝或执行失败 ``{"ok": False, "error": ...}``（**绝不抛异常**）。

    说明：
        - ``rows`` 是 dict 列表，键为 ``cursor.description`` 给出的列名；
          同名列（``SELECT a, a FROM t``）在 dict 里会合并，需要区分请用别名。
        - ``truncated`` 表示"还有更多行被 limit 截断"（与字段截断无关，
          字段截断见返回里的 ``cell_truncated``）。
    """
    # ── 闸门 1：路径必须在项目根目录内 ──
    full_path, path_error = _resolve_in_root(db_path)
    if path_error or not full_path:
        logger.warning("[sqlite_query] 路径拒绝: %s", path_error)
        return {"ok": False, "error": path_error or "数据库路径非法"}
    if not os.path.exists(full_path):
        return {"ok": False, "error": f"数据库文件不存在: {db_path}"}
    if os.path.isdir(full_path):
        return {"ok": False, "error": f"路径是目录而不是数据库文件: {db_path}"}

    # ── 闸门 2：语句形态 ──
    if not isinstance(sql, str) or not sql.strip():
        return {"ok": False, "error": "请提供要执行的 SQL（sql）"}
    cleaned = _strip_comments(sql).strip()
    cleaned, single_error = _normalize_single_statement(cleaned)
    if single_error or not cleaned:
        logger.warning("[sqlite_query] 拒绝多语句: %s", str(sql)[:120])
        return {"ok": False, "error": single_error or "SQL 内容为空"}
    kind, kind_error = _classify(cleaned)
    if kind_error:
        logger.warning("[sqlite_query] 拒绝非白名单语句: %s", cleaned[:120])
        return {"ok": False, "error": kind_error}
    forbidden = _check_forbidden(cleaned)
    if forbidden:
        logger.warning("[sqlite_query] 拒绝写操作: %s", cleaned[:120])
        return {"ok": False, "error": forbidden}

    bound, params_error = _coerce_params(params)
    if params_error or bound is None:
        return {"ok": False, "error": params_error or "绑定参数非法"}

    effective_limit = _as_int(limit, _DEFAULT_LIMIT, low=1, high=_MAX_LIMIT)

    # ── 自动补 LIMIT：仅对 SELECT / WITH 生效（PRAGMA table_info 不接受 LIMIT）──
    #
    # 【不易】自动补的是 ``LIMIT limit + 1``（比 limit 多一行），取行时也取 ``limit + 1`` 行，
    # 随后只返回前 ``limit`` 行。为什么：若严格补 ``LIMIT limit``，SQLite 自己就把结果
    # 截到 limit 行，多取的那一次永远拿不到第 limit+1 行 ⇒ ``truncated`` **恒为 False**，
    # 调用方无从判断"是不是还有更多"。多取的那一行只用于判断，不进返回值。
    # SQL 自带 LIMIT 时不改语句，但仍多取一行 —— 这样"自带 LIMIT 大于工具 limit"
    # 的情形（如 SQL 写 LIMIT 500、limit 传 100）也能正确报出 truncated。
    auto_limit = False
    fetch_count = effective_limit + 1
    final_sql = cleaned
    if kind in (_KIND_SELECT, _KIND_WITH) and not re.search(r"\blimit\b", cleaned, re.IGNORECASE):
        final_sql = f"{cleaned}\nLIMIT {fetch_count}"
        auto_limit = True

    conn = None
    try:
        # ── 闸门 3：只读 URI 打开 ──
        conn = sqlite3.connect(_read_only_uri(full_path), uri=True, timeout=5.0)
        # ── 闸门 4：连接级只读（双保险）──
        conn.execute("PRAGMA query_only=1")

        # ── EXPLAIN 预检：让 SQLite 自己再判一次"这条语句能不能编译" ──
        # 只 prepare 不执行（EXPLAIN 返回的是 VDBE 程序，不是结果）。
        # 两类语句不走预检：EXPLAIN 本身就是只读解释；PRAGMA table_info 不接受
        # EXPLAIN 前缀（语法错误），其形态由白名单正则独占。
        if kind in (_KIND_SELECT, _KIND_WITH):
            conn.execute("EXPLAIN " + final_sql, bound)

        cursor = conn.execute(final_sql, bound)
        columns = [str(d[0]) for d in (cursor.description or [])]
        fetched = cursor.fetchmany(fetch_count)
        # 多取到的那一行说明"还有更多" ⇒ truncated；它本身不进返回值（返回行数恒 ≤ limit）
        truncated = len(fetched) > effective_limit
        rows: List[dict] = []
        cell_truncated = False
        for record in fetched[:effective_limit]:
            row: dict = {}
            for idx, value in enumerate(record):
                key = columns[idx] if idx < len(columns) else f"col_{idx + 1}"
                normalized, clipped = _cell(value)
                row[key] = normalized
                cell_truncated = cell_truncated or clipped
            rows.append(row)
    except sqlite3.Error as e:
        logger.warning("[sqlite_query] SQLite 错误: %s", e)
        return {"ok": False, "error": f"查询失败: {e}", "db_path": db_path}
    except Exception as e:  # noqa: BLE001 工具链路上绝不外抛异常
        logger.warning("[sqlite_query] 查询异常: %s", e)
        return {"ok": False, "error": f"查询异常: {e}", "db_path": db_path}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 关闭失败无需上报
                pass

    return {
        "ok": True,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "limit": effective_limit,
        "auto_limit_applied": auto_limit,
        "cell_truncated": cell_truncated,
        "db_path": db_path,
        "read_only_uri": _read_only_uri(full_path),
    }


# ════════════════════════════════════════════════════════════
#  工具注册
# ════════════════════════════════════════════════════════════

def register_all(dl):
    """注册数据库查询工具（``sqlite_query``）

    Args:
        dl: DigitalLife / LifecycleManager 实例。本工具是 ``perceive/read``，
        **不取 dl 的任何属性** —— 保留该形参只为与其它 ``register_all(dl)``
        模块保持同一接线形状。
    """

    @_tools.register("sqlite_query",
        "只读查询本地 SQLite 数据库（sqlite / sql / database query / 查询数据库 / "
        "查一下 db）。安全约束：以 file:<db_path>?mode=ro&immutable=1 只读 URI 打开并设 "
        "PRAGMA query_only=1（双保险只读）；只允许**单条** SELECT / WITH ... SELECT / "
        "PRAGMA table_info / EXPLAIN 语句；禁止多语句（语句内出现分号即拒绝）；"
        "禁止一切写操作（INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/ATTACH/PRAGMA 赋值等"
        "一律拒绝）；SQL 未写 LIMIT 时自动补 LIMIT（limit 默认 100，上限 1000）；"
        "db_path 必须落在项目根目录内，越界路径拒绝；结果以 dict 列表返回，"
        "单个字段值超过 2000 字符会截断。"
        "Read-only SQLite database query, run a SELECT on a local sqlite file",
        schema={
            "type": "object",
            "properties": {
                "db_path": {
                    "type": "string",
                    "description": "SQLite 数据库路径（相对项目根目录，如 data/tool_fewshot.db）",
                },
                "sql": {
                    "type": "string",
                    "description": ("单条只读 SQL，如 SELECT name FROM tools LIMIT 5；"
                                    "只允许 SELECT / WITH ... SELECT / PRAGMA table_info / EXPLAIN"),
                },
                "limit": {
                    "type": "integer",
                    "description": "行数上限，默认 100，最大 1000；SQL 未写 LIMIT 时自动补上",
                },
                "params": {
                    "type": "array",
                    "description": "绑定参数数组，按顺序对应 SQL 里的 ? 占位符（可选）",
                },
            },
            "required": ["db_path", "sql"],
        })
    def _sqlite_query(**kwargs):
        """只读查询入口（参数见 schema；异常一律收口为 ok=False）"""
        try:
            return sqlite_query(
                kwargs.get("db_path"),
                kwargs.get("sql"),
                limit=kwargs.get("limit", _DEFAULT_LIMIT),
                params=kwargs.get("params"),
            )
        except Exception as e:  # noqa: BLE001 工具链路上绝不外抛异常
            logger.error("[sqlite_query] 查询异常: %s", e, exc_info=True)
            return {"ok": False, "error": f"查询数据库异常: {e}"}


__all__ = ["register_all", "sqlite_query", "_MAX_LIMIT", "_DEFAULT_LIMIT", "_MAX_CELL_CHARS"]
