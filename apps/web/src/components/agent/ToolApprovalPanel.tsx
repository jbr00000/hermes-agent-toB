import { SquareTerminal } from 'lucide-react'
import type { ToolApproval } from '../../types'

/** controlled 权限档的逐条命令审批面板：允许 / 拒绝 / 本次运行全部允许。 */
export function ToolApprovalPanel({
  approvals,
  onDecide,
}: {
  approvals: ToolApproval[]
  onDecide: (approvalId: string, decision: 'allow' | 'deny' | 'allow_all') => void
}) {
  return (
    <div className="space-y-2">
      {approvals.map((approval) => (
        <div key={approval.id} className="rounded-md border border-amber-300 bg-amber-50 p-3">
          <div className="flex items-center gap-2 text-sm font-medium text-ink">
            <SquareTerminal size={14} className="text-amber-600" />
            <span>等待批准执行命令</span>
            <span className="rounded bg-panel px-1.5 py-0.5 text-xs text-zinc-500">{approval.toolName}</span>
          </div>
          <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-all rounded-md border border-line bg-panel p-2 font-mono text-xs text-ink">
            {approval.commandPreview || '(无命令预览)'}
          </pre>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <button
              className="h-7 rounded-md bg-ink px-3 text-xs text-white hover:opacity-90"
              onClick={() => onDecide(approval.id, 'allow')}
            >
              允许
            </button>
            <button
              className="h-7 rounded-md border border-line px-3 text-xs text-danger hover:bg-red-50"
              onClick={() => onDecide(approval.id, 'deny')}
            >
              拒绝
            </button>
            <button
              className="h-7 rounded-md border border-line px-3 text-xs text-zinc-600 hover:bg-field"
              onClick={() => onDecide(approval.id, 'allow_all')}
            >
              本次运行全部允许
            </button>
          </div>
        </div>
      ))}
    </div>
  )
}
