# -*- coding: utf-8 -*-
"""ContextAssembler 经验库层单测（方案 P4）。

核心不变量：
  1. 未传 experience_fn ⇒ 该层恒空，system_text 与接入前**逐字相同**（零行为变化）
  2. 经验段必须渲染在**最末**（前缀缓存契约：易变内容后置）
  3. experience_fn 抛异常 ⇒ 静默降级为空，主链路不受影响
"""
from __future__ import annotations

import pytest

from agent.context.assembler import ContextAssembler


def _exp(i=1):
    return [{"id": "e%d" % i, "title": "t", "content": "[经验] 修复 pytest 断言失败",
             "source": "2026-09-20"}]


def test_no_experience_fn_is_noop():
    """未配置经验源时，输出与不传该参数逐字一致。"""
    a = ContextAssembler(token_budget=3000)
    b = ContextAssembler(token_budget=3000, experience_fn=None)
    ca, cb = a.assemble("任务"), b.assemble("任务")
    assert ca.experience_notes == [] and cb.experience_notes == []
    assert ca.system_text == cb.system_text
    assert ca.layer_tokens.get("experience") == 0


def test_experience_layer_populated():
    asm = ContextAssembler(token_budget=3000, experience_fn=lambda t: _exp())
    ctx = asm.assemble("修复 pytest")
    assert len(ctx.experience_notes) == 1
    assert ctx.experience_notes[0]["id"] == "e1"
    assert ctx.layer_tokens["experience"] > 0
    assert ctx.summary()["experience_hit"] == ["e1"]


def test_experience_rendered_last_for_prefix_cache():
    """【关键】经验段必须排在【可用工具】之后 —— 它逐轮变化。

    system_prompt_config.py:436-439 要求"稳定节前置、易变节后置"；
    经验段若前置会击穿其后全部 DeepSeek 前缀缓存。
    """
    asm = ContextAssembler(token_budget=3000, experience_fn=lambda t: _exp())
    text = asm.assemble("修复 pytest").system_text
    assert "【相关经验" in text
    assert text.index("【可用工具】") < text.index("【相关经验"), \
        "经验段必须在可用工具之后（前缀缓存契约）"
    # 且必须是最后一段
    assert text.rstrip().endswith("修复 pytest 断言失败")


def test_experience_fn_exception_degrades():
    def boom(_t):
        raise RuntimeError("检索挂了")
    asm = ContextAssembler(token_budget=3000, experience_fn=boom)
    ctx = asm.assemble("任务")
    assert ctx.experience_notes == []
    assert "【相关经验" not in ctx.system_text


def test_experience_skips_empty_content():
    asm = ContextAssembler(token_budget=3000,
                           experience_fn=lambda t: [{"id": "x", "content": ""},
                                                    {"id": "y", "content": "有内容"}])
    ctx = asm.assemble("任务")
    assert [e["id"] for e in ctx.experience_notes] == ["y"]


def test_experience_fn_none_return_ok():
    asm = ContextAssembler(token_budget=3000, experience_fn=lambda t: None)
    assert asm.assemble("任务").experience_notes == []


def test_render_text_includes_experience_with_source():
    asm = ContextAssembler(token_budget=3000, experience_fn=lambda t: _exp())
    ctx = asm.assemble("修复 pytest")
    out = asm.render_text(ctx)
    assert "[相关经验" in out
    assert "2026-09-20" in out, "方案要求强制附带来源与日期"
    # 统计行仍在最末（footer 语义）
    assert out.rstrip().splitlines()[-1].startswith("[上下文统计]")


def test_guarded_path_also_carries_experience():
    """守卫路径（assemble_guarded）走 _rebuild_system_text，须同样带上经验段。"""
    asm = ContextAssembler(token_budget=3000, experience_fn=lambda t: _exp())
    ctx = asm.assemble_guarded("修复 pytest")
    assert ctx.experience_notes
    assert "【相关经验" in ctx.system_text
