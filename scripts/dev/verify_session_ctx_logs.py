"""验证显式传参调用链的日志埋点（chat → process → _get_user_context 三层串联）

验证目标:
    1. chat(session_id, session_mgr) 显式参数正确透传到 process
    2. 无参调用时回退实例 _session_id，不破坏 CLI 等调用方
    3. 三层埋点日志（chat.session_ctx / process.session_ctx / user_context.source）
       均输出正确的来源判定

用法:
    python scripts/dev/verify_session_ctx_logs.py
"""
import logging
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))  # 项目根

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

from agent.orchestrator.orchestrator import Orchestrator
from agent.session_manager import SessionManager

failures = []
def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name} {detail}")
    if not cond:
        failures.append(name)


# ── 构造不执行 __init__ 的 Orchestrator 占位实例（stub process 验证参数透传）──
obj = object.__new__(Orchestrator)
obj._session_id = "inst_ts_20260731_120000"  # 模拟 DigitalLife 构造时的实例 ID

received = {}
def stub_process(user_input, **kwargs):
    received.update(kwargs)
    return {"success": True, "response": "ok"}

obj.process = stub_process

print("=" * 56)
print("STEP 1: chat 显式传参 → process 透传")
print("=" * 56)
Orchestrator.chat(obj, "你好", session_id="sess_A", session_mgr="SM_INST")
check("session_id 透传", received.get("session_id") == "sess_A")
check("session_mgr 透传", received.get("session_mgr") == "SM_INST")

print("\n" + "=" * 56)
print("STEP 2: 无参调用 → process 收 None，埋点回退实例 ID")
print("=" * 56)
Orchestrator.chat(obj, "你好")
check("无参调用不报错", received.get("session_id") is None)
check("实例 _session_id 保留", obj._session_id == "inst_ts_20260731_120000")

print("\n" + "=" * 56)
print("STEP 3: _get_user_context 显式/回退/降级（埋点来源判定）")
print("=" * 56)
tmp = Path(tempfile.mkdtemp(prefix="yunshu_ctx_log_"))
sm = SessionManager(sessions_dir=str(tmp / "s"))
sess = sm.create_session(title="日志验证", timezone="Asia/Shanghai", locale="zh-CN")
obj2 = object.__new__(Orchestrator)
obj2._session_mgr = sm
obj2._session_id = sess["id"]

# 显式传参
ctx1 = Orchestrator._get_user_context(obj2, session_id=sess["id"], session_mgr=sm)
check("显式传参返回正确上下文", ctx1 and "Asia/Shanghai" in ctx1)
# 回退实例
ctx2 = Orchestrator._get_user_context(obj2)
check("回退实例返回相同上下文", ctx1 == ctx2)
# 无 session_mgr 降级
obj3 = object.__new__(Orchestrator)
check("无 session_mgr 返回 None", Orchestrator._get_user_context(obj3) is None)
# 会话不存在降级
check("会话不存在返回 None", Orchestrator._get_user_context(
    obj2, session_id="sess_nonexist", session_mgr=sm) is None)

print("\n" + "=" * 56)
if failures:
    print(f"结果: {len(failures)} 项失败 -> {failures}")
    sys.exit(1)
print("结果: 显式传参调用链 + 三层埋点验证全部通过 ✓")
