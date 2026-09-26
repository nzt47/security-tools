"""F1b 单测：update_meta 不得静默丢数据 / 破坏格式（全部用 tmp_path）

背景（docs/audit_skill_governance/FINDINGS_DURING_IMPL.md · F1）：
    update_meta 曾走 parse → SkillMDParser.serialize 的**整文件重排**，后果：
      ① _META_FIELDS 白名单外的既有字段被静默删除；
      ② YAML 注释被静默删除；
      ③ description 引号被剥离、tags: [a, b, c] 行内列表被展开成块状、末尾换行丢失；
      ④ 任何一次技能启停（registry.set_enabled → update_meta）都产生格式噪声 diff，
         淹没 G1 的 134 文件描述改造。

修复（agent/skills_mgmt/file_store.py）：
    update_meta 改走 SkillMDParser.patch_front_matter 的**最小侵入**路径 ——
    只重写被 patch 的顶层键所在行，其余内容逐字节保留。
    白名单语义被拆成两件事：【允许改什么】只作用于 patch（白名单外的 patch 键被忽略）；
    【保留什么】以文件现状为准（不再按白名单裁剪）。

本文件不触碰生产 data/skills_repo（审计期间已发生过两处 skill.md 被误改）。
"""

import inspect

import pytest

from agent.skills_mgmt import SkillsMgmtService
from agent.skills_mgmt.file_store import SkillFileStore, SkillMDParser
from agent.skills_mgmt.registry import SkillRegistry

SKILL_ID = "demo-skill"

# 覆盖四类"易被重排吃掉"的写法：引号 / 行内列表 / 注释 / 白名单外字段
ORIGINAL = """---
id: demo-skill
name: Demo Skill
description: "a quoted description"
tags: [a, b, c]
# a hand-written comment
unknown_custom_field: KEEP_ME
config_schema:
  type: object
  properties:
    a:
      type: string
enabled: true
---

# Demo

Body content.
"""


@pytest.fixture
def store(tmp_path):
    """隔离的文件存储（repo 在 tmp_path 下，绝不指向 data/skills_repo）"""
    st = SkillFileStore(repo_path=str(tmp_path / "skills_repo"))
    # 硬约束：绝不指向生产 data/skills_repo
    assert str(st.repo_path.resolve()).startswith(str(tmp_path.resolve()))
    return st


def _make(store, text=ORIGINAL, skill_id=SKILL_ID, newline="\n"):
    """按指定行尾写一个技能目录，返回 skill.md 路径（用 bytes 写，避免换行翻译）"""
    d = store.repo_path / skill_id
    (d / "scripts").mkdir(parents=True, exist_ok=True)
    (d / "temp").mkdir(exist_ok=True)
    md = d / "skill.md"
    md.write_bytes(text.replace("\n", newline).encode("utf-8"))
    return md


def _line_diff(before: bytes, after: bytes):
    """返回 (逐行差异列表, 行数是否相同) —— 行尾不敏感，配 raw 断言用"""
    b = before.decode("utf-8").splitlines()
    a = after.decode("utf-8").splitlines()
    diff = [(i, x, y) for i, (x, y) in enumerate(zip(b, a)) if x != y]
    return diff, len(b) == len(a)


# ════════════════════════════════════════════════════════════
#  1. 不丢数据：白名单外字段 / 注释
# ════════════════════════════════════════════════════════════

class TestNoSilentDataLoss:
    def test_unknown_field_survives_update(self, store):
        """白名单外的自定义字段必须原样保留（修复前被静默删除）"""
        md = _make(store)
        store.update_meta(SKILL_ID, {"enabled": False})
        text = md.read_text(encoding="utf-8")
        assert "unknown_custom_field: KEEP_ME" in text

    def test_yaml_comment_survives_update(self, store):
        """YAML 注释必须原样保留（修复前被静默删除）"""
        md = _make(store)
        store.update_meta(SKILL_ID, {"enabled": False})
        assert "# a hand-written comment" in md.read_text(encoding="utf-8")

    def test_whitelist_outside_patch_key_changes_nothing(self, store):
        """【允许改什么】：白名单外的 patch 键被忽略 ⇒ 文件字节完全不变

        注意与上一条的区别：忽略的是"改的请求"，不是"既有的内容"。
        """
        md = _make(store)
        before = md.read_bytes()
        store.update_meta(SKILL_ID, {"unknown_custom_field": "CHANGED"})
        assert md.read_bytes() == before

    def test_read_side_whitelist_unchanged(self, store):
        """读侧白名单语义未变：parse 仍只回白名单字段（本卡只改写侧）"""
        _make(store)
        meta, _ = store._read_md(SKILL_ID)
        assert "unknown_custom_field" not in meta
        assert meta["enabled"] is True


# ════════════════════════════════════════════════════════════
#  2. 不破坏格式：引号 / 行内列表 / 末尾换行 / 行尾符
# ════════════════════════════════════════════════════════════

class TestFormatPreserved:
    def test_quoted_scalar_preserved(self, store):
        md = _make(store)
        store.update_meta(SKILL_ID, {"enabled": False})
        assert 'description: "a quoted description"' in md.read_text(encoding="utf-8")

    def test_inline_list_preserved_when_tags_not_patched(self, store):
        """未改动 tags 时，行内列表必须保持原样（修复前 1 行被展开成 9 行）"""
        md = _make(store)
        fm_before = md.read_text(encoding="utf-8").split("---")[1].count("\n")
        store.update_meta(SKILL_ID, {"enabled": False})
        text = md.read_text(encoding="utf-8")
        assert "tags: [a, b, c]" in text
        assert "tags:\n- a" not in text
        assert text.split("---")[1].count("\n") == fm_before

    def test_trailing_newline_preserved(self, store):
        md = _make(store)
        store.update_meta(SKILL_ID, {"enabled": False})
        assert md.read_bytes().endswith(b"Body content.\n")

    def test_missing_trailing_newline_is_restored(self, store):
        """原本就没有末尾换行的文件：补一个（POSIX 文本约定，之后 diff 稳定）"""
        md = _make(store, ORIGINAL.rstrip("\n"))
        store.update_meta(SKILL_ID, {"enabled": False})
        assert md.read_bytes().endswith(b"Body content.\n")

    def test_lf_stays_lf(self, store):
        md = _make(store, newline="\n")
        store.update_meta(SKILL_ID, {"enabled": False})
        assert b"\r\n" not in md.read_bytes()

    def test_crlf_stays_crlf(self, store):
        """行尾符按原文保留（Path.write_text 在 Windows 会做 \n → \r\n 翻译）"""
        md = _make(store, newline="\r\n")
        store.update_meta(SKILL_ID, {"enabled": False})
        raw = md.read_bytes()
        assert b"enabled: false\r\n" in raw
        assert raw.count(b"\r\n") == raw.count(b"\n")

    def test_multiline_value_block_replaced_without_orphans(self, store):
        """多行标量的折行（含中间空行）必须整段替换，不能残留孤儿行

        实现期回归：yaml.safe_dump 把 "a\nb" 写成 `k: 'a` + 空行 + `  b'`；
        块范围判定若在空行处截断，文件里就会留下 `  b'` 残片（本次实测捕获并修复）。
        """
        md = _make(store)
        store.update_meta(SKILL_ID, {"description": "first line\nsecond line"})
        assert store.get_metadata(SKILL_ID)["description"] == "first line\nsecond line"
        store.update_meta(SKILL_ID, {"description": "third"})
        text = md.read_text(encoding="utf-8")
        assert store.get_metadata(SKILL_ID)["description"] == "third"
        assert "second line" not in text
        assert "KEEP_ME" in text and "# a hand-written comment" in text

    def test_unicode_value_not_escaped(self, store):
        """allow_unicode=True 的既有语义不变：中文不转义（保持可评审）"""
        md = _make(store)
        store.update_meta(SKILL_ID, {"name": "中文技能名"})
        assert "name: 中文技能名" in md.read_text(encoding="utf-8")
        assert store.get_metadata(SKILL_ID)["name"] == "中文技能名"

    def test_list_value_patch_remains_parseable_after_later_write(self, store):
        """显式 patch 列表值 → 写为块状可解析，且后续写入不破坏它"""
        _make(store)
        store.update_meta(SKILL_ID, {"tags": ["x", "y"]})
        assert store.get_metadata(SKILL_ID)["tags"] == ["x", "y"]
        store.update_meta(SKILL_ID, {"enabled": False})
        assert store.get_metadata(SKILL_ID)["tags"] == ["x", "y"]
        assert store.get_metadata(SKILL_ID)["enabled"] is False

    def test_block_value_replaced_without_orphan_lines(self, store):
        """patch 值是嵌套块时，旧块的续行必须整段替换，不能残留孤儿行"""
        md = _make(store)
        store.update_meta(SKILL_ID, {"config_schema": {"type": "object"}})
        text = md.read_text(encoding="utf-8")
        assert text.split("---")[1].count("properties:") == 0
        assert "      type: string" not in text
        assert "config_schema:\n  type: object\n" in text
        assert "enabled: true" in text          # 后续行不受影响
        assert "KEEP_ME" in text and "# a hand-written comment" in text


# ════════════════════════════════════════════════════════════
#  3. 最小 diff：只有被 patch 的那一行变
# ════════════════════════════════════════════════════════════

class TestMinimalDiff:
    def test_only_enabled_line_changes(self, store):
        """改 enabled ⇒ 除该行外文本完全相同（逐行 + 字节双重断言）"""
        md = _make(store)
        before = md.read_bytes()
        store.update_meta(SKILL_ID, {"enabled": False})
        after = md.read_bytes()

        diff, same_len = _line_diff(before, after)
        assert same_len, "行数不应变化"
        assert len(diff) == 1, "只应有 1 行不同，实际: %r" % (diff,)
        assert diff[0][1].strip() == "enabled: true"
        assert diff[0][2].strip() == "enabled: false"

        # 去掉那一行后，其余字节必须完全一致（含行尾符 / 末尾换行）
        assert (before.replace(b"enabled: true", b"enabled: false") == after)

    def test_same_value_patch_keeps_bytes_identical(self, store):
        """值未变 ⇒ 一个字节都不动（重复启停不产生噪声 diff）"""
        md = _make(store)
        before = md.read_bytes()
        store.update_meta(SKILL_ID, {"enabled": True})
        assert md.read_bytes() == before

    def test_repeated_toggle_returns_to_original_bytes(self, store):
        """启停一个来回 ⇒ 文件回到原始字节（这是 G1 改造的前提）"""
        md = _make(store)
        before = md.read_bytes()
        store.update_meta(SKILL_ID, {"enabled": False})
        store.update_meta(SKILL_ID, {"enabled": True})
        assert md.read_bytes() == before

    def test_new_key_appended_inside_front_matter(self, store):
        md = _make(store, """---
id: demo-skill
enabled: true
---

Body
""")
        store.update_meta(SKILL_ID, {"author": "yunshu"})
        text = md.read_text(encoding="utf-8")
        assert "author: yunshu" in text.split("---")[1]
        assert store.get_metadata(SKILL_ID)["author"] == "yunshu"


# ════════════════════════════════════════════════════════════
#  4. 回归：enabled 读写语义 + 既有调用方
# ════════════════════════════════════════════════════════════

class TestEnabledSemanticsRegression:
    def test_enabled_read_write_via_get_metadata(self, store):
        _make(store)
        assert store.get_metadata(SKILL_ID)["enabled"] is True
        store.update_meta(SKILL_ID, {"enabled": False})
        assert store.get_metadata(SKILL_ID)["enabled"] is False
        store.update_meta(SKILL_ID, {"enabled": True})
        assert store.get_metadata(SKILL_ID)["enabled"] is True

    def test_metadata_index_reflects_change(self, store):
        """load_metadata_index（检索/模型可见的同一份来源）必须立刻反映新值"""
        _make(store)
        store.load_metadata_index()
        store.update_meta(SKILL_ID, {"enabled": False})
        assert store.load_metadata_index(refresh=True)[SKILL_ID]["enabled"] is False

    def test_other_fields_still_writable(self, store):
        """除 enabled 外的白名单字段仍然可改（不只是启停路径可用）"""
        md = _make(store)
        store.update_meta(SKILL_ID, {"status": "approved", "name": "新名字"})
        meta = store.get_metadata(SKILL_ID)
        assert meta["status"] == "approved"
        assert meta["name"] == "新名字"
        assert "KEEP_ME" in md.read_text(encoding="utf-8")

    def test_new_instruction_replaces_body_and_keeps_meta(self, store):
        md = _make(store)
        store.update_meta(SKILL_ID, {}, new_instruction="# New Body\n\nhello")
        text = md.read_text(encoding="utf-8")
        assert "# New Body" in text and "hello" in text
        assert "# Demo" not in text
        assert "KEEP_ME" in text and "# a hand-written comment" in text
        meta, body = store._read_md(SKILL_ID)
        assert body == "# New Body\n\nhello"
        assert meta["enabled"] is True
        assert text.endswith("\n")

    def test_signature_and_return_unchanged(self, store):
        """【兼容性】签名与返回语义不得改变"""
        sig = inspect.signature(SkillFileStore.update_meta)
        assert list(sig.parameters) == ["self", "skill_id", "patch",
                                       "new_instruction"]
        assert sig.parameters["new_instruction"].default is None
        _make(store)
        assert store.update_meta(SKILL_ID, {"enabled": False}) is None

    def test_missing_skill_raises_same_error(self, store):
        from agent.skills_mgmt.exceptions import SkillNotFoundError
        store.repo_path.mkdir(parents=True, exist_ok=True)
        with pytest.raises(SkillNotFoundError):
            store.update_meta("no-such-skill", {"enabled": False})

    def test_unclosed_front_matter_raises_same_error(self, store):
        from agent.skills_mgmt.exceptions import SkillFileError
        _make(store, "---\nid: demo-skill\nenabled: true\n")
        with pytest.raises(SkillFileError):
            store.update_meta(SKILL_ID, {"enabled": False})


class TestExistingCallers:
    """grep update_meta 得到的两个生产调用方仍必须工作"""

    @pytest.fixture
    def svc(self, tmp_path):
        return SkillsMgmtService(
            store_path=str(tmp_path / "skills_mgmt.json"),
            repo_path=str(tmp_path / "skills_repo"),
        )

    def test_registry_set_enabled_file_track_keeps_content(self, svc):
        """调用方 1：registry.set_enabled（文件轨）—— 启停后人工内容仍在"""
        md = _make(svc.file_store, skill_id="self_reflection")
        before_extra = "unknown_custom_field: KEEP_ME"

        reg = SkillRegistry(service=svc)
        result = reg.set_enabled("self_reflection", False)

        assert result == {"ok": True, "id": "self_reflection",
                          "enabled": False, "track": "file_track"}
        assert reg.is_enabled("self_reflection") is False
        text = md.read_text(encoding="utf-8")
        assert before_extra in text
        assert "# a hand-written comment" in text
        assert "tags: [a, b, c]" in text
        assert 'description: "a quoted description"' in text

    def test_registry_toggle_roundtrip_is_byte_stable(self, svc):
        """调用方 1 的另一入口：toggle 一个来回 ⇒ 回到原始字节"""
        md = _make(svc.file_store, skill_id="safety_guard")
        before = md.read_bytes()
        reg = SkillRegistry(service=svc)
        assert reg.toggle("safety_guard")["enabled"] is False
        assert reg.toggle("safety_guard")["enabled"] is True
        assert md.read_bytes() == before

    def test_solidify_style_status_sync(self, svc):
        """调用方 2：process_distill/solidify.py 的 front matter 状态同步"""
        md = _make(svc.file_store)
        svc.file_store.update_meta(SKILL_ID, {"status": "approved"})
        assert svc.file_store.get_metadata(SKILL_ID)["status"] == "approved"
        assert "KEEP_ME" in md.read_text(encoding="utf-8")
