#!/bin/sh
# 云枢 T8 网关容器入口：确保 .env 配置 → 可选租户初始化 → 启动 app_server
set -e
cd /app

# 1) .env 单一数据源：缺失则生成（守 user_rules「配置走 .env」）
if [ ! -f .env ]; then
    echo "FLASK_API_TOKEN=$(python -c 'import secrets;print(secrets.token_hex(32))')" > .env
    chmod 600 .env
    echo "[entrypoint] 已生成 .env（FLASK_API_TOKEN）"
else
    echo "[entrypoint] .env 已存在，复用"
fi

# 2) 历史审计日志租户归属补全（幂等：已含 tenant_id 的记录不变；--apply --yes 非交互）
#    Why：T8 数据隔离修复后，修复前写入的历史记录无 tenant_id，容器挂载 data 卷
#    启动时自动补全为 system 归属（自动备份 .bak_*），保证隔离语义从启动即生效。
if [ -d data/audit ] && ls data/audit/audit_*.jsonl >/dev/null 2>&1; then
    echo "[entrypoint] 历史审计日志租户归属补全（幂等）..."
    python scripts/migrate_audit_logs_tenant.py --apply --yes \
        || echo "[entrypoint] 审计迁移未完成（服务仍将启动）"
fi

# 3) 可选租户初始化（INIT_TENANT=1 时执行一键部署脚本，幂等）
if [ "${INIT_TENANT:-0}" = "1" ]; then
    echo "[entrypoint] 执行租户初始化（deploy_t8_gateway.py）..."
    python scripts/deploy_t8_gateway.py --skip-env || echo "[entrypoint] 初始化未完成（服务仍将启动）"
fi

echo "[entrypoint] 启动 app_server..."
exec "$@"
