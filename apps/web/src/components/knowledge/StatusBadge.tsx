import { LoaderCircle } from 'lucide-react'
import type { KnowledgeDocumentStatus } from '../../types'
import { Badge, cn } from '../ui'

// 构建流水线是两步：① 解析（MinerU 识别转 Markdown / 本地直读）② 入库（ES+Milvus 索引）。
// 进行中的两个状态分别标出步序，让用户知道当前卡在哪一步
const STATUS_TEXT: Record<KnowledgeDocumentStatus, string> = {
  uploaded: '待解析',
  pending: '排队中',
  parsing: '识别中 1/2',
  syncing: '入库中 2/2',
  ready: '可检索',
  failed: '失败',
}

const STATUS_TONE: Record<KnowledgeDocumentStatus, string> = {
  uploaded: 'bg-amber-50 text-amber-700',
  pending: 'bg-zinc-100 text-zinc-600',
  parsing: 'bg-sky-50 text-info',
  syncing: 'bg-violet-50 text-violet-600',
  ready: 'bg-emerald-50 text-success',
  failed: 'bg-red-50 text-danger',
}

const STATUS_TIP: Partial<Record<KnowledgeDocumentStatus, string>> = {
  parsing: '第 1 步（共 2 步）：MinerU 识别文档并转换为 Markdown',
  syncing: '第 2 步（共 2 步）：分块写入 ES + Milvus 检索索引',
}

export function StatusBadge({ status, className }: { status: KnowledgeDocumentStatus; className?: string }) {
  const active = status === 'parsing' || status === 'syncing'
  return (
    <Badge title={STATUS_TIP[status]} className={cn('gap-1', STATUS_TONE[status], className)}>
      {active && <LoaderCircle size={12} className="animate-spin" />}
      {STATUS_TEXT[status]}
    </Badge>
  )
}
