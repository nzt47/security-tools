"""
数据处理工具集 -- JSON/YAML 查询、转换、验证与格式检测

我是云枢的"数据之手"——提供对 JSON、YAML、CSV、XML 等数据格式的处理能力。
"""

import json
import logging
import csv
import io
import re

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════════════════
#  JSONPath 轻量实现
# ════════════════════════════════════════════════════════════════════════════════


def _parse_jsonpath(path: str) -> list:
    """将 JSONPath 表达式解析为 token 列表。

    支持的语法：
      - `$` → 根节点
      - `.key` → 属性访问
      - `[n]` → 数组索引
      - `[*]` → 数组通配
      - `..key` → 递归下降

    Token 类型:
      - ("root", None) — 根节点
      - ("dot", "key") — 点记法键名
      - ("index", int) — 数组索引
      - ("wildcard", None) — 数组通配
      - ("recursive_descent", "key") — 递归下降搜索

    Args:
        path: JSONPath 表达式字符串

    Returns:
        token 列表，每个元素为 (类型, 值)

    Examples:
        $.store.book[0].title
        $..author
        $.items[*].name
    """
    if not path or not path.startswith("$"):
        raise ValueError(f"JSONPath 必须以 $ 开头，收到: {path!r}")

    tokens = []
    # 去掉开头的 $
    remaining = path[1:]

    # 正则一次匹配一个 token
    # 四个分支按优先级：.. → . → [n|*] → 键名（不含 . [ ] 的字符序列）
    token_pattern = re.compile(
        r"""
        \.\.
        |
        \.
        |
        \[(\d+|\*)\]
        |
        ([^.\[\]]+)
        """,
        re.VERBOSE,
    )

    pos = 0
    while pos < len(remaining):
        m = token_pattern.match(remaining, pos)
        if not m:
            raise ValueError(
                f"JSONPath 解析失败，位置 {pos}: {remaining[pos:]!r}"
            )

        matched = m.group(0)
        if matched == ".":
            # 后面应跟着键名
            pos = m.end()
            key_match = token_pattern.match(remaining, pos)
            if key_match:
                key_text = key_match.group(0)
                # 确保捕获的是键名，不是另一个操作符
                if key_text not in (".", "") and not key_text.startswith("["):
                    tokens.append(("dot", key_text))
                    pos = key_match.end()
                    continue
            raise ValueError(
                f"JSONPath 中 '.' 后缺少键名，位置 {pos}: {remaining[pos:]!r}"
            )

        elif matched == "..":
            # 递归下降：后面应跟着键名
            pos = m.end()
            key_match = token_pattern.match(remaining, pos)
            if key_match:
                key_text = key_match.group(0)
                if (
                    key_text not in ("..", ".", "")
                    and not key_text.startswith("[")
                ):
                    tokens.append(("recursive_descent", key_text))
                    pos = key_match.end()
                    continue
            raise ValueError(
                f"JSONPath 中 '..' 后缺少键名，位置 {pos}: {remaining[pos:]!r}"
            )

        elif matched.startswith("["):
            inner = m.group(1)  # 括号内的内容
            if inner == "*":
                tokens.append(("wildcard", None))
            elif inner.isdigit():
                tokens.append(("index", int(inner)))
            else:
                raise ValueError(f"不支持的 JSONPath 语法: {matched}")
            pos = m.end()

        else:
            # 直接匹配到的键名（路径首段的键名或递归下降后的键名）
            tokens.append(("dot", matched))
            pos = m.end()

    # 为简化后续处理，在开头确保有 root 概念
    if not tokens:
        # 只有 $，没有后续
        tokens.append(("root", None))
    elif tokens[0][0] not in ("root",):
        # 非 $ 起始 token 序列需要在逻辑上前置 root
        # 实际上所有路径都以 $ 开头，所以 tokens[0] 必然是后续段落
        # 插入虚拟 root 以便统一逻辑
        tokens.insert(0, ("root", None))
    else:
        # 第一个 token 已经是 root
        pass

    return tokens


def _recursive_descent_search(data, key: str) -> list:
    """递归搜索整个数据结构中所有键名为 key 的值。

    Args:
        data: Python 对象（dict/list/其他）
        key: 要搜索的键名

    Returns:
        所有匹配值的列表
    """
    results = []

    if isinstance(data, dict):
        if key in data:
            results.append(data[key])
        for v in data.values():
            results.extend(_recursive_descent_search(v, key))
    elif isinstance(data, list):
        for item in data:
            results.extend(_recursive_descent_search(item, key))

    return results


def _walk_jsonpath(data, tokens: list) -> list:
    """按照 token 列表在数据结构中遍历，返回匹配到的所有值。

    Args:
        data: Python 对象
        tokens: _parse_jsonpath 返回的 token 列表

    Returns:
        匹配值的列表
    """
    if not tokens:
        return [data]

    current_set = [data]

    for i, (ttype, tval) in enumerate(tokens):
        next_set = []

        if ttype == "root":
            # root token：当前值不变
            # 但如果后续还有 token，root 只是一个标记
            continue

        if ttype == "recursive_descent":
            # 递归下降：对当前集合中的每一项递归搜索
            for item in current_set:
                found = _recursive_descent_search(item, tval)
                next_set.extend(found)
            current_set = next_set
            continue

        # dot / index / wildcard：对当前集合中的每一项执行访问
        for item in current_set:
            if item is None:
                continue

            if ttype == "dot":
                if isinstance(item, dict) and tval in item:
                    next_set.append(item[tval])

            elif ttype == "index":
                if isinstance(item, list) and 0 <= tval < len(item):
                    next_set.append(item[tval])

            elif ttype == "wildcard":
                if isinstance(item, list):
                    next_set.extend(item)
                elif isinstance(item, dict):
                    next_set.extend(item.values())

        current_set = next_set

        if not current_set:
            break

    return current_set


def json_query(data, path: str) -> dict:
    """使用 JSONPath 查询 JSON 数据。

    支持的 JSONPath 语法：
      - `$` — 根节点
      - `.key` — 属性访问（如 `.name`, `.address.city`）
      - `[n]` — 数组索引（如 `[0]`, `[1]`）
      - `[*]` — 数组通配（所有元素）
      - `..key` — 递归下降搜索（查找所有名为 key 的属性）

    Args:
        data: JSON 字符串，或 Python 对象（dict/list）
        path: JSONPath 表达式

    Returns:
        {"ok": True, "data": [匹配结果列表], "count": 匹配数量}
        或 {"ok": False, "error": "..."}

    Examples:
        json_query('{"store":{"book":[{"title":"A"},{"title":"B"}]}}', '$.store.book[0].title')
        → {"ok": True, "data": ["A"], "count": 1}

        json_query('{"a":{"b":1},"c":{"b":2}}', '$..b')
        → {"ok": True, "data": [1, 2], "count": 2}
    """
    try:
        # 1. 如果 data 是字符串，先解析为 Python 对象
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError as e:
                return {"ok": False, "error": f"JSON 解析失败: {e}"}

        # 2. 验证 data 类型
        if not isinstance(data, (dict, list)):
            return {
                "ok": False,
                "error": f"数据必须是 JSON 对象或数组，收到类型: {type(data).__name__}",
            }

        # 3. 解析并执行 JSONPath
        if path == "$":
            return {"ok": True, "data": [data], "count": 1}

        tokens = _parse_jsonpath(path)
        results = _walk_jsonpath(data, tokens)

        return {"ok": True, "data": results, "count": len(results)}

    except ValueError as e:
        return {"ok": False, "error": f"JSONPath 语法错误: {e}"}
    except Exception as e:
        logger.error("json_query 异常: %s", e)
        return {"ok": False, "error": f"查询失败: {e}"}


# ════════════════════════════════════════════════════════════════════════════════
#  JSON ↔ YAML 转换（单入口、双方向）
# ════════════════════════════════════════════════════════════════════════════════

#: to 参数 → 源格式显示名（to="yaml" 表示输入是 JSON，反之亦然）
_CONVERT_SOURCE_NAMES = {"yaml": "JSON", "json": "YAML"}

#: JSON 输出默认缩进 —— 与合并前的 yaml_to_json 逐字节一致（原先写死 indent=2）
_DEFAULT_JSON_INDENT = 2


def data_convert(data: str, to: str, *, indent: int = None) -> dict:
    """把 data 从另一种格式转换成 to 指定的格式（双向单入口）。

    【为什么合并】原 `json_to_yaml` / `yaml_to_json` 是一对互逆镜像：同一动作的
    两个方向，词频完全相同（评估报告 §6.2 第 0 档第 5 行、q08 风险点）。用一个
    `to` 参数表达方向，范式与本项目既有的 `compress(format="zip"|"tar.gz")` 一致。

    Args:
        data: 待转换的字符串。to="yaml" 时按 JSON 解析；to="json" 时按 YAML 解析。
        to: 目标格式，仅接受 "yaml" | "json"（必填）。非法值返回结构化错误。
        indent: JSON 输出缩进（仅 to="json" 生效）。None → 2（与合并前一致）。

    Returns:
        {"ok": True, "data": "<转换结果字符串>"}  —— 与合并前两个工具返回值同形
        或 {"ok": False, "error": "..."}
    """
    # 先判 to：它决定 data 按哪种格式解析，也是唯一的必填枚举参数
    if not isinstance(to, str) or to not in _CONVERT_SOURCE_NAMES:
        return {
            "ok": False,
            "error": f"不支持的转换目标 to={to!r}，仅支持 'yaml' 或 'json'",
        }

    source_name = _CONVERT_SOURCE_NAMES[to]
    direction = f"{source_name}→{to.upper()}"

    try:
        if not isinstance(data, str):
            return {
                "ok": False,
                "error": f"数据必须是字符串，收到类型: {type(data).__name__}",
            }

        if not data.strip():
            return {"ok": False, "error": f"{source_name} 数据为空"}

        if to == "yaml":
            # ── JSON → YAML ──
            try:
                obj = json.loads(data)
            except json.JSONDecodeError as e:
                # 带上原始异常文本（含 line/column/char），不静默返回空
                return {"ok": False, "error": f"JSON 解析失败: {e}"}

            import yaml

            yaml_str = yaml.dump(
                obj,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
            )

            return {"ok": True, "data": yaml_str}

        # ── YAML → JSON ──
        import yaml

        try:
            obj = yaml.safe_load(data)
        except yaml.YAMLError as e:
            return {"ok": False, "error": f"YAML 解析失败: {e}"}

        # 验证解析结果类型
        if obj is None:
            return {"ok": False, "error": "YAML 解析结果为空（空文档或仅注释）"}

        if not isinstance(obj, (dict, list, str, int, float, bool)):
            return {
                "ok": False,
                "error": f"YAML 解析结果类型不支持: {type(obj).__name__}",
            }

        if indent is not None and (isinstance(indent, bool) or not isinstance(indent, int)):
            return {
                "ok": False,
                "error": f"indent 必须是整数，收到类型: {type(indent).__name__}",
            }

        json_str = json.dumps(
            obj,
            ensure_ascii=False,
            indent=_DEFAULT_JSON_INDENT if indent is None else indent,
        )

        return {"ok": True, "data": json_str}

    except ImportError:
        return {"ok": False, "error": f"pyyaml 库未安装，无法进行 {direction} 转换"}
    except Exception as e:
        logger.error("data_convert(%s) 异常: %s", direction, e)
        return {"ok": False, "error": f"转换失败: {e}"}


# ── 已合并工具的函数级兼容入口 ────────────────────────────────────────────────
# 工具注册已下线（改挂 data_convert，见 agent/tools/code_tools.py），但函数保留：
# 站内调用点与既有单元测试仍按名引用，且实现只有一份 —— 都走 data_convert。

def json_to_yaml(json_data: str) -> dict:
    """[已并入 data_convert(json_data, "yaml")] JSON 字符串 → YAML 字符串。"""
    return data_convert(json_data, "yaml")


def yaml_to_json(yaml_data: str) -> dict:
    """[已并入 data_convert(yaml_data, "json")] YAML 字符串 → JSON 字符串。"""
    return data_convert(yaml_data, "json")


# ════════════════════════════════════════════════════════════════════════════════
#  JSON 验证
# ════════════════════════════════════════════════════════════════════════════════


def _json_parsed_type(obj) -> str:
    """JSON 解析结果的类型名。

    bool 必须在 int/float 之前判定 —— bool 是 int 的子类，顺序写反会把 true
    报成 "number"。本函数是唯一一份判定：`json_validate` 与 `data_format_detect`
    曾各写一份，正是 json_validate 被合并的原因（评估报告 §6.2 第 0 档第 4 行）。
    """
    if isinstance(obj, dict):
        return "object"
    if isinstance(obj, list):
        return "array"
    if isinstance(obj, str):
        return "string"
    if isinstance(obj, bool):
        return "boolean"
    if isinstance(obj, (int, float)):
        return "number"
    if obj is None:
        return "null"
    return type(obj).__name__


def _json_keys_count(obj):
    """dict 的键数 / list 的元素数；其他类型返回 None（与迁移前口径一致）。"""
    return len(obj) if isinstance(obj, (dict, list)) else None


def _json_check_fields(json_block: dict) -> dict:
    """把 JSON 校验块摊平成 data_format_detect 的返回字段。

    扁平字段 `valid` / `parsed_type` / `keys_count` / `data` 与被合并的
    `json_validate` 同名同义（调用方从 data_format_detect 即可拿到全部输出）；
    嵌套 `json` 块额外承载 `error` —— 放平级会与 data_format_detect 自身的失败
    字段 `error` 撞名（ok=False 表示"检测失败"，而 ok=True + valid=False 表示
    "检测成功且不是合法 JSON"）。
    """
    return {
        "valid": json_block["valid"],
        "parsed_type": json_block["parsed_type"],
        "keys_count": json_block["keys_count"],
        "data": json_block["data"],
        "json": dict(json_block),
    }


def json_validate(data: str) -> dict:
    """验证字符串是否为合法 JSON。

    [已并入 data_format_detect] 工具注册已下线；本函数保留供站内调用与既有测试
    使用，其全部输出均可从 `data_format_detect` 的 valid / parsed_type /
    keys_count / data / json.error 得到。

    Args:
        data: 待验证的字符串

    Returns:
        {"ok": True, "valid": True, "parsed_type": "object|array|string|...", "data": <解析后的对象>}
        或 {"ok": True, "valid": False, "error": "..."}
        或 {"ok": False, "error": "..."}  — 输入类型错误等
    """
    try:
        if not isinstance(data, str):
            return {
                "ok": False,
                "error": f"数据必须是字符串，收到类型: {type(data).__name__}",
            }

        if not data.strip():
            return {"ok": True, "valid": False, "error": "数据为空字符串"}

        try:
            obj = json.loads(data)
        except json.JSONDecodeError as e:
            # 返回更友好的错误信息
            error_msg = str(e)
            return {"ok": True, "valid": False, "error": f"JSON 格式无效: {error_msg}"}

        return {
            "ok": True,
            "valid": True,
            "parsed_type": _json_parsed_type(obj),
            "keys_count": _json_keys_count(obj),
            "data": obj,
        }

    except Exception as e:
        logger.error("json_validate 异常: %s", e)
        return {"ok": False, "error": f"验证失败: {e}"}


# ════════════════════════════════════════════════════════════════════════════════
#  数据格式检测
# ════════════════════════════════════════════════════════════════════════════════


def _is_xml(data: str) -> float:
    """检测数据是否为 XML 格式。

    Returns:
        置信度 (0.0 - 1.0)，0 表示确定不是
    """
    stripped = data.strip()
    if not stripped:
        return 0.0

    # XML 声明
    if stripped.startswith("<?xml"):
        return 0.95

    # XML 根元素模式
    xml_pattern = re.compile(r"<\?xml\b|<[a-zA-Z_][\w\-.]*(\s[^>]*)?>.*</[a-zA-Z_][\w\-.]*>", re.DOTALL)
    if xml_pattern.search(stripped):
        return 0.85

    return 0.0


def _is_yaml(data: str) -> tuple:
    """检测数据是否为 YAML 格式。

    Returns:
        (置信度, 解析后的对象 或 None)
    """
    stripped = data.strip()
    if not stripped:
        return (0.0, None)

    try:
        import yaml

        obj = yaml.safe_load(data)
        if isinstance(obj, (dict, list)):
            # 成功了，而且是结构化数据
            return (0.90, obj)
        elif obj is not None and not isinstance(obj, str):
            # 成功但非结构化（如纯数字），降低置信度
            return (0.60, obj)
        elif isinstance(obj, str) and len(obj) > 20:
            # YAML 把整段文本解析为一个字符串，这很可能不是真正的 YAML
            return (0.40, obj)
        elif isinstance(obj, str) and len(obj) <= 20:
            # 可能是 YAML 的简单值
            return (0.70, obj)
        else:
            # obj is None，空 YAML 文档
            return (0.10, None)
    except Exception:
        return (0.0, None)


def _is_csv(data: str) -> float:
    """检测数据是否为 CSV 格式。

    Returns:
        置信度 (0.0 - 1.0)
    """
    stripped = data.strip()
    if not stripped:
        return 0.0

    # 至少需要包含换行符或逗号
    if "\n" not in stripped and "," not in stripped:
        return 0.0

    try:
        # 使用 csv.Sniffer 检测
        sample = stripped[:8192]  # 取前 8KB 作为样本
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        # 验证：尝试解析几行
        reader = csv.reader(io.StringIO(sample), dialect)
        rows = list(reader)
        if len(rows) >= 1:
            # 至少有一行数据
            return 0.85
        return 0.50
    except csv.Error:
        # 可能不是 CSV
        pass
    except Exception:
        pass

    # 回退检查：包含逗号分隔的行且有多行
    lines = stripped.split("\n")
    if len(lines) >= 2:
        # 检查逗号分隔的一致性
        comma_counts = [line.count(",") for line in lines if line.strip()]
        if comma_counts and len(set(comma_counts)) <= 2 and max(comma_counts) > 0:
            return 0.60

    return 0.0


def data_format_detect(data: str) -> dict:
    """自动检测字符串数据的格式类型。

    按优先级依次尝试: JSON → XML → YAML → CSV

    Args:
        data: 待检测的字符串数据

    Returns:
        {"ok": True, "format": "json"|"xml"|"yaml"|"csv"|"unknown",
         "confidence": 0.0-1.0, "details": "...", "scores": {"json":..,"xml":..,"yaml":..,"csv":..},
         # ↓ 以下 JSON 校验字段由已合并的 json_validate 提供（同名同义）
         "valid": bool,              # 是否合法 JSON
         "parsed_type": str|None,    # object|array|string|boolean|number|null
         "keys_count": int|None,     # dict 键数 / list 元素数，其他类型为 None
         "data": <解析后的 JSON>|None,
         "json": {...上述五项 + "error": 失败原因|None}}
        或 {"ok": False, "error": "..."}
    """
    try:
        if not isinstance(data, str):
            return {
                "ok": False,
                "error": f"数据必须是字符串，收到类型: {type(data).__name__}",
            }

        if not data.strip():
            return {
                "ok": True,
                "format": "unknown",
                "confidence": 0.0,
                "details": "数据为空",
                **_json_check_fields({
                    "valid": False,
                    "parsed_type": None,
                    "keys_count": None,
                    "data": None,
                    "error": "数据为空字符串",
                }),
            }

        stripped = data.strip()
        results = []

        # 1. JSON 检测 — 尝试 json.loads
        #    同时产出原 json_validate 的校验字段：此处与 json_validate 用的是同一个
        #    json.loads + 同一套类型判定，后者独有价值仅 parsed_type / keys_count
        #    （评估报告 §6.2 第 0 档第 4 行）。
        json_confidence = 0.0
        json_block = {
            "valid": False,
            "parsed_type": None,
            "keys_count": None,
            "data": None,
            "error": "JSON 格式无效: 不是合法 JSON",
        }
        try:
            parsed_obj = json.loads(stripped)
            if isinstance(parsed_obj, (dict, list)):
                json_confidence = 0.95
            elif isinstance(parsed_obj, str) and len(stripped) < 100:
                # JSON 字符串字面量，较短则可能
                json_confidence = 0.70
            else:
                json_confidence = 0.50
            json_block = {
                "valid": True,
                "parsed_type": _json_parsed_type(parsed_obj),
                "keys_count": _json_keys_count(parsed_obj),
                "data": parsed_obj,
                "error": None,
            }
        except (json.JSONDecodeError, ValueError) as e:
            # 保留原始异常文本（含 line/column），不静默丢掉失败原因
            json_block["error"] = f"JSON 格式无效: {e}"
        results.append(("json", json_confidence))

        # 2. XML 检测
        xml_confidence = _is_xml(stripped)
        results.append(("xml", xml_confidence))

        # 3. YAML 检测
        yaml_confidence, _ = _is_yaml(stripped)
        results.append(("yaml", yaml_confidence))

        # 4. CSV 检测
        csv_confidence = _is_csv(stripped)
        results.append(("csv", csv_confidence))

        # 找到最高置信度
        best = max(results, key=lambda x: x[1])

        if best[1] <= 0.0:
            return {
                "ok": True,
                "format": "unknown",
                "confidence": 0.0,
                "details": "无法识别数据格式",
                "scores": {fmt: conf for fmt, conf in results},
                **_json_check_fields(json_block),
            }

        # 生成描述
        format_descriptions = {
            "json": "JSON (JavaScript Object Notation) — 结构化数据交换格式",
            "xml": "XML (eXtensible Markup Language) — 标记语言",
            "yaml": "YAML (YAML Ain't Markup Language) — 人类友好的数据序列化格式",
            "csv": "CSV (Comma-Separated Values) — 表格数据格式",
        }

        return {
            "ok": True,
            "format": best[0],
            "confidence": round(best[1], 4),
            "details": format_descriptions.get(best[0], best[0]),
            "scores": {fmt: round(conf, 4) for fmt, conf in results},
            **_json_check_fields(json_block),
        }

    except Exception as e:
        logger.error("data_format_detect 异常: %s", e)
        return {"ok": False, "error": f"格式检测失败: {e}"}
