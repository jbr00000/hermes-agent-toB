/** 共享 UI 基础件：从 App.tsx 下沉，供知识库等视图复用（保持纸面风格 token）。 */
import * as React from 'react'
import { History, type LucideIcon } from 'lucide-react'

export function cn(...classes: Array<string | false | null | undefined>): string {
  return classes.filter(Boolean).join(' ')
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
      <button className="flex h-8 items-center gap-2 rounded-md border border-line px-3 text-sm hover:bg-field">
        <History size={15} />
        历史
      </button>
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

export function Badge({ children, className }: { children: React.ReactNode; className?: string }) {
  return <span className={cn('inline-flex h-6 items-center rounded px-2 text-xs font-medium', className)}>{children}</span>
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
