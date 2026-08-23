# 云枢 T8 网关 Docker Compose 部署

一键拉起完整环境（网关 + 多租户 + 监控端点），数据持久化于命名卷。

## 前置

- Docker Engine ≥ 24 + Docker Compose v2
- 端口 5678 空闲（可用 `YUNSHU_PORT` 覆盖）
- **首次构建较慢**：镜像含 torch（CPU 版）、chromadb、opencv 等 AI 依赖，预计 10–20 分钟（视网络）

## 快速开始

```bash
# 1) 构建并启动（INIT_TENANT=1 时启动前自动执行租户初始化，幂等）
cd deploy/t8-gateway
docker compose up -d --build
INIT_TENANT=1 docker compose up -d   # 追加初始化示例

# 2) 等待健康检查通过（start_period 90s）
docker compose ps
# yunshu-t8-gateway   Up (healthy)

# 3) 验证网关
curl http://127.0.0.1:5678/api/open/echo                 # {"ok":true,...}
curl http://127.0.0.1:5678/api/docs | head -c 200         # Swagger

# 4) 查看生成的令牌 / API Key（容器内 .env，单次明文）
docker compose exec yunshu-t8-gateway cat .env
```

## 配置

| 项 | 方式 | 说明 |
|---|---|---|
| 服务端口 | `YUNSHU_PORT=8080 docker compose up -d` | 默认 5678 |
| 租户初始化 | `INIT_TENANT=1`（environment） | 启动前跑 `deploy_t8_gateway.py --skip-env`（幂等：租户/Key 查重复用） |
| 数据持久化 | 命名卷 `yunshu_data:/app/data` | 租户/Key/审计日志（`data/audit/audit_*.jsonl`） |
| 密钥 | 容器内 `/app/.env`（entrypoint 自动生成 `FLASK_API_TOKEN`） | 写操作需 `FLASK_API_TOKEN` 环境变量导出 |
| 资源限制 | compose `deploy.resources.limits.memory: 2g` | 可按需调整 |

## 运维命令

```bash
# 日志（含网关挂载与 T8.4 开放端点日志）
docker compose logs -f yunshu-t8-gateway

# 模拟 401/403/429（在宿主机对映射端口执行）
python scripts/simulate_gateway_errors.py --base-url http://127.0.0.1:5678

# 停止 / 移除
docker compose down          # 保留数据卷
docker compose down -v       # 连同数据卷一并删除（慎用）
```

## 关联

- 部署脚本：`scripts/deploy_t8_gateway.py`
- 运维手册：`docs/zh/云枢T8多租户运维部署手册_20260816.md`
- 故障演练：`docs/zh/T8故障演练剧本_20260816.md`
