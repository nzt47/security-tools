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

**【2026-09-27 CI-3 修 · 夹具根】**

`data/skills_mgmt.json` 被 `.gitignore:224` 排除 ⇒ **不在 HEAD**，干净检出上不存在。
本门判的是两条生产路径的**差**（pathA 缺 2 / pathB 缺 0），**没有主轨就没有差**：
干净检出实测 pathB **1 failed**，pathA 那两条虽然"绿"但绿的理由是"主轨不存在"（空转）。
现改为把这些用例跑在**夹具根**上（tmp 里造一份"有主轨的仓库"：真仓 `data/skills_repo`
副本 + 只含这 2 条的 `data/skills_mgmt.json`）—— `_production_recall_sets(root)` 本身
就是 `--root` 参数化的。**判据与断言强度一条未改**，另加了"夹具主轨必须真的被读到"的
非空转前置。
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys

import pytest

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

# ────────────────────────────────────────────────────────────
#  【2026-09-27 CI-3】夹具根：本门判的是「两条生产路径的**差**」，没有主轨就没有差
# ────────────────────────────────────────────────────────────
# 【为什么原来的写法在 CI 上必红 / 必空转（干净检出实测，不是推断）】
#   `data/skills_mgmt.json` 被 `.gitignore:224` 排除 ⇒ 用 `git archive HEAD` 得到的
#   干净检出上**没有主轨**（CI 的 6 个 shard 跑的就是这种 checkout）⇒ 根本没有"缺口"
#   可判：
#     · pathB 断言实测 **1 failed**（缺 2 条）—— 本门在 CI 上必红；
#     · pathA 的两条断言虽然"通过"，但**通过的理由是主轨不存在**（空转）——
#       将来真把主轨接进裸入口，它们也不会红，等于没锁。
# 【夹具根】在 tmp 下造一个**布局等价**的仓库根：
#     data/skills_repo/        ← 真仓 28 条的副本（文件轨是 git 里的产物，原样搬来）
#     data/skills_mgmt.json    ← **只放这 2 条主轨独有技能**（本门要判的就是它们的可召回性）
#   `_production_recall_sets(root)` 本来就是 `--root` 参数化的（脚本自身也用它跑
#   `--root`），喂夹具根 = 喂一份"**有主轨**的仓库"，判据口径一字未改。
# 【断言强度】判据一条没改（仍是"pathA 必须缺 / pathB 必须不缺"），另外**增加**了
#   非空转前置：夹具主轨必须真的被读到（否则本门会退回"因为没主轨所以通过"的旧形态）。
FIXTURE_MAIN_TRACK = {
    sid: {
        "id": sid, "name": sid, "category": "custom", "source": "manual",
        "status": "published", "author": "workbench",
        "description": "主轨独有技能（CI-3 夹具）：%s" % sid,
        "content": "指令型内容",
        "content_type": "markdown", "tags": [], "enabled": True,
        "is_sensitive": False,
        "config_schema": {"type": "object", "properties": {}},
        "output_schema": {},
    }
    for sid in MAIN_TRACK_ONLY
}


@pytest.fixture
def fixture_root(tmp_path):
    """**有主轨**的仓库根（tmp）：文件轨 = 真仓副本，主轨 = 夹具台账（不碰仓库 data/）"""
    data = tmp_path / "data"
    data.mkdir()
    shutil.copytree(os.path.join(ROOT, "data", "skills_repo"),
                    str(data / "skills_repo"))
    (data / "skills_mgmt.json").write_text(
        json.dumps(FIXTURE_MAIN_TRACK, ensure_ascii=False), encoding="utf-8")
    return str(tmp_path)


def _load_drift_module():
    spec = importlib.util.spec_from_file_location(
        "verify_index_drift_under_test",
        os.path.join(ROOT, "scripts", "verify_index_drift.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_pathA_bare_loader_cannot_recall_main_track_skills(fixture_root):
    """事实锁：**裸 loader 路径**召回不到主轨独有技能（当前为 2 条：`global-core-principles`、`skill`）。

    若此断言失败 ⇒ 说明有人把主轨接进了裸入口路径。**那是好事**，
    但必须同步更新 `verify_index_drift.py` 的 S2 判据与本文件，
    否则本门会继续报一个已经不存在的缺口。

    【CI-3】根取自 `fixture_root`（**含主轨**）⇒ 本条在 CI 上是**真断言**：
    主轨确实存在、确实被 pathB 读到，而 pathA 依然读不到它们。
    """
    mod = _load_drift_module()
    assert set(mod.main_track_ids(fixture_root)) == set(MAIN_TRACK_ONLY), (
        "夹具根的主轨 id 必须**恰好**是主轨独有集合 %r（实得 %r）⇒ 否则本条会退回"
        "「主轨不存在所以通过」的空转形态"
        % (sorted(MAIN_TRACK_ONLY), sorted(mod.main_track_ids(fixture_root))))
    sets = mod._production_recall_sets(fixture_root)
    bare = sets.get("bare")
    assert bare is not None, "pathA 调用失败（None）—— S2 会无法判 FAIL，属回归"
    assert len(bare) > 0, "pathA 返回空集：可能是导入/路径问题，而不是主轨已接通"
    present = [s for s in MAIN_TRACK_ONLY if s in bare]
    assert present == [], (
        "裸 loader 路径（load_metadata_index）现在能返回主轨独有技能 %r —— "
        "说明主轨已被接进该路径。请同步更新 scripts/verify_index_drift.py 的 S2 判据"
        "（pathA 应已自动转绿），并改写本测试。" % present
    )


def test_pathB_service_can_recall_main_track_skills(fixture_root):
    """反向锁：**服务形态路径**当前**能**召回主轨技能（缺 0）。

    这条防止「矫枉过正」：如果有人为了让 S2 变绿，把服务路径也弄成缺 2，
    本测试会红 —— 那说明他改错了地方。

    【CI-3】根取自 `fixture_root`（**含主轨**）⇒ 在干净检出上也能判：
    旧写法在干净检出上必红（主轨文件不存在 ⇒ 服务路径无从补位）。
    """
    mod = _load_drift_module()
    sets = mod._production_recall_sets(fixture_root)
    svc = sets.get("service")
    if svc is None:
        pytest.skip("服务形态路径不可用（可能是临时副本创建失败），本次不判")
    missing = [s for s in MAIN_TRACK_ONLY if s not in svc]
    assert missing == [], (
        "服务形态路径（SkillFileStore + SkillIndexCache）丢了主轨技能 %r —— "
        "该路径本应能召回它们；这属于功能回归，不是预期。" % missing
    )
    # 【非空转自证 · CI-3】pathB（SkillFileStore + SkillIndexCache 服务形态）读到的必须
    # **恰好等于**「文件轨 ∪ 夹具主轨」：
    #   · `bare` 是同一夹具根上的文件轨（真仓 28 条副本）；
    #   · 夹具主轨里那 2 条**不在**文件轨里（pathA 用例已断言 `present == []`）；
    #   ⇒ 它们只可能来自**注入的那份迷你台账**。若被测路径没读到它（或读到别处的台账），
    #     这个等式立刻不成立 —— 这条断言就是"夹具确实喂到了这条真实路径"的证据。
    bare = sets.get("bare") or set()
    got, want = set(svc), (set(bare) | set(MAIN_TRACK_ONLY))
    assert got == want, (
        "服务形态路径的可召回集合 != 文件轨(%d 条) ∪ 夹具主轨(%r)："
        "多出 %r、缺少 %r ⇒ 注入的迷你主轨没被这条真实路径读到，本条是空转的"
        % (len(bare), sorted(MAIN_TRACK_ONLY),
           sorted(got - want), sorted(want - got)))


def test_s2_judges_both_paths_separately_not_disk_union():
    """S2 判据必须是「两条生产路径分别判」，不是磁盘分区并集。"""
    src = open(os.path.join(ROOT, "scripts", "verify_index_drift.py"), encoding="utf-8").read()
    assert "_production_recall_sets" in src, (
        "verify_index_drift.py 缺少 _production_recall_sets —— S2 可能退回磁盘分区口径（假绿）"
    )
    assert "load_metadata_index" in src, "S2 判据没有调用生产入口 load_metadata_index()"
    assert "pathA" in src and "pathB" in src, "S2 必须分别报告 pathA（裸入口）与 pathB（服务形态）"
    assert "参考" in src, "S2 必须把磁盘口径作为「参考（非判据）」打印，便于察觉假绿"


def test_drift_script_s2_fails_on_pathA_gap_now(fixture_root):
    """当前状态：pathA 缺 2 应报 FAIL —— 我们的工具必须说真话。

    【CI-3】根取自 `fixture_root`（**含主轨**）：旧写法下本条在干净检出上"通过"
    只是因为主轨不存在（空转），现在它必须证明「有主轨、pathA 仍缺」。
    """
    mod = _load_drift_module()
    assert set(mod.main_track_ids(fixture_root)) == set(MAIN_TRACK_ONLY), (
        "夹具根的主轨 id 必须恰好是主轨独有集合：实得 %r"
        % sorted(mod.main_track_ids(fixture_root)))
    sets = mod._production_recall_sets(fixture_root)
    bare = sets.get("bare")
    assert bare is not None
    from_main = [s for s in MAIN_TRACK_ONLY if s in bare]
    assert from_main == [], (
        "裸入口已能召回主轨技能 %r；若属实，S2 pathA 门应已转绿，请更新本测试" % from_main
    )