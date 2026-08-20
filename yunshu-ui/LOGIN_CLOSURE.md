# 登录功能结案报告

> 结案时间：2026-08-18
> 模块：`yunshu-ui`（React 18 + TypeScript + Tailwind CSS + Zustand + axios）
> 结案状态：✅ 全部需求闭环，测试通过，遗留已处理

---

## 一、需求交付清单

| # | 需求 | 实现 | 验证 |
|---|---|---|---|
| 1 | 登录页面开发（居中卡片、Tailwind 手写样式、无 UI 库） | `src/pages/Login/index.tsx`（路由 `/login` 已接入） | ✅ |
| 2 | Loading 状态：请求中按钮禁用 + 转圈 | 按钮 `disabled` + spinner | ✅ |
| 3 | 登录成功：写入 Token（守卫/拦截器双通道）并跳转 | `saveToken`（localStorage `token`）+ `useUserStore.setToken`；跳回守卫来源路径或 `/` | ✅ |
| 4 | 登录失败：错误提示 | 统一走全局 Toast（见第 5 条） | ✅ |
| 5 | 错误提示统一为全局 Toast，风格一致 | 全局 axios 拦截器 `notify` → `toast.error/info`；前端校验直接 `toast.error`；**全项目零 `alert` 残留** | ✅ |
| 6 | 登录后用户信息正确同步全局 Store | 成功路径 `setUserInfo(data.user)`，`yunshu-user-store` 持久化（`partialize` 剔除敏感字段） | ✅ |
| 7 | 记住密码（localStorage） | 密码经 Web Crypto AES-GCM 加密后存储，不落明文；退出后自动解密回填 | ✅ |
| 8 | 本地 Mock 数据跑通登录流程 | 复用 `src/mocks/devMock.ts`（`VITE_MOCK_API=true` 时生效），账号 `admin/123456` | ✅ |

## 二、关键实现说明

### 1. 登录链路（src/pages/Login/index.tsx）
- 成功：`login(data)` → `saveToken`（守卫与 axios 拦截器读取的 localStorage `token`）→ `userStore.setToken/setUserInfo` → 记住密码处理 → `navigate(from ?? '/')`。
- 失败：接口错误由全局拦截器统一 `toast.error`（单一来源，避免双弹）；前端空表单校验直接 `toast.error('请输入用户名和密码')`。

### 2. 错误提示统一（src/utils/request.ts）
- `notify(message, level)` 已由 `alert` 改为 `toast.error/info`（复用 `src/components/Toaster.tsx`，入口 `main.tsx` 全局挂载）。

### 3. 记住密码加密存储（src/pages/Login/index.tsx）
- 存储键：`yunshu-remember-login`（`{ remember, username, passwordCipher }`）、`yunshu-remember-key`（AES-GCM raw 密钥）。
- 算法：AES-GCM-256，随机 12 字节 IV，密文 base64；密钥 `extractable=true` 导出持久化以便复用（仅本地存储，不外传）。
- 回填：页面加载异步解密，密钥丢失/数据损坏时仅回填用户名（容错降级）。
- 边界：`localStorage.clear()` 会连密钥一并清除（属预期）。

### 4. 全局 Store（src/store/userStore.ts，既有增强）
- `setToken` / `setUserInfo` 独立设置；`fetchUserInfo` 拉取当前用户；`logout` 同步清 token + userInfo + localStorage 凭证；`partialize` 持久化时剔除敏感字段（如 `phone`）。

## 三、验证记录

### 单元 / 集成测试（vitest，全部通过）
```
Test Files  2 passed (2)
     Tests  6 passed (6)
```
用例覆盖：登录成功 token 双写 + 来源跳转、默认跳首页、失败不写 token 不跳转、空表单 Toast 校验、记住密码密文保存（不含明文）。

### 浏览器实测（真实后端 + Mock 双环境）
| 场景 | 结果 |
|---|---|
| 无 token 守卫拦截 → `#/login` | PASS |
| 登录失败 → 右上角全局 Toast（`role=alert`、`fixed right-4 top-4`），无原生 alert、无内联错误条 | PASS |
| 登录成功 → 跳转 `#/` 仪表盘，token 正确写入 | PASS |
| Store 同步：`yunshu-user-store.userInfo` = admin / 管理员 / role=admin | PASS |
| 记住密码：密文存储（JSON 无明文 `123456`）、密钥持久化 | PASS |
| 退出登录 → 自动解密回填 admin/123456 + 勾选状态保留 | PASS |
| 取消勾选 → 登录后记录清除 | PASS |
| 登出/清空存储 → 守卫回跳登录页 | PASS |

## 四、安全处理

- **明文密码风险（已处理）**：原记住密码为明文存储，已升级为 Web Crypto AES-GCM-256 加密（密文 + 随机 IV）。
- 已知边界：密钥与密文同源存储于 localStorage，可防"静态泄露/复制"类风险；**无法抵御同源 XSS**。更高安全等级需后端会话机制或专用凭据存储，属后续演进项。

## 五、环境与配置状态

| 项 | 状态 |
|---|---|
| `.env.development` | `VITE_MOCK_API=false`（真实后端联调） |
| 后端 | `python app_server.py`（127.0.0.1:5678）运行中 |
| 前端 | `npm run dev`（http://localhost:5173/static/）运行中 |
| 测试账号 | `admin / 123456` |
| Mock 模式 | 需要时改 `VITE_MOCK_API=true` 并重启 dev server 即可 |

## 六、遗留与建议（非阻塞）

1. 记住密码的 XSS 边界（见第四节）——建议由产品决策是否需要升级。
2. 并发会话曾多次修改 `.env` / `request.ts` / 删除旧 `LoginPage.tsx`，建议后续开发前先同步分支状态。

## 七、涉及文件

- 新增：`src/pages/Login/index.tsx`
- 修改：`src/router/index.tsx`（登录页路由指向）、`src/utils/request.ts`（notify → Toast）、`src/mocks/devMock.ts`（mock 登录账号校验）、`.env.development`（联调开关）
- 删除：`src/pages/LoginPage.tsx`（旧临时登录页，已无引用）
- 测试：`src/pages/Login/index.test.tsx`（5 用例）、`src/store/authFlow.integration.test.ts`（1 用例）
