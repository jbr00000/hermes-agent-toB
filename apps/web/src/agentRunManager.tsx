import React from 'react'
import type { QueryClient } from '@tanstack/react-query'
import { api } from './api'
import type {
  ActiveModelRun,
  AgentTaskDetail,
  AgentTaskStatus,
  ChatMessage,
  PermissionMode,
  ToolApproval,
  ToolEvent,
} from './types'

export type AgentRunStatus =
  | 'connecting'
  | 'streaming'
  | 'cancelling'
  | 'completed'
  | 'cancelled'
  | 'failed'

export interface AgentRunSnapshot {
  taskId: string
  sessionId: string
  requestId: string
  phase: 'plan' | 'execute'
  source: 'local' | 'restored'
  status: AgentRunStatus
  taskStatus: AgentTaskStatus
  permissionMode: PermissionMode
  userMessage: ChatMessage | null
  assistantMessage: ChatMessage
  toolEvents: ToolEvent[]
  toolApprovals: ToolApproval[]
  sequence: number
  startedAt: number
  updatedAt: number
  error: string | null
}

interface InternalRun {
  snapshot: AgentRunSnapshot
  pendingContent: string
  flushTimer: number | null
}

type AgentApi = Pick<
  typeof api,
  | 'streamTaskPlan'
  | 'streamTaskExecute'
  | 'streamTaskEvents'
  | 'cancelTask'
  | 'listToolApprovals'
  | 'decideToolApproval'
>

const ACTIVE_STATUSES = new Set<AgentRunStatus>(['connecting', 'streaming', 'cancelling'])

/** tool.approval_required 的 SSE 负载是后端的 snake_case 审批行。 */
function approvalFromEvent(event: Record<string, unknown>): ToolApproval | null {
  if (typeof event.id !== 'string' || typeof event.tool_name !== 'string') return null
  const status = typeof event.status === 'string' ? event.status : 'pending'
  return {
    id: event.id,
    taskId: typeof event.task_id === 'string' ? event.task_id : '',
    runRequestId: typeof event.run_request_id === 'string' ? event.run_request_id : '',
    toolName: event.tool_name,
    commandPreview: typeof event.command_preview === 'string' ? event.command_preview : '',
    status: (['pending', 'approved', 'denied', 'expired'].includes(status)
      ? status
      : 'pending') as ToolApproval['status'],
    createdAt: typeof event.created_at === 'number' ? event.created_at * 1000 : Date.now(),
    decidedAt: typeof event.decided_at === 'number' ? event.decided_at * 1000 : null,
  }
}

export function isAgentRunActive(run: AgentRunSnapshot | null): boolean {
  return run !== null && ACTIVE_STATUSES.has(run.status)
}

export function mergeAgentMessages(
  persistedMessages: ChatMessage[],
  run: AgentRunSnapshot | null,
): ChatMessage[] {
  if (!run) return persistedMessages
  const messages = [...persistedMessages]
  if (run.userMessage && !messages.some((message) => message.id === run.userMessage?.id)) {
    messages.push(run.userMessage)
  }
  const assistantPersisted = messages.some((message) => (
    message.id === run.assistantMessage.id
    || (message.role === 'assistant' && message.modelRunId === run.requestId)
  ))
  if (!assistantPersisted) messages.push(run.assistantMessage)
  return messages
}

function displayTime(timestamp: number): string {
  return new Date(timestamp).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
  })
}

export class AgentRunManager {
  private readonly runs = new Map<string, InternalRun>()
  private readonly listeners = new Map<string, Set<() => void>>()
  private readonly controllers = new Map<string, AbortController>()
  private readonly scrollPositions = new Map<string, number>()

  constructor(
    private readonly queryClient: QueryClient,
    private readonly agentApi: AgentApi = api,
  ) {}

  getSnapshot(taskId: string): AgentRunSnapshot | null {
    return this.runs.get(taskId)?.snapshot ?? null
  }

  subscribe(taskId: string, listener: () => void): () => void {
    const listeners = this.listeners.get(taskId) ?? new Set<() => void>()
    listeners.add(listener)
    this.listeners.set(taskId, listeners)
    return () => {
      listeners.delete(listener)
      if (listeners.size === 0) this.listeners.delete(taskId)
    }
  }

  getScrollPosition(taskId: string): number | null {
    return this.scrollPositions.get(taskId) ?? null
  }

  setScrollPosition(taskId: string, scrollTop: number): void {
    this.scrollPositions.set(taskId, Math.max(0, scrollTop))
  }

  clearAll(): void {
    const taskIds = [...this.runs.keys()]
    for (const run of this.runs.values()) {
      if (run.flushTimer !== null) window.clearTimeout(run.flushTimer)
    }
    this.runs.clear()
    this.scrollPositions.clear()
    const controllers = [...this.controllers.values()]
    this.controllers.clear()
    controllers.forEach((controller) => controller.abort())
    taskIds.forEach((taskId) => this.notify(taskId))
  }

  startPlan(task: AgentTaskDetail, message: string): string {
    return this.start(task, 'plan', message)
  }

  startExecute(task: AgentTaskDetail): string {
    return this.start(task, 'execute', null)
  }

  async cancel(taskId: string): Promise<void> {
    const run = this.runs.get(taskId)
    if (!run || !isAgentRunActive(run.snapshot)) return
    this.update(taskId, (snapshot) => ({
      ...snapshot,
      status: 'cancelling',
      updatedAt: Date.now(),
      error: null,
    }))
    try {
      await this.agentApi.cancelTask(taskId)
    } catch (error) {
      this.update(taskId, (snapshot) => ({
        ...snapshot,
        status: 'streaming',
        updatedAt: Date.now(),
        error: error instanceof Error ? error.message : '停止任务失败',
      }))
      throw error
    }
  }

  async decideApproval(
    taskId: string,
    approvalId: string,
    decision: 'allow' | 'deny' | 'allow_all',
  ): Promise<void> {
    const decided = await this.agentApi.decideToolApproval(taskId, approvalId, decision)
    this.update(taskId, (snapshot) => ({
      ...snapshot,
      toolApprovals: snapshot.toolApprovals.map((item) => (
        item.id === approvalId ? { ...item, status: decided.status, decidedAt: decided.decidedAt } : item
      )),
      updatedAt: Date.now(),
    }))
  }

  reconcileServerState(task: AgentTaskDetail): void {
    const activeRun = task.activeRun
    const current = this.runs.get(task.id)
    if (activeRun) {
      if (
        current
        && current.snapshot.source === 'local'
        && current.snapshot.requestId === activeRun.id
        && isAgentRunActive(current.snapshot)
      ) return
      if (
        current
        && current.snapshot.source === 'restored'
        && current.snapshot.requestId === activeRun.id
        && current.snapshot.sequence >= activeRun.sequence
        && isAgentRunActive(current.snapshot)
      ) return
      this.restore(task, activeRun)
      return
    }
    if (!current) return
    const persisted = task.messages.some((message) => (
      message.role === 'assistant' && message.modelRunId === current.snapshot.requestId
    ))
    if (persisted || current.snapshot.source === 'restored') this.remove(task.id)
  }

  private start(
    task: AgentTaskDetail,
    phase: 'plan' | 'execute',
    message: string | null,
  ): string {
    const current = this.getSnapshot(task.id)
    if (isAgentRunActive(current)) throw new Error('当前任务正在运行')
    const requestId = crypto.randomUUID()
    const now = Date.now()
    const snapshot: AgentRunSnapshot = {
      taskId: task.id,
      sessionId: task.sessionId,
      requestId,
      phase,
      source: 'local',
      status: 'connecting',
      taskStatus: phase === 'plan' ? 'planning' : 'running',
      permissionMode: phase === 'plan' ? 'read' : task.permission.mode,
      userMessage: message ? {
        id: `agent-user-${requestId}`,
        role: 'user',
        content: message,
        createdAt: displayTime(now),
      } : null,
      assistantMessage: {
        id: `agent-assistant-${requestId}`,
        role: 'assistant',
        content: '',
        createdAt: displayTime(now),
        status: 'streaming',
        modelRunId: requestId,
        thinkingStartedAt: now,
      },
      toolEvents: [],
      toolApprovals: [],
      sequence: 0,
      startedAt: now,
      updatedAt: now,
      error: null,
    }
    this.replace(task.id, snapshot)
    const controller = new AbortController()
    this.controllers.set(requestId, controller)
    this.queryClient.setQueryData<AgentTaskDetail>(['task', task.id], {
      ...task,
      status: snapshot.taskStatus,
      currentRunId: requestId,
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
    })
    void this.consume(task, requestId, phase, message, controller)
    return requestId
  }

  private restore(task: AgentTaskDetail, activeRun: ActiveModelRun): void {
    const now = Date.now()
    const startedAt = activeRun.startedAt || activeRun.observedAt - activeRun.elapsedMs
    const phase = activeRun.phase ?? (task.status === 'planning' ? 'plan' : 'execute')
    this.replace(task.id, {
      taskId: task.id,
      sessionId: task.sessionId,
      requestId: activeRun.id,
      phase,
      source: 'restored',
      status: activeRun.status === 'queued' ? 'connecting' : 'streaming',
      taskStatus: task.status,
      permissionMode: phase === 'plan' ? 'read' : task.permission.mode,
      userMessage: null,
      assistantMessage: {
        id: `active-agent-${activeRun.id}`,
        role: 'assistant',
        content: activeRun.partialContent,
        createdAt: displayTime(startedAt),
        status: 'streaming',
        modelRunId: activeRun.id,
        thinkingStartedAt: startedAt,
      },
      toolEvents: task.events.filter((event) => event.runId === activeRun.id),
      toolApprovals: [],
      sequence: activeRun.sequence,
      startedAt,
      updatedAt: activeRun.snapshotUpdatedAt ?? now,
      error: null,
    })
    // 重连时用数据库里的 pending 审批重建 ground truth（SSE 重放可能漏/重）。
    void this.agentApi.listToolApprovals(task.id, 'pending').then((approvals) => {
      const pending = approvals.filter((approval) => approval.runRequestId === activeRun.id)
      if (!pending.length) return
      this.update(task.id, (snapshot) => {
        if (snapshot.requestId !== activeRun.id || !isAgentRunActive(snapshot)) return snapshot
        const known = new Set(snapshot.toolApprovals.map((approval) => approval.id))
        const fresh = pending.filter((approval) => !known.has(approval.id))
        if (!fresh.length) return snapshot
        return {
          ...snapshot,
          toolApprovals: [...snapshot.toolApprovals, ...fresh],
          updatedAt: Date.now(),
        }
      })
    }).catch(() => undefined)
    if (!this.controllers.has(activeRun.id)) {
      const controller = new AbortController()
      this.controllers.set(activeRun.id, controller)
      void this.consume(task, activeRun.id, phase, null, controller, true)
    }
  }

  private async consume(
    task: AgentTaskDetail,
    requestId: string,
    phase: 'plan' | 'execute',
    message: string | null,
    controller: AbortController,
    reconnect = false,
  ): Promise<void> {
    let terminalEventReceived = false
    const handlers = {
      onSession: (event: Record<string, unknown>) => {
        this.update(task.id, (snapshot) => ({
          ...snapshot,
          status: 'streaming',
          userMessage: event.message && typeof event.message !== 'string'
            ? event.message as ChatMessage
            : snapshot.userMessage,
          updatedAt: Date.now(),
        }))
      },
      onDelta: (event: Record<string, unknown>) => {
        if (typeof event.content === 'string') this.queueDelta(task.id, event.content)
      },
      onFinal: (event: Record<string, unknown>) => {
        terminalEventReceived = true
        this.flush(task.id)
        this.update(task.id, (snapshot) => {
          const terminalStatus = event.status === 'cancelled'
            ? 'cancelled' as const
            : event.status === 'failed'
              ? 'failed' as const
              : 'completed' as const
          return {
            ...snapshot,
            status: terminalStatus,
            assistantMessage: event.message && typeof event.message !== 'string'
              ? event.message as ChatMessage
              : {
                  ...snapshot.assistantMessage,
                  content: typeof event.content === 'string'
                    ? event.content
                    : snapshot.assistantMessage.content,
                  status: terminalStatus,
                },
            updatedAt: Date.now(),
            error: terminalStatus === 'failed' ? '任务中的工具执行失败' : null,
          }
        })
      },
      onError: (event: Record<string, unknown>) => {
        terminalEventReceived = true
        this.flush(task.id)
        this.markFailed(
          task.id,
          typeof event.message === 'string' ? event.message : '任务执行失败',
        )
      },
      onEvent: (eventName: string, event: Record<string, unknown>) => {
        if (eventName === 'task.status' && typeof event.status === 'string') {
          this.update(task.id, (snapshot) => ({
            ...snapshot,
            taskStatus: event.status as AgentTaskStatus,
            permissionMode: typeof event.permission_mode === 'string'
              ? event.permission_mode as PermissionMode
              : snapshot.permissionMode,
            updatedAt: Date.now(),
          }))
        }
        // 审批事件必须在 tool.* 通用分支之前处理（名字同样以 tool. 开头，
        // 但它们不是 ToolEvent，且 SSE 重放会重复送达——按审批 id 去重）。
        if (eventName === 'tool.approval_required') {
          const approval = approvalFromEvent(event)
          if (!approval) return
          this.update(task.id, (snapshot) => {
            if (snapshot.toolApprovals.some((item) => item.id === approval.id)) {
              return snapshot
            }
            return {
              ...snapshot,
              toolApprovals: [...snapshot.toolApprovals, approval],
              updatedAt: Date.now(),
            }
          })
          return
        }
        if (eventName === 'tool.approval_resolved') {
          const approvalId = typeof event.approval_id === 'string' ? event.approval_id : null
          const status = typeof event.status === 'string' ? event.status : null
          if (!approvalId || !status) return
          this.update(task.id, (snapshot) => ({
            ...snapshot,
            toolApprovals: snapshot.toolApprovals.map((item) => (
              item.id === approvalId
                ? { ...item, status: status as ToolApproval['status'], decidedAt: Date.now() }
                : item
            )),
            updatedAt: Date.now(),
          }))
          return
        }
        if (eventName.startsWith('tool.')) {
          this.update(task.id, (snapshot) => ({
            ...snapshot,
            toolEvents: (() => {
              const eventId = typeof event.id === 'number' ? event.id : -Date.now()
              if (snapshot.toolEvents.some((item) => item.id === eventId)) {
                return snapshot.toolEvents
              }
              return [...snapshot.toolEvents, {
                id: eventId,
                taskId: task.id,
                runId: requestId,
                sequence: typeof event.sequence === 'number'
                  ? event.sequence
                  : snapshot.toolEvents.length + 1,
                eventType: eventName,
                toolName: typeof event.tool_name === 'string' ? event.tool_name : null,
                riskLevel: ['read', 'controlled_write', 'high_risk'].includes(
                  String(event.risk_level),
                )
                  ? event.risk_level as ToolEvent['riskLevel']
                  : 'unknown',
                status: typeof event.status === 'string' ? event.status : 'running',
                payload: typeof event.payload === 'object' && event.payload
                  ? event.payload as Record<string, unknown>
                  : {},
                createdAt: Date.now(),
              }]
            })(),
            updatedAt: Date.now(),
          }))
        }
      },
    }
    try {
      if (reconnect) {
        const contentOffset = this.getSnapshot(task.id)?.assistantMessage.content.length ?? 0
        await this.agentApi.streamTaskEvents(
          task.id,
          requestId,
          contentOffset,
          handlers,
          controller.signal,
        )
      } else if (phase === 'plan') {
        await this.agentApi.streamTaskPlan(
          task.id,
          requestId,
          message ?? '',
          handlers,
          controller.signal,
        )
      } else {
        await this.agentApi.streamTaskExecute(
          task.id,
          requestId,
          handlers,
          controller.signal,
        )
      }
      if (!terminalEventReceived && isAgentRunActive(this.getSnapshot(task.id))) {
        this.markFailed(task.id, '流式连接已结束，正在同步服务端状态')
      }
    } catch (error) {
      this.flush(task.id)
      this.markFailed(
        task.id,
        error instanceof Error ? error.message : '无法连接 Agent 服务',
      )
    } finally {
      if (this.controllers.get(requestId) === controller) this.controllers.delete(requestId)
      await Promise.all([
        this.queryClient.invalidateQueries({ queryKey: ['task', task.id] }),
        this.queryClient.invalidateQueries({ queryKey: ['tasks'] }),
      ])
    }
  }

  private queueDelta(taskId: string, content: string): void {
    const run = this.runs.get(taskId)
    if (!run) return
    run.pendingContent += content
    if (run.flushTimer === null) {
      run.flushTimer = window.setTimeout(() => this.flush(taskId), 50)
    }
  }

  private flush(taskId: string): void {
    const run = this.runs.get(taskId)
    if (!run) return
    if (run.flushTimer !== null) {
      window.clearTimeout(run.flushTimer)
      run.flushTimer = null
    }
    if (!run.pendingContent) return
    const content = run.pendingContent
    run.pendingContent = ''
    this.update(taskId, (snapshot) => ({
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

  private markFailed(taskId: string, error: string): void {
    this.update(taskId, (snapshot) => ({
      ...snapshot,
      status: 'failed',
      taskStatus: 'failed',
      permissionMode: 'read',
      updatedAt: Date.now(),
      error,
      assistantMessage: {
        ...snapshot.assistantMessage,
        content: snapshot.assistantMessage.content || '任务未完成，请重试。',
        status: 'failed',
      },
    }))
  }

  private replace(taskId: string, snapshot: AgentRunSnapshot): void {
    const existing = this.runs.get(taskId)
    if (existing?.flushTimer !== null && existing?.flushTimer !== undefined) {
      window.clearTimeout(existing.flushTimer)
    }
    this.runs.set(taskId, { snapshot, pendingContent: '', flushTimer: null })
    this.notify(taskId)
  }

  private update(
    taskId: string,
    updater: (snapshot: AgentRunSnapshot) => AgentRunSnapshot,
  ): void {
    const run = this.runs.get(taskId)
    if (!run) return
    run.snapshot = updater(run.snapshot)
    this.notify(taskId)
  }

  private remove(taskId: string): void {
    const run = this.runs.get(taskId)
    if (run?.flushTimer !== null && run?.flushTimer !== undefined) {
      window.clearTimeout(run.flushTimer)
    }
    this.runs.delete(taskId)
    this.notify(taskId)
  }

  private notify(taskId: string): void {
    this.listeners.get(taskId)?.forEach((listener) => listener())
  }
}

const AgentRunContext = React.createContext<AgentRunManager | null>(null)

export function AgentRunProvider({
  manager,
  children,
}: {
  manager: AgentRunManager
  children: React.ReactNode
}) {
  return <AgentRunContext.Provider value={manager}>{children}</AgentRunContext.Provider>
}

export function useAgentRunManager(): AgentRunManager {
  const manager = React.useContext(AgentRunContext)
  if (!manager) throw new Error('AgentRunProvider is missing')
  return manager
}

export function useAgentRun(taskId: string): AgentRunSnapshot | null {
  const manager = useAgentRunManager()
  return React.useSyncExternalStore(
    React.useCallback(
      (listener) => manager.subscribe(taskId, listener),
      [manager, taskId],
    ),
    React.useCallback(() => manager.getSnapshot(taskId), [manager, taskId]),
    () => null,
  )
}
