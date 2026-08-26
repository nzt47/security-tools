"""诊断: 捕获 agent.permission_system logger 的输出,看是否有非 JSON 行"""
import json
import logging
import sys
import os

sys.path.insert(0, os.path.abspath("."))

captured = []

class CaptureHandler(logging.Handler):
    def emit(self, record):
        captured.append(record.getMessage())

logger = logging.getLogger("agent.permission_system")
logger.setLevel(logging.DEBUG)
logger.addHandler(CaptureHandler())

from agent.permission_system import PermissionGateway, ABACContext, Role

gw = PermissionGateway(policy_path="data/permission_policies.json")
ctx = ABACContext(role=Role.GUEST, session_source="cli")
gw.check("shell_execute", {"cmd": "ls"}, ctx)

print(f"=== 捕获 {len(captured)} 行 ===")
for i, line in enumerate(captured):
    is_json = False
    try:
        json.loads(line)
        is_json = True
    except Exception as e:
        pass
    print(f"[{i}] json={is_json} len={len(line)} repr={line[:120]!r}")
