import React from 'react'
import type { QueryClient } from '@tanstack/react-query'
import { api, toKnowledgeCitations, toMessage } from './api'
import { mergeRunMessages } from './mergeMessages'
import type { ActiveModelRun, ChatMessage, ConversationDetail, KnowledgeSearchMode } from './types'

export type ChatRunStatus =
  | 'connecting'
  | 'streaming'
  | 'cancelling'
  | 'completed'
  | 'cancelled'
  | 'failed'

/** 知识库问答选项：kbId=null 检索全部库；searchMode 默认 fast */
export interface KnowledgeQaOptions {
  kbId: string | null
  searchMode?: KnowledgeSearchMode
}

export interface ChatRunSnapshot {
  sessionId: string
  requestId: string
  source: 'local' | 'restored'
  status: ChatRunStatus
  userMessage: ChatMessage | null
  assistantMessage: ChatMessage
  title: string | null
  sequence: number
  startedAt: number
  updatedAt: number
  error: string | null
}

interface InternalRun {
  snapshot: ChatRunSnapshot
  pendingContent: string
  flushTimer: number | null
}

type ChatApi = Pick<typeof api, 'streamChat' | 'cancelChat'>

const ACTIVE_STATUSES = new Set<ChatRunStatus>(['connecting', 'streaming', 'cancelling'])

export function isChatRunActive(run: ChatRunSnapshot | null): boolean {
  return run !== null && ACTIVE_STATUSES.has(run.status)
}

export function mergeChatMessages(
  persistedMessages: ChatMessage[],
  run: ChatRunSnapshot | null,
): ChatMessage[] {
  return mergeRunMessages(persistedMessages, run)
}

export class ChatRunManager {
  private readonly runs = new Map<string, InternalRun>()
  private readonly listeners = new Map<string, Set<() => void>>()
  private readonly streamControllers = new Map<string, AbortController>()
  private readonly scrollPositions = new Map<string, number>()

  constructor(
    private readonly queryClient: QueryClient,
    private readonly chatApi: ChatApi = api,
  ) {}

  getSnapshot(sessionId: string): ChatRunSnapshot | null {
    return this.runs.get(sessionId)?.snapshot ?? null
  }

  getScrollPosition(sessionId: string): number | null {
    return this.scrollPositions.get(sessionId) ?? null
  }

  setScrollPosition(sessionId: string, scrollTop: number): void {
    this.scrollPositions.set(sessionId, Math.max(0, scrollTop))
  }

  clearScrollPosition(sessionId: string): void {
    this.scrollPositions.delete(sessionId)
  }

  subscribe(sessionId: string, listener: () => void): () => void {
    const listeners = this.listeners.get(sessionId) ?? new Set<() => void>()
    listeners.add(listener)
    this.listeners.set(sessionId, listeners)
    return () => {
      listeners.delete(listener)
      if (listeners.size === 0) this.listeners.delete(sessionId)
    }
  }

  clearAll(): void {
    const sessionIds = [...this.runs.keys()]
    for (const run of this.runs.values()) {
      if (run.flushTimer !== null) window.clearTimeout(run.flushTimer)
    }
    this.runs.clear()
    this.scrollPositions.clear()

    const controllers = [...this.streamControllers.values()]
    this.streamControllers.clear()
    controllers.forEach((controller) => controller.abort())
    sessionIds.forEach((sessionId) => this.notify(sessionId))
  }

  start(
    sessionId: string,
    message: string,
    options?: { knowledgeQa?: KnowledgeQaOptions | null },
  ): string {
    const current = this.getSnapshot(sessionId)
    if (isChatRunActive(current)) throw new Error('当前问答正在生成')

    const requestId = crypto.randomUUID()
    const now = Date.now()
    const createdAt = new Date(now).toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
    })
    const snapshot: ChatRunSnapshot = {
      sessionId,
      requestId,
      source: 'local',
      status: 'connecting',
      userMessage: {
        id: `chat-user-${requestId}`,
        role: 'user',
        content: message,
        createdAt,
      },
      assistantMessage: {
        id: `chat-assistant-${requestId}`,
        role: 'assistant',
        content: '',
        createdAt,
        status: 'streaming',
        modelRunId: requestId,
        thinkingStartedAt: now,
      },
      title: null,
      sequence: 0,
      startedAt: now,
      updatedAt: now,
      error: null,
    }
    this.replace(sessionId, snapshot)
    const controller = new AbortController()
    this.streamControllers.set(requestId, controller)
    this.queryClient.setQueryData<ConversationDetail>(['conversation', sessionId], (detail) => (
      detail
        ? {
            ...detail,
            status: 'running',
            activeRun: {
              id: requestId,
              status: 'running',
              startedAt: now,
              elapsedMs: 0,
              observedAt: now,
              partialContent: '',
              sequence: 0,
              snapshotUpdatedAt: now,
            },
          }
        : detail
    ))
    void this.consume(sessionId, requestId, message, controller, options?.knowledgeQa ?? null)
    return requestId
  }

  async cancel(sessionId: string): Promise<void> {
    const run = this.runs.get(sessionId)
    if (!run || !isChatRunActive(run.snapshot)) return
    const previousStatus = run.snapshot.status
    this.update(sessionId, (snapshot) => ({
      ...snapshot,
      status: 'cancelling',
      updatedAt: Date.now(),
      error: null,
    }))
    try {
      await this.chatApi.cancelChat(run.snapshot.requestId)
      // 服务端确认取消后同步断开本地 SSE——否则服务端忽略取消时流还在继续写，
      // UI 却已显示"已取消"
      this.streamControllers.get(run.snapshot.requestId)?.abort()
    } catch (error) {
      this.update(sessionId, (snapshot) => ({
        ...snapshot,
        status: previousStatus,
        updatedAt: Date.now(),
        error: error instanceof Error ? error.message : '停止生成失败',
      }))
      throw error
    }
  }

  reconcileServerState(sessionId: string, detail: ConversationDetail): void {
    const activeRun = detail.activeRun
    const current = this.runs.get(sessionId)

    if (activeRun) {
      // 用户已取消、服务端尚未确认完：等 activeRun 消失后由下方 persisted 检查清理，
      // 不要因为轮询快照滞后把已取消的运行回摆成 streaming
      if (
        current
        && current.snapshot.requestId === activeRun.id
        && (current.snapshot.status === 'cancelled' || current.snapshot.status === 'cancelling')
      ) {
        return
      }
      if (
        current
        && current.snapshot.source === 'local'
        && current.snapshot.requestId === activeRun.id
        && isChatRunActive(current.snapshot)
      ) {
        return
      }
      if (
        current
        && current.snapshot.source === 'restored'
        && current.snapshot.requestId === activeRun.id
        && current.snapshot.sequence >= activeRun.sequence
        && isChatRunActive(current.snapshot)
      ) {
        return
      }
      this.restore(sessionId, activeRun)
      return
    }

    if (!current) return
    const persisted = detail.messages.some((message) => (
      message.role === 'assistant' && message.modelRunId === current.snapshot.requestId
    ))
    if (persisted || current.snapshot.source === 'restored') this.remove(sessionId)
  }

  private restore(sessionId: string, activeRun: ActiveModelRun): void {
    const now = Date.now()
    const startedAt = activeRun.startedAt || activeRun.observedAt - activeRun.elapsedMs
    const current = this.runs.get(sessionId)?.snapshot
    this.replace(sessionId, {
      sessionId,
      requestId: activeRun.id,
      source: 'restored',
      status: 'streaming',
      userMessage: null,
      assistantMessage: {
        id: `active-${activeRun.id}`,
        role: 'assistant',
        content: activeRun.partialContent,
        createdAt: new Date(startedAt).toLocaleTimeString('zh-CN', {
          hour: '2-digit',
          minute: '2-digit',
        }),
        status: 'streaming',
        modelRunId: activeRun.id,
        thinkingStartedAt: startedAt,
      },
      title: current?.title ?? null,
      sequence: activeRun.sequence,
      startedAt,
      updatedAt: activeRun.snapshotUpdatedAt ?? now,
      error: null,
    })
  }

  private async consume(
    sessionId: string,
    requestId: string,
    message: string,
    controller: AbortController,
    knowledgeQa: KnowledgeQaOptions | null = null,
  ): Promise<void> {
    let terminalEventReceived = false
    try {
      await this.chatApi.streamChat(sessionId, requestId, message, {
        onSession: (event) => {
          this.update(sessionId, (snapshot) => ({
            ...snapshot,
            status: 'streaming',
            userMessage: event.message && typeof event.message !== 'string'
              ? event.message
              : snapshot.userMessage,
            title: event.title ?? snapshot.title,
            updatedAt: Date.now(),
          }))
        },
        onDelta: (event) => {
          if (event.content) this.queueDelta(sessionId, event.content)
        },
        onFinal: (event) => {
          terminalEventReceived = true
          this.flush(sessionId)
          this.update(sessionId, (snapshot) => {
            const terminalStatus = event.status === 'cancelled'
              ? 'cancelled' as const
              : event.status === 'failed'
                ? 'failed' as const
                : 'completed' as const
            // final 事件的 message 是后端落库消息原样（snake_case，引用在
            // metadata.citations）——必须走 toMessage 映射，否则引用卡片会在
            // 回答完成的瞬间从气泡上消失
            const finalMessage = event.message && typeof event.message !== 'string'
              ? { ...toMessage(event.message as Parameters<typeof toMessage>[0]), status: terminalStatus }
              : {
                  ...snapshot.assistantMessage,
                  content: event.content ?? snapshot.assistantMessage.content,
                  status: terminalStatus,
                }
            return {
              ...snapshot,
              status: terminalStatus,
              assistantMessage: finalMessage,
              title: event.title ?? snapshot.title,
              updatedAt: Date.now(),
              error: null,
            }
          })
          this.queryClient.setQueryData<ConversationDetail>(['conversation', sessionId], (detail) => (
            detail ? { ...detail, status: 'idle', activeRun: null } : detail
          ))
          // final 后回源持久化消息（标题/引用/耗时以服务端为准），避免因缓存
          // 滞留导致切换会话前后看到的气泡不一致
          void this.queryClient.invalidateQueries({ queryKey: ['conversation', sessionId] })
        },
        onError: (event) => {
          terminalEventReceived = true
          this.flush(sessionId)
          const error = typeof event.message === 'string' ? event.message : '回答生成失败'
          this.markFailed(sessionId, error)
        },
        onEvent: (eventName, event) => {
          // 知识库问答：knowledge_search 一完成，服务端就推送累计引用列表 ——
          // 直接挂到流式气泡上，让引用卡片随回答一起出现（持久化兜底在 final 消息的 metadata）
          if (eventName !== 'citations') return
          const citations = toKnowledgeCitations(event.chunks)
          if (citations.length === 0) return
          this.update(sessionId, (snapshot) => ({
            ...snapshot,
            updatedAt: Date.now(),
            assistantMessage: { ...snapshot.assistantMessage, citations },
          }))
        },
      }, controller.signal, knowledgeQa)
      if (!terminalEventReceived && isChatRunActive(this.getSnapshot(sessionId))) {
        this.markFailed(sessionId, '流式连接已结束，正在同步服务端状态')
      }
    } catch (error) {
      this.flush(sessionId)
      if (controller.signal.aborted) {
        // 本地主动断开（用户取消 / 登出 clearAll）：不是失败。登出时 runs 已清空，
        // update 自然落空；取消时把状态落成 cancelled，等服务端快照兜底纠正
        this.update(sessionId, (snapshot) => (
          isChatRunActive(snapshot)
            ? {
                ...snapshot,
                status: 'cancelled',
                updatedAt: Date.now(),
                assistantMessage: { ...snapshot.assistantMessage, status: 'cancelled' },
              }
            : snapshot
        ))
      } else {
        this.markFailed(
          sessionId,
          error instanceof Error ? error.message : '无法连接 Chat 服务',
        )
      }
    } finally {
      if (this.streamControllers.get(requestId) === controller) {
        this.streamControllers.delete(requestId)
      }
      await Promise.all([
        this.queryClient.invalidateQueries({ queryKey: ['conversation', sessionId] }),
        this.queryClient.invalidateQueries({ queryKey: ['conversations'] }),
      ])
    }
  }

  private queueDelta(sessionId: string, content: string): void {
    const run = this.runs.get(sessionId)
    if (!run) return
    run.pendingContent += content
    if (run.flushTimer === null) {
      run.flushTimer = window.setTimeout(() => this.flush(sessionId), 50)
    }
  }

  private flush(sessionId: string): void {
    const run = this.runs.get(sessionId)
    if (!run) return
    if (run.flushTimer !== null) {
      window.clearTimeout(run.flushTimer)
      run.flushTimer = null
    }
    if (!run.pendingContent) return
    const content = run.pendingContent
    run.pendingContent = ''
    this.update(sessionId, (snapshot) => ({
      ...snapshot,
      status: snapshot.status === 'connecting' ? 'streaming' : snapshot.status,
      sequence: snapshot.sequence + 1,
      updatedAt: Date.now(),
      assistantMessage: {
        ...snapshot.assistantMessage,
        content: `${snapshot.assistantMessage.content}${content}`,
      },
    }))
  }

  private markFailed(sessionId: string, error: string): void {
    this.update(sessionId, (snapshot) => ({
      ...snapshot,
      status: 'failed',
      updatedAt: Date.now(),
      error,
      assistantMessage: {
        ...snapshot.assistantMessage,
        content: snapshot.assistantMessage.content || '回答未完成，请重试。',
        status: 'failed',
      },
    }))
  }

  private replace(sessionId: string, snapshot: ChatRunSnapshot): void {
    const existing = this.runs.get(sessionId)
    if (existing?.flushTimer !== null && existing?.flushTimer !== undefined) {
      window.clearTimeout(existing.flushTimer)
    }
    this.runs.set(sessionId, { snapshot, pendingContent: '', flushTimer: null })
    this.notify(sessionId)
  }

  private update(
    sessionId: string,
    updater: (snapshot: ChatRunSnapshot) => ChatRunSnapshot,
  ): void {
    const run = this.runs.get(sessionId)
    if (!run) return
    run.snapshot = updater(run.snapshot)
    this.notify(sessionId)
  }

  private remove(sessionId: string): void {
    const run = this.runs.get(sessionId)
    if (run?.flushTimer !== null && run?.flushTimer !== undefined) {
      window.clearTimeout(run.flushTimer)
    }
    this.runs.delete(sessionId)
    this.notify(sessionId)
  }

  private notify(sessionId: string): void {
    this.listeners.get(sessionId)?.forEach((listener) => listener())
  }
}

const ChatRunContext = React.createContext<ChatRunManager | null>(null)

export function ChatRunProvider({
  manager,
  children,
}: {
  manager: ChatRunManager
  children: React.ReactNode
}) {
  return <ChatRunContext.Provider value={manager}>{children}</ChatRunContext.Provider>
}

export function useChatRunManager(): ChatRunManager {
  const manager = React.useContext(ChatRunContext)
  if (!manager) throw new Error('ChatRunProvider is missing')
  return manager
}

export function useChatRun(sessionId: string): ChatRunSnapshot | null {
  const manager = useChatRunManager()
  return React.useSyncExternalStore(
    React.useCallback(
      (listener) => manager.subscribe(sessionId, listener),
      [manager, sessionId],
    ),
    React.useCallback(() => manager.getSnapshot(sessionId), [manager, sessionId]),
    () => null,
  )
}
