"""Jira 任务 #TASK-1234 关联提交、上传附件并更新状态脚本（手动执行）。

用法:
    1. 设置环境变量（或脚本内替换为实际值）:
       $env:JIRA_BASE_URL="https://<your-instance>.atlassian.net"
       $env:JIRA_EMAIL="you@example.com"
       $env:JIRA_TOKEN="<your-api-token>"
       $env:JIRA_ATTACH="C:\\Windows\\Temp\\task8_close_audit_20260815.zip"  # 可选
    2. python scripts/jira_update_task_status.py

说明:
    - 可选上传审计 zip 附件（JIRA_ATTACH 指向文件）
    - 在 #TASK-1234 添加开发备注（引用 commit 661d3b74 + 本地验证结果）
    - 将任务状态更新为 "Done"（状态 id 因实例配置而异，失败时打印可用的 transitions）
"""
import json
import os
import sys
import urllib.request
import uuid
from pathlib import Path

BASE = os.environ.get("JIRA_BASE_URL", "").rstrip("/")
EMAIL = os.environ.get("JIRA_EMAIL", "")
TOKEN = os.environ.get("JIRA_TOKEN", "")
ATTACH = os.environ.get("JIRA_ATTACH", "")
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


def _req(method, path, payload=None, raw_headers=None):
    data = json.dumps(payload).encode() if payload is not None else None
    hdrs = dict(headers)
    if raw_headers:
        hdrs.update(raw_headers)
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def _upload_attachment(path):
    """以 multipart/form-data 上传附件（Jira 要求 X-Atlassian-Token: no-check）。"""
    p = Path(path)
    if not p.is_file():
        print(f"附件不存在: {path}")
        return None
    boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"
    data = bytearray()
    def _part(name, value, is_file=False, filename=""):
        data.extend(f"--{boundary}\r\n".encode())
        if is_file:
            data.extend(f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode())
            data.extend(b"Content-Type: application/zip\r\n\r\n")
            data.extend(value)
        else:
            data.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
            data.extend(b"\r\n")
    _part("file", p.read_bytes(), is_file=True, filename=p.name)
    data.extend(f"--{boundary}--\r\n".encode())
    hdrs = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "X-Atlassian-Token": "no-check",
    }
    req = urllib.request.Request(
        f"{BASE}/rest/api/2/issue/{ISSUE}/attachments",
        data=bytes(data), headers=hdrs, method="POST",
    )
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
    "本地验证结果 (2026-08-15):\n"
    "- 全量单元测试 11749 passed / 2 failed (TestCallLLMV2 修复后 2 passed，commit 6964d441)\n"
    "- 指标命名规范测试 45 passed，全库违规 0\n"
    "- 验证脚本 C1 PASS（降级 YAML 校验），C2-C5 SKIP（本地无 Prometheus 实例）\n"
    "部署注意: C2-C6 需部署环境执行脚本完成实际采集验证。"
)
if ATTACH:
    status_code, body = _upload_attachment(ATTACH)
    if status_code is None:
        print(f"附件上传跳过: {body or '文件不存在'}")
    else:
        print(f"上传附件 {Path(ATTACH).name}: HTTP {status_code}" + ("" if status_code == 200 else f"\n{body[:500]}"))
else:
    print("上传附件跳过: 未设置 JIRA_ATTACH")

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
