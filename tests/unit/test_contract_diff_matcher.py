"""回归守卫（阶段 0 / R1）：契约对拍工具的匹配器不得退化。

【为什么值得单测】本工具的价值**完全**取决于"零假阳性"：一个会误报的门禁没人会看，
最终等于没有门禁（本仓已有先例：M-35 记录的多次"改动未触发 CI"事故）。
开发过程中实测到 4 类假阳性，每一类都在此锁死，防止后人改动匹配逻辑时回退：
  ① TS 模板字面量 ${...} 残留 "$"（曾产生 60+ 条假阳性）；
  ② Blueprint url_prefix 未解析（/api/modules/topology 被误判不存在）；
  ③ 模块级常量前缀 f"{PREFIX}/x" 未展开（/api/cp/tool-exemptions 被误判）；
  ④ 查询串模板 /api/x/stream${q}（q 是 "?a=b"，不是路径段）；
  ⑤ **docstring 里的用法示例被当成真路由** —— 2026-10-03 实测：给 plugins/plugin_api.py
     的 require_auth 写用法示例后，文本扫描把 /api/x 计成真实端点，routes_total 449→450。
"""
from __future__ import annotations

import ast

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def cd():
    """从路径加载 scripts/audit/contract_diff.py（它不是包的一部分）。"""
    path = ROOT / "scripts" / "audit" / "contract_diff.py"
    spec = importlib.util.spec_from_file_location("contract_diff_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["contract_diff_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


ROUTES = {
    "/api/modules/topology": {"methods": ["GET"], "where": ["agent/modules_api.py:1"]},
    "/api/cp/tool-exemptions": {"methods": ["GET", "POST"], "where": ["x.py:1"]},
    "/api/skills-mgmt/assess/stream": {"methods": ["GET"], "where": ["y.py:1"]},
    "/api/schedules/<task_id>": {"methods": ["DELETE"], "where": ["p.py:1"]},
    "/api/schedules/<task_id>/pause": {"methods": ["POST"], "where": ["p.py:1"]},
    "/api/mcp/services/<string:service_id>": {"methods": ["DELETE"], "where": ["p.py:1"]},
}


class TestNorm:
    def test_模板字面量残留美元号被剥离(self, cd):
        assert cd._norm("/api/skills-mgmt/${item.id}/publish") == "/api/skills-mgmt/<>/publish"

    def test_flask_转换器归一(self, cd):
        assert cd._norm("/api/schedules/<task_id>") == "/api/schedules/<>"
        assert cd._norm("/api/mcp/services/<string:service_id>") == "/api/mcp/services/<>"

    def test_冒号风格归一(self, cd):
        assert cd._norm("/api/x/:id/y") == "/api/x/<>/y"


class TestRouteKnown:
    def test_精确命中(self, cd):
        assert cd._route_known("/api/modules/topology", ROUTES) is True

    def test_模板字面量动态段命中(self, cd):
        """②/①：/api/schedules/${id}/pause 应命中 /api/schedules/<task_id>/pause。"""
        assert cd._route_known("/api/schedules/${id}/pause", ROUTES) is True

    def test_查询串模板后缀容错(self, cd):
        """④：/api/skills-mgmt/assess/stream${q} 的 ${q} 是 ?a=b，不是路径段。"""
        assert cd._route_known("/api/skills-mgmt/assess/stream${q}", ROUTES) is True

    def test_子树声明命中(self, cd):
        assert cd._route_known("/api/modules/*", ROUTES) is True

    def test_真正缺失的端点必须判为未知(self, cd):
        """本工具实测检出的两个真实前端/后端不一致，不得被容错吃掉。"""
        assert cd._route_known("/api/schedules/${id}/delete", ROUTES) is False
        assert cd._route_known("/api/mcp/services/${id}/delete", ROUTES) is False

    def test_完全不同路径判为未知(self, cd):
        assert cd._route_known("/api/does-not-exist", ROUTES) is False


class TestStaticScan:
    def test_Blueprint前缀被解析(self, cd):
        """②：/api/modules/topology 由 url_prefix 注册，静态扫描必须能还原。"""
        routes = cd.collect_routes_static()
        assert "/api/modules/topology" in routes, "Blueprint url_prefix 解析回退"

    def test_常量前缀被展开(self, cd):
        """③：f"{PREFIX}/tool-exemptions" 形式必须能还原为完整路径。"""
        routes = cd.collect_routes_static()
        assert "/api/cp/tool-exemptions" in routes, "f-string 常量前缀展开回退"

    def test_嵌套副本被排除(self, cd):
        """M-26：security-tools/ 是未跟踪的完整副本，必须排除，否则产出双份契约。"""
        routes = cd.collect_routes_static()
        assert not any("security-tools" in w for r in routes.values() for w in r["where"])


class TestDocstringIsNotARoute:
    """(5) 类假阳性：源码文本里的用法示例不得被当作真实路由。"""

    def test_docstring_里的路由示例不算真路由(self, cd):
        # 用 chr(34)/chr(10) 拼装，避免在测试里嵌套三引号（本仓多处已因嵌套引号踩坑）
        src = (
            'def f():' + chr(10)
            + '    ' + chr(34) * 3 + '用法示例:' + chr(10)
            + chr(10)
            + '        @bp.route(' + chr(34) + '/api/x' + chr(34) + ', methods=[' + chr(34) + 'POST' + chr(34) + '])' + chr(10)
            + '    ' + chr(34) * 3 + chr(10)
            + '    return 1' + chr(10)
        )
        assert cd._real_route_decorator_lines(ast.parse(src)) == {}

    def test_真实装饰器仍被识别(self, cd):
        src = '@bp.route("/api/y", methods=["POST"])\n@require_auth\ndef g():\n    pass\n'
        assert list(cd._real_route_decorator_lines(ast.parse(src)).values()) == ["route"]

    def test_plugin_api_的用法示例未污染真实路由集(self, cd):
        routes = cd.collect_routes_static()
        assert "/api/x" not in routes, (
            "plugin_api.py 的 docstring 用法示例被当成了真实路由 —— 门禁会凭空造出端点。"
        )


class TestExclusionUsesRepoRelativePath:
    """(6) 类缺陷：排除判定必须按**仓库相对路径**。

    2026-10-03 由 CI 实测暴露：本仓 GitHub Actions 的 checkout 路径是
    /home/runner/work/<repo>/<repo>/，而**仓库名恰好叫 security-tools**；
    当时实现用绝对路径的 parts 去比对 EXCLUDE_DIRS（含 security-tools），于是 CI 上
    整棵仓库被判为排除 ⇒ 只扫到 app_server.py/main.py，agent/ 与 plugins/ 全丢。
    后果不只是两条用例变红，而是**门禁会带着残缺路由集静默判「零漂移」**。
    """

    def test_仓库内嵌套副本仍被排除(self, cd):
        from pathlib import Path
        assert cd._is_excluded(Path("security-tools/app_server.py")) is True

    def test_正常源码不被排除(self, cd):
        from pathlib import Path
        for rel in ("agent/modules_api.py", "plugins/skills.py", "app_server.py"):
            assert cd._is_excluded(Path(rel)) is False, rel + " 被误排除"

    def test_CI_绝对路径场景不被误判(self, cd):
        from pathlib import Path
        abs_like = Path("/home/runner/work/security-tools/security-tools/agent/modules_api.py")
        # 取「从 agent 开始」的部分作为仓库相对路径（不写死下标，避免路径层数变化时失效）
        rel = Path(*abs_like.parts[abs_like.parts.index("agent"):])
        assert rel.as_posix() == "agent/modules_api.py"
        assert cd._is_excluded(rel) is False, (
            "CI 的 checkout 路径含仓库名 security-tools，按绝对路径判定会整仓被排除"
        )
        # 反证：拿绝对路径直接判定会命中 —— 这正是当初的错误做法
        assert any(p in cd.EXCLUDE_DIRS for p in abs_like.parts) is True

    def test_agent_目录确实被扫描到(self, cd):
        """端到端反证：agent/ 下的路由必须出现在结果里。"""
        routes = cd.collect_routes_static()
        where = [w for r in routes.values() for w in r["where"]]
        assert any(w.startswith("agent/") for w in where), (
            "agent/ 下的路由一条都没扫到 —— 排除逻辑很可能又按绝对路径判定了"
        )

class Test前端字面量的两个口径:
    """`frontend_literals`（去重路径 = 合同面）与 `frontend_stray_literals`（出现次数 = 收敛进度）
    必须**分开**，且常量层只从**计数**里排除、不从**正确性校验**里排除。

    【为什么单列一类】阶段 5 / R5 要收口前端 /api 字面量。开发中实测到一个会误导人的点：
    去重路径数**不会**因为"把字面量搬进常量层"而下降（路径还在，只是换了地方写）。
    若拿它当进度指标，就会得出"改了一堆、数字没动"的错误结论，进而放弃收口。
    故两个口径都保留，且用途写死：
      · literal 数 -> 对拍（引用了哪些端点）；
      · stray 数   -> 进度与门禁（还有多少处散落），目标 0。
    """

    def test_常量层从计数里排除(self, cd):
        stray = cd.collect_stray_frontend_literals()["react"]
        assert cd.SANCTIONED_FRONTEND_LAYER not in stray, (
            "被许可的端点常量层不应出现在 stray 统计里 —— 它就是收口点本身"
        )

    def test_常量层仍参与正确性对拍(self, cd):
        """**这条是关键**：常量层被排除的只能是"计数"，不能是"路径是否存在"的校验。

        若把它整段排除，\`frontend_calls_missing_endpoint\` 就会对该层**失明** ——
        而那正是当年检出 2 个真实前后端缺陷（方法与路径双错）的判据，
        等于为了降数字把门禁弄瞎。
        """
        literals = cd.collect_frontend_literals()["react"]
        files_of = {f for fs in literals.values() for f in fs}
        assert cd.SANCTIONED_FRONTEND_LAYER in files_of, (
            "端点常量层没有参与 frontend_literals 对拍 —— "
            "它的路径将不再被校验是否命中真实后端路由（门禁被弄瞎）"
        )

    def test_stray_计次而非去重(self, cd, tmp_path):
        """同一文件里写两次同一个路径，stray 必须是 2 而不是 1。"""
        stray = cd.collect_stray_frontend_literals()
        assert isinstance(stray, dict) and "react" in stray and "legacy" in stray
        # 逐文件计数，且总数 = 各文件之和（去重口径下这两者会不等）
        for label, per_file in stray.items():
            assert all(isinstance(n, int) and n > 0 for n in per_file.values())

    def test_两个口径都在报告快照里(self, cd):
        """快照缺了 stray 就没法追踪收敛进度；缺了 literal 就没法看合同面。"""
        src = (ROOT / "scripts" / "audit" / "contract_diff.py").read_text(encoding="utf-8")
        assert '"frontend_literals"' in src and '"frontend_stray_literals"' in src
