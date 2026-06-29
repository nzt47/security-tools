"""快速验证 D2/D3/D5 三项可见性指标"""
import re
import glob
import os

agent_dir = 'agent'

# D2: 结构化日志覆盖率
total_logs = 0
structured_logs = 0
for py_file in glob.iglob(agent_dir + '/**/*.py', recursive=True):
    try:
        content = open(py_file, encoding='utf-8').read()
    except Exception:
        continue
    log_calls = re.findall(r'logger\.(info|debug|warning|error|critical)\(', content)
    total_logs += len(log_calls)
    structured = re.findall(r'logger\.\w+\(.*?(?:trace_id|json\.dumps)', content, re.DOTALL)
    structured_logs += len(structured)
d2 = round(structured_logs / total_logs * 100, 1) if total_logs else 100.0

# D3: trace_coverage
routes_dir = 'agent/server_routes'
total_routes = 0
traced_routes = 0
for py_file in glob.iglob(routes_dir + '/routes_*.py'):
    content = open(py_file, encoding='utf-8').read()
    total_routes += len(re.findall(r'@app\.route\(["\']', content))
    traced_routes += len(re.findall(r'@trace_route', content))
d3 = round(traced_routes / total_routes * 100, 1) if total_routes else 100.0

# D5: track_event_coverage
total_modules = 0
tracked_modules = 0
for sub_dir in os.listdir(agent_dir):
    full = os.path.join(agent_dir, sub_dir)
    if not os.path.isdir(full) or sub_dir.startswith('_'):
        continue
    total_modules += 1
    for root, _, files in os.walk(full):
        for f in files:
            if not f.endswith('.py'):
                continue
            try:
                content = open(os.path.join(root, f), encoding='utf-8').read()
            except Exception:
                continue
            if re.search(r'(trackEvent|BusinessMetricsCollector|track\()', content):
                tracked_modules += 1
                break
d5 = round(tracked_modules / total_modules * 100, 1) if total_modules else 100.0

print(f'D2 structured_log_coverage: {d2}% (threshold >=30) {"PASS" if d2>=30 else "FAIL"}')
print(f'D3 trace_coverage: {d3}% (threshold >=30) {"PASS" if d3>=30 else "FAIL"}')
print(f'D5 track_event_coverage: {d5}% (threshold >=30) {"PASS" if d5>=30 else "FAIL"}')
print(f'  total_routes={total_routes}, traced_routes={traced_routes}')
print(f'  total_modules={total_modules}, tracked_modules={tracked_modules}')
