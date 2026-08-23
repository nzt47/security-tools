import sys
import json

params = json.loads(sys.stdin.read())

# worker 级逃逸尝试：sys.exit（预扫描放行 sys，由 worker sys 代理拦截 → escape）
sys.exit(1)
