# P1 硬编码密码修复执行计划

> **报告日期**：2026-07-20
> **优先级**：P1（高，安全合规要求）
> **预估总耗时**：72 分钟
> **关联文档**：
> - [BFG_CLEANUP_REPORT_20260719.md](../BFG_CLEANUP_REPORT_20260719.md)
> - [GH_ACTIONS_CLEANUP_REPORT_20260720.md](./GH_ACTIONS_CLEANUP_REPORT_20260720.md)
> - [SECURITY_AUDIT_FINAL_SUMMARY_P1_P3_20260720.md](./SECURITY_AUDIT_FINAL_SUMMARY_P1_P3_20260720.md)

---

## 一、背景与目标

### 1.1 背景

BFG 清理已将 git 历史中的明文密码替换为占位符（如 `***REMOVED_GLITCHTIP_PWD***`），但**工作区当前已跟踪文件**仍存在以下硬编码密码问题：

- GlitchTip 超级管理员密码以 BFG 占位符形式残留（运行时会失败）
- Grafana 默认密码 `admin123` 直接硬编码在脚本与 compose 文件中
- `.env` 缺失必要的密码变量定义

### 1.2 修复目标（【不易】不变量）

- **安全不变量**：所有密码必须从环境变量读取，禁止硬编码到任何已跟踪文件
- **兼容不变量**：现有 GlitchTip/Grafana 部署流程不破坏，可正常启动
- **可测不变量**：每个修复点需可通过 DryRun/启动验证确认无回归

---

## 二、修复范围一览

| 序号 | 文件 | 行号 | 当前值 | 修复方式 | 耗时 |
|------|------|------|--------|---------|------|
| 1 | `.env` | 新增 | 无 | 添加 `GRAFANA_ADMIN_PASSWORD=` + `GLITCHTIP_ADMIN_PASSWORD=` | 5 min |
| 2 | `.env.example` | 新增 | 无 | 添加示例变量（占位符值） | 2 min |
| 3 | `docker/glitchtip/orm_setup_inline.py` | L52 | `***REMOVED_GLITCHTIP_PWD***` | `os.environ.get('GLITCHTIP_ADMIN_PASSWORD')` + 缺失即 sys.exit(1) | 10 min |
| 4 | `scripts/_import_dashboards.py` | L6-8 | `admin`/`admin123` | `os.environ.get('GRAFANA_ADMIN_USER', 'admin')` + `os.environ.get('GRAFANA_ADMIN_PASSWORD')` | 5 min |
| 5 | `docker-compose.monitoring.yml` | L28 | `GF_SECURITY_ADMIN_PASSWORD=admin123` | `GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASSWORD:-admin}` | 5 min |
| 6 | `docker-compose.monitoring.aliyun.yml` | L40 | `admin123` | 同上 | 5 min |
| 7 | 功能验证 | - | - | DryRun + 容器启动验证 | 20 min |
| 8 | 回归测试 | - | - | 跑相关 pytest + lint | 15 min |
| 9 | 提交变更 | - | - | git commit + push | 5 min |

---

## 三、详细修复方案

### 3.1 Step 1：在 .env 中添加密码变量（5 min）

**位置**：`c:\Users\Administrator\agent\.env`

**修改**：在文件末尾追加以下内容（不展示已有内容）：

```bash
# ============================================================================
# 监控/可观测性组件管理员密码（P1 安全修复 2026-07-20 新增）
# ============================================================================
# GlitchTip 超级管理员密码（用于 orm_setup_inline.py 初始化）
# 长度 >= 12，包含大小写字母+数字+符号
GLITCHTIP_ADMIN_EMAIL=admin@local.test
GLITCHTIP_ADMIN_PASSWORD=

# Grafana 管理员密码（用于 _import_dashboards.py 与 docker-compose）
GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=

# GlitchTip PostgreSQL 密码（如未单独配置）
POSTGRES_PASSWORD=
```

**验证**：

```powershell
Get-Content c:\Users\Administrator\agent\.env | Select-String "GLITCHTIP_ADMIN_PASSWORD|GRAFANA_ADMIN_PASSWORD"
```

### 3.2 Step 2：在 .env.example 中添加示例（2 min）

**位置**：`c:\Users\Administrator\agent\.env.example`

**修改**：追加以下内容（使用占位符，不暴露真实密码）：

```bash
# ============================================================================
# 监控/可观测性组件管理员密码（示例占位符，请替换为真实值）
# ============================================================================
GLITCHTIP_ADMIN_EMAIL=admin@local.test
GLITCHTIP_ADMIN_PASSWORD=your-glitchtip-password-here

GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=your-grafana-password-here

POSTGRES_PASSWORD=your-postgres-password-here
```

### 3.3 Step 3：修复 docker/glitchtip/orm_setup_inline.py（10 min）

**位置**：`c:\Users\Administrator\agent\docker\glitchtip\orm_setup_inline.py` L50-57

**修改前**：

```python
    # ── 1. 确保超级管理员账号存在 ─────────────────────
    email = "admin@local.test"
    password = "***REMOVED_GLITCHTIP_PWD***"
    user = User.objects.filter(email=email).first()
    if not user:
        user = User.objects.create_superuser(email=email, password=password)
        log("user_created", 0, "success", user_id=user.id)
    else:
        if not user.is_active:
            user.is_active = True
            user.save(update_fields=["is_active"])
        log("user_existing", 0, "success", user_id=user.id)
```

**修改后**：

```python
    # ── 1. 确保超级管理员账号存在 ─────────────────────
    # 【P1 修复 2026-07-20】密码从环境变量读取，避免硬编码（BFG 清理后占位符已失效）
    email = os.environ.get("GLITCHTIP_ADMIN_EMAIL", "admin@local.test")
    password = os.environ.get("GLITCHTIP_ADMIN_PASSWORD")
    if not password:
        log("error", 0, "failure",
            error_type="ConfigError",
            error_message="GLITCHTIP_ADMIN_PASSWORD 环境变量未设置，无法初始化超级管理员")
        sys.exit(1)
    user = User.objects.filter(email=email).first()
    if not user:
        user = User.objects.create_superuser(email=email, password=password)
        log("user_created", 0, "success", user_id=user.id)
    else:
        if not user.is_active:
            user.is_active = True
            user.save(update_fields=["is_active"])
        log("user_existing", 0, "success", user_id=user.id)
```

**关键说明**：
- `os` 与 `sys` 模块在文件 L14/L17 已 import，无需新增
- 缺失环境变量时调用 `sys.exit(1)` 而非抛异常，符合 GlitchTip 容器初始化失败语义
- 日志通过现有 `log()` 函数输出，保持可观测性

### 3.4 Step 4：修复 scripts/_import_dashboards.py（5 min）

**位置**：`c:\Users\Administrator\agent\scripts\_import_dashboards.py` L1-8

**修改前**：

```python
#!/usr/bin/env python3
"""导入全链路监控仪表盘到 Grafana"""
import json
import requests

GRAFANA_URL = "http://localhost:3000"
GRAFANA_USER = "admin"
GRAFANA_PASSWORD = "admin123"
```

**修改后**：

```python
#!/usr/bin/env python3
"""导入全链路监控仪表盘到 Grafana"""
import json
import os
import sys

import requests

GRAFANA_URL = os.environ.get("GRAFANA_URL", "http://localhost:3000")
GRAFANA_USER = os.environ.get("GRAFANA_ADMIN_USER", "admin")
# 【P1 修复 2026-07-20】密码从环境变量读取，避免硬编码 admin123
GRAFANA_PASSWORD = os.environ.get("GRAFANA_ADMIN_PASSWORD")
if not GRAFANA_PASSWORD:
    print("ERROR: GRAFANA_ADMIN_PASSWORD 环境变量未设置，无法导入仪表盘")
    sys.exit(1)
```

**关键说明**：
- `os.environ.get("GRAFANA_ADMIN_USER", "admin")` 保留默认值 `admin`（用户名非敏感）
- `GRAFANA_PASSWORD` 无默认值，缺失即退出，避免误用空密码

### 3.5 Step 5：修复 docker-compose.monitoring.yml（5 min）

**位置**：`c:\Users\Administrator\agent\docker-compose.monitoring.yml` L28

**修改前**：

```yaml
      - GF_SECURITY_ADMIN_PASSWORD=admin123
```

**修改后**：

```yaml
      - GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASSWORD:-admin}
```

**关键说明**：
- 使用 docker-compose 变量插值 `${VAR:-default}` 语法
- 兜底值 `admin` 仅用于本地开发，生产部署必须在 `.env` 中配置真实密码
- 如果 `.env` 中未设置 `GRAFANA_ADMIN_PASSWORD`，启动时会警告（在 Step 1 已要求设置）

### 3.6 Step 6：修复 docker-compose.monitoring.aliyun.yml（5 min）

**位置**：`c:\Users\Administrator\agent\docker-compose.monitoring.aliyun.yml` L40

**修改**：与 Step 5 完全相同：

```yaml
      - GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASSWORD:-admin}
```

---

## 四、功能验证（Step 7，20 min）

### 4.1 GlitchTip 修复验证

```powershell
# 1. 验证环境变量已注入容器
docker compose -f docker-compose.glitchtip.yml exec -T web env | Select-String "GLITCHTIP_ADMIN"

# 2. 重新执行 orm_setup_inline.py（应在容器内）
docker compose -f docker-compose.glitchtip.yml exec -T web python manage.py shell < docker/glitchtip/orm_setup_inline.py

# 3. 预期输出（成功）
# {"trace_id": "setup-...", "module_name": "orm_setup_inline", "action": "complete", "result": "success", ...}

# 4. 预期输出（失败 - 环境变量未设置）
# {"trace_id": "setup-...", "module_name": "orm_setup_inline", "action": "error", "result": "failure", "error_type": "ConfigError", ...}
```

### 4.2 Grafana 修复验证

```powershell
# 1. 验证 _import_dashboards.py 在缺失密码时正确退出
$env:GRAFANA_ADMIN_PASSWORD = ""
python scripts\_import_dashboards.py
# 预期: ERROR: GRAFANA_ADMIN_PASSWORD 环境变量未设置，无法导入仪表盘

# 2. 验证设置密码后能正常调用
$env:GRAFANA_ADMIN_PASSWORD = "your-real-password"
python scripts\_import_dashboards.py
# 预期: 正常导入仪表盘或返回 Grafana API 响应
```

### 4.3 docker-compose 配置验证

```powershell
# 1. 验证 compose 配置语法
docker compose -f docker-compose.monitoring.yml config

# 2. 验证变量插值结果
docker compose -f docker-compose.monitoring.yml config | Select-String "GF_SECURITY_ADMIN_PASSWORD"
# 预期: GF_SECURITY_ADMIN_PASSWORD=<.env 中的真实值>
```

---

## 五、回归测试（Step 8，15 min）

### 5.1 静态检查

```powershell
# 1. Python 语法检查
python -m py_compile docker/glitchtip/orm_setup_inline.py
python -m py_compile scripts/_import_dashboards.py

# 2. 全局硬编码密码扫描（应为 0）
git grep -n "admin123" -- '*.py' '*.yml' '*.yaml'
git grep -n "REMOVED_GLITCHTIP_PWD" -- '*.py' '*.yml' '*.yaml'
git grep -nE "password\s*=\s*['\"]" -- '*.py'
```

### 5.2 单元测试

```powershell
# 跑相关测试套件
python -m pytest tests/test_glitchtip_setup.py -v 2>$null
python -m pytest tests/test_monitoring.py -v 2>$null

# 若无专项测试，至少跑全量 smoke
python -m pytest tests/ -x --timeout=60
```

### 5.3 安全扫描

```powershell
# 1. 使用 detect-secrets 或 trufflehog 扫描
detect-secrets scan docker/glitchtip/orm_setup_inline.py
detect-secrets scan scripts/_import_dashboards.py

# 2. 预期：无 HIGH/CRITICAL 级别硬编码密码告警
```

---

## 六、提交变更（Step 9，5 min）

```powershell
# 1. 暂存修改
git add docker/glitchtip/orm_setup_inline.py
git add scripts/_import_dashboards.py
git add docker-compose.monitoring.yml
git add docker-compose.monitoring.aliyun.yml
git add .env.example
# 注意：.env 不提交（已在 .gitignore）

# 2. 提交
git commit -m "fix(security): P1 移除工作区硬编码密码，改用环境变量注入

- docker/glitchtip/orm_setup_inline.py: 密码改用 GLITCHTIP_ADMIN_PASSWORD 环境变量，缺失即 sys.exit(1)
- scripts/_import_dashboards.py: 密码改用 GRAFANA_ADMIN_PASSWORD 环境变量
- docker-compose.monitoring.yml: GF_SECURITY_ADMIN_PASSWORD 改用变量插值
- docker-compose.monitoring.aliyun.yml: 同上
- .env.example: 新增 GLITCHTIP_ADMIN_PASSWORD / GRAFANA_ADMIN_PASSWORD 示例

关联文档：docs/security/P1_HARDCODED_PASSWORD_FIX_PLAN_20260720.md"

# 3. 推送
git push origin master
git push gitee master
```

---

## 七、风险评估

| 风险点 | 等级 | 缓解措施 |
|--------|------|---------|
| `.env` 中密码为空导致容器启动失败 | 中 | Step 1 已要求部署前填写；缺失时 `sys.exit(1)` 主动报错 |
| 已部署 GlitchTip 实例密码变更后无法登录旧数据 | 中 | 部署前需在 GlitchTip Web 修改原密码与新 `.env` 一致 |
| docker-compose 变量插值在旧版 docker 不支持 | 低 | docker-compose v1.27+ 已支持 `${VAR:-default}`，要求文档已说明 |
| `.env` 误提交到 git | 高 | 已在 `.gitignore` 中排除 `.env`，仅提交 `.env.example` |
| 团队成员未拉取新代码导致密码失效 | 中 | 需通知协作者重新 clone 并配置本地 `.env` |

---

## 八、验证清单（执行后勾选）

- [ ] `.env` 中 `GLITCHTIP_ADMIN_PASSWORD` 已设置（非空，长度 >= 12）
- [ ] `.env` 中 `GRAFANA_ADMIN_PASSWORD` 已设置（非空，长度 >= 12）
- [ ] `docker/glitchtip/orm_setup_inline.py` L52 不再含 `***REMOVED_GLITCHTIP_PWD***`
- [ ] `scripts/_import_dashboards.py` L8 不再含 `admin123`
- [ ] `docker-compose.monitoring.yml` L28 不再含 `admin123` 硬编码
- [ ] `docker-compose.monitoring.aliyun.yml` L40 不再含 `admin123` 硬编码
- [ ] `git grep -n "admin123"` 在已跟踪文件中返回 0 行
- [ ] `git grep -n "REMOVED_GLITCHTIP_PWD"` 返回 0 行
- [ ] GlitchTip 容器能正常启动并完成 ORM 初始化
- [ ] Grafana 仪表盘导入脚本能正常调用 API
- [ ] `detect-secrets scan` 无 HIGH/CRITICAL 级别告警
- [ ] 变更已 commit 并 push 到 origin + gitee

---

## 九、三义校验

- **【不易】** 安全不变量锁定：所有密码必须从环境变量读取，禁止硬编码；`.env` 不进 git；`.env.example` 仅含占位符。修复范围 6 个文件全部按此约束设计
- **【变易】** 按需兜底：`GRAFANA_ADMIN_USER` 保留 `admin` 默认值（非敏感）；`${VAR:-admin}` 在本地开发场景兜底；`GLITCHTIP_ADMIN_PASSWORD` 无默认值（强约束）
- **【简易】** 9 步线性流程，每步明确耗时与验证命令；初中级工程师 30s 可读；表格化呈现修复范围

---

## 十、时间表

| 阶段 | 步骤 | 累计耗时 |
|------|------|---------|
| 准备 | Step 1-2 | 7 min |
| 代码修复 | Step 3-6 | 25 min |
| 验证 | Step 7-8 | 35 min |
| 提交 | Step 9 | 40 min |
| 缓冲 | 异常处理 | 72 min |

---

> **执行人**：Yi-Jing Coding Agent
> **审核状态**：待用户审核与执行
> **下一步**：用户确认后按 Step 1-9 顺序执行