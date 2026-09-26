# -*- coding: utf-8 -*-
"""G1C-UA 护栏：Layer-1 的**三条检索腿**必须共用同一份"元数据文本"口径。

## 为什么需要这份文件（本卡最重要的产出）

技能检索有 3 条腿，每条都**各自**实现过一遍"文档文本由哪些字段拼成"：

| 腿 | 实现位置 | G1C-U1 之后 |
|---|---|---|
| TF-IDF | `loader._meta_to_meta_text()` | ✅ 并入 `description_zh` |
| BM25   | `bm25_searcher._skill_to_doc()` | ❌ 同形独立实现，未同步（实测中文 2/8）|
| 向量   | `vector_adapter._build_vector_text()` 的 front matter 段 | ❌ 同形独立实现，未同步 |

G1C-U1 只修了一条腿 ⇒ "改了 A、B/C 照旧"这种**同形实现漏改**在本审计里已经出现多次；
本文件把它变成**结构性不可能**：三条腿都调用 `loader._meta_to_meta_text()`，
而"是否并入 `description_zh`"只由 `loader._include_description_zh()`（读单一 env
`CP_SKILL_META_INCLUDE_ZH`）决定。

## 本文件守的四件事（每一件都能在"只改一条腿"时变红）

1. **同一字段列表**：三腿对同一 meta 产出的 front-matter 文本**逐字相同**；
   任何一条腿自己改字段（或改回旧列表）⇒ 立刻不等 ⇒ 红。
2. **同一开关**：`CP_SKILL_META_INCLUDE_ZH` 开/关**同时**决定三条腿；
   并且 monkeypatch 掉 `loader._include_description_zh` 这一个函数体，三条腿必须
   **一起**翻转 —— 这条专门抓"某条腿自己读 env / 自己缓存开关"的假生效。
3. **没有第二个开关**：`CP_SKILL_META_INCLUDE_ZH` 字面量在 `agent/**.py` 里
   **只允许**出现在 `loader.py`（读取点）与 `settings/registry.py`（登记）两处。
4. **召回数字锁死**：BM25 腿中文 8/8、英文 8/8；置 0 ⇒ 中文回落 2/8。

## 不在本文件覆盖范围（如实声明，见 G1C-UA.md §6）

· 向量腿的**真实召回**需要加载 BGE-m3（约 18 s / 450 MB），不在单元测试预算内；
  本文件只断言向量腿的**文本构造**（= 口径出问题的地方），召回由探针实测。
· `searcher._match_score`（管理页搜索）是**另一个消费者**，它的中文并入是无条件的
  （G1-C/R-d 的"展示与搜索同源"裁定），不在本开关口径内。
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ENV = "CP_SKILL_META_INCLUDE_ZH"

#: 与 G1C-U1 报告**同一组** query，便于对拍（改前 TF-IDF 2/8、BM25 2/8）
ZH_QUERIES = (
    ("写测试时要避免哪些反模式", "testing-anti-patterns"),
    ("给测试加 Mock 有什么坑", "testing-anti-patterns"),
    ("生成后端接口时怎么加结构化日志和健康检查", "code-observability"),
    ("前后端状态不同步、有竞态该怎么防", "frontend-state-sync"),
    ("乐观更新回滚和请求取消怎么写", "frontend-state-sync"),
    ("做一个不用查文档就能看懂的自解释界面", "self-explanatory-ui"),
    ("界面设计时怎么把帮助信息集成进去", "self-explanatory-ui"),
    ("代码交付前怎么做自测和审计报告", "engineering-test-delivery"),
)
EN_QUERIES = (
    ("what anti-patterns to avoid when writing tests", "testing-anti-patterns"),
    ("pitfalls of adding mocks in tests", "testing-anti-patterns"),
    ("structured logs and health check for backend api", "code-observability"),
    ("frontend backend state out of sync race condition", "frontend-state-sync"),
    ("optimistic update rollback and request cancellation", "frontend-state-sync"),
    ("self explanatory interface without documentation", "self-explanatory-ui"),
    ("integrate help information into interface design", "self-explanatory-ui"),
    ("self testing and audit report before code delivery", "engineering-test-delivery"),
)

#: 实测（G1C-UA 探针，生产入口 `SkillLoader()`，top_k=5）
BM25_ZH_RECALL_ON = 8
BM25_ZH_RECALL_OFF = 2
EN_RECALL_BOTH = 8


# ════════════════════════════════════════════════════════════
#  三腿 front-matter 文本的三个取数口（各自走**该腿自己的**代码路径）
# ════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def file_store():
    from agent.skills_mgmt.file_store import SkillFileStore
    return SkillFileStore()


@pytest.fixture(scope="module")
def meta_index(file_store):
    return file_store.load_metadata_index(refresh=True) or {}


def _legacy_four_fields(meta: dict, name_fallback: str = "") -> str:
    """改前（G1C-U1 之前）三条腿共用的四字段拼接 —— 作为"置 0 必须逐字回到它"的判据"""
    parts = [
        meta.get("name") or name_fallback or "",
        meta.get("description", ""),
        " ".join(meta.get("tags", []) or []),
        meta.get("category", ""),
    ]
    return " ".join(p for p in parts if p)


def _tfidf_leg_text(meta: dict) -> str:
    """TF-IDF 腿的文档文本（生产入口：倒排索引 / _match_score 用的就是它）"""
    from agent.skills_mgmt import loader as L
    return L._meta_to_meta_text(meta)


def _bm25_leg_text(meta: dict) -> str:
    """BM25 腿的文档文本（走 BM25SkillSearcher 真正使用的那个函数）"""
    from agent.skills_mgmt.bm25_searcher import _skill_to_doc
    return _skill_to_doc(meta)


def _vector_leg_front_text(meta: dict, skill_id: str, file_store) -> str:
    """向量腿的 front-matter 部分（body_summary_chars=0 ⇒ 返回纯 front_text）

    走 `SkillVectorAdapter._build_vector_text` 本体，不另拼字符串 ——
    否则"测试自己拼对了"而生产拼错了。
    """
    from agent.skills_mgmt.vector_adapter import SkillVectorAdapter
    va = SkillVectorAdapter.__new__(SkillVectorAdapter)  # 不初始化后端/模型
    va.fs = file_store
    va.body_summary_chars = 0
    return va._build_vector_text(meta, skill_id)


def _cjk(s: str) -> int:
    return sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")


# ════════════════════════════════════════════════════════════
#  1. 同一字段列表（"只改一条腿" ⇒ 立刻不等 ⇒ 红）
# ════════════════════════════════════════════════════════════

class TestThreeLegsShareOneFieldList:

    def test_all_three_front_texts_are_byte_identical(self, meta_index, file_store):
        """对全部技能：TF-IDF / BM25 / 向量 三腿的 front-matter 文本必须**逐字相同**

        这是本卡的核心断言。任何一条腿被单独改动（改字段、改顺序、改开关、改回旧列表）
        都会让这一条变红 —— 它就是"防止第四次只修一条腿"的那道闸。
        """
        bad = {}
        for sid, meta in meta_index.items():
            a = _tfidf_leg_text(meta)
            b = _bm25_leg_text(meta)
            c = _vector_leg_front_text(meta, sid, file_store)
            if not (a == b == c):
                bad[sid] = {"tfidf": a[-60:], "bm25": b[-60:], "vector": c[-60:]}
        assert bad == {}, (
            "三条腿的文档文本口径不一致（同形实现又分叉了）：%d/%d 条技能不等，"
            "前 3 条（取各自文本**末尾** 60 字符，description_zh 是追加在末尾的）= %r"
            % (len(bad), len(meta_index), dict(list(bad.items())[:3])))

    def test_legs_delegate_to_the_canonical_builder(self):
        """源码级：BM25 / 向量两条腿必须**调用** loader 的那份实现，而不是各留一份字段列表

        行为相等还不够 —— 两条腿都"碰巧写出同样结果"的同形实现，下次仍会各自跑偏。
        这条要求它们**结构上**没有自己的字段列表。
        """
        from agent.skills_mgmt import bm25_searcher as B
        from agent.skills_mgmt.vector_adapter import SkillVectorAdapter
        for name, src in (
            ("bm25_searcher._skill_to_doc", inspect.getsource(B._skill_to_doc)),
            ("vector_adapter._build_vector_text",
             inspect.getsource(SkillVectorAdapter._build_vector_text)),
        ):
            assert "_meta_to_meta_text" in src, (
                "%s 没有委托给 loader._meta_to_meta_text —— 它又成了一份同形独立实现；"
                "这正是 G1C-U1 → G1C-UA 之间『只修一条腿』的成因" % name)

    def test_vector_leg_keeps_skill_id_name_fallback(self, file_store):
        """向量腿原有口径：name 缺失时用 skill_id 兜底（保住 skill_id 里的英文 token）"""
        meta = {"description": "no name here", "tags": [], "category": "meta"}
        got = _vector_leg_front_text(meta, "some_skill_id", file_store)
        assert got.startswith("some_skill_id"), "向量腿的 name 兜底被改坏了: %r" % got
        # 而 TF-IDF / BM25 两腿的默认（name_fallback="")不受影响 ⇒ 保持旧行为
        assert _tfidf_leg_text(meta) == _legacy_four_fields(meta)
        assert _bm25_leg_text(meta) == _legacy_four_fields(meta)

    def test_dual_track_skills_have_chinese_in_all_three_legs(self, meta_index, file_store):
        """20 条带 description_zh 的技能：三腿的文档文本里都必须真的含那段中文"""
        dual = [sid for sid, m in meta_index.items()
                if str(m.get("description_zh") or "").strip()]
        assert len(dual) >= 20, "前置不成立：带 description_zh 的技能少于 20 条（实得 %d）" % len(dual)
        bad = {}
        for sid in dual:
            meta = meta_index[sid]
            zh = str(meta["description_zh"]).strip()
            texts = {
                "tfidf": _tfidf_leg_text(meta),
                "bm25": _bm25_leg_text(meta),
                "vector": _vector_leg_front_text(meta, sid, file_store),
            }
            miss = {k: _cjk(v) for k, v in texts.items() if zh not in v}
            if miss:
                bad[sid] = miss
        assert bad == {}, (
            "有腿的文档文本里没有 description_zh（中文 query 命中不了它）：%d/%d 条技能，"
            "前 3 条 = %r" % (len(bad), len(dual), dict(list(bad.items())[:3])))


# ════════════════════════════════════════════════════════════
#  2. 同一个开关（"某条腿自己读 env / 自己缓存开关" ⇒ 红）
# ════════════════════════════════════════════════════════════

class TestOneSwitchDecidesAllThreeLegs:

    def _texts(self, meta, sid, file_store):
        return {
            "tfidf": _tfidf_leg_text(meta),
            "bm25": _bm25_leg_text(meta),
            "vector": _vector_leg_front_text(meta, sid, file_store),
        }

    def test_env_switch_on_puts_zh_into_all_three_legs(self, meta_index, file_store, monkeypatch):
        monkeypatch.delenv(ENV, raising=False)  # 默认 = 新行为
        sid = "code-observability"
        zh = str(meta_index[sid]["description_zh"]).strip()
        texts = self._texts(meta_index[sid], sid, file_store)
        missing = [k for k, v in texts.items() if zh not in v]
        assert missing == [], "开关开时这些腿没并入 description_zh: %r" % missing

    def test_env_switch_off_removes_zh_from_all_three_legs(self, meta_index, file_store, monkeypatch):
        """置 0 ⇒ **三条腿一起**回到改前的四字段拼接（不是只回一条）"""
        monkeypatch.setenv(ENV, "0")
        bad = {}
        for sid, meta in meta_index.items():
            want = _legacy_four_fields(meta)
            texts = self._texts(meta, sid, file_store)
            diff = {k: v[:100] for k, v in texts.items() if v != want}
            if diff:
                bad[sid] = diff
        assert bad == {}, "置 0 后仍有腿没有逐字回到旧行为: %r" % bad

    def test_patching_the_single_gate_flips_all_three_together(self, meta_index, file_store, monkeypatch):
        """把**唯一的那个开关函数**打成 False，三条腿必须一起翻转（不碰任何 env）

        这条专抓两类假生效：
          · 某条腿自己 `os.environ.get("CP_SKILL_META_INCLUDE_ZH")`（有第二个读取点）；
          · 某条腿在导入期/构造期把开关缓存成了常量。
        以上任一情况，这里都会只剩两条腿翻转 ⇒ 红。
        """
        from agent.skills_mgmt import loader as L
        sid = "code-observability"
        zh = str(meta_index[sid]["description_zh"]).strip()
        on = self._texts(meta_index[sid], sid, file_store)
        assert all(zh in v for v in on.values()), "前置不成立：默认三腿都应含中文"

        monkeypatch.setattr(L, "_include_description_zh", lambda: False)
        off = self._texts(meta_index[sid], sid, file_store)
        still_on = [k for k, v in off.items() if zh in v]
        assert still_on == [], (
            "把唯一的开关函数打成 False 后，这些腿仍然把 description_zh 拼了进去 "
            "⇒ 它们有自己的第二个开关/缓存: %r" % still_on)

        monkeypatch.setattr(L, "_include_description_zh", lambda: True)
        back = self._texts(meta_index[sid], sid, file_store)
        assert all(zh in v for v in back.values()), "开关打回 True 后三腿未一起恢复"

    @staticmethod
    def _env_read_count(txt: str) -> int:
        """数"真的读这个 env"的表达式条数（注释/文档里提到名字**不算**）

        loader.py 的写法是常量间接：`_ENV_META_INCLUDE_ZH = "CP_SKILL_META_INCLUDE_ZH"`
        + `os.environ.get(_ENV_META_INCLUDE_ZH)`。所以先把该文件里"绑定到这个名字"的
        常量名收出来，再把 字面量 + 这些常量名 都当作可接受的读取实参。
        这样"另起一个常量再读"也照样被算作第二个读取点。
        """
        names = set(re.findall(
            r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*[\"']" + re.escape(ENV) + r"[\"']",
            txt, re.M))
        targets = ["[\"']" + re.escape(ENV) + "[\"']"] + [re.escape(n) for n in sorted(names)]
        pat = re.compile(
            r"(?:os\.environ\s*\.\s*get\s*\(\s*|os\.environ\s*\[\s*|os\.getenv\s*\(\s*)"
            r"(?:" + "|".join(targets) + r")")
        return len(pat.findall(txt))

    def test_exactly_one_env_read_point_in_the_whole_agent_package(self):
        """`CP_SKILL_META_INCLUDE_ZH` 在 `agent/**.py` 里**只有一个**读取点（loader.py）

        同一个语义有第二个开关 = "关了一个、另一个还开着"的假生效（本审计反复追的形态）。
        """
        readers = {}
        for p in (ROOT / "agent").rglob("*.py"):
            try:
                txt = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            n = self._env_read_count(txt)
            if n:
                readers[p.relative_to(ROOT).as_posix()] = n
        assert readers == {"agent/skills_mgmt/loader.py": 1}, (
            "读取点应当**有且只有** agent/skills_mgmt/loader.py 一处，实得 %r —— "
            "多出来的每一处都是第二个开关（两个开关必然出现『关了一个、另一个还开着』）"
            % readers)


# ════════════════════════════════════════════════════════════
#  3. 召回数字锁死（生产入口，真 28 条技能库）
# ════════════════════════════════════════════════════════════

class TestLegRecallIsLocked:

    @pytest.fixture()
    def loader(self, file_store):
        from agent.skills_mgmt.loader import SkillLoader
        return SkillLoader(file_store=file_store)

    def _bm25_hits(self, loader, queries):
        s = loader._get_bm25_searcher()
        assert s is not None and s.is_available(), "BM25 腿不可用（前置不成立）"
        return sum(1 for q, exp in queries
                   if exp in [m.skill_id for m in s.search(q, top_k=5)])

    def test_bm25_leg_chinese_recall(self, loader, monkeypatch):
        monkeypatch.delenv(ENV, raising=False)
        got = self._bm25_hits(loader, ZH_QUERIES)
        assert got == BM25_ZH_RECALL_ON, (
            "BM25 腿中文命中 = %d/%d，钉死值 = %d（改前实测 %d）"
            % (got, len(ZH_QUERIES), BM25_ZH_RECALL_ON, BM25_ZH_RECALL_OFF))

    def test_bm25_leg_english_recall(self, loader, monkeypatch):
        monkeypatch.delenv(ENV, raising=False)
        got = self._bm25_hits(loader, EN_QUERIES)
        assert got == EN_RECALL_BOTH, (
            "BM25 腿英文命中 = %d/%d（并入中文不得让英文退化）" % (got, len(EN_QUERIES)))

    def test_bm25_leg_switch_off_falls_back_to_pre_change_recall(self, loader, monkeypatch):
        """置 0 ⇒ BM25 腿中文回落到 2/8（= G1C-U1 改前 TF-IDF 的同量级）——
        即"这条腿的收益真的由**同一个**开关承载"。"""
        monkeypatch.setenv(ENV, "0")
        got = self._bm25_hits(loader, ZH_QUERIES)
        assert got == BM25_ZH_RECALL_OFF, (
            "置 0 后 BM25 腿中文命中 = %d/%d，期望 %d（改前实测值）"
            % (got, len(ZH_QUERIES), BM25_ZH_RECALL_OFF))

    def test_tfidf_leg_chinese_recall_still_locked(self, loader, monkeypatch):
        """TF-IDF 腿（G1C-U1 的成果）不得被本卡改坏"""
        monkeypatch.delenv(ENV, raising=False)
        got = sum(1 for q, exp in ZH_QUERIES
                  if exp in [m.skill_id for m in loader.match(q, top_k=5, use_vector=False, use_bm25=False).matches])
        assert got == 8, "TF-IDF 腿中文命中 = %d/8（G1C-U1 成果被改坏）" % got


# ════════════════════════════════════════════════════════════
#  4. 向量腿的哈希/代价随同一个开关走（取代 G1-B/M7 的"永不并入"裁定）
# ════════════════════════════════════════════════════════════

class TestVectorHashFollowsTheSameSwitch:

    def _va(self, file_store, body_chars=200):
        from agent.skills_mgmt.vector_adapter import SkillVectorAdapter
        va = SkillVectorAdapter.__new__(SkillVectorAdapter)
        va.fs = file_store
        va.body_summary_chars = body_chars
        return va

    def test_switch_off_restores_pre_change_text_and_hash(self, meta_index, file_store, monkeypatch):
        """置 0 ⇒ 向量文本与哈希**逐字回到改前**（不会触发一次性重编码）"""
        monkeypatch.setenv(ENV, "0")
        va = self._va(file_store)
        bad = {}
        for sid, meta in meta_index.items():
            text, h = va._vector_text_and_hash(meta, sid)
            from agent.skills_mgmt.file_store import SkillFileStore as _FS  # noqa: F401
            body = va.fs.load_instruction(sid) or ""
            body = body[: va.body_summary_chars]
            legacy = _legacy_four_fields(meta, name_fallback=sid)
            want = ("%s\n%s" % (legacy, body)) if body else legacy
            if text != want:
                bad[sid] = text[:120]
        assert bad == {}, "置 0 后向量文本没有逐字回到改前: %r" % bad

    def test_switch_on_changes_text_only_for_dual_track(self, meta_index, file_store, monkeypatch):
        """开关开 ⇒ 只有带 description_zh 的技能文本变化（其余逐字不变）"""
        va = self._va(file_store)
        monkeypatch.setenv(ENV, "0")
        off = {sid: va._vector_text_and_hash(m, sid)[0] for sid, m in meta_index.items()}
        monkeypatch.delenv(ENV, raising=False)
        on = {sid: va._vector_text_and_hash(m, sid)[0] for sid, m in meta_index.items()}
        changed = {sid for sid in meta_index if off[sid] != on[sid]}
        dual = {sid for sid, m in meta_index.items()
                if str(m.get("description_zh") or "").strip()}
        assert changed == dual, (
            "文本变化集合与双轨集合不符：多改 %r / 少改 %r"
            % (sorted(changed - dual), sorted(dual - changed)))

    def test_full_rebuild_is_triggered_once_by_the_switch(self, meta_index):
        """影响面自证：脏条数 ≥ 阈值 ⇒ 首次会走**一次**全量重编码（本卡如实登记代价）"""
        from agent.skills_mgmt import vector_adapter as V
        dual = sum(1 for m in meta_index.values() if str(m.get("description_zh") or "").strip())
        total = len(meta_index)
        threshold = max(V._CONTENT_HASH_FULL_REBUILD_MIN_DIRTY,
                        int(total * V._CONTENT_HASH_FULL_REBUILD_DIRTY_RATIO))
        assert dual >= threshold, (
            "脏条数 %d < 阈值 %d ⇒ 首次不会全量重建；若本断言失败说明影响面变了，需重测"
            % (dual, threshold))

