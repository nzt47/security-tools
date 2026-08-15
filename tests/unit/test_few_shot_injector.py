"""Few-shot 注入器单元测试

覆盖:
- 加载: JSONL 成功加载 / 坏行跳过 / 文件缺失返回空列表
- 选择: 按 intent 做 TF-IDF 检索，过滤 rating<4 示例
- 注入: 无示例 / 示例数<3 / 无高置信匹配时不注入（宁缺毋滥）
- 采集: add_example 去重；service.record_execution 仅采集 rating=5 成功案例
- 集成: ContextInjector.build_context 注入 Few-shot 段落（Layer 2.5）
"""
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent.skills_mgmt.few_shot_injector import FewShotExample, FewShotInjector
from agent.skills_mgmt.context_injector import ContextInjector
from agent.skills_mgmt.loader import MatchResult, SkillMatch


# ═══════════════════════════════════════════════════════════════════
#  Fixture 与工具
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def injector(tmp_path):
    """每个测试独立的临时示例库目录"""
    return FewShotInjector(few_shot_dir=str(tmp_path / "skill_few_shot"))


def _example(example_id="ex_001", intent="用户问昨天天气",
             input_="用户问昨天天气如何",
             output="昨天晴转多云，最高温 28 度。",
             rating=5, tags=None, created_at="2026-07-13T10:00:00"):
    return FewShotExample(
        example_id=example_id,
        intent=intent,
        input=input_,
        output=output,
        rating=rating,
        tags=tags or ["weather", "query"],
        created_at=created_at,
    )


def _write_jsonl(injector: FewShotInjector, skill_id: str,
                 *lines: str) -> Path:
    """直接写入原始行（含坏行测试）"""
    path = injector._path_for(skill_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_examples(injector: FewShotInjector, skill_id: str,
                    examples: list) -> Path:
    path = injector._path_for(skill_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for ex in examples:
            f.write(json.dumps(ex.to_dict(), ensure_ascii=False) + "\n")
    return path


def _make_mock_loader(skill_id: str = "self_reflection") -> MagicMock:
    """构造 build_context 可用的 mock loader"""
    loader = MagicMock()
    match = SkillMatch(
        skill_id=skill_id, name=skill_id, description="测试技能",
        score=0.9, estimated_tokens=50,
    )
    loader.match.return_value = MatchResult(
        matches=[match], total_scanned=1, elapsed_ms=1.0,
        estimated_total_tokens=50,
    )
    loader.list_all_metadata.return_value = [
        {"skill_id": skill_id, "enabled": True},
    ]
    loader.load_instruction.return_value = {
        "skill_id": skill_id,
        "instruction": "## 使用说明\n1. 步骤一\n2. 步骤二\n",
        "estimated_tokens": 20,
    }
    return loader


# ═══════════════════════════════════════════════════════════════════
#  1. 加载示例库
# ═══════════════════════════════════════════════════════════════════

class TestLoadExamples:

    def test_load_examples_from_jsonl(self, injector):
        """加载示例库成功（含坏行跳过，不阻塞）"""
        _write_jsonl(
            injector, "weather",
            json.dumps(_example("ex_001", "用户问昨天天气").to_dict(),
                       ensure_ascii=False),
            "this is not valid json {",          # 坏行：跳过
            json.dumps({"example_id": "ex_bad"}, ensure_ascii=False),  # 缺必填字段：跳过
            json.dumps(_example("ex_002", "用户问今天日期").to_dict(),
                       ensure_ascii=False),
        )
        examples = injector.load_examples("weather")
        assert len(examples) == 2
        assert examples[0].example_id == "ex_001"
        assert examples[0].intent == "用户问昨天天气"
        assert examples[0].rating == 5
        assert examples[0].tags == ["weather", "query"]

    def test_load_examples_missing_file_returns_empty(self, injector):
        """示例库文件不存在 → 返回空列表（不报错）"""
        assert injector.load_examples("no_such_skill") == []

    def test_load_examples_guards_path_traversal(self, injector):
        """非法 skill_id 被规范化，不产生路径穿越"""
        # 规范化后为扁平文件名，解析路径必须仍位于示例库目录内
        path = injector._path_for("../evil")
        assert path.parent == injector.dir.resolve()
        assert injector.load_examples("../evil") == []


# ═══════════════════════════════════════════════════════════════════
#  2. 检索选择
# ═══════════════════════════════════════════════════════════════════

class TestSelectExamples:

    def test_select_examples_by_intent(self, injector):
        """根据 intent 检索出最匹配示例"""
        _write_examples(injector, "weather", [
            _example("ex_001", "用户问昨天天气", output="昨天晴转多云。"),
            # 【Why】ex_002 原 intent "用户问今天日期" 与查询仅 2/6 bigram 重叠，
            # TF-IDF 余弦 0.2243 < min_score=0.3 被"宁缺毋滥"过滤，导致 len(selected)==1
            # 与断言 len==2 矛盾。改为 "用户问昨天日期"（4/6 重叠，score≈0.54）保留断言强度。
            _example("ex_002", "用户问昨天日期", output="今天是 7 月 13 日。"),
            _example("ex_003", "解析PDF文件", output="已解析 3 页。"),
        ])
        selected = injector.select_examples("weather", "用户问昨天天气", top_k=2)
        assert len(selected) == 2
        # 最匹配的示例排在最前
        assert selected[0].example_id == "ex_001"

    def test_select_examples_returns_empty_for_unrelated_intent(self, injector):
        """无高置信匹配时返回空列表（宁缺毋滥）"""
        _write_examples(injector, "weather", [
            _example("ex_001", "用户问昨天天气"),
            _example("ex_002", "用户问今天日期"),
            _example("ex_003", "解析PDF文件"),
        ])
        # 与所有示例均无词项交集的意图 → score=0 < min_score
        assert injector.select_examples("weather", "zzzqqqxxx") == []

    def test_select_examples_filters_low_rating(self, injector):
        """rating < min_rating 的示例不参与检索"""
        _write_examples(injector, "weather", [
            _example("ex_001", "用户问昨天天气", rating=3),
            _example("ex_002", "用户问昨天天气", rating=5),
            _example("ex_003", "用户问今天日期", rating=5),
        ])
        selected = injector.select_examples("weather", "用户问昨天天气")
        assert all(ex.rating >= 4 for ex in selected)
        assert all(ex.example_id != "ex_001" for ex in selected)


# ═══════════════════════════════════════════════════════════════════
#  3. 注入
# ═══════════════════════════════════════════════════════════════════

class TestInject:

    def test_inject_returns_empty_when_no_examples(self, injector):
        """无示例库 → has_examples=False，prompt 为空"""
        ctx = injector.inject("no_such_skill", "用户问昨天天气")
        assert ctx["has_examples"] is False
        assert ctx["prompt"] == ""
        assert ctx["examples"] == []
        assert ctx["estimated_tokens"] == 0

    def test_inject_skips_when_insufficient_examples(self, injector):
        """示例数 < 3 时不注入（数据量不足，宁缺毋滥）"""
        _write_examples(injector, "weather", [
            _example("ex_001", "用户问昨天天气"),
            _example("ex_002", "用户问今天日期"),
        ])
        ctx = injector.inject("weather", "用户问昨天天气")
        assert ctx["has_examples"] is False

    def test_inject_filters_low_rating(self, injector):
        """rating<4 的示例不注入（即使与 intent 完全匹配）"""
        _write_examples(injector, "weather", [
            _example("ex_001", "用户问昨天天气", rating=3, output="低分输出"),
            _example("ex_002", "用户问昨天天气", rating=5, output="高分输出A"),
            _example("ex_003", "用户问今天日期", rating=5, output="高分输出B"),
        ])
        ctx = injector.inject("weather", "用户问昨天天气")
        assert ctx["has_examples"] is True
        assert "高分输出A" in ctx["prompt"]
        assert "低分输出" not in ctx["prompt"]

    def test_inject_respects_token_budget(self, injector):
        """注入内容受 max_tokens 预算约束"""
        _write_examples(injector, "weather", [
            _example("ex_001", "用户问昨天天气", output="短输出。"),
            _example("ex_002", "用户问今天日期", output="短输出二。"),
            _example("ex_003", "解析PDF文件", output="短输出三。"),
        ])
        # 预算极小 → 一个示例都放不下 → 不注入
        ctx = injector.inject("weather", "用户问昨天天气", max_tokens=5)
        assert ctx["has_examples"] is False


# ═══════════════════════════════════════════════════════════════════
#  4. 采集与去重
# ═══════════════════════════════════════════════════════════════════

class TestAddExample:

    def test_add_example_dedup(self, injector):
        """自动采集时去重：(intent, input) 或 example_id 重复则不追加"""
        path = injector._path_for("weather")
        ex = _example("ex_001", intent="用户问昨天天气", input_="用户问昨天天气如何")
        assert injector.add_example("weather", ex) is True
        assert len(injector.load_examples("weather")) == 1

        # 同一 (intent, input) 重复 → 跳过
        dup = _example("ex_999", intent="用户问昨天天气", input_="用户问昨天天气如何")
        assert injector.add_example("weather", dup) is False
        assert len(injector.load_examples("weather")) == 1

        # 同一 example_id 重复 → 跳过
        same_id = _example("ex_001", intent="用户问明天天气", input_="另一个输入")
        assert injector.add_example("weather", same_id) is False
        assert len(injector.load_examples("weather")) == 1

        # 全新示例 → 追加成功
        new = _example("ex_002", intent="用户问今天日期", input_="今天几号？")
        assert injector.add_example("weather", new) is True
        assert len(injector.load_examples("weather")) == 2


# ═══════════════════════════════════════════════════════════════════
#  5. build_context 集成（Layer 2.5）
# ═══════════════════════════════════════════════════════════════════

class TestBuildContextFewShot:

    def test_build_context_includes_fewshot(self, tmp_path):
        """build_context 注入 Few-shot 段落"""
        loader = _make_mock_loader("self_reflection")
        fewshot = FewShotInjector(few_shot_dir=str(tmp_path / "skill_few_shot"))
        _write_examples(fewshot, "self_reflection", [
            _example("ex_001", "帮我复查回答", input_="复查：今天天气结论",
                     output="已复查，逻辑一致，无需修正。"),
            _example("ex_002", "自检逻辑漏洞", input_="自检：推导过程",
                     output="发现一处假设未验证，已标注。"),
            _example("ex_003", "核对事实依据", input_="核对：引用数据",
                     output="两处引用无法追溯，已剔除。"),
        ])
        injector = ContextInjector(loader=loader, few_shot_injector=fewshot)

        out = injector.build_context(
            "帮我复查回答", max_tokens=6000, skill_id="self_reflection")

        assert "技能示例（Few-shot）" in out["prompt"]
        assert out["layers"]["layer2_5_fewshot"] is True

    def test_build_context_skips_fewshot_when_no_examples(self, tmp_path):
        """无示例库时 build_context 不受影响（Few-shot 可选）"""
        loader = _make_mock_loader("self_reflection")
        fewshot = FewShotInjector(few_shot_dir=str(tmp_path / "skill_few_shot"))
        injector = ContextInjector(loader=loader, few_shot_injector=fewshot)

        out = injector.build_context(
            "帮我复查回答", max_tokens=6000, skill_id="self_reflection")

        assert "技能示例（Few-shot）" not in out["prompt"]
        assert out["layers"]["layer2_5_fewshot"] is False
        assert out["layers"]["layer2_instruction"] is True  # Layer 2 不受影响

    def test_build_context_survives_fewshot_failure(self, tmp_path, caplog):
        """Few-shot 注入抛异常时 build_context 主流程不受影响"""
        loader = _make_mock_loader("self_reflection")
        from unittest.mock import MagicMock
        broken = MagicMock()
        broken.inject.side_effect = RuntimeError("few-shot backend down")
        injector = ContextInjector(loader=loader, few_shot_injector=broken)

        with caplog.at_level(logging.WARNING, logger="agent.skills_mgmt"):
            out = injector.build_context(
                "帮我复查回答", max_tokens=6000, skill_id="self_reflection")

        assert out["prompt"]  # 主流程照常返回
        assert out["layers"]["layer2_5_fewshot"] is False
        assert any("build_context.fewshot_failed" in r.getMessage()
                   for r in caplog.records), "应记录 fewshot 失败日志"


# ═══════════════════════════════════════════════════════════════════
#  6. service.record_execution 自动采集
# ═══════════════════════════════════════════════════════════════════

class TestRecordExecutionCollection:

    @pytest.fixture
    def svc(self, tmp_path):
        from agent.skills_mgmt import SkillsMgmtService
        return SkillsMgmtService(store_path=str(tmp_path / "skills.json"))

    def test_record_execution_auto_collects_rating5_success(
            self, svc, tmp_path, monkeypatch):
        """rating=5 且 success=True 且提供输入/输出 → 自动采集示例"""
        import agent.skills_mgmt.few_shot_injector as fsi_module
        svc.create_manual({
            "id": "collect-skill", "name": "collect-skill",
            "description": "采集测试", "content": "print('x')",
            "content_type": "python", "category": "custom",
        })
        calls = []

        class _StubInjector:
            def __init__(self, *args, **kwargs):
                pass

            def add_example(self, skill_id, example):
                calls.append((skill_id, example))
                return True

        monkeypatch.setattr(fsi_module, "FewShotInjector", _StubInjector)

        svc.record_execution(
            "collect-skill", success=True, latency_ms=100,
            feedback_rating=5,
            input_text="用户问昨天天气",
            output_text="昨天晴转多云。",
        )

        assert len(calls) == 1
        skill_id, example = calls[0]
        assert skill_id == "collect-skill"
        assert example.rating == 5
        assert example.input == "用户问昨天天气"
        assert example.output == "昨天晴转多云。"

    def test_record_execution_skips_non_rating5(self, svc, monkeypatch):
        """rating != 5 或缺少输入/输出 → 不采集"""
        import agent.skills_mgmt.few_shot_injector as fsi_module
        svc.create_manual({
            "id": "skip-skill", "name": "skip-skill",
            "description": "跳过测试", "content": "print('x')",
            "content_type": "python", "category": "custom",
        })
        calls = []

        class _StubInjector:
            def __init__(self, *args, **kwargs):
                pass

            def add_example(self, skill_id, example):
                calls.append((skill_id, example))
                return True

        monkeypatch.setattr(fsi_module, "FewShotInjector", _StubInjector)

        # rating=4（非 5）→ 不采集
        svc.record_execution(
            "skip-skill", success=True, latency_ms=100, feedback_rating=4,
            input_text="x", output_text="y",
        )
        # rating=5 但失败 → 不采集
        svc.record_execution(
            "skip-skill", success=False, latency_ms=100, feedback_rating=5,
            input_text="x", output_text="y",
        )
        # rating=5 成功但缺输出 → 不采集
        svc.record_execution(
            "skip-skill", success=True, latency_ms=100, feedback_rating=5,
            input_text="x",
        )
        assert calls == []

    def test_record_execution_collection_failure_does_not_break(
            self, svc, monkeypatch, caplog):
        """采集抛异常时 record_execution 主流程不受影响"""
        import agent.skills_mgmt.few_shot_injector as fsi_module
        svc.create_manual({
            "id": "fail-skill", "name": "fail-skill",
            "description": "失败测试", "content": "print('x')",
            "content_type": "python", "category": "custom",
        })

        class _StubInjector:
            def __init__(self, *args, **kwargs):
                pass

            def add_example(self, skill_id, example):
                raise OSError("disk full")

        monkeypatch.setattr(fsi_module, "FewShotInjector", _StubInjector)

        with caplog.at_level(logging.WARNING, logger="agent.skills_mgmt"):
            svc.record_execution(
                "fail-skill", success=True, latency_ms=100, feedback_rating=5,
                input_text="x", output_text="y",
            )
        # 指标记录仍正常
        skill = svc.get("fail-skill")
        assert skill.metrics.usage_count == 1
        assert any("few-shot 示例采集失败" in r.getMessage()
                   for r in caplog.records)
