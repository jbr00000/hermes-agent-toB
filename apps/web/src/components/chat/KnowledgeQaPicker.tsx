import * as React from 'react'
import { useAtom } from 'jotai'
import { Check, ChevronDown, Database, FileText } from 'lucide-react'
import {
  knowledgeQaEnabledAtom,
  knowledgeQaKbIdAtom,
  knowledgeQaSearchModeAtom,
} from '../../state'
import type { KnowledgeBase } from '../../types'
import { cn } from '../ui'

/** Chat 输入框的知识库问答开关 + 选库下拉（全部知识库 / 指定库）。 */
export function KnowledgeQaPicker({ bases }: { bases: KnowledgeBase[] }) {
  const [enabled, setEnabled] = useAtom(knowledgeQaEnabledAtom)
  const [kbId, setKbId] = useAtom(knowledgeQaKbIdAtom)
  const [searchMode, setSearchMode] = useAtom(knowledgeQaSearchModeAtom)
  const [open, setOpen] = React.useState(false)
  const rootRef = React.useRef<HTMLDivElement | null>(null)

  React.useEffect(() => {
    if (!open) return
    const onPointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    return () => document.removeEventListener('mousedown', onPointerDown)
  }, [open])

  // 库被删除后本地残留的 kbId 自动回退为"全部知识库"
  const selectedBase = kbId ? bases.find((base) => base.id === kbId) : null
  React.useEffect(() => {
    if (kbId && bases.length > 0 && !selectedBase) setKbId(null)
  }, [bases.length, kbId, selectedBase, setKbId])
  const selectedName = selectedBase?.name ?? '全部知识库'

  const itemClass = 'flex h-9 w-full items-center gap-2 rounded px-2.5 text-left text-sm hover:bg-field'
  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        title="知识库问答"
        className={cn(
          'flex h-8 items-center gap-1.5 rounded-md px-2 text-xs transition',
          enabled ? 'bg-[#e4f3ec] font-medium text-[#237a57]' : 'text-zinc-500 hover:bg-field hover:text-ink',
        )}
        onClick={() => setOpen((current) => !current)}
      >
        <Database size={16} />
        {enabled && <span className="max-w-32 truncate">{selectedName}</span>}
        {enabled && searchMode === 'precise' && (
          <span className="rounded-sm bg-[#237a57] px-1 py-px text-[10px] font-medium text-white">精准</span>
        )}
        <ChevronDown size={12} className={cn('transition', open && 'rotate-180')} />
      </button>
      {open && (
        <div className="absolute bottom-10 left-0 z-20 w-60 rounded-md border border-line bg-panel p-1 shadow-card">
          <button type="button" className={itemClass} onClick={() => setEnabled((current) => !current)}>
            <span className={cn(
              'flex h-4 w-4 shrink-0 items-center justify-center rounded-sm border',
              enabled ? 'border-[#237a57] bg-[#237a57] text-white' : 'border-line bg-panel',
            )}>
              {enabled && <Check size={12} />}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-sm">知识库问答</span>
              <span className="block text-[11px] text-zinc-400">严格基于知识库回答并标注来源</span>
            </span>
          </button>
          {enabled && (
            <>
              <div className="my-1 border-t border-line" />
              <div className="px-2.5 pb-1 pt-0.5 text-[11px] text-zinc-400">检索模式</div>
              <div className="mx-1 mb-1 flex rounded-md bg-field p-0.5">
                {([
                  { value: 'fast' as const, label: '快速', hint: '单次检索，即时回答' },
                  { value: 'precise' as const, label: '精准', hint: '先理解问题再检索，更慢更准' },
                ]).map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    title={option.hint}
                    className={cn(
                      'flex h-7 flex-1 items-center justify-center rounded text-xs transition',
                      searchMode === option.value
                        ? 'bg-panel font-medium text-[#237a57] shadow-sm'
                        : 'text-zinc-500 hover:text-ink',
                    )}
                    onClick={() => setSearchMode(option.value)}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
              <div className="my-1 border-t border-line" />
              <div className="px-2.5 pb-1 pt-0.5 text-[11px] text-zinc-400">检索范围</div>
              <button
                type="button"
                className={cn(itemClass, kbId === null && 'bg-field font-medium')}
                onClick={() => { setKbId(null); setOpen(false) }}
              >
                <Database size={14} className="shrink-0 text-zinc-400" />
                全部知识库
              </button>
              {bases.map((base) => (
                <button
                  key={base.id}
                  type="button"
                  className={cn(itemClass, kbId === base.id && 'bg-field font-medium')}
                  onClick={() => { setKbId(base.id); setOpen(false) }}
                >
                  <FileText size={14} className="shrink-0 text-zinc-400" />
                  <span className="truncate">{base.name}</span>
                </button>
              ))}
              {bases.length === 0 && (
                <div className="px-2.5 py-1.5 text-xs text-zinc-400">还没有知识库，请先在知识库页面创建</div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}
