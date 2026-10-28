# -*- coding: utf-8 -*-
"""分片耗时权重表的守卫用例。

【守什么】scripts/shard_time_weights*.json 是**提交产物**，直接决定 CI 的
6 分片成员（split_unit_tests.py --by=time）。它由 CI 的 junit 产物机械重生成
（scripts/regen_shard_time_weights.py），因此这里守三类容易踩的坑：

1. **表与文件系统脱节**（文件改名/删了，表里还留着旧路径）⇒ 权重永远命中不了，
   该文件退回按条数定标；② **表被手改坏**（负值/0/缺 calibration）⇒ 贪心把文件
   反复塞进同一分片，某个 shard 被拖长到 runner 回收。
2. **表内不一致 ⇒ 分配失衡**：用表重算一次 --shards N 的分配，断言 max/min 有界。
   **注意它守不住什么**：权重表「过期」（真实耗时涨了但表没重算）时，分配按旧数字
   仍然均衡、本断言照绿 —— 2026-10-28 之前 Shard 4 实测 1620–1851s、其余
   341–720s（比例 3.6–5.4）正是这种过期，机器判不出来。过期只能靠人按
   §11.10 定期重跑 scripts/regen_shard_time_weights.py（该脚本 --check 可做
   「表 = junit 重算结果」的逐字节对拍）。本断言守的是另一类错：表里有负值/0、
   某个重文件的数值被手改小 ⇒ 贪心立刻把重文件堆进同一分片。
3. **分片落到「单文件」**：integration job 用 pytest -m 过滤（not slow/e2e/
   requires_llm/skip_ci），若贪心为均衡把某个在该 job 里被整体过滤的慢文件
   单独放进一个分片，该 shard 会收集到 0 条用例、pytest 退出码 5 ⇒ job 假红。
   故断言每个分片至少 2 个文件，并断言 integration 模式选的是**它自己的**表。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"

GENERIC_TABLE = SCRIPTS / "shard_time_weights.json"
INTEGRATION_TABLE = SCRIPTS / "shard_time_weights_integration.json"

MODES = (
    pytest.param("tests/unit", 6, id="unit-6"),
    pytest.param("tests/integration", 4, id="integration-4"),
    pytest.param("tests", 6, id="full-project-6"),
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def splitter():
    import sys
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    import split_unit_tests

    return split_unit_tests


@pytest.fixture(scope="module")
def assignments(splitter) -> dict:
    """每个模式只算一次：收集文件 → 权重 → 贪心分配。"""
    out = {}
    for mode, shards in (("tests/unit", 6), ("tests/integration", 4), ("tests", 6)):
        files = splitter.collect_test_files(ROOT, mode)
        use_lines = mode in ("tests", "tests/integration")
        counts = {f: splitter.count_tests(ROOT, f, use_lines=use_lines) for f in files}
        raw = ({f: splitter.count_tests(ROOT, f, use_lines=False) for f in files}
               if use_lines else counts)
        table, rate = splitter.load_time_weights(ROOT, mode)
        weights = splitter.time_weights(files, counts, raw, table, rate)
        buckets = splitter.greedy_assign(files, weights, shards)
        out[mode] = {
            "shards": shards,
            "files": files,
            "buckets": buckets,
            "weights": weights,
            "totals": [sum(weights[f] for f in b) for b in buckets],
        }
    return out


@pytest.mark.parametrize("path", [GENERIC_TABLE, INTEGRATION_TABLE], ids=["generic", "integration"])
def test_table_shape_and_paths_exist(path):
    """表结构完整、每个键都指向真实存在的测试文件、权重全为正数。"""
    doc = _load(path)
    assert doc["schema"] == 1
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", doc["generated_at"]), doc["generated_at"]
    files = doc["files"]
    assert files, "权重表不能为空"
    missing = [p for p in files if not (ROOT / p).is_file()]
    assert not missing, "表里有已不存在的文件: %s" % missing[:5]
    assert all(p.endswith(".py") for p in files)
    non_positive = {p: w for p, w in files.items() if not isinstance(w, (int, float)) or w <= 0}
    assert not non_positive, "权重必须为正: %s" % list(non_positive.items())[:5]


@pytest.mark.parametrize("path", [GENERIC_TABLE, INTEGRATION_TABLE], ids=["generic", "integration"])
def test_calibration_is_self_consistent(path):
    """calibration 与 files 对得上：手改表而不重算时这里会红。"""
    doc = _load(path)
    cal = doc["calibration"]
    total = sum(doc["files"].values())
    # 允许 1 秒级误差：表内是 3 位小数四舍五入后再求和
    assert abs(cal["measured_seconds_total"] - total) < 1.0, (cal["measured_seconds_total"], total)
    assert cal["measured_files"] == len(doc["files"])
    assert 0 < cal["default_seconds_per_test"] < 10, cal["default_seconds_per_test"]
    # 每次实测的 shard 步骤墙钟都不该比该分片的 junit 合计还小
    for shard, meta in cal["observed_shards"].items():
        if "step_s" in meta:
            assert meta["step_s"] >= meta["junit_s"] * 0.5, (shard, meta)


def test_integration_mode_uses_its_own_table(splitter):
    """integration job 的 marker 过滤与其它 job 不同 ⇒ 必须选专属表。

    专属表缺失时会静默回退通用表（这是运行期兜底，不能当成正常态）：一旦回退，
    integration 的 4 个分片会各被 1–2 个「在本 job 被过滤掉」的慢文件占住。
    """
    table, rate = splitter.load_time_weights(ROOT, "tests/integration")
    expected = _load(INTEGRATION_TABLE)
    assert rate == pytest.approx(expected["calibration"]["default_seconds_per_test"], abs=1e-9)
    assert set(table) == set(expected["files"])


@pytest.mark.parametrize("mode,shards", MODES)
def test_assignment_is_a_partition(assignments, mode, shards):
    """每个测试文件恰好进一个分片，且并集等于收集口径（不漏不重）。"""
    data = assignments[mode]
    assert len(data["buckets"]) == shards
    flat = [f for b in data["buckets"] for f in b]
    assert len(flat) == len(set(flat))
    assert sorted(flat) == sorted(data["files"])


@pytest.mark.parametrize("mode,shards", MODES)
def test_assignment_is_balanced(assignments, mode, shards):
    """分片秒数 max/min 有界（守表内一致性，不守「表是否过期」）。"""
    totals = assignments[mode]["totals"]
    assert min(totals) > 0
    assert max(totals) / min(totals) <= 1.20, [round(t) for t in totals]


@pytest.mark.parametrize("mode,shards", MODES)
def test_every_shard_has_at_least_two_files(assignments, mode, shards):
    """单文件分片 = 该文件被 marker 全过滤时 job 假红（pytest 退出码 5）。"""
    counts = [len(b) for b in assignments[mode]["buckets"]]
    assert min(counts) >= 2, counts


def test_splitter_falls_back_when_table_missing(tmp_path, splitter):
    """表不存在时不抛异常（CI 不能因缺数据而崩），退回按条数均衡。"""
    table, rate = splitter.load_time_weights(tmp_path, "tests/integration")
    assert table == {}
    assert rate > 0
