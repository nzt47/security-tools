# -*- coding: utf-8 -*-
"""E1-D · 工具路由**下发集确定性**回归（跨进程 / 跨 PYTHONHASHSEED）

背景（E1-D 实测，见 docs/audit_skill_governance/E1D.md §1）
--------------------------------------------------------
E1-C 留下一句"下发层（hybrid_select_tools 返回值）跨进程不可复现"。E1-D 复现并定位：
融合入口用 `all_candidates: set[str]` 汇合两路候选，而 `fused.sort` 是**稳定排序**
⇒ 分数并列的先后 = set 迭代序 = **字符串哈希随机化** ⇒ 截断点落在并列块内部时，
**成员**随进程变。下发阶同理：`hybrid_select_tools` 把 `selected`（set）交给
`_apply_alias_merge_and_priority_sort`，其内部 `sorted(selected, key=priority)` 同样
稳定排序 ⇒ 同优先级并列项的先后随哈希变 ⇒ `max_tools` 截断点上的成员随进程变。

本文件把修复钉死（三条判据，缺一不可）
------------------------------------
1. **并列必然发生的合成索引**上，融合序列必须是"分数降序 + 候选汇合序"这一条确定序列；
2. **真跨进程**：同一索引 / 同一查询，PYTHONHASHSEED ∈ {0,1,2,random,random} 各起一个
   新解释器，`hybrid_select_tools` 的完整**有序**下发集必须逐位相同；
3. 下发阶交给 helper 的候选**必须是有序序列**（不是 set）—— 否则第 2 条只能靠"运气"
   （并列块恰好不跨截断点时看不出来）。

【非空转自证】把 `_query_locked` 的次级键 `(-x[1], _cand_pos[x[0]])` 去掉（回到
`key=lambda x: x[1], reverse=True`），并把 `ordered_candidates` 换回 `selected`：
本文件的 1/2/3 条**全部变红**（原话与原始输出见 E1D.md §2.3）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytestmark = pytest.mark.timeout(300)

#: 并列块规模：必须 **大于** 候选池截断点（top_k/max_tools = 25），
#: 否则并列只影响块内顺序、不影响成员 ⇒ 判据 2 会退化成空转。
_N_TIES = 40
_TIE_DESC = "tieprobezeta 并列探针 检索 演示"
_TIE_QUERY = "tieprobezeta 并列探针"


@pytest.fixture(autouse=True)
def _disable_embedding_probe(monkeypatch):
    """纯 BM25 降级路：本文件的判据与向量腿无关（且避免 18s 模型加载）"""
    monkeypatch.setenv("AGENT_HYBRID_EMBEDDING", "0")


@pytest.fixture(autouse=True)
def _reset_singleton():
    import agent.tool_router_hybrid as mod
    from agent.tool_router_hybrid import reset_hybrid_retriever

    reset_hybrid_retriever()
    mod._PROBE_RESULT = None
    yield
    reset_hybrid_retriever()
    mod._PROBE_RESULT = None


@pytest.fixture
def tie_index(tmp_path):
    """真实工具 + N 个**描述逐字相同**的探针工具（⇒ BM25 raw 分严格相等）"""
    real = ROOT / "data" / "tool_index.json"
    if not real.exists():
        pytest.skip("真实索引缺失: %s" % real)
    with open(real, "r", encoding="utf-8") as f:
        tools = list(json.load(f).get("tools", []))
    for i in range(1, _N_TIES + 1):
        tools.append({"name": "tieprobe%02d" % i,     # 等长名 ⇒ 文档长度一致
                      "description": _TIE_DESC, "parameter_names": []})
    p = tmp_path / "synth_tie_index.json"
    p.write_text(json.dumps({"tools": tools}, ensure_ascii=False), encoding="utf-8")
    return p


class TestFusedOrderIsDeterministic:
    """判据 1：并列块的融合序列 = 候选汇合序（= BM25 索引序），不再是哈希序"""

    def test_tie_block_keeps_candidate_order(self, tie_index):
        from agent.tool_router_hybrid import HybridRetriever

        r = HybridRetriever(index_path=str(tie_index))
        assert r.available and r.degraded is True
        fused = r.query(_TIE_QUERY, top_k=_N_TIES + 10) or []
        names = [n for n, _ in fused]
        tie_names = [n for n in names if n.startswith("tieprobe")]
        assert len(tie_names) == _N_TIES, (
            "并列块没被完整召回（判据会空转）：%d != %d" % (len(tie_names), _N_TIES))

        # 分数确实**全等**（否则这条用例证明的不是"并列下的确定性"）
        scores = {round(s, 12) for n, s in fused if n.startswith("tieprobe")}
        assert len(scores) == 1, "并列块分数不等 ⇒ 本用例没测到并列：%s" % scores

        # 并列块内部顺序 == 索引插入序（= 候选汇合序），即**不是** set 迭代序
        assert tie_names == ["tieprobe%02d" % i for i in range(1, _N_TIES + 1)], (
            "并列块顺序不是确定的候选汇合序 —— 回到了 set 迭代序（哈希随机化）")
        # 主键语义未变：分数整体降序
        vals = [s for _, s in fused]
        assert vals == sorted(vals, reverse=True), "主键（分数降序）被破坏"


class TestPayloadIsCrossProcessDeterministic:
    """判据 2：真跨进程（不同 PYTHONHASHSEED）下发集**逐位**相同"""

    _DRIVER = (
        "import json, sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import agent.tool_router_hybrid as trh\n"
        "r = trh.HybridRetriever(index_path=sys.argv[2])\n"
        "trh._hybrid_instance = r\n"
        "p = trh.hybrid_select_tools(sys.argv[3], None, max_tools=25, top_k=40)\n"
        "print('E1D ' + json.dumps(list(p or []), ensure_ascii=False))\n"
    )

    def _run(self, index, seed):
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = seed
        env["PYTHONUTF8"] = "1"
        env["AGENT_HYBRID_EMBEDDING"] = "0"
        for k in ("AGENT_HYBRID_ALPHA",):
            env.pop(k, None)
        proc = subprocess.run([sys.executable, "-c", self._DRIVER, str(ROOT),
                               str(index), _TIE_QUERY],
                              env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=240)
        for ln in (proc.stdout or "").splitlines():
            if ln.startswith("E1D "):
                return json.loads(ln[4:])
        raise AssertionError("子进程未产出下发集：rc=%s stderr=%s"
                             % (proc.returncode, (proc.stderr or "")[-500:]))

    def test_payload_is_identical_across_hash_seeds(self, tie_index):
        seen = {}
        for seed in ("0", "1", "2", "random", "random"):
            seen.setdefault(tuple(self._run(tie_index, seed)), []).append(seed)
        assert len(seen) == 1, (
            "同一查询 / 同一索引 / 同一份代码，跨进程下发了**不同的工具集**：\n"
            + "\n".join("  seeds=%s -> %s" % (v, list(k)) for k, v in seen.items()))
        payload = list(next(iter(seen)))
        assert len(payload) == 25, "下发集规模异常：%d" % len(payload)
        assert payload[:4] == ["get_status", "search_memory", "remember", "tieprobe01"], (
            "下发集前缀不是确定序列（相关度序/类别序被扰动）：%s" % payload[:6])


class TestPayloadStageCandidateFeedIsOrdered:
    """判据 3：交给 helper 的候选必须是**有序序列**（set 回潮即红）"""

    def test_helper_receives_an_ordered_sequence(self, tie_index, monkeypatch):
        import agent.tool_router_hybrid as trh

        captured = {}
        real = trh._apply_alias_merge_and_priority_sort

        def spy(selected, categories, max_tools, preferred_order=None):
            captured["selected"] = selected
            captured["categories"] = categories
            return real(selected, categories, max_tools,
                        preferred_order=preferred_order)

        monkeypatch.setattr(trh, "_apply_alias_merge_and_priority_sort", spy)
        trh.reset_hybrid_retriever()
        r = trh.HybridRetriever(index_path=str(tie_index))
        trh._hybrid_instance = r
        out = trh.hybrid_select_tools(_TIE_QUERY, None, max_tools=25, top_k=40)

        assert out, "下发集为空 ⇒ 本用例没测到真实路径"
        sel = captured.get("selected")
        assert sel is not None, "helper 未被调用"
        assert not isinstance(sel, (set, frozenset)), (
            "候选又以 set 形式交给 helper 了 —— 同优先级并列的先后会退回哈希序，"
            "截断点上的成员将随进程变（E1-D 本体的根因）")
        assert isinstance(sel, list), "候选必须是有序序列，实测 %s" % type(sel)
        tie_seq = [t for t in sel if t.startswith("tieprobe")]
        assert tie_seq == ["tieprobe%02d" % i for i in range(1, _N_TIES + 1)], (
            "有序候选里的并列块次序不是确定的汇合序：%s" % tie_seq[:8])
        assert not isinstance(captured.get("categories"), (set, frozenset)), (
            "类别集合又以 set 形式传入 —— code/knowledge 同为 priority=5，"
            "floors 的先后会随哈希变")
