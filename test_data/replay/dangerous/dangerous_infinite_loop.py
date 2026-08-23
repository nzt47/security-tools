import sys
import json

params = json.loads(sys.stdin.read())

# 死循环：应被墙钟超时强杀（进程级 terminate/kill）
while True:
    pass
