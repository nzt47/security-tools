#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
查询 GlitchTip 数据库，验证上报的事件是否已落库。

执行方式：
    docker compose exec -T web python manage.py shell < verify_events_inline.py
"""
import json
import sys
import time

from django.db import connection

from apps.issue_events.models import Issue, IssueEvent, IssueTag
from apps.projects.models import Project

TRACE_ID_TARGET = "verify-1782495548"
MODULE = "verify_events"


def log(action, result="success", **kw):
    entry = {
        "trace_id": TRACE_ID_TARGET,
        "module_name": MODULE,
        "action": action,
        "duration_ms": 0,
        "result": result,
    }
    entry.update(kw)
    print(json.dumps(entry, ensure_ascii=False, default=str))


start = time.time()
log("start", "success")

# ── 1. 查询所有 Issue ──────────────────────────────
issues = Issue.objects.all().order_by("-id")
log("issues_count", "success", total=issues.count())

print("\n" + "=" * 60)
print("Issues (最近 10 条)")
print("=" * 60)
for issue in issues[:10]:
    print(f"  ID={issue.id} | status={issue.status} | level={getattr(issue, 'level', 'N/A')} | "
          f"short_id={getattr(issue, 'short_id', 'N/A')} | "
          f"project_id={getattr(issue, 'project_id', 'N/A')} | "
          f"title={getattr(issue, 'title', 'N/A')[:80]}")

# ── 2. 查询所有 IssueEvent ─────────────────────────
events = IssueEvent.objects.all().order_by("-id")
log("events_count", "success", total=events.count())

print("\n" + "=" * 60)
print("IssueEvents (最近 10 条)")
print("=" * 60)
for evt in events[:10]:
    event_id_hex = str(getattr(evt, "event_id", getattr(evt, "id", "N/A")))
    print(f"  ID={evt.id} | event_id={event_id_hex[:16]}... | "
          f"issue_id={getattr(evt, 'issue_id', 'N/A')} | "
          f"created={getattr(evt, 'created', 'N/A')}")

# ── 3. 查询 IssueTag 中是否包含 trace_id ─────────────
tags = IssueTag.objects.all()
log("tags_count", "success", total=tags.count())

trace_tags = []
for tag in tags:
    try:
        key = getattr(tag, "key", None)
        val = getattr(tag, "value", None)
        if key and "trace" in str(key).lower():
            trace_tags.append((tag.id, str(key), str(val)[:80]))
    except Exception:
        continue

print("\n" + "=" * 60)
print(f"IssueTags 包含 'trace' 关键字 ({len(trace_tags)} 条)")
print("=" * 60)
for tid, k, v in trace_tags[:10]:
    print(f"  tag_id={tid} | key={k} | value={v}")

# ── 4. 直接查询数据库表（兜底）──────────────────────
with connection.cursor() as cursor:
    cursor.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name LIKE '%issue%'
        ORDER BY table_name
    """)
    tables = [r[0] for r in cursor.fetchall()]
    log("issue_tables", "success", tables=tables)

    # 查询 issue_event_issueevent 表
    cursor.execute("""
        SELECT COUNT(*) FROM issue_events_issueevent
    """)
    total_events = cursor.fetchone()[0]
    log("raw_events_count", "success", count=total_events)

    if total_events > 0:
        cursor.execute("""
            SELECT id, issue_id, created
            FROM issue_events_issueevent
            ORDER BY id DESC
            LIMIT 5
        """)
        rows = cursor.fetchall()
        print("\n" + "=" * 60)
        print("issue_events_issueevent (最近 5 条)")
        print("=" * 60)
        for r in rows:
            print(f"  id={r[0]} | issue_id={r[1]} | created={r[2]}")

total_ms = (time.time() - start) * 1000
log("complete", "success", duration_ms=total_ms)

print("\n" + "=" * 60)
print(f"验证完成 | 耗时 {total_ms:.0f}ms")
print("=" * 60)
