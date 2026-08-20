# 测试环境部署手册（feat/continue-dev）

> 适用分支：`feat/continue-dev`
> 更新日期：2026-08-20
> 目标：将「登录 + 用户管理(M1) + 角色权限(M2) + 菜单与数据权限(M3) + 操作审计(M4)」前端与后端部署到测试环境

---

## 一、架构概览

```
浏览器 ──▶ Nginx/直接访问 ──▶ yunshu-ui/dist（React 静态资源，base=/static/）
                 │                    │（同域 /api 代理，或直连）
                 ▼                    ▼
          Flask app_server.py（127.0.0.1:5678，含 /api/auth、/api/user、/api/audit 等）
                 │
                 ▼
          Redis(fake) + SQLite（持久化）
```

- 前端：`yunshu-ui/`（React 18 + Vite + TS），生产构建产物可复制到 Flask 的 `static/` 与 `templates/` 同域部署（`npm run build:flask`）。
- 后端：仓库根 `app_server.py`（Flask），依赖 `agent/` 包与 `requirements.txt`。

## 二、前置条件

| 依赖 | 版本 | 说明 |
|---|---|---|
| Node.js | 18+（建议 20 LTS） | 前端构建 |
| Python | 3.10 ~ 3.12 | 后端运行 |
| Git | - | 拉取代码 |
| Redis | 任意（或使用项目 fake redis） | 后端依赖，默认 6379 |

> 注意：M2/M3/M4 的接口（角色/菜单/审计分页）目前由前端 devMock 提供完整实现，真实后端仅实现 `auth/login`、`user/*`、`audit/logs`（基础查询）。**真实后端补齐角色/菜单接口前，这些页面在测试环境需通过前端构建开关使用 mock 数据**（见 5.3）。

## 三、代码获取

```bash
# 1. 克隆仓库（若未克隆）
git clone <仓库地址> security-tools
cd security-tools

# 2. 切换并更新目标分支
git fetch origin
git checkout feat/continue-dev
git pull origin feat/continue-dev
```

## 四、后端部署

```bash
# 1. 创建虚拟环境并安装依赖（建议）
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate    # Linux/macOS
pip install -r requirements.txt

# 2. 配置环境变量（仓库根 .env，参考 .env.example）
#    YUNSHU_ADMIN_USERNAME=admin
#    YUNSHU_ADMIN_PASSWORD=<强密码>（测试账号密码，修改后需重启后端）

# 3. 启动后端（监听 127.0.0.1:5678）
python app_server.py
```

验证：

```bash
curl http://127.0.0.1:5678/api/health          # 返回传感器 JSON 即启动成功
curl -X POST http://127.0.0.1:5678/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"<密码>"}' # 返回 token 即登录接口正常
```

## 五、前端部署

### 5.1 依赖安装与构建

```bash
cd yunshu-ui
npm ci                      # 按 lockfile 安装，保证依赖一致
```

### 5.2 方式 A：Flask 同域部署（推荐，随 app_server 一起发布）

```bash
# 构建并将产物复制到 Flask static/ 与 templates/（自动完成）
npm run build:flask
```

构建产物位置：
- `yunshu-ui/dist/index.html` → `templates/yunshu.html`
- `yunshu-ui/dist/assets/*` → `static/assets/*`

随后后端直接提供页面：访问 `http://<测试机>:5678/yunshu.html`。

### 5.3 方式 B：独立部署（前端静态服务 + API 反向代理）

```bash
npm run build                # 产物在 yunshu-ui/dist/
```

> 架构要点：项目构建固定 `base=/static/`（见 vite.config.ts），即页面内资源路径为 `/static/assets/*`。
> 因此部署时需将 `dist/` 内容放入 Nginx web root 的 `static/` 子目录，并让 `/` 指向 `static/index.html`。

```bash
# 部署结构示例（web root = /srv/yunshu/web）
mkdir -p /srv/yunshu/web/static
cp -r yunshu-ui/dist/* /srv/yunshu/web/static/
```

```nginx
server {
    listen 80;
    server_name <测试环境域名或 IP>;

    # web root：其中 static/ 为前端构建产物（dist/* 已复制至此）
    root /srv/yunshu/web;

    # SPA 入口（HashRouter）：访问根路径返回 index.html
    location = / {
        try_files /static/index.html =404;
    }

    # 静态资源：/static/assets/* → web/static/assets/*
    location /static/ {
        try_files $uri =404;
    }

    # API 反向代理 → 后端 5678（proxy_pass 无 URI 后缀，保留 /api 前缀）
    location /api {
        proxy_pass http://127.0.0.1:5678;
        proxy_set_header Host $host;
        # 【Why】转发真实客户端 IP：操作审计（/api/audit/logs）依赖来源 IP 展示
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

> 说明：若部署位置与上述 web root 不同，只需同步调整 `root` 与 `location = /` 的路径即可；`/api` 代理与静态配置相互独立。

### 5.4 前端环境变量（构建前配置 `yunshu-ui/.env.production`）

| 变量 | 值 | 说明 |
|---|---|---|
| `VITE_MOCK_API` | `false` | 走真实后端（角色/菜单等未实现的接口会失败，见注意事项） |
| `VITE_API_BASE` | 留空 | 留空走同域 `/api` 相对路径（需 Nginx/Flask 代理） |
| `VITE_LOG_LEVEL` | `info` | 日志级别 |

> **注意事项（M2~M4 联调）**：真实后端当前未实现 `/api/role*`、`/api/menu*`、`/api/audit/logs` 分页参数。测试环境若需完整验证角色/菜单/审计页面，二选一：
> 1. 后端补齐上述接口（推荐，契约见 `yunshu-ui/src/api/role.ts`、`menu.ts`、`audit.ts`）；
> 2. 或前端构建时设 `VITE_MOCK_API=true`（devMock 兜底，仅本地验证用）。

## 六、启动与冒烟验证

1. 后端：`python app_server.py`（5678）→ `/api/health` 正常。
2. 前端：方式 A 访问 `/yunshu.html`；方式 B 访问 Nginx 站点。
3. 冒烟用例：

| # | 场景 | 预期 |
|---|---|---|
| 1 | 打开页面无 token | 自动跳转登录页 |
| 2 | admin/测试密码 登录 | 跳转仪表盘，侧边栏显示系统管理 |
| 3 | 系统管理 → 用户列表 | 列表/分页/搜索可用 |
| 4 | 系统管理 → 角色权限 | 角色列表 + 权限/数据范围弹窗（需接口支持） |
| 5 | 系统管理 → 菜单管理 | 菜单树展示（需接口支持） |
| 6 | 系统管理 → 操作审计 | 日志列表与筛选（需接口支持） |

## 七、回滚

```bash
git checkout <上一个稳定标签或 commit>
cd yunshu-ui && npm ci && npm run build:flask   # 重新构建
# 重启 app_server.py
```

## 八、常见问题

| 现象 | 排查 |
|---|---|
| 登录返回 500 | 后端未就绪/依赖 Redis 未启动；`/api/health` 确认后端存活 |
| 页面 404 / 资源加载失败 | 确认 `base=/static/` 与产物复制位置正确 |
| 登录仍返回 mock-token | 构建时 `VITE_MOCK_API` 仍为 true |
| 角色/菜单/审计页报错 | 真实后端未实现对应接口，参考 5.4 注意事项 |
| 端口冲突 | 前端 5173（dev）/后端 5678，检查占用后调整 |
