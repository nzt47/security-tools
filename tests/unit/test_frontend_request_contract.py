"""前端**请求键**与后端**读取键**的机械对拍（2026-10-06）。

【为什么需要它】两个真实缺陷都是**请求侧**的静默错，方向与「迁移信封」相反：
前端发的键后端不读 ⇒ 后端拿到默认值或直接 400，前端却看不出来。实测（对活体服务打真实请求）：

  · POST /api/knowledge/query + {query, limit}（前端原先发的）→ **HTTP 400**「查询问题不能为空」
    —— 后端读的是 question/top_k（agent/server_routes/routes_knowledge.py::api_knowledge_query）
  · POST /api/vector/search   + {query, limit} → 200 但 **count=5**
    —— 后端读的是 query/top_k（plugins/memory.py::api_vector_search），前端写的 limit: 10 从未生效

两者都不是「报错即被发现」的形态：前者的报错文案说的是「问题为空」（而用户明明输入了），
后者干脆静默少给一半结果。本文件把这类错**机械化**地拦住。

【判据】对每个已知的「前端调用点 → 后端视图」配对：
  · 前端**字面量 body** 的键集合（postEnvelope/hubPost 的第二个实参对象字面量）；
  · 后端视图里「data.get(某字符串)」读到的键集合
    （AST 取字符串常量，只认接收者叫 data 的那种 —— data 是该视图里 request.get_json() 的局部名）；
  · 断言 **发送 ⊇ 读取**（后端读的每个键，前端都得发）。

【本文件的边界（写清楚，免得被当成万能）】
  · 只覆盖**显式列在 _CASES 里**的配对；新增端点要顺手加一行 —— 与「静态接线断言」同一性质；
  · body 若是**变量**而不是字面量，_frontend_keys 会抛 AssertionError 让人来更新本表（不静默放过）。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.timeout(300)

ROOT = Path(__file__).resolve().parents[2]

#: (前端文件, 端点常量, 后端文件, 后端视图函数)
_CASES = [
    ("yunshu-ui/src/pages/hub/memory/search.tsx", "VECTOR_SEARCH",
     "plugins/memory.py", "api_vector_search"),
    ("yunshu-ui/src/pages/hub/memory/search.tsx", "KNOWLEDGE_QUERY",
     "agent/server_routes/routes_knowledge.py", "api_knowledge_query"),
    ("yunshu-ui/src/pages/hub/memory/index.tsx", "VECTOR_SEARCH",
     "plugins/memory.py", "api_vector_search"),
]


def _frontend_keys(src: str, const_name: str) -> set:
    """取前端为某端点常量发出去的**字面量** body 键集合。"""
    pattern = re.compile(
        r"(?:postEnvelope|hubPost)(?:<[^>]*>)?\(\s*" + re.escape(const_name) + r"\s*,\s*\{([^{}]*)\}"
    )
    match = pattern.search(src)
    assert match, (
        "在源码里找不到 " + const_name + " 的**字面量** body —— 该调用点被改写成了变量？"
        "请同步更新 tests/unit/test_frontend_request_contract.py 的 _CASES（不要让它静默放过）。"
    )
    return {k for k in re.findall(r"(\w+)\s*:", match.group(1))}


def _backend_reads(path: Path, func_name: str) -> set:
    """取后端视图里 data.get(某字符串) 的键集合（AST，只认接收者叫 data 的调用）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            keys = set()
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                func = call.func
                if not isinstance(func, ast.Attribute) or func.attr != "get":
                    continue
                if not isinstance(func.value, ast.Name) or func.value.id != "data":
                    continue
                if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
                    keys.add(call.args[0].value)
            return keys
    raise AssertionError(path.name + " 里找不到函数 " + func_name + " —— 视图被重命名/删除了？")


@pytest.mark.parametrize("front_rel,const,back_rel,func_name", _CASES)
def test_后端读的键前端都发了(front_rel, const, back_rel, func_name):
    sent = _frontend_keys((ROOT / front_rel).read_text(encoding="utf-8"), const)
    read = _backend_reads(ROOT / back_rel, func_name)
    missing = sorted(read - sent)
    assert not missing, (
        const + "（" + front_rel + "）没有发送后端 " + back_rel + "::" + func_name + " 会读的键："
        + repr(missing) + "\n"
        "  后端读：" + repr(sorted(read)) + "\n"
        "  前端发：" + repr(sorted(sent)) + "\n"
        "两者的后果**都不会自己冒出来**：后端拿到默认值（静默少结果），或直接 400"
        "（而报错文案说的是别的原因）。请把前端请求体的键改成后端契约里的键。"
    )


def test_判据对合成样例成立():
    """两个方向：键齐全时放行；缺键时必须能区分出来（否则本文件是假绿）。"""
    backend = (
        "def view():\n"
        "    data = request.get_json() or {}\n"
        "    q = data.get('query', '').strip()\n"
        "    k = data.get('top_k', 5)\n"
        "    return data.get('other')\n"
    )
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "sample_view.py"
        p.write_text(backend, encoding="utf-8")
        reads = _backend_reads(p, "view")
        assert reads == {"query", "top_k", "other"}, "AST 抽取读键失败：实测 " + repr(reads)

    good = "const d = await postEnvelope<{a: number}>(VECTOR_SEARCH, { query: q, top_k: 10 })"
    assert _frontend_keys(good, "VECTOR_SEARCH") == {"query", "top_k"}
    bad = "const d = await postEnvelope<{a: number}>(VECTOR_SEARCH, { query: q, limit: 10 })"
    assert _frontend_keys(bad, "VECTOR_SEARCH") == {"query", "limit"}, (
        "判据没能区分 limit 与 top_k ⇒ 它对本次那类缺陷是失明的"
    )
