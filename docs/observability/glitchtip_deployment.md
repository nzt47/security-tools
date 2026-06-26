# GlitchTip 自建错误追踪后端 — 部署指南

为云枢项目构建自建错误追踪后端（GlitchTip），替代 SaaS 版 Sentry，**零付费依赖**。
后端用 `sentry-sdk` 通过标准 Sentry DSN 协议上报，前端用 `@sentry/react` 同样对接。
GlitchTip 与 Sentry SDK 完全兼容，无需改动业务代码即可切换后端。

## 一、架构概览

```
┌──────────────────┐         DSN/HTTPS         ┌──────────────────────┐
│  前端 (yunshu-ui) │  ──────────────────────▶ │  GlitchTip (Nginx)   │
│  @sentry/react   │                            │   ↓                  │
└──────────────────┘                            │  Django (Web)       │
                                                 │   ↓                  │
┌──────────────────┐         DSN/HTTPS         │  PostgreSQL          │
│  后端 (agent)    │  ──────────────────────▶ │   +                  │
│  sentry-sdk      │                            │  Redis               │
└──────────────────┘                            │   +                  │
                                                 │  Worker (Celery)    │
                                                 └──────────────────────┘
```

| 组件 | 作用 | 镜像 |
|------|------|------|
| Nginx | 反向代理，TLS 终止 | `glitchtip/nginx:latest` |
| Django | Web API（接收 Sentry 事件） | `glitchtip/glitchtip:latest` |
| Worker | Celery 异步任务（邮件、索引） | `glitchtip/glitchtip:latest` |
| PostgreSQL | 事件存储 | `postgres:15` |
| Redis | Celery broker + 缓存 | `redis:7` |

## 二、前置要求

- Docker 20.10+ 与 Docker Compose v2+
- 单机最低配置：2 vCPU / 4 GB RAM / 20 GB 磁盘
- 已有域名 + TLS 证书（生产环境强烈建议）；本地开发可用 localhost

## 三、Docker Compose 部署

### 3.1 准备目录

```bash
mkdir -p /opt/glitchtip && cd /opt/glitchtip
```

### 3.2 创建 `docker-compose.yml`

```yaml
version: "3.8"

services:
  postgres:
    image: postgres:15
    container_name: glitchtip-postgres
    restart: unless-stopped
    environment:
      POSTGRES_DB: glitchtip
      POSTGRES_USER: glitchtip
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-change_me_in_prod}
    volumes:
      - postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U glitchtip"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7
    container_name: glitchtip-redis
    restart: unless-stopped
    command: redis-server --maxmemory 256mb --maxmemory-policy allkeys-lru
    volumes:
      - redis-data:/data

  web:
    image: glitchtip/glitchtip:latest
    container_name: glitchtip-web
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_started
    env_file:
      - .env
    ports:
      - "8000:8000"   # 生产建议不暴露，由 Nginx 反代

  worker:
    image: glitchtip/glitchtip:latest
    container_name: glitchtip-worker
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_started
    env_file:
      - .env
    command: ./bin/run-celery-with-beat.sh

volumes:
  postgres-data:
  redis-data:
```

### 3.3 创建 `.env`

```bash
# ─── Django 核心配置 ─────────────────────────────────────
SECRET_KEY=请替换为_50_位以上随机字符串
DJANGO_SETTINGS_MODULE=glitchtip.settings.production
ALLOWED_HOSTS=glitchtip.example.com,localhost

# ─── 数据库 ─────────────────────────────────────────────
DATABASE_URL=postgres://glitchtip:change_me_in_prod@postgres:5432/glitchtip
REDIS_URL=redis://redis:6379/0

# ─── 外部访问 URL（前端 SDK 用此构造 DSN） ───────────────
GLITCHTIP_DOMAIN=https://glitchtip.example.com
CSRF_TRUSTED_ORIGINS=https://glitchtip.example.com

# ─── 邮件（可选，错误告警邮件） ─────────────────────────
EMAIL_HOST=smtp.example.com
EMAIL_PORT=587
EMAIL_HOST_USER=postmaster@example.com
EMAIL_HOST_PASSWORD=your_smtp_password
EMAIL_USE_TLS=True
DEFAULT_FROM_EMAIL=GlitchTip <alerts@example.com>

# ─── Celery ─────────────────────────────────────────────
CELERY_WORKER_CONCURRENCY=2
CELERY_WORKER_MAX_TASKS_PER_CHILD=100
```

> **安全提示：** `.env` 文件权限设为 `600`，禁止提交到版本库。`SECRET_KEY` 与数据库密码务必替换。

### 3.4 启动服务并初始化

```bash
docker compose up -d

# 执行数据库迁移 + 创建管理员账号
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
```

访问 `http://localhost:8000` 或生产域名，使用超级管理员账号登录。

## 四、创建项目并获取 DSN

1. 登录 GlitchTip → 右上角 `+ New Project`
2. 选择平台：后端选 `Python`，前端选 `JavaScript`（或 `React`）
3. 创建后页面会展示 DSN，形如：
   ```
   https://public_key@glitchtip.example.com/project_id
   ```
4. 后端项目与前端项目分别创建（DSN 不同），便于隔离事件流。

## 五、云枢后端对接

### 5.1 注入 DSN

后端通过环境变量读取 DSN（见 [`agent/error_reporting_config.py`](file:///c:/Users/Administrator/agent/agent/error_reporting_config.py)）：

```bash
# /etc/systemd/system/yunshu.service 或 .env
SENTRY_DSN=https://public_key@glitchtip.example.com/1
SENTRY_ENVIRONMENT=production           # dev / staging / production
SENTRY_SAMPLE_RATE=1.0                  # 错误事件采样率（0~1）
SENTRY_TRACES_SAMPLE_RATE=0.0            # 性能追踪采样率（默认关闭，避免开销）
SENTRY_RELEASE=v1.2.3                   # 与 Git Tag 对齐，便于版本归因
SENTRY_SERVER_NAME=yunshu-node-01        # 节点标识
SENTRY_MIN_LEVEL=error                  # 最低上报级别
```

### 5.2 启动验证

```bash
# 验证 SDK 初始化
python -c "from agent.error_reporting_config import init_sentry, is_sentry_enabled; init_sentry(); print('enabled:', is_sentry_enabled())"

# 触发测试错误
curl -X POST http://localhost:5678/api/test/error -H "Content-Type: application/json" -d '{"msg":"glitchtip probe"}'
```

GlitchTip 后台应能在 5 秒内看到对应事件，且 `tags` 中包含 `trace_id`。

### 5.3 敏感信息过滤

SDK 在 `before_send` 钩子中过滤以下字段（见 [`_sentry_before_send`](file:///c:/Users/Administrator/agent/agent/error_reporting_config.py)）：

- `password`, `token`, `authorization`, `secret`, `api_key` / `api-key`（连字符与下划线等价）
- `id_card`, `bank_card`, `cvv`, `ssn`
- 任意匹配 `*_password`, `*_token` 后缀的字段

过滤后值替换为 `[REDACTED]`，原字段保留键名以便溯源。

## 六、云枢前端对接

### 6.1 注入 DSN

在 [`yunshu-ui/.env.production`](file:///c:/Users/Administrator/agent/yunshu-ui/.env.production) 中配置：

```bash
VITE_SENTRY_DSN=https://public_key@glitchtip.example.com/2
VITE_SENTRY_SAMPLE_RATE=0.1              # 生产环境错误采样率（避免洪流）
VITE_SENTRY_TRACES_SAMPLE_RATE=0          # 性能追踪采样率
VITE_REPLAY_SAMPLE_RATE=0.01              # rrweb 录制采样率（1%）
```

开发环境配置见 [`yunshu-ui/.env.development`](file:///c:/Users/Administrator/agent/yunshu-ui/.env.development)：
```bash
VITE_SENTRY_DSN=https://public_key@glitchtip.example.com/2
VITE_SENTRY_SAMPLE_RATE=1                 # 开发全量上报便于调试
VITE_SENTRY_TRACES_SAMPLE_RATE=0
VITE_REPLAY_SAMPLE_RATE=0.01
```

### 6.2 启动验证

```bash
cd yunshu-ui
npm install        # 安装 @sentry/react、rrweb、rrweb-player
npm run build      # 构建产物
npm run preview    # 本地预览
```

访问任意页面，控制台应输出 `[Sentry] 已初始化` 日志。
手动触发错误（在浏览器控制台执行 `throw new Error('glitchtip probe')`），GlitchTip 后台应在 5 秒内收到事件，且 `breadcrumbs` 中包含最近 10 个用户操作。

## 七、健康检查与监控

### 7.1 自带健康检查

```bash
curl http://glitchtip.example.com/api/0/organizations/
# 应返回 200 与组织信息
```

### 7.2 云枢侧状态探测

```bash
# 在云枢侧查看 Sentry 是否启用
curl http://localhost:5678/api/diagnostics/health | jq '.error_correlation.sentry_enabled'
# 应返回 true
```

### 7.3 资源监控建议

- PostgreSQL：开启 `pg_stat_statements`，监控慢查询
- Redis：监控 `used_memory`，超过 80% 触发告警
- 磁盘：GlitchTip 事件约 5~50 KB/条，按日均 1 万事件估算约 200 MB/天，建议预留 50 GB

## 八、备份与恢复

### 8.1 数据备份

```bash
# 备份 PostgreSQL
docker exec glitchtip-postgres pg_dump -U glitchtip glitchtip > backup_$(date +%Y%m%d).sql

# 备份 .env 配置
cp /opt/glitchtip/.env /backup/glitchtip.env.$(date +%Y%m%d)
```

建议每日备份，保留 30 天。

### 8.2 数据恢复

```bash
# 恢复 PostgreSQL
cat backup_20260626.sql | docker exec -i glitchtip-postgres psql -U glitchtip glitchtip

# 重启服务
docker compose restart web worker
```

## 九、故障排查

| 现象 | 排查步骤 |
|------|---------|
| SDK 初始化失败 | 检查 DSN 格式、网络连通性、GlitchTip 服务状态 |
| 事件未到达 | 检查 GlitchTip `nginx` 日志与 Django 日志：`docker compose logs web` |
| 事件丢失 tags/trace_id | 检查 `before_send` 钩子是否正确执行（后端日志含 `[Sentry]` 前缀） |
| 性能开销过高 | 降低 `SENTRY_SAMPLE_RATE` 到 0.1；关闭 `SENTRY_TRACES_SAMPLE_RATE` |
| 邮件告警未送达 | 检查 `.env` 中 SMTP 配置；查看 worker 日志：`docker compose logs worker` |

## 十、安全加固清单

- [ ] `.env` 文件权限 `600`，禁止提交到 Git
- [ ] 启用 HTTPS（Let's Encrypt 或自签证书）
- [ ] GlitchTip 管理后台启用 2FA
- [ ] PostgreSQL 密码为强随机字符串（≥ 32 位）
- [ ] 防火墙仅放行 80/443 端口，禁止 5432/6379 外网访问
- [ ] 定期轮换 `SECRET_KEY` 与数据库密码（建议每季度）
- [ ] 监控异常事件洪流，配置速率限制（GlitchTip 后台 → Project Settings → Rate Limit）

## 十一、参考资源

- GlitchTip 官方文档：https://glitchtip.com/documentation
- Sentry SDK 协议：https://develop.sentry.dev/sdk/overview
- 云枢错误上报配置：[`agent/error_reporting_config.py`](file:///c:/Users/Administrator/agent/agent/error_reporting_config.py)
- 云枢前端 Sentry 集成：[`yunshu-ui/src/utils/sentry.ts`](file:///c:/Users/Administrator/agent/yunshu-ui/src/utils/sentry.ts)
- 健康检查端点：`GET /api/diagnostics/health` → `error_correlation.sentry_enabled`
