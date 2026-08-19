# 登录验证测试步骤

> 目标：验证「无 token 自动跳转登录页 → 真实登录成功 → 进入受保护页面 → 登出回跳」完整链路。
> 环境：Web 模式（http://localhost:5173/static/），后端 Flask（127.0.0.1:5678）。

## 前置条件

1. 启动后端：`python app_server.py`（依赖 fake redis，已在 6379 运行）
2. 启动前端：`npm run dev`（Vite 端口 5173）
3. 确认环境变量 `.env.development` 中 `VITE_MOCK_API=false`（关闭接口 mock，登录走真实后端）
4. 测试账号：`admin / 123456`
   - 配置在项目根目录 `.env`：`YUNSHU_ADMIN_USERNAME` / `YUNSHU_ADMIN_PASSWORD`
   - 修改后需**重启后端**生效

## 场景 1：无 token 自动跳转登录页

**手动触发无 token 状态（二选一）**

- 方式 A（清空浏览器存储）：
  1. 打开页面后按 `F12` → Console
  2. 执行 `localStorage.clear()`
  3. 刷新页面
- 方式 B（登出）：在已登录状态下点击右上角「退出登录」

**预期结果**

| 检查项 | 预期 |
|---|---|
| URL | 自动变为 `http://localhost:5173/static/#/login` |
| 页面 | 显示登录表单（云枢 / 用户名 / 密码 / 登录） |
| Console 日志 | `[route-guard] RequireAuth：未登录（localStorage 无 token）…重定向 /login`（warn） |

## 场景 2：真实登录成功

1. 在登录页输入用户名 `admin`、密码 `123456`
2. 点击「登录」
3. 等待约 1 秒（真实后端延迟）

**预期结果**

| 检查项 | 预期 |
|---|---|
| URL | `#/`（仪表盘），不再停留 `#/login` |
| 页面 | 显示「仪表盘」、统计卡片、「访问趋势」「用户角色分布」图表 |
| Console 日志 | `[auth] 登录成功，已写入 token，跳转 → /` |
| | `[route-guard] RequireAuth：已登录（token=…），放行 → /` |
| | `[route-guard] AuthRoute：权限校验通过 → /（authority="公开"，role="admin"）` |
| | `[chart] 访问趋势折线图 容器尺寸：…px`、`[chart] 用户角色分布饼图 容器尺寸：…px`（若容器尺寸为 0 会输出 warn） |

## 场景 3：登出回跳

1. 点击右上角「退出登录」

**预期结果**

| 检查项 | 预期 |
|---|---|
| URL | 回到 `#/login` |
| 存储 | `localStorage.getItem('token')` 为 `null` |

## 场景 4（可选）：权限守卫

- 登录 `admin` 后侧边栏可见「系统管理 / 用户列表」（`authority: 'admin'`）
- Console 无权限时输出：`[route-guard] AuthRoute：无权限访问 …（需 authority="…"）→ 重定向 /403`

## 日志前缀速查

| 前缀 | 含义 | 排查用途 |
|---|---|---|
| `[route-guard]` | 登录守卫 / 权限守卫 / 兜底路由跳转 | 跳转逻辑是否正确 |
| `[auth]` | 登录请求成功/失败 | 登录链路是否走真实后端 |
| `[chart]` | Dashboard 图表挂载 / 尺寸 / 卸载 | 排查 removeChild、图表宽高为 0 告警 |
| `[mosaic]` | Mosaic 面板拖拽开始/结束、布局变更、面板分离摘除 | 排查 removeChild 与面板生命周期问题 |

## 常见问题

| 现象 | 原因与处理 |
|---|---|
| 登录报「用户名或密码错误」 | 后端账号未配置或未重启：检查 `.env` 并重启 `app_server.py` |
| 登录仍返回 mock-token | `VITE_MOCK_API` 仍为 true：改为 false 后重启 `npm run dev` |
| 端口被占用 | 前端默认 5173、后端 5678、fake redis 6379，冲突时检查占用 |
| 无 token 却未跳转登录页 | 先 `localStorage.clear()` 并硬刷新（Ctrl+Shift+R） |
| 拖拽 Mosaic 面板时无 `[mosaic] 面板拖拽开始/结束` 日志 | Mosaic 走 react-dnd 的原生 HTML5 DnD（dragstart），脚本合成 mousedown/mousemove/mouseup 无法驱动该机制，**需人工按住面板标题栏拖拽**才能触发这两条日志。`[mosaic] 布局变更` 日志在拖拽/拆分/关闭面板时（onChange）始终正常输出，可作为生命周期观测的兜底 |
| Console 出现 `[chart] …容器尺寸为 0（…）` warn | 多为 React StrictMode 首次挂载瞬间「布局未完成」导致（随后 requestAnimationFrame 自动重读，会再输出一条正常尺寸日志，属预期行为）。若重读后**持续为 0**，查看 warn 中附带的 `display` / `offsetParent` 诊断：`display=none` 或 `offsetParent=null` 表示父容器未显示（被折叠/隐藏），需检查图表所在容器的布局与可见性 |
| 登录页停留时 Console 出现 `GET /user/info 401` | 正常：无 token 时若页面发起用户信息请求，axios 拦截器会 401 提示并清除凭证（不影响守卫跳转） |
