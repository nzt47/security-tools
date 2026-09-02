# src/hooks 索引

> 业务 hooks 统一收口目录。新增 hook 先在此登记。

## useTablePage（`src/hooks/useTablePage.ts`）

列表页统一抽象：分页 + 搜索 + 加载 + 竞态防护。系统管理列表页（UserList / RoleList / AuditList / NotificationCenter）均使用。

```ts
import { useTablePage } from '@/hooks/useTablePage'

const { query, setQuery, list, total, loading, fetchList, handleSearch, handleReset, goPage } =
  useTablePage<Item, Query>({
    fetcher: getItemList,                    // 返回 { list, total }
    defaultQuery: { page: 1, pageSize: 10, keyword: '' },
    deps: [],                                // 外部依赖（如路由参数），变化时重置重拉
  })
```

要点：

- **竞态防护**：请求序号比对，过期响应丢弃；卸载后不 setState。
- **fetcher 经 ref 持有**：可传内联函数（如按参数分流的 lambda），不会引发无限重拉。
- **handleSearch(filters)**：合并筛选字段并重置页码为 1；**handleReset()**：回默认查询。
- **setList**：本地更新列表（消息中心"标记已读"等场景），不触发重拉。
- **goPage(target)**：跳页，越界自动钳制到最后一页。
- 错误提示由 `request.ts` 拦截器统一处理，hook 不重复弹窗。
