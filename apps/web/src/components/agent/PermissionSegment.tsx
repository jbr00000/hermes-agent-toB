import { FileCheck2, KeyRound, LockKeyhole, type LucideIcon } from 'lucide-react'
import type { PermissionMode } from '../../types'
import { cn } from '../ui'

/** 权限三档分段控件（只读 / 受控写入 / 完全访问），AgentView 顶栏与右侧面板共用。 */
export function PermissionSegment({ value, onChange, compact = false, disabled = false }: { value: PermissionMode; onChange: (mode: PermissionMode) => void; compact?: boolean; disabled?: boolean }) {
  const items: Array<{ value: PermissionMode; label: string; icon: LucideIcon }> = [
    { value: 'read', label: '只读', icon: LockKeyhole },
    { value: 'controlled', label: '受控写入', icon: FileCheck2 },
    { value: 'full', label: '完全访问', icon: KeyRound },
  ]

  return (
    <div className="flex rounded-md border border-line bg-field p-0.5">
      {items.map((item) => {
        const Icon = item.icon
        return (
          <button
            key={item.value}
            title={item.label}
            className={cn(
              'flex items-center gap-1.5 rounded text-xs transition',
              compact ? 'h-8 w-8 justify-center px-0 sm:w-auto sm:px-2' : 'h-7 px-2',
              value === item.value ? 'bg-panel text-ink shadow-sm' : 'text-zinc-500 hover:text-ink',
              compact && item.value === 'controlled' && 'hidden xl:flex',
              disabled && 'cursor-not-allowed opacity-50',
            )}
            disabled={disabled}
            onClick={() => onChange(item.value)}
          >
            <Icon size={13} />
            <span className={cn(compact && 'hidden sm:inline')}>{item.label}</span>
          </button>
        )
      })}
    </div>
  )
}
