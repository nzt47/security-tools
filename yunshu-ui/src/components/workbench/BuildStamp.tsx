/**
 * BuildStamp —— 构建时间小徽标（2026-11-21 从顶栏移到导航栏页脚）
 * ------------------------------------------------------------------
 * 【Why】前端修复上线后，未刷新的页面会一直执行旧 bundle，看起来像"改了没用/还是老问题"。
 * 有构建戳即可一眼确认（悬浮显示完整 ISO 时间与强制刷新提示）。
 * 【为什么挪走】顶栏的"当前叶子"与叶子页自己的标题重复，去掉后顶栏只剩品牌/状态/工具；
 * 构建戳属于"应用身份"，与导航栏页脚的「云枢 · 统一工作台 v3」同处更自然（Owner 2026-11-21）。
 */

export default function BuildStamp() {
  const iso = typeof __YUNSHU_BUILD__ === 'string' ? __YUNSHU_BUILD__ : ''
  if (!iso) return null
  const d = new Date(iso)
  const label = Number.isNaN(d.getTime())
    ? iso
    : `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  return (
    <span className="wb-build-stamp" title={`当前页面构建于 ${iso}；若与最新构建不符请强制刷新（Ctrl+F5）`}>
      build {label}
    </span>
  )
}
