"""L31 · 守护不变量「editable == True ⟺ 该节渲染函数读取 custom_content」

背景（L21 修复的正确性所依赖的前提）
------------------------------------------------------------------
`yunshu-ui/src/pages/prompt-lab/identityPrompt.ts` 的 buildRows 用 `editable`
决定「发出内容」口径：仅可编辑节把 `custom_content` 当作真正发出的文本，其余节
一律显示后端 `emit_text`（渲染原文）。

该判据成立的**唯一依据**是一条后端不变量：

    凡 meta["editable"] is True 的节，其渲染函数必读 custom_content；
    反之，不读 custom_content 的节必不得标 editable == True。

一旦后端新增/改动渲染函数而破坏它（例如给某 toggle 节加上 custom_content 支持、
却忘记同步 editable；或给某 editable 节换了不读 custom 的渲染实现），前端就会
**显示与事实相反的发出内容** —— 这正是 L21 所要消灭的缺陷形态。

为何用行为断言、而非 inspect.getsource 做源码文本匹配
------------------------------------------------------------------
源码文本匹配（`"custom_content" in inspect.getsource(fn)`）有**假红模式**：
只要有人在某非 editable 渲染函数里写一句注释或 docstring 提到 `custom_content`
（如 `# TODO: 支持 custom_content`），该节就会被误判为"读了 custom_content"，
测试因**错误的原因**变红，误导排查。行为断言直接锚定前端真正依赖的语义
（后端是否把 custom 当成输出），且不依赖源码可得性（纯 .pyc 安装亦可运行）。

设计要点
------------------------------------------------------------------
* 遍历 SECTION_REGISTRY **全部**节 —— 不硬编码 key，将来新增节自动纳入断言，
  这才是这条不变量真正被守护的关键。
* 先收集**全部**违规再断言，失败时打印**差集**（哪节落在 S 不在 R、或反之），
  便于定位；而非在第一个违规处即中断。
* 负向对照：disabled 时 custom 绝不得出现在输出中（排除假阳性）。
* 零新依赖；不改任何产品代码。
"""
from __future__ import annotations

import pytest

from agent.system_prompt_config import SECTION_REGISTRY

# 哨兵文本：独一无二，避免与模板中的任何既有字面量碰撞（含占位符 {xx}）
SENTINEL = "ZZ_L31_SENTINEL_EDITABLE_ONLY_ZZ"


def _render(fn, key: str, *, enabled: bool) -> str:
    """以最小 sections 调用渲染函数：只给目标节 enabled/custom_content。

    各渲染函数一律用 dict.get 取值并带默认，故最小字典即可安全调用。
    返回 str(out)，异常向上抛出由调用方记录（不静默吞掉）。
    """
    out = fn({key: {"enabled": enabled, "custom_content": SENTINEL}})
    return str(out)


def _scan(sections_registry):
    """扫一遍注册表，返回 (S, R, violations, rendered)。

    S = meta["editable"] is True 的节集合
    R = 渲染函数**行为上**读取 custom_content 的节集合
    violations = [(key, 描述, 输出片段), ...]，收集全部违规以便一次性报告差集
    """
    S, R, violations = set(), set(), []
    rendered: dict[str, str] = {}

    for key, fn, meta in sections_registry:
        editable = meta.get("editable") is True
        if editable:
            S.add(key)

        # ── 启用态：不变量主体 ────────────────────────────────────────────
        try:
            out = _render(fn, key, enabled=True)
        except Exception as exc:  # noqa: BLE001  记录而非中断，保证差集完整
            S.discard(key)
            if editable:
                S.add(key)
            violations.append(
                (key, f"启用态渲染异常，无法核验不变量: {type(exc).__name__}: {exc}", "")
            )
            continue

        rendered[key] = out
        reads = SENTINEL in out
        if reads:
            R.add(key)

        if editable and not reads:
            violations.append(
                (key,
                 "editable=True 但渲染函数**未**读 custom_content "
                 "⇒ 前端会把并非真正发出的文本当成发出内容显示（L21 缺陷形态）",
                 out[:120])
            )
        elif editable and out != SENTINEL:
            violations.append(
                (key,
                 "editable=True 且读了 custom_content，但未原样返回（应为 out == SENTINEL）",
                 out[:120])
            )
        elif not editable and reads:
            violations.append(
                (key,
                 "editable!=True 但渲染函数**读**了 custom_content "
                 "⇒ 前端按 editable 收口会显示后端实际发出内容，与后端渲染不一致",
                 out[:120])
            )

        # ── 停用态：负向对照（排除假阳性）─────────────────────────────────
        try:
            out_off = _render(fn, key, enabled=False)
        except Exception as exc:  # noqa: BLE001
            violations.append(
                (key, f"停用态渲染异常: {type(exc).__name__}: {exc}", "")
            )
            continue

        if SENTINEL in out_off:
            violations.append(
                (key, "停用态仍把 custom_content 写进输出（停用节不得发出自定义内容）",
                 out_off[:120])
            )
        if editable and out_off != "":
            violations.append(
                (key, "editable 节停用后应返回空串 ''（实测契约），实际非空", out_off[:120])
            )

    return S, R, violations, rendered


def _format_diff(S: set, R: set, violations) -> str:
    """失败信息：先给差集，再给逐条违规明细。"""
    only_s = sorted(S - R)
    only_r = sorted(R - S)
    lines = [
        f"不变量被破坏：editable == True ⟺ 渲染函数读取 custom_content",
        f"  S (editable=True)          = {sorted(S)}",
        f"  R (渲染函数读取 custom)     = {sorted(R)}",
        f"  差集 S - R (标了 editable 却不读 custom) = {only_s}",
        f"  差集 R - S (读了 custom 却没标 editable)  = {only_r}",
        "",
        "违规明细：",
    ]
    for key, reason, snippet in violations:
        lines.append(f"  · [{key}] {reason}")
        if snippet:
            lines.append(f"      输出片段: {snippet!r}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
#  一、不变量主体
# ══════════════════════════════════════════════════════════════════════════


def test_editable_iff_render_reads_custom_content():
    """核心：遍历全部节，断言 S == R，失败时打印差集。"""
    S, R, violations, _ = _scan(SECTION_REGISTRY)

    assert not violations, _format_diff(S, R, violations)
    assert S == R, _format_diff(S, R, violations)


def test_registry_is_non_vacuous():
    """防空转：注册表非空，且确有 editable 节 —— 否则上面的断言是空转通过。"""
    assert len(SECTION_REGISTRY) > 0, "SECTION_REGISTRY 为空，不变量断言将空转通过"
    S, R, _, _ = _scan(SECTION_REGISTRY)
    assert S, "没有任何 editable=True 的节，S == R 会以空集空转通过"
    assert len(S) >= 1 and S == R


# ══════════════════════════════════════════════════════════════════════════
#  二、逐节行为（可定位到具体节，而非只有整体差集）
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "key,fn,meta", SECTION_REGISTRY, ids=[e[0] for e in SECTION_REGISTRY]
)
def test_section_emits_custom_content_iff_editable(key, fn, meta):
    """逐节粒度：editable ⟺ out == SENTINEL；非 editable ⇒ SENTINEL not in out。"""
    editable = meta.get("editable") is True
    out = _render(fn, key, enabled=True)

    if editable:
        assert SENTINEL in out, (
            f"[{key}] editable=True 但渲染函数未读 custom_content；输出={out[:120]!r}"
        )
        assert out == SENTINEL, (
            f"[{key}] editable=True 读了 custom_content 但未原样返回；输出={out[:120]!r}"
        )
    else:
        assert SENTINEL not in out, (
            f"[{key}] editable!=True 但渲染函数读了 custom_content；输出={out[:120]!r}"
        )


@pytest.mark.parametrize(
    "key,fn,meta", SECTION_REGISTRY, ids=[e[0] for e in SECTION_REGISTRY]
)
def test_disabled_never_emits_custom_content(key, fn, meta):
    """负向对照：停用节不得发出 custom_content（排除假阳性）。

    editable 节停用后另有更严契约：返回空串 ''。
    """
    out_off = _render(fn, key, enabled=False)

    # 对所有节成立：停用 ⇒ custom 绝不出现
    assert SENTINEL not in out_off, (
        f"[{key}] 停用态仍把 custom_content 写进输出；输出={out_off[:120]!r}"
    )
    # 仅对 editable 节成立（identity/principles 实测停用返回 ''）
    if meta.get("editable") is True:
        assert out_off == "", (
            f"[{key}] editable 节停用后应返回空串 ''，实际={out_off[:120]!r}"
        )


# ══════════════════════════════════════════════════════════════════════════
#  三、口径自证：前端 buildRows 的两条分支都能被本条不变量覆盖
# ══════════════════════════════════════════════════════════════════════════


def test_both_branches_are_exercised():
    """确认注册表里**两种**节都存在 —— 不变量才有判别力。

    若某天所有节都变成 editable（或都不 editable），S == R 仍可能成立但已失去
    守护意义，这里显式要求两个分支各至少一节。
    """
    S, R, violations, _ = _scan(SECTION_REGISTRY)
    non_editable = {k for k, _fn, m in SECTION_REGISTRY if m.get("editable") is not True}

    assert S == R, _format_diff(S, R, violations)
    assert S, "缺少 editable 节，无法守护『可编辑节以 custom 为准』分支"
    assert non_editable, "缺少非 editable 节，无法守护『按 editable 收口』分支"
