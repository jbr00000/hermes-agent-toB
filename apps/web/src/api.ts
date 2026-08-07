import type {
  ActiveModelRun,
  AgentTaskDetail,
  AuthUser,
  ChatMessage,
  ConversationDetail,
  ConversationSummary,
  PermissionMode,
  SessionSummary,
  TaskPermission,
  TaskPlan,
  TaskRun,
  ToolEvent,
} from './types'

const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '') ?? '/api'

let accessToken: string | null = null
let refreshPromise: Promise<AuthUser | null> | null = null

interface AuthResponse {
  access_token: string
  token_type: string
  user: AuthUser
}

interface BackendSession {
  id: string
  title: string
  interaction_type: 'chat' | 'agent'
  status: string
  pinned: boolean
  archived: boolean
  updated_at: number
}

interface BackendMessage {
  id: string
  role: ChatMessage['role']
  content: string
  status?: ChatMessage['status']
  model_run_id?: string
  duration_ms?: number
  created_at?: number
  timestamp?: number
}

interface BackendModelRun {
  id: string
  status: 'queued' | 'running'
  phase?: 'plan' | 'execute' | null
  started_at: number
  elapsed_ms: number
  partial_content?: string
  sequence?: number
  snapshot_updated_at?: number | null
}

interface BackendTaskPermission {
  id: string | null
  mode: PermissionMode
  created_at: number | null
  expires_at: number | null
}

interface BackendTaskPlan {
  id: string
  task_id: string
  version: number
  content: string
  status: TaskPlan['status']
  created_at: number
  approved_at: number | null
}

interface BackendTaskRun {
  id: string
  task_id: string
  phase: TaskRun['phase']
  attempt: number
  status: TaskRun['status']
  error: string | null
  started_at: number
  completed_at: number | null
}

interface BackendToolEvent {
  id: number
  task_id: string
  run_id: string
  sequence: number
  event_type: string
  tool_name: string | null
  status: string
  payload: Record<string, unknown>
  created_at: number
}

interface BackendTask {
  id: string
  session_id: string
  title: string
  status: AgentTaskDetail['status']
  risk_level: AgentTaskDetail['risk']
  current_run_id: string | null
  created_at: number
  updated_at: number
  completed_at: number | null
  session: BackendSession
  messages: BackendMessage[]
  active_run: BackendModelRun | null
  plan: BackendTaskPlan | null
  permission: BackendTaskPermission
  runs: BackendTaskRun[]
  events: BackendToolEvent[]
  artifacts: Array<{
    id: string
    name: string
    path: string
    media_type: string | null
    size_bytes: number | null
    status: string
  }>
}

type BackendTaskSummary = Pick<
  BackendTask,
  'id' | 'title' | 'status' | 'risk_level' | 'updated_at'
>

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

async function parseError(response: Response): Promise<ApiError> {
  try {
    const body = await response.json() as { detail?: string }
    return new ApiError(response.status, body.detail ?? `请求失败 (${response.status})`)
  } catch {
    return new ApiError(response.status, `请求失败 (${response.status})`)
  }
}

async function refreshSession(): Promise<AuthUser | null> {
  if (refreshPromise) return refreshPromise
  refreshPromise = fetch(`${API_BASE}/auth/refresh`, {
    method: 'POST',
    credentials: 'include',
  }).then(async (response) => {
    if (!response.ok) {
      accessToken = null
      return null
    }
    const body = await response.json() as AuthResponse
    accessToken = body.access_token
    return body.user
  }).finally(() => {
    refreshPromise = null
  })
  return refreshPromise
}

async function apiFetch(path: string, init: RequestInit = {}, retry = true): Promise<Response> {
  const headers = new Headers(init.headers)
  if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`)
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
    credentials: 'include',
  })
  if (response.status === 401 && retry && await refreshSession()) {
    return apiFetch(path, init, false)
  }
  return response
}

function formatTime(timestamp: number): string {
  return new Date(timestamp * 1000).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
  })
}

function conversationPeriod(timestamp: number): ConversationSummary['period'] {
  const value = new Date(timestamp * 1000)
  const now = new Date()
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const startYesterday = startToday - 24 * 60 * 60 * 1000
  if (value.getTime() >= startToday) return 'today'
  if (value.getTime() >= startYesterday) return 'yesterday'
  return 'earlier'
}

function toConversation(row: BackendSession): ConversationSummary {
  return {
    id: row.id,
    title: row.title,
    space: '企业工作区',
    updatedAt: formatTime(row.updated_at),
    period: conversationPeriod(row.updated_at),
    pinned: row.pinned,
  }
}

function toMessage(row: BackendMessage): ChatMessage {
  return {
    id: row.id,
    role: row.role,
    content: row.content,
    createdAt: formatTime(row.created_at ?? row.timestamp ?? Date.now() / 1000),
    status: row.status,
    modelRunId: row.model_run_id,
    durationMs: row.duration_ms,
  }
}

function toActiveRun(row: BackendModelRun | null): ActiveModelRun | null {
  if (!row) return null
  return {
    id: row.id,
    status: row.status,
    phase: row.phase ?? null,
    startedAt: row.started_at * 1000,
    elapsedMs: row.elapsed_ms,
    observedAt: Date.now(),
    partialContent: row.partial_content ?? '',
    sequence: row.sequence ?? 0,
    snapshotUpdatedAt: row.snapshot_updated_at ? row.snapshot_updated_at * 1000 : null,
  }
}

function toPermission(row: BackendTaskPermission): TaskPermission {
  return {
    id: row.id,
    mode: row.mode,
    createdAt: row.created_at === null ? null : row.created_at * 1000,
    expiresAt: row.expires_at === null ? null : row.expires_at * 1000,
  }
}

function toPlan(row: BackendTaskPlan | null): TaskPlan | null {
  if (!row) return null
  return {
    id: row.id,
    taskId: row.task_id,
    version: row.version,
    content: row.content,
    status: row.status,
    createdAt: row.created_at * 1000,
    approvedAt: row.approved_at === null ? null : row.approved_at * 1000,
  }
}

function toTaskRun(row: BackendTaskRun): TaskRun {
  return {
    id: row.id,
    taskId: row.task_id,
    phase: row.phase,
    attempt: row.attempt,
    status: row.status,
    error: row.error,
    startedAt: row.started_at * 1000,
    completedAt: row.completed_at === null ? null : row.completed_at * 1000,
  }
}

function toToolEvent(row: BackendToolEvent): ToolEvent {
  return {
    id: row.id,
    taskId: row.task_id,
    runId: row.run_id,
    sequence: row.sequence,
    eventType: row.event_type,
    toolName: row.tool_name,
    status: row.status,
    payload: row.payload,
    createdAt: row.created_at * 1000,
  }
}

function toTask(row: BackendTask): AgentTaskDetail {
  return {
    id: row.id,
    sessionId: row.session_id,
    title: row.title,
    status: row.status,
    risk: row.risk_level,
    currentRunId: row.current_run_id,
    createdAt: row.created_at * 1000,
    updatedAt: row.updated_at * 1000,
    completedAt: row.completed_at === null ? null : row.completed_at * 1000,
    session: {
      id: row.session.id,
      title: row.session.title,
      status: row.session.status,
    },
    messages: row.messages.map(toMessage),
    activeRun: toActiveRun(row.active_run),
    plan: toPlan(row.plan),
    permission: toPermission(row.permission),
    runs: row.runs.map(toTaskRun),
    events: row.events.map(toToolEvent),
    artifacts: row.artifacts.map((artifact) => ({
      id: artifact.id,
      name: artifact.name,
      path: artifact.path,
      mediaType: artifact.media_type,
      sizeBytes: artifact.size_bytes,
      status: artifact.status,
    })),
  }
}

function toTaskSummary(row: BackendTaskSummary): SessionSummary {
  return {
    id: row.id,
    title: row.title,
    space: '企业工作区',
    status: row.status,
    updatedAt: formatTime(row.updated_at),
    risk: row.risk_level,
  }
}

export interface ChatStreamEvent {
  session_id?: string
  request_id?: string
  title?: string
  content?: string
  status?: string
  message?: ChatMessage | string
  code?: string
  [key: string]: unknown
}

interface BackendChatStreamEvent extends Omit<ChatStreamEvent, 'message'> {
  message?: BackendMessage | string
}

export interface ChatStreamHandlers {
  onSession?: (event: ChatStreamEvent) => void
  onDelta?: (event: ChatStreamEvent) => void
  onFinal?: (event: ChatStreamEvent) => void
  onError?: (event: ChatStreamEvent) => void
  onEvent?: (eventName: string, event: ChatStreamEvent) => void
}

function dispatchSseBlock(block: string, handlers: ChatStreamHandlers): void {
  let event = 'message'
  const data: string[] = []
  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
  }
  if (data.length === 0) return
  const raw = JSON.parse(data.join('\n')) as BackendChatStreamEvent
  const payload: ChatStreamEvent = {
    ...raw,
    message: raw.message && typeof raw.message !== 'string' ? toMessage(raw.message) : raw.message,
  }
  handlers.onEvent?.(event, payload)
  if (event === 'session') handlers.onSession?.(payload)
  if (event === 'delta') handlers.onDelta?.(payload)
  if (event === 'final') handlers.onFinal?.(payload)
  if (event === 'error') handlers.onError?.(payload)
}

async function consumeEventStream(
  response: Response,
  handlers: ChatStreamHandlers,
): Promise<void> {
  if (!response.body) throw new ApiError(502, '服务端未返回流式响应')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, '\n')
    const blocks = buffer.split('\n\n')
    buffer = blocks.pop() ?? ''
    for (const block of blocks) dispatchSseBlock(block, handlers)
    if (done) break
  }
  if (buffer.trim()) dispatchSseBlock(buffer, handlers)
}

export const api = {
  async restoreSession(): Promise<AuthUser | null> {
    const response = await fetch(`${API_BASE}/auth/session`, { credentials: 'include' })
    if (!response.ok) return null
    const body = await response.json() as (AuthResponse & { authenticated: true }) | { authenticated: false }
    if (!body.authenticated) return null
    accessToken = body.access_token
    return body.user
  },

  async login(username: string, password: string): Promise<AuthUser> {
    const response = await fetch(`${API_BASE}/auth/login`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    })
    if (!response.ok) throw await parseError(response)
    const body = await response.json() as AuthResponse
    accessToken = body.access_token
    return body.user
  },

  async logout(): Promise<void> {
    await apiFetch('/auth/logout', { method: 'POST' }, false)
    accessToken = null
  },

  async listConversations(): Promise<ConversationSummary[]> {
    const response = await apiFetch('/sessions?interaction_type=chat')
    if (!response.ok) throw await parseError(response)
    const body = await response.json() as { sessions: BackendSession[] }
    return body.sessions.map(toConversation)
  },

  async createConversation(): Promise<ConversationSummary> {
    const response = await apiFetch('/sessions', {
      method: 'POST',
      body: JSON.stringify({ interaction_type: 'chat' }),
    })
    if (!response.ok) throw await parseError(response)
    const body = await response.json() as { session: BackendSession }
    return toConversation(body.session)
  },

  async getConversation(sessionId: string): Promise<ConversationDetail> {
    if (sessionId.startsWith('draft-')) {
      return { messages: [], status: 'idle', activeRun: null }
    }
    const response = await apiFetch(`/sessions/${encodeURIComponent(sessionId)}`)
    if (!response.ok) throw await parseError(response)
    const body = await response.json() as {
      session: BackendSession
      messages: BackendMessage[]
      active_run: BackendModelRun | null
    }
    return {
      messages: body.messages.map(toMessage),
      status: body.session.status,
      activeRun: toActiveRun(body.active_run),
    }
  },

  async updateConversation(
    sessionId: string,
    changes: { title?: string; pinned?: boolean; archived?: boolean },
  ): Promise<ConversationSummary> {
    const response = await apiFetch(`/sessions/${encodeURIComponent(sessionId)}`, {
      method: 'PATCH',
      body: JSON.stringify(changes),
    })
    if (!response.ok) throw await parseError(response)
    const body = await response.json() as { session: BackendSession }
    return toConversation(body.session)
  },

  async deleteConversation(sessionId: string): Promise<void> {
    const response = await apiFetch(`/sessions/${encodeURIComponent(sessionId)}`, {
      method: 'DELETE',
    })
    if (!response.ok) throw await parseError(response)
  },

  async listTasks(): Promise<SessionSummary[]> {
    const response = await apiFetch('/tasks')
    if (!response.ok) throw await parseError(response)
    const body = await response.json() as { tasks: BackendTaskSummary[] }
    return body.tasks.map(toTaskSummary)
  },

  async createTask(title?: string): Promise<AgentTaskDetail> {
    const response = await apiFetch('/tasks', {
      method: 'POST',
      body: JSON.stringify({ title }),
    })
    if (!response.ok) throw await parseError(response)
    const body = await response.json() as { task: BackendTask }
    return toTask(body.task)
  },

  async getTask(taskId: string): Promise<AgentTaskDetail> {
    const response = await apiFetch(`/tasks/${encodeURIComponent(taskId)}`)
    if (!response.ok) throw await parseError(response)
    const body = await response.json() as { task: BackendTask }
    return toTask(body.task)
  },

  async deleteTask(taskId: string): Promise<void> {
    const response = await apiFetch(`/tasks/${encodeURIComponent(taskId)}`, {
      method: 'DELETE',
    })
    if (!response.ok) throw await parseError(response)
  },

  async setTaskPermission(
    taskId: string,
    mode: PermissionMode,
    ttlSeconds = 900,
  ): Promise<TaskPermission> {
    const response = await apiFetch(`/tasks/${encodeURIComponent(taskId)}/permission`, {
      method: 'PUT',
      body: JSON.stringify({ mode, ttl_seconds: ttlSeconds }),
    })
    if (!response.ok) throw await parseError(response)
    const body = await response.json() as { permission: BackendTaskPermission }
    return toPermission(body.permission)
  },

  async approveTask(taskId: string): Promise<AgentTaskDetail> {
    const response = await apiFetch(`/tasks/${encodeURIComponent(taskId)}/approve`, {
      method: 'POST',
    })
    if (!response.ok) throw await parseError(response)
    const body = await response.json() as { task: BackendTask }
    return toTask(body.task)
  },

  async streamTaskPlan(
    taskId: string,
    requestId: string,
    message: string,
    handlers: ChatStreamHandlers,
    signal?: AbortSignal,
  ): Promise<void> {
    const response = await apiFetch(`/tasks/${encodeURIComponent(taskId)}/plan`, {
      method: 'POST',
      signal,
      body: JSON.stringify({ request_id: requestId, message }),
    })
    if (!response.ok) throw await parseError(response)
    await consumeEventStream(response, handlers)
  },

  async streamTaskExecute(
    taskId: string,
    requestId: string,
    handlers: ChatStreamHandlers,
    signal?: AbortSignal,
  ): Promise<void> {
    const response = await apiFetch(`/tasks/${encodeURIComponent(taskId)}/execute`, {
      method: 'POST',
      signal,
      body: JSON.stringify({ request_id: requestId }),
    })
    if (!response.ok) throw await parseError(response)
    await consumeEventStream(response, handlers)
  },

  async streamTaskEvents(
    taskId: string,
    requestId: string,
    contentOffset: number,
    handlers: ChatStreamHandlers,
    signal?: AbortSignal,
  ): Promise<void> {
    const response = await apiFetch(
      `/tasks/${encodeURIComponent(taskId)}/runs/${encodeURIComponent(requestId)}/events?content_offset=${Math.max(0, contentOffset)}`,
      { signal },
    )
    if (!response.ok) throw await parseError(response)
    await consumeEventStream(response, handlers)
  },

  async cancelTask(taskId: string): Promise<void> {
    const response = await apiFetch(`/tasks/${encodeURIComponent(taskId)}/cancel`, {
      method: 'POST',
    })
    if (!response.ok) throw await parseError(response)
  },

  async streamChat(
    sessionId: string,
    requestId: string,
    message: string,
    handlers: ChatStreamHandlers,
    signal?: AbortSignal,
  ): Promise<void> {
    const response = await apiFetch('/chat', {
      method: 'POST',
      signal,
      body: JSON.stringify({
        session_id: sessionId,
        request_id: requestId,
        interaction_type: 'chat',
        message,
      }),
    })
    if (!response.ok) throw await parseError(response)
    await consumeEventStream(response, handlers)
  },

  async cancelChat(requestId: string): Promise<void> {
    const response = await apiFetch(`/chat/${encodeURIComponent(requestId)}/cancel`, {
      method: 'POST',
    })
    if (!response.ok && response.status !== 404) throw await parseError(response)
  },

  async getFeatures() {
    const response = await apiFetch('/features')
    if (!response.ok) throw await parseError(response)
    const body = await response.json() as { features: { host_terminal?: boolean } }
    return {
      host_terminal: Boolean(body.features.host_terminal),
      sandbox: 'docker',
      provider: 'deepseek',
      model: 'deepseek-v4-pro',
    }
  },
}
