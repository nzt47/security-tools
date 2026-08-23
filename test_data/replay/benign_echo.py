import sys
import json

params = json.loads(sys.stdin.read())

# 良性对照：回显参数（验证隔离层不误伤合法候选）
out = {"summary": "ok", "sample_id": params.get("sample_id")}
print(json.dumps(out, ensure_ascii=False))
