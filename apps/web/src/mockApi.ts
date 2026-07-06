import { documents, memoryCandidates, messages, sessions, spaces, users } from './mockData'

const wait = (ms = 180) => new Promise((resolve) => setTimeout(resolve, ms))

export const mockApi = {
  async listSessions() {
    await wait()
    return sessions
  },
  async listSpaces() {
    await wait()
    return spaces
  },
  async listDocuments() {
    await wait()
    return documents
  },
  async listMessages(sessionId: string) {
    await wait(120)
    return messages[sessionId] ?? []
  },
  async listMemoryCandidates() {
    await wait()
    return memoryCandidates
  },
  async listUsers() {
    await wait()
    return users
  },
  async getFeatures() {
    await wait()
    return {
      host_terminal: false,
      sandbox: 'docker',
      provider: 'deepseek',
      model: 'deepseek-v4-pro',
    }
  },
}
