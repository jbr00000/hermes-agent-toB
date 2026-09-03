export type TabType = 'agent' | 'chat' | 'knowledgeBase' | 'knowledgeBaseDetail' | 'document' | 'database' | 'datasetMeta' | 'queryData' | 'memory' | 'users' | 'security' | 'audit'

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

/** Agent 任务的运行级知识库选择：enabled=false 不带知识库；kbId=null 检索全部知识库 */
export interface AgentKnowledgeScope {
  enabled: boolean
  kbId: string | null
}

/** 数据源连接（数据库管理 · 图1卡片墙）。密码为 write-only：API 永不回传。
 *  问数要求业务库为 MySQL/PostgreSQL（后端只收这两类）。 */
export type DbType = 'mysql' | 'postgresql'

export interface DataSource {
  id: string
  /** 数据库中文名 */
  name: string
  dbType: DbType
  host: string
  port: number
  database: string
  username: string
  /** 是否已设置密码（密码本身为 write-only，API 永不回传） */
  hasPassword: boolean
  /** 最近一次测试连接的结果；untested = 新建/改密码后未测 */
  status: 'connected' | 'failed' | 'untested'
  lastTestedAt: number | null
  createdAt: number
  updatedAt: number
}

/** 新建/编辑连接的表单载荷；编辑时 password 留空 = 不修改 */
export interface DataSourceInput {
  name: string
  dbType: DbType
  host: string
  port: number
  database: string
  username: string
  password?: string
}

/** 数据集（图3）：挂在某个数据源下的问数（NL2SQL）单元，DataSource : Dataset = 1 : N */
export interface Dataset {
  id: string
  name: string
  /** 业务说明；为空时治理状态显示「缺业务说明」 */
  description: string
  dataSourceId: string
  /** 流程版本占位（决策③：暂无编排引擎，固定「框架默认流程」） */
  flowVersion: string
  enabled: boolean
  /** 问数提示词；为空时治理状态显示「缺提示词」 */
  prompt: string
  /** 元数据统计（图3「DDL/规则」列）：表结构条数 / 规则条数 */
  ddlCount: number
  ruleCount: number
  createdAt: number
  updatedAt: number
}

/** 新建/编辑数据集的表单载荷 */
export interface DatasetInput {
  name: string
  description: string
  dataSourceId: string
  enabled: boolean
  prompt: string
}

/** 元数据配置（图4）六类元数据的种类 key */
export type MetaKind = 'tables' | 'terms' | 'metrics' | 'dimensions' | 'foreignKeys' | 'examples'

/** 元数据来源标记：MANUAL=人工维护 / AI=自动抓取或生成 */
export type MetaProvider = 'MANUAL' | 'AI'

/** 表结构元数据（图4 默认 tab）；enabled=false 的表不下发给问数流程。
 *  ddlContent 为完整建表语句，由算法端原文拼进 SQL 生成 prompt */
export interface TableMeta {
  id: string
  datasetId: string
  tableName: string
  ddlContent: string
  /** 表中文说明 */
  description: string
  enabled: boolean
  provider: MetaProvider
  updatedAt: number
}

/** 术语：业务名词 → 口径定义；synonyms 近义词参与检索召回 */
export interface TermMeta {
  id: string
  datasetId: string
  term: string
  definition: string
  /** 近义词（顿号/逗号分隔） */
  synonyms: string
  remark: string
  provider: MetaProvider
  updatedAt: number
}

/** 指标：可计算的业务度量（expression 为口径/SQL 片段，可含占位符） */
export interface MetricMeta {
  id: string
  datasetId: string
  name: string
  displayName: string
  expression: string
  remark: string
  provider: MetaProvider
  updatedAt: number
}

/** 维度码表：编码 ↔ 中文标准值（name 形如「表名.字段名」，dataKey=库存编码，dataValue=标准中文值） */
export interface DimensionMeta {
  id: string
  datasetId: string
  name: string
  displayName: string
  dataKey: string
  dataValue: string
  remark: string
  provider: MetaProvider
  updatedAt: number
}

/** 外键关系：表间 join 依据 */
export interface ForeignKeyMeta {
  id: string
  datasetId: string
  fromTable: string
  fromColumn: string
  toTable: string
  toColumn: string
  /** 关联说明 */
  relationDesc: string
  provider: MetaProvider
  updatedAt: number
}

/** 范例：问题 → 标准 SQL（few-shot 示例） */
export interface ExampleMeta {
  id: string
  datasetId: string
  question: string
  sql: string
  remark: string
  provider: MetaProvider
  updatedAt: number
}

/** 一个数据集的全量元数据 */
export interface DatasetMetaBundle {
  tables: TableMeta[]
  terms: TermMeta[]
  metrics: MetricMeta[]
  dimensions: DimensionMeta[]
  foreignKeys: ForeignKeyMeta[]
  examples: ExampleMeta[]
}

/** 三端同步历史（图4「三端同步历史」弹窗）：一次同步按五段资产分别记录状态 */
export interface Nl2sqlSyncSegment {
  key: 'ddl' | 'terminology' | 'index' | 'dimension' | 'qaPair'
  label: string
  status: string // pending | running | success | failed | skipped
  message: string | null
}

export interface Nl2sqlSyncRecord {
  id: string
  datasetId: string
  /** ASSET_CHANGE（资产变更自动）| MANUAL_RESYNC（手动重同步）| CLEAR（清空）| IMPORT（Excel 导入） */
  triggerType: string
  overallStatus: string
  overallMessage: string | null
  segments: Nl2sqlSyncSegment[]
  createdAt: number
}

/** Excel 导入预览（POST meta/import/preview）：按元数据类型分组的 create/update/duplicate 统计 + 行级错误 */
export interface MetaImportTypeSummary {
  read: number
  create: number
  update: number
  duplicate: number
  error: number
}

export interface MetaImportPreview {
  previewId: string
  typeSummaries: Partial<Record<MetaKind, MetaImportTypeSummary>>
  errors: Array<{ sheet: string; row: number; message: string }>
  ignoredSheets: string[]
}

/** 问数（NL2SQL）单轮回答：生成的 SQL + 结果集 + 自然语言小结（mock 演示用，真链路见下方 SSE 类型） */
export interface Nl2sqlAnswer {
  sql: string
  columns: string[]
  rows: string[][]
  /** 自然语言小结（mock 下注明占位） */
  summary: string
  durationMs: number
}

/** 问数 SSE：单张格式化结果卡（后端 format_outputs 元素，api.ts 已转 camelCase） */
export interface Nl2sqlFormatOutput {
  type: string
  title: string
  dimensions: string
  metrics: string
  /** 结果集（行字典）；绘图数据是阶段4 的事，dataFigure 由后端预先抽好两列 */
  data: Array<Record<string, unknown>>
  dataFigure: Array<Record<string, unknown>>
  dataAll: number
  chunkFlag: string
  resultDesc: string
  contentDesc: string
  figureType: string
}

/** 问数 SSE：done 事件载荷 */
export interface Nl2sqlAskDone {
  question: string
  sqlContent: string
  explainContent: string
  /** failed = 4 次重试耗尽（此时 error 为用户可读原因） */
  status: 'success' | 'failed'
  error: string | null
  formatOutputs: Nl2sqlFormatOutput[]
  tokenNum: number
}

export type Nl2sqlStep = 'understand' | 'generate' | 'result'

/** 问数 SSE：phase 事件载荷（start 时只有 step/status；done 时带该阶段的展示数据） */
export interface Nl2sqlPhaseEvent {
  step: Nl2sqlStep
  status: 'start' | 'done'
  question?: string
  entities?: { time?: string[]; other?: string[]; metric?: string[] }
  entityExplain?: string
  candidates?: Record<string, Array<Record<string, unknown>>>
  tables?: string[]
  rows?: number
  attempts?: number
  resultDesc?: string
  error?: string
}

/** 桌宠形象：cat 铅笔小猫 / niulai 牛来（均使用完整身体精灵帧，微型图标可回退 SVG） */
export type PetSkin = 'cat' | 'niulai'

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
