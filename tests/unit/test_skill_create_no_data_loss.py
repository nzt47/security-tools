# -*- coding: utf-8 -*-
"""F1b-C 单测：`SkillFileStore.create()` 不得静默丢字段 / 行尾不得随平台变化。

背景（G1-C 卡的 F1b-C 前置；上游是 F1b）：
    F1b 把 `update_meta` 改成**最小侵入**（`patch_front_matter`）后，写路径上仍剩
    `create()` 走 `SkillMDParser.serialize` + `Path.write_text`，两处残余：

      ① **静默丢字段**：`serialize()` 按 `_META_FIELDS` 白名单裁剪。update 侧这还
         说得通（白名单 = "允许改什么"，文件里的既有内容另有保留规则），但 create
         侧**白名单同时被当成了"保留什么"**，而 create() 是新文件**唯一**的内容来源
         ⇒ 调用方传进来的白名单外键直接消失，且无任何日志。
         （G1-C 第 1 步要用 `create()` 新建 5 个含 `description_zh` 的 skill.md，
          正是这条路径；G1-B 的 M2 也差点踩到同一个坑。）
      ② **行尾随平台变化**：`write_text` 默认 `newline=None`，Windows 上把 `\n`
         翻成 `\r\n` ⇒ 同一个 `create()` 在 Windows / Linux 产出**不同字节**。
         `.index/cache.json` 存的是 skill.md 的**原始字节 md5** ⇒ 平台间 hash 不通用。

修复（`agent/skills_mgmt/file_store.py`）：
    · `SkillMDParser.serialize(..., only_meta_fields=False)`：新增开关，create 侧原样
      写出调用方给出的**全部**键；默认 True 保持既有调用方的契约不变。
    · `create()` 改用 `open(..., newline="")` 写盘（与 `update_meta` 同一写法）
      ⇒ 落盘字节 == serialize() 文本，跨平台确定。

【注意】`parse()` 的白名单过滤**不变** —— 它管的是"读出来给谁看"，与"写进去丢不丢"
是两件事；本文件在 F1b-C-3 里显式把这条契约也钉住，避免"顺手放宽读侧白名单"。

本文件全部使用 tmp_path，绝不触碰生产 data/skills_repo。
"""

import pytest

from agent.skills_mgmt.file_store import (
    SkillFileStore,
    SkillMDParser,
    _META_FIELDS,
)

#: 覆盖 F1b-C ①：两个标量 + 一个嵌套结构（嵌套结构最容易被"顺手序列化"吃掉）
OUTSIDE = {
    "unknown_custom_field": "KEEP_ME",
    "created_at": "2026-01-02T03:04:05",
    "custom_nested": {"a": 1, "b": [1, 2]},
}

BASE_META = {
    "name": "Probe Skill",
    "description": "EN description",
    "description_zh": "中文说明",
    "enabled": True,
    "status": "approved",
}

SID = "f1bc-probe"


@pytest.fixture
def store(tmp_path):
    st = SkillFileStore(repo_path=str(tmp_path / "skills_repo"))
    # 硬约束：绝不指向生产 data/skills_repo
    assert str(st.repo_path.resolve()).startswith(str(tmp_path.resolve()))
    return st


def _create(store, meta=None):
    store.create(SID, dict(meta if meta is not None else {**BASE_META, **OUTSIDE}),
                 instruction="# body line")
    return store.repo_path / SID / "skill.md"


def _fm_key_lines(text: str):
    """front matter 里的顶层键名集合（不引 yaml，直接按行取）"""
    assert text.startswith("---")
    body = text.split("---", 2)[1]
    return {ln.split(":", 1)[0].strip()
            for ln in body.splitlines()
            if ln[:1].isalpha() and ":" in ln}


# ════════════════════════════════════════════════════════════
#  F1b-C-1  白名单外字段不得被静默丢弃
# ════════════════════════════════════════════════════════════

class TestCreatePreservesAllMetaFields:

    def test_out_of_whitelist_keys_land_in_file(self, store):
        """默认契约：调用方给什么就写什么（修复前 3/3 被静默丢弃）"""
        md = _create(store)
        text = md.read_text(encoding="utf-8")
        missing = [k for k in OUTSIDE if k + ":" not in text]
        assert missing == [], f"create() 仍丢弃白名单外字段: {missing}"

    def test_nested_value_round_trips_through_yaml(self, store):
        """嵌套结构必须能被 yaml.safe_load 原样读回（不只是"字符串在文件里"）"""
        import yaml
        md = _create(store)
        fm = md.read_text(encoding="utf-8").split("---", 2)[1]
        loaded = yaml.safe_load(fm)
        assert loaded["custom_nested"] == {"a": 1, "b": [1, 2]}
        assert loaded["unknown_custom_field"] == "KEEP_ME"
        assert loaded["created_at"] == "2026-01-02T03:04:05"

    def test_id_is_forced_to_skill_id(self, store):
        """既有契约不变：create 的 id 一律以入参 skill_id 为准"""
        md = _create(store, {**BASE_META, "id": "WRONG-ID", **OUTSIDE})
        assert _fm_key_lines(md.read_text(encoding="utf-8")) >= {"id"}
        assert "id: f1bc-probe" in md.read_text(encoding="utf-8")
        assert "id: WRONG-ID" not in md.read_text(encoding="utf-8")

    def test_whitelist_semantics_on_read_unchanged(self, store):
        """F1b-C-3：`parse()` 仍按白名单**读**（读侧契约不许被顺手放宽）"""
        md = _create(store)
        parsed, body = SkillMDParser.parse(md.read_text(encoding="utf-8"))
        assert set(parsed) <= _META_FIELDS
        assert [k for k in OUTSIDE if k in parsed] == []
        assert body == "# body line"

    def test_survives_a_followup_update_meta(self, store):
        """create 之后任何一次 update_meta（= 技能启停）不得把新键删掉（与 F1b 同组）"""
        md = _create(store)
        store.update_meta(SID, {"enabled": False})
        text = md.read_text(encoding="utf-8")
        assert [k for k in OUTSIDE if k + ":" not in text] == []
        assert "enabled: false" in text

    def test_serialize_default_contract_unchanged(self):
        """反证：`serialize()` 的默认行为（only_meta_fields=True）不许被我改掉 ——
        它另有调用方（把既有 front matter 重新序列化），默认必须仍是白名单裁剪。"""
        out = SkillMDParser.serialize({**BASE_META, **OUTSIDE}, "# b")
        assert [k for k in OUTSIDE if k + ":" in out] == []
        out2 = SkillMDParser.serialize({**BASE_META, **OUTSIDE}, "# b",
                                       only_meta_fields=False)
        assert [k for k in OUTSIDE if k + ":" not in out2] == []


# ════════════════════════════════════════════════════════════
#  F1b-C-2  行尾必须跨平台确定（不随 os.linesep 变）
# ════════════════════════════════════════════════════════════

class TestCreateEolDeterministic:

    def test_created_file_is_lf_only(self, store):
        raw = _create(store).read_bytes()
        assert raw.count(b"\r\n") == 0, (
            "create() 仍写出了 CRLF ⇒ 行尾随平台变化（Windows 上 newline=None "
            "会把 \\n 翻成 \\r\\n）")

    def test_bytes_equal_serialize_output(self, store):
        """落盘字节必须逐字节等于 serialize() 的文本（即"没有换行翻译"）"""
        md = _create(store)
        # 与 create() 内部的键序一致：meta 原序 + 末尾补 id
        expect = SkillMDParser.serialize(
            {**BASE_META, **OUTSIDE, "id": SID}, "# body line",
            only_meta_fields=False)
        assert md.read_bytes() == expect.encode("utf-8")

    def test_eol_matches_git_blob_form(self, store):
        """与 git 仓库里 skill.md 的 blob 形态一致（LF）。

        实测依据：`git show HEAD:data/skills_repo/<pd-*>/skill.md` 的 blob 中
        CRLF 出现 0 次；工作区里的 CRLF 只是 `core.autocrlf=true` 的 checkout 产物。
        """
        raw = _create(store).read_bytes()
        assert raw.endswith(b"\n") is False or raw.count(b"\r") == 0

    def test_update_meta_preserves_lf_after_create(self, store):
        md = _create(store)
        store.update_meta(SID, {"enabled": False})
        assert md.read_bytes().count(b"\r\n") == 0
