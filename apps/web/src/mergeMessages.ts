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
  if (run.userMessage && !messages.some((message) => message.id === run.userMessage?.id)) {
    messages.push(run.userMessage)
  }

  const assistantPersisted = messages.some((message) => (
    message.id === run.assistantMessage.id
    || (message.role === 'assistant' && message.modelRunId === run.requestId)
  ))
  if (!assistantPersisted) messages.push(run.assistantMessage)
  return messages
}
