#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通过 Django ORM 在 GlitchTip 容器内创建组织/团队/项目并输出 DSN。

用法（容器内执行）：
    docker compose exec -T web python /code/setup_glitchtip_orm.py

可观测性：
- 输出结构化 JSON 日志（trace_id / module_name / action / duration_ms）
- 包含 success / failure 标签
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

# ─── 在容器内必须先初始化 Django 环境 ─────────────────
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "glitchtip.settings.production")
try:
    django.setup()
except Exception as e:
    print(f"[FATAL] django.setup() failed: {e}", file=sys.stderr)
    sys.exit(2)

# ─── 导入 GlitchTip 核心模型 ─────────────────────────
from django.contrib.auth import get_user_model
from django.db import transaction

from organizations.models import Organization, OrganizationUser
from teams.models import Team
from projects.models import Project, ProjectKey
from glitchtip.users.models import EmailAddress

User = get_user_model()

# ─── 结构化日志工具 ─────────────────────────────────
TRACE_ID = f"setup-{int(time.time())}"
MODULE_NAME = "setup_glitchtip_orm"


def log(action: str, duration_ms: float = 0.0, result: str = "success", **kwargs):
    """输出结构化 JSON 日志（满足可观测性约束）"""
    entry = {
        "trace_id": TRACE_ID,
        "module_name": MODULE_NAME,
        "action": action,
        "duration_ms": round(duration_ms, 2),
        "result": result,
    }
    entry.update({k: v for k, v in kwargs.items() if k not in entry})
    print(json.dumps(entry, ensure_ascii=False, default=str))


# ─── 主流程 ─────────────────────────────────────────
def ensure_user():
    """确保超级管理员账号存在并激活"""
    email = "admin@local.test"
    password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "Admin@2026!")

    user = User.objects.filter(email=email).first()
    if user:
        if not user.is_active:
            user.is_active = True
            user.save(update_fields=["is_active"])
        log("ensure_user", 0, "success", user_id=user.id, email=email, action="existing")
        return user

    user = User.objects.create_superuser(email=email, password=password)
    EmailAddress.objects.get_or_create(
        user=user, email=email, defaults={"primary": True, "verified": True}
    )
    log("ensure_user", 0, "success", user_id=user.id, email=email, action="created")
    return user


def ensure_organization(owner):
    """获取或创建默认组织"""
    slug = "yunshu"
    org = Organization.objects.filter(slug=slug).first()
    if org:
        log("ensure_organization", 0, "success", org_id=org.id, slug=slug, action="existing")
        return org

    org = Organization.objects.create(name="Yunshu", slug=slug)
    OrganizationUser.objects.create(organization=org, user=owner, role=4)
    log("ensure_organization", 0, "success", org_id=org.id, slug=slug, action="created")
    return org


def ensure_team(org):
    """获取或创建默认团队"""
    slug = "yunshu-team"
    team = Team.objects.filter(organization=org, slug=slug).first()
    if team:
        log("ensure_team", 0, "success", team_id=team.id, slug=slug, action="existing")
        return team
    team = Team.objects.create(organization=org, name="Yunshu Team", slug=slug)
    log("ensure_team", 0, "success", team_id=team.id, slug=slug, action="created")
    return team


def ensure_project(org, team):
    """获取或创建默认项目（platform=python）"""
    slug = "yunshu-backend"
    project = Project.objects.filter(organization=org, slug=slug).first()
    if project:
        log("ensure_project", 0, "success", project_id=project.id, slug=slug, action="existing")
        return project

    project = Project.objects.create(
        organization=org,
        team=team,
        name="Yunshu Backend",
        slug=slug,
        platform="python",
    )
    log("ensure_project", 0, "success", project_id=project.id, slug=slug, action="created")
    return project


def get_or_create_dsn(project):
    """获取或创建 ProjectKey，输出 DSN 字符串"""
    key = ProjectKey.objects.filter(project=project).first()
    if not key:
        key = ProjectKey.objects.create(project=project)
        log("get_or_create_dsn", 0, "success", key_id=key.id, action="created")
    else:
        log("get_or_create_dsn", 0, "success", key_id=key.id, action="existing")

    # GlitchTip DSN 格式：http://<public_key>@<host>/<project_id>
    # public_key 即 ProjectKey.datastore_name 或 DSN public key
    # 在 GlitchTip 中，DSN 字段已经计算好，直接取用即可
    public_key = key.public_key_hex if hasattr(key, "public_key_hex") else None

    # 兜底：从 key.data 属性或 DSN 字段读取
    if not public_key:
        # GlitchTip 在 ProjectKey 模型中保存了完整 DSN，尝试读取
        # 不同版本字段名可能不同：dsn / get_dsn / public_key
        for attr in ("public_key", "dsn_public_key", "datastore_name"):
            val = getattr(key, attr, None)
            if callable(val):
                try:
                    val = val()
                except Exception:
                    val = None
            if val:
                public_key = val
                break

    host = os.environ.get("GLITCHTIP_DOMAIN", "http://localhost:8000")
    # 去除协议前缀，DSN 中需保留 host
    host_clean = host.replace("https://", "").replace("http://", "").rstrip("/")
    # DSN schema：http://<key>@localhost:8000/<project_id>
    # GlitchTip 容器内 self=http://localhost:8000；外部访问也用此 host
    dsn = f"http://{public_key}@localhost:8000/{project.id}"
    return dsn, public_key, project.id


def main():
    start = time.time()
    try:
        log("start", 0, "success", trace_id=TRACE_ID)

        # 1. 用户
        user = ensure_user()

        # 2. 组织 / 团队 / 项目（事务保护）
        with transaction.atomic():
            org = ensure_organization(user)
            team = ensure_team(org)
            project = ensure_project(org, team)

        # 3. DSN
        dsn, public_key, project_id = get_or_create_dsn(project)

        total_ms = (time.time() - start) * 1000
        log("complete", total_ms, "success",
            dsn=dsn, public_key=public_key, project_id=project_id)

        # 输出到 stdout 最后一行，便于外部解析
        print("=" * 60)
        print("GLITCHTIP_DSN=" + dsn)
        print("PROJECT_ID=" + str(project_id))
        print("PUBLIC_KEY=" + str(public_key))
        print("=" * 60)
        return 0
    except Exception as e:
        total_ms = (time.time() - start) * 1000
        log("error", total_ms, "failure",
            error_type=type(e).__name__, error_message=str(e),
            stack_trace=traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
