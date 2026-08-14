"""模拟 LLM 连续 2 轮输出相同 root_cause，验证 reflection_retries 轮数兜底生效

背景：设计文档步骤 4 曾建议"LLM 连续 2 轮输出相同 root_cause 时终止反思"，
评估结论暂不实现（同根因去重），当前收敛由 reflection_retries（默认 2）轮数上限兜底。

本脚本构造极端场景：
- 工具 fetch_order_data 永远抛 ConnectionError（网络超时）
- LLM 每次失败反思都返回【完全相同】的 root_cause（"上游 API 网关超时，网络不可达"）

验证目标（当前实现的正确行为）：
  1. failure_reflect 实际调用次数 == reflection_retries（=2），不受"相同 root_cause"影响
  2. 第 3 次失败时反思轮数达上限 → 终止反思并升级（"失败反思轮数达上限"日志）
  3. 反思超限后不再调用 failure_reflect（>>> 调用日志恰为 2 次）
  4. 反思不阻断主循环：任务正常收尾（max_iterations 兜底）

运行：python scripts/simulate_same_root_cause_reflection.py
"""
import asyncio
import json
import logging
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

from planning.executor import ToolRegistry
from planning.react import ReActLoop
from planning.reflector import Reflector


def fetch_order_data(order_id: str = "A001"):
    """模拟网络请求：永远超时（构造持续失败场景）"""
    raise ConnectionError("模拟网络请求超时: 连接 api.example.com/orders 超时(>3s)")


# 每次失败反思都返回【完全相同】的 root_cause——验证同根因场景下收敛仍由轮数兜底
SAME_ROOT_CAUSE = "上游 API 网关超时，网络不可达"
REFLECT_RESPONSE = json.dumps({
    "root_cause": SAME_ROOT_CAUSE,
    "confidence": 0.8,
    "repair_actions": ["改用备用接口(api-backup.example.com)"],
    "avoid": ["原样重发相同请求"],
}, ensure_ascii=False)


class FakeLLM:
    """模拟 LLM：思考 prompt 走思考序列，反思 prompt 返回固定的相同 root_cause"""

    def __init__(self, think_responses):
        self._think_responses = list(think_responses)
        self.reflection_prompts = []
        self.reflect_responses = []  # LLM 反思实际返回的响应（验证 root_cause 相同）

    async def chat(self, messages):
        content = messages[0]["content"]
        if "反思引擎" in content:
            self.reflection_prompts.append(content)
            self.reflect_responses.append(REFLECT_RESPONSE)
            return REFLECT_RESPONSE
        if self._think_responses:
            return self._think_responses.pop(0)
        return json.dumps({"reasoning": "完成", "action_type": "finish", "result": "任务结束"})


async def main() -> int:
    print("=" * 80)
    print("【模拟场景】LLM 连续输出相同 root_cause → reflection_retries 轮数兜底验证")
    print("=" * 80)

    # 思考序列：3 次调用工具（前 2 次触发失败反思，第 3 次失败时反思超限终止）+ finish 收尾
    # max_iterations=5 保证任务在反思终止后仍能正常收尾（验证反思不阻断主循环）
    think_responses = [
        json.dumps({"reasoning": f"第{i}次调用网络接口", "action_type": "tool_call",
                    "action": {"tool": "fetch_order_data", "params": {"order_id": "A001"},
                               "description": f"调用 fetch_order_data（第{i}次）"}})
        for i in range(1, 4)
    ]
    think_responses.append(
        json.dumps({"reasoning": "任务终止", "action_type": "finish", "result": "网络不可用，放弃"})
    )

    fake_llm = FakeLLM(think_responses)

    planner = type("P", (), {})()
    planner.llm = fake_llm
    planner.tool_registry = ToolRegistry()
    planner.tool_registry.register("fetch_order_data", fetch_order_data)

    with tempfile.TemporaryDirectory() as tmp_dir:
        reflector = Reflector(llm_service=fake_llm, persist_dir=tmp_dir)
        loop = ReActLoop(planner, reflector, max_iterations=5)
        context: dict = {"session": "same_root_cause_demo"}
        result = await loop.run("获取订单 A001 数据", context)

        print("─" * 80)
        print("【运行观测】")
        print(f"  循环终止原因        = {result.error}")
        print(f"  失败步骤数          = {len([s for s in result.steps if not s.success])}")
        print(f"  reflection_retries  = {loop.reflection_retries}")
        print(f"  LLM 反思输出相同 root_cause 次数 = {len(fake_llm.reflection_prompts)}")

        # 从内存日志统计 failure_reflect 实际调用次数（>>> 调用日志行数）
        reflect_calls = sum(1 for line in _captured if "调用 failure_reflect（输入）" in line)
        # 反思超限后的后续动作：任务5 快照还原重试 或 既有升级路径，二者都证明轮数上限已触发
        limit_logged = any("失败反思轮数达上限" in line for line in _captured)
        restored_logged = any("snapshot_restored" in line or "已回滚到最近快照" in line
                              for line in _captured)

        checks = [
            ("反思输出 root_cause 全部相同（场景构造成功）",
             len(fake_llm.reflect_responses) == 2
             and all(json.loads(r)["root_cause"] == SAME_ROOT_CAUSE
                     for r in fake_llm.reflect_responses)),
            (f"failure_reflect 实际调用次数 == reflection_retries({loop.reflection_retries})",
             reflect_calls == loop.reflection_retries == 2),
            ("反思超限后不再反思（第 3 次失败无 failure_reflect 调用）",
             reflect_calls == 2 and len(fake_llm.reflection_prompts) == 2),
            ("反思轮数达上限 → 触发后续收敛动作（快照还原重试 或 升级路径）",
             limit_logged or restored_logged),
            ("反思不阻断主循环：任务正常收尾", len(result.steps) >= 1),
            ("lessons_db 沉淀失败教训", len(reflector.lessons_db) >= 1),
            ("lessons.json 持久化成功",
             os.path.exists(os.path.join(tmp_dir, "lessons.json"))),
        ]
        print("─" * 80)
        print("【验证结果】")
        for desc, ok in checks:
            print(f"  [{'PASS' if ok else 'FAIL'}] {desc}")

        ok = all(ok for _, ok in checks)
        print("─" * 80)
        print(f"总体: {'PASS — 相同 root_cause 下 reflection_retries 轮数兜底生效' if ok else 'FAIL'}")
        return 0 if ok else 1


_captured: list = []


class _MemoryHandler(logging.Handler):
    def emit(self, record):
        _captured.append(self.format(record))


logging.getLogger().addHandler(_MemoryHandler())


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
