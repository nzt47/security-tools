"""等待服务就绪脚本"""
import time
import requests
import sys

BASE_URL = "http://localhost:5678"
MAX_WAIT = 90  # 最长等待90秒


def wait_for_service():
    """等待服务就绪"""
    start = time.time()
    while time.time() - start < MAX_WAIT:
        try:
            resp = requests.get(f"{BASE_URL}/api/health/summary", timeout=3)
            if resp.status_code == 200:
                data = resp.json()
                print(f"✅ 服务就绪! 耗时 {time.time()-start:.1f}秒")
                print(f"   当前健康度: {data.get('overall_score')}")
                print(f"   当前等级: {data.get('level')}")
                return True
        except requests.exceptions.ConnectionError:
            pass
        except Exception as e:
            print(f"   等待中... ({e.__class__.__name__})")
        time.sleep(3)
    print(f"❌ 服务 {MAX_WAIT} 秒内未就绪")
    return False


if __name__ == "__main__":
    ok = wait_for_service()
    sys.exit(0 if ok else 1)
