# src/utils 工具索引

> 通用工具函数统一收口目录（与 `src/lib/` 分工：`lib` = 领域/跨层模块如 sse/mosaic/promptFactors，`utils` = 通用工具）。新增工具先在此登记。

## storage（`src/utils/storage.ts`）

统一浏览器存储封装。设计要点：**不自动加前缀**（既有契约键 `token` / `yunshu-theme` 为无前缀裸键，守卫/拦截器直接读取）；新键统一 `yunshu:` 命名并先登记到 `STORAGE_KEYS`。

```ts
import { storage, STORAGE_KEYS } from '@/utils/storage'
storage.setRaw(STORAGE_KEYS.TOKEN, token)        // 原样字符串（token 用）
storage.getRaw(STORAGE_KEYS.TOKEN)                // string | null
storage.setJSON(key, { a: 1 })                    // 结构化数据
storage.getJSON(key, fallback)                    // 损坏 JSON 回退 fallback，不抛异常
storage.remove(key) / storage.has(key)
```

## async（`src/utils/async.ts`）

防抖 / 节流 / 取消辅助。

```ts
import { debounce, throttle, isAbortError, abortable } from '@/utils/async'
const d = debounce(onSearch, 300); d.cancel()     // 搜索框防抖
const t = throttle(broadcast, 150); t.cancel()    // 广播节流
isAbortError(err)                                 // 区分"主动取消"与"失败"
await abortable(promise, signal)                  // 中止时 reject AbortError
```

## format（`src/utils/format.ts`）

零依赖纯函数格式化。

```ts
import { formatDate, formatBytes, formatNumber } from '@/utils/format'
formatDate(Date.now())            // 'YYYY-MM-DD HH:mm:ss'（支持 /YYYY/MM/DD/HH/mm/ss 占位）
formatBytes(1048576)              // '1 MB'
formatNumber(1234567.891)         // '1,234,567.891'
```

## clipboard（`src/utils/clipboard.ts`）

统一复制入口（clipboard API 优先，textarea 降级）。

```ts
import { copyText } from '@/utils/clipboard'
const ok = await copyText(text)   // boolean，失败由调用方提示
```

## 既有工具

| 文件 | 说明 |
|---|---|
| `cn.ts` | Tailwind 类名合并（`@/lib/cn`，本目录仅登记说明） |
| `logger.ts` | 极简 Logger（`VITE_LOG_LEVEL` 控制，前缀 `[yunshu]`） |
| `request.ts` | Axios 封装（拦截器 / 401 登出 / 解包 / perf 日志） |
| `system.ts` | 系统级能力（`downloadFile`，Electron 迁移预留点） |
