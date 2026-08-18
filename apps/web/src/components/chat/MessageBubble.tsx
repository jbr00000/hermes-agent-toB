import * as React from 'react'
import { ShieldCheck } from 'lucide-react'
import { CORTEX_MARK_URL, cn } from '../ui'
import { Markdown } from '../Markdown'
import type { ChatMessage, KnowledgeCitation, TabType } from '../../types'

export const MessageBubble = React.memo(function MessageBubble({
  message,
  onOpenTab,
}: {
  message: ChatMessage
  /** 引用卡片点击跳转文档详情；不传（无 knowledge feature）时卡片降级为纯文本 */
  onOpenTab?: (type: TabType, refId: string, title: string) => void
}) {
  const assistant = message.role === 'assistant'
  const system = message.role === 'system'
  const thinking = assistant && message.status === 'streaming'
  const completedSeconds = message.durationMs === undefined
    ? null
    : Math.max(1, Math.ceil(message.durationMs / 1000))

  if (thinking && !message.content) {
    return <ThinkingBubble message={message} />
  }

  return (
    <div className={cn('flex gap-3', !assistant && !system && 'justify-end')}>
      {(assistant || system) && (
        <div className={cn('flex h-8 w-8 shrink-0 items-center justify-center rounded-md', system ? 'bg-amber-50 text-caution' : 'bg-[#237a57] text-white')}>
          {system ? <ShieldCheck size={16} /> : <img src={CORTEX_MARK_URL} alt="" className="h-7 w-7" />}
        </div>
      )}
      <div className={cn('max-w-[88%] rounded-md border px-3 py-3 text-sm leading-6 sm:max-w-[78%] sm:px-4', assistant || system ? 'border-line bg-panel' : 'border-ink bg-ink text-white')}>
        {assistant || system ? (
          <Markdown content={message.content || '...'} />
        ) : (
          <div className="whitespace-pre-wrap break-words">{message.content || '...'}</div>
        )}
        {assistant && !system && message.citations && message.citations.length > 0 && (
          <CitationCards citations={message.citations} onOpenTab={onOpenTab} />
        )}
        <div className={cn('mt-2 text-[11px]', assistant || system ? 'text-zinc-400' : 'text-white/60')}>
          {thinking && message.thinkingStartedAt ? (
            <ElapsedThinkingTime startedAt={message.thinkingStartedAt} />
          ) : (
            <>
              {assistant && completedSeconds !== null ? `思考了 ${completedSeconds} 秒${message.createdAt ? ' · ' : ''}` : ''}
              {message.createdAt}
            </>
          )}
        </div>
      </div>
    </div>
  )
})

/** 知识库问答的"参考来源"卡片列表：文档名 + 分块标题 + 摘要 + 序号 badge。 */
function CitationCards({
  citations,
  onOpenTab,
}: {
  citations: KnowledgeCitation[]
  onOpenTab?: (type: TabType, refId: string, title: string) => void
}) {
  return (
    <div className="mt-3 border-t border-line pt-2">
      <div className="mb-1.5 text-[11px] font-medium text-zinc-400">参考来源</div>
      <div className="space-y-1.5">
        {citations.map((citation, index) => {
          const clickable = Boolean(onOpenTab && citation.docId)
          const body = (
            <div className="flex items-start gap-2">
              <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-sm bg-[#e4f3ec] text-[10px] font-semibold text-[#237a57]">
                {citation.num ?? index + 1}
              </span>
              <span className="min-w-0">
                <span className="block truncate text-xs font-medium text-ink">
                  {citation.docName || '未命名文档'}
                  {citation.chunkTitle && <span className="font-normal text-zinc-500"> · {citation.chunkTitle}</span>}
                </span>
                {citation.snippet && (
                  <span className="mt-0.5 line-clamp-2 block text-xs leading-5 text-zinc-500">{citation.snippet}</span>
                )}
              </span>
            </div>
          )
          const className = cn(
            'w-full rounded-md border border-line bg-[#fcfcfd] px-2.5 py-2 text-left',
            clickable && 'transition hover:border-[#237a57]/40 hover:bg-[#f2f9f6]',
          )
          return clickable ? (
            <button
              key={citation.chunkId || index}
              type="button"
              className={className}
              title="查看文档详情"
              onClick={() => onOpenTab?.('document', citation.docId, citation.docName || '文档详情')}
            >
              {body}
            </button>
          ) : (
            <div key={citation.chunkId || index} className={className}>{body}</div>
          )
        })}
      </div>
    </div>
  )
}

function ThinkingBubble({ message }: { message: ChatMessage }) {
  const displayTime = message.createdAt || new Date(
    message.thinkingStartedAt ?? Date.now(),
  ).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })

  return (
    <div className="flex items-start gap-3" role="status" aria-live="polite">
      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-[#237a57] text-white shadow-sm">
        <img src={CORTEX_MARK_URL} alt="" className="h-8 w-8" />
      </div>
      <div className="min-w-0 pt-0.5">
        <div className="flex min-h-9 flex-wrap items-center gap-2">
          <span className="text-sm font-medium text-zinc-500">Cortex · {displayTime}</span>
          <span className="inline-flex h-8 items-center gap-2 rounded-full bg-[#e4f3ec] px-3 text-sm font-medium text-[#237a57]">
            <span className="h-2 w-2 animate-pulse rounded-full bg-[#82bda5]" />
            正在生成
          </span>
        </div>
        <div className="mt-2 flex h-5 items-center gap-1.5 text-xs text-zinc-400">
          <span className="h-2 w-2 animate-bounce rounded-full bg-[#b7d8ca]" style={{ animationDelay: '-320ms' }} />
          <span className="h-2 w-2 animate-bounce rounded-full bg-[#5aa083]" style={{ animationDelay: '-160ms' }} />
          <span className="h-2 w-2 animate-bounce rounded-full bg-[#237a57]" />
          {message.thinkingStartedAt && (
            <span className="ml-1"><ElapsedThinkingTime startedAt={message.thinkingStartedAt} /></span>
          )}
        </div>
      </div>
    </div>
  )
}

function ElapsedThinkingTime({ startedAt }: { startedAt: number }) {
  const [now, setNow] = React.useState(Date.now())

  React.useEffect(() => {
    setNow(Date.now())
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [startedAt])

  const elapsed = Math.max(0, Math.floor((now - startedAt) / 1000))
  return <span>已思考 {elapsed} 秒</span>
}
