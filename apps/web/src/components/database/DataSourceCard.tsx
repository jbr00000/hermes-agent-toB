import { Cable, Pencil, Trash2 } from 'lucide-react'
import type { DataSource, DbType } from '../../types'
import { Badge, cn } from '../ui'

const DB_TYPE_LABELS: Record<DbType, string> = {
  mysql: 'MySQL',
  postgresql: 'PostgreSQL',
  sqlite: 'SQLite',
}

const STATUS_STYLES: Record<DataSource['status'], { label: string; className: string }> = {
  connected: { label: '已连接', className: 'bg-emerald-50 text-emerald-700' },
  failed: { label: '连接失败', className: 'bg-red-50 text-danger' },
  untested: { label: '未测试', className: 'bg-field text-zinc-500' },
}

/** 数据源连接卡片（图1卡片墙的一项）：连接信息 + 状态徽章 + 测试/编辑/删除。 */
export function DataSourceCard({
  dataSource,
  testing,
  onTest,
  onEdit,
  onDelete,
}: {
  dataSource: DataSource
  testing: boolean
  onTest: () => void
  onEdit: () => void
  onDelete: () => void
}) {
  const status = STATUS_STYLES[dataSource.status]
  return (
    <div className="flex flex-col rounded-md border border-line bg-panel p-4 shadow-card">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold">{dataSource.name}</div>
          <div className="mt-0.5 text-xs text-zinc-500">{DB_TYPE_LABELS[dataSource.dbType]}</div>
        </div>
        <Badge className={cn('shrink-0', status.className)}>{status.label}</Badge>
      </div>

      <dl className="mt-3 space-y-1.5 text-xs text-zinc-600">
        <div className="flex justify-between gap-3">
          <dt className="shrink-0 text-zinc-400">主机地址</dt>
          <dd className="truncate font-mono">{dataSource.host}:{dataSource.port}</dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt className="shrink-0 text-zinc-400">数据库</dt>
          <dd className="truncate font-mono">{dataSource.database}</dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt className="shrink-0 text-zinc-400">用户名</dt>
          <dd className="truncate font-mono">{dataSource.username}</dd>
        </div>
      </dl>

      <div className="mt-4 flex items-center justify-between border-t border-line pt-3">
        <button
          className="flex h-7 items-center gap-1.5 rounded-md px-2 text-xs text-zinc-500 hover:bg-field hover:text-ink disabled:text-zinc-300"
          disabled={testing}
          onClick={onTest}
        >
          <Cable size={13} className={cn(testing && 'animate-pulse')} />
          {testing ? '测试中…' : '测试连接'}
        </button>
        <div className="flex items-center gap-1">
          <button
            title="编辑连接"
            className="flex h-7 w-7 items-center justify-center rounded-md text-zinc-500 hover:bg-field hover:text-ink"
            onClick={onEdit}
          >
            <Pencil size={14} />
          </button>
          <button
            title="删除连接"
            className="flex h-7 w-7 items-center justify-center rounded-md text-zinc-500 hover:bg-red-50 hover:text-danger"
            onClick={onDelete}
          >
            <Trash2 size={14} />
          </button>
        </div>
      </div>
    </div>
  )
}
