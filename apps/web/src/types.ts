export type TabType = 'agent' | 'chat' | 'knowledgeBase' | 'knowledgeBaseDetail' | 'document' | 'memory' | 'users' | 'security' | 'audit'

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

/** controlled 权限档：一条待用户批准的 terminal/process 命令。 */
export interface ToolApproval {
  id: string
  taskId: string
  runRequestId: string
  toolName: string
  commandPreview: string
  status: 'pending' | 'approved' | 'denied' | 'expired'
  createdAt: number
  decidedAt: number | null
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

/** 任务交付文件（沙箱工作区产物，GET /tasks/{id}/artifacts） */
export interface TaskArtifact {
  name: string
  path: string
  sizeBytes: number
  modifiedAt: number
  mediaType: string | null
}

export type UserRole = 'superadmin' | 'admin' | 'user'

export interface UserFeatures {
  agent: boolean
  chat: boolean
  knowledge: boolean
  memory: boolean
}

export interface AuthUser {
  id: string
  username: string
  role: UserRole
  features: UserFeatures
  /** 建号/管理员重置后为 true：必须先改密才能进入工作台。 */
  mustChangePassword?: boolean
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

/** 知识库问答引用卡片 —— 对应 server citations SSE 事件 / 消息 metadata.citations 的一项 */
export interface KnowledgeCitation {
  num: number | null
  chunkId: string
  docId: string
  docName: string
  chunkTitle: string
  snippet: string
  score: number | null
}

/** 知识库问答检索模式：fast=单次融合检索；precise=轻量模型改写+编排（更慢更准） */
export type KnowledgeSearchMode = 'fast' | 'precise'

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  createdAt: string
  status?: 'streaming' | 'completed' | 'cancelled' | 'failed'
  modelRunId?: string
  durationMs?: number
  thinkingStartedAt?: number
  /** 知识库问答的引用来源（assistant 消息；流式中由 citations 事件填充，刷新后由 metadata 回填） */
  citations?: KnowledgeCitation[]
  /** 用户原文：content 可能拼了附件全文（注入模型用），气泡展示优先用这个 */
  displayContent?: string
  /** 本轮注入的附件明细（user 消息；由 metadata.attachments 回填） */
  attachments?: MessageAttachment[]
}

/** 一轮消息里附件全文的注入结果（对齐 server/uploads.py 的 usage 明细） */
export interface MessageAttachment {
  id: string
  fileName: string
  tokenCount: number
  includedTokens: number
  /** full=全文注入 · truncated=超预算截断 · skipped=预算耗尽/产物缺失未注入 */
  status: 'full' | 'truncated' | 'skipped'
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

/** 临时上传附件（chat 会话 / agent 任务问答上下文；对齐 server/storage/models.py UploadedFile） */
export type UploadOwnerType = 'session' | 'task'
export type UploadParseStatus = 'parsing' | 'ready' | 'failed'
export interface UploadedFile {
  id: string
  ownerType: UploadOwnerType
  ownerId: string
  fileName: string
  fileExt: string
  sizeBytes: number
  parseStatus: UploadParseStatus
  parseError: string | null
  /** 解析全文的 token 数（ready 后才有意义），预算条与 chip 用 */
  tokenCount: number
  createdAt: number
}

/** GET /uploads 附带的 token 预算用量（over_budget=true 时前端显示黄色警告条，不阻断发送） */
export interface UploadBudget {
  maxInputTokens: number
  budgetTokens: number
  fileTokens: number
  overBudget: boolean
}

export interface KnowledgeSpace {
  id: string
  name: string
  role: 'member' | 'contributor' | 'kb_admin' | 'space_admin'
  libraries: number
  documents: number
}

/** 知识库实体 —— 三步流程的步骤①：先建库，再往库里传文档，最后选文档解析 */
export interface KnowledgeBase {
  id: string
  name: string
  description: string | null
  docCount: number
  chunkCount: number
  createdAt: number
  updatedAt: number
}

/** uploaded = 已上传待解析（上传与解析已解耦，需显式触发解析才会进入构建流水线） */
export type KnowledgeDocumentStatus = 'uploaded' | 'pending' | 'parsing' | 'syncing' | 'ready' | 'failed'

/** 对齐 server/routes/knowledge.py 的 document 序列化（企业知识库） */
export interface KnowledgeDocument {
  id: string
  kbId: string
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
  kbId: string
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

export interface AdminUserRow {
  id: string
  username: string
  role: UserRole
  status: 'active' | 'disabled'
  features: UserFeatures
  createdAt: number
}

export interface AuditEvent {
  id: number
  time: string
  eventType: string
  username: string
  subject: string
  status: string
}
