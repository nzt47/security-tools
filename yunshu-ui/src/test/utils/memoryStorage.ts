/**
 * 内存版 Storage 实现（测试工具）
 * ------------------------------------------------------
 * 背景：Node 22 实验性 Web Storage（--localstorage-file 无有效路径时）会注入一个
 * 缺 clear 等方法的 localStorage stub，覆盖 jsdom 的原生实现，导致 persist 相关
 * 测试失败。本模块提供语义完整的 Storage 供测试环境复用。
 *
 * 用法：setup.ts 中在 localStorage 不可用时替换为 createMemoryStorage()；
 * 其他需要独立存储实例的测试（多窗口 / 隔离场景）也可直接复用。
 */
export function createMemoryStorage(): Storage {
  const store = new Map<string, string>()
  return {
    get length() {
      return store.size
    },
    clear: () => store.clear(),
    getItem: (key: string) => store.get(key) ?? null,
    key: (index: number) => [...store.keys()][index] ?? null,
    removeItem: (key: string) => {
      store.delete(key)
    },
    setItem: (key: string, value: string) => {
      store.set(key, String(value))
    },
  } as Storage
}
