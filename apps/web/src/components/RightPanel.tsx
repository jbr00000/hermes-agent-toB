import * as React from 'react'
import { useAtom } from 'jotai'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Archive,
  Check,
  ClipboardList,
  Database,
  FileArchive,
  LockKeyhole,
  PanelRight,
  PanelRightClose,
  PanelRightOpen,
  ShieldCheck,
  type LucideIcon,
} from 'lucide-react'
import { api } from '../api'
import { isAgentRunActive, useAgentRun } from '../agentRunManager'
import { mockApi } from '../mockApi'
import { rightPanelCollapsedAtom, selectedSpaceAtom } from '../state'
import type { AgentTaskDetail, KnowledgeDocument, PermissionMode, TabType, WorkTab } from '../types'
import { PermissionSegment } from './agent/PermissionSegment'
import { Badge, cn, IconButton, InfoRow } from './ui'
import { AttachmentRows } from './uploads/AttachmentChips'
import { useAttachments } from './uploads/useAttachments'

/** 右侧上下文面板：agent 标签显示任务上下文，chat 标签显示问答上下文。 */
export function RightPanel({ activeTab, onOpenTab }: { activeTab: WorkTab | null; onOpenTab?: (type: TabType, refId: string, title: string) => void }) {
  const [collapsed, setCollapsed] = useAtom(rightPanelCollapsedAtom)
  const queryClient = useQueryClient()
  const taskId = activeTab?.type === 'agent' ? activeTab.refId : ''
  const run = useAgentRun(taskId)
  const { files } = useAttachments('task', taskId)
  const taskQuery = useQuery({
    queryKey: ['task', taskId],
    queryFn: () => api.getTask(taskId),
    enabled: Boolean(taskId),
  })
  const docsQuery = useQuery({
    queryKey: ['knowledgeDocuments', 'ready'],
    queryFn: () => api.listKnowledgeDocuments('ready'),
    enabled: activeTab?.type === 'chat',
    retry: false,
  })
  const referencedDocs = (docsQuery.data?.documents ?? []).slice(0, 3)
  const task = taskQuery.data
  const permissionMode = run?.permissionMode ?? task?.permission.mode ?? 'read'
  const taskStatus = run?.taskStatus ?? task?.status ?? 'draft'
  const active = isAgentRunActive(run)

  const setPermissionMode = (mode: PermissionMode) => {
    if (!task || active) return
    void api.setTaskPermission(task.id, mode).then((permission) => {
      queryClient.setQueryData<AgentTaskDetail>(['task', task.id], (current) => (
        current ? { ...current, permission } : current
      ))
    })
  }

  if (collapsed) {
    return <CollapsedStrip onExpand={() => setCollapsed(false)} />
  }

  if (activeTab?.type === 'chat') {
    return <ChatRightPanel sessionId={activeTab.refId} referencedDocs={referencedDocs} onOpenTab={onOpenTab} onCollapse={() => setCollapsed(true)} />
  }

  return (
    <aside className="hidden min-h-0 flex-col bg-[#fbfbfc] xl:flex">
      <div className="flex h-14 items-center justify-between border-b border-line px-4">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <PanelRight size={16} />
          任务上下文
        </div>
        <div className="flex items-center gap-1.5">
          <Badge className="bg-zinc-100 text-zinc-600">{activeTab?.type ?? 'none'}</Badge>
          <IconButton label="收起面板" icon={PanelRightClose} onClick={() => setCollapsed(true)} />
        </div>
      </div>
      <div className="thin-scrollbar min-h-0 flex-1 overflow-y-auto p-4">
        <PanelSection title="权限模式" icon={ShieldCheck}>
          <PermissionSegment value={permissionMode} onChange={setPermissionMode} disabled={!task || active} />
          {permissionMode === 'full' && (
            <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-caution">
              完全访问已对本任务持久生效，切回只读后才会降权。
            </div>
          )}
        </PanelSection>

        <PanelSection title="本次任务文件" icon={FileArchive}>
          <AttachmentRows files={files} emptyText="暂无任务文件" />
        </PanelSection>

        <PanelSection title="引用知识库" icon={Database}>
          <div className="border-y border-line py-3 text-xs text-zinc-500">未选择知识库</div>
        </PanelSection>

        <PanelSection title="计划状态" icon={ClipboardList}>
          <div className="space-y-2 text-sm">
            <StepLine done={taskStatus !== 'draft'} label="识别任务风险" />
            <StepLine done={Boolean(task?.plan)} label="生成执行计划" />
            <StepLine done={task?.plan?.status === 'approved'} label="用户审批" />
            <StepLine done={taskStatus === 'completed'} label="执行与结果回传" />
          </div>
        </PanelSection>
      </div>
    </aside>
  )
}

function ChatRightPanel({ sessionId, referencedDocs, onOpenTab, onCollapse }: { sessionId: string; referencedDocs: KnowledgeDocument[]; onOpenTab?: (type: TabType, refId: string, title: string) => void; onCollapse: () => void }) {
  const [selectedSpace] = useAtom(selectedSpaceAtom)
  const spacesQuery = useQuery({ queryKey: ['spaces'], queryFn: mockApi.listSpaces })
  const activeSpace = (spacesQuery.data ?? []).find((space) => space.id === selectedSpace)
  const { files } = useAttachments('session', sessionId)

  return (
    <aside className="hidden min-h-0 flex-col bg-[#fbfbfc] xl:flex">
      <div className="flex h-14 items-center justify-between border-b border-line px-4">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <PanelRight size={16} />
          问答上下文
        </div>
        <div className="flex items-center gap-1.5">
          <Badge className="bg-[#e0ece4] text-[#28513d]">Chat</Badge>
          <IconButton label="收起面板" icon={PanelRightClose} onClick={onCollapse} />
        </div>
      </div>
      <div className="thin-scrollbar min-h-0 flex-1 overflow-y-auto p-4">
        <PanelSection title="安全模式" icon={LockKeyhole}>
          <div className="flex items-center justify-between border-y border-line py-2.5 text-sm">
            <span className="text-zinc-500">当前权限</span>
            <Badge className="bg-emerald-50 text-success">只读检索</Badge>
          </div>
        </PanelSection>

        <PanelSection title="当前范围" icon={Archive}>
          <div className="space-y-3">
            <InfoRow label="业务空间" value={activeSpace?.name ?? '诉讼仲裁'} />
            <InfoRow label="回答方式" value="知识库增强" />
          </div>
        </PanelSection>

        <PanelSection title="问答附件" icon={FileArchive}>
          <AttachmentRows files={files} emptyText="暂无附件" />
        </PanelSection>

        <PanelSection title="知识来源" icon={Database}>
          <div className="divide-y divide-line border-y border-line">
            {referencedDocs.length === 0 && (
              <div className="py-3 text-xs text-zinc-500">企业知识库暂无可检索文档</div>
            )}
            {referencedDocs.map((doc) => (
              <button
                key={doc.id}
                type="button"
                title="查看文档详情"
                className="block w-full py-2.5 text-left text-sm transition hover:bg-field"
                onClick={() => onOpenTab?.('document', doc.id, doc.title)}
              >
                <div className="truncate font-medium">{doc.title}</div>
                <div className="mt-1 flex items-center justify-between text-xs text-zinc-500">
                  <span className="truncate">{doc.fileName}</span>
                  <span className="shrink-0">{doc.chunkCount} 个片段</span>
                </div>
              </button>
            ))}
          </div>
        </PanelSection>
      </div>
    </aside>
  )
}

/** 收起后的窄条：只占 40px 宽，点击图标展开面板。 */
function CollapsedStrip({ onExpand }: { onExpand: () => void }) {
  return (
    <aside className="hidden min-h-0 flex-col items-center border-l border-line bg-[#fbfbfc] xl:flex">
      <div className="flex h-14 items-center justify-center border-b border-line">
        <IconButton label="展开面板" icon={PanelRightOpen} onClick={onExpand} />
      </div>
    </aside>
  )
}

function PanelSection({ title, icon: Icon, children }: { title: string; icon: LucideIcon; children: React.ReactNode }) {
  return (
    <section className="mb-5">
      <div className="mb-2 flex items-center gap-2 text-sm font-semibold">
        <Icon size={15} />
        {title}
      </div>
      {children}
    </section>
  )
}

function StepLine({ label, done }: { label: string; done?: boolean }) {
  return (
    <div className="flex items-center gap-2">
      <span className={cn('flex h-5 w-5 items-center justify-center rounded-full border text-[11px]', done ? 'border-success bg-emerald-50 text-success' : 'border-line text-zinc-400')}>
        {done ? <Check size={12} /> : ''}
      </span>
      <span className={done ? 'text-zinc-700' : 'text-zinc-500'}>{label}</span>
    </div>
  )
}
