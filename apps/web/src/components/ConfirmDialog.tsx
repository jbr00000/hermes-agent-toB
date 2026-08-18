import { LoaderCircle, Trash2, type LucideIcon } from 'lucide-react'

/** 破坏性操作的统一确认弹窗（删除任务/问答/知识库/文档/用户共用）。
 *  点击遮罩或取消关闭；pending 期间禁止关闭与重复提交。 */
export function ConfirmDialog({
  icon: Icon = Trash2,
  title,
  description,
  confirmLabel = '删除',
  pending = false,
  onConfirm,
  onCancel,
}: {
  icon?: LucideIcon
  title: string
  description: string
  confirmLabel?: string
  pending?: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !pending) onCancel()
      }}
    >
      <div
        className="w-full max-w-sm rounded-md border border-line bg-panel p-5 shadow-card"
        role="alertdialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="flex items-start gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-red-50 text-danger">
            <Icon size={17} />
          </div>
          <div>
            <div className="text-sm font-semibold">{title}</div>
            <p className="mt-1 text-sm leading-6 text-zinc-500">{description}</p>
          </div>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <button
            className="h-9 rounded-md border border-line px-3 text-sm hover:bg-field disabled:text-zinc-400"
            disabled={pending}
            onClick={onCancel}
          >
            取消
          </button>
          <button
            className="flex h-9 items-center gap-2 rounded-md bg-danger px-3 text-sm font-medium text-white disabled:bg-zinc-300"
            disabled={pending}
            onClick={onConfirm}
          >
            {pending ? <LoaderCircle size={15} className="animate-spin" /> : <Icon size={15} />}
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
