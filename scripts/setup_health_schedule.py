"""健康检查定时任务配置脚本"""
import requests
import json

BASE_URL = "http://localhost:5678"


def create_scheduled_task(name: str, command: str, interval_sec: int):
    """创建定时任务"""
    try:
        resp = requests.post(
            f"{BASE_URL}/api/scheduler/create",
            json={"name": name, "command": command, "interval_sec": interval_sec},
            timeout=5
        )
        result = resp.json()
        if result.get("ok"):
            print(f"✅ 任务创建成功: {name}")
            return True
        else:
            print(f"❌ 任务创建失败: {name} - {result.get('error')}")
            return False
    except Exception as e:
        print(f"❌ 请求失败: {e}")
        return False


def list_tasks():
    """列出所有任务"""
    try:
        resp = requests.get(f"{BASE_URL}/api/scheduler/tasks", timeout=5)
        result = resp.json()
        tasks = result.get("tasks", [])
        print(f"\n📋 当前定时任务列表 (共 {len(tasks)} 个):")
        if not tasks:
            print("   (暂无定时任务)")
        for task in tasks:
            enabled = "✓" if task.get("enabled") else "✗"
            interval = task.get("interval_sec", "N/A")
            last_run = task.get("last_run", "从未")
            print(f"   [{enabled}] {task['name']}")
            print(f"       命令: {task.get('command', 'N/A')[:60]}...")
            print(f"       间隔: {interval}s | 上次: {last_run}")
        return tasks
    except Exception as e:
        print(f"❌ 获取任务列表失败: {e}")
        return []


def main():
    print("=" * 60)
    print("🏥 健康检查定时任务配置")
    print("=" * 60)

    # 健康检查任务配置
    tasks = [
        {
            "name": "系统健康检查",
            "command": "python scripts/health_check.py --json",
            "interval_sec": 300,
            "description": "每5分钟执行一次系统健康检查，生成JSON报告"
        },
        {
            "name": "健康度评分上报",
            "command": "python -c \"import requests; r=requests.post('http://localhost:5678/api/health/score'); print('Health:', r.json().get('overall_score', 'N/A'))\"",
            "interval_sec": 60,
            "description": "每分钟计算并上报健康度评分"
        },
        {
            "name": "详细健康报告",
            "command": "python scripts/health_check.py --detail",
            "interval_sec": 1800,
            "description": "每30分钟执行一次详细健康检查"
        }
    ]

    print("\n📝 将创建以下定时任务:")
    for i, task in enumerate(tasks, 1):
        print(f"\n   {i}. {task['name']}")
        print(f"      间隔: {task['interval_sec']}秒 ({task['interval_sec']//60}分钟)")
        print(f"      说明: {task['description']}")

    print("\n" + "-" * 60)
    confirm = input("是否创建这些定时任务? (y/n): ").strip().lower()

    if confirm == "y":
        print("\n🚀 开始创建定时任务...")
        for task in tasks:
            create_scheduled_task(task["name"], task["command"], task["interval_sec"])
    else:
        print("已取消")

    # 列出当前所有任务
    list_tasks()

    print("\n" + "=" * 60)
    print("✅ 定时任务配置完成!")
    print("=" * 60)


if __name__ == "__main__":
    main()
