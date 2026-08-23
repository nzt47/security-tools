# 云枢 · 权限与系统日志导出接口（OpenAPI 文档）

> 来源：[system-log.openapi.yaml](../api/system-log.openapi.yaml)（OpenAPI 3.0.3）
> 用途：粘贴至项目 Wiki / 供前端后端对齐接口契约

## 鉴权与响应约定

- **鉴权**：除登录外，全部接口需携带 `Authorization: Bearer <token>`（token 由登录接口签发）
- **响应**：统一 `{code, data, message}`；`code=200` 成功；业务错误以 HTTP 200 + `code!=200` 返回（前端弹 Toast）；令牌失效返回 HTTP 401
- **权限码**：菜单级 `system:*:view`、操作级 `system:log:export`（admin 通配）

## 接口清单

| 方法 | 路径 | 说明 | 鉴权 | 权限 |
|---|---|---|---|---|
| POST | `/api/auth/login` | 管理后台登录（签发 token + 返回用户信息） | 无 | - |
| GET | `/api/auth/menus` | 下发当前用户可见菜单树（按角色/权限码过滤） | Bearer | 登录用户 |
| GET | `/api/system/logs` | 系统日志分页查询（方案 A：前端拉取数据源） | Bearer | 登录用户 |
| POST | `/api/system/log/export` | 导出系统日志 CSV（方案 B：大数据量后端导出） | Bearer | `system:log:export` |

## 接口详情

### 1. POST /api/auth/login

请求体：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| username | string | ✅ | 账号（admin / user / manager） |
| password | string | ✅ | 密码 |

响应：`code=200` 成功携带 `token` + `user`；`code=401` 账号或密码错误。

### 2. GET /api/auth/menus

无参数。响应 `data` 为菜单树数组，节点结构：

| 字段 | 类型 | 说明 |
|---|---|---|
| path | string | 路由路径（小写英文） |
| title | string | 菜单名 / 面包屑文案 |
| icon | string | 图标名（前端 MENU_ICON_MAP 映射） |
| authority | string | 权限码（后端已按角色过滤） |
| children | array | 子菜单 |

### 3. GET /api/system/logs

查询参数：

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| page | integer | 1 | 页码（≥1） |
| pageSize | integer | 10 | 每页条数（1~100） |
| keyword | string | - | 操作人 / 操作内容关键字模糊搜索 |

响应 `data`：`{total, list}`，`list` 项结构：

| 字段 | 类型 | 说明 |
|---|---|---|
| time | string | 操作时间，如 `2026-08-21 10:02:11` |
| operator | string | 操作人 |
| action | string | 操作内容 |
| result | string | 结果（成功等） |

### 4. POST /api/system/log/export

需操作级权限码 `system:log:export`（后端 `PermissionManager.has_permission` 校验，admin 通配）。
响应：`text/csv` 文件流（含 UTF-8 BOM，Excel 中文不乱码）；无权限返回业务错误 `code=403`。

## 完整 OpenAPI YAML

```yaml
openapi: 3.0.3
info:
  title: 云枢 · 权限与系统日志导出接口
  version: 1.2.0
  description: |
    权限与日志导出相关接口契约（对接真实后端日志数据的 OpenAPI 定义）。

    - 鉴权：除登录外，全部接口需携带 `Authorization: Bearer <token>`（token 由登录接口签发）。
    - 业务响应统一为 `{code, data, message}`：code=200 成功；业务错误以 HTTP 200 + code!=200 返回（前端弹 Toast）；令牌失效返回 HTTP 401。
    - 权限码契约：菜单级 `system:*:view`、操作级 `system:log:export`（admin 通配）。
  license:
    name: 云枢
servers:
  - url: http://127.0.0.1:5678
    description: 本地 Flask 后端

tags:
  - name: auth
    description: 认证与菜单下发
  - name: system-log
    description: 系统日志（查询 / 导出）

paths:
  /api/auth/login:
    post:
      tags: [auth]
      summary: 管理后台登录（签发 token + 返回用户信息）
      security: []
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [username, password]
              properties:
                username:
                  type: string
                  example: admin
                password:
                  type: string
                  example: "123456"
      responses:
        "200":
          description: 登录结果（code=200 成功并携带 token/user；code=401 账号或密码错误）
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/ApiResponse"

  /api/auth/menus:
    get:
      tags: [auth]
      summary: 下发当前用户可见菜单树（按角色/权限码过滤）
      security:
        - BearerAuth: []
      responses:
        "200":
          description: 菜单树（节点：path/title/icon/authority/children）
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/ApiResponse"

  /api/system/logs:
    get:
      tags: [system-log]
      summary: 系统日志分页查询（接入指南方案 A：前端拉取数据源）
      security:
        - BearerAuth: []
      parameters:
        - name: page
          in: query
          schema:
            type: integer
            minimum: 1
            default: 1
        - name: pageSize
          in: query
          schema:
            type: integer
            minimum: 1
            maximum: 100
            default: 10
        - name: keyword
          in: query
          schema:
            type: string
            description: 操作人 / 操作内容关键字模糊搜索
      responses:
        "200":
          description: 日志分页结果
          content:
            application/json:
              schema:
                allOf:
                  - $ref: "#/components/schemas/ApiResponse"
                  - type: object
                    properties:
                      data:
                        $ref: "#/components/schemas/LogListResult"

  /api/system/log/export:
    post:
      tags: [system-log]
      summary: 导出系统日志 CSV（接入指南方案 B：大数据量后端导出）
      description: 需要操作级权限码 system:log:export（PermissionManager.has_permission 校验，admin 通配）。
      security:
        - BearerAuth: []
      responses:
        "200":
          description: CSV 文件流（含 UTF-8 BOM，Excel 中文不乱码）
          content:
            text/csv:
              schema:
                type: string
                format: binary
        "403":
          description: 无导出权限（业务错误 HTTP 200 + code=403）

components:
  securitySchemes:
    BearerAuth:
      type: http
      scheme: bearer
      description: 登录接口返回的 token，格式 `Authorization: Bearer <token>`
  schemas:
    ApiResponse:
      type: object
      properties:
        code:
          type: integer
          example: 200
        data:
          nullable: true
        message:
          type: string
          example: success
    SystemLog:
      type: object
      required: [time, operator, action, result]
      properties:
        time:
          type: string
          example: "2026-08-21 10:02:11"
        operator:
          type: string
          example: admin
        action:
          type: string
          example: 登录
        result:
          type: string
          example: 成功
    LogListResult:
      type: object
      properties:
        total:
          type: integer
          example: 128
        list:
          type: array
          items:
            $ref: "#/components/schemas/SystemLog"
```
