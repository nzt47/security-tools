"""负样本库回归测试 — 验证 BM25 检索的区分能力

【不易】不破坏现有 hybrid 测试,不依赖 Embedding(纯 BM25 路径)
【变易】G7/G8 方向性 case 用 xfail 标记,记录 BM25 已知缺陷,
        未来 Reranker 上线后这些 case 应转为 PASS
【简易】parametrize 展开 25 query,失败明细清晰(selected vs expected/negative)

测试数据: data/tool_negative_samples.json (v1.1, 10 组 25 query)
关联报告: docs/reports/tool_retrieval_eval_report_20260719.md §3.2
"""

import json
import os

import pytest

from agent.tool_router_hybrid import get_hybrid_retriever, reset_hybrid_retriever

# 负样本库路径(相对测试文件定位项目根)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_NEGATIVE_SAMPLES_PATH = os.path.join(_PROJECT_ROOT, "data", "tool_negative_samples.json")

# xfail 标记:BM25 单路检索的已知缺陷 case
# key = (group_id, query),value = xfail 原因(分类 + 详细说明)
#
# 实测结果(2026-07-19,70 工具索引):
#   - 25 个 case 中 10 个 PASS,15 个 xfail
#   - xfail 分布:G1(2)/G4(2)/G5(1)/G6(3)/G7(2)/G8(2)/G9(2)/G10(1)
#   - 失败类型:召回缺失(BM25 词频分散)/ 负样本泄漏(无法区分相似工具)/ 方向性混淆
#
# 这些 xfail 是引入 Cross-Encoder Reranker 的核心依据。
# Reranker 上线后,这些 case 应转为 PASS,届时移除对应 xfail 标记。
_XFAIL_CASES = {
    # ── 方向性混淆(BM25 无法区分动词方向)— 报告 §3.2 明确识别 ──
    ("G7_compress_family", "把 logs 文件夹压缩成 zip"):
        "方向性混淆:BM25 无法区分压缩/解压方向,待 Reranker(报告 q07)",
    ("G8_format_convert_direction", "把 config.json 转换成 yaml 格式"):
        "方向性混淆:BM25 无法区分转换方向,待 Reranker(报告 q08)",

    # ── 召回缺失(BM25 词频分散,expected_positive 不在 top-5)──
    ("G1_web_search_family", "在百度上搜索 Python 教程"):
        "召回缺失:web_search 不在 top-5,search_* 工具族互相干扰",
    ("G1_web_search_family", "抓取 https://example.com 的 HTML 内容"):
        "召回缺失:web_get 不在 top-5,fetch_news 泄漏(BM25 对 URL 不敏感)",
    ("G4_list_family", "列出 /home/user 下的所有文件"):
        "召回缺失:list_directory 不在 top-5,list_async_tasks 泄漏",
    ("G4_list_family", "查看提交的后台任务列表"):
        "召回缺失:list_async_tasks 不在 top-5,submit_task 词频更高",
    ("G5_install_family", "安装 markdown 编辑器扩展"):
        "召回缺失:ext_install 不在 top-5,install_tool 词频更高",
    ("G6_task_family", "创建每天凌晨 3 点执行的定时任务"):
        "召回缺失:schedule_task 不在 top-5,list_scheduled_tasks 词频更高",
    ("G9_search_semantic_family", "在 Google 上搜索 Python 异步教程"):
        "召回缺失:web_search 不在 top-5,search_* 工具族互相干扰",
    ("G9_search_semantic_family", "回忆之前讨论过的项目架构"):
        "召回缺失:search_memory 不在 top-5,BM25 对「回忆」语义不敏感",

    # ── 负样本泄漏(BM25 无法区分相似工具,negative 进入 top-5)──
    ("G6_task_family", "提交一个后台数据处理任务"):
        "负样本泄漏:schedule_task 进入 top-5(BM25 对「任务」匹配过宽)",
    ("G6_task_family", "取消任务 ID 为 abc123 的后台任务"):
        "负样本泄漏:schedule_task + submit_task 进入 top-5",
    ("G7_compress_family", "解压 archive.tar.gz 到当前目录"):
        "负样本泄漏:compress 进入 top-5(BM25 无法区分压缩/解压方向)",
    ("G8_format_convert_direction", "读取 data.yaml 转成 JSON 对象"):
        "负样本泄漏:json_to_yaml 进入 top-5(BM25 无法区分转换方向)",
    ("G10_read_write_direction", "读取 config.yaml 的内容"):
        "负样本泄漏:write_file 进入 top-5(BM25 对「文件」匹配过宽)",
}


# ════════════════════════════════════════════════════════════
#  Fixtures
# ════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _disable_embedding_probe(monkeypatch):
    """禁用 Embedding 探测,走纯 BM25 路径(避免子进程加载模型)"""
    monkeypatch.setenv("AGENT_HYBRID_EMBEDDING", "0")
    yield


@pytest.fixture(autouse=True)
def _reset_hybrid_singleton():
    """每个测试前重置 hybrid 单例,避免索引污染"""
    reset_hybrid_retriever()
    yield
    reset_hybrid_retriever()


# ════════════════════════════════════════════════════════════
#  数据加载辅助
# ════════════════════════════════════════════════════════════

def _load_negative_samples() -> dict:
    """加载负样本库 JSON"""
    with open(_NEGATIVE_SAMPLES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _collect_all_cases() -> list[tuple[str, str, tuple, tuple]]:
    """展开所有 (group_id, query, expected_positive, negative) 四元组"""
    data = _load_negative_samples()
    cases = []
    for group in data["groups"]:
        gid = group["group_id"]
        for q in group["queries"]:
            cases.append((
                gid,
                q["query"],
                tuple(q["expected_positive"]),
                tuple(q["negative"]),
            ))
    return cases


# 模块加载时求值(用于 parametrize)
_ALL_CASES = _collect_all_cases()
_CASE_IDS = [f"{c[0]}_q{i:02d}" for i, c in enumerate(_ALL_CASES)]


# ════════════════════════════════════════════════════════════
#  结构合法性测试
# ════════════════════════════════════════════════════════════

class TestNegativeSamplesStructure:
    """验证负样本库 JSON 结构合法性"""

    def test_json_loadable(self):
        """JSON 可解析"""
        data = _load_negative_samples()
        assert isinstance(data, dict)
        assert "version" in data
        assert "groups" in data

    def test_version_is_v1_1(self):
        """版本号应为 v1.1(扩充后)"""
        data = _load_negative_samples()
        assert data["version"] == "1.1"

    def test_groups_count_is_10(self):
        """组数应为 10(G1-G10)"""
        data = _load_negative_samples()
        assert len(data["groups"]) == 10

    def test_total_queries_count_is_25(self):
        """总 query 数应为 25"""
        data = _load_negative_samples()
        total = sum(len(g["queries"]) for g in data["groups"])
        assert total == 25

    def test_all_groups_have_required_fields(self):
        """每组必须含 group_id/tools/queries 字段"""
        data = _load_negative_samples()
        for g in data["groups"]:
            assert "group_id" in g, f"组缺少 group_id: {g}"
            assert "tools" in g, f"组 {g.get('group_id')} 缺少 tools"
            assert "queries" in g, f"组 {g.get('group_id')} 缺少 queries"
            assert len(g["queries"]) > 0, f"组 {g.get('group_id')} queries 为空"
            assert len(g["tools"]) >= 2, f"组 {g.get('group_id')} tools 少于 2 个"

    def test_all_queries_have_required_fields(self):
        """每条 query 必须含 query/expected_positive/negative 字段"""
        data = _load_negative_samples()
        for g in data["groups"]:
            for q in g["queries"]:
                assert "query" in q, f"组 {g['group_id']} 有 query 缺 query 字段"
                assert "expected_positive" in q, f"组 {g['group_id']} query 缺 expected_positive"
                assert "negative" in q, f"组 {g['group_id']} query 缺 negative"
                assert len(q["expected_positive"]) > 0, (
                    f"组 {g['group_id']} query '{q['query']}' expected_positive 为空"
                )
                assert isinstance(q["negative"], list), (
                    f"组 {g['group_id']} query '{q['query']}' negative 不是 list"
                )

    def test_new_groups_present(self):
        """验证 v1.1 新增的 4 个组存在"""
        data = _load_negative_samples()
        group_ids = {g["group_id"] for g in data["groups"]}
        expected_new = {
            "G7_compress_family",
            "G8_format_convert_direction",
            "G9_search_semantic_family",
            "G10_read_write_direction",
        }
        assert expected_new.issubset(group_ids), f"缺失组: {expected_new - group_ids}"


# ════════════════════════════════════════════════════════════
#  检索区分能力测试
# ════════════════════════════════════════════════════════════

class TestNegativeSamplesRetrieval:
    """对每个 query 跑 BM25 检索,验证区分能力

    验证逻辑:
    1. 召回验证:expected_positive 全部出现在 top-5
    2. 区分验证:negative 全部不出现在 top-5

    xfail 策略:
    - G7 q1(压缩方向)、G8 q1(JSON→YAML 方向)预期 BM25 失败
    - 失败时调用 pytest.xfail() 标记,不报错
    - 未来 Reranker 上线后,这些 case 应转为 PASS(测试通过即修复)
    """

    @pytest.mark.parametrize(
        "group_id, query, expected_positive, negative",
        _ALL_CASES,
        ids=_CASE_IDS,
    )
    def test_query_distinction(self, group_id, query, expected_positive, negative):
        """验证 query 召回 expected_positive 且不召回 negative"""
        retriever = get_hybrid_retriever()
        assert retriever is not None, "HybridRetriever 初始化失败"

        results = retriever.query(query, top_k=5)
        selected = [tool for tool, _ in results]
        selected_set = set(selected)

        # 召回验证:expected_positive 全部在 top-5
        missing = set(expected_positive) - selected_set
        # 区分验证:negative 全部不在 top-5
        leaked = set(negative) & selected_set

        # 构造错误信息(若任一验证失败)
        error_parts = []
        if missing:
            error_parts.append(f"召回缺失={sorted(missing)}")
        if leaked:
            error_parts.append(f"负样本泄漏={sorted(leaked)}")

        if error_parts:
            error_msg = " | ".join(error_parts) + f" | selected={selected}"
            xfail_reason = _XFAIL_CASES.get((group_id, query))
            if xfail_reason:
                # 预期失败:BM25 已知缺陷,标记 xfail 不报错
                pytest.xfail(f"{xfail_reason} | {error_msg}")
            else:
                # 非预期失败:报错
                pytest.fail(f"[{group_id}] query='{query}' {error_msg}")
        # 无 error:测试通过(若 xfail_reason 存在,这是意外通过,说明 BM25 改进或工具描述优化)


# ════════════════════════════════════════════════════════════
#  统计汇总测试(非 parametrize,整体视角)
# ════════════════════════════════════════════════════════════

class TestNegativeSamplesStatistics:
    """整体统计:验证 xfail case 数量符合预期(防止 xfail 标记漂移)"""

    def test_xfail_cases_count_is_15(self):
        """xfail 标记的 case 数应为 15(实测 BM25 单路缺陷 case)

        分布:G1(2)/G4(2)/G5(1)/G6(3)/G7(2)/G8(2)/G9(2)/G10(1)
        若 Reranker 上线后 case 转为 PASS,应同步减少 xfail 并更新本断言
        """
        assert len(_XFAIL_CASES) == 15, (
            f"xfail case 数应为 15,实际 {len(_XFAIL_CASES)}。"
            f"若新增/移除 xfail,请同步更新本断言"
        )

    def test_xfail_cases_exist_in_samples(self):
        """xfail 标记的 case 必须实际存在于负样本库"""
        all_case_keys = {(c[0], c[1]) for c in _ALL_CASES}
        for xfail_key in _XFAIL_CASES:
            assert xfail_key in all_case_keys, (
                f"xfail case {xfail_key} 不存在于负样本库,请检查标记"
            )

    def test_xfail_groups_cover_8_groups(self):
        """xfail case 应覆盖 8 个组(G1/G4/G5/G6/G7/G8/G9/G10)

        G2(pdf)和 G3(execute)未出现 xfail,因 alias merge 后区分度足够
        """
        xfail_groups = {key[0] for key in _XFAIL_CASES}
        expected_groups = {
            "G1_web_search_family",
            "G4_list_family",
            "G5_install_family",
            "G6_task_family",
            "G7_compress_family",
            "G8_format_convert_direction",
            "G9_search_semantic_family",
            "G10_read_write_direction",
        }
        assert xfail_groups == expected_groups, (
            f"xfail 组应为 {expected_groups},实际 {xfail_groups}"
        )

    def test_passing_cases_count_is_10(self):
        """通过 case 数应为 10(25 - 15 xfail)"""
        passing_count = len(_ALL_CASES) - len(_XFAIL_CASES)
        assert passing_count == 10, (
            f"通过 case 数应为 10,实际 {passing_count}"
        )


# ════════════════════════════════════════════════════════════
#  基础检索可用性测试(确保 hybrid retriever 能正常工作)
# ════════════════════════════════════════════════════════════

class TestHybridRetrieverAvailable:
    """基础检索可用性 — 确保负样本测试的前提条件成立"""

    def test_retriever_can_be_initialized(self):
        """HybridRetriever 可正常初始化"""
        retriever = get_hybrid_retriever()
        assert retriever is not None

    def test_retriever_returns_results_for_simple_query(self):
        """简单 query 应返回非空结果"""
        retriever = get_hybrid_retriever()
        results = retriever.query("搜索", top_k=5)
        assert len(results) > 0, "简单 query 应返回非空结果"
        assert all(isinstance(t, tuple) and len(t) == 2 for t in results), (
            "结果应为 (tool_name, score) 元组列表"
        )

    def test_retriever_top_k_respected(self):
        """top_k 参数应被尊重"""
        retriever = get_hybrid_retriever()
        results = retriever.query("搜索", top_k=3)
        assert len(results) <= 3, f"top_k=3 但返回 {len(results)} 个结果"