import * as React from 'react'
import { useAtom } from 'jotai'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { CircleStop, Mic, Send, Trash2 } from 'lucide-react'
import { api, ApiError } from '../../api'
import {
  isAgentRunActive,
  mergeAgentMessages,
  useAgentRun,
  useAgentRunManager,
} from '../../agentRunManager'
import { agentKnowledgeEnabledAtom, agentKnowledgeKbIdAtom } from '../../state'
import type { AgentKnowledgeScope, AgentTaskDetail, PermissionMode, TabType } from '../../types'
import { MessageBubble } from '../chat/MessageBubble'
import { AgentKnowledgePicker } from '../chat/KnowledgeQaPicker'
import { ConfirmDialog } from '../ConfirmDialog'
import { cn, IconButton, MessageSkeleton } from '../ui'
import { AttachmentBudgetBar, AttachmentChips, AttachmentPickerButton } from '../uploads/AttachmentChips'
import { useAttachments } from '../uploads/useAttachments'
import { PermissionSegment } from './PermissionSegment'
import { ArtifactsCard } from './ArtifactsCard'
import { TaskPlanPanel } from './TaskPlanPanel'
import { ToolApprovalPanel } from './ToolApprovalPanel'
import { ToolEventTimeline } from './ToolEventTimeline'

export function AgentView({ taskId, title, knowledgeEnabled, onDeleted, onOpenTab }: { taskId: string; title: string; knowledgeEnabled: boolean; onDeleted: (taskId: string) => void; onOpenTab: (type: TabType, refId: string, title: string) => void }) {
  const queryClient = useQueryClient()
  const agentRunManager = useAgentRunManager()
  const run = useAgentRun(taskId)
  const query = useQuery({
    queryKey: ['task', taskId],
    queryFn: () => api.getTask(taskId),
    refetchInterval: (result) => result.state.data?.activeRun ? 1000 : false,
  })
  // 知识库选择器的库清单（与 Chat/知识库管理页共用 queryKey 缓存）；404 = 部署未启用知识库
  const knowledgeBasesQuery = useQuery({
    queryKey: ['knowledgeBases'],
    queryFn: () => api.listKnowledgeBases(),
    retry: false,
    enabled: knowledgeEnabled,
  })
  const knowledgeAvailable = knowledgeEnabled
    && !(knowledgeBasesQuery.error instanceof ApiError && knowledgeBasesQuery.error.status === 404)
  const [knowledgeEnabledPref] = useAtom(agentKnowledgeEnabledAtom)
  const [knowledgeKbId] = useAtom(agentKnowledgeKbIdAtom)
  // 运行级知识库选择随每次运行发给后端；选择器不可见（无 feature/部署未启用）时不传，
  // 服务端保持默认行为
  const knowledgeScope: AgentKnowledgeScope | undefined = knowledgeAvailable
    ? { enabled: knowledgeEnabledPref, kbId: knowledgeEnabledPref ? knowledgeKbId : null }
    : undefined
  const [draft, setDraft] = React.useState('')
  const [actionPending, setActionPending] = React.useState(false)
  const [actionError, setActionError] = React.useState<string | null>(null)
  const [deleteConfirmOpen, setDeleteConfirmOpen] = React.useState(false)
  const scrollRef = React.useRef<HTMLDivElement | null>(null)
  const restoredScrollRef = React.useRef(false)
  // 输出流期间是否贴底跟随：用户上滚超过阈值即退出跟随，回到底部自动恢复
  const shouldFollowOutputRef = React.useRef(true)
  const wasActiveRef = React.useRef(false)

  // 附件服务端持久化（owner=本任务）：parsing 中的文件存在时 hook 内每 1.5s 轮询；
  // execute 阶段后端会把原件暂存进沙箱工作区 uploads/，前端无需额外处理
  const {
    files,
    budget: uploadBudget,
    upload: uploadAttachments,
    remove: removeAttachment,
    addFiles: addAttachments,
  } = useAttachments('task', taskId)

  React.useEffect(() => {
    if (query.data) agentRunManager.reconcileServerState(query.data)
  }, [agentRunManager, query.data])

  const task = query.data
  const active = isAgentRunActive(run)
  const taskStatus = run?.taskStatus ?? task?.status ?? 'draft'
  const permissionMode = run?.permissionMode ?? task?.permission.mode ?? 'read'
  const messages = mergeAgentMessages(task?.messages ?? [], run)
  const toolEvents = React.useMemo(() => {
    const persisted = task?.events ?? []
    const live = run?.toolEvents ?? []
    const ids = new Set(persisted.map((event) => event.id))
    return [...persisted, ...live.filter((event) => !ids.has(event.id))]
  }, [run?.toolEvents, task?.events])
  const pendingApprovals = React.useMemo(
    () => (run?.toolApprovals ?? []).filter((approval) => approval.status === 'pending'),
    [run?.toolApprovals],
  )
  const approvedPlan = task?.plan?.status === 'approved'
  const canPlan = !active && Boolean(task) && !approvedPlan && taskStatus !== 'completed'
  // 计划已批准（含已完成/失败/取消）后，输入框切换为「执行模式」：
  // 追加的指令不再走规划，直接以当前权限档位进入沙箱执行
  const canFollowUpExecute = !active && approvedPlan

  React.useLayoutEffect(() => {
    const element = scrollRef.current
    if (!element || restoredScrollRef.current || !task) return
    restoredScrollRef.current = true
    const saved = agentRunManager.getScrollPosition(taskId)
    element.scrollTop = active ? element.scrollHeight : Math.min(saved ?? element.scrollHeight, element.scrollHeight)
    const bottomGap = element.scrollHeight - element.clientHeight - element.scrollTop
    shouldFollowOutputRef.current = active || bottomGap <= 96
  }, [active, agentRunManager, task, taskId])

  React.useEffect(() => {
    restoredScrollRef.current = false
    shouldFollowOutputRef.current = true
    wasActiveRef.current = false
    return () => {
      const element = scrollRef.current
      if (element) agentRunManager.setScrollPosition(taskId, element.scrollTop)
    }
  }, [agentRunManager, taskId])

  React.useLayoutEffect(() => {
    const element = scrollRef.current
    const startedActive = active && !wasActiveRef.current
    if (element && active && (startedActive || shouldFollowOutputRef.current)) {
      element.scrollTop = element.scrollHeight
      shouldFollowOutputRef.current = true
    }
    wasActiveRef.current = active
  }, [active, messages.length, run?.assistantMessage.content, toolEvents.length, pendingApprovals.length])

  const sendPlanRequest = React.useCallback(() => {
    const text = draft.trim()
    if (!text || !task || !canPlan) return
    setDraft('')
    setActionError(null)
    try {
      agentRunManager.startPlan(task, text, knowledgeScope)
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '无法启动规划')
    }
  }, [agentRunManager, canPlan, draft, knowledgeScope, task])

  const sendFollowUpExecute = React.useCallback(() => {
    const text = draft.trim()
    if (!text || !task || !canFollowUpExecute) return
    setDraft('')
    setActionError(null)
    try {
      agentRunManager.startExecute(task, text, knowledgeScope)
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '无法启动执行')
    }
  }, [agentRunManager, canFollowUpExecute, draft, knowledgeScope, task])

  const sendDraft = React.useCallback(() => {
    if (canPlan) sendPlanRequest()
    else sendFollowUpExecute()
  }, [canPlan, sendPlanRequest, sendFollowUpExecute])

  const changePermission = React.useCallback(async (mode: PermissionMode) => {
    if (!task || active) return
    setActionPending(true)
    setActionError(null)
    try {
      const permission = await api.setTaskPermission(task.id, mode)
      queryClient.setQueryData<AgentTaskDetail>(['task', task.id], (current) => (
        current ? { ...current, permission } : current
      ))
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '权限变更失败')
    } finally {
      setActionPending(false)
    }
  }, [active, queryClient, task])

  const approvePlan = React.useCallback(async () => {
    if (!task || active) return
    setActionPending(true)
    setActionError(null)
    try {
      const updated = await api.approveTask(task.id)
      queryClient.setQueryData(['task', task.id], updated)
      await queryClient.invalidateQueries({ queryKey: ['tasks'] })
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '计划审批失败')
    } finally {
      setActionPending(false)
    }
  }, [active, queryClient, task])

  const executePlan = React.useCallback(() => {
    if (!task || active || task.plan?.status !== 'approved') return
    setActionError(null)
    try {
      agentRunManager.startExecute(task, undefined, knowledgeScope)
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '无法启动执行')
    }
  }, [active, agentRunManager, knowledgeScope, task])

  const stopTask = React.useCallback(() => {
    void agentRunManager.cancel(taskId).catch((error) => {
      setActionError(error instanceof Error ? error.message : '停止任务失败')
    })
  }, [agentRunManager, taskId])

  const deleteTask = React.useCallback(async () => {
    if (active) return
    setActionPending(true)
    setActionError(null)
    try {
      await api.deleteTask(taskId)
      setDeleteConfirmOpen(false)
      onDeleted(taskId)
      void queryClient.invalidateQueries({ queryKey: ['tasks'] })
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '删除任务失败')
      setDeleteConfirmOpen(false)
    } finally {
      setActionPending(false)
    }
  }, [active, onDeleted, queryClient, taskId])

  // 选中即上传（服务端解析全文，chip 轮询 parsing→ready/failed）；解析中不阻塞发送
  const pickFiles = (selected: FileList | null) => {
    setActionError(null)
    addAttachments(selected, setActionError)
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex min-h-14 items-center justify-between gap-2 border-b border-line px-3 py-2 sm:h-14 sm:px-5 sm:py-0">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold">{title}</div>
        </div>
        <div className="flex items-center gap-2">
          <PermissionSegment
            value={permissionMode}
            onChange={(mode) => void changePermission(mode)}
            compact
            disabled={active || actionPending}
          />
          <button
            title="删除任务"
            className="flex h-8 w-8 items-center justify-center rounded-md border border-line text-zinc-500 hover:bg-red-50 hover:text-danger disabled:opacity-40"
            disabled={active || actionPending}
            onClick={() => setDeleteConfirmOpen(true)}
          >
            <Trash2 size={15} />
          </button>
        </div>
      </div>

      <div
        ref={scrollRef}
        className="thin-scrollbar min-h-0 flex-1 overflow-y-auto px-8 py-6"
        onScroll={(event) => {
          const element = event.currentTarget
          const bottomGap = element.scrollHeight - element.clientHeight - element.scrollTop
          shouldFollowOutputRef.current = bottomGap <= 96
          if (!active) agentRunManager.setScrollPosition(taskId, element.scrollTop)
        }}
      >
        <div className="mx-auto max-w-4xl space-y-5">
          <TaskPlanPanel
            status={taskStatus}
            plan={task?.plan ?? null}
            permissionMode={permissionMode}
            pending={active || actionPending}
            onApprove={() => void approvePlan()}
            onExecute={executePlan}
            onModeChange={(mode) => void changePermission(mode)}
          />
          {query.isLoading ? (
            <MessageSkeleton />
          ) : query.isError ? (
            <div className="border-y border-red-100 bg-red-50 px-4 py-8 text-center text-sm text-danger">
              {query.error instanceof Error ? query.error.message : '任务加载失败'}
            </div>
          ) : messages.length === 0 ? (
            <div className="border-y border-line py-16 text-center">
              <div className="text-sm font-medium">新任务</div>
              <div className="mt-1 text-xs text-zinc-500">输入目标后，Agent 会先生成可审批的执行计划。</div>
            </div>
          ) : (
            messages.map((message) => <MessageBubble key={message.id} message={message} onOpenTab={onOpenTab} />)
          )}
          <ToolEventTimeline events={toolEvents} activeRunId={run?.requestId ?? task?.currentRunId ?? null} />
          <ArtifactsCard taskId={taskId} active={active} />
          {pendingApprovals.length > 0 && (
            <ToolApprovalPanel
              approvals={pendingApprovals}
              onDecide={(approvalId, decision) => {
                void agentRunManager.decideApproval(taskId, approvalId, decision).catch((error) => {
                  setActionError(error instanceof Error ? error.message : '审批操作失败')
                })
              }}
            />
          )}
          {actionError && (
            <div className="border-l-2 border-danger bg-red-50 px-3 py-2 text-sm text-danger">
              {actionError}
            </div>
          )}
        </div>
      </div>

      <div className="border-t border-line bg-[#fbfbfc] px-6 py-4">
        <div className="mx-auto max-w-4xl">
          <div className="rounded-md border border-line bg-panel shadow-sm">
          <AttachmentChips
            files={files}
            onRemove={(fileId) => removeAttachment.mutate(fileId)}
            removing={removeAttachment.isPending}
          />
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              // 与 Chat 输入框一致：Enter 发送，Shift+Enter 换行；
              // isComposing 守卫防止中文输入法选词时的 Enter 误触发发送
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault()
                sendDraft()
              }
            }}
            className="block min-h-[82px] w-full resize-none bg-transparent px-4 py-3 text-sm outline-none"
            placeholder={approvedPlan
              ? '执行模式：输入追加指令直接执行（如「把结果保存成 Excel」），按当前权限档位运行'
              : '规划模式：输入任务目标，Agent 将先生成可审批的执行计划（Enter 发送，Shift+Enter 换行）'}
            disabled={!canPlan && !canFollowUpExecute}
          />
          <div className="flex items-center justify-between border-t border-line px-3 py-2">
            <div className="flex items-center gap-1.5">
              <span className={cn(
                'mr-1 rounded px-1.5 py-0.5 text-[11px] font-medium',
                approvedPlan ? 'bg-emerald-50 text-success' : 'bg-zinc-100 text-zinc-500',
              )}>
                {approvedPlan ? '执行模式' : '规划模式'}
              </span>
              <AttachmentPickerButton label="添加文件" onPick={pickFiles} />
              <IconButton label="语音输入" icon={Mic} />
              {knowledgeAvailable && (
                <AgentKnowledgePicker bases={knowledgeBasesQuery.data ?? []} />
              )}
              {uploadAttachments.isPending && (
                <span className="ml-2 text-xs text-zinc-500">上传中…</span>
              )}
            </div>
            <button
              className={cn(
                'flex h-8 items-center gap-2 rounded-md px-3 text-sm font-medium transition active:scale-[0.98]',
                active || ((canPlan || canFollowUpExecute) && draft.trim()) ? 'bg-ink text-white' : 'bg-zinc-200 text-zinc-400',
              )}
              disabled={!active && (!(canPlan || canFollowUpExecute) || !draft.trim())}
              onClick={active ? stopTask : sendDraft}
            >
              {active ? <CircleStop size={15} /> : <Send size={15} />}
              {active ? '停止' : '发送'}
            </button>
          </div>
          <AttachmentBudgetBar budget={uploadBudget} />
          </div>
        </div>
      </div>
      {deleteConfirmOpen && (
        <ConfirmDialog
          title="删除任务"
          description="确认删除该任务及其对话、计划和工具记录？此操作不可撤销。"
          pending={actionPending}
          onConfirm={() => void deleteTask()}
          onCancel={() => setDeleteConfirmOpen(false)}
        />
      )}
    </div>
  )
}
