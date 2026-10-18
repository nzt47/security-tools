# -*- coding: utf-8 -*-
"""前端页面「可达性」审计的守卫（2026-10-18）。

【锁死三件事】
  1. 解析口径：`@/x` 别名、相对路径、省略扩展名、目录 index、import.meta.glob；
  2. 判据口径：**只有入口闭包内的导入才算"进门"** —— 被"没人路由的页面"导入的
     文件同样不可达（岛屿依赖），这是"引用 ≠ 可达"最容易漏的一半；
  3. 产物与台账：提交产物必须与重算一致；告警台账必须双向一致
     （新告警未登记 / 修好后没删 都会红）。
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _mod():
    return importlib.import_module("scripts.audit.ui_reachability")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """合成 repo：ROOT/UI_SRC/PAGES_DIR/ENTRY 全部指到 tmp_path。"""
    m = _mod()
    src = tmp_path / "yunshu-ui" / "src"
    (src / "pages").mkdir(parents=True)
    monkeypatch.setattr(m, "ROOT", tmp_path)
    monkeypatch.setattr(m, "UI_SRC", src)
    monkeypatch.setattr(m, "PAGES_DIR", src / "pages")
    monkeypatch.setattr(m, "ENTRY", "yunshu-ui/src/main.tsx")
    return m, src


class Test解析口径:
    def test_别名_相对_扩展名与目录_index(self, tree):
        m, src = tree
        _write(src / "util" / "x.ts", "export const a = 1\n")
        _write(src / "widgets" / "index.tsx", "export default function W(){return null}\n")
        # 真实引用方目录必须存在：POSIX 下 stat("a/不存在/../b") 会 ENOENT（Windows 会做词法规约）。
        # 这不是被测代码的问题（import 一定发生在真实目录里的文件），但测试基准必须像真的。
        (src / "workbench").mkdir(parents=True, exist_ok=True)
        assert m.resolve_spec("@/util/x", src) == "yunshu-ui/src/util/x.ts"
        assert m.resolve_spec("./util/x", src / "workbench") is None, "相对路径按**引用方目录**解析"
        assert m.resolve_spec("../util/x", src / "workbench") == "yunshu-ui/src/util/x.ts"
        assert m.resolve_spec("@/widgets", src) == "yunshu-ui/src/widgets/index.tsx"
        assert m.resolve_spec("react", src) is None, "第三方包不参与可达性"

    def test_静态_动态_副作用与_glob_都算依赖(self, tree):
        m, src = tree
        _write(src / "a.ts", "export const a = 1\n")
        _write(src / "b.ts", "export const b = 1\n")
        _write(src / "c.ts", "export const c = 1\n")
        _write(src / "pages" / "auto" / "x.tsx", "export default function X(){return null}\n")
        text = (
            "import A from './a'\n"
            "export { b } from './b'\n"
            "import './c'\n"
            "const Lazy = lazy(() => import('./a'))\n"
            "const mods = import.meta.glob('./pages/auto/*.tsx', { eager: true })\n"
        )
        deps = m.parse_imports(text, src)
        assert deps == [
            "yunshu-ui/src/a.ts",
            "yunshu-ui/src/b.ts",
            "yunshu-ui/src/c.ts",
            "yunshu-ui/src/pages/auto/x.tsx",
        ]


class Test判据:
    def test_闭包外的页面与其依赖都算不可达(self, tree):
        m, src = tree
        _write(src / "main.tsx", "import App from './App'\n")
        _write(src / "App.tsx", "const P = lazy(() => import('@/pages/used'))\nexport default function App(){return null}\n")
        _write(src / "pages" / "used.tsx", "export default function U(){return null}\n")
        _write(src / "pages" / "orphan.tsx", "import './island'\nexport default function O(){return null}\n")
        _write(src / "pages" / "island.tsx", "export default function I(){return null}\n")
        payload = m.classify()
        assert [e["path"] for e in payload["unreachable"]] == [
            "yunshu-ui/src/pages/island.tsx",
            "yunshu-ui/src/pages/orphan.tsx",
        ], "被孤儿页导入 ≠ 可达（岛屿依赖必须一起报）"
        assert payload["orphan_default"] == []

    def test_default_无人消费才算孤儿(self, tree):
        m, src = tree
        _write(src / "main.tsx", "import App from './App'\n")
        _write(src / "App.tsx", (
            "import Used, { Named } from '@/pages/withdefault'\n"
            "import { Only } from '@/pages/namedonly'\n"
            "import '@/pages/sideonly'\n"
            "export default function App(){return null}\n"
        ))
        _write(src / "pages" / "withdefault.tsx", "export default function W(){return null}\n")
        _write(src / "pages" / "namedonly.tsx", "export function Only(){return null}\nexport default function N(){return null}\n")
        _write(src / "pages" / "sideonly.tsx", "export default function S(){return null}\n")
        payload = m.classify()
        assert payload["unreachable"] == []
        assert [e["path"] for e in payload["orphan_default"]] == [
            "yunshu-ui/src/pages/namedonly.tsx",
            "yunshu-ui/src/pages/sideonly.tsx",
        ]

    def test_注释里提到的_import_不算依赖(self, tree):
        """本仓注释常写"见 pages/hub/x import '@/y'"。不剥注释 ⇒ 假边 ⇒ 漏报。"""
        m, src = tree
        _write(src / "main.tsx", "// import C from './pages/commented'\nimport App from './App'\n")
        _write(src / "App.tsx", "/* import D from './pages/commented2' */\nexport default function A(){return null}\n")
        _write(src / "pages" / "commented.tsx", "export default function C(){return null}\n")
        _write(src / "pages" / "commented2.tsx", "export default function D(){return null}\n")
        payload = m.classify()
        assert [e["path"] for e in payload["unreachable"]] == [
            "yunshu-ui/src/pages/commented.tsx",
            "yunshu-ui/src/pages/commented2.tsx",
        ]

    def test_glob_发现的页面算可达(self, tree):
        m, src = tree
        _write(src / "main.tsx", "import './registry'\n")
        _write(src / "registry.ts", "const m = import.meta.glob('./pages/auto/*.tsx', { eager: true })\n")
        _write(src / "pages" / "auto" / "x.tsx", "export default function X(){return null}\n")
        payload = m.classify()
        assert payload["unreachable"] == [], "import.meta.glob 与 import 同权（构建期静态发现）"

    def test_lazy_带_then_的具名导入不算消费_default(self, tree):
        m, src = tree
        _write(src / "main.tsx", "import App from './App'\n")
        _write(src / "App.tsx", (
            "const A = lazy(() => import('@/pages/named').then((m) => ({ default: m.Named })))\n"
            "export default function App(){return null}\n"
        ))
        _write(src / "pages" / "named.tsx", "export function Named(){return null}\nexport default function N(){return null}\n")
        payload = m.classify()
        assert payload["orphan_default"], "只取具名导出时 default 仍是死代码"


class Test真实仓库:
    def test_产物与工作树一致(self):
        m = _mod()
        artifact = json.loads((ROOT / "reports" / "ui_reachability.json").read_text(encoding="utf-8"))
        fresh = m.classify()
        fresh["page_files"] = m.page_files()
        assert artifact.get("content_hash") == m.content_hash(fresh), (
            "可达性结论已漂移：运行 python scripts/audit/ui_reachability.py 重新派生并提交产物"
        )

    def test_告警台账双向一致(self):
        m = _mod()
        payload = m.classify()
        findings = [e["path"] for e in payload["unreachable"]] + [e["path"] for e in payload["orphan_default"]]
        triage = m.load_triage()
        untracked, stale = m.triage_problems(findings, triage)
        assert untracked == [], "新增了未登记的告警：" + repr(untracked)
        assert stale == [], "台账里留有已消解的陈旧项：" + repr(stale)
        assert all((v or {}).get("status") in m.TRIAGE_STATUSES for v in triage.values())

    def test_上一轮的两个坑仍被同一条判据看见(self):
        """#1202 的两个教训：没人路由的页面 / 只被具名引用的 default。

        这条用例的价值不在"现在有 2 条"，而在于**判据真的能看见那两种形态** ——
        它们当时都是扫描器全绿、用户点不到。
        """
        m = _mod()
        payload = m.classify()
        unreachable = {e["path"] for e in payload["unreachable"]}
        orphans = {e["path"] for e in payload["orphan_default"]}
        assert "yunshu-ui/src/pages/hub/session/index.tsx" in unreachable, (
            "会话叶子渲染的是 WorkbenchChatPage，这个页面没有任何入口 —— 审计必须报出来"
        )
        assert "yunshu-ui/src/pages/hub/memory/workflow.tsx" in orphans, (
            "skill-center 只取 MemoryWorkflowTable 具名导出，default 是死代码 —— 审计必须报出来"
        )
