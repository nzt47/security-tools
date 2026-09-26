# -*- coding: utf-8 -*-
"""F11 · 工作流学习子系统最小健全性用例（真签名 / 去重 / 异常输入不落库）

对应任务卡 F11 的三条硬伤与要求：
  1. `task_signature` 必须是**实词签名**，而不是"输入切碎后的单字清单"；
  2. 同任务重复出现 → 只 1 条 + 计数累加（且跨会话样本数不因此丢失）；
  3. 退化/异常输入不产生垃圾条目（既不新建，也不写盘）。

实测背景（证据见 docs/audit_skill_governance/F11.md §1）：
  - 旧口径产物 `python|件|所|文|有|目|统|计|里|项` = 单字清单；
  - 同一输入"帮我列出当前工作目录下的文件"产生过 3 条**签名相同**的条目；
  - 运行期 96 次 `ping` 请求把"1 步草稿"写进仓库（每条一次）。

【数据隔离】全部用例只写 tmp_path 下的仓库文件，**不触碰**
`data/learned_workflows.json`（该文件是 tracked 运行期数据，见 F11 报告 §5）。
"""

from __future__ import annotations

import json

import pytest

from agent.workflow_learning import admission
from agent.workflow_learning.exceptions import ErrorCode, WorkflowLearningError
from agent.workflow_learning.learner import (
    SIGNATURE_FALLBACK,
    canonical_signature,
    signature_tokens,
)
from agent.workflow_learning.models import (
    LearnedWorkflow,
    LearningRecord,
    WorkflowStatus,
    WorkflowStep,
)
from agent.workflow_learning.repository import WorkflowRepository
from agent.workflow_learning.service import WorkflowLearningService

#: `FINDINGS_DURING_IMPL.md` F11 节记录的三个真实输入（及其旧签名）
TASK_PYTHON = "统计项目里所有 Python 文件的行数并保存报告"
OLD_SIG_PYTHON = "python|件|所|文|有|目|统|计|里|项"
TASK_ZIP = "把代码仓库打包成 zip 压缩包"
TASK_JSON = "读取 JSON 配置文件并转换为 YAML 格式"
TASK_LIST = "帮我列出当前工作目录下的文件"

#: 退化输入：不含任何实词（停用字/标点/单个汉字）
DEGENERATE_INPUTS = ["", "   ", "???", "!!!", "好", "你好"]


def _tool_calls(n: int = 2) -> list:
    return [{"name": "tool_%d" % i, "params": {}, "success": True}
            for i in range(n)]


def _record(user_input: str, *, session_id: str = "sess-A", n_steps: int = 2,
            success: bool = True) -> LearningRecord:
    return LearningRecord(session_id=session_id, user_input=user_input,
                          tool_calls=_tool_calls(n_steps), success=success)


@pytest.fixture
def svc(tmp_path) -> WorkflowLearningService:
    """独立临时仓库的学习服务（绝不写真实 data/learned_workflows.json）"""
    s = WorkflowLearningService(repo_path=str(tmp_path / "wf.json"))
    s.set_tool_executor(lambda tool, params: {"ok": True, "tool": tool})
    return s


# ═══════════════════════════════════════════════════════════════════
#  1. 真签名口径
# ═══════════════════════════════════════════════════════════════════

class TestTaskSignature:

    def test_签名不再是单字清单(self):
        """每个 token 至少 2 个有效字符（与 admission.MIN_TRIGGER_CHARS 同源）"""
        for task in (TASK_PYTHON, TASK_ZIP, TASK_JSON, TASK_LIST):
            toks = signature_tokens(task)
            assert toks, "任务输入必须产出实词: %r" % task
            for t in toks:
                assert admission.is_discriminative_trigger(t), (
                    "签名 token 无区分度（单字/空）: %r in %r" % (t, task))

    def test_旧口径的单字签名不再出现(self):
        sig = canonical_signature(TASK_PYTHON)
        assert sig != OLD_SIG_PYTHON
        # 单字（"件"/"所"/…）绝不出现在新签名里
        assert "件" not in sig.split("|")
        # 英文整词与中文词块都在（前者证明按词而非按字，后者证明中文成词）
        toks = sig.split("|")
        assert "python" in toks
        assert "文件" in toks
        assert sum(1 for t in toks if len(t) >= 2) == len(toks)

    def test_不同任务得到不同签名(self):
        sigs = {canonical_signature(t)
                for t in (TASK_PYTHON, TASK_ZIP, TASK_JSON, TASK_LIST)}
        assert len(sigs) == 4, "语义不同的任务不得共享签名: %s" % sigs

    def test_签名是输入的纯函数(self):
        """大小写/首尾空白不同 → 同一签名；重复求值恒定（去重键必须稳定）"""
        assert canonical_signature(TASK_PYTHON) == \
            canonical_signature("  " + TASK_PYTHON.lower() + "  ")
        assert canonical_signature(TASK_PYTHON) == canonical_signature(TASK_PYTHON)

    def test_无实词输入落到退化标记(self):
        for bad in DEGENERATE_INPUTS:
            assert canonical_signature(bad) == SIGNATURE_FALLBACK, (
                "退化输入不该产出签名: %r" % bad)
        assert canonical_signature(TASK_PYTHON) != SIGNATURE_FALLBACK


# ═══════════════════════════════════════════════════════════════════
#  2. 同任务去重（累加计数而非新建）
# ═══════════════════════════════════════════════════════════════════

class TestSameTaskDedup:

    def test_同样输入两次只一条且计数为2(self, svc):
        wf1 = svc.learn_from_interaction(_record(TASK_PYTHON))
        wf2 = svc.learn_from_interaction(_record(TASK_PYTHON))
        assert svc.repo.count() == 1
        assert wf1.id == wf2.id
        assert wf2.observed_count == 2
        assert wf2.task_signature == wf1.task_signature

    def test_重复观察会累加并落盘(self, svc, tmp_path):
        for _ in range(5):
            svc.learn_from_interaction(_record(TASK_JSON))
        assert svc.repo.count() == 1
        on_disk = json.load(open(tmp_path / "wf.json", encoding="utf-8"))
        assert len(on_disk) == 1
        entry = list(on_disk.values())[0]
        assert entry["observed_count"] == 5

    def test_同型风暴只留一条(self, svc):
        """复刻实测：96 次同类请求（B2 探针的 `ping` 风暴）→ 1 条 + 计数 96"""
        for _ in range(96):
            svc.learn_from_interaction(_record("ping", n_steps=1))
        entries = svc.list_workflows()
        assert len(entries) == 1
        assert entries[0].observed_count == 96
        # 单步 → 仍按准入判为草稿（本卡不改准入政策）
        assert entries[0].status == WorkflowStatus.DRAFT.value

    def test_跨会话样本数不因去重而丢失(self, svc):
        """去重后仍要能算出"同签名出现在 2 个会话"（自动升格门槛依赖它）"""
        wf = svc.learn_from_interaction(_record(TASK_PYTHON, session_id="sess-A"))
        assert svc.repo.count_distinct_sessions(wf.task_signature) == 1
        wf2 = svc.learn_from_interaction(_record(TASK_PYTHON, session_id="sess-B"))
        assert svc.repo.count() == 1
        assert wf2.observed_count == 2
        assert svc.repo.count_distinct_sessions(wf.task_signature) == 2, (
            "去重不得把跨会话支持数焊死在 1：source_sessions 必须累积")
        assert wf2.source_sessions == ["sess-A", "sess-B"]

    def test_不同任务各自成条(self, svc):
        svc.learn_from_interaction(_record(TASK_PYTHON))
        svc.learn_from_interaction(_record(TASK_ZIP))
        assert svc.repo.count() == 2
        assert len({w.task_signature for w in svc.list_workflows()}) == 2

    def test_合并不得复活归档条目或覆盖步骤(self, svc):
        repo = svc.repo
        legacy = LearnedWorkflow(
            id="legacy-t1", name="存量",
            task_signature=OLD_SIG_PYTHON,   # 旧"字符清单"签名
            trigger_patterns=["python"],
            steps=[WorkflowStep(step_id="step_1", tool_name="search_files"),
                   WorkflowStep(step_id="step_2", tool_name="write_file")],
            source_session_id="sess-old",
            source_user_input=TASK_PYTHON,
            status=WorkflowStatus.ARCHIVED,
            success_count=7, confidence=0.9,
        )
        repo.upsert(legacy)
        wf = svc.learn_from_interaction(_record(TASK_PYTHON))
        assert repo.count() == 1, "同任务不得因为旧签名口径不同而新建条目"
        assert wf.id == "legacy-t1"
        assert wf.observed_count == 2
        assert wf.status == WorkflowStatus.ARCHIVED.value, "合并不得复活已归档条目"
        assert wf.success_count == 7, "合并不得覆盖执行统计"
        assert [s.tool_name for s in wf.steps] == ["search_files", "write_file"]
        assert wf.task_signature == canonical_signature(TASK_PYTHON), (
            "合并后签名应对齐到规范口径，否则跨会话读数看不到这条")
        assert repo.count_distinct_sessions(wf.task_signature) == 2


# ═══════════════════════════════════════════════════════════════════
#  3. 异常/退化输入不产生垃圾条目
# ═══════════════════════════════════════════════════════════════════

class TestNoJunkEntries:

    @pytest.mark.parametrize("bad", DEGENERATE_INPUTS)
    def test_无实词输入被拒绝且不落盘(self, svc, bad):
        with pytest.raises(WorkflowLearningError) as exc:
            svc.learn_from_interaction(_record(bad))
        assert exc.value.code == ErrorCode.LEARN_FAILED
        assert svc.repo.count() == 0

    def test_无工具调用不落盘(self, svc):
        rec = LearningRecord(session_id="s", user_input=TASK_PYTHON,
                             tool_calls=[], success=True)
        with pytest.raises(WorkflowLearningError):
            svc.learn_from_interaction(rec)
        assert svc.repo.count() == 0

    def test_失败交互不落盘(self, svc):
        with pytest.raises(WorkflowLearningError):
            svc.learn_from_interaction(_record(TASK_PYTHON, success=False))
        assert svc.repo.count() == 0

    def test_拒绝可被上层识别(self, svc):
        """拒绝必须是**可判定**的（HTTP 层据此回 400，编排层据此跳过）"""
        try:
            svc.learn_from_interaction(_record("???"))
        except WorkflowLearningError as e:
            payload = e.to_dict()
            assert payload["ok"] is False
            assert payload["code"] == ErrorCode.LEARN_FAILED
            assert "实词" in payload["error"]
        else:  # pragma: no cover - 拒绝失效时给出明确失败原因
            pytest.fail("退化输入未被拒绝")

