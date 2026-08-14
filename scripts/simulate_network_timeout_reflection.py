"""模拟网络超时场景，验证任务4 失败反思分支真实触发

场景：LLM 连续两次调用 `fetch_order_data` 工具，工具模拟"网络请求超时"
（ConnectionError，message 含"超时"）→ 失败反思分支应被触发：
  1. build_diagnosis 将错误分类为 network_timeout，给出 repair_hints
     （"建议重试或换备用路径，禁止无限重试"）
  2. reflector.failure_reflect 经 LLM 解析产出 root_cause/repair_actions/avoid
  3. 修复建议注入 context._hints、失败历史注入 context._failure_history
  4. 第 2 轮 _think prompt 含"失败反思记录"段（前 N 次失败摘要，强制换思路）
  5. 失败教训沉淀 lessons_db 并持久化 lessons.json

运行：python scripts/simulate_network_timeout_reflection.py
"""
import asyncio
import json
import logging
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Windows 控制台 UTF-8 输出（避免中文 gbk 编码报错）
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
from planning.models.action import ActionResult
from planning.models import Task


def fetch_order_data(order_id: str = "A001"):
    """模拟网络请求：连接 api.example.com 超时"""
    raise ConnectionError("模拟网络请求超时: 连接 api.example.com/orders 超时(>3s)")


class FakeLLM:
    """模拟 LLM：按 prompt 内容分发——思考 prompt 走思考序列、反思 prompt 返回反思 JSON。

    反思与思考共用同一 LLM 实例（Reflector 与 ReActLoop 共享），必须区分，
    否则失败反思会吞掉思考响应序列。
    """

    def __init__(self, think_responses, reflect_response):
        self._think_responses = list(think_responses)
        self._reflect_response = reflect_response
        self.prompts = []
        self.reflection_prompts = []

    async def chat(self, messages):
        content = messages[0]["content"]
        self.prompts.append(content)
        if "反思引擎" in content:  # FAILURE_REFLECTION_PROMPT 特征
            self.reflection_prompts.append(content)
            return self._reflect_response
        if self._think_responses:
            return self._think_responses.pop(0)
        return json.dumps({"reasoning": "完成", "action_type": "finish", "result": "任务结束"})


async def main() -> int:
    print("=" * 80)
    print("【模拟场景】网络超时 → 失败反思分支触发验证")
    print("=" * 80)

    # LLM 思考序列：第 1 轮调工具 → 失败；第 2 轮再调工具（收到反思注入）→ 失败；第 3 轮收尾
    think_responses = [
        json.dumps({
            "reasoning": "调用网络接口获取订单数据",
            "action_type": "tool_call",
            "action": {"tool": "fetch_order_data", "params": {"order_id": "A001"},
                       "description": "调用 fetch_order_data 获取订单 A001 数据"},
        }),
        json.dumps({
            "reasoning": "按反思建议重试，改用备用接口",
            "action_type": "tool_call",
            "action": {"tool": "fetch_order_data", "params": {"order_id": "A001"},
                       "description": "重试 fetch_order_data"},
        }),
        json.dumps({"reasoning": "任务终止", "action_type": "finish", "result": "网络不可用，放弃"}),
    ]
    # 失败反思 LLM 响应（每次失败反思都会返回同一份根因/修复建议）
    reflect_response = json.dumps({
        "root_cause": "上游 API 网关超时，网络不可达",
        "confidence": 0.8,
        "repair_actions": ["改用备用接口(api-backup.example.com)", "降低超时重试次数"],
        "avoid": ["原样重发相同请求", "无限重试"],
    })

    fake_llm = FakeLLM(think_responses, reflect_response)

    planner = type("P", (), {})()
    planner.llm = fake_llm
    planner.tool_registry = ToolRegistry()
    planner.tool_registry.register("fetch_order_data", fetch_order_data)

    with tempfile.TemporaryDirectory() as tmp_dir:
        reflector = Reflector(llm_service=fake_llm, persist_dir=tmp_dir)
        loop = ReActLoop(planner, reflector, max_iterations=5)
        context: dict = {"session": "network_timeout_demo"}
        result = await loop.run("获取订单 A001 数据", context)

        print("─" * 80)
        print("【验证结果】")
        failed_steps = [s for s in result.steps if not s.success]
        print(f"  循环终止原因       = {result.error}")
        print(f"  失败步骤数         = {len(failed_steps)}")
        print(f"  失败步骤观察       = {[s.observation[:60] for s in failed_steps]}")
        print(f"  context._hints（调试） = {context.get('_hints')}")

        # get_advice_for_task 检索验证：失败教训已入 lessons_db，应能被同任务类型检索到
        advice = reflector.get_advice_for_task("获取订单 A001 数据")
        advice_ok = (
            advice is not None
            and advice.get("related_lessons", 0) >= 1
            and any("根因" in p.get("failure", "") for p in advice.get("common_pitfalls") or [])
        )

        checks = [
            ("失败反思日志出现（>>> 调用 / <<< 返回 已打印在上方）",
             any("调用 failure_reflect" in line for line in _captured_react_log())),
            ("错误分类为 network_timeout", _classified_type(context) == "network_timeout"),
            ("修复建议注入 _hints", any("改用备用接口" in h for h in (context.get("_hints") or []))),
            ("失败历史注入 _failure_history（含根因猜测）",
             len(context.get("_failure_history") or []) >= 1
             and bool(context["_failure_history"][-1].get("guess"))),
            ("第 2 轮 prompt 含失败反思记录段",
             any("失败反思记录" in p for p in fake_llm.prompts)),
            ("第 2 轮 prompt 含第1次失败摘要",
             any("第1次失败" in p for p in fake_llm.prompts)),
            ("lessons_db 新增失败教训", len(reflector.lessons_db) >= 1),
            ("lessons.json 持久化成功",
             os.path.exists(os.path.join(tmp_dir, "lessons.json"))),
            ("get_advice_for_task 检索到失败教训（含根因）", advice_ok),
        ]
        for desc, ok in checks:
            print(f"  [{'PASS' if ok else 'FAIL'}] {desc}")

        print("─" * 80)
        print("【get_advice_for_task 检索内容】")
        if advice:
            print(json.dumps(advice, ensure_ascii=False, indent=2))
        else:
            print("  （无检索结果）")

        print("─" * 80)
        print("【第 2 轮 prompt 中的失败反思注入段】")
        for p in fake_llm.prompts:
            if "失败反思记录" in p:
                start = p.index("【失败反思记录")
                print(p[start:start + 400])
                break

        print("─" * 80)
        print("【lessons.json 持久化内容（格式校验）】")
        lessons_file = os.path.join(tmp_dir, "lessons.json")
        if os.path.exists(lessons_file):
            with open(lessons_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            for lesson in data:
                print(json.dumps(lesson, ensure_ascii=False, indent=2))
                print("   字段集合:", sorted(lesson.keys()))
            print(f"  lessons.json 共 {len(data)} 条")
        else:
            print("  （lessons.json 不存在）")

        ok = all(ok for _, ok in checks)
        print("─" * 80)
        print(f"总体: {'PASS — 网络超时失败反思闭环真实触发' if ok else 'FAIL'}")
        return 0 if ok else 1


_captured: list = []


def _captured_react_log() -> list:
    """捕获本进程已输出的 react 日志行（验证 >>> / <<< 出现）"""
    # 日志已直接打印到控制台；这里从 stdout 拿不到，改用内存记录——
    # 通过 hook logging StreamHandler 在 basicConfig 前挂一个记录 handler
    return _captured


class _MemoryHandler(logging.Handler):
    def emit(self, record):
        _captured.append(self.format(record))


# 让日志既打印控制台又进内存（验证用）
logging.getLogger().addHandler(_MemoryHandler())


def _classified_type(context: dict) -> str:
    """从 context._failure_history 取最后一次失败的 error_type"""
    hist = context.get("_failure_history") or []
    return hist[-1]["error_type"] if hist else ""


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
