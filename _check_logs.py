"""检查结构化日志转换质量"""
import re
from pathlib import Path

files = [
    'agent/p6_snapshot.py',
    'agent/p6/snapshot.py',
    'agent/orchestrator/lifecycle_manager.py',
    'agent/tools/file_tools.py',
    'agent/web/search.py',
    'agent/network/config_manager.py',
    'agent/network_config.py',
]

print("=" * 70)
print("结构化日志转换质量检查")
print("=" * 70)

total_structured = 0
total_broken = 0

for f in files:
    c = Path(f).read_text(encoding='utf-8')
    # 统计转换后的结构化日志
    structured = re.findall(r'logger\.\w+\(json\.dumps\(\{.*?"trace_id".*?\}', c, re.DOTALL)
    # 检查损坏的日志（json.dumps 未闭合）
    broken = re.findall(r'logger\.\w+\(json\.dumps\(\{[^}]*$', c, re.MULTILINE)
    # 检查 ensure_ascii 参数
    has_ensure_ascii = 'ensure_ascii=False' in c
    # 检查必要字段
    has_trace_id = '"trace_id"' in c
    has_module = '"module_name"' in c
    has_action = '"action"' in c
    has_duration = '"duration_ms"' in c

    total_structured += len(structured)
    total_broken += len(broken)

    status = "OK" if len(broken) == 0 else "BROKEN"
    fields = f"trace_id={has_trace_id}, module={has_module}, action={has_action}, duration={has_duration}"
    print(f"  [{status}] {f}: {len(structured)} structured, {len(broken)} broken, ensure_ascii={has_ensure_ascii}")
    print(f"         fields: {fields}")

print(f"\n总计: {total_structured} structured logs, {total_broken} broken logs")
print(f"质量: {'PASS' if total_broken == 0 else 'FAIL'}")
