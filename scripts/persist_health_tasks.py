"""将健康检查定时任务持久化并注册

1. 将健康检查任务写入 data/scheduled_tasks.json（服务重启后 load_from_json 自动加载）
2. 通过 API 立即创建（当前会话生效）
"""
import json
import os
import time
import requests

BASE_URL = "http://localhost:5678"
DATA_DIR = "c:/Users/Administrator/agent/data"
TASKS_FILE = os.path.join(DATA_DIR, "scheduled_tasks.json")

# 健康检查任务定义（与 setup_health_schedule.py 保持一致）
HEALTH_TASKS = [
    {
        "name": "系统健康检查",
        "command": "python scripts/health_check.py --json",
        "interval_sec": 300,
    },
    {
        "name": "健康度评分上报",
        "command": (
            "python -c \"import requests; "
            "r=requests.post('http://localhost:5678/api/health/score'); "
            "print('Health:', r.json().get('overall_score', 'N/A'))\""
        ),
        "interval_sec": 60,
    },
    {
        "name": "详细健康报告",
        "command": "python scripts/health_check.py --detail",
        "interval_sec": 1800,
    },
]


def persist_tasks():
    """将健康任务写入 scheduled_tasks.json（幂等）"""
    # 读取现有任务
    existing = []
    if os.path.exists(TASKS_FILE):
        with open(TASKS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        existing = data.get("tasks", [])

    existing_names = {t.get("name") for t in existing}
    added = 0
    for t in HEALTH_TASKS:
        if t["name"] in existing_names:
            print(f"  ⏭️ 已存在: {t['name']}")
            continue
        task = {
            "id": str(int(time.time() * 1000)) + str(added),
            "name": t["name"],
            "command": t["command"],
            "interval_sec": t["interval_sec"],
            "enabled": True,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "last_run": None,
            "run_count": 0,
        }
        existing.append(task)
        existing_names.add(t["name"])
        added += 1

    with open(TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump({"tasks": existing}, f, ensure_ascii=False, indent=2)
    print(f"✅ 已写入 {added} 个新任务到 {TASKS_FILE}（当前共 {len(existing)} 个）")
    return added


def register_via_api():
    """通过 API 立即创建任务（当前会话生效）"""
    # 获取现有任务名，避免重复
    try:
        r = requests.get(f"{BASE_URL}/api/scheduler/tasks", timeout=5)
        current = r.json().get("tasks", [])
        current_names = {t.get("name") for t in current}
    except Exception:
        current_names = set()

    created = 0
    for t in HEALTH_TASKS:
        if t["name"] in current_names:
            print(f"  ⏭️ API 已存在: {t['name']}")
            continue
        try:
            r = requests.post(
                f"{BASE_URL}/api/scheduler/create",
                json={"name": t["name"], "command": t["command"], "interval_sec": t["interval_sec"]},
                timeout=5,
            )
            result = r.json()
            if result.get("ok"):
                print(f"  ✅ API 创建成功: {t['name']}")
                created += 1
            else:
                print(f"  ❌ API 创建失败: {t['name']} - {result.get('error')}")
        except Exception as e:
            print(f"  ❌ API 请求失败: {t['name']} - {e}")
    return created


def verify():
    """验证任务是否生效"""
    r = requests.get(f"{BASE_URL}/api/scheduler/tasks", timeout=5)
    tasks = r.json().get("tasks", [])
    print("\n=== 当前健康任务 ===")
    found = False
    for t in tasks:
        name = t.get("name") or ""
        if "健康" in name or "health" in name.lower():
            found = True
            enabled = "ON" if t.get("enabled") else "OFF"
            print(f"  [{enabled}] {name} | 间隔: {t.get('interval_sec')}s | 命令: {(t.get('command') or '')[:60]}")


if __name__ == "__main__":
    print("=" * 60)
    print("📝 健康检查定时任务持久化")
    print("=" * 60)
    persist_tasks()
    print("\n🚀 通过 API 注册（当前会话生效）...")
    register_via_api()
    verify()
