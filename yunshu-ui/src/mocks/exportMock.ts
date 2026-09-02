/**
 * 导出页大数据量 Mock（5000 条用户）
 * ------------------------------------------------------
 * 用途：本地验证大数据量导出的分片进度条与性能表现。
 * 启用：.env.development 中 VITE_EXPORT_LARGE_MOCK=true 时，导出页数据源切换为本 Mock
 *       （经 dev server 中间件 src/mocks/devMock.ts 的 /api/export/users 返回）。
 * 生成规则：确定性生成（无随机），便于测试断言与性能对比。
 * 注意：本文件只被 vite 插件（devMock.ts）引用，不会打进前端生产包。
 */
/** 导出 Mock 用户项（结构对齐 @/api/user 的 UserListItem；避免在 node 上下文的 vite 插件里引入 @/ 别名） */
interface ExportMockUser {
  id: number
  username: string
  email: string
  role: string
  /** 1 启用 / 0 禁用 */
  status: 0 | 1
  createdAt: string
}

/** 5000 条用户：覆盖分页/关键字/状态多样性，id 为 5 的倍数禁用，id%3===0 为 manager */
export const EXPORT_MOCK_USERS: ExportMockUser[] = Array.from({ length: 5000 }, (_, i) => {
  const id = i + 1
  return {
    id,
    username: id === 1 ? 'admin' : `user${String(id).padStart(4, '0')}`,
    email: `user${id}@yunshu.local`,
    role: id === 1 ? 'admin' : id % 3 === 0 ? 'manager' : 'user',
    status: (id % 5 === 0 ? 0 : 1) as 0 | 1,
    createdAt: `2026-0${(id % 9) + 1}-${String((id % 27) + 1).padStart(2, '0')} 10:30:00`,
  }
})
