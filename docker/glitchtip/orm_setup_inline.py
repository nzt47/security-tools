#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GlitchTip ORM 初始化脚本（容器内通过 manage.py shell 执行）。

执行方式：
    docker compose exec -T web python manage.py shell < orm_setup_inline.py

字段依据（通过 _meta.get_fields() 探测）：
- Team: slug, organization（无 name）
- Project: slug, name, organization, platform, teams(M2M)
- ProjectKey: project, public_key(UUIDField), is_active, name, data(JSONField)
"""
import json
import os
import re
import sys
import time
import traceback

from django.contrib.auth import get_user_model
from django.db import transaction

from apps.organizations_ext.models import Organization, OrganizationUser
from apps.teams.models import Team
from apps.projects.models import Project, ProjectKey

User = get_user_model()

TRACE_ID = f"setup-{int(time.time())}"
MODULE = "orm_setup_inline"


def log(action, duration_ms=0.0, result="success", **kw):
    entry = {
        "trace_id": TRACE_ID,
        "module_name": MODULE,
        "action": action,
        "duration_ms": round(duration_ms, 2),
        "result": result,
    }
    entry.update(kw)
    print(json.dumps(entry, ensure_ascii=False, default=str))


def main():
    start = time.time()
    log("start", 0, "success")

    # ── 1. 确保超级管理员账号存在 ─────────────────────
    email = "admin@local.test"
    password = "Admin@2026!"
    user = User.objects.filter(email=email).first()
    if not user:
        user = User.objects.create_superuser(email=email, password=password)
        log("user_created", 0, "success", user_id=user.id)
    else:
        if not user.is_active:
            user.is_active = True
            user.save(update_fields=["is_active"])
        log("user_existing", 0, "success", user_id=user.id)

    # ── 2. 组织 / 团队 / 项目 ─────────────────────────
    with transaction.atomic():
        org_slug = "yunshu"
        org, org_created = Organization.objects.get_or_create(
            slug=org_slug, defaults={"name": "Yunshu"}
        )
        log("org_created" if org_created else "org_existing", 0, "success", org_id=org.id)

        if not OrganizationUser.objects.filter(organization=org, user=user).exists():
            OrganizationUser.objects.create(organization=org, user=user, role=4)
            log("orguser_created", 0, "success")

        # Team: 只有 slug + organization 字段
        team_slug = "yunshu-team"
        team, team_created = Team.objects.get_or_create(
            organization=org, slug=team_slug
        )
        log("team_created" if team_created else "team_existing", 0, "success", team_id=team.id)

        # Project: teams 是 M2M，不是 FK
        proj_slug = "yunshu-backend"
        project, proj_created = Project.objects.get_or_create(
            organization=org,
            slug=proj_slug,
            defaults={"name": "Yunshu Backend", "platform": "python"},
        )
        log("project_created" if proj_created else "project_existing", 0, "success", project_id=project.id)

        # 关联 team ↔ project（M2M）
        if team not in project.teams.all():
            project.teams.add(team)

    # ── 3. ProjectKey 与 DSN ─────────────────────────
    # public_key 是 UUIDField，创建时会自动生成
    key = ProjectKey.objects.filter(project=project, is_active=True).first()
    if not key:
        key = ProjectKey.objects.create(project=project, name="Default")
        log("projectkey_created", 0, "success", key_id=key.id, public_key=str(key.public_key))
    else:
        log("projectkey_existing", 0, "success", key_id=key.id, public_key=str(key.public_key))

    public_key = str(key.public_key)  # UUID → str

    # GlitchTip DSN: http://<public_key>@<host>/<project_id>
    host = "localhost:8000"
    dsn = f"http://{public_key}@{host}/{project.id}"

    total_ms = (time.time() - start) * 1000
    log("complete", total_ms, "success",
        dsn=dsn, public_key=public_key, project_id=project.id)

    print("=" * 60)
    print("GLITCHTIP_DSN=" + dsn)
    print("PROJECT_ID=" + str(project.id))
    print("PUBLIC_KEY=" + public_key)
    print("=" * 60)


# manage.py shell 通过 exec() 执行脚本，直接调用 main()
try:
    main()
except Exception as e:
    log("error", 0, "failure",
        error_type=type(e).__name__,
        error_message=str(e),
        stack_trace=traceback.format_exc())
    sys.exit(1)
