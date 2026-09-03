/**
 * 分类图标：种子分类/自动建类/未分类 → lucide 图标（技能中心两处共用）
 */
import {
  Brain, Code2, FileQuestion, FileText, Folder, Languages, Mail, Mic,
  Search, ShieldCheck, Smile, TrendingUp, Workflow,
} from 'lucide-react'

const MAP: Record<string, typeof Smile> = {
  '交流与人格': Smile,
  '记忆与知识': Brain,
  '安全与合规': ShieldCheck,
  '语音与多媒体': Mic,
  '邮件与通讯': Mail,
  '文档与办公': FileText,
  '代码与工程': Code2,
  '网络与搜索': Search,
  '数据分析与可视化': TrendingUp,
  '工作流与自动化': Workflow,
  '翻译与写作': Languages,
  '未分类': FileQuestion,
}

export default function ClassIcon({ name, size = 13, className }: { name?: string; size?: number; className?: string }) {
  const Icon = (name ? MAP[name] : undefined) ?? Folder
  return <Icon size={size} className={className} />
}
