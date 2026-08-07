"""验证会话元数据 → 提示词注入 全链路（Mock 请求驱动，无真实 LLM 调用）

链路模拟（与 routes_chat 运行时一致）:
    HTTP 请求头 → _extract_session_meta(request) → SessionManager.create_session(**meta)
    → 路由同步 Yunshu._session_id/_session_mgr → Orchestrator._get_user_context()
    → PersonaInjector.build_system_prompt(user_context=...) 注入

用法:
    python scripts/dev/verify_prompt_injection_mock.py
"""
import logging
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))  # 项目根

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("verify_prompt_injection")

failures = []
def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name} {detail}")
    if not cond:
        failures.append(name)


# ═══════ 1. Mock 请求构造（带时区/设备/语言）═══════
print("=" * 56)
print("STEP 1: 构造带元数据的 Mock 请求")
print("=" * 56)

from flask import Flask, request
from agent.server_routes.routes_sessions import _extract_session_meta

app = Flask(__name__)
mock_headers = {
    "X-Timezone": "Asia/Shanghai",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile Safari",
}
with app.test_request_context(
    path="/api/sessions", method="POST", headers=mock_headers, json={"title": "Mock会话"}
):
    meta = _extract_session_meta(request)
    print(f"  → 提取元数据: {meta}")
    check("timezone 从 X-Timezone 提取", meta.get("timezone") == "Asia/Shanghai")
    check("locale 从 Accept-Language 提取", meta.get("locale") == "zh-CN")
    check("device_type 从 UA 启发式=mobile", meta.get("device_type") == "mobile")

# 反例：桌面 UA
with app.test_request_context(path="/", method="POST", headers={
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0",
}):
    meta2 = _extract_session_meta(request)
    print(f"  → 桌面 UA 元数据: {meta2}")
    check("desktop UA → device_type=desktop", meta2.get("device_type") == "desktop")
    check("英文环境 → locale=en-US", meta2.get("locale") == "en-US")
    check("无 timezone 时返回 None", meta2.get("timezone") is None)


# ═══════ 2. 会话创建 + 元数据持久化 ═══════
print("\n" + "=" * 56)
print("STEP 2: SessionManager 创建会话并持久化元数据")
print("=" * 56)

from agent.session_manager import SessionManager

tmp = Path(tempfile.mkdtemp(prefix="yunshu_mock_inject_"))
sm = SessionManager(sessions_dir=str(tmp / "sessions"))
sess = sm.create_session(title="Mock会话", **meta)
print(f"  → 会话: {sess['id']}")
meta_loaded = sm.get_session_metadata(sess["id"])
print(f"  → meta.json 读回: timezone={meta_loaded.get('timezone')}, "
      f"device={meta_loaded.get('device_type')}, locale={meta_loaded.get('locale')}")
check("meta.json 持久化 timezone", meta_loaded.get("timezone") == "Asia/Shanghai")
check("meta.json 持久化 device_type", meta_loaded.get("device_type") == "mobile")
check("meta.json 持久化 locale", meta_loaded.get("locale") == "zh-CN")


# ═══════ 3. 模拟路由同步 + Orchestrator._get_user_context ═══════
print("\n" + "=" * 56)
print("STEP 3: 同步 _session_id/_session_mgr → _get_user_context()")
print("=" * 56)

from agent.orchestrator.orchestrator import Orchestrator


class DummyHost:  # 模拟 DigitalLife（宿主类，拥有 _session_id/_session_mgr）
    def __init__(self, session_mgr, session_id):
        self._session_mgr = session_mgr
        self._session_id = session_id


host = DummyHost(session_mgr=sm, session_id=sess["id"])
# 修复后链路：chat(session_id=..., session_mgr=...) 显式传参（并发安全）
user_context = Orchestrator._get_user_context(
    host, session_id=sess["id"], session_mgr=sm,
)
print(f"  → user_context(显式传参) = {user_context!r}")
check("user_context 生成成功", user_context is not None)
check("含时区", "Asia/Shanghai" in (user_context or ""))
check("含设备", "mobile" in (user_context or ""))
check("含语言", "zh-CN" in (user_context or ""))

# 向后兼容：无参调用仍回退实例全局 _session_id/_session_mgr（CLI 等未接入方）
user_context_fb = Orchestrator._get_user_context(host)
print(f"  → user_context(回退实例属性) = {user_context_fb!r}")
check("向后兼容：无参回退实例属性", user_context_fb == user_context)

# 降级场景：无 session_mgr / 无 session_id / 会话不存在
host_no_mgr = DummyHost(session_mgr=None, session_id=sess["id"])
check("无 _session_mgr 降级返回 None", Orchestrator._get_user_context(host_no_mgr) is None)
host_no_id = DummyHost(session_mgr=sm, session_id=None)
check("无 _session_id 降级返回 None", Orchestrator._get_user_context(host_no_id) is None)
host_bad_id = DummyHost(session_mgr=sm, session_id="sess_nonexist")
check("会话不存在降级返回 None", Orchestrator._get_user_context(host_bad_id) is None)


# ═══════ 4. PersonaInjector 注入验证 ═══════
print("\n" + "=" * 56)
print("STEP 4: build_system_prompt 注入 user_context")
print("=" * 56)

from persona.persona_model_enhanced import PersonaModel
from persona.persona_injector import PersonaInjector

pi = PersonaInjector(PersonaModel())
system_prompt = pi.build_system_prompt(
    body_status="当前 CPU 温度 45°C，运行正常。",
    memory_context="用户最近在讨论项目重构。",
    tool_status="可用工具: 记忆、搜索、代码",
    user_context=user_context,
)
print(f"  → 注入位置: '# 用户上下文' 在动态区")
idx = system_prompt.find("# 用户上下文")
check("注入区块存在", idx > 0)
print(f"\n----- 系统提示词（# 用户上下文 区块）-----")
print(system_prompt[idx:idx + 200])
print("-----------------------------------------")
check("说话风格信息（时区）入提示词", "用户时区: Asia/Shanghai" in system_prompt)

# 不传 user_context → 不注入（向后兼容）
sp2 = pi.build_system_prompt(body_status="x")
check("无 user_context 不注入", "# 用户上下文" not in sp2)

# ═══════ 5. 完整链路演示（Mock 请求 → 注入后提示词片段）═══════
print("\n" + "=" * 56)
print("STEP 5: 端到端链路总览")
print("=" * 56)
print(f"  Mock 请求头:")
print(f"    X-Timezone:      {mock_headers['X-Timezone']}")
print(f"    Accept-Language: {mock_headers['Accept-Language']}")
print(f"    User-Agent:      {mock_headers['User-Agent'][:50]}...")
print(f"  ↓ _extract_session_meta")
print(f"    {meta}")
print(f"  ↓ create_session / _session_id 同步")
print(f"    session_id = {sess['id']}")
print(f"  ↓ _get_user_context()")
print(f"    {user_context!r}")
print(f"  ↓ build_system_prompt(user_context=...)")
print(f"    '# 用户上下文' 已注入 → {len(system_prompt)} chars system prompt")

print(f"\n{'='*56}")
if failures:
    print(f"结果: {len(failures)} 项失败 -> {failures}")
    sys.exit(1)
print("结果: 提示词注入验证全部通过 ✓")
