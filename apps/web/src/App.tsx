import * as React from 'react'
import { useAtom } from 'jotai'
import { useQuery } from '@tanstack/react-query'
import {
  AlertTriangle,
  Archive,
  Bot,
  Brain,
  Check,
  ChevronDown,
  CircleStop,
  ClipboardList,
  Database,
  FileArchive,
  FileCheck2,
  FileText,
  Gauge,
  History,
  KeyRound,
  Layers3,
  LockKeyhole,
  MessageSquareText,
  Mic,
  MoreHorizontal,
  PanelRight,
  Paperclip,
  Play,
  Plus,
  Search,
  Send,
  ShieldCheck,
  Sparkles,
  Table2,
  UserCog,
  Users,
  X,
  type LucideIcon,
} from 'lucide-react'
import { db } from './db'
import { mockApi } from './mockApi'
import { documents, sessions as fallbackSessions } from './mockData'
import {
  activeTabIdAtom,
  attachedFilesAtom,
  createTab,
  permissionModeAtom,
  selectedSpaceAtom,
  tabsAtom,
} from './state'
import type {
  ChatMessage,
  KnowledgeDocument,
  KnowledgeSpace,
  MemoryCandidate,
  PermissionMode,
  SessionSummary,
  TabType,
  UserRow,
  WorkTab,
} from './types'

const MAX_TABS = 12

function cn(...classes: Array<string | false | null | undefined>): string {
  return classes.filter(Boolean).join(' ')
}

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

function typeIcon(type: TabType): LucideIcon {
  switch (type) {
    case 'agent':
      return Bot
    case 'knowledgeBase':
      return Database
    case 'document':
      return FileText
    case 'memory':
      return Brain
    case 'users':
      return Users
    case 'security':
      return ShieldCheck
    case 'audit':
      return ClipboardList
    default:
      return Layers3
  }
}

function statusText(status: SessionSummary['status']): string {
  return {
    idle: '空闲',
    running: '运行中',
    plan_pending: '待审批',
    approved: '已批准',
    completed: '已完成',
  }[status]
}

function statusTone(status: SessionSummary['status']): string {
  return {
    idle: 'bg-zinc-100 text-zinc-600',
    running: 'bg-emerald-50 text-success',
    plan_pending: 'bg-amber-50 text-caution',
    approved: 'bg-sky-50 text-info',
    completed: 'bg-zinc-100 text-zinc-600',
  }[status]
}

function docStatusText(status: KnowledgeDocument['status']): string {
  return {
    ready: '可引用',
    parsing: '解析中',
    review: '待审核',
    failed: '失败',
  }[status]
}

function docStatusTone(status: KnowledgeDocument['status']): string {
  return {
    ready: 'bg-emerald-50 text-success',
    parsing: 'bg-sky-50 text-info',
    review: 'bg-amber-50 text-caution',
    failed: 'bg-red-50 text-danger',
  }[status]
}

export default function App(): React.ReactElement {
  const [tabs, setTabs] = useAtom(tabsAtom)
  const [activeTabId, setActiveTabId] = useAtom(activeTabIdAtom)
  const [hydrated, setHydrated] = React.useState(false)
  const [notice, setNotice] = React.useState<string | null>(null)
  const restoredRef = React.useRef(false)
  const sessionsQuery = useQuery({ queryKey: ['sessions'], queryFn: mockApi.listSessions })
  const spacesQuery = useQuery({ queryKey: ['spaces'], queryFn: mockApi.listSpaces })

  React.useEffect(() => {
    if (restoredRef.current) return
    restoredRef.current = true
    db.tabs.orderBy('order').toArray()
      .then((stored) => {
        if (stored.length > 0) {
          setTabs(stored)
          setActiveTabId(stored[0]?.id ?? null)
        }
      })
      .finally(() => setHydrated(true))
  }, [setActiveTabId, setTabs])

  React.useEffect(() => {
    if (!hydrated || tabs.length > 0) return
    const first = sessionsQuery.data?.[0] ?? fallbackSessions[0]
    if (!first) return
    const tab = createTab('agent', first.id, first.title, 0)
    setTabs([tab])
    setActiveTabId(tab.id)
  }, [hydrated, sessionsQuery.data, setActiveTabId, setTabs, tabs.length])

  React.useEffect(() => {
    if (!hydrated) return
    const timer = window.setTimeout(() => {
      db.transaction('rw', db.tabs, async () => {
        await db.tabs.clear()
        if (tabs.length > 0) await db.tabs.bulkPut(tabs)
      }).catch(console.error)
    }, 250)
    return () => window.clearTimeout(timer)
  }, [hydrated, tabs])

  const openTab = React.useCallback((type: TabType, refId: string, title: string) => {
    const id = `${type}:${refId}`
    setTabs((current) => {
      const existing = current.find((tab) => tab.id === id)
      if (existing) {
        setActiveTabId(id)
        return current.map((tab) => tab.id === id ? { ...tab, updatedAt: Date.now() } : tab)
      }
      if (current.length >= MAX_TABS) {
        setNotice(`最多同时打开 ${MAX_TABS} 个标签`)
        return current
      }
      const next = createTab(type, refId, title, current.length)
      setActiveTabId(next.id)
      return [...current, next]
    })
  }, [setActiveTabId, setTabs])

  const closeTab = React.useCallback((tabId: string) => {
    setTabs((current) => {
      const index = current.findIndex((tab) => tab.id === tabId)
      const next = current.filter((tab) => tab.id !== tabId).map((tab, order) => ({ ...tab, order }))
      if (activeTabId === tabId) {
        setActiveTabId(next[Math.max(0, index - 1)]?.id ?? next[0]?.id ?? null)
      }
      return next
    })
  }, [activeTabId, setActiveTabId, setTabs])

  const activeTab = tabs.find((tab) => tab.id === activeTabId) ?? null

  return (
    <div className="min-h-[100dvh] bg-shell text-ink">
      <div className="grid min-h-[100dvh] grid-cols-1 lg:grid-cols-[280px_minmax(0,1fr)]">
        <Sidebar
          sessions={sessionsQuery.data ?? []}
          spaces={spacesQuery.data ?? []}
          activeTab={activeTab}
          onOpenTab={openTab}
        />
        <div className="flex min-w-0 flex-col">
          <TopBar spaces={spacesQuery.data ?? []} />
          <TabBar tabs={tabs} activeTabId={activeTabId} onActivate={setActiveTabId} onClose={closeTab} />
          <div className="grid min-h-0 flex-1 grid-cols-1 xl:grid-cols-[minmax(0,1fr)_340px]">
            <main className="min-w-0 overflow-hidden border-r border-line bg-panel">
              {activeTab ? (
                <TabContent tab={activeTab} onOpenTab={openTab} />
              ) : (
                <EmptyWorkspace onNewTask={() => {
                  const first = sessionsQuery.data?.[0] ?? fallbackSessions[0]
                  if (first) openTab('agent', first.id, first.title)
                }} />
              )}
            </main>
            <RightPanel activeTab={activeTab} />
          </div>
        </div>
      </div>
      {notice && (
        <div className="fixed bottom-5 left-1/2 z-50 -translate-x-1/2 rounded-md border border-line bg-panel px-4 py-2 text-sm shadow-panel">
          <span>{notice}</span>
          <button className="ml-3 text-zinc-500 hover:text-ink" onClick={() => setNotice(null)}>
            关闭
          </button>
        </div>
      )}
    </div>
  )
}

function Sidebar({
  sessions,
  spaces,
  activeTab,
  onOpenTab,
}: {
  sessions: SessionSummary[]
  spaces: KnowledgeSpace[]
  activeTab: WorkTab | null
  onOpenTab: (type: TabType, refId: string, title: string) => void
}) {
  return (
    <aside className="hidden min-h-0 flex-col border-r border-line bg-[#fbfbfc] lg:flex">
      <div className="border-b border-line px-4 py-4">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-md bg-ink text-white">
            <Bot size={19} />
          </div>
          <div className="min-w-0">
            <div className="truncate text-[15px] font-semibold">Hermes Agent</div>
            <div className="text-xs text-zinc-500">企业智能体工作台</div>
          </div>
        </div>
        <button
          className="mt-4 flex h-9 w-full items-center justify-center gap-2 rounded-md bg-ink px-3 text-sm font-medium text-white transition active:scale-[0.98]"
          onClick={() => onOpenTab('agent', `draft-${Date.now()}`, '新智能体任务')}
        >
          <Plus size={16} />
          新建任务
        </button>
        <label className="mt-3 flex h-9 items-center gap-2 rounded-md border border-line bg-panel px-3">
          <Search size={15} className="text-zinc-400" />
          <input className="min-w-0 flex-1 bg-transparent text-sm outline-none" placeholder="搜索会话、文档、用户" />
        </label>
      </div>

      <div className="thin-scrollbar min-h-0 flex-1 overflow-y-auto px-3 py-3">
        <NavGroup title="工作区">
          <NavButton active={activeTab?.type === 'knowledgeBase'} icon={Database} label="知识库" onClick={() => onOpenTab('knowledgeBase', 'main', '知识库')} />
          <NavButton active={activeTab?.type === 'memory'} icon={Brain} label="记忆中心" onClick={() => onOpenTab('memory', 'main', '记忆中心')} />
          <NavButton active={activeTab?.type === 'users'} icon={Users} label="用户与权限" onClick={() => onOpenTab('users', 'main', '用户与权限')} />
          <NavButton active={activeTab?.type === 'security'} icon={ShieldCheck} label="能力与安全" onClick={() => onOpenTab('security', 'main', '能力与安全')} />
          <NavButton active={activeTab?.type === 'audit'} icon={ClipboardList} label="审计中心" onClick={() => onOpenTab('audit', 'main', '审计中心')} />
        </NavGroup>

        <NavGroup title="业务空间">
          {spaces.map((space) => (
            <button
              key={space.id}
              className="flex w-full items-center justify-between rounded-md px-2.5 py-2 text-left text-sm text-zinc-700 transition hover:bg-field"
              onClick={() => onOpenTab('knowledgeBase', space.id, space.name)}
            >
              <span className="truncate">{space.name}</span>
              <span className="ml-2 rounded bg-zinc-100 px-1.5 py-0.5 text-[11px] text-zinc-500">{space.documents}</span>
            </button>
          ))}
        </NavGroup>

        <NavGroup title="最近会话">
          <div className="space-y-1">
            {sessions.map((session) => (
              <button
                key={session.id}
                className={cn(
                  'w-full rounded-md px-2.5 py-2 text-left transition hover:bg-field',
                  activeTab?.type === 'agent' && activeTab.refId === session.id && 'bg-field',
                )}
                onClick={() => onOpenTab('agent', session.id, session.title)}
              >
                <div className="flex items-center gap-2">
                  <span className={cn('h-2 w-2 rounded-full status-dot', session.status === 'running' ? 'bg-success text-success' : session.status === 'plan_pending' ? 'bg-caution text-caution' : 'bg-zinc-400 text-zinc-400')} />
                  <span className="min-w-0 flex-1 truncate text-sm font-medium">{session.title}</span>
                </div>
                <div className="mt-1 flex items-center justify-between pl-4 text-[11px] text-zinc-500">
                  <span>{session.space}</span>
                  <span>{session.updatedAt}</span>
                </div>
              </button>
            ))}
          </div>
        </NavGroup>
      </div>
    </aside>
  )
}

function NavGroup({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-5">
      <div className="mb-1.5 px-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-zinc-400">{title}</div>
      <div className="space-y-1">{children}</div>
    </section>
  )
}

function NavButton({ icon: Icon, label, active, onClick }: { icon: LucideIcon; label: string; active?: boolean; onClick: () => void }) {
  return (
    <button
      className={cn(
        'flex h-9 w-full items-center gap-2.5 rounded-md px-2.5 text-sm transition active:scale-[0.98]',
        active ? 'bg-ink text-white' : 'text-zinc-700 hover:bg-field',
      )}
      onClick={onClick}
    >
      <Icon size={16} />
      <span>{label}</span>
    </button>
  )
}

function TopBar({ spaces }: { spaces: KnowledgeSpace[] }) {
  const [selectedSpace, setSelectedSpace] = useAtom(selectedSpaceAtom)
  const activeSpace = spaces.find((space) => space.id === selectedSpace) ?? spaces[0]

  return (
    <header className="flex h-14 items-center justify-between border-b border-line bg-panel px-4">
      <div className="flex items-center gap-3">
        <button className="flex h-9 items-center gap-2 rounded-md border border-line bg-panel px-3 text-sm hover:bg-field">
          <Archive size={15} />
          <span>{activeSpace?.name ?? '业务空间'}</span>
          <ChevronDown size={14} className="text-zinc-400" />
        </button>
        <div className="hidden items-center gap-1.5 text-xs text-zinc-500 lg:flex">
          <span className="h-2 w-2 rounded-full bg-success" />
          <span>Mock 前端模式</span>
        </div>
      </div>
      <div className="flex items-center gap-2">
        {spaces.slice(0, 3).map((space) => (
          <button
            key={space.id}
            className={cn(
              'hidden h-8 rounded-md px-3 text-xs transition md:block',
              selectedSpace === space.id ? 'bg-ink text-white' : 'bg-field text-zinc-600 hover:bg-zinc-200',
            )}
            onClick={() => setSelectedSpace(space.id)}
          >
            {space.name}
          </button>
        ))}
          <div className="ml-2 hidden h-8 items-center gap-2 rounded-md border border-line px-2.5 text-xs sm:flex">
          <UserCog size={14} />
          admin
        </div>
      </div>
    </header>
  )
}

function TabBar({ tabs, activeTabId, onActivate, onClose }: { tabs: WorkTab[]; activeTabId: string | null; onActivate: (id: string) => void; onClose: (id: string) => void }) {
  return (
    <div className="thin-scrollbar flex h-11 items-end gap-1 overflow-x-auto border-b border-line bg-[#f9fafb] px-2">
      {tabs.map((tab) => {
        const Icon = typeIcon(tab.type)
        const active = tab.id === activeTabId
        return (
          <button
            key={tab.id}
            className={cn(
              'group flex h-9 max-w-[230px] items-center gap-2 rounded-t-md border border-b-0 px-3 text-sm transition',
              active ? 'border-line bg-panel text-ink' : 'border-transparent text-zinc-500 hover:bg-field hover:text-ink',
            )}
            onClick={() => onActivate(tab.id)}
          >
            <Icon size={14} />
            <span className="truncate">{tab.title}</span>
            <span
              role="button"
              tabIndex={0}
              className="rounded p-0.5 text-zinc-400 hover:bg-zinc-200 hover:text-ink"
              onClick={(event) => {
                event.stopPropagation()
                onClose(tab.id)
              }}
            >
              <X size={13} />
            </span>
          </button>
        )
      })}
    </div>
  )
}

function TabContent({ tab, onOpenTab }: { tab: WorkTab; onOpenTab: (type: TabType, refId: string, title: string) => void }) {
  if (tab.type === 'agent') return <AgentView sessionId={tab.refId} title={tab.title} />
  if (tab.type === 'knowledgeBase') return <KnowledgeBaseView selectedRef={tab.refId} onOpenTab={onOpenTab} />
  if (tab.type === 'document') return <DocumentView documentId={tab.refId} />
  if (tab.type === 'memory') return <MemoryView />
  if (tab.type === 'users') return <UsersView />
  if (tab.type === 'security') return <SecurityView />
  if (tab.type === 'audit') return <AuditView />
  return null
}

function EmptyWorkspace({ onNewTask }: { onNewTask: () => void }) {
  return (
    <div className="flex h-full items-center justify-center">
      <div className="text-center">
        <Bot className="mx-auto mb-3 text-zinc-300" size={42} />
        <div className="text-base font-medium">没有打开的标签</div>
        <button className="mt-4 rounded-md bg-ink px-4 py-2 text-sm text-white" onClick={onNewTask}>新建任务</button>
      </div>
    </div>
  )
}

function AgentView({ sessionId, title }: { sessionId: string; title: string }) {
  const [permissionMode, setPermissionMode] = useAtom(permissionModeAtom)
  const [files, setFiles] = useAtom(attachedFilesAtom)
  const query = useQuery({ queryKey: ['messages', sessionId], queryFn: () => mockApi.listMessages(sessionId) })
  const [localMessages, setLocalMessages] = React.useState<ChatMessage[]>([])
  const [draft, setDraft] = React.useState('')
  const [streaming, setStreaming] = React.useState(false)
  const fileInputRef = React.useRef<HTMLInputElement | null>(null)

  React.useEffect(() => {
    setLocalMessages(query.data ?? [])
  }, [query.data, sessionId])

  const sendMessage = React.useCallback(() => {
    const text = draft.trim()
    if (!text || streaming) return
    const now = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    const assistantId = `assistant-${Date.now()}`
    setDraft('')
    setStreaming(true)
    setLocalMessages((current) => [
      ...current,
      { id: `user-${Date.now()}`, role: 'user', content: text, createdAt: now },
      { id: assistantId, role: 'assistant', content: '', createdAt: now },
    ])

    const chunks = [
      '已进入前端模拟执行流程。',
      '我会先识别任务风险，再展示计划审批和权限模式。',
      '当前任务引用了临时附件与业务空间知识库；如果涉及共享知识库修改或数据库写入，将要求完全访问。',
    ]
    let index = 0
    const timer = window.setInterval(() => {
      setLocalMessages((current) => current.map((message) => (
        message.id === assistantId
          ? { ...message, content: `${message.content}${message.content ? '\n' : ''}${chunks[index] ?? ''}` }
          : message
      )))
      index += 1
      if (index >= chunks.length) {
        window.clearInterval(timer)
        setStreaming(false)
        if (permissionMode === 'full') setPermissionMode('read')
      }
    }, 520)
  }, [draft, permissionMode, setPermissionMode, streaming])

  const addFiles = (selected: FileList | null) => {
    if (!selected?.length) return
    const next = Array.from(selected).map((file) => ({
      id: `f-${Date.now()}-${file.name}`,
      name: file.name,
      size: file.size,
      status: 'parsing' as const,
    }))
    setFiles((current) => [...current, ...next])
    window.setTimeout(() => {
      setFiles((current) => current.map((file) => next.some((item) => item.id === file.id) ? { ...file, status: 'ready' } : file))
    }, 1100)
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex h-14 items-center justify-between border-b border-line px-5">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold">{title}</div>
          <div className="mt-0.5 flex items-center gap-2 text-xs text-zinc-500">
            <span>Session {sessionId}</span>
            <span className="h-1 w-1 rounded-full bg-zinc-300" />
            <span>{streaming ? '运行中' : '可输入'}</span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <PermissionSegment value={permissionMode} onChange={setPermissionMode} compact />
          <button className="h-8 rounded-md border border-line px-2.5 text-xs hover:bg-field">
            <MoreHorizontal size={15} />
          </button>
        </div>
      </div>

      <div className="thin-scrollbar min-h-0 flex-1 overflow-y-auto px-8 py-6">
        <div className="mx-auto max-w-4xl space-y-5">
          <PlanBanner permissionMode={permissionMode} onModeChange={setPermissionMode} />
          {query.isLoading ? (
            <MessageSkeleton />
          ) : localMessages.length === 0 ? (
            <div className="border-y border-line py-16 text-center">
              <Bot className="mx-auto mb-3 text-zinc-300" size={40} />
              <div className="text-sm font-medium">新任务</div>
            </div>
          ) : (
            localMessages.map((message) => <MessageBubble key={message.id} message={message} />)
          )}
          {streaming && (
            <div className="flex items-center gap-2 text-sm text-zinc-500">
              <span className="h-2 w-2 animate-pulse rounded-full bg-success" />
              正在生成
            </div>
          )}
        </div>
      </div>

      <div className="border-t border-line bg-[#fbfbfc] px-6 py-4">
        <div className="mx-auto max-w-4xl rounded-md border border-line bg-panel shadow-sm">
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            className="block min-h-[82px] w-full resize-none bg-transparent px-4 py-3 text-sm outline-none"
            placeholder="输入任务，例如：读取费用测算表，总结主要内容并生成 txt"
          />
          <div className="flex items-center justify-between border-t border-line px-3 py-2">
            <div className="flex items-center gap-1.5">
              <input ref={fileInputRef} type="file" multiple className="hidden" onChange={(event) => addFiles(event.target.files)} />
              <IconButton label="添加文件" icon={Paperclip} onClick={() => fileInputRef.current?.click()} />
              <IconButton label="语音输入" icon={Mic} />
              <IconButton label="选择知识库" icon={Database} />
              <span className="ml-2 text-xs text-zinc-500">{files.length} 个任务文件</span>
            </div>
            <button
              className={cn(
                'flex h-8 items-center gap-2 rounded-md px-3 text-sm font-medium transition active:scale-[0.98]',
                streaming || !draft.trim() ? 'bg-zinc-200 text-zinc-400' : 'bg-ink text-white',
              )}
              disabled={streaming || !draft.trim()}
              onClick={sendMessage}
            >
              {streaming ? <CircleStop size={15} /> : <Send size={15} />}
              {streaming ? '生成中' : '发送'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

function MessageBubble({ message }: { message: ChatMessage }) {
  const assistant = message.role === 'assistant'
  const system = message.role === 'system'
  return (
    <div className={cn('flex gap-3', !assistant && !system && 'justify-end')}>
      {(assistant || system) && (
        <div className={cn('flex h-8 w-8 shrink-0 items-center justify-center rounded-md', system ? 'bg-amber-50 text-caution' : 'bg-emerald-50 text-success')}>
          {system ? <ShieldCheck size={16} /> : <Bot size={16} />}
        </div>
      )}
      <div className={cn('max-w-[78%] whitespace-pre-wrap rounded-md border px-4 py-3 text-sm leading-6', assistant || system ? 'border-line bg-panel' : 'border-ink bg-ink text-white')}>
        <div>{message.content || '...'}</div>
        <div className={cn('mt-2 text-[11px]', assistant || system ? 'text-zinc-400' : 'text-white/60')}>{message.createdAt}</div>
      </div>
    </div>
  )
}

function MessageSkeleton() {
  return (
    <div className="space-y-3">
      {[0, 1, 2].map((item) => (
        <div key={item} className="h-16 animate-pulse rounded-md bg-field" />
      ))}
    </div>
  )
}

function PlanBanner({ permissionMode, onModeChange }: { permissionMode: PermissionMode; onModeChange: (mode: PermissionMode) => void }) {
  const needsFull = permissionMode !== 'full'
  return (
    <div className="border-y border-line bg-[#fcfcfd] px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-md bg-amber-50 text-caution">
            <AlertTriangle size={18} />
          </div>
          <div>
            <div className="text-sm font-semibold">计划审批模拟</div>
            <div className="text-xs text-zinc-500">写共享知识库或数据库写入需计划审批与完全访问同时满足</div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button className="flex h-8 items-center gap-1.5 rounded-md border border-line px-3 text-xs hover:bg-field">
            <FileCheck2 size={14} />
            批准计划
          </button>
          <button
            className={cn('flex h-8 items-center gap-1.5 rounded-md px-3 text-xs font-medium', needsFull ? 'bg-zinc-200 text-zinc-500' : 'bg-ink text-white')}
            onClick={() => onModeChange('full')}
          >
            <Play size={14} />
            {needsFull ? '切换完全访问' : '执行'}
          </button>
        </div>
      </div>
    </div>
  )
}

function PermissionSegment({ value, onChange, compact = false }: { value: PermissionMode; onChange: (mode: PermissionMode) => void; compact?: boolean }) {
  const items: Array<{ value: PermissionMode; label: string; icon: LucideIcon }> = [
    { value: 'read', label: '只读', icon: LockKeyhole },
    { value: 'controlled', label: '受控写入', icon: FileCheck2 },
    { value: 'full', label: '完全访问', icon: KeyRound },
  ]

  return (
    <div className="flex rounded-md border border-line bg-field p-0.5">
      {items.map((item) => {
        const Icon = item.icon
        return (
          <button
            key={item.value}
            className={cn(
              'flex items-center gap-1.5 rounded text-xs transition',
              compact ? 'h-8 w-8 justify-center px-0 sm:w-auto sm:px-2' : 'h-7 px-2',
              value === item.value ? 'bg-panel text-ink shadow-sm' : 'text-zinc-500 hover:text-ink',
              compact && item.value === 'controlled' && 'hidden xl:flex',
            )}
            onClick={() => onChange(item.value)}
          >
            <Icon size={13} />
            <span className={cn(compact && 'hidden sm:inline')}>{item.label}</span>
          </button>
        )
      })}
    </div>
  )
}

function IconButton({ label, icon: Icon, onClick }: { label: string; icon: LucideIcon; onClick?: () => void }) {
  return (
    <button title={label} className="flex h-8 w-8 items-center justify-center rounded-md text-zinc-500 hover:bg-field hover:text-ink" onClick={onClick}>
      <Icon size={16} />
    </button>
  )
}

function KnowledgeBaseView({ selectedRef, onOpenTab }: { selectedRef: string; onOpenTab: (type: TabType, refId: string, title: string) => void }) {
  const [selectedSpace, setSelectedSpace] = useAtom(selectedSpaceAtom)
  const spacesQuery = useQuery({ queryKey: ['spaces'], queryFn: mockApi.listSpaces })
  const docsQuery = useQuery({ queryKey: ['documents'], queryFn: mockApi.listDocuments })
  const spaces = spacesQuery.data ?? []
  const effectiveSpace = selectedRef !== 'main' ? selectedRef : selectedSpace
  const docs = (docsQuery.data ?? []).filter((doc) => doc.spaceId === effectiveSpace)

  React.useEffect(() => {
    if (selectedRef !== 'main') setSelectedSpace(selectedRef)
  }, [selectedRef, setSelectedSpace])

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader icon={Database} title="知识库" subtitle="业务空间、文档解析、引用范围与权限覆盖" />
      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[260px_minmax(0,1fr)]">
        <aside className="border-r border-line bg-[#fbfbfc] p-3">
          <div className="space-y-1">
            {spaces.map((space) => (
              <button
                key={space.id}
                className={cn('w-full rounded-md px-3 py-2 text-left text-sm transition hover:bg-field', effectiveSpace === space.id && 'bg-field')}
                onClick={() => setSelectedSpace(space.id)}
              >
                <div className="flex items-center justify-between">
                  <span className="font-medium">{space.name}</span>
                  <span className="text-[11px] text-zinc-500">{space.role}</span>
                </div>
                <div className="mt-1 text-xs text-zinc-500">{space.libraries} 个知识库 · {space.documents} 个文档</div>
              </button>
            ))}
          </div>
        </aside>
        <section className="thin-scrollbar min-w-0 overflow-y-auto p-5">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <div className="text-sm font-semibold">文档列表</div>
              <div className="text-xs text-zinc-500">默认继承空间权限，敏感文档可单独覆盖</div>
            </div>
            <button className="flex h-8 items-center gap-2 rounded-md bg-ink px-3 text-sm text-white">
              <Plus size={15} />
              上传到待处理区
            </button>
          </div>
          <DataTable>
            <thead>
              <tr>
                <Th>文档</Th>
                <Th>知识库</Th>
                <Th>状态</Th>
                <Th>权限</Th>
                <Th>分段</Th>
                <Th>更新</Th>
              </tr>
            </thead>
            <tbody>
              {docs.map((doc) => (
                <tr key={doc.id} className="border-b border-line last:border-0 hover:bg-[#fafafa]">
                  <Td>
                    <button className="flex min-w-0 items-center gap-2 text-left font-medium hover:text-info" onClick={() => onOpenTab('document', doc.id, doc.title)}>
                      <FileText size={15} className="shrink-0 text-zinc-400" />
                      <span className="truncate">{doc.title}</span>
                    </button>
                  </Td>
                  <Td>{doc.library}</Td>
                  <Td><Badge className={docStatusTone(doc.status)}>{docStatusText(doc.status)}</Badge></Td>
                  <Td>{doc.permission === 'override' ? <Badge className="bg-amber-50 text-caution">单独授权</Badge> : <Badge className="bg-zinc-100 text-zinc-600">继承</Badge>}</Td>
                  <Td>{doc.chunks}</Td>
                  <Td>{doc.updatedAt}</Td>
                </tr>
              ))}
            </tbody>
          </DataTable>
        </section>
      </div>
    </div>
  )
}

function DocumentView({ documentId }: { documentId: string }) {
  const doc = documents.find((item) => item.id === documentId) ?? documents[0]
  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader icon={FileText} title={doc.title} subtitle={`${doc.library} · ${doc.owner}`} />
      <div className="thin-scrollbar grid min-h-0 flex-1 grid-cols-1 overflow-y-auto xl:grid-cols-[minmax(0,1fr)_320px]">
        <section className="p-6">
          <div className="border-y border-line py-4">
            <div className="mb-3 flex items-center justify-between">
              <div className="text-sm font-semibold">解析摘要</div>
              <Badge className={docStatusTone(doc.status)}>{docStatusText(doc.status)}</Badge>
            </div>
            <p className="max-w-3xl text-sm leading-7 text-zinc-600">
              该文档已被切分为 {doc.chunks} 个可检索片段，包含费用构成、人员投入、阶段拆分、测算公式和风险说明。敏感文档已开启权限覆盖，Agent 只能在授权范围内引用。
            </p>
          </div>
          <div className="mt-6">
            <div className="mb-3 text-sm font-semibold">片段预览</div>
            <div className="divide-y divide-line border-y border-line">
              {['软件开发费用由需求分析、设计、编码、测试、交付运维五部分组成。', '人员投入按角色、级别、月投入比例进行折算。', '风险预备费建议按项目复杂度和需求稳定性分档。'].map((item, index) => (
                <div key={item} className="grid grid-cols-[70px_minmax(0,1fr)] gap-4 py-3 text-sm">
                  <span className="text-xs text-zinc-400">Chunk {index + 1}</span>
                  <span className="text-zinc-700">{item}</span>
                </div>
              ))}
            </div>
          </div>
        </section>
        <aside className="border-t border-line bg-[#fbfbfc] p-5 xl:border-l xl:border-t-0">
          <div className="mb-4 text-sm font-semibold">权限</div>
          <div className="space-y-3 text-sm">
            <InfoRow label="当前策略" value={doc.permission === 'override' ? '文档级覆盖' : '继承空间'} />
            <InfoRow label="可读角色" value="普通成员以上" />
            <InfoRow label="可维护角色" value="知识库贡献者" />
            <InfoRow label="管理角色" value="知识库管理员" />
          </div>
          <button className="mt-5 flex h-8 items-center gap-2 rounded-md border border-line px-3 text-sm hover:bg-field">
            <ShieldCheck size={15} />
            权限覆盖
          </button>
        </aside>
      </div>
    </div>
  )
}

function MemoryView() {
  const query = useQuery({ queryKey: ['memoryCandidates'], queryFn: mockApi.listMemoryCandidates })
  const [items, setItems] = React.useState<MemoryCandidate[]>([])

  React.useEffect(() => {
    setItems(query.data ?? [])
  }, [query.data])

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader icon={Brain} title="记忆中心" subtitle="个人记忆与待审核候选" />
      <section className="thin-scrollbar min-h-0 flex-1 overflow-y-auto p-6">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <div className="text-sm font-semibold">待审核候选</div>
            <div className="text-xs text-zinc-500">批准后写入个人长期记忆</div>
          </div>
          <button className="flex h-8 items-center gap-2 rounded-md border border-line px-3 text-sm hover:bg-field">
            <Plus size={15} />
            新增记忆
          </button>
        </div>
        <div className="divide-y divide-line border-y border-line">
          {items.map((item) => (
            <div key={item.id} className="grid grid-cols-[minmax(0,1fr)_auto] gap-5 py-4">
              <div>
                <div className="text-sm text-zinc-800">{item.content}</div>
                <div className="mt-2 text-xs text-zinc-500">来源：{item.source}</div>
              </div>
              <div className="flex items-center gap-2">
                <button className="h-8 rounded-md border border-line px-3 text-sm hover:bg-field" onClick={() => setItems((current) => current.filter((candidate) => candidate.id !== item.id))}>忽略</button>
                <button className="flex h-8 items-center gap-1.5 rounded-md bg-ink px-3 text-sm text-white" onClick={() => setItems((current) => current.filter((candidate) => candidate.id !== item.id))}>
                  <Check size={14} />
                  批准
                </button>
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}

function UsersView() {
  const query = useQuery({ queryKey: ['users'], queryFn: mockApi.listUsers })
  const [users, setUsers] = React.useState<UserRow[]>([])

  React.useEffect(() => {
    setUsers(query.data ?? [])
  }, [query.data])

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader icon={Users} title="用户与权限" subtitle="系统角色、空间成员与知识库角色" />
      <section className="thin-scrollbar min-h-0 flex-1 overflow-y-auto p-6">
        <div className="mb-4 flex justify-end">
          <button className="flex h-8 items-center gap-2 rounded-md bg-ink px-3 text-sm text-white">
            <Plus size={15} />
            创建用户
          </button>
        </div>
        <DataTable>
          <thead>
            <tr>
              <Th>用户</Th>
              <Th>系统角色</Th>
              <Th>业务空间</Th>
              <Th>状态</Th>
            </tr>
          </thead>
          <tbody>
            {users.map((user) => (
              <tr key={user.id} className="border-b border-line last:border-0">
                <Td>
                  <div className="font-medium">{user.username}</div>
                  <div className="text-xs text-zinc-500">{user.id}</div>
                </Td>
                <Td>
                  <select
                    className="h-8 rounded-md border border-line bg-panel px-2 text-sm"
                    value={user.role}
                    onChange={(event) => {
                      const role = event.target.value as UserRow['role']
                      setUsers((current) => current.map((item) => item.id === user.id ? { ...item, role } : item))
                    }}
                  >
                    <option value="admin">admin</option>
                    <option value="user">user</option>
                  </select>
                </Td>
                <Td>{user.spaces.join('、')}</Td>
                <Td><Badge className="bg-emerald-50 text-success">启用</Badge></Td>
              </tr>
            ))}
          </tbody>
        </DataTable>
      </section>
    </div>
  )
}

function SecurityView() {
  const query = useQuery({ queryKey: ['features'], queryFn: mockApi.getFeatures })
  const features = query.data
  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader icon={ShieldCheck} title="能力与安全" subtitle="运行模式、工具权限与高风险门控" />
      <section className="thin-scrollbar min-h-0 flex-1 overflow-y-auto p-6">
        <div className="grid gap-5 xl:grid-cols-2">
          <PlainPanel title="运行环境">
            <InfoRow label="Provider" value={features?.provider ?? 'deepseek'} />
            <InfoRow label="Model" value={features?.model ?? 'deepseek-v4-pro'} />
            <InfoRow label="Sandbox" value={features?.sandbox ?? 'docker'} />
            <InfoRow label="Host terminal" value={features?.host_terminal ? '开启' : '关闭'} />
          </PlainPanel>
          <PlainPanel title="权限模式策略">
            <PolicyRow icon={LockKeyhole} title="只读模式" text="问答、检索、读取授权文件、生成计划" />
            <PolicyRow icon={FileCheck2} title="受控写入" text="导出结果文件、个人记忆、上传待处理文档" />
            <PolicyRow icon={KeyRound} title="完全访问" text="共享知识库修改、数据库写入、终端命令" />
          </PlainPanel>
        </div>
        <div className="mt-6 border-y border-line py-4">
          <div className="mb-3 text-sm font-semibold">高风险操作</div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            {['修改共享知识库', '数据库写操作', '终端命令', '批量删除'].map((item) => (
              <div key={item} className="flex items-center gap-2 rounded-md bg-field px-3 py-2 text-sm">
                <AlertTriangle size={15} className="text-caution" />
                {item}
              </div>
            ))}
          </div>
        </div>
      </section>
    </div>
  )
}

function AuditView() {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader icon={ClipboardList} title="审计中心" subtitle="会话、工具、权限切换与高风险动作" />
      <section className="thin-scrollbar min-h-0 flex-1 overflow-y-auto p-6">
        <div className="border-y border-line">
          {[
            ['09:42', 'chat_turn', '智库平台费用测算文档总结', 'completed'],
            ['09:40', 'permission_mode', '受控写入切换', 'approved'],
            ['昨天', 'plan_approval', '合同条款差异分析', 'pending'],
          ].map(([time, type, subject, status]) => (
            <div key={`${time}-${type}`} className="grid grid-cols-[90px_150px_minmax(0,1fr)_120px] gap-4 border-b border-line px-2 py-3 text-sm last:border-0">
              <span className="text-zinc-500">{time}</span>
              <span className="font-mono text-xs text-zinc-500">{type}</span>
              <span>{subject}</span>
              <Badge className={status === 'completed' ? 'bg-emerald-50 text-success' : status === 'approved' ? 'bg-sky-50 text-info' : 'bg-amber-50 text-caution'}>{status}</Badge>
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}

function RightPanel({ activeTab }: { activeTab: WorkTab | null }) {
  const [permissionMode, setPermissionMode] = useAtom(permissionModeAtom)
  const [files] = useAtom(attachedFilesAtom)
  const docsQuery = useQuery({ queryKey: ['documents'], queryFn: mockApi.listDocuments })
  const referencedDocs = (docsQuery.data ?? []).slice(0, 3)

  return (
    <aside className="hidden min-h-0 flex-col bg-[#fbfbfc] xl:flex">
      <div className="flex h-14 items-center justify-between border-b border-line px-4">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <PanelRight size={16} />
          任务上下文
        </div>
        <Badge className="bg-zinc-100 text-zinc-600">{activeTab?.type ?? 'none'}</Badge>
      </div>
      <div className="thin-scrollbar min-h-0 flex-1 overflow-y-auto p-4">
        <PanelSection title="权限模式" icon={ShieldCheck}>
          <PermissionSegment value={permissionMode} onChange={setPermissionMode} />
          {permissionMode === 'full' && (
            <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-caution">
              完全访问仅当前任务生效，任务结束后自动降权。
            </div>
          )}
        </PanelSection>

        <PanelSection title="本次任务文件" icon={FileArchive}>
          <div className="divide-y divide-line border-y border-line">
            {files.map((file) => (
              <div key={file.id} className="py-2.5 text-sm">
                <div className="flex min-w-0 items-center gap-2">
                  <FileText size={15} className="shrink-0 text-zinc-400" />
                  <span className="truncate">{file.name}</span>
                </div>
                <div className="mt-1 flex items-center justify-between pl-6 text-xs text-zinc-500">
                  <span>{formatBytes(file.size)}</span>
                  <span>{file.status === 'ready' ? '可用' : '解析中'}</span>
                </div>
              </div>
            ))}
          </div>
        </PanelSection>

        <PanelSection title="引用知识库" icon={Database}>
          <div className="space-y-2">
            {referencedDocs.map((doc) => (
              <div key={doc.id} className="rounded-md border border-line bg-panel px-3 py-2 text-sm">
                <div className="truncate font-medium">{doc.title}</div>
                <div className="mt-1 flex items-center justify-between text-xs text-zinc-500">
                  <span>{doc.library}</span>
                  <span>{doc.permission === 'override' ? '单独授权' : '继承权限'}</span>
                </div>
              </div>
            ))}
          </div>
        </PanelSection>

        <PanelSection title="计划状态" icon={ClipboardList}>
          <div className="space-y-2 text-sm">
            <StepLine done label="识别任务风险" />
            <StepLine done label="生成执行计划" />
            <StepLine label="用户审批" />
            <StepLine label="执行与结果回传" />
          </div>
        </PanelSection>
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

function PageHeader({ icon: Icon, title, subtitle }: { icon: LucideIcon; title: string; subtitle: string }) {
  return (
    <header className="flex h-16 items-center justify-between border-b border-line px-6">
      <div className="flex min-w-0 items-center gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-field text-zinc-700">
          <Icon size={18} />
        </div>
        <div className="min-w-0">
          <div className="truncate text-base font-semibold">{title}</div>
          <div className="truncate text-xs text-zinc-500">{subtitle}</div>
        </div>
      </div>
      <button className="flex h-8 items-center gap-2 rounded-md border border-line px-3 text-sm hover:bg-field">
        <History size={15} />
        历史
      </button>
    </header>
  )
}

function DataTable({ children }: { children: React.ReactNode }) {
  return (
    <div className="thin-scrollbar overflow-x-auto border-y border-line">
      <table className="w-full min-w-[760px] table-fixed text-left text-sm">{children}</table>
    </div>
  )
}

function Th({ children }: { children: React.ReactNode }) {
  return <th className="border-b border-line bg-[#fafafa] px-3 py-2 text-xs font-semibold text-zinc-500">{children}</th>
}

function Td({ children }: { children: React.ReactNode }) {
  return <td className="min-w-0 px-3 py-3 align-middle text-sm text-zinc-700">{children}</td>
}

function Badge({ children, className }: { children: React.ReactNode; className?: string }) {
  return <span className={cn('inline-flex h-6 items-center rounded px-2 text-xs font-medium', className)}>{children}</span>
}

function PlainPanel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-y border-line py-4">
      <div className="mb-3 text-sm font-semibold">{title}</div>
      <div className="space-y-3">{children}</div>
    </section>
  )
}

function InfoRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 text-sm">
      <span className="text-zinc-500">{label}</span>
      <span className="min-w-0 truncate font-medium">{value}</span>
    </div>
  )
}

function PolicyRow({ icon: Icon, title, text }: { icon: LucideIcon; title: string; text: string }) {
  return (
    <div className="flex items-start gap-3">
      <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-field">
        <Icon size={15} />
      </div>
      <div>
        <div className="text-sm font-medium">{title}</div>
        <div className="mt-0.5 text-xs text-zinc-500">{text}</div>
      </div>
    </div>
  )
}
