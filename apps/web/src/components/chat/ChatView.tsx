import * as React from 'react'
import { useAtom } from 'jotai'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Archive,
  Check,
  CircleStop,
  LockKeyhole,
  Mic,
  MoreHorizontal,
  Paperclip,
  Pencil,
  Pin,
  Play,
  Send,
  Trash2,
  X,
} from 'lucide-react'
import { api, ApiError } from '../../api'
import {
  isChatRunActive,
  mergeChatMessages,
  useChatRun,
  useChatRunManager,
} from '../../chatRunManager'
import {
  chatAttachedFilesAtom,
  knowledgeQaEnabledAtom,
  knowledgeQaKbIdAtom,
  knowledgeQaSearchModeAtom,
} from '../../state'
import type { AttachedFile, ConversationSummary, TabType } from '../../types'
import { ConfirmDialog } from '../ConfirmDialog'
import { Badge, cn, IconButton, MessageSkeleton } from '../ui'
import { KnowledgeQaPicker } from './KnowledgeQaPicker'
import { MessageBubble } from './MessageBubble'

export function ChatView({
  sessionId,
  title,
  knowledgeEnabled,
  onOpenTab,
  onPromote,
  onConversationUpdated,
  onConversationArchived,
}: {
  sessionId: string
  title: string
  /** 用户 knowledge feature：控制知识库问答入口与引用卡片的可点击性 */
  knowledgeEnabled: boolean
  onOpenTab: (type: TabType, refId: string, title: string) => void
  onPromote: () => void
  onConversationUpdated: (sessionId: string, title: string) => void
  onConversationArchived: (sessionId: string) => void
}) {
  const [filesByTab, setFilesByTab] = useAtom(chatAttachedFilesAtom)
  const [knowledgeQaEnabled] = useAtom(knowledgeQaEnabledAtom)
  const [knowledgeQaKbId] = useAtom(knowledgeQaKbIdAtom)
  const [knowledgeQaSearchMode] = useAtom(knowledgeQaSearchModeAtom)
  const queryClient = useQueryClient()
  const chatRunManager = useChatRunManager()
  const run = useChatRun(sessionId)
  const query = useQuery({
    queryKey: ['conversation', sessionId],
    queryFn: () => api.getConversation(sessionId),
    refetchInterval: (result) => result.state.data?.activeRun ? 1000 : false,
  })
  // 选库下拉的库清单（与知识库管理页共用 queryKey 缓存）；404 = 部署未启用知识库
  const knowledgeBasesQuery = useQuery({
    queryKey: ['knowledgeBases'],
    queryFn: () => api.listKnowledgeBases(),
    retry: false,
    enabled: knowledgeEnabled,
  })
  const knowledgeDeploymentMissing = knowledgeBasesQuery.error instanceof ApiError
    && knowledgeBasesQuery.error.status === 404
  const knowledgeQaAvailable = knowledgeEnabled && !knowledgeDeploymentMissing
  const knowledgeQaActive = knowledgeQaAvailable && knowledgeQaEnabled
  const [draft, setDraft] = React.useState('')
  const [actionError, setActionError] = React.useState<string | null>(null)
  const [menuOpen, setMenuOpen] = React.useState(false)
  const [renaming, setRenaming] = React.useState(false)
  const [renameDraft, setRenameDraft] = React.useState(title)
  const [actionPending, setActionPending] = React.useState(false)
  const [deleteConfirmOpen, setDeleteConfirmOpen] = React.useState(false)
  const fileInputRef = React.useRef<HTMLInputElement | null>(null)
  const messageScrollRef = React.useRef<HTMLDivElement | null>(null)
  const didRestoreScrollRef = React.useRef(false)
  const shouldFollowOutputRef = React.useRef(true)
  const wasRunningRef = React.useRef(false)

  // 附件按本会话隔离（atom 是 { [sessionId]: 文件列表 }），切会话互不串扰
  const files = React.useMemo(() => filesByTab[sessionId] ?? [], [filesByTab, sessionId])
  const setFiles = React.useCallback((updater: (current: AttachedFile[]) => AttachedFile[]) => {
    setFilesByTab((current) => ({ ...current, [sessionId]: updater(current[sessionId] ?? []) }))
  }, [sessionId, setFilesByTab])

  React.useEffect(() => {
    if (query.data) chatRunManager.reconcileServerState(sessionId, query.data)
  }, [chatRunManager, query.data, sessionId])

  React.useEffect(() => {
    if (run?.title) onConversationUpdated(sessionId, run.title)
  }, [onConversationUpdated, run?.title, sessionId])

  React.useEffect(() => {
    if (!renaming) setRenameDraft(title)
  }, [renaming, title])

  const currentConversation = queryClient
    .getQueryData<ConversationSummary[]>(['conversations'])
    ?.find((conversation) => conversation.id === sessionId)
  const isRunning = isChatRunActive(run) || Boolean(query.data?.activeRun)
  const displayMessages = React.useMemo(
    () => mergeChatMessages(query.data?.messages ?? [], run),
    [query.data?.messages, run],
  )
  const streamError = actionError ?? run?.error ?? null

  React.useLayoutEffect(() => {
    didRestoreScrollRef.current = false
    shouldFollowOutputRef.current = true
    wasRunningRef.current = false
    return () => {
      const container = messageScrollRef.current
      if (container) chatRunManager.setScrollPosition(sessionId, container.scrollTop)
    }
  }, [chatRunManager, sessionId])

  React.useLayoutEffect(() => {
    if (query.isLoading) return
    const container = messageScrollRef.current
    if (!container) return

    const startedRunning = isRunning && !wasRunningRef.current
    if (!didRestoreScrollRef.current) {
      const savedPosition = chatRunManager.getScrollPosition(sessionId)
      if (isRunning || savedPosition === null) {
        container.scrollTop = container.scrollHeight
      } else {
        container.scrollTop = savedPosition
      }
      didRestoreScrollRef.current = true
      const bottomGap = container.scrollHeight - container.clientHeight - container.scrollTop
      shouldFollowOutputRef.current = bottomGap <= 96
    } else if (isRunning && (startedRunning || shouldFollowOutputRef.current)) {
      container.scrollTop = container.scrollHeight
      shouldFollowOutputRef.current = true
    }
    wasRunningRef.current = isRunning
    chatRunManager.setScrollPosition(sessionId, container.scrollTop)
  }, [chatRunManager, displayMessages, isRunning, query.isLoading, sessionId])

  const trackMessageScroll = React.useCallback((event: React.UIEvent<HTMLDivElement>) => {
    const container = event.currentTarget
    const bottomGap = container.scrollHeight - container.clientHeight - container.scrollTop
    shouldFollowOutputRef.current = bottomGap <= 96
    chatRunManager.setScrollPosition(sessionId, container.scrollTop)
  }, [chatRunManager, sessionId])

  const updateConversation = React.useCallback(async (
    changes: { title?: string; pinned?: boolean; archived?: boolean },
  ) => {
    setActionPending(true)
    setActionError(null)
    try {
      const updated = await api.updateConversation(sessionId, changes)
      if (changes.archived) {
        queryClient.setQueryData<ConversationSummary[]>(['conversations'], (current = []) => (
          current.filter((conversation) => conversation.id !== sessionId)
        ))
        onConversationArchived(sessionId)
      } else {
        queryClient.setQueryData<ConversationSummary[]>(['conversations'], (current = []) => (
          current.map((conversation) => conversation.id === sessionId ? updated : conversation)
        ))
        if (changes.title) onConversationUpdated(sessionId, updated.title)
        await queryClient.invalidateQueries({ queryKey: ['conversations'] })
      }
      setMenuOpen(false)
      return updated
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '更新问答失败')
      return null
    } finally {
      setActionPending(false)
    }
  }, [onConversationArchived, onConversationUpdated, queryClient, sessionId])

  const commitRename = React.useCallback(async () => {
    const nextTitle = renameDraft.trim()
    if (!nextTitle || nextTitle === title) {
      setRenaming(false)
      setRenameDraft(title)
      return
    }
    const updated = await updateConversation({ title: nextTitle })
    if (updated) setRenaming(false)
  }, [renameDraft, title, updateConversation])

  const deleteConversation = React.useCallback(async () => {
    setActionPending(true)
    setActionError(null)
    try {
      await queryClient.cancelQueries({ queryKey: ['conversation', sessionId] })
      await api.deleteConversation(sessionId)
      chatRunManager.clearScrollPosition(sessionId)
      queryClient.setQueryData<ConversationSummary[]>(['conversations'], (current = []) => (
        current.filter((conversation) => conversation.id !== sessionId)
      ))
      setDeleteConfirmOpen(false)
      onConversationArchived(sessionId)
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '删除问答失败')
      setDeleteConfirmOpen(false)
    } finally {
      setActionPending(false)
    }
  }, [chatRunManager, onConversationArchived, queryClient, sessionId])

  const sendMessage = React.useCallback(() => {
    const text = draft.trim()
    if (!text || isRunning) return
    try {
      chatRunManager.start(sessionId, text, {
        knowledgeQa: knowledgeQaActive
          ? { kbId: knowledgeQaKbId, searchMode: knowledgeQaSearchMode }
          : null,
      })
      setDraft('')
      setActionError(null)
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '无法启动 Chat 服务')
    }
  }, [chatRunManager, draft, isRunning, knowledgeQaActive, knowledgeQaKbId, knowledgeQaSearchMode, sessionId])

  const stopMessage = React.useCallback(() => {
    const requestId = run?.requestId ?? query.data?.activeRun?.id
    if (!requestId) return
    const cancellation = run
      ? chatRunManager.cancel(sessionId)
      : api.cancelChat(requestId)
    void cancellation.catch((error) => {
      setActionError(error instanceof Error ? error.message : '停止生成失败')
    })
  }, [chatRunManager, query.data?.activeRun?.id, run, sessionId])

  // 附件仅本地暂存（上传通道未接入），选中即入列，没有"解析中"的假状态
  const addFiles = (selected: FileList | null) => {
    if (!selected?.length) return
    const next = Array.from(selected).map((file) => ({
      id: `chat-file-${Date.now()}-${file.name}`,
      name: file.name,
      size: file.size,
    }))
    setFiles((current) => [...current, ...next])
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex min-h-14 items-center justify-between gap-2 border-b border-line px-3 py-2 sm:px-5">
        <div className="min-w-0 flex-1">
          {renaming ? (
            <div className="flex max-w-lg items-center gap-1.5">
              <input
                autoFocus
                aria-label="问答标题"
                className="h-8 min-w-0 flex-1 rounded-md border border-line px-2 text-sm font-medium outline-none focus:border-zinc-500"
                value={renameDraft}
                maxLength={100}
                onChange={(event) => setRenameDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') void commitRename()
                  if (event.key === 'Escape') setRenaming(false)
                }}
              />
              <IconButton label="保存标题" icon={Check} onClick={() => void commitRename()} />
              <IconButton label="取消重命名" icon={X} onClick={() => setRenaming(false)} />
            </div>
          ) : (
            <div className="truncate text-sm font-semibold">{title}</div>
          )}
          <div className="mt-0.5 flex min-w-0 items-center gap-2 text-xs text-zinc-500">
            <span className="truncate">会话 {sessionId}</span>
            <span className="h-1 w-1 rounded-full bg-zinc-300" />
            <span className="shrink-0">{isRunning ? '回答中' : '智能问答'}</span>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1.5 sm:gap-2">
          <Badge className="bg-emerald-50 text-success">
            <LockKeyhole size={12} className="sm:mr-1.5" />
            <span className="hidden sm:inline">只读</span>
          </Badge>
          <button title="转为任务" className="flex h-8 items-center gap-1.5 rounded-md border border-line px-2.5 text-xs hover:bg-field sm:px-3" onClick={onPromote}>
            <Play size={14} />
            <span className="hidden sm:inline">转为任务</span>
          </button>
          <div className="relative">
            <IconButton label="更多" icon={MoreHorizontal} onClick={() => setMenuOpen((open) => !open)} />
            {menuOpen && (
              <div className="absolute right-0 top-10 z-20 w-40 rounded-md border border-line bg-panel p-1 shadow-card">
                <button
                  className="flex h-9 w-full items-center gap-2 rounded px-2.5 text-left text-sm hover:bg-field"
                  onClick={() => {
                    setRenameDraft(title)
                    setRenaming(true)
                    setMenuOpen(false)
                  }}
                >
                  <Pencil size={14} />
                  重命名
                </button>
                <button
                  disabled={actionPending}
                  className="flex h-9 w-full items-center gap-2 rounded px-2.5 text-left text-sm hover:bg-field disabled:text-zinc-400"
                  onClick={() => void updateConversation({ pinned: !currentConversation?.pinned })}
                >
                  <Pin size={14} />
                  {currentConversation?.pinned ? '取消置顶' : '置顶问答'}
                </button>
                <button
                  disabled={actionPending || isRunning}
                  className="flex h-9 w-full items-center gap-2 rounded px-2.5 text-left text-sm text-danger hover:bg-red-50 disabled:text-zinc-400"
                  onClick={() => void updateConversation({ archived: true })}
                >
                  <Archive size={14} />
                  归档问答
                </button>
                <button
                  disabled={actionPending || isRunning}
                  className="flex h-9 w-full items-center gap-2 rounded px-2.5 text-left text-sm text-danger hover:bg-red-50 disabled:text-zinc-400"
                  onClick={() => {
                    setMenuOpen(false)
                    setDeleteConfirmOpen(true)
                  }}
                >
                  <Trash2 size={14} />
                  永久删除
                </button>
              </div>
            )}
          </div>
        </div>
      </div>

      <div
        ref={messageScrollRef}
        className="thin-scrollbar min-h-0 flex-1 overflow-y-auto px-3 py-5 sm:px-8 sm:py-6"
        onScroll={trackMessageScroll}
      >
        <div className="mx-auto max-w-4xl space-y-5">
          {query.isLoading ? (
            <MessageSkeleton />
          ) : displayMessages.length === 0 && !isRunning ? (
            <div className="border-y border-line py-16 text-center">
              <div className="text-sm font-medium">新问答</div>
              <div className="mt-1 text-xs text-zinc-500">输入问题，我陪你一起找答案。</div>
            </div>
          ) : (
            displayMessages.map((message) => (
              <MessageBubble
                key={message.id}
                message={message}
                onOpenTab={knowledgeEnabled ? onOpenTab : undefined}
              />
            ))
          )}
          {streamError && (
            <div className="border-y border-red-100 bg-red-50 px-3 py-2 text-sm text-danger" role="alert">
              {streamError}
            </div>
          )}
        </div>
      </div>

      <div className="border-t border-line bg-[#fbfbfc] px-3 py-3 sm:px-6 sm:py-4">
        <div className="mx-auto max-w-4xl">
          <div className="rounded-md border border-line bg-panel shadow-sm">
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault()
                void sendMessage()
              }
            }}
            className="block min-h-[82px] w-full resize-none bg-transparent px-4 py-3 text-sm outline-none"
            placeholder={knowledgeQaActive ? '知识库问答：回答将严格基于知识库内容并标注来源' : '输入问题，Chat 模式只会读取授权数据'}
          />
          <div className="flex items-center justify-between gap-2 border-t border-line px-2 py-2 sm:px-3">
            <div className="flex min-w-0 items-center gap-1">
              <input ref={fileInputRef} type="file" multiple className="hidden" onChange={(event) => addFiles(event.target.files)} />
              <IconButton label="添加只读附件" icon={Paperclip} onClick={() => fileInputRef.current?.click()} />
              <IconButton label="语音输入" icon={Mic} />
              {knowledgeQaAvailable && (
                <KnowledgeQaPicker bases={knowledgeBasesQuery.data ?? []} />
              )}
              {files.length > 0 && (
                <span className="ml-1 hidden truncate text-xs text-zinc-500 sm:inline">
                  {files.length} 个附件（本地暂存，暂不参与问答）
                </span>
              )}
            </div>
            <button
              className={cn(
                'flex h-8 items-center gap-2 rounded-md px-3 text-sm font-medium transition active:scale-[0.98]',
                isRunning || !draft.trim() ? 'bg-zinc-200 text-zinc-500' : 'bg-[#3d735a] text-white',
              )}
              disabled={!isRunning && !draft.trim()}
              onClick={() => isRunning ? stopMessage() : void sendMessage()}
            >
              {isRunning ? <CircleStop size={15} /> : <Send size={15} />}
              {isRunning ? '停止' : '发送'}
            </button>
          </div>
          </div>
        </div>
      </div>
      {deleteConfirmOpen && (
        <ConfirmDialog
          title="永久删除问答"
          description="对话消息和模型运行记录将被删除，企业审计记录仍会保留。"
          pending={actionPending}
          onConfirm={() => void deleteConversation()}
          onCancel={() => setDeleteConfirmOpen(false)}
        />
      )}
    </div>
  )
}
