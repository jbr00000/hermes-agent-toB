import { File, FilePieChart, FileSpreadsheet, FileText, FileType } from 'lucide-react'
import { cn } from '../ui'

// 按扩展名映射图标与配色，一眼区分文档格式（tooltip 显示大写扩展名兜底）
const FORMAT_ICON: Record<string, { Icon: typeof File; tone: string }> = {
  '.pdf': { Icon: FileText, tone: 'text-red-500' },
  '.doc': { Icon: FileType, tone: 'text-blue-600' },
  '.docx': { Icon: FileType, tone: 'text-blue-600' },
  '.ppt': { Icon: FilePieChart, tone: 'text-orange-500' },
  '.pptx': { Icon: FilePieChart, tone: 'text-orange-500' },
  '.xls': { Icon: FileSpreadsheet, tone: 'text-emerald-600' },
  '.xlsx': { Icon: FileSpreadsheet, tone: 'text-emerald-600' },
  '.txt': { Icon: File, tone: 'text-zinc-400' },
  '.md': { Icon: File, tone: 'text-zinc-400' },
}

export function FormatIcon({ ext, size = 15, className }: { ext: string; size?: number; className?: string }) {
  const key = ext.toLowerCase()
  const { Icon, tone } = FORMAT_ICON[key] ?? { Icon: File, tone: 'text-zinc-400' }
  const label = key.startsWith('.') ? key.slice(1).toUpperCase() : key.toUpperCase()
  return (
    <span title={label || '文件'} className={cn('inline-flex shrink-0', className)}>
      <Icon size={size} className={tone} />
    </span>
  )
}
