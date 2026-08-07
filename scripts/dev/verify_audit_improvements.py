"""验证用户档案卡 + 会话元数据 + 内存窗口 改进（临时目录，不污染真实数据）"""
import asyncio
import json
import os
import sys
import tempfile
import shutil
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="yunshu_audit_verify_"))
print(f"[TMP] {TMP}")

failures = []

def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name} {detail}")
    if not cond:
        failures.append(name)

# ── 1. LongTermMemory save_profile/get_profile upsert ──
print("\n=== 1. LongTermMemory 用户档案 upsert ===")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))  # 项目根
from agent.memory.long_term_memory import LongTermMemory

async def test_ltm():
    db = TMP / "long_term.db"
    ltm = LongTermMemory(db_path=str(db))
    # 首次建档
    ok = await ltm.save_profile(
        user_id="u_001",
        name="张三",
        occupation="软件工程师",
        core_goals=["升职", "学习AI"],
        preferences={"theme": "dark", "language": "中文"},
        timezone="Asia/Shanghai",
        device_type="desktop",
        locale="zh-CN",
    )
    check("save_profile 首次建档", ok)
    p1 = await ltm.get_profile("u_001")
    check("get_profile 字段完整", p1 and p1["name"] == "张三" and p1["occupation"] == "软件工程师")
    check("core_goals JSON 解析", p1 and p1["core_goals"] == ["升职", "学习AI"])
    check("preferences JSON 解析", p1 and p1["preferences"] == {"theme": "dark", "language": "中文"})
    check("timezone/device/locale", p1 and p1["timezone"] == "Asia/Shanghai" and p1["device_type"] == "desktop" and p1["locale"] == "zh-CN")

    # 更新部分字段：只更新姓名，其余保持
    await ltm.save_profile(user_id="u_001", name="李四")
    p2 = await ltm.get_profile("u_001")
    check("upsert 覆盖姓名", p2 and p2["name"] == "李四")
    check("upsert 保留未更新字段(occupation)", p2 and p2["occupation"] == "软件工程师")
    check("upsert 保留未更新字段(timezone)", p2 and p2["timezone"] == "Asia/Shanghai")

    # 唯一性：同 user_id 只有一条记录
    rows = ltm.list_profiles()
    check("同 user_id 仅一条记录（事实唯一性）", len(rows) == 1)
    check("list_profiles 含新列", rows and all("timezone" in dict(r) for r in rows))

    # 不存在档案
    p3 = await ltm.get_profile("u_nonexist")
    check("get_profile 不存在返回 None", p3 is None)

    # 表结构三列存在
    import sqlite3
    conn = sqlite3.connect(str(db))
    cols = [c[1] for c in conn.execute("PRAGMA table_info(user_profile)").fetchall()]
    conn.close()
    check("user_profile 含 timezone/device_type/locale 列",
          {"timezone", "device_type", "locale"} <= set(cols))

asyncio.run(test_ltm())

# ── 2. SessionManager 会话元数据 ──
print("\n=== 2. SessionManager 会话元数据 ===")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent.session_manager import SessionManager

sm = SessionManager(sessions_dir=str(TMP / "sessions"))
sess = sm.create_session(title="测试", timezone="Asia/Shanghai", device_type="mobile", locale="zh-CN")
check("create_session 返回元数据", sess.get("timezone") == "Asia/Shanghai" and sess.get("device_type") == "mobile")
meta = sm.get_session_metadata(sess["id"])
check("get_session_metadata 读 meta.json", meta and meta.get("timezone") == "Asia/Shanghai" and meta.get("locale") == "zh-CN")
ok = sm.update_session_metadata(sess["id"], device_type="desktop")
meta2 = sm.get_session_metadata(sess["id"])
check("update_session_metadata 更新", ok and meta2.get("device_type") == "desktop")

# 向后兼容：旧式调用
sess2 = sm.create_session("旧式")
check("create_session 向后兼容（无元数据）", sess2.get("timezone") is None)

# ── 3. MemoryManager 内存滑动窗口 ──
print("\n=== 3. MemoryManager 内存滑动窗口 ===")
from memory.memory_manager import MemoryManager

mm = MemoryManager({"data_dir": str(TMP / "memory_data"), "llm": {}})
mm.add_message("user", "早上好")
mm.add_message("assistant", "早上好，有什么可以帮你？")
mm.add_message("user", "帮我看看项目")
ctx = mm.get_context(token_limit=100000)
check("get_context 含窗口消息", any(m.get("role") == "user" and "早上好" in m.get("content", "") for m in ctx))
check("窗口消息数=3", len(mm._message_window) == 3)
mm.clear_memory()
check("clear_memory 清空窗口", len(mm._message_window) == 0)

# ── 4. PersonaInjector user_context 注入 ──
print("\n=== 4. PersonaInjector user_context 注入 ===")
from persona.persona_model_enhanced import PersonaModel
from persona.persona_injector import PersonaInjector

pi = PersonaInjector(PersonaModel())
sp = pi.build_system_prompt(user_context="用户时区: Asia/Shanghai；设备类型: mobile；语言环境: zh-CN")
check("注入 user_context", "# 用户上下文" in sp and "Asia/Shanghai" in sp and "mobile" in sp)
sp_none = pi.build_system_prompt()
check("无 user_context 不注入", "# 用户上下文" not in sp_none)

# ── 5. 迁移脚本 dry-run + 执行 ──
print("\n=== 5. 迁移脚本 ===")
import subprocess
r1 = subprocess.run(
    [sys.executable, "scripts/migrate_add_user_profile.py", "--db-path", str(TMP / "migrate.db"), "--dry-run"],
    capture_output=True, text=True,
)
check("迁移脚本 dry-run 退出码 0", r1.returncode == 0, f"rc={r1.returncode}")
r2 = subprocess.run(
    [sys.executable, "scripts/migrate_add_user_profile.py", "--db-path", str(TMP / "migrate.db")],
    capture_output=True, text=True,
)
rep = json.loads(r2.stdout)
check("迁移脚本执行成功", r2.returncode == 0 and rep.get("status") == "success")
check("迁移脚本建表", rep.get("created") is True)
r3 = subprocess.run(
    [sys.executable, "scripts/migrate_add_user_profile.py", "--db-path", str(TMP / "migrate.db")],
    capture_output=True, text=True,
)
rep3 = json.loads(r3.stdout)
check("迁移脚本幂等（重复执行 created=False）", rep3.get("created") is False)
import sqlite3 as s3
c = s3.connect(str(TMP / "migrate.db"))
tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
c.close()
check("migrate.db 含 user_profile 表", "user_profile" in tables)

# ── 清理 ──
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'='*40}")
if failures:
    print(f"结果: {len(failures)} 项失败 -> {failures}")
    sys.exit(1)
print("结果: 全部验证通过 ✓")
