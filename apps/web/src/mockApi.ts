import { conversations, dataSources, memoryCandidates, messages, sessions, spaces } from './mockData'
import type { DataSource, DataSourceInput, UploadedFile, UploadOwnerType } from './types'

const wait = (ms = 180) => new Promise((resolve) => setTimeout(resolve, ms))

/** 纯前端开发用的内存附件表（key = `${ownerType}:${ownerId}`）；解析一律秒级转 ready。 */
const mockUploads = new Map<string, UploadedFile[]>()
const mockUploadKey = (ownerType: UploadOwnerType, ownerId: string) => `${ownerType}:${ownerId}`

function mockBudget(files: UploadedFile[]) {
  const fileTokens = files.reduce((sum, file) => sum + file.tokenCount, 0)
  const budgetTokens = 116_000
  return {
    maxInputTokens: 128_000,
    budgetTokens,
    fileTokens,
    overBudget: fileTokens > budgetTokens,
  }
}

export const mockApi = {
  async listSessions() {
    await wait()
    return sessions
  },
  async listConversations() {
    await wait()
    return conversations
  },
  async listSpaces() {
    await wait()
    return spaces
  },
  async listMessages(sessionId: string) {
    await wait(120)
    return messages[sessionId] ?? []
  },
  async listMemoryCandidates() {
    await wait()
    return memoryCandidates
  },
  async getFeatures() {
    await wait()
    return {
      host_terminal: false,
      sandbox: 'docker',
      provider: 'deepseek',
      model: 'deepseek-v4-pro',
      dataPermissions: { enabled: false, allowedTables: null },
    }
  },
  async listUploads(ownerType: UploadOwnerType, ownerId: string) {
    await wait(120)
    const files = mockUploads.get(mockUploadKey(ownerType, ownerId)) ?? []
    return { files, budget: mockBudget(files) }
  },
  async uploadFiles(ownerType: UploadOwnerType, ownerId: string, picked: File[]) {
    await wait()
    const key = mockUploadKey(ownerType, ownerId)
    const current = mockUploads.get(key) ?? []
    const added: UploadedFile[] = picked.map((file, index) => ({
      id: `mock-upload-${Date.now()}-${index}`,
      ownerType,
      ownerId,
      fileName: file.name,
      fileExt: file.name.slice(file.name.lastIndexOf('.')).toLowerCase(),
      sizeBytes: file.size,
      parseStatus: 'ready',
      parseError: null,
      tokenCount: Math.max(1, Math.round(file.size / 3)),
      createdAt: Date.now() / 1000,
    }))
    mockUploads.set(key, [...current, ...added].slice(0, 5))
    return added
  },
  async deleteUpload(fileId: string) {
    await wait(120)
    for (const [key, files] of mockUploads) {
      mockUploads.set(key, files.filter((file) => file.id !== fileId))
    }
  },

  // ---- 数据源连接（数据库管理；后端未接，先走内存 mock，适配时平移到 api.ts）----

  async listDataSources(): Promise<DataSource[]> {
    await wait()
    return dataSources
  },
  async createDataSource(input: DataSourceInput): Promise<DataSource> {
    await wait()
    const now = Date.now() / 1000
    const created: DataSource = {
      id: `ds-${Date.now()}`,
      name: input.name,
      dbType: input.dbType,
      host: input.host,
      port: input.port,
      database: input.database,
      username: input.username,
      status: 'untested',
      lastTestedAt: null,
      createdAt: now,
      updatedAt: now,
    }
    dataSources.unshift(created)
    return created
  },
  async updateDataSource(id: string, input: DataSourceInput): Promise<DataSource> {
    await wait()
    const index = dataSources.findIndex((ds) => ds.id === id)
    if (index < 0) throw new Error('数据源不存在')
    const prev = dataSources[index]
    const updated: DataSource = {
      ...prev,
      name: input.name,
      dbType: input.dbType,
      host: input.host,
      port: input.port,
      database: input.database,
      username: input.username,
      // 连接参数变了 → 旧测试结果失效；mock 语义：password 留空 = 不修改
      status: 'untested',
      lastTestedAt: null,
      updatedAt: Date.now() / 1000,
    }
    dataSources[index] = updated
    return updated
  },
  async deleteDataSource(id: string) {
    await wait()
    const index = dataSources.findIndex((ds) => ds.id === id)
    if (index >= 0) dataSources.splice(index, 1)
  },
  /** 测试连接：mock 总是成功（真实后端探活由用户的算法端适配） */
  async testDataSource(id: string): Promise<DataSource> {
    await wait(600)
    const ds = dataSources.find((item) => item.id === id)
    if (!ds) throw new Error('数据源不存在')
    ds.status = 'connected'
    ds.lastTestedAt = Date.now() / 1000
    ds.updatedAt = ds.lastTestedAt
    return ds
  },
}
