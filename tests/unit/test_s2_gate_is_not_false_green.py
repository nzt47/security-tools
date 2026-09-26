"""S2 假绿回归锁（主审计新增 2026-09-25；2026-09-25 19:2x 按 C1 的两路径细化更新）

【为什么有这个文件】

`scripts/verify_index_drift.py` 的 S2 门曾经是**假绿**：它把 `cache.json` 的
`skills ∪ main_track` 当作「可召回集合」，而那是**磁盘存储分区**、不是**代码路径**。
C1 把主轨条目持久化进 `main_track` 后，S2 由 FAIL(7) 变 PASS(0)，**但生产行为零变化**
（最多只影响走 `SkillIndexCache` 的那条路径）。

主审计把判据改为「实测生产入口」后，C1 进一步细化为**两条路径都必须测**：

| 路径 | 装配方式 | 生产调用点 | 实测可召回 |
|---|---|---|---|
| pathA `bare` | 裸 `SkillFileStore.load_metadata_index()` | `capregistry/skillsearch.py:53`、`orchestrator.py:3874`（`SkillLoader()` 默认形态） | **28（缺 2）** |
| pathB `service` | `SkillFileStore + SkillIndexCache` | `SkillsMgmtService.__init__`（`service.py:77-82`） | **30（缺 0）** |

⇒ 结论比主审计初版**更精确**：不是「全部不可召回」，而是
**「走裸 loader 的那条真实生产路径缺 2 项」**。脚本对 pathA 报 FAIL 是**真话**。

**【2026-09-26 数字刷新（主审计，G1-C 之后）】** 上面两格原为 **23（缺 7）/ 30（缺 0）**。
G1-C（H-3）把 5 条主轨独有技能迁移成 `data/skills_repo/<id>/skill.md`（用户已确认「2 排除 / 5 纳入」），
⇒ `bare` **23 → 28**、主轨独有集合 **7 → 2**（只剩不纳入的 `global-core-principles` 与 `skill`）。
**本文件只刷新数字，判据与断言强度一字未改** —— 它仍然在锁「裸 loader 召回不到主轨独有技能」这个**事实**，
以及「S2 对 pathA 报 FAIL 说真话」这个**形态**（G1-C 刷新了 `test_skill_description_single_source.py` 的
`KNOWN_MAIN_TRACK_ONLY`，但**漏了本文件**，所以由主审计补上）。

【本文件锁什么】

锁**事实与判据形态**，不锁实现细节：

1. pathA 确实缺主轨独有技能（当前事实）；
2. pathB 确实**不缺**（避免有人「修」成两条都缺，那会矫枉过正）；
3. 判据确实走**生产入口**、且**两条路径分别判 FAIL**（不是取并集、也不降级为磁盘口径）；
4. 报告必须打印磁盘口径作为**参考**（便于一眼看出假绿风险）。

【将来谁把 pathA 也接上主轨会怎样】
测试 1 变红 ⇒ 逼人更新本文件与 S2 判据 ⇒ **那一天的绿才是真绿**。
"""
from __future__ import annotations

import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

#: 主轨独有的技能（只在 data/skills_mgmt.json 里，不在 skills_repo/*/skill.md）
#: 【2026-09-26】G1-C/H-3 迁移后由 7 条收敛为 **2 条** —— 恰好是用户裁定「**不纳入检索**」的那两条：
#:   `global-core-principles`（常驻行为准则，不是可路由技能）、
#:   `skill`（其 description 字段原本被错填成指令内容，G1-C 已修文案但按裁定仍不纳入）。
#: 原 7 条里另外 5 条已迁移为 skill.md，因此**不再属于「主轨独有」**，从本表移出是**契约更新而非放宽**。
MAIN_TRACK_ONLY = [
    "global-core-principles",
    "skill",
]


def _load_drift_module():
    spec = importlib.util.spec_from_file_location(
        "verify_index_drift_under_test",
        os.path.join(ROOT, "scripts", "verify_index_drift.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_pathA_bare_loader_cannot_recall_main_track_skills():
    """事实锁：**裸 loader 路径**当前召回不到主轨独有技能（当前为 2 条：`global-core-principles`、`skill`）。

    若此断言失败 ⇒ 说明有人把主轨接进了裸入口路径。**那是好事**，
    但必须同步更新 `verify_index_drift.py` 的 S2 判据与本文件，
    否则本门会继续报一个已经不存在的缺口。
    """
    mod = _load_drift_module()
    sets = mod._production_recall_sets(ROOT)
    bare = sets.get("bare")
    assert bare is not None, "pathA 调用失败（None）—— S2 会无法判 FAIL，属回归"
    assert len(bare) > 0, "pathA 返回空集：可能是导入/路径问题，而不是主轨已接通"
    present = [s for s in MAIN_TRACK_ONLY if s in bare]
    assert present == [], (
        "裸 loader 路径（load_metadata_index）现在能返回主轨独有技能 %r —— "
        "说明主轨已被接进该路径。请同步更新 scripts/verify_index_drift.py 的 S2 判据"
        "（pathA 应已自动转绿），并改写本测试。" % present
    )


def test_pathB_service_can_recall_main_track_skills():
    """反向锁：**服务形态路径**当前**能**召回主轨技能（缺 0）。

    这条防止「矫枉过正」：如果有人为了让 S2 变绿，把服务路径也弄成缺 2，
    本测试会红 —— 那说明他改错了地方。
    """
    mod = _load_drift_module()
    sets = mod._production_recall_sets(ROOT)
    svc = sets.get("service")
    if svc is None:
        import pytest
        pytest.skip("服务形态路径不可用（可能是临时副本创建失败），本次不判")
    missing = [s for s in MAIN_TRACK_ONLY if s not in svc]
    assert missing == [], (
        "服务形态路径（SkillFileStore + SkillIndexCache）丢了主轨技能 %r —— "
        "该路径本应能召回它们；这属于功能回归，不是预期。" % missing
    )


def test_s2_judges_both_paths_separately_not_disk_union():
    """S2 判据必须是「两条生产路径分别判」，不是磁盘分区并集。"""
    src = open(os.path.join(ROOT, "scripts", "verify_index_drift.py"), encoding="utf-8").read()
    assert "_production_recall_sets" in src, (
        "verify_index_drift.py 缺少 _production_recall_sets —— S2 可能退回磁盘分区口径（假绿）"
    )
    assert "load_metadata_index" in src, "S2 判据没有调用生产入口 load_metadata_index()"
    assert "pathA" in src and "pathB" in src, "S2 必须分别报告 pathA（裸入口）与 pathB（服务形态）"
    assert "参考" in src, "S2 必须把磁盘口径作为「参考（非判据）」打印，便于察觉假绿"


def test_drift_script_s2_fails_on_pathA_gap_now():
    """当前状态：pathA 缺 2 应报 FAIL —— 我们的工具必须说真话。"""
    mod = _load_drift_module()
    sets = mod._production_recall_sets(ROOT)
    bare = sets.get("bare")
    assert bare is not None
    from_main = [s for s in MAIN_TRACK_ONLY if s in bare]
    assert from_main == [], (
        "裸入口已能召回主轨技能 %r；若属实，S2 pathA 门应已转绿，请更新本测试" % from_main
    )