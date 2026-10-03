"""鉴权覆盖率基线守卫（安全·2026-10-03）。

【解决什么 —— 这是「只修那 39 条会复发」的机制化答案】
2026-09-27 的鉴权盘点与 routes_workspace.py 的模块 docstring 都指出：鉴权原为逐路由
装饰器、无全局兜底，而 plugins/ 的蓝图由 loader 目录扫描自动挂载 ⇒ 新插件作者漏加
装饰器即裸奔。这是**可复发的结构性成因**，逐条补齐治不了根。
本次审计又实证了同族的第二种形态（H-5）：漏装饰器 **且** 被 CP_API_AUTH_ALLOW 豁免。

【怎么做】把「当前的无鉴权变更型路由集合」当作**只允许收缩的基线**（与
failures_baseline.txt 同款纪律，本仓已有先例）：
  - 新增一条无鉴权变更型路由 ⇒ 测试红（新代码必须自带鉴权）；
  - 修掉一条 ⇒ 重跑脚本使基线收缩（不允许悄悄变多）。

【为什么不断言为 0】当前仍有存量。直接断言 0 会让本测试长期红、进而被忽略，
等于没有守卫。收缩式基线是这两害之间更可执行的一档，但它**要求有人在修**。

【局限·必读】静态扫描只看**装饰器**，看不到 app_server._api_auth_gate 的全局兜底，
也看不到 CP_API_AUTH_ALLOW 豁免。故「未装饰」≠「当前可未授权调用」。
豁免那一半由 find_allowed_write_endpoints 覆盖（test_auth_allowlist_audit.py 与
app_server.audit_auth_allowlist）。两面合起来才是完整判定。
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "reports" / "auth_coverage_baseline.json"

MUTATING = {"POST", "PUT", "DELETE", "PATCH"}
# 认作"已鉴权"的装饰器标记。**每新增一个鉴权装饰器都必须登记到这里**，否则用它标注的
# 端点会被误判为裸奔，守卫随即失真（实测教训：加 require_auth 后忘登记，基线凭空多 1 条；
# 加 _require_admin 后忘登记，11 条已受保护的端点全部被误报）。
#   require_token  —— agent/server_routes/* 与 5 个插件的本地包装
#   require_auth   —— plugins/plugin_api.py 的统一装饰器（审计 M-40 收口点）
#   _require_admin —— plugins/admin_api.py 的后台守卫（会话令牌 或 共享 API 令牌）
GUARD_MARKERS = ("require_token", "require_auth", "_require_admin", "auth=True",
                 "login_required", "require_permission")
ROUTE_DEC = re.compile(r"^\s*@([A-Za-z_][\w.]*)\.route\(")
PATH_RE = re.compile(r'''route\(\s*["']([^"']+)''')


def _scan_files():
    files = [ROOT / "app_server.py"]
    files += sorted((ROOT / "plugins").glob("*.py"))
    files += sorted((ROOT / "agent" / "server_routes").glob("*.py"))
    return [f for f in files if f.exists()]


def _real_route_decorator_lines(tree):
    """返回 {行号: 方法名}，只含**真实**函数装饰器。

    【为什么必须走 AST】纯文本扫描会把**文档字符串里的示例**当成真路由 —— 这不是假设：
    2026-10-03 给 plugins/plugin_api.py 的 require_auth 写了一段用法示例（docstring 里含
    "@bp.route(\"/api/x\", methods=[\"POST\"])"），文本扫描立刻把它算成一条无鉴权写端点，
    基线凭空多出一条假阳性。AST 只认 decorator_list，从根上消除这类误报。
    """
    found = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for d in node.decorator_list:
            if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute):
                meth = d.func.attr.lower()
                if meth in ("route", "get", "post", "put", "delete", "patch"):
                    found[int(d.lineno)] = meth
    return found


def collect_unguarded_mutating():
    """返回 {文件:行 方法 路径}：无鉴权装饰器的变更型路由。"""
    found = set()
    for f in _scan_files():
        src = f.read_text(encoding="utf-8", errors="replace")
        lines = src.splitlines()
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        real = _real_route_decorator_lines(tree)
        for i, ln in enumerate(lines):
            if (i + 1) not in real:  # ast.lineno 是 1-based；只在真实装饰器行上判定
                continue
            if not ROUTE_DEC.match(ln):
                continue
            block, j = [ln], i + 1
            while j < len(lines) and j < i + 10:
                block.append(lines[j])
                if re.match(r"^\s*def\s", lines[j]):
                    break
                j += 1
            blob = "\n".join(block)
            if any(g in blob for g in GUARD_MARKERS):
                continue
            pm = PATH_RE.search(ln)
            if not pm:
                continue
            mm = re.search(r"methods\s*=\s*\[([^\]]*)\]", blob)
            methods = (re.findall(r'''["']([A-Z]+)["']''', mm.group(1))
                       if mm else ["GET"])
            hit = sorted(set(methods) & MUTATING)
            if not hit:
                continue
            rel = str(f.relative_to(ROOT)).replace(chr(92), "/")
            found.add(rel + ":" + str(i + 1) + " " + "/".join(hit) + " " + pm.group(1))
    return found


@pytest.fixture(scope="module")
def baseline():
    assert BASELINE.exists(), "缺少基线 " + str(BASELINE)
    return json.loads(BASELINE.read_text(encoding="utf-8"))


class TestAuthCoverageBaseline:
    def test_无新增的无鉴权变更型路由(self, baseline):
        """新代码不得引入未装饰的写端点（只允许从基线删）。"""
        new = sorted(collect_unguarded_mutating() - set(baseline["unguarded_mutating"]))
        assert not new, (
            "新增 " + str(len(new)) + " 条无鉴权变更型路由（写端点漏鉴权）：\n  "
            + "\n  ".join(new)
            + "\n修法：① @require_token；② 插件用 @_view(auth=True)；"
            "③ 确需无鉴权则显式写入基线并在 PR 说明。同时确认未被"
            " CP_API_AUTH_ALLOW 豁免（审计 H-5）。"
        )

    def test_基线只允许收缩(self, baseline):
        """已修好的条目必须从基线删除，否则基线会退化成永久豁免单。"""
        stale = sorted(set(baseline["unguarded_mutating"]) - collect_unguarded_mutating())
        assert not stale, (
            "基线有 " + str(len(stale)) + " 条已失效（已加鉴权或路由已删）：\n  "
            + "\n  ".join(stale)
            + "\n请运行 python scripts/audit/auth_coverage.py --write 收缩基线。"
        )

    def test_基线文件自洽(self, baseline):
        assert baseline["total_unguarded_mutating"] == len(baseline["unguarded_mutating"])
        assert isinstance(baseline.get("note"), str) and baseline["note"]

    def test_检测器具备分辨力_已知未装饰者必须被检出(self):
        """证明扫描不是恒空集：已知的无鉴权写端点必须在结果里。

        【为什么锚在 /api/auth/login】本断言的锚点必须跟着**当前确实未装饰**的端点走，
        否则会退化成永远失败（进而被人删掉）。2026-10-03 该锚点已迁移两次：
          plugins/chat.py POST /api/chat   -> 补上鉴权后失效；
          plugins/admin_api POST /api/user -> 补上 _require_admin 后失效；
        现在只剩 /api/auth/login —— 它是**刻意**不装饰的（鸡生蛋：登录本身不该要求令牌），
        因此是最合适的稳定锚点。若将来它也变了，这条断言必须再迁移。
        """
        current = collect_unguarded_mutating()
        assert any("plugins/admin_api.py" in r and "/api/auth/login" in r for r in current), (
            "plugins/admin_api.py 的 POST /api/auth/login 当前刻意无鉴权装饰器，扫描却未检出 —— "
            "说明扫描逻辑已失效（守卫会变成永远通过的空壳）。"
        )

    def test_检测器具备分辨力_已装饰者必须被排除(self):
        """反向证明：2026-10-03 修好的 /api/replay/upload 不得再出现在结果里。"""
        current = collect_unguarded_mutating()
        assert not any("routes_replay.py" in r and "upload" in r for r in current), (
            "routes_replay.py 的 /api/replay/upload 已加 @require_token（提交 258db45c），"
            "扫描却仍判为无鉴权 —— 说明装饰器识别逻辑有误。"
        )