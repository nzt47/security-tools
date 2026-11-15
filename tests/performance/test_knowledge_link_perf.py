"""知识库链接检测 + list 缓存 性能回归测试（CI：-m performance）。

防回归护栏：
1. 6000 卡正确性：优化版 find_broken_links vs 无缓存基准逐条一致
   （防磁盘预扫描/缓存优化引入漏判或误判）
2. 确定性：3 次调用断链结果完全一致（无状态泄漏）
3. 耗时上限：6000 卡断链检测 < 2000ms（宽松阈值，防平台抖动误报）
4. list 内存缓存：热命中显著快于冷读（5 次中位数比 ≥1.43 倍，且热命中
   期间不重新读盘）；文件修改后指纹自动失效并重载（不返回陈旧数据）。判据与
   实测分布见 _WARM_RATIO_MAX 上方的注释（2026-11-15 改：原 2 倍门槛在
   windows runner 上必然红，且红的是「stat vs 解析的成本结构」而非缓存失效）

运行方式（CI test.yml performance-tests job）:
    pytest tests/performance/test_knowledge_link_perf.py -m performance --timeout=300
"""
from __future__ import annotations

import logging
import statistics
import time
from pathlib import Path

import pytest

from agent.knowledge.card import CardStore
from agent.knowledge.links import find_broken_links, find_orphans, resolve_link
from agent.knowledge.schema import Card

pytestmark = pytest.mark.performance


@pytest.fixture(autouse=True)
def _silence_knowledge_link_warnings():
    """性能测试期间屏蔽逐条断链 warning，测试结束后必须恢复。

    【不易】logging.disable() 是进程级全局开关：若模块顶层直接调用且不恢复，
    会屏蔽同进程后续所有测试的日志输出（CI 2026-08-10 Shard 6 复现：
    test_knowledge_observability 4 例捕获 buffer 为空而失败）。
    try/finally 保证每个测试结束后恢复（logging.disable(NOTSET)），
    污染不越出本模块；函数级 finally 满足 pre-commit logging-disable-leak-scan。
    """
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)

GHOST_POOL = 200


def build_store(root: Path, total: int, *, n_ghost: int = 0) -> CardStore:
    """批量写卡建库（模板 frontmatter，绕过 create 校验以提速建库）。

    结构：n_ghost 张孤儿卡（指向幽灵池）+ 互链环若干。
    """
    store = CardStore(root / "wiki")
    d = store._wiki_root / "concepts"
    d.mkdir(parents=True, exist_ok=True)

    def write(slug: str, links: list[str]) -> None:
        link_yaml = str(links).replace("'", '"')
        d.joinpath(f"{slug}.md").write_text(
            f"---\ntitle: {slug}\nslug: {slug}\nstatus: current\ntype: concepts\n"
            f"source: inbox/t.md\ndate: 2026-08-08\ntags: []\nlinks: {link_yaml}\n"
            f"contradictions: []\ninsight: 洞见\n---\n正文 {slug}\n",
            encoding="utf-8",
        )

    for i in range(n_ghost):
        write(f"o{i}", [f"幽灵{i % GHOST_POOL}"])
    for i in range(total - n_ghost):
        write(f"g{i}", [f"g{(i + 1) % (total - n_ghost)}"])
    return store


def reference_broken(cards, store_):
    """无缓存基准：每个链接独立 resolve_link（未优化前的原逻辑）。"""
    broken = []
    for card in cards:
        for target in card.links:
            if resolve_link(target, store_) is None:
                broken.append({"from_slug": card.slug, "to_slug": target})
    return sorted(broken, key=lambda b: (b["from_slug"], b["to_slug"]))


def test_broken_links_massive_correct_deterministic_fast(tmp_path):
    """6000 卡（3000 孤儿断链 + 3000 互链）：正确性 + 确定性 + 耗时上限。"""
    store = build_store(tmp_path, 6000, n_ghost=3000)
    cards = list(store.list())
    assert len(cards) == 6000

    # 正确性：优化版 vs 基准
    opt = sorted(find_broken_links(cards, store),
                 key=lambda b: (b["from_slug"], b["to_slug"]))
    ref = reference_broken(cards, store)
    assert opt == ref
    # 断链仅来自 3000 张 o* 幽灵卡（指向磁盘/内存均不存在的幽灵池）；
    # 3000 张 g* 互链环目标全部存在，不产生断链。
    assert len(opt) == 3000

    # 确定性：3 次调用一致
    r2 = sorted(find_broken_links(cards, store),
                key=lambda b: (b["from_slug"], b["to_slug"]))
    r3 = sorted(find_broken_links(cards, store),
                key=lambda b: (b["from_slug"], b["to_slug"]))
    assert opt == r2 == r3

    # 耗时上限（含磁盘预扫描）：< 2000ms
    t0 = time.perf_counter()
    find_broken_links(cards, store)
    elapsed = (time.perf_counter() - t0) * 1000
    assert elapsed < 2000, f"6000 卡断链检测 {elapsed:.0f}ms 超上限"

    # 孤儿数正确
    assert len(find_orphans(cards)) == 3000


#: 热命中 / 冷读 的耗时上限（1.43 倍即判「显著更快」）。
#:
#: 【变易 2026-11-15】原判据是 warm < cold * 0.5（2 倍），在 windows runner 上必然红：
#:   实测分布（run 43715479538 / 31d88c89b，同一份代码）
#:     · ubuntu-latest（2 vCPU）：过 2 倍门槛（历史一直绿）；
#:     · windows-latest：warm=89.8ms、cold=166.1ms = **1.85x** ⇒ 撞 2 倍门槛；
#:     · 本机（Windows 12 核，5 次采样）：cold≈450ms、warm≈3.5ms ≈ 130x，且热命中期间
#:       _list_from_disk 调用 0 次（缓存确实没重新读盘）。
#:   为什么 2 倍在 windows runner 上测不出来：热命中的成本 = 指纹扫描（2000 条
#:   scandir + stat，用来判断 mtime 是否变化），冷读的成本 = 2000 个
#:   read_text + YAML 解析。两者之比只反映「stat 与解析的相对成本」，是**平台
#:   文件系统成本结构**，不是「缓存是否生效」的度量 —— 所以 1.85x 不代表缓存失效，
#:   反过来把门槛放宽到 2 倍以下也不该只靠调数字。
#: 故判据拆成三条，缺一不可（都不依赖具体平台）：
#:   ① 热命中期间 **不得** 重新读盘（_list_from_disk 调用 0 次）—— 缓存生效的直接证据；
#:   ② 冷/热各采样 _SAMPLES 次取**中位数**（单次采样噪声大：本机首采 114ms、稳态 3.5ms，
#:      runner 上单点相差 2 倍也很常见）；
#:   ③ 中位数比 ≥ 1.43 倍（warm < cold * 0.7）—— 保留「显著更快」的量化下限；
#:      实测 1.85x 仍有 ~30% 余量，而缓存彻底失效时比值会掉到 1 附近，必定红。
_WARM_RATIO_MAX = 0.7
#: 冷/热各采样次数（中位数抗噪；每次冷读都要全量解析 2000 张卡，5 次 ≈ 1–3 秒量级）。
_SAMPLES = 5


def test_list_cache_speedup(tmp_path, monkeypatch, record_property):
    """list(use_cache=True) 热命中显著快于冷读：不重读盘 + 中位数比 ≥1.43x。"""
    store = build_store(tmp_path, 2000, n_ghost=500)
    store.list()  # 预热（文件系统缓存）

    colds: list[float] = []
    cold: list[Card] = []
    for _ in range(_SAMPLES):
        t0 = time.perf_counter()
        cold = store.list()
        colds.append((time.perf_counter() - t0) * 1000)
    assert len(cold) == 2000

    store.list(use_cache=True)  # 填充缓存（全量加载，不计时）

    calls = _spy_list_from_disk(monkeypatch)
    warms: list[float] = []
    warm: list[Card] = []
    for _ in range(_SAMPLES):
        t0 = time.perf_counter()
        warm = store.list(use_cache=True)
        warms.append((time.perf_counter() - t0) * 1000)

    assert len(warm) == 2000
    assert [c.slug for c in warm] == [c.slug for c in cold]

    med_cold = statistics.median(colds)
    med_warm = statistics.median(warms)
    ratio = med_cold / med_warm if med_warm > 0 else float("inf")
    # 分布同时进 junit 的 <properties>（CI 上传 knowledge-link-perf-results.xml，
    # 通过用例也留读数；pytest 只对失败用例写 system-out，故用 record_property）。
    record_property("list_cache_cold_ms_all", ",".join("%.1f" % t for t in colds))
    record_property("list_cache_warm_ms_all", ",".join("%.1f" % t for t in warms))
    record_property("list_cache_median_cold_ms", "%.1f" % med_cold)
    record_property("list_cache_median_warm_ms", "%.1f" % med_warm)
    record_property("list_cache_ratio", "%.2f" % ratio)
    record_property("list_cache_reloads", str(len(calls)))
    print(
        "list 缓存采样（各 %d 次，ms）：\n  cold=%s → 中位 %.1f\n  warm=%s → 中位 %.1f\n"
        "  比值（中位）%.2fx · 热命中期间重载次数 %d"
        % (
            _SAMPLES,
            ",".join("%.1f" % t for t in colds), med_cold,
            ",".join("%.1f" % t for t in warms), med_warm,
            ratio, len(calls),
        )
    )

    assert not calls, (
        "热命中触发了全量重载（日志 %d 次 _list_from_disk）：缓存未生效。"
        " cold=%s warm=%s" % (len(calls), colds, warms)
    )
    assert med_warm < med_cold * _WARM_RATIO_MAX, (
        "缓存未显著提速: 中位 warm=%.1fms vs cold=%.1fms（%.2fx，判据需 ≥%.2fx）"
        "；cold=%s warm=%s"
        % (med_warm, med_cold, ratio, 1 / _WARM_RATIO_MAX,
           ["%.1f" % t for t in colds], ["%.1f" % t for t in warms])
    )


def test_list_cache_invalidation_on_modify(tmp_path):
    """文件修改后指纹自动失效：缓存不返回陈旧数据。"""
    store = build_store(tmp_path, 300, n_ghost=0)
    store.list(use_cache=True)

    # 修改 g0 状态为 draft
    p = store._wiki_root / "concepts" / "g0.md"
    text = p.read_text(encoding="utf-8")
    p.write_text(text.replace("status: current", "status: draft"), encoding="utf-8")

    reloaded = store.list(use_cache=True)
    by_slug = {c.slug: c for c in reloaded}
    assert by_slug["g0"].status == "draft", "指纹未失效，返回了陈旧缓存!"


def _make_card(slug: str, *, status: str = "current", type_: str = "concepts") -> Card:
    return Card(
        title=slug, slug=slug, status=status, type=type_,
        source="inbox/t.md", date="2026-08-08", insight="洞见", content=f"正文 {slug}",
    )


def _spy_list_from_disk(monkeypatch) -> list:
    """包装 _list_from_disk 计数调用次数（检测是否触发全量重载）。"""
    calls: list = []
    original = CardStore._list_from_disk

    def spy(self, parallel: bool = False):
        # 【变易】透传 parallel：c7774023 给 _list_from_disk 增加 parallel 参数后，
        # 旧 spy 签名未接收该参数，store.list() 调用 _list_from_disk(parallel=...)
        # 时抛 TypeError，导致性能测试 6/6 全失败（CI 全绿回归）。
        calls.append(1)
        return original(self, parallel=parallel)

    monkeypatch.setattr(CardStore, "_list_from_disk", spy)
    return calls


def test_list_cache_sync_on_create(tmp_path, monkeypatch):
    """create 后缓存增量同步：立即可见，且不触发全量重载。"""
    store = build_store(tmp_path, 300, n_ghost=0)
    store.list(use_cache=True)  # 加载缓存
    calls = _spy_list_from_disk(monkeypatch)

    store.create(_make_card("new1"))
    reloaded = store.list(use_cache=True)

    assert not calls, "create 后缓存应增量同步，不应触发全量重载"
    slugs = {c.slug for c in reloaded}
    assert "new1" in slugs and len(slugs) == 301


def test_list_cache_sync_on_update(tmp_path, monkeypatch):
    """update 后缓存增量同步：内容/状态变更立即可见，且不触发全量重载。"""
    store = build_store(tmp_path, 300, n_ghost=0)
    store.list(use_cache=True)
    calls = _spy_list_from_disk(monkeypatch)

    card = _make_card("g0", status="draft")
    store.update(card)
    reloaded = store.list(use_cache=True)

    assert not calls, "update 后缓存应增量同步，不应触发全量重载"
    by_slug = {c.slug: c for c in reloaded}
    assert by_slug["g0"].status == "draft"


def test_list_cache_sync_on_update_type_move(tmp_path, monkeypatch):
    """update 变更 type：旧目录移除 + 新目录加入，缓存与指纹同步。"""
    store = build_store(tmp_path, 300, n_ghost=0)
    store.list(use_cache=True)
    calls = _spy_list_from_disk(monkeypatch)

    store.update(_make_card("g0", type_="entities"))
    reloaded = store.list(use_cache=True)

    assert not calls, "type 迁移后缓存应增量同步，不应触发全量重载"
    by_slug = {c.slug: c for c in reloaded}
    assert by_slug["g0"].type == "entities"
    assert len(reloaded) == 300, "type 迁移不应增减卡片数量"


def test_list_cache_sync_on_delete(tmp_path, monkeypatch):
    """delete 后缓存增量同步：立即消失，且不触发全量重载。"""
    store = build_store(tmp_path, 300, n_ghost=0)
    store.create(_make_card("iso"))  # 孤立卡（无入链），建库前创建
    store.list(use_cache=True)
    calls = _spy_list_from_disk(monkeypatch)

    assert store.delete("iso") is True
    # 【不易】delete 内部的入链检查（_incoming_sources）在索引缺失时回退
    # 全库扫描 self.list()，会触发一次 _list_from_disk —— 这是删除语义的
    # 合法扫描，非缓存失效。重置计数后再 list(use_cache=True) 验证增量
    # 同步确实生效（不因误计导致假阳性断言失败）。
    calls.clear()
    reloaded = store.list(use_cache=True)

    assert not calls, "delete 后缓存应增量同步，不应触发全量重载"
    assert "iso" not in {c.slug for c in reloaded}
    assert len(reloaded) == 300


def test_delete_many_invalidates_cache(tmp_path):
    """批量删除后缓存整体失效：下次 list 全量重载，正确性不退化。"""
    store = build_store(tmp_path, 300, n_ghost=0)
    store.list(use_cache=True)
    store.create(_make_card("iso1"))
    store.create(_make_card("iso2"))

    res = store.delete_many(["iso1", "iso2"])

    assert res == {"iso1": True, "iso2": True}
    reloaded = store.list(use_cache=True)  # 失效后触发全量重载
    slugs = {c.slug for c in reloaded}
    assert "iso1" not in slugs and "iso2" not in slugs
    assert len(reloaded) == 300
