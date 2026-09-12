"""TASK-S9-01 真机复验脚本：连续两次不同提问的 tool_steps / reasoning / response 对比。

用途（两条路径，均从项目根目录执行）：

1. **服务进程外复验**（本脚本）：直接构造 ``DigitalLife`` 并连续 ``chat()``，
   打印每轮的 ``tool_steps`` / ``reasoning`` / ``response`` 摘要；
2. **服务进程内复验**：见 ``scripts/dev/s901_live_check.ps1``（POST /api/chat）。

用法::

    python scripts/dev/s901_turn_state_repro.py

注意：
- 本脚本**只读**运行时状态，不写任何台账；两轮使用**不同 session_id**；
- 会真实外呼 LLM（凭证已修复），单轮约 3–15s，整轮通常 < 60s。
"""

import io
import json
import os
import sys

# 允许从任意 cwd 运行（以 cwd 为项目根解析 import）
_ROOT = os.path.abspath(os.getcwd())
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

if sys.platform == "win32" and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

QUESTIONS = [
    ("s901-probe-A", "帮我列出当前工作目录下的文件"),
    ("s901-probe-B", "2 加 3 等于多少？只回答数字"),
    # 判据 3：回到会话 A 再问一句（本轮无工具），不得拿到会话 B 的 state，也不得复用 A 上一轮
    ("s901-probe-A", "1 加 1 等于几？只回答数字"),
]


def _turn_state(dl, session_id):
    """兼容读取：修复后走 last_turn_state，修复前读全局属性。"""
    if hasattr(dl, "last_turn_state"):
        try:
            return dl.last_turn_state(session_id), "last_turn_state(session_id)"
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}, "last_turn_state(error)"
    return {
        "tool_steps": getattr(dl, "_last_tool_steps", None),
        "reasoning": getattr(dl, "_last_reasoning", None),
    }, "全局实例属性(修复前)"


def main() -> int:
    from config import Config
    from agent import DigitalLife

    # 与服务进程一致的 .env → os.environ 引导（lifecycle_manager 经 os.getenv 读 LLM_API_KEY）
    try:
        from agent.env_config_manager import get_env_config_manager
        get_env_config_manager().reload()
        print("[init] .env 已加载到 os.environ")
    except Exception as _e:  # noqa: BLE001
        print("[init] .env 引导失败（不影响结构判据，仅影响 LLM 可用性）: %r" % (_e,))
    print("[init] LLM_API_KEY 已注入: %s" % bool(os.environ.get("LLM_API_KEY")))

    print("=" * 78)
    print("[S9-01 真机复验] 连续两次不同提问 —— tool_steps / reasoning / response")
    print("=" * 78)
    dl = DigitalLife(Config().merged)
    dl.start()  # 与 app_server.py:499 一致：未 start 时 process() 直接返回"我还没有被唤醒"
    print("[init] DigitalLife 就绪（已 start）")
    print("[init] _v2_lifetrace=%s  _trace_recorder=%s  _tool_calling_service=%s"
          % (getattr(dl, "_v2_lifetrace", None) is not None,
             getattr(dl, "_trace_recorder", None) is not None,
             getattr(dl, "_tool_calling_service", None) is not None))

    snapshots = []
    for session_id, question in QUESTIONS:
        print("-" * 78)
        print("[Q] session=%s  question=%s" % (session_id, question))
        try:
            response = dl.chat(question, session_id=session_id)
        except Exception as e:  # noqa: BLE001
            print("[ERR] chat 抛出异常: %r" % (e,))
            response = ""
        state, source = _turn_state(dl, session_id)
        steps = state.get("tool_steps") or []
        reasoning = state.get("reasoning")
        text = response or ""
        print("  [source]      %s" % source)
        print("  [tool_steps]  n=%d  %s" % (
            len(steps), json.dumps(steps, ensure_ascii=False)[:400]))
        print("  [reasoning]   %r" % (reasoning,))
        print("  [response]    len=%d  head=%r" % (len(text), text[:200]))
        snapshots.append({
            "session_id": session_id,
            "question": question,
            "tool_steps": steps,
            "reasoning": reasoning,
            "response": text,
        })

    print("=" * 78)
    # 原始证据落盘（报告片段可原样复现）
    try:
        _out = os.path.join(_ROOT, ".s901_evidence", "probe_snapshots.json")
        os.makedirs(os.path.dirname(_out), exist_ok=True)
        with open(_out, "w", encoding="utf-8") as _f:
            json.dump(snapshots, _f, ensure_ascii=False, indent=2)
        print("[evidence] 原始快照已落盘: %s" % _out)
    except Exception as _e:  # noqa: BLE001
        print("[evidence] 落盘失败: %r" % (_e,))
    a, b = snapshots[0], snapshots[1]
    c = snapshots[2] if len(snapshots) > 2 else None
    same_steps = a["tool_steps"] == b["tool_steps"]
    same_reasoning = a["reasoning"] == b["reasoning"]
    print("[判据1] tool_steps 两轮相等? %s" % same_steps)
    print("[判据1] reasoning  两轮相等? %s" % same_reasoning)
    print("[判据1] 第二轮本轮无内容时是否为空? tool_steps=%r reasoning=%r"
          % (b["tool_steps"], b["reasoning"]))
    print("[判据2] 第二轮响应含 '5'? %s" % ("5" in b["response"]))
    print("[判据2] 第二轮响应是技能文档? %s"
          % ("# self_reflection" in b["response"] or "适用场景" in b["response"]))
    print("[判据2] 第二轮响应是工具原始 JSON? %s"
          % (("'ok': True" in b["response"]) or ("abs_path" in b["response"])))
    print("[判据4] 第一轮 tool_steps 非空(工具真实执行并落账)? %s"
          % (len(a["tool_steps"]) > 0))
    if c is not None:
        print("[判据3] 回到会话 A 的第二问（本轮无工具）: tool_steps=%r reasoning=%r"
              % (c["tool_steps"], c["reasoning"]))
        print("[判据3] 是否复用 A 上一轮 tool_steps? %s" % (c["tool_steps"] == a["tool_steps"]))
        print("[判据3] 是否污染会话 B? B.tool_steps=%r" % (b["tool_steps"],))
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
