import type {
  ActiveModelRun,
  AdminUserRow,
  AgentTaskDetail,
  AuditEvent,
  AuthUser,
  ChatMessage,
  ConversationDetail,
  ConversationSummary,
  KnowledgeBase,
  KnowledgeChunk,
  KnowledgeDocument,
  KnowledgeStats,
  PermissionMode,
  SessionSummary,
  TaskPermission,
  TaskPlan,
  TaskRun,
  ToolApproval,
  ToolEvent,
  UserFeatures,
  UserRole,
} from './types'

const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '') ?? '/api'

let accessToken: string | null = null
let refreshPromise: Promise<AuthUser | null> | null = null
let currentUserUpdater: ((user: AuthUser) => void) | null = null

/** 403 自愈钩子：App 挂载时注册 setUser，后台重拉 /auth/me 后刷新前端权限视图。 */
export function setCurrentUserUpdater(updater: ((user: AuthUser) => void) | null): void {
  currentUserUpdater = updater
}

interface BackendAuthUser {
  id: string
  username: string
  role: UserRole
  features: UserFeatures
  must_change_password?: boolean
}

function toAuthUser(row: BackendAuthUser): AuthUser {
  return {
    id: row.id,
    username: row.username,
    role: row.role,
    features: row.features,
    mustChangePassword: row.must_change_password === true,
  }
}

interface AuthResponse {
  access_token: string
  token_type: string
  user: BackendAuthUser
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
  risk_level: ToolEvent['riskLevel']
  status: string
  payload: Record<string, unknown>
  created_at: number
}

interface BackendToolApproval {
  id: string
  task_id: string
  run_request_id: string
  user_id: string
  tool_name: string
  command_preview: string
  args_fingerprint: string
  status: ToolApproval['status']
  decided_by: string | null
  created_at: number
  decided_at: number | null
}

interface BackendTask {
  id: string
  session_id: string
  source_session_id: string | null
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

interface BackendAuditEvent {
  id: number
  event_type: string
  session_id: string | null
  user_id: string | null
  username: string
  status: string
  mode: string | null
  metadata: {
    tool_name?: string
    args?: { keys?: string[]; query?: string; url?: string; urls?: string[] }
    duration_ms?: number
  }
  error: string | null
  created_at: number
}

interface BackendKnowledgeBase {
  id: string
  name: string
  description: string | null
  creator_id: string | null
  doc_count: number
  chunk_count: number
  created_at: number
  updated_at: number
}

interface BackendKnowledgeDocument {
  id: string
  kb_id: string
  uploader_id: string
  title: string
  file_name: string
  file_ext: string
  size_bytes: number
  status: KnowledgeDocument['status']
  error: string | null
  parser: KnowledgeDocument['parser']
  chunk_count: number
  retry_count: number
  created_at: number
  updated_at: number
  finished_at: number | null
}

interface BackendKnowledgeChunk {
  id: string
  kb_id: string
  doc_id: string
  doc_name: string
  chunk_title: string | null
  content: string
  doc_pos: number
  token_num: number
  is_use: boolean
}

interface BackendAdminUser {
  id: string
  username: string
  role: UserRole
  status: 'active' | 'disabled'
  features: UserFeatures
  created_at: number
}

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
    return toAuthUser(body.user)
  }).finally(() => {
    refreshPromise = null
  })
  return refreshPromise
}

async function apiFetch(path: string, init: RequestInit = {}, retry = true): Promise<Response> {
  const headers = new Headers(init.headers)
  if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`)
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
    credentials: 'include',
  })
  if (response.status === 401 && retry && await refreshSession()) {
    return apiFetch(path, init, false)
  }
  // 403 = 登录有效但权限被管理员收回/调整。后台重拉 /auth/me 自愈前端视图
  // （fire-and-forget，不阻塞原请求的错误传播；retry=false 防递归）。
  if (response.status === 403 && currentUserUpdater) {
    const updater = currentUserUpdater
    void apiFetch('/auth/me', {}, false)
      .then(async (meResponse) => {
        if (!meResponse.ok) return
        const body = await meResponse.json() as { user: BackendAuthUser }
        updater(toAuthUser(body.user))
      })
      .catch(() => undefined)
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

function toAdminUserRow(row: BackendAdminUser): AdminUserRow {
  return {
    id: row.id,
    username: row.username,
    role: row.role,
    status: row.status,
    features: row.features,
    createdAt: row.created_at,
  }
}

function toAuditEvent(row: BackendAuditEvent): AuditEvent {
  const metadata = row.metadata ?? {}
  const toolName = metadata.tool_name
  const args = metadata.args ?? {}
  let subject: string
  if (toolName) {
    const detail = args.query ?? args.url ?? (args.urls?.length ? args.urls.join('、') : '')
    subject = detail ? `${toolName} · ${detail}` : toolName
  } else if (row.error) {
    subject = row.error
  } else {
    subject = row.session_id ? `会话 ${row.session_id.slice(0, 8)}` : '—'
  }
  return {
    id: row.id,
    time: formatTime(row.created_at),
    eventType: row.event_type,
    username: row.username,
    subject,
    status: row.status,
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
    riskLevel: row.risk_level,
    status: row.status,
    payload: row.payload,
    createdAt: row.created_at * 1000,
  }
}

function toToolApproval(row: BackendToolApproval): ToolApproval {
  return {
    id: row.id,
    taskId: row.task_id,
    runRequestId: row.run_request_id,
    toolName: row.tool_name,
    commandPreview: row.command_preview,
    status: row.status,
    createdAt: row.created_at * 1000,
    decidedAt: row.decided_at === null ? null : row.decided_at * 1000,
  }
}

function toTask(row: BackendTask): AgentTaskDetail {
  return {
    id: row.id,
    sessionId: row.session_id,
    sourceSessionId: row.source_session_id,
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

function toKnowledgeBase(row: BackendKnowledgeBase): KnowledgeBase {
  return {
    id: row.id,
    name: row.name,
    description: row.description,
    docCount: row.doc_count,
    chunkCount: row.chunk_count,
    createdAt: row.created_at * 1000,
    updatedAt: row.updated_at * 1000,
  }
}

function toKnowledgeDocument(row: BackendKnowledgeDocument): KnowledgeDocument {
  return {
    id: row.id,
    kbId: row.kb_id,
    title: row.title,
    fileName: row.file_name,
    fileExt: row.file_ext,
    sizeBytes: row.size_bytes,
    status: row.status,
    error: row.error,
    parser: row.parser,
    chunkCount: row.chunk_count,
    retryCount: row.retry_count,
    uploaderId: row.uploader_id,
    createdAt: row.created_at * 1000,
    updatedAt: row.updated_at * 1000,
    finishedAt: row.finished_at === null ? null : row.finished_at * 1000,
  }
}

function toKnowledgeChunk(row: BackendKnowledgeChunk): KnowledgeChunk {
  return {
    id: row.id,
    kbId: row.kb_id,
    docId: row.doc_id,
    docName: row.doc_name,
    chunkTitle: row.chunk_title ?? '',
    content: row.content,
    docPos: row.doc_pos,
    tokenNum: row.token_num,
    isUse: row.is_use,
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
    return toAuthUser(body.user)
  },

  async login(username: string, password: string, remember = false): Promise<AuthUser> {
    const response = await fetch(`${API_BASE}/auth/login`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password, remember }),
    })
    if (!response.ok) throw await parseError(response)
    const body = await response.json() as AuthResponse
    accessToken = body.access_token
    return toAuthUser(body.user)
  },

  async changePassword(oldPassword: string, newPassword: string): Promise<AuthUser> {
    const response = await fetch(`${API_BASE}/auth/change-password`, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      },
      body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
    })
    if (!response.ok) throw await parseError(response)
    const body = await response.json() as AuthResponse
    accessToken = body.access_token
    return toAuthUser(body.user)
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

  async createTask(title?: string, sourceSessionId?: string): Promise<AgentTaskDetail> {
    const response = await apiFetch('/tasks', {
      method: 'POST',
      body: JSON.stringify({ title, source_session_id: sourceSessionId }),
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

  async listToolApprovals(taskId: string, status?: ToolApproval['status']): Promise<ToolApproval[]> {
    const query = status ? `?status=${status}` : ''
    const response = await apiFetch(
      `/tasks/${encodeURIComponent(taskId)}/tool-approvals${query}`,
    )
    if (!response.ok) throw await parseError(response)
    const body = await response.json() as { approvals: BackendToolApproval[] }
    return body.approvals.map(toToolApproval)
  },

  async decideToolApproval(
    taskId: string,
    approvalId: string,
    decision: 'allow' | 'deny' | 'allow_all',
  ): Promise<ToolApproval> {
    const response = await apiFetch(
      `/tasks/${encodeURIComponent(taskId)}/tool-approvals/${encodeURIComponent(approvalId)}`,
      { method: 'POST', body: JSON.stringify({ decision }) },
    )
    if (!response.ok) throw await parseError(response)
    const body = await response.json() as { approval: BackendToolApproval }
    return toToolApproval(body.approval)
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
    const body = await response.json() as {
      features: { host_terminal?: boolean }
      data_permissions?: { enabled?: boolean; allowed_tables?: string[] | null }
    }
    return {
      host_terminal: Boolean(body.features.host_terminal),
      sandbox: 'docker',
      provider: 'deepseek',
      model: 'deepseek-v4-pro',
      dataPermissions: {
        enabled: Boolean(body.data_permissions?.enabled),
        allowedTables: body.data_permissions?.allowed_tables ?? null,
      },
    }
  },

  async listAuditEvents(limit = 100): Promise<AuditEvent[]> {
    const response = await apiFetch(`/audit/events?limit=${limit}`)
    if (!response.ok) throw await parseError(response)
    const body = await response.json() as { events: BackendAuditEvent[] }
    return body.events.map(toAuditEvent)
  },

  // ------------------------------------------- 知识库（三步：建库 → 上传 → 选择解析）

  async listKnowledgeBases(): Promise<KnowledgeBase[]> {
    const response = await apiFetch('/knowledge/bases')
    if (!response.ok) throw await parseError(response)
    const body = await response.json() as { bases: BackendKnowledgeBase[] }
    return body.bases.map(toKnowledgeBase)
  },

  async createKnowledgeBase(name: string, description?: string): Promise<KnowledgeBase> {
    const response = await apiFetch('/knowledge/bases', {
      method: 'POST',
      body: JSON.stringify({ name, description: description?.trim() || null }),
    })
    if (!response.ok) throw await parseError(response)
    const body = await response.json() as { base: BackendKnowledgeBase }
    return toKnowledgeBase(body.base)
  },

  async renameKnowledgeBase(kbId: string, name: string, description?: string): Promise<KnowledgeBase> {
    const response = await apiFetch(`/knowledge/bases/${encodeURIComponent(kbId)}`, {
      method: 'PATCH',
      body: JSON.stringify({ name, description: description?.trim() || null }),
    })
    if (!response.ok) throw await parseError(response)
    const body = await response.json() as { base: BackendKnowledgeBase }
    return toKnowledgeBase(body.base)
  },

  async deleteKnowledgeBase(kbId: string): Promise<void> {
    const response = await apiFetch(`/knowledge/bases/${encodeURIComponent(kbId)}`, {
      method: 'DELETE',
    })
    if (!response.ok) throw await parseError(response)
  },

  async listKnowledgeDocuments(
    status?: KnowledgeDocument['status'],
    kbId?: string,
  ): Promise<{ documents: KnowledgeDocument[]; stats: KnowledgeStats }> {
    const params = new URLSearchParams()
    if (status) params.set('status', status)
    if (kbId) params.set('kb_id', kbId)
    const query = params.size > 0 ? `?${params.toString()}` : ''
    const response = await apiFetch(`/knowledge/documents${query}`)
    if (!response.ok) throw await parseError(response)
    const body = await response.json() as {
      documents: BackendKnowledgeDocument[]
      stats: KnowledgeStats
    }
    return { documents: body.documents.map(toKnowledgeDocument), stats: body.stats }
  },

  async getKnowledgeDocument(docId: string): Promise<KnowledgeDocument> {
    const response = await apiFetch(`/knowledge/documents/${encodeURIComponent(docId)}`)
    if (!response.ok) throw await parseError(response)
    const body = await response.json() as { document: BackendKnowledgeDocument }
    return toKnowledgeDocument(body.document)
  },

  async listKnowledgeChunks(docId: string): Promise<KnowledgeChunk[]> {
    const response = await apiFetch(`/knowledge/documents/${encodeURIComponent(docId)}/chunks`)
    if (!response.ok) throw await parseError(response)
    const body = await response.json() as { chunks: BackendKnowledgeChunk[] }
    return body.chunks.map(toKnowledgeChunk)
  },

  async uploadKnowledgeDocument(kbId: string, file: File, title?: string): Promise<KnowledgeDocument> {
    const form = new FormData()
    form.append('file', file)
    if (title?.trim()) form.append('title', title.trim())
    const response = await apiFetch(
      `/knowledge/bases/${encodeURIComponent(kbId)}/documents`,
      { method: 'POST', body: form },
    )
    if (!response.ok) throw await parseError(response)
    const body = await response.json() as { document: BackendKnowledgeDocument }
    return toKnowledgeDocument(body.document)
  },

  /** 步骤③：批量把 uploaded/failed 文档入队解析；返回入队与跳过清单。 */
  async parseKnowledgeDocuments(
    documentIds: string[],
  ): Promise<{ queued: { id: string; job_id: string }[]; skipped: { id: string; reason: string }[] }> {
    const response = await apiFetch('/knowledge/documents/parse', {
      method: 'POST',
      body: JSON.stringify({ document_ids: documentIds }),
    })
    if (!response.ok) throw await parseError(response)
    return response.json() as Promise<{
      queued: { id: string; job_id: string }[]
      skipped: { id: string; reason: string }[]
    }>
  },

  async deleteKnowledgeDocument(docId: string): Promise<void> {
    const response = await apiFetch(`/knowledge/documents/${encodeURIComponent(docId)}`, {
      method: 'DELETE',
    })
    if (!response.ok) throw await parseError(response)
  },

  async retryKnowledgeDocument(docId: string): Promise<void> {
    const response = await apiFetch(`/knowledge/documents/${encodeURIComponent(docId)}/retry`, {
      method: 'POST',
    })
    if (!response.ok) throw await parseError(response)
  },

  // ---------------------------------------------------------- 用户管理（superadmin）

  async listUsers(): Promise<AdminUserRow[]> {
    const response = await apiFetch('/users')
    if (!response.ok) throw await parseError(response)
    const body = await response.json() as { users: BackendAdminUser[] }
    return body.users.map(toAdminUserRow)
  },

  async createUser(input: {
    username: string
    password: string
    role: UserRole
    features?: Partial<UserFeatures>
  }): Promise<AdminUserRow> {
    const response = await apiFetch('/users', {
      method: 'POST',
      body: JSON.stringify(input),
    })
    if (!response.ok) throw await parseError(response)
    const body = await response.json() as { user: BackendAdminUser }
    return toAdminUserRow(body.user)
  },

  async updateUserRole(userId: string, role: UserRole): Promise<void> {
    const response = await apiFetch(`/users/${encodeURIComponent(userId)}/role`, {
      method: 'PUT',
      body: JSON.stringify({ role }),
    })
    if (!response.ok) throw await parseError(response)
  },

  async resetUserPassword(userId: string, password: string): Promise<void> {
    const response = await apiFetch(`/users/${encodeURIComponent(userId)}/password`, {
      method: 'PUT',
      body: JSON.stringify({ password }),
    })
    if (!response.ok) throw await parseError(response)
  },

  async updateUserStatus(userId: string, status: 'active' | 'disabled'): Promise<void> {
    const response = await apiFetch(`/users/${encodeURIComponent(userId)}/status`, {
      method: 'PUT',
      body: JSON.stringify({ status }),
    })
    if (!response.ok) throw await parseError(response)
  },

  async updateUserFeatures(userId: string, features: Partial<UserFeatures>): Promise<UserFeatures> {
    const response = await apiFetch(`/users/${encodeURIComponent(userId)}/features`, {
      method: 'PUT',
      body: JSON.stringify(features),
    })
    if (!response.ok) throw await parseError(response)
    const body = await response.json() as { features: UserFeatures }
    return body.features
  },

  async deleteUser(userId: string): Promise<void> {
    const response = await apiFetch(`/users/${encodeURIComponent(userId)}`, {
      method: 'DELETE',
    })
    if (!response.ok) throw await parseError(response)
  },
}
