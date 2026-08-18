import { History } from 'lucide-react'
import type { ToolEvent } from '../../types'
import { cn } from '../ui'

/** 工具活动时间线：过滤内部工具（_ 前缀），活跃运行显示本轮全部，否则显示最近 6 条。 */
export function ToolEventTimeline({ events, activeRunId }: { events: ToolEvent[]; activeRunId: string | null }) {
  const auditableEvents = events.filter((event) => !event.toolName?.startsWith('_'))
  const visible = activeRunId
    ? auditableEvents.filter((event) => event.runId === activeRunId)
    : auditableEvents.slice(-6)
  if (visible.length === 0) return null
  return (
    <section className="border-y border-line py-3" aria-live="polite">
      <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-zinc-500">
        <History size={14} />
        工具活动
      </div>
      <div className="space-y-1.5">
        {visible.slice(-8).map((event) => {
          const failed = event.status === 'failed'
          const completed = event.eventType === 'tool.completed'
          return (
            <div key={`${event.runId}-${event.sequence}-${event.eventType}`} className="flex items-center gap-2 text-xs text-zinc-600">
              <span className={cn(
                'h-1.5 w-1.5 rounded-full',
                failed ? 'bg-danger' : completed ? 'bg-success' : 'animate-pulse bg-caution',
              )} />
              <span className="font-medium">{event.toolName ?? 'Agent tool'}</span>
              <span className="text-zinc-400">{failed ? '执行失败' : completed ? '已完成' : '执行中'}</span>
            </div>
          )
        })}
      </div>
    </section>
  )
}
