export type TabType = 'agent' | 'knowledgeBase' | 'document' | 'memory' | 'users' | 'security' | 'audit'

export interface WorkTab {
  id: string
  type: TabType
  title: string
  refId: string
  order: number
  pinned?: boolean
  updatedAt: number
}

export type PermissionMode = 'read' | 'controlled' | 'full'

export interface SessionSummary {
  id: string
  title: string
  space: string
  status: 'idle' | 'running' | 'plan_pending' | 'approved' | 'completed'
  updatedAt: string
  risk: 'low' | 'medium' | 'high'
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  createdAt: string
}

export interface KnowledgeSpace {
  id: string
  name: string
  role: 'member' | 'contributor' | 'kb_admin' | 'space_admin'
  libraries: number
  documents: number
}

export interface KnowledgeDocument {
  id: string
  title: string
  spaceId: string
  library: string
  status: 'ready' | 'parsing' | 'review' | 'failed'
  permission: 'inherited' | 'override'
  owner: string
  updatedAt: string
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

