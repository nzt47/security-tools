"""前端字面量**收集口径**守卫（2026-10-05 · 阶段 5 / R5 收尾）。

【解决什么 —— 一次"过滤误伤"，它比少计数严重得多】
契约对拍的两个收集器都写过这一行：

    if "/assets/" in rel: continue      # 本意：排除 static/assets/ 下的 Vite 产物

`"/assets/" in rel` 是**子串**判断，于是它同时吞掉了
`yunshu-ui/src/pages/hub/assets/index.tsx` —— 一个**真实源码文件**。
实测该文件里 5 处 `/api` 字面量因此**既不计入** `frontend_literals`（去重路径数）、
**也不计入** `frontend_stray_literals`（收口进度），后果有两层：

  ① `test_react_字面量必须恰好为零` 报 **0 并通过** —— 而该文件明明还有 5 处；
  ② 更严重：**"前端调用 → 后端路由"这一步也看不见它**，于是藏住了一个真实缺陷 ——
     前端 `POST /api/assets/<cat>/<id>/delete`，后端只注册 `DELETE /api/assets/<cat>/<id>`，
     方法与路径双错，实测 404。这正是对拍工具当初被造出来要抓的那类缺陷。

⇒ 本文件把"过滤只准拦构建产物、不准拦源码"这条**机制**钉住。
【为什么锚机制不锚数值】断言写成"某文件必须恰好 N 处"会随收口合法变化
（本仓已因此红过三条 CI）；这里断言的是**判据函数本身的输入输出**与**集合关系**。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: 收集器唯一的输入面（与 contract_diff 里两处收集器保持一致）。
COLLECTOR_INPUTS = (
    (frozenset({".ts", ".tsx"}), ("yunshu-ui/src",)),
    (frozenset({".html", ".js"}), ("templates", "static")),
)


@pytest.fixture(scope="module")
def cd():
    path = ROOT / "scripts" / "audit" / "contract_diff.py"
    spec = importlib.util.spec_from_file_location("cd_filter", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cd_filter"] = mod
    spec.loader.exec_module(mod)
    return mod


class Test构建产物判据:
    """`_is_frontend_build_artifact` 只准拦产物，不准拦源码。"""

    @pytest.mark.parametrize("rel, want, why", [
        # —— 必须**放行**（源码）——
        ("yunshu-ui/src/pages/hub/assets/index.tsx", False, "被误伤过的真实源码文件"),
        ("yunshu-ui/src/pages/hub/assets/sub/panel.tsx", False, "源码子目录"),
        ("yunshu-ui/src/assets/logo.ts", False, "名字叫 assets 的源码目录"),
        ("yunshu-ui/src/api/endpoints.ts", False, "端点常量层本身"),
        # —— 必须**拦住**（产物）——
        ("static/assets/index-BjzRX7wB.js", True, "Vite 入口产物"),
        ("static/assets/AdminGuard-BgOtL5QH.js", True, "Vite 分包"),
        ("static/assets/skill-center-Qn6yU5-7.js", True, "名字里带连字符的分包"),
    ])
    def test_判据(self, cd, rel, want, why):
        got = cd._is_frontend_build_artifact(rel)
        assert got is want, (
            "构建产物判据对「" + rel + "」判定为 " + str(got)
            + "，期望 " + str(want) + "（" + why + "）。\n"
            "判错方向有两种后果，都要防：\n"
            "  · 源码被判成产物 ⇒ **静默少给文件**，门禁与对拍都会失明（本次的真实成因）；\n"
            "  · 产物被判成源码 ⇒ stray 计数被几百个构建产物灌爆，门禁失去意义。"
        )

    def test_产物拦不住会灌爆计数(self, cd):
        """反向保证：`static/assets/` 下的文件必须**全部被某一条排除规则拦住**。

        这条断言的是**量级**：本仓 `static/assets/` 实测 119 个文件，
        若排除失效，stray 会从 2 变成几百，门禁立刻失去分辨力。

        【为什么要按"两个排除规则"判，而不是只判产物判据】实测该目录 119 个文件里，
        62 个是构建产物（`index-BjzRX7wB.js`），57 个是 **sourcemap**（`....js.map`）——
        后者由收集器里**另一条**规则（`rel.endswith(".map")`）排除。
        只判产物判据会把 57 个 sourcemap 误报成"漏网"。故这里复算的是
        「两条规则**合起来**是否覆盖目录全部内容」—— 那才是门禁真正依赖的性质。
        """
        static_assets = cd.ROOT / "static" / "assets"
        if not static_assets.exists():
            pytest.skip("本工作树没有 static/assets（构建产物未生成）—— CI 上同样可能不存在")
        files = [f for f in static_assets.iterdir() if f.is_file()]
        if not files:
            pytest.skip("static/assets 为空")

        def excluded(name: str) -> bool:
            rel = "static/assets/" + name
            return cd._is_frontend_build_artifact(rel) or rel.endswith(".map")

        leaked = sorted(f.name for f in files if not excluded(f.name))
        assert not leaked, (
            "static/assets 下有文件**不被任何排除规则拦住**，实测漏网 " + str(len(leaked))
            + "/" + str(len(files)) + "：\n  " + "\n  ".join(leaked[:10])
            + "\n后果：这些文件会进入 stray 计数并把它灌爆，门禁失去分辨力。"
        )


class Test收集器不吞源码:
    """收集器必须覆盖 `src` 下的**全部**源文件（与一次不剪枝的遍历逐条一致）。"""

    def test_源码目录不被吞(self, cd):
        """两个方向都要断言，缺一个就会漏掉一类错误。

        【方向 A：源码不得被吞】被误伤的文件在"按后缀贪心"的集合里、却不在收集器产出里。
        【方向 B：产物不得被放行】反过来，收集器产出里不得出现被过滤规则明确排除的产物。

        【为什么必须写成"两个方向"】只写 A，会把"产物被放行"当成正常；
        只写 B，会把"源码被吞"当成正常 —— 而这两种错误恰好都发生过/差点发生。
        """
        import os
        for suffixes, dirs in COLLECTOR_INPUTS:
            collected = set()
            for p in cd._iter_source_files(suffixes, dirs):
                rel = str(p.relative_to(cd.ROOT)).replace("\\", "/")
                if cd._is_frontend_build_artifact(rel) or rel.endswith(".map"):
                    continue
                collected.add(rel)

            greedy = set()
            for d in dirs:
                base = cd.ROOT / d
                if not base.exists():
                    continue
                for dirpath, _dirnames, filenames in os.walk(base):
                    for name in filenames:
                        if Path(name).suffix not in suffixes:
                            continue
                        greedy.add(str((Path(dirpath) / name).relative_to(cd.ROOT)).replace("\\", "/"))

            # 方向 A：贪心里**不是产物**的，必须全部被收集
            expected = {r for r in greedy if not cd._is_frontend_build_artifact(r)}
            missing = sorted(expected - collected)
            assert not missing, (
                "以下**源码文件**没有被收集器覆盖（被目录过滤吞掉了）：\n  "
                + "\n  ".join(missing[:10])
                + ("\n  ...共 " + str(len(missing)) + " 个" if len(missing) > 10 else "")
                + "\n后果：它们既不计入 frontend_literals（去重路径数），也不计入 stray（收口进度），"
                  "且**整段逃出「前端调用 → 后端路由」的对拍** —— 本仓实测因此藏住了一个 404 缺陷。"
            )

            # 方向 B：收集器不得放行产物
            leaked = sorted(r for r in collected if cd._is_frontend_build_artifact(r))
            assert not leaked, (
                "以下**构建产物**混进了收集器产出（过滤没拦住）：\n  "
                + "\n  ".join(leaked[:10])
                + "\n后果：stray 计数被构建产物灌爆，门禁失去分辨力。"
            )

    def test_已知源码目录确实在内(self, cd):
        """把曾经被误伤的**具体路径**钉住 —— 它是最容易被再次吞掉的那个。"""
        target = "yunshu-ui/src/pages/hub/assets/index.tsx"
        got = {str(p.relative_to(cd.ROOT)).replace("\\", "/")
               for p in cd._iter_source_files({".ts", ".tsx"}, ["yunshu-ui/src"])}
        assert target in got, (
            target + " 不在收集器输入里 —— 该路径曾被 \"/assets/\" 子串过滤误伤，"
            "它一消失，这条路径上的所有契约缺陷都会重新变得不可见。"
        )
        assert not cd._is_frontend_build_artifact(target), (
            target + " 被判成了构建产物 —— 它是人手写的源码，不是 Vite 产物。"
        )
