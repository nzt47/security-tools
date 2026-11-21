"""分身身份提示词方案库的守卫用例（2026-11-21）。

【为什么要有它】方案库是**安全边界的一部分**（角色正文决定分身以什么身份行动），
且它按角色类型多套提供给用户直接复制到 `role_text`。三类回退必须被钉住：

1. 角色覆盖漂移：词表新增角色、方案库忘了补 ⇒ 那个角色在 UI 里是空的；
2. 合同被破坏：正文超 `ROLE_TEXT_MAX_CHARS`、含 `{}` 占位符（role_text 不做插值）、
   或 id 重复 —— 用户复制过去会在创建分身时被后端拒；
3. 两份清单分叉：方案库自己写死角色标题（应与 `role_templates` 单一来源一致）。
"""
from __future__ import annotations

import pytest

from agent.subagent.role_prompt_library import (
    ROLE_PROMPT_SETS,
    prompt_sets_catalog,
    validate_library,
)
from agent.subagent.role_templates import (
    ROLE_TEMPLATES,
    ROLE_TEMPLATE_IDS,
    ROLE_TEXT_MAX_CHARS,
    role_catalog,
)


class Test库自检:
    def test_自检无问题(self):
        assert validate_library() == [], "方案库自检不通过：" + repr(validate_library())

    def test_每个受控角色都有至少两套(self):
        missing = [tid for tid in ROLE_TEMPLATE_IDS if len(ROLE_PROMPT_SETS.get(tid, ())) < 2]
        assert not missing, "这些角色不足两套方案：" + repr(missing)

    def test_方案库不定义词表之外的角色(self):
        extra = sorted(set(ROLE_PROMPT_SETS) - set(ROLE_TEMPLATE_IDS))
        assert not extra, "方案库出现了词表之外的角色 id：" + repr(extra)


class Test合同:
    def test_每套都非空且不超_role_text_上限(self):
        for tid, sets in ROLE_PROMPT_SETS.items():
            for s in sets:
                assert s.body.strip(), tid + " / " + s.id + " 正文为空"
                assert 80 <= len(s.body) <= ROLE_TEXT_MAX_CHARS, (
                    tid + " / " + s.id + " 长度异常：" + str(len(s.body))
                    + "（要 ≥80 才算成稿，且不得超过 " + str(ROLE_TEXT_MAX_CHARS) + "）"
                )
                assert s.scene.strip(), tid + " / " + s.id + " 缺少适用场景说明"

    def test_正文不含占位符(self):
        for sets in ROLE_PROMPT_SETS.values():
            for s in sets:
                assert "{" not in s.body and "}" not in s.body, (
                    s.id + " 含 {} 占位符 —— role_text 不做插值，用户复制过去会原样出现"
                )

    def test_同一角色的方案_id_唯一(self):
        for tid, sets in ROLE_PROMPT_SETS.items():
            ids = [s.id for s in sets]
            assert len(ids) == len(set(ids)), tid + " 方案 id 重复：" + repr(ids)


class Test投影:
    def test_目录顺序与角色词表一致且标题取自词表(self):
        groups = prompt_sets_catalog()
        assert [g["role_id"] for g in groups] == list(ROLE_TEMPLATE_IDS), (
            "方案库投影顺序必须跟受控词表一致（UI 按它排角色）"
        )
        for g in groups:
            assert g["role_title"] == ROLE_TEMPLATES[g["role_id"]].title, (
                g["role_id"] + " 的角色标题与词表不一致 ⇒ 两份清单分叉"
            )
            assert g["role_note"] == ROLE_TEMPLATES[g["role_id"]].note

    def test_角色视图带上方案库(self):
        cat = role_catalog()
        assert cat.get("prompt_sets") == prompt_sets_catalog(), (
            "role_catalog() 里的 prompt_sets 必须就是方案库投影（GET /api/subagent/list 的 role 段）"
        )

    def test_每套都带_chars_字段(self):
        for g in prompt_sets_catalog():
            for s in g["sets"]:
                assert s["chars"] == len(s["body"]), s["id"] + " 的 chars 与正文长度不一致"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
