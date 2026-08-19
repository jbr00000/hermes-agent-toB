import type { ChatMessage } from './types'

/** chat / agent 两个 run manager 共用的消息归并：把进行中的运行快照叠到持久化消息之后，
 *  已持久化的（按消息 id 或 modelRunId=requestId 判定）不重复追加。 */
export function mergeRunMessages(
  persistedMessages: ChatMessage[],
  run: {
    userMessage: ChatMessage | null
    assistantMessage: ChatMessage
    requestId: string
  } | null,
): ChatMessage[] {
  if (!run) return persistedMessages

  const messages = [...persistedMessages]
  // 用户消息除了按 id 去重，还要按 modelRunId 去重：本地快照的 id 是
  // agent-user-<requestId>，持久化后换成服务端 UUID——若 session 事件在
  // SSE 里丢失（曾由事件 id 撞号导致），快照 id 不会被替换，仅靠 id 去重
  // 会在轮询回源后出现两个一模一样的用户气泡
  const userPersisted = messages.some((message) => (
    message.id === run.userMessage?.id
    || (message.role === 'user' && message.modelRunId === run.requestId)
  ))
  if (run.userMessage && !userPersisted) {
    messages.push(run.userMessage)
  }

  const assistantPersisted = messages.some((message) => (
    message.id === run.assistantMessage.id
    || (message.role === 'assistant' && message.modelRunId === run.requestId)
  ))
  if (!assistantPersisted) messages.push(run.assistantMessage)
  return messages
}
