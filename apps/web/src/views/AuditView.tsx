import { useQuery } from '@tanstack/react-query'
import { ClipboardList } from 'lucide-react'
import { api, ApiError } from '../api'
import { Badge, PageHeader } from '../components/ui'

export function AuditView() {
  const eventsQuery = useQuery({
    queryKey: ['auditEvents'],
    queryFn: () => api.listAuditEvents(),
    refetchInterval: 15000,
    retry: false,
  })
  const events = eventsQuery.data ?? []
  const forbidden = eventsQuery.error instanceof ApiError && eventsQuery.error.status === 403

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader icon={ClipboardList} title="审计中心" subtitle="会话、工具、权限切换与高风险动作" />
      <section className="thin-scrollbar min-h-0 flex-1 overflow-y-auto p-6">
        {forbidden ? (
          <div className="py-10 text-center text-sm text-zinc-500">审计记录仅管理员可见</div>
        ) : eventsQuery.isError ? (
          <div className="py-10 text-center text-sm text-zinc-500">审计记录加载失败，请稍后重试</div>
        ) : eventsQuery.isLoading ? (
          <div className="py-10 text-center text-sm text-zinc-500">加载中…</div>
        ) : events.length === 0 ? (
          <div className="py-10 text-center text-sm text-zinc-500">暂无审计记录</div>
        ) : (
          <div className="border-y border-line">
            {events.map((event) => (
              <div key={event.id} className="grid grid-cols-[90px_140px_110px_minmax(0,1fr)_110px] gap-4 border-b border-line px-2 py-3 text-sm last:border-0">
                <span className="text-zinc-500">{event.time}</span>
                <span className="truncate font-mono text-xs text-zinc-500">{event.eventType}</span>
                <span className="truncate text-zinc-600">{event.username || '—'}</span>
                <span className="truncate" title={event.subject}>{event.subject}</span>
                <Badge className={
                  event.status === 'completed' ? 'bg-emerald-50 text-success'
                    : event.status === 'blocked' ? 'bg-red-50 text-danger'
                      : event.status === 'failed' ? 'bg-amber-50 text-caution'
                        : 'bg-zinc-100 text-zinc-600'
                }>{event.status}</Badge>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
