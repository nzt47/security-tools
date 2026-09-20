# -*- coding: utf-8 -*-
"""`sensor/tags.py` 单元补测 —— TASK-09 · P0-5（其二）。

本文件守的是"标签学"的**结构不变量**，不是"抄表"
==================================================
`get_tags()` 的失败模式**不是抛异常**，而是"**静默少给标签**"：

  · `_CATEGORY_TAGS` 少了一个 `Category` 成员 ⇒ 该类读数只拿到覆写标签，不报错；
  · 覆写规则的正则写错（漏 `^`、写错前缀）⇒ 该规则**永不命中**，只是标签少几个；
  · 覆写规则里写了不属于 8 大维度的字符串 ⇒ 下游按维度聚合时该值**落在所有维度之外**。

⇒ 故本文件用四条结构不变量代替逐条抄表：
  ① `_CATEGORY_TAGS` 的键集合 **恰好等于** `set(Category)`（18/18，无缺无多）；
  ② 每个类别的默认标签 **恰好 8 个**，且**每个维度恰好命中一个**、**顺序固定**为
     domain → locus → temporal → method → layer → role → datatype → control；
  ③ 8 个维度的取值集合**两两不相交**，并集恰好 28 个（词表封闭）；
  ④ 每条覆写规则都必须是**合法的、带 `^` 锚的纯字面前缀正则**，其标签全部落在 28 词表内。

【不易】纯函数 + 纯常量：不 import 硬件、不读磁盘、Linux CI 可全绿（E7）。
【不易】不使用 `importlib.reload`（重载会新建 `Category` 对象，使 `is` 比较失效）。
【变易】与 `tests/unit/test_sensor_body_switch.py` 无交集：后者测的是
    `body_sensor` 的开关/分发，不触碰本模块的维度结构。
"""
from __future__ import annotations

import re

import pytest

from sensor.sensor_reading import Category
from sensor.tags import (
    CTRL_CONFIG,
    CTRL_OBSERVE,
    DOMAIN_BEHAVIOR,
    DOMAIN_ENVIRONMENT,
    DOMAIN_HARDWARE,
    DOMAIN_SOFTWARE,
    DTYPE_CONFIG,
    DTYPE_EVENT,
    DTYPE_NUMERIC,
    DTYPE_STATE,
    LAYER_APPLICATION,
    LAYER_PHYSICAL,
    LAYER_SYSTEM,
    LOCUS_BOUNDARY,
    LOCUS_EXTERNAL,
    LOCUS_INTERNAL,
    METHOD_DELTA,
    METHOD_MONITOR,
    METHOD_PROBE,
    METHOD_QUERY,
    ROLE_ENVIRONMENT,
    ROLE_PERFORMANCE,
    ROLE_SECURITY,
    ROLE_SOCIAL,
    ROLE_VITAL,
    TEMP_DELTA,
    TEMP_DYNAMIC,
    TEMP_STATIC,
    _CATEGORY_TAGS,
    _SENSOR_TAG_OVERRIDES,
    get_tags,
)

# ── 8 大维度的词表（模块 docstring 的 1~8 条维度）──────────────────────
DIMENSIONS = [
    ("domain", [DOMAIN_HARDWARE, DOMAIN_SOFTWARE, DOMAIN_BEHAVIOR, DOMAIN_ENVIRONMENT]),
    ("locus", [LOCUS_INTERNAL, LOCUS_EXTERNAL, LOCUS_BOUNDARY]),
    ("temporal", [TEMP_STATIC, TEMP_DYNAMIC, TEMP_DELTA]),
    ("method", [METHOD_PROBE, METHOD_MONITOR, METHOD_QUERY, METHOD_DELTA]),
    ("layer", [LAYER_PHYSICAL, LAYER_SYSTEM, LAYER_APPLICATION]),
    ("role", [ROLE_VITAL, ROLE_PERFORMANCE, ROLE_SECURITY, ROLE_SOCIAL, ROLE_ENVIRONMENT]),
    ("datatype", [DTYPE_NUMERIC, DTYPE_STATE, DTYPE_EVENT, DTYPE_CONFIG]),
    ("control", [CTRL_OBSERVE, CTRL_CONFIG]),
]
DIM_NAMES = [d[0] for d in DIMENSIONS]
VOCABULARY = [tag for _, tags in DIMENSIONS for tag in tags]
DIM_OF = {tag: name for name, tags in DIMENSIONS for tag in tags}


def _dedup(seq):
    """独立实现"去重并保持首次出现顺序"作为断言判据（dict 保序）。"""
    return list(dict.fromkeys(seq))


def _oracle_tags(category_default, sensor_name):
    """**差分判据**：按声明顺序把命中规则追加到类别默认之上，再去重保序。

    这是把 `get_tags` 的"合并顺序 + 去重保序"契约**独立重写一遍**做 oracle
    （不是抄常量表）。若实现改成排序、改成末尾去重、或调换两层顺序，本判据即失败。
    """
    out = list(category_default)
    for pattern, extra in _SENSOR_TAG_OVERRIDES:
        if re.match(pattern, sensor_name):
            out.extend(extra)
    return _dedup(out)


def _no_match_name():
    """一个**不命中任何覆写规则**的传感器名（用于隔离"类别默认"这一层）。"""
    return "zzz_no_such_sensor_prefix"


# ═══════════════════════════════════════════════════════════════
#  1. 词表（8 维度）结构
# ═══════════════════════════════════════════════════════════════

class TestVocabulary:
    """28 个标签构成封闭词表；维度之间不共享取值。"""

    def test_eight_dimensions_with_fixed_arities(self):
        """恰好 8 个维度，元数依次为 4/3/3/4/3/5/4/2（合计 28）。"""
        assert len(DIMENSIONS) == 8
        assert [len(tags) for _, tags in DIMENSIONS] == [4, 3, 3, 4, 3, 5, 4, 2]
        assert len(VOCABULARY) == 28

    def test_vocabulary_tags_are_unique(self):
        """28 个标签两两不同（重复会让"维度归属"变成多义）。"""
        assert len(set(VOCABULARY)) == 28

    @pytest.mark.parametrize("i", range(len(DIMENSIONS)))
    def test_dimension_internal_uniqueness(self, i):
        """单个维度内部无重复值。"""
        name, tags = DIMENSIONS[i]
        assert len(set(tags)) == len(tags), name

    @pytest.mark.parametrize("i,j", [(i, j) for i in range(8) for j in range(i + 1, 8)])
    def test_dimension_pairs_are_disjoint(self, i, j):
        """**28 对维度两两不相交** —— 这是"一个标签只能属于一个维度"的前提。

        若两个维度共享取值，`DIM_OF` 反查会静默取到后者，
        下游"按维度聚合标签"就会把同一个标签同时算进两条轴。
        """
        a, b = DIMENSIONS[i], DIMENSIONS[j]
        assert set(a[1]).isdisjoint(set(b[1])), f"{a[0]} ∩ {b[0]}"

    def test_every_category_tag_belongs_to_vocabulary(self):
        """类别默认标签里**不允许出现词表外的字符串**（否则该标签不属于任何维度）。"""
        stray = sorted({t for tags in _CATEGORY_TAGS.values() for t in tags} - set(VOCABULARY))
        assert stray == []

    def test_every_override_tag_belongs_to_vocabulary(self):
        """覆写标签同样必须落在词表内。"""
        stray = sorted({t for _, extra in _SENSOR_TAG_OVERRIDES for t in extra} - set(VOCABULARY))
        assert stray == []


# ═══════════════════════════════════════════════════════════════
#  2. 类别默认标签：18/18 覆盖 + 每维恰一 + 顺序固定
# ═══════════════════════════════════════════════════════════════

class TestCategoryDefaultMap:
    """`_CATEGORY_TAGS` 是全量覆盖的映射，且每条是"每维恰一"的规范型。"""

    def test_key_set_is_exactly_all_categories(self):
        """键集合 == `set(Category)`：**不多不少**。

        缺一个 ⇒ 该类读数静默只剩覆写标签（少标签、不报错）；
        多一个 ⇒ 存在永远取不到的键（说明 Category 被改动过而此处未同步）。
        """
        assert set(_CATEGORY_TAGS) == set(Category)
        assert len(_CATEGORY_TAGS) == 18

    def test_all_keys_are_category_enum_members(self):
        """键必须是 `Category` 成员本身（用**值**做键会让 `cat_key in dict` 恒为假）。"""
        assert all(isinstance(k, Category) for k in _CATEGORY_TAGS)

    @pytest.mark.parametrize("cat", list(Category), ids=lambda c: c.name)
    def test_each_category_has_exactly_eight_tags(self, cat):
        """每个类别**恰好 8 个**标签（≠8 说明漏了某维度或某维度给了两个）。"""
        assert len(_CATEGORY_TAGS[cat]) == 8

    @pytest.mark.parametrize("cat", list(Category), ids=lambda c: c.name)
    def test_each_dimension_hits_exactly_one_tag(self, cat):
        """**核心结构不变量**：每个维度在默认标签里**恰好命中一个**，顺序也固定。

        顺序被下游按位置读取（模块 docstring 明写 1~8 的顺序），
        故这里逐位置断言"第 k 个必须属于第 k 个维度"。
        """
        tags = _CATEGORY_TAGS[cat]
        for idx, (dim_name, vocab) in enumerate(DIMENSIONS):
            assert tags[idx] in vocab, f"{cat.name} 第{idx}位应属 {dim_name}，实为 {tags[idx]!r}"
        # 反向：每个维度都被覆盖到（与上一循环合起来等价于"双射"）
        assert sorted(DIM_OF[t] for t in tags) == sorted(DIM_NAMES)

    @pytest.mark.parametrize("cat", list(Category), ids=lambda c: c.name)
    def test_no_duplicate_tags_within_category(self, cat):
        """同一类别内不得重复（重复会让长度 8 但实际只覆盖 7 个维度）。"""
        assert len(set(_CATEGORY_TAGS[cat])) == 8

    def test_known_category_content_locked(self):
        """抽查两类**逐值**锁定（结构不变量之外的锚点，防止整体错位后仍"结构自洽"）。

        `CPU` 与 `CHANGE` 分别代表"硬件-性能"与"变化-事件"两种典型形态。
        """
        assert _CATEGORY_TAGS[Category.CPU] == [
            DOMAIN_HARDWARE, LOCUS_INTERNAL, TEMP_DYNAMIC,
            METHOD_PROBE, LAYER_PHYSICAL, ROLE_PERFORMANCE,
            DTYPE_NUMERIC, CTRL_OBSERVE,
        ]
        assert _CATEGORY_TAGS[Category.CHANGE] == [
            DOMAIN_HARDWARE, LOCUS_INTERNAL, TEMP_DELTA,
            METHOD_DELTA, LAYER_PHYSICAL, ROLE_VITAL,
            DTYPE_EVENT, CTRL_OBSERVE,
        ]

    def test_network_and_system_are_the_configured_ones(self):
        """`NETWORK` / `SYSTEM` 的 control 维度是 `可配置`，其余 16 类为 `仅可观测`。

        这条锁的是"可干预性"这条轴的语义边界：只有网络与系统类宣称可写。
        """
        configurable = {c for c, tags in _CATEGORY_TAGS.items() if CTRL_CONFIG in tags}
        assert configurable == {Category.NETWORK, Category.SYSTEM}
        assert all(CTRL_OBSERVE in tags for c, tags in _CATEGORY_TAGS.items()
                   if c not in (Category.NETWORK, Category.SYSTEM))


# ═══════════════════════════════════════════════════════════════
#  3. `get_tags` 的层次：类别默认 → 覆写追加 → 去重保序
# ═══════════════════════════════════════════════════════════════

class TestGetTagsLayers:
    """三层语义分离：类别默认、覆写追加、去重保序。"""

    @pytest.mark.parametrize("cat", list(Category), ids=lambda c: c.name)
    def test_category_default_layer_is_verbatim(self, cat):
        """不命中任何覆写时，返回值**逐值等于**类别默认（含顺序），且是**新列表**。"""
        got = get_tags(cat, _no_match_name())
        assert got == _CATEGORY_TAGS[cat]
        assert got is not _CATEGORY_TAGS[cat]

    @pytest.mark.parametrize("cat", list(Category), ids=lambda c: c.name)
    def test_category_accepts_its_string_value(self, cat):
        """传字符串值必须与传枚举**逐值相同**（`get_tags` 内部会 `Category(...)` 归一）。"""
        assert get_tags(cat.value, _no_match_name()) == get_tags(cat, _no_match_name())

    def test_unknown_string_category_yields_only_overrides(self):
        """未知字符串类别 ⇒ 无默认标签，但覆写照常生效（不抛异常）。"""
        assert get_tags("no_such_category", _no_match_name()) == []
        assert get_tags("no_such_category", "cpu_temp") == [
            TEMP_DYNAMIC, DTYPE_NUMERIC, ROLE_VITAL,
        ]

    @pytest.mark.parametrize("bad", [None, "", 0, 1.5, [], {}, ("cpu",), True])
    def test_bad_category_is_treated_as_no_default(self, bad):
        """`None` / 空串 / 非字符串 ⇒ `Category(...)` 抛 `ValueError`/`TypeError` 被吞 ⇒ 无默认。

        **静默**是刻意的（传感器是自发现插件，类别可能为空），
        但必须锁定：任何 falsy/非法类别都不会让 `get_tags` 抛异常。
        """
        assert get_tags(bad, _no_match_name()) == []

    def test_override_layer_replaces_nothing_only_appends(self):
        """**覆写只能追加、不能替换** —— 由此同一维度可能出现**两个值**。

        `get_tags(Category.CPU, "cpu_temp")` 的功能角色轴同时含
        `性能监控`（类别默认）与 `基础生存`（覆写追加）⇒ 2 个 role。
        这不是崩溃，而是"每维恰一"这一规范型在**输出层**不再成立。
        登记为观察项（见报告 §5），本用例只锁定现状。
        """
        got = get_tags(Category.CPU, "cpu_temp")
        roles = [t for t in got if DIM_OF[t] == "role"]
        assert roles == [ROLE_PERFORMANCE, ROLE_VITAL]
        assert len(roles) == 2
        # 且 domain 轴仍只有一个（该覆写没有引入第二个 domain）
        assert [t for t in got if DIM_OF[t] == "domain"] == [DOMAIN_HARDWARE]
        # 追加位置在末尾（默认 8 个之后）
        assert got[:8] == _CATEGORY_TAGS[Category.CPU]
        assert got[8:] == [ROLE_VITAL]

    def test_domain_axis_can_also_double(self):
        """domain 轴同样会翻倍：`disk_io_*` 把 `行为感知` 追加到 `硬件感知` 之上。

        真实的 `sensor/behavior_sensor.py` 会产出 `behavior_disk_*` 之类的名字，
        故"一条读数同时属于两个目标域"在生产里是会发生的形态。
        """
        got = get_tags(Category.DISK, "disk_io_read")
        domains = [t for t in got if DIM_OF[t] == "domain"]
        assert domains == [DOMAIN_HARDWARE, DOMAIN_BEHAVIOR]

    def test_unknown_sensor_name_falls_back_to_category_default(self):
        """未知传感器名（真实场景里占多数）⇒ 只有类别默认标签，**不报错、不空手**。"""
        assert get_tags(Category.PROCESS, "whatever_new_sensor") == _CATEGORY_TAGS[Category.PROCESS]

    @pytest.mark.parametrize("name", ["", " ", "CPU_TEMP", "cpu", "xcpu_temp", "cpu-temp"])
    def test_similar_but_non_matching_names(self, name):
        """**锚定语义**：`^` 锚 + 大小写敏感 + 前缀必须完整匹配。

        `"CPU_TEMP"`（大写）与 `"xcpu_temp"`（前缀多了字符）都**不得**命中
        `^cpu_temp`；`"cpu-temp"`（连字符）也不得命中。
        失败模式仍是"静默少标签"，故必须显式锁。
        """
        assert get_tags(None, name) == []

    def test_dedup_preserves_first_occurrence_order(self):
        """去重保留**首次出现**位置：重复标签不移动、不重排。"""
        got = get_tags(Category.CPU, "cpu_temp")
        assert got == _dedup(list(_CATEGORY_TAGS[Category.CPU]) + [TEMP_DYNAMIC, DTYPE_NUMERIC, ROLE_VITAL])
        assert got.count(TEMP_DYNAMIC) == 1
        assert got.count(DTYPE_NUMERIC) == 1

    def test_every_returned_tag_belongs_to_vocabulary(self):
        """任意组合下返回值都不得含词表外标签（下游按维度聚合的前提）。"""
        for cat in Category:
            for _, extra in _SENSOR_TAG_OVERRIDES:
                for t in get_tags(cat, _no_match_name()):
                    assert t in DIM_OF

    def test_repeated_calls_are_idempotent_and_do_not_mutate_map(self):
        """**别名防线**：反复调用（含命中覆写）不得污染 `_CATEGORY_TAGS` 本体。

        若实现哪天写成 `tags = _CATEGORY_TAGS[cat_key]` 再 `extend(...)`，
        全局映射会被**永久改写** —— 一次调用污染整个进程的标签语义，
        且失败表现为"越跑标签越多"，极难定位。故这里显式回归。
        """
        snapshot = {c: list(tags) for c, tags in _CATEGORY_TAGS.items()}
        for _ in range(3):
            assert get_tags(Category.CPU, _no_match_name()) == snapshot[Category.CPU]
            get_tags(Category.CPU, "cpu_temp")
            get_tags(Category.CHANGE, "board_firmware")
        assert _CATEGORY_TAGS == snapshot
        assert all(len(tags) == 8 for tags in _CATEGORY_TAGS.values())


# ═══════════════════════════════════════════════════════════════
#  4. 覆写规则表：逐条结构校验（80 条，逐条参数化）
# ═══════════════════════════════════════════════════════════════

OVERRIDE_CASES = [
    pytest.param(pattern, extra, id=f"{pattern}->{'+'.join(extra)}")
    for pattern, extra in _SENSOR_TAG_OVERRIDES
]
_LITERAL_RE = re.compile(r"^[a-z0-9_]+$")


class TestOverrideRuleTable:
    """每一条覆写规则都必须"能编译、已锚定、是纯字面前缀、标签在词表内、真的会命中"。"""

    def test_rule_count_is_eighty_one(self):
        """规则条数锁定为 **81**（增删规则应当是一次有意识的改动）。

        【实测校正】我在写这条之前按肉眼数了一遍得出 80，实测是 **81**
        （`len(_SENSOR_TAG_OVERRIDES) == 81`）—— 再次印证"数出来的数字不能当基线"。
        """
        assert len(_SENSOR_TAG_OVERRIDES) == 81

    def test_rule_shapes_are_pairs(self):
        """每条规则必须恰好是 `(pattern: str, extra_tags: list[str])` 二元组。"""
        for pattern, extra in _SENSOR_TAG_OVERRIDES:
            assert isinstance(pattern, str) and isinstance(extra, list)
            assert extra, pattern

    @pytest.mark.parametrize("pattern,extra", OVERRIDE_CASES)
    def test_pattern_compiles_and_is_anchored(self, pattern, extra):
        """正则可编译，且**不得匹配空串** ⇒ 证明 `^` 锚存在。

        失败模式：漏写 `^` 时 `re.match` 仍能从串首匹配（`match` 本就锚定串首），
        但一旦有人把 `re.match` 改成 `re.search`，无锚规则会**意外命中串中间**。
        因此"不匹配空串"这条断言是对**锚定意图**的直接编码。
        """
        compiled = re.compile(pattern)
        assert compiled.match("") is None
        assert pattern.startswith("^")

    @pytest.mark.parametrize("pattern,extra", OVERRIDE_CASES)
    def test_pattern_is_pure_literal_prefix(self, pattern, extra):
        """`^` 之后必须是纯 `[a-z0-9_]` 字面前缀（不含正则元字符）。

        含元字符会带来两重风险：① 意图不明的模糊命中；
        ② `_SENSOR_TAG_OVERRIDES` 是**有序**列表，模糊规则会掩盖后面的精确规则。
        """
        assert _LITERAL_RE.match(pattern[1:]) is not None, pattern

    @pytest.mark.parametrize("pattern,extra", OVERRIDE_CASES)
    def test_extra_tags_have_no_internal_duplicates(self, pattern, extra):
        """单条规则内部不得自我重复（有重复说明该规则写得草率）。"""
        assert len(set(extra)) == len(extra), pattern

    @pytest.mark.parametrize("pattern,extra", OVERRIDE_CASES)
    def test_extra_tags_in_vocabulary(self, pattern, extra):
        """标签必须落在 8 维度词表内，且**每一维最多一个**。"""
        assert all(t in DIM_OF for t in extra), pattern
        dims = [DIM_OF[t] for t in extra]
        assert len(set(dims)) == len(dims), f"{pattern} 同维度多值: {dims}"

    @pytest.mark.parametrize("pattern,extra", OVERRIDE_CASES)
    def test_rule_fires_for_its_own_prefix(self, pattern, extra):
        """**该规则必须被自己的前缀命中** —— 这是"死规则"的唯一探测器。

        若前缀写错（例如把 `^disk_partition` 写成 `^disk_partions`），
        规则会永远不命中，而所有结构断言依旧通过（静默少标签）。
        这里用 `pattern[1:]` 作为该规则的天然样本名。
        """
        sample = pattern[1:]
        got = get_tags(None, sample)
        missing = [t for t in extra if t not in got]
        assert missing == [], f"{pattern} 未被自己的前缀命中，缺少 {missing}"

    @pytest.mark.parametrize("pattern,extra", OVERRIDE_CASES)
    def test_default_layer_survives_every_override(self, pattern, extra):
        """**只追加、从不替换**：命中任意规则后，前 8 个标签仍是类别默认且顺序不变。

        这条锁住"覆写是叠加层"这一分层语义；若实现改成"覆写替换默认"，
        同维度多值问题会消失，但默认层的信息会被抹掉 —— 那是另一种契约，必须显式变更。
        """
        got = get_tags(Category.CHANGE, pattern[1:])
        assert got[:8] == _CATEGORY_TAGS[Category.CHANGE]

    @pytest.mark.parametrize("pattern,extra", OVERRIDE_CASES)
    def test_merge_order_and_dedup_against_oracle(self, pattern, extra):
        """与差分 oracle 逐值相等 ⇒ 合并顺序正确、去重保留**首次出现**位置、无多余标签。

        注意 `^system_event_whea_` 与 `^system_event_crash_` 会**同时**命中
        更通用的 `^system_event_`；oracle 会如实叠加两条规则，
        因此这条同时覆盖了"前缀重叠时按声明顺序累加"的语义。
        """
        assert get_tags(Category.CHANGE, pattern[1:]) == _oracle_tags(
            _CATEGORY_TAGS[Category.CHANGE], pattern[1:])

    def test_overlapping_patterns_accumulate_in_declaration_order(self):
        """**前缀重叠**时按声明顺序累加：`system_event_whea_*` 同时命中
        `^system_event_whea_` 与 `^system_event_` 两条规则（后者是前者的前缀）。

        语义上这是"由具体到一般"的叠加，结果仍去重保序 ⇒ 锁定其顺序：
        先 `whea` 的专属标签，再 `system_event_` 的通用标签。
        """
        got = get_tags(None, "system_event_whea_x")
        assert got == _dedup(
            [DOMAIN_HARDWARE, TEMP_DELTA, DTYPE_EVENT, ROLE_VITAL]
            + [DOMAIN_SOFTWARE, TEMP_DELTA, DTYPE_EVENT, ROLE_ENVIRONMENT]
        )
        assert got == [DOMAIN_HARDWARE, TEMP_DELTA, DTYPE_EVENT, ROLE_VITAL,
                       DOMAIN_SOFTWARE, ROLE_ENVIRONMENT]

    def test_most_generic_event_rule_also_fires(self):
        """对照组：只命中通用规则的事件名取到通用标签集。"""
        assert get_tags(None, "system_event_other") == [
            DOMAIN_SOFTWARE, TEMP_DELTA, DTYPE_EVENT, ROLE_ENVIRONMENT,
        ]


# ═══════════════════════════════════════════════════════════════
#  5. 真实调用面：`file_watcher` / `change_detector` / `hardware_file_sensor`
# ═══════════════════════════════════════════════════════════════

class TestRealProducerNames:
    """用**生产真实产出**的传感器名验标签，而不是自造名字（TASK-00 §0.2f/D12）。"""

    FILE_EVENT_NAMES = [
        "file_created", "file_modified", "file_deleted", "file_moved",
        "dir_created", "dir_deleted", "dir_moved", "file_event", "dir_event",
    ]

    def test_file_override_rules_are_effective_noops(self):
        """**发现（登记，未修）**：7 条文件/目录覆写规则在 `Category.FILE` 下全是**空操作**。

        它们追加的 `DTYPE_EVENT`（`事件量`）**已经包含在** `_CATEGORY_TAGS[Category.FILE]`
        的 datatype 位（`sensor/tags.py:124`），去重后不产生任何变化。
        ⇒ 实测意义：**没有任何一条真实文件事件会因为覆写而多出标签**；
          这 7 条规则当前唯一的可观测效果是"如果将来 `Category.FILE` 的默认改了，
          它们才会重新生效"。这不是崩溃，是**声明与效果不一致**（噪声规则）。
        """
        expected = _CATEGORY_TAGS[Category.FILE]
        for name in self.FILE_EVENT_NAMES:
            assert get_tags(Category.FILE, name) == expected, name
        assert DTYPE_EVENT in expected  # 这正是空操作的原因

    def test_dir_moved_missing_rule_is_invisible_only_because_of_noop(self):
        """**发现（登记，未修）**：`file_moved` 有规则、`dir_moved` **没有**。

        `sensor/file_watcher.py:178-179` 对目录事件做
        `sensor_name.replace("file", "dir")` ⇒ 目录移动产出的是 `dir_moved`，
        而覆写表里只有 `^dir_created` / `^dir_deleted`，**缺 `^dir_moved`**。
        这就是一处真实的不对称；但因为上面那条"空操作"，
        两者的输出**恰好相同** ⇒ 缺陷被掩盖成不可观测。
        本用例把两件事一起锁死：**缺规则是真的，不可观测也是真的**。
        """
        assert not any(p == "^dir_moved" for p, _ in _SENSOR_TAG_OVERRIDES)
        assert get_tags(Category.FILE, "dir_moved") == get_tags(Category.FILE, "file_moved")
        assert get_tags(Category.FILE, "dir_moved") == _CATEGORY_TAGS[Category.FILE]

    def test_change_detector_event_names_get_change_tags(self):
        """`change_detector` 的 7 类事件名（`change_*`）**没有任何覆写规则**，
        因此一律只拿 `Category.CHANGE` 的 8 个默认标签。"""
        names = ["change_device_added", "change_device_removed", "change_device_modified",
                 "change_disk_mounted", "change_disk_unmounted", "change_process_started",
                 "change_process_stopped", "change_service_state", "change_registry",
                 "change_environment", "change_system_info"]
        for name in names:
            assert get_tags(Category.CHANGE, name) == _CATEGORY_TAGS[Category.CHANGE], name

    def test_hardware_file_rule_replaces_domain(self):
        """`hwfile_*` 命中 `^hwfile_` ⇒ 域名轴变为 `硬件感知`，且**只追加不替换**
        ⇒ 结果里同时存在 `软件感知`（若类别是 FILE）与新追加的 `硬件感知`。"""
        got = get_tags(Category.FILE, "hwfile_added_driver")
        assert got[:8] == _CATEGORY_TAGS[Category.FILE]
        assert got[8:] == [DOMAIN_HARDWARE, TEMP_STATIC, DTYPE_CONFIG, LAYER_SYSTEM]
        assert [t for t in got if DIM_OF[t] == "layer"] == [LAYER_APPLICATION, LAYER_SYSTEM]


# ═══════════════════════════════════════════════════════════════
#  6. `sensor_name=None` 的 TypeError（L-9 登记）
# ═══════════════════════════════════════════════════════════════

class TestSensorNameTypeContract:
    """`sensor_name` 的类型契约：`None` 会抛 `TypeError`（登记 L-9，锁定现状）。"""

    def test_sensor_name_none_raises_typeerror(self):
        """**缺陷登记（L-9，未修）**：`sensor_name=None` ⇒ `re.match(pattern, None)`
        抛 `TypeError: expected string or bytes-like object`。

        为什么值得登记：`SensorReading.sensor_name` **允许为 `None`**
        （`sensor_reading.py:52` 不做校验，且真实读数里存在空字段），
        因此"从读数直接取 `sensor_name` 传给 `get_tags`"是一条**可达**的调用路径。
        正确修法应是 `sensor_name or ""`。本任务禁止改生产代码 ⇒ 只锁定现状。
        另注：`category=None` **不抛**（见上文），⇒ 两个入参的容错**不一致**。
        """
        with pytest.raises(TypeError):
            get_tags(Category.CPU, None)

    def test_sensor_name_none_raises_even_without_category(self):
        """`category=None` 也救不了：正则循环在类别解析之后无条件执行。"""
        with pytest.raises(TypeError):
            get_tags(None, None)

    @pytest.mark.parametrize("bad_name", [None, 3, 3.5, b"cpu_temp", ["cpu_temp"]])
    def test_non_string_sensor_name_raises_typeerror(self, bad_name):
        """非字符串同样 `TypeError`（含 `bytes`：正则需要 str）。"""
        with pytest.raises(TypeError):
            get_tags(Category.CPU, bad_name)

    def test_empty_string_is_accepted(self):
        """**对照**：空串是合法输入 ⇒ 只拿类别默认标签（与 `None` 形成显式对比）。"""
        assert get_tags(Category.CPU, "") == _CATEGORY_TAGS[Category.CPU]
