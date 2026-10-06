"""K4 收口守卫：前端**启发式 helper 已删除且调用点归零**（P1-front 第七批 · 2026-10-05）。

【钉的是什么】「pickObj / pickList」这两个 helper 做的是
「在若干候选键里猜哪个是业务载荷」——**那不是契约**：后端换一种包法，前端会静默读错而不是报错。
P1-front 逐端点把被消费端点迁到统一信封后（第三~七批），全部调用点已换成显式解析，
两个 helper 也从 pages/hub/components/ui.tsx 删除了。本文件防它**悄悄回来**。

【判据口径（与计数纪律同源）】**先剥注释再计数** —— 否则描述这件事的注释本身会把计数推高
（本仓在推进 §2.2 记过：我自己的注释让「还剩多少处」多出两处）。
剥注释复用 scripts.audit.contract_diff.strip_comments（同一实现，避免第二份口径）。

【为什么不用「文本里出现 pickObj 就算失败」】那会把注释与文档也算进来；
本文件断言的是**代码里没有这个标识符**，而 strip_comments 正好把注释去掉。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.timeout(300)

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "yunshu-ui" / "src"

_SUFFIXES = (".ts", ".tsx")
_PATTERN = re.compile(r"\b(pickObj|pickList)\b")


def _strip_imports(src: str) -> str:
    """去掉 import 语句（可能跨行）—— import 行不是调用点。"""
    return re.sub(r"^import\s[\s\S]*?from\s+['\"][^'\"]+['\"];?", "", src, flags=re.M)


def _count_hits(src: str, strip_comments) -> list:
    """返回剥注释后的命中原行（供失败信息直接展示）。"""
    stripped = _strip_imports(strip_comments(src))
    return [ln.strip() for ln in stripped.splitlines() if _PATTERN.search(ln)]


@pytest.fixture(scope="module")
def strip_comments():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from scripts.audit.contract_diff import strip_comments as sc
    return sc


def _scan(strip_comments) -> dict:
    hits: dict = {}
    for path in sorted(SRC.rglob("*")):
        if path.suffix not in _SUFFIXES or not path.is_file():
            continue
        found = _count_hits(path.read_text(encoding="utf-8"), strip_comments)
        if found:
            hits[path.relative_to(SRC).as_posix()] = found
    return hits


def test_前端不得再出现_pickObj_pickList(strip_comments):
    """全仓前端源码里这两个标识符必须**一处都没有**（定义与调用都不许）。"""
    hits = _scan(strip_comments)
    assert hits == {}, (
        "前端又出现了 pickObj/pickList（实测：" + repr(hits) + "）。两个失败方向都要看清：\n"
        "  · 若命中的是**调用点** ⇒ 该端点还没迁，或改回了启发式：请按 src/api/envelope.ts 的\n"
        "    getEnvelope/postEnvelope（自己发请求）或 unwrapEnvelopeBody（已解析的体）显式解析；\n"
        "  · 若命中的是**定义** ⇒ 有人把 helper 加回来了：那是把「猜」重新变成可选项，\n"
        "    而它的失败模式是**静默**的（读到 undefined 而不是报错）。\n"
        "迁移清单与端点→消费方对照见 docs/closeout/ 的第五轮各篇。"
    )


def test_判据对合成样例成立(strip_comments):
    """判据的两个方向：**真调用**要数得到；**注释里的提及**不算。

    【为什么必须钉这一条】只断言「真实源码里 0 处」的话，一个写坏了的判据
    （比如正则写错、或者剥注释把代码也剥掉了）同样会报 0 —— 那是**假绿**。
    """
    real_call = "const xs = pickList<Foo>(resp, 'items')\nconst o = pickObj(resp)\n"
    assert len(_count_hits(real_call, strip_comments)) == 2, "真调用没被数到 ⇒ 判据失效（假绿）"

    only_comments = (
        "// 迁移前这里用 pickObj 猜形状，现已改为 getEnvelope\n"
        "/* pickList 的候选键语义见旧实现 */\n"
        "const ok = 1\n"
    )
    assert _count_hits(only_comments, strip_comments) == [], (
        "注释里的提及被算成了调用 ⇒ 判据会把文档也算成违规（本仓记过这个坑）"
    )

    import_line = "import { pickList, pickObj } from './ui'\nconst x = 1\n"
    assert _count_hits(import_line, strip_comments) == [], "import 行不是调用点，不该计入"
