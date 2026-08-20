import { conversations, memoryCandidates, messages, sessions, spaces } from './mockData'
import type { UploadedFile, UploadOwnerType } from './types'

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
}
