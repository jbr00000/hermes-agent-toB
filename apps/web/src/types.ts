export type TabType = 'agent' | 'chat' | 'knowledgeBase' | 'document' | 'memory' | 'users' | 'security' | 'audit'

export type WorkspaceMode = 'agent' | 'chat'

export interface WorkTab {
  id: string
  ownerId: string
  type: TabType
  title: string
  refId: string
  order: number
  pinned?: boolean
  updatedAt: number
}

export type PermissionMode = 'read' | 'controlled' | 'full'

export type AgentTaskStatus =
  | 'draft'
  | 'queued'
  | 'planning'
  | 'awaiting_approval'
  | 'ready'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled'

export interface TaskPermission {
  id: string | null
  mode: PermissionMode
  createdAt: number | null
  expiresAt: number | null
}

export interface TaskPlan {
  id: string
  taskId: string
  version: number
  content: string
  status: 'pending' | 'approved' | 'superseded'
  createdAt: number
  approvedAt: number | null
}

export interface TaskRun {
  id: string
  taskId: string
  phase: 'plan' | 'execute'
  attempt: number
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
  error: string | null
  startedAt: number
  completedAt: number | null
}

export interface ToolEvent {
  id: number
  taskId: string
  runId: string
  sequence: number
  eventType: 'tool.started' | 'tool.progress' | 'tool.completed' | string
  toolName: string | null
  riskLevel: 'read' | 'controlled_write' | 'high_risk' | 'unknown'
  status: string
  payload: Record<string, unknown>
  createdAt: number
}

export interface AgentTaskDetail {
  id: string
  sessionId: string
  sourceSessionId: string | null
  title: string
  status: AgentTaskStatus
  risk: 'low' | 'medium' | 'high' | 'unknown'
  currentRunId: string | null
  createdAt: number
  updatedAt: number
  completedAt: number | null
  session: {
    id: string
    title: string
    status: string
  }
  messages: ChatMessage[]
  activeRun: ActiveModelRun | null
  plan: TaskPlan | null
  permission: TaskPermission
  runs: TaskRun[]
  events: ToolEvent[]
  artifacts: Array<{
    id: string
    name: string
    path: string
    mediaType: string | null
    sizeBytes: number | null
    status: string
  }>
}

export interface AuthUser {
  id: string
  username: string
  role: 'admin' | 'user'
}

export interface SessionSummary {
  id: string
  title: string
  space: string
  status: AgentTaskStatus
  updatedAt: string
  risk: 'low' | 'medium' | 'high' | 'unknown'
}

export interface ConversationSummary {
  id: string
  title: string
  space: string
  updatedAt: string
  period: 'today' | 'yesterday' | 'earlier'
  pinned?: boolean
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  createdAt: string
  status?: 'streaming' | 'completed' | 'cancelled' | 'failed'
  modelRunId?: string
  durationMs?: number
  thinkingStartedAt?: number
}

export interface ActiveModelRun {
  id: string
  status: 'queued' | 'running'
  phase?: 'plan' | 'execute' | null
  startedAt: number
  elapsedMs: number
  observedAt: number
  partialContent: string
  sequence: number
  snapshotUpdatedAt: number | null
}

export interface ConversationDetail {
  messages: ChatMessage[]
  status: string
  activeRun: ActiveModelRun | null
}

export interface AttachedFile {
  id: string
  name: string
  size: number
  status: 'ready' | 'parsing'
}

export interface KnowledgeSpace {
  id: string
  name: string
  role: 'member' | 'contributor' | 'kb_admin' | 'space_admin'
  libraries: number
  documents: number
}

export type KnowledgeDocumentStatus = 'pending' | 'parsing' | 'syncing' | 'ready' | 'failed'

/** 对齐 server/routes/knowledge.py 的 document 序列化（企业统一知识库） */
export interface KnowledgeDocument {
  id: string
  title: string
  fileName: string
  fileExt: string
  sizeBytes: number
  status: KnowledgeDocumentStatus
  error: string | null
  parser: 'mineru' | 'local' | null
  chunkCount: number
  retryCount: number
  uploaderId: string
  createdAt: number
  updatedAt: number
  finishedAt: number | null
}

export interface KnowledgeChunk {
  id: string
  docId: string
  docName: string
  chunkTitle: string
  content: string
  docPos: number
  tokenNum: number
  isUse: boolean
}

export interface KnowledgeStats {
  documents: number
  chunks: number
}

export interface MemoryCandidate {
  id: string
  content: string
  source: string
  status: 'pending' | 'approved'
}

export interface UserRow {
  id: string
  username: string
  role: 'admin' | 'user'
  spaces: string[]
}

export interface AuditEvent {
  id: number
  time: string
  eventType: string
  username: string
  subject: string
  status: string
}
