/** 共享 UI 基础件：从 App.tsx 下沉，供知识库等视图复用（保持纸面风格 token）。 */
import * as React from 'react'
import type { LucideIcon } from 'lucide-react'

export const CORTEX_MARK_URL = '/assets/cortex-logo-mark.svg'

export function cn(...classes: Array<string | false | null | undefined>): string {
  return classes.filter(Boolean).join(' ')
}

export function IconButton({ label, icon: Icon, onClick }: { label: string; icon: LucideIcon; onClick?: () => void }) {
  return (
    <button title={label} className="flex h-8 w-8 items-center justify-center rounded-md text-zinc-500 hover:bg-field hover:text-ink" onClick={onClick}>
      <Icon size={16} />
    </button>
  )
}

export function MessageSkeleton() {
  return (
    <div className="space-y-3">
      {[0, 1, 2].map((item) => (
        <div key={item} className="h-16 animate-pulse rounded-md bg-field" />
      ))}
    </div>
  )
}

export function PageHeader({ icon: Icon, title, subtitle }: { icon: LucideIcon; title: string; subtitle: string }) {
  return (
    <header className="flex h-16 items-center justify-between border-b border-line px-6">
      <div className="flex min-w-0 items-center gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-field text-zinc-700">
          <Icon size={18} />
        </div>
        <div className="min-w-0">
          <div className="truncate text-base font-semibold">{title}</div>
          <div className="truncate text-xs text-zinc-500">{subtitle}</div>
        </div>
      </div>
    </header>
  )
}

export function DataTable({ children }: { children: React.ReactNode }) {
  return (
    <div className="thin-scrollbar overflow-x-auto border-y border-line">
      <table className="w-full min-w-[760px] table-fixed text-left text-sm">{children}</table>
    </div>
  )
}

export function Th({ children, className }: { children: React.ReactNode; className?: string }) {
  return <th className={cn('border-b border-line bg-[#fafafa] px-3 py-2 text-xs font-semibold text-zinc-500', className)}>{children}</th>
}

export function Td({ children, className }: { children: React.ReactNode; className?: string }) {
  return <td className={cn('min-w-0 px-3 py-3 align-middle text-sm text-zinc-700', className)}>{children}</td>
}

export function Badge({ children, className, title }: { children: React.ReactNode; className?: string; title?: string }) {
  return <span title={title} className={cn('inline-flex h-6 items-center rounded px-2 text-xs font-medium', className)}>{children}</span>
}

/** 开关（启用/停用）：button role="switch"，纸面 token。 */
export function Toggle({
  checked,
  disabled,
  onChange,
  label,
}: {
  checked: boolean
  disabled?: boolean
  onChange: (value: boolean) => void
  label?: string
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label ?? (checked ? '停用' : '启用')}
      disabled={disabled}
      onClick={(event) => {
        event.stopPropagation()
        onChange(!checked)
      }}
      className={cn(
        'relative h-5 w-9 shrink-0 rounded-full transition-colors',
        checked ? 'bg-ink' : 'bg-zinc-300',
        disabled ? 'cursor-not-allowed opacity-40' : 'cursor-pointer',
      )}
    >
      <span
        className={cn(
          'absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform',
          checked && 'translate-x-4',
        )}
      />
    </button>
  )
}

export function InfoRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 text-sm">
      <span className="text-zinc-500">{label}</span>
      <span className="min-w-0 truncate font-medium">{value}</span>
    </div>
  )
}

export function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

/** 知识库列表/详情共用的「月-日 时:分」格式化 */
export function formatDateTime(timestampMs: number): string {
  return new Date(timestampMs).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}
