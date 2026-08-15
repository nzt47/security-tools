"""Jira 任务 #TASK-1234 关联提交并更新状态脚本（手动执行）。

用法:
    1. 设置环境变量（或脚本内替换为实际值）:
       $env:JIRA_BASE_URL="https://<your-instance>.atlassian.net"
       $env:JIRA_EMAIL="you@example.com"
       $env:JIRA_TOKEN="<your-api-token>"
    2. python scripts/jira_update_task_status.py

说明:
    - 在 #TASK-1234 添加开发备注（引用 commit 661d3b74）
    - 将任务状态更新为 "Done"（状态 id 因实例配置而异，失败时打印可用的 transitions）
"""
import json
import os
import sys
import urllib.request

BASE = os.environ.get("JIRA_BASE_URL", "").rstrip("/")
EMAIL = os.environ.get("JIRA_EMAIL", "")
TOKEN = os.environ.get("JIRA_TOKEN", "")
ISSUE = "TASK-1234"
COMMIT = "661d3b74"
NEW_STATUS = "Done"  # 若实例的过渡 id 不同，脚本会列出可选 transitions

if not (BASE and EMAIL and TOKEN):
    sys.exit("请先设置 JIRA_BASE_URL / JIRA_EMAIL / JIRA_TOKEN 环境变量")

import base64

auth = base64.b64encode(f"{EMAIL}:{TOKEN}".encode()).decode()
headers = {
    "Authorization": f"Basic {auth}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}


def _req(method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


comment = (
    f"开发提交关联: `{COMMIT}`\n"
    "变更摘要:\n"
    "- 补全 prometheus.yml rule_files 引入 lock_watchdog_alerts.yml\n"
    "- 新增 scripts/verify_prometheus_checklist.py (C1-C5 一键验证)\n"
    "- 阶段5手册新增 §2.3 部署验证 Checklist\n"
    "- 指标命名规范修复配套收尾 (db70b097 同批)\n"
    "部署注意: C2-C6 需部署环境执行脚本完成实际采集验证。"
)
status_code, body = _req(
    "POST", f"/rest/api/2/issue/{ISSUE}/comment", {"body": comment}
)
print(f"添加备注: HTTP {status_code}" + ("" if status_code == 201 else f"\n{body[:500]}"))

# 查询当前 transitions
status_code, body = _req("GET", f"/rest/api/2/issue/{ISSUE}/transitions")
if status_code == 200:
    transitions = json.loads(body).get("transitions", [])
    print("可用 transitions:", [t["name"] for t in transitions])
    target = next((t for t in transitions if t["name"].lower() == NEW_STATUS.lower()), None)
    if target:
        status_code, body = _req(
            "POST",
            f"/rest/api/2/issue/{ISSUE}/transitions",
            {"transition": {"id": target["id"]}},
        )
        print(f"更新状态 -> {NEW_STATUS}: HTTP {status_code}")
    else:
        print(f"未找到名为 '{NEW_STATUS}' 的 transition，请手动选择上面的可用值")
else:
    print(f"查询 transitions 失败: HTTP {status_code}\n{body[:500]}")
