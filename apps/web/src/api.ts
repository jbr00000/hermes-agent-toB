import type { AuthUser, ChatMessage, ConversationSummary } from './types'

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
  created_at?: number
  timestamp?: number
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
  }
}

export interface ChatStreamEvent {
  session_id?: string
  request_id?: string
  title?: string
  content?: string
  status?: string
  message?: BackendMessage
  code?: string
  [key: string]: unknown
}

export interface ChatStreamHandlers {
  onSession?: (event: ChatStreamEvent) => void
  onDelta?: (event: ChatStreamEvent) => void
  onFinal?: (event: ChatStreamEvent) => void
  onError?: (event: ChatStreamEvent) => void
}

function dispatchSseBlock(block: string, handlers: ChatStreamHandlers): void {
  let event = 'message'
  const data: string[] = []
  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
  }
  if (data.length === 0) return
  const payload = JSON.parse(data.join('\n')) as ChatStreamEvent
  if (event === 'session') handlers.onSession?.(payload)
  if (event === 'delta') handlers.onDelta?.(payload)
  if (event === 'final') handlers.onFinal?.(payload)
  if (event === 'error') handlers.onError?.(payload)
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

  async listMessages(sessionId: string): Promise<ChatMessage[]> {
    if (sessionId.startsWith('draft-')) return []
    const response = await apiFetch(`/sessions/${encodeURIComponent(sessionId)}`)
    if (!response.ok) throw await parseError(response)
    const body = await response.json() as { messages: BackendMessage[] }
    return body.messages.map(toMessage)
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
