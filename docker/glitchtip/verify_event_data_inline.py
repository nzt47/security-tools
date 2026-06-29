#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
查询 GlitchTip 事件原始数据，验证 trace_id 和敏感字段过滤。

执行方式：
    docker compose exec -T web python manage.py shell < verify_event_data_inline.py
"""
import json
import sys
import time

from django.db import connection

MODULE = "verify_event_data"
start = time.time()

print("=== 1. 查询 IssueEvent 原始数据 ===")
with connection.cursor() as cursor:
    cursor.execute("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = 'issue_events_issueevent'
        ORDER BY ordinal_position
    """)
    cols = cursor.fetchall()
    print("IssueEvent 表结构:")
    for c in cols:
        print(f"  {c[0]} ({c[1]})")

    cursor.execute("""
        SELECT id, issue_id, data::text
        FROM issue_events_issueevent
        ORDER BY id DESC
        LIMIT 2
    """)
    rows = cursor.fetchall()

    for r in rows:
        evt_id = str(r[0])
        issue_id = r[1]
        raw_data = r[2] if r[2] else "{}"
        print(f"\n{'='*60}")
        print(f"Event ID: {evt_id}")
        print(f"Issue ID: {issue_id}")
        print(f"Data length: {len(raw_data)} chars")
        print(f"{'='*60}")

        try:
            data = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
            state = {"trace": False, "redacted": False, "sensitive_raw": False}

            def search_dict(d, path=""):
                if isinstance(d, dict):
                    for k, v in d.items():
                        ks = str(k)
                        if ks == "trace_id" or (isinstance(v, str) and v.startswith("verify-178249")):
                            print(f"  [TRACE_ID] {path}.{k} = {v}")
                            state["trace"] = True
                        if ks in ("password", "api_key", "api-key") and isinstance(v, str):
                            if v == "[REDACTED]":
                                print(f"  [REDACTED_OK] {path}.{k} = {v}")
                                state["redacted"] = True
                            elif "should_be_redacted" in v:
                                print(f"  [SENSITIVE_RAW] {path}.{k} = {v}")
                                state["sensitive_raw"] = True
                        search_dict(v, f"{path}.{k}")
                elif isinstance(d, list):
                    for i, item in enumerate(d):
                        search_dict(item, f"{path}[{i}]")

            search_dict(data)

            print(f"\n  Summary:")
            print(f"    trace_id found: {state['trace']}")
            print(f"    [REDACTED] found: {state['redacted']}")
            print(f"    Sensitive raw found: {state['sensitive_raw']}")
            if not state["trace"]:
                print(f"    Top-level keys: {list(data.keys()) if isinstance(data, dict) else type(data)}")
        except Exception as e:
            print(f"  [ERROR] parsing data: {e}")
            print(f"  Raw (first 800 chars):")
            print(f"  {raw_data[:800]}")

print(f"\n=== 验证完成 | 耗时 {(time.time()-start)*1000:.0f}ms ===")
