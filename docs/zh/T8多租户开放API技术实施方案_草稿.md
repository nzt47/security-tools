# T8：多租户开放 API 技术实施方案（草稿 · 待确认）

> 状态：**草稿**，供评审确认。落地条件：出现真实的多租户/对外开放需求。本方案基于现状能力（ApiGateway 的 Key/Scope/限流/配额 + [multi_tenant.py](file:///c:/Users/Administrator/agent/agent/multi_tenant.py) 现成 TenantManager）分阶段演进，可随时中止。

## 一、现状盘点（已有基础）

| 能力 | 现状 | 缺口 |
|---|---|---|
| API Key 认证 | ApiKeyManager：64 位 Key、user_id、enable/disable、scope | Key 未绑定租户/角色 |
| 限流 | RateLimiter 多级令牌桶（全局→接口→用户→并发） | 无租户级聚合限额 |
| 配额 | QuotaManager（user_id/类型/周期） | 未接入租户 |
| 租户模型 | [TenantManager](file:///c:/Users/Administrator/agent/agent/multi_tenant.py#L80)：Tenant/User/RoleAssignment/PermissionType，RLock 线程安全 | **未接入 HTTP/网关** |
| 文档 | /api/docs 全量 292 路径 | 无开放/内部标记 |
| 内部认证 | FLASK_API_TOKEN（单一共享） | 与开放 API 双轨并存 |

## 二、目标

把 `/api/open/*` 从"单机演示"升级为**多租户开放平台**：租户 → 用户 → API Key → scope/角色 → 配额/限流 → 审计全链路闭环，同时内部 API（FLASK_API_TOKEN）保持不变。

## 三、分阶段实施方案

### T8.1 租户管理 API（HTTP 化 TenantManager）

- **改动**：`agent/multi_tenant.py` 暴露 `get_tenant_manager()` 单例；新增路由模块 `agent/server_routes/routes_tenants.py`（register_routes，state 未用）
- **接口**（前缀 `/api/open/tenants`，内部管理）：
  - `POST /api/open/tenants` 创建租户（type/parent_id/limits）
  - `GET /api/open/tenants` 列表；`GET /api/open/tenants/<id>` 详情
  - `POST /api/open/tenants/<id>/users` 创建用户；`POST .../users/<uid>/roles` 分配角色
  - `DELETE /api/open/tenants/<id>` 停用（软删）
- **验收**：CRUD 200；并发创建不炸（RLock 已保证）
- **风险**：低（复用现成模型）

### T8.2 网关认证升级（Key ↔ 租户/角色绑定）

- **改动**：`agent/api_gateway.py`
  - `ApiKeyManager.create_key` 增加 `tenant_id` / `role` 字段
  - `authenticate()` 通过后由 `TenantManager` 解析租户状态（enabled？quota？）
  - `check_scopes` 从"Key 自带 scope"升级为"角色权限表"（PermissionType 与 ApiGateway scope 映射）
- **验收**：禁用租户的 Key 一律 403；角色变化即时生效
- **风险**：中（认证链路改动，需回归现有 Key 流程 + test_api_gateway.py）

### T8.3 租户级配额/限流隔离

- **改动**：`agent/api_gateway.py` + `agent/multi_tenant.py`
  - QuotaManager 增加 `tenant_id` 维度（`set_quota(tenant_id, 'api_calls', ...)`，按租户聚合）
  - RateLimiter 用户桶 key 改为 `tenant:{id}:user:{uid}`（租户内独立限额 + 租户间隔离）
  - 配额耗尽/限流触发返回 429 + 租户标识
- **验收**：租户 A 打满不影响租户 B；单租户内用户共享租户配额
- **风险**：中（配额 key 变更影响现有 anonymous 语义，需兼容）

### T8.4 内部端点逐批开放（可选迁移）

- **目标**：把内部 API（/api/*）按需暴露为开放端点
- **做法**：`_scan_internal_routes` 已把全部内部路由登记进网关文档；开放时对目标端点调用 `gw.register_endpoint(auth_required=True, scopes=[...])` 并改 before_request 白名单
- **范围建议**：只开放只读/低危端点（/api/news、/api/knowledge/cards GET、/api/search...），写操作保持 FLASK_API_TOKEN
- **验收**：开放端点带 Key 可访问、无 Key 401、scope 不足 403
- **风险**：高（影响面大），建议每批 3-5 个端点灰度

## 四、涉及文件

| 文件 | 阶段 |
|---|---|
| `agent/multi_tenant.py`（新增单例入口/权限映射） | T8.1/T8.2/T8.3 |
| `agent/server_routes/routes_tenants.py`（新增） | T8.1 |
| `agent/api_gateway.py`（Key 扩展/租户解析/配额 key） | T8.2/T8.3 |
| `agent/api_gateway_flask.py`（白名单扩展/管理端点） | T8.2/T8.4 |
| `app_server.py`（接线 routes_tenants） | T8.1 |
| `tests/unit/test_api_gateway.py` + 新增 tenant 测试 | 各阶段 |

## 五、决策点（需确认）

1. **开放范围**：仅 `/api/open/*` 新增开放端点，还是把内部只读端点逐步开放（T8.4）？
2. **认证迁移**：内部 API 是否长期保留 FLASK_API_TOKEN 双轨，还是分批迁入网关（建议：保留，降低风险）？
3. **配额默认值**：租户默认配额（建议 10k 次/日，可配置覆盖）？
4. **阶段推进**：是否按 T8.1 → T8.4 顺序执行，还是只做 T8.1+T8.2（最小可用）？

## 六、验收总纲

- 全链路：创建租户 → 建用户 → 分配角色 → 生成 Key → 带 Key 调开放端点 → 配额耗尽 429 → 禁用租户 403
- 回归：现有 `/api/open/echo`、`/api/open/keys`、CLI `yunshu-gateway-check` 4/4 不回归
- 并发：多租户并发请求无锁竞争/配额超卖（复用 RLock + 双检锁模式）
