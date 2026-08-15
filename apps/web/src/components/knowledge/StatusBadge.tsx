import { LoaderCircle } from 'lucide-react'
import type { KnowledgeDocumentStatus } from '../../types'
import { Badge, cn } from '../ui'

const STATUS_TEXT: Record<KnowledgeDocumentStatus, string> = {
  uploaded: '待解析',
  pending: '排队中',
  parsing: '解析中',
  syncing: '入库中',
  ready: '可检索',
  failed: '失败',
}

const STATUS_TONE: Record<KnowledgeDocumentStatus, string> = {
  uploaded: 'bg-amber-50 text-amber-700',
  pending: 'bg-zinc-100 text-zinc-600',
  parsing: 'bg-sky-50 text-info',
  syncing: 'bg-sky-50 text-info',
  ready: 'bg-emerald-50 text-success',
  failed: 'bg-red-50 text-danger',
}

export function StatusBadge({ status, className }: { status: KnowledgeDocumentStatus; className?: string }) {
  const active = status === 'parsing' || status === 'syncing'
  return (
    <Badge className={cn('gap-1', STATUS_TONE[status], className)}>
      {active && <LoaderCircle size={12} className="animate-spin" />}
      {STATUS_TEXT[status]}
    </Badge>
  )
}
