import { AlertTriangle, FileCheck2, KeyRound, LoaderCircle, Play } from 'lucide-react'
import type { AgentTaskStatus, PermissionMode, TaskPlan } from '../../types'
import { Markdown } from '../Markdown'
import { cn } from '../ui'

/** 任务计划条：生成中 / 待审批 / 已批准待执行三种状态，含审批与执行按钮。 */
export function TaskPlanPanel({
  status,
  plan,
  permissionMode,
  pending,
  onApprove,
  onExecute,
  onModeChange,
}: {
  status: AgentTaskStatus
  plan: TaskPlan | null
  permissionMode: PermissionMode
  pending: boolean
  onApprove: () => void
  onExecute: () => void
  onModeChange: (mode: PermissionMode) => void
}) {
  if (!plan && status === 'draft') return null
  // 计划仍是 pending 但任务已 failed/cancelled 时也必须露出「批准计划」：
  // 后端 approve_task_plan 不校验任务状态（批准后置回 ready），否则这种
  // 状态下两个按钮都不显示，用户没有任何出路
  const awaitingApproval = plan?.status === 'pending'
    && ['awaiting_approval', 'failed', 'cancelled'].includes(status)
  // completed 也保留重新执行入口：历史产物在任务工作区里还在，
  // 追加新指令请用下方输入框（执行模式）
  const executable = plan?.status === 'approved' && ['ready', 'failed', 'cancelled', 'completed'].includes(status)
  return (
    <div className="border-y border-line bg-[#fcfcfd] px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <div className={cn(
            'flex h-9 w-9 shrink-0 items-center justify-center rounded-md',
            awaitingApproval ? 'bg-amber-50 text-caution' : 'bg-emerald-50 text-success',
          )}>
            {awaitingApproval ? <AlertTriangle size={18} /> : <FileCheck2 size={18} />}
          </div>
          <div className="min-w-0">
            <div className="text-sm font-semibold">
              {status === 'planning' ? '正在生成执行计划' : awaitingApproval ? `执行计划 v${plan?.version}` : status === 'completed' ? '任务已完成' : '已批准执行计划'}
            </div>
            <div className="text-xs text-zinc-500">
              {awaitingApproval
                ? '审批后才可执行；涉及写入或终端操作时还需切换到完全访问。'
                : permissionMode === 'full'
                  ? '完全访问将持续生效，直到你手动切回只读。'
                  : '当前只会启用与权限等级匹配的工具。'}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {awaitingApproval && (
            <button
              className="flex h-8 items-center gap-1.5 rounded-md bg-ink px-3 text-xs font-medium text-white disabled:bg-zinc-300"
              disabled={pending}
              onClick={onApprove}
            >
              {pending ? <LoaderCircle size={14} className="animate-spin" /> : <FileCheck2 size={14} />}
              批准计划
            </button>
          )}
          {executable && permissionMode !== 'full' && (
            <button
              className="flex h-8 items-center gap-1.5 rounded-md border border-line px-3 text-xs hover:bg-field disabled:text-zinc-400"
              disabled={pending}
              onClick={() => onModeChange('full')}
            >
              <KeyRound size={14} />
              完全访问
            </button>
          )}
          {executable && (
            <button
              className="flex h-8 items-center gap-1.5 rounded-md bg-ink px-3 text-xs font-medium text-white disabled:bg-zinc-300"
              disabled={pending}
              onClick={onExecute}
            >
              <Play size={14} />
              {status === 'failed' || status === 'cancelled' || status === 'completed' ? '重新执行' : '执行'}
            </button>
          )}
        </div>
      </div>
      {plan?.content && (
        <details className="mt-3 border-t border-line pt-3" open={awaitingApproval}>
          <summary className="cursor-pointer text-xs font-medium text-zinc-600">查看计划内容</summary>
          <Markdown content={plan.content} className="mt-3 max-h-64 overflow-y-auto text-zinc-700" />
        </details>
      )}
    </div>
  )
}
