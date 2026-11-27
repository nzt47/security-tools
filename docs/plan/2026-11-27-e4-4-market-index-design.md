# E4-4 设计：扩展市场索引 v1 + 官方推荐位/bundle（2026-11-27）

> 状态：**已实现**（2026-11-27，提交 939c9d8f5）。本文件只回答「索引是什么形状、抓取边界在哪、
> 推荐位为什么默认关、bundle 为什么不新增路由/工具」，实现细节见代码与测试。

## 0. 现状（实现前的只读差距分析，逐条可核）

| 事实 | 位置 |
|---|---|
| 索引形态只有一句注释（复数键），代码按**单数**键比较 ⇒ 社区条目整类被过滤 | `market.py:29,110,258` |
| 远程索引 URL 硬编码；抓取是裸 `urlopen`（未过 `ssrf_guard`、跟随 3xx、无字节上限） | `market.py:30,59-64` |
| 缓存落 `agent/data/`（不在 `data/` 忽略规则内） | `market.py:33` |
| 推荐算法已有、前端零调用（只留 endpoint 常量） | `market.py:225-274` / `endpoints.ts:135` |
| 安装双闸 + 验签 | `plugins/skills.py:608-615` / `plugins_installer.py:149-175,271` |
| 市场抽屉（唯一安装入口） | `plugin-manage.tsx:596-651` |

## 1. 索引契约（schema v1）

```json
{
  "schema_version": 1,
  "plugins": [{"id": "p1", "name": "…", "description": "…", "source": "github:o/r",
                "tags": ["ui"], "stars": 12, "featured": true}],
  "skills": [], "mcp": [], "channels": [],
  "bundles": [{"id": "starter", "name": "入门包", "featured": true,
               "items": ["p1", "p2"]}]
}
```

- **单/复数类型键、扁平 `items` 三种形状都接受**，归一后只有一种（`market_index.normalize_index`）。
- 条目字段白名单：`id/name/description/tags/stars/source/featured/homepage/author/version`；
  `command/args/deps` 等安装参数**一律丢弃**（否则远程索引可直达成安装器参数 = 投毒面）。
- `source` 只允许 `github:` / `url:` / `local:` / `path:` / `builtin:` 前缀；不合法整条丢弃。
- **bundle 只允许 plugin**：mcp/skill/channel 的安装会走 pip/npm 或起进程，绕过既有验签边界；
  未知引用过滤、空 bundle 丢弃。
- 上限：索引 1 MiB / 条目 512 / 每 bundle 50 项 / id 128 字符；重名按 `(type,id)` 去重。

## 2. 抓取与缓存（外部输入按不可信处理）

| 边界 | 做法 |
|---|---|
| 地址可配 | `CP_EXTENSION_MARKET_INDEX_URL`（默认保持仓库原地址；空 = 只用内置 + 本地缓存） |
| 只允许 https | 非 https 直接拒，不建连接 |
| SSRF | 每次抓取调 `guardrails.ssrf_guard.check_url`；守卫不可用 ⇒ fail-closed |
| 重定向 | `_NoRedirect`：3xx 直接抛错，不跟随（避免把索引 URL 当跳板） |
| 字节上限 | 读 `MAX_INDEX_BYTES + 1`，超限即拒（不截断解析） |
| 缓存 | 仓库根 `data/extension_market_index.json`（已 gitignore、原子写、坏缓存按无缓存） |
| 日志 | 只记类型名与条数；URL 里的 query/token 不进日志 |

## 3. 推荐位与 bundle（默认关，不新增路由/工具）

- 开关 `CP_EXTENSION_MARKET_RECOMMEND_ENABLED`（**默认关**）：关时 `search_all` 的
  `featured/bundles` 为空、抽屉不渲染推荐区；`get_recommendations` 只在 `force=True` 时返回。
- 官方推荐位 = 索引里 `featured: true` 的条目（先 featured、再内置、再 stars 降序）。
- bundle 安装 = **前端串行** N 次既有 `POST /api/extensions/install`：`{type:'plugin', source: id-or-source}`。
  不新增路由、不新增工具 ⇒ 不触发路由覆盖率/计数契约派生；签名、白名单、双闸全在安装点，逐项生效。
- 局部失败如实汇报（成功 N 项 / 失败列表），不做“整体回滚”（安装本身没有事务语义，谎报原子性更危险）。

## 4. 回滚

- 关推荐位：`CP_EXTENSION_MARKET_RECOMMEND_ENABLED=0`（默认）；
- 关远程索引：`CP_EXTENSION_MARKET_INDEX_URL=`（空）⇒ 只用内置 + 本地缓存；
- 索引缓存坏了：删 `data/extension_market_index.json`（运行期状态，无可回版本）。

## 5. 判据（实现完成时逐条验）

1. 单/复数/扁平三种形状归一后逐条相同；复数键不再整类消失。
2. 非法 source / 安装参数字段 / 非 plugin bundle 引用被丢弃且计数。
3. 非 https、SSRF 拒绝、超 1MiB、schema_version 不符 ⇒ 全部回 None 且不写缓存。
4. 推荐位默认关：search 的 featured/bundles 为空、抽屉无推荐区；开 env 后 featured 置顶。
5. bundle 一键装：前端串行 N 次 install，body 与单项安装逐字一致；失败列表上屏。
6. `scan_settings --check` 零缺口（两个新 env）；干净 worktree 全量 pytest 绿；`npm run ui:check` 绿。