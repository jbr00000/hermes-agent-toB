import * as React from 'react'
import { AlertTriangle, FileText, Loader2, Paperclip, X } from 'lucide-react'
import type { UploadBudget, UploadedFile } from '../../types'
import { cn, formatBytes, IconButton } from '../ui'
import { attachmentStatusLabel, formatTokens } from './useAttachments'

/** 回形针按钮 + 隐藏 file input（ChatView/AgentView 共用）；选中即回调，选完清空可重复选同一文件 */
export function AttachmentPickerButton({
  label,
  onPick,
}: {
  label: string
  onPick: (files: FileList | null) => void
}) {
  const inputRef = React.useRef<HTMLInputElement | null>(null)
  return (
    <>
      <input
        ref={inputRef}
        type="file"
        multiple
        className="hidden"
        onChange={(event) => {
          onPick(event.target.files)
          event.target.value = ''
        }}
      />
      <IconButton label={label} icon={Paperclip} onClick={() => inputRef.current?.click()} />
    </>
  )
}

/** 输入框上方的附件 chip 列表：解析状态 + 删除。只读模式（右侧面板）不显示删除按钮。 */
export function AttachmentChips({
  files,
  onRemove,
  removing,
}: {
  files: UploadedFile[]
  onRemove?: (fileId: string) => void
  removing?: boolean
}) {
  if (files.length === 0) return null
  return (
    <div className="flex flex-wrap gap-1.5 px-3 pt-2.5">
      {files.map((file) => (
        <span
          key={file.id}
          title={file.parseStatus === 'failed' ? (file.parseError ?? '解析失败') : file.fileName}
          className={cn(
            'flex h-7 max-w-56 items-center gap-1.5 rounded-md border px-2 text-xs',
            file.parseStatus === 'failed'
              ? 'border-red-200 bg-red-50 text-danger'
              : file.parseStatus === 'parsing'
                ? 'border-amber-200 bg-amber-50 text-caution'
                : 'border-line bg-field text-zinc-600',
          )}
        >
          {file.parseStatus === 'parsing'
            ? <Loader2 size={12} className="shrink-0 animate-spin" />
            : <FileText size={12} className="shrink-0" />}
          <span className="truncate">{file.fileName}</span>
          <span className="shrink-0 text-[11px] opacity-75">{attachmentStatusLabel(file)}</span>
          {onRemove && (
            <button
              type="button"
              title="删除附件"
              disabled={removing}
              className="shrink-0 rounded-sm hover:text-ink disabled:opacity-40"
              onClick={() => onRemove(file.id)}
            >
              <X size={12} />
            </button>
          )}
        </span>
      ))}
    </div>
  )
}

/** 附件 token 预算条：有附件时常显用量（灰字），超预算升级为黄色警告（不阻断发送）。 */
export function AttachmentBudgetBar({ budget }: { budget: UploadBudget | null }) {
  if (!budget || budget.fileTokens === 0) return null
  if (!budget.overBudget) {
    return (
      <div className="border-t border-line px-3 py-1.5 text-[11px] text-zinc-400">
        附件全文共 {formatTokens(budget.fileTokens)} tokens · 本模型输入上限
        {formatTokens(budget.maxInputTokens)}（可用预算约 {formatTokens(budget.budgetTokens)}）
      </div>
    )
  }
  return (
    <div className="flex items-start gap-2 border-t border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-caution">
      <AlertTriangle size={14} className="mt-0.5 shrink-0" />
      <span>
        附件全文共 {formatTokens(budget.fileTokens)} tokens，超出本模型可用预算
        {formatTokens(budget.budgetTokens)} tokens（上限 {formatTokens(budget.maxInputTokens)}）。
        发送后将从最新上传的文件开始截断，被截断的文件可能回答不全。
      </span>
    </div>
  )
}

/** 右侧面板用的只读附件列表（名称 + 大小 + 状态）。 */
export function AttachmentRows({ files, emptyText }: { files: UploadedFile[]; emptyText: string }) {
  if (files.length === 0) {
    return <div className="border-y border-line py-3 text-xs text-zinc-500">{emptyText}</div>
  }
  return (
    <div className="divide-y divide-line border-y border-line">
      {files.map((file) => (
        <div key={file.id} className="py-2.5 text-sm">
          <div className="flex min-w-0 items-center gap-2">
            <FileText size={15} className="shrink-0 text-zinc-400" />
            <span className="truncate">{file.fileName}</span>
          </div>
          <div className="mt-1 flex items-center justify-between pl-6 text-xs text-zinc-500">
            <span>{formatBytes(file.sizeBytes)}</span>
            <span className={file.parseStatus === 'failed' ? 'text-danger' : undefined}>
              {attachmentStatusLabel(file)}
            </span>
          </div>
        </div>
      ))}
    </div>
  )
}
