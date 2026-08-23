import * as React from 'react'
import { useAtom } from 'jotai'
import { Check, ChevronDown, Database, FileText } from 'lucide-react'
import {
  agentKnowledgeEnabledAtom,
  agentKnowledgeKbIdAtom,
  knowledgeQaEnabledAtom,
  knowledgeQaKbIdAtom,
  knowledgeQaSearchModeAtom,
} from '../../state'
import type { KnowledgeBase, KnowledgeSearchMode } from '../../types'
import { cn } from '../ui'

/** 知识库选择的值：enabled=false 本轮不带知识库；kbId=null 检索全部知识库 */
export interface KnowledgeScopeValue {
  enabled: boolean
  kbId: string | null
}

/**
 * 受控的知识库选择器：开关 + 选库下拉（全部知识库 / 指定库）。
 * Chat（知识库问答模式）与 Agent（运行级检索范围）共用；检索模式段由 chat 侧按需传入。
 */
export function KnowledgeScopePicker({
  value,
  onChange,
  bases,
  enableLabel,
  enableHint,
  searchMode,
}: {
  value: KnowledgeScopeValue
  onChange: (next: KnowledgeScopeValue) => void
  bases: KnowledgeBase[]
  enableLabel: string
  enableHint: string
  searchMode?: { value: KnowledgeSearchMode; onChange: (mode: KnowledgeSearchMode) => void }
}) {
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
  const selectedBase = value.kbId ? bases.find((base) => base.id === value.kbId) : null
  React.useEffect(() => {
    if (value.kbId && bases.length > 0 && !selectedBase) {
      onChange({ ...value, kbId: null })
    }
  }, [bases.length, value, selectedBase, onChange])
  const selectedName = selectedBase?.name ?? '全部知识库'

  const itemClass = 'flex h-9 w-full items-center gap-2 rounded px-2.5 text-left text-sm hover:bg-field'
  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        title={enableLabel}
        className={cn(
          'flex h-8 items-center gap-1.5 rounded-md px-2 text-xs transition',
          value.enabled ? 'bg-[#e4f3ec] font-medium text-[#237a57]' : 'text-zinc-500 hover:bg-field hover:text-ink',
        )}
        onClick={() => setOpen((current) => !current)}
      >
        <Database size={16} />
        {value.enabled && <span className="max-w-32 truncate">{selectedName}</span>}
        {value.enabled && searchMode?.value === 'precise' && (
          <span className="rounded-sm bg-[#237a57] px-1 py-px text-[10px] font-medium text-white">精准</span>
        )}
        <ChevronDown size={12} className={cn('transition', open && 'rotate-180')} />
      </button>
      {open && (
        <div className="absolute bottom-10 left-0 z-20 w-60 rounded-md border border-line bg-panel p-1 shadow-card">
          <button type="button" className={itemClass} onClick={() => onChange({ ...value, enabled: !value.enabled })}>
            <span className={cn(
              'flex h-4 w-4 shrink-0 items-center justify-center rounded-sm border',
              value.enabled ? 'border-[#237a57] bg-[#237a57] text-white' : 'border-line bg-panel',
            )}>
              {value.enabled && <Check size={12} />}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-sm">{enableLabel}</span>
              <span className="block text-[11px] text-zinc-400">{enableHint}</span>
            </span>
          </button>
          {value.enabled && (
            <>
              {searchMode && (
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
                          searchMode.value === option.value
                            ? 'bg-panel font-medium text-[#237a57] shadow-sm'
                            : 'text-zinc-500 hover:text-ink',
                        )}
                        onClick={() => searchMode.onChange(option.value)}
                      >
                        {option.label}
                      </button>
                    ))}
                  </div>
                </>
              )}
              <div className="my-1 border-t border-line" />
              <div className="px-2.5 pb-1 pt-0.5 text-[11px] text-zinc-400">检索范围</div>
              <button
                type="button"
                className={cn(itemClass, value.kbId === null && 'bg-field font-medium')}
                onClick={() => { onChange({ ...value, kbId: null }); setOpen(false) }}
              >
                <Database size={14} className="shrink-0 text-zinc-400" />
                全部知识库
              </button>
              {bases.map((base) => (
                <button
                  key={base.id}
                  type="button"
                  className={cn(itemClass, value.kbId === base.id && 'bg-field font-medium')}
                  onClick={() => { onChange({ ...value, kbId: base.id }); setOpen(false) }}
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

/** Chat 输入框的知识库问答开关 + 选库下拉（全部知识库 / 指定库）。 */
export function KnowledgeQaPicker({ bases }: { bases: KnowledgeBase[] }) {
  const [enabled, setEnabled] = useAtom(knowledgeQaEnabledAtom)
  const [kbId, setKbId] = useAtom(knowledgeQaKbIdAtom)
  const [searchMode, setSearchMode] = useAtom(knowledgeQaSearchModeAtom)
  return (
    <KnowledgeScopePicker
      value={{ enabled, kbId }}
      onChange={(next) => { setEnabled(next.enabled); setKbId(next.kbId) }}
      bases={bases}
      enableLabel="知识库问答"
      enableHint="严格基于知识库回答并标注来源"
      searchMode={{ value: searchMode, onChange: setSearchMode }}
    />
  )
}

/** Agent 任务的运行级知识库选择：不带知识库 / 全部知识库 / 指定库（规划与执行都生效）。 */
export function AgentKnowledgePicker({ bases }: { bases: KnowledgeBase[] }) {
  const [enabled, setEnabled] = useAtom(agentKnowledgeEnabledAtom)
  const [kbId, setKbId] = useAtom(agentKnowledgeKbIdAtom)
  return (
    <KnowledgeScopePicker
      value={{ enabled, kbId }}
      onChange={(next) => { setEnabled(next.enabled); setKbId(next.kbId) }}
      bases={bases}
      enableLabel="知识库检索"
      enableHint="规划和执行时可检索知识库内容"
    />
  )
}
