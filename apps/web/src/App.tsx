import * as React from 'react'
import { useAtom } from 'jotai'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  Archive,
  Brain,
  Cat,
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
  LoaderCircle,
  LockKeyhole,
  LogOut,
  Menu,
  MessageSquareText,
  Mic,
  MoreHorizontal,
  PanelRight,
  Paperclip,
  Pencil,
  Pin,
  Play,
  Plus,
  Search,
  Send,
  ShieldCheck,
  SquareTerminal,
  Table2,
  Trash2,
  UserCog,
  Users,
  X,
  type LucideIcon,
} from 'lucide-react'
import { db } from './db'
import { api, ApiError, setCurrentUserUpdater } from './api'
import {
  isAgentRunActive,
  mergeAgentMessages,
  useAgentRun,
  useAgentRunManager,
} from './agentRunManager'
import {
  isChatRunActive,
  mergeChatMessages,
  useChatRun,
  useChatRunManager,
} from './chatRunManager'
import { mockApi } from './mockApi'
import { PetAvatar } from './components/pet/PetAvatar'
import type { PetState } from './components/pet/PetAvatar'
import { petStateFromAgentRun, petStateFromChatRun } from './components/pet/petState'
import { PetFloat } from './components/pet/PetFloat'
import { Markdown } from './components/Markdown'
import { Badge, DataTable, formatBytes, InfoRow, PageHeader, Td, Th } from './components/ui'
import { KnowledgeBaseView } from './components/knowledge/KnowledgeBaseView'
import { KnowledgeBaseListView } from './components/knowledge/KnowledgeBaseListView'
import { DocumentDetailView } from './components/knowledge/DocumentDetailView'
import { UserAdminView } from './components/users/UserAdminView'
import LoginBackdrop from './components/LoginBackdrop'
import {
  activeTabIdAtom,
  attachedFilesAtom,
  chatAttachedFilesAtom,
  createTab,
  knowledgeQaEnabledAtom,
  knowledgeQaKbIdAtom,
  petVisibleAtom,
  selectedSpaceAtom,
  tabId,
  tabsAtom,
  workspaceModeAtom,
} from './state'
import type {
  AgentTaskDetail,
  AgentTaskStatus,
  ChatMessage,
  ConversationSummary,
  AuthUser,
  KnowledgeBase,
  KnowledgeCitation,
  KnowledgeDocument,
  KnowledgeSpace,
  MemoryCandidate,
  PermissionMode,
  SessionSummary,
  TabType,
  TaskPlan,
  ToolApproval,
  ToolEvent,
  WorkspaceMode,
  WorkTab,
} from './types'

const MAX_TABS = 12
const CORTEX_MARK_URL = '/assets/cortex-logo-mark.svg'

function cn(...classes: Array<string | false | null | undefined>): string {
  return classes.filter(Boolean).join(' ')
}

type AppIcon = React.ComponentType<{ size?: number | string; className?: string }>

// Agent 模式专属图标：虚线航迹 + 纸飞机——"把任务派出去"，与纸面风格统一。
// 笔画参数对齐 lucide（24 视窗 / round caps / currentColor），可与 lucide 图标互换使用。
function AgentMark({ size = 24, className }: { size?: number | string; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.9}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      <path d="M3.5 20.5c2.8-.8 5.3-3.3 6.6-6.9" strokeDasharray="0.1 3.1" />
      <path d="M21 3 15.3 14.5 12.9 11.1 9.5 8.7 21 3z" />
      <path d="M21 3 12.9 11.1" />
    </svg>
  )
}

function typeIcon(type: TabType): AppIcon {
  switch (type) {
    case 'agent':
      return AgentMark
    case 'chat':
      return MessageSquareText
    case 'knowledgeBase':
    case 'knowledgeBaseDetail':
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
    draft: '草稿',
    queued: '排队中',
    planning: '规划中',
    awaiting_approval: '待审批',
    ready: '待执行',
    running: '运行中',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消',
  }[status]
}

function statusTone(status: SessionSummary['status']): string {
  return {
    draft: 'bg-zinc-100 text-zinc-600',
    queued: 'bg-sky-50 text-info',
    planning: 'bg-emerald-50 text-success',
    awaiting_approval: 'bg-amber-50 text-caution',
    ready: 'bg-sky-50 text-info',
    running: 'bg-emerald-50 text-success',
    completed: 'bg-zinc-100 text-zinc-600',
    failed: 'bg-red-50 text-danger',
    cancelled: 'bg-zinc-100 text-zinc-600',
  }[status]
}

export default function App(): React.ReactElement {
  const [user, setUser] = React.useState<AuthUser | null>(null)
  const [restoring, setRestoring] = React.useState(true)
  const [, setTabs] = useAtom(tabsAtom)
  const [, setActiveTabId] = useAtom(activeTabIdAtom)
  const [, setAttachedFiles] = useAtom(attachedFilesAtom)
  const [, setChatAttachedFiles] = useAtom(chatAttachedFilesAtom)
  const queryClient = useQueryClient()
  const chatRunManager = useChatRunManager()
  const agentRunManager = useAgentRunManager()

  React.useEffect(() => {
    api.restoreSession()
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setRestoring(false))
  }, [])

  // 403 自愈：任何请求被权限拦截时，api 层后台重拉 /auth/me 并回调这里刷新用户。
  React.useEffect(() => {
    setCurrentUserUpdater(setUser)
    return () => setCurrentUserUpdater(null)
  }, [])

  if (restoring) return <AppLoading />
  if (!user) return <LoginView onLogin={setUser} />
  if (user.mustChangePassword) {
    return <ForcePasswordChangeView username={user.username} onChanged={setUser} />
  }
  return (
    <WorkspaceApp
      user={user}
      onLogout={() => {
        chatRunManager.clearAll()
        agentRunManager.clearAll()
        queryClient.clear()
        setTabs([])
        setActiveTabId(null)
        setAttachedFiles([])
        setChatAttachedFiles([])
        setUser(null)
      }}
    />
  )
}

function AppLoading(): React.ReactElement {
  return (
    <div className="flex min-h-[100dvh] items-center justify-center bg-shell text-ink">
      <div className="flex items-center gap-3 text-sm text-zinc-500">
        <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-success" />
        正在连接企业工作台
      </div>
    </div>
  )
}

function LoginView({ onLogin }: { onLogin: (user: AuthUser) => void }): React.ReactElement {
  const [username, setUsername] = React.useState('admin')
  const [password, setPassword] = React.useState('')
  const [remember, setRemember] = React.useState(false)
  const [submitting, setSubmitting] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!username.trim() || !password) return
    setSubmitting(true)
    setError(null)
    try {
      onLogin(await api.login(username.trim(), password, remember))
    } catch (loginError) {
      setError(loginError instanceof Error ? loginError.message : '登录失败')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="relative min-h-[100dvh] overflow-hidden bg-shell text-ink">
      <LoginBackdrop />
      <div className="relative z-10 min-h-[100dvh] overflow-y-auto px-4 py-8 sm:px-6">
        <div className="mx-auto flex min-h-[calc(100dvh-4rem)] w-full max-w-[400px] flex-col justify-center">
        <div className="mb-5 flex items-center gap-3 px-1">
          <div className="flex h-10 w-10 items-center justify-center rounded-md bg-[#237a57] text-white shadow-sm">
            <img src={CORTEX_MARK_URL} alt="" className="h-8 w-8" />
          </div>
          <div>
            <div className="text-lg font-semibold text-white">Cortex Agent</div>
            <div className="text-xs text-zinc-400">企业智能体工作台</div>
          </div>
        </div>
        <form className="rounded-md border border-white/80 bg-panel/95 p-5 shadow-[0_18px_50px_rgba(0,0,0,0.45)] backdrop-blur-[2px] sm:p-7" onSubmit={submit}>
          <div className="mb-5">
            <div className="text-base font-semibold">登录</div>
            <div className="mt-1 text-sm text-zinc-500">使用企业账号进入工作台</div>
          </div>
          <label className="mb-4 block">
            <span className="mb-1.5 block text-xs font-medium text-zinc-600">用户名</span>
            <input
              autoComplete="username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              className="h-10 w-full rounded-md border border-line bg-panel px-3 text-sm outline-none transition focus:border-zinc-500 focus:ring-2 focus:ring-zinc-200"
            />
          </label>
          <label className="block">
            <span className="mb-1.5 block text-xs font-medium text-zinc-600">密码</span>
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="h-10 w-full rounded-md border border-line bg-panel px-3 text-sm outline-none transition focus:border-zinc-500 focus:ring-2 focus:ring-zinc-200"
            />
          </label>
          <label className="mt-3 flex items-center gap-2 text-sm text-zinc-700">
            <input
              type="checkbox"
              checked={remember}
              onChange={(event) => setRemember(event.target.checked)}
              className="h-4 w-4 accent-[#3d735a]"
            />
            保持登录（30 天内免登录）
          </label>
          {error && <div className="mt-4 bg-red-50 px-3 py-2 text-sm text-danger" role="alert">{error}</div>}
          <button
            type="submit"
            disabled={submitting || !username.trim() || !password}
            className="mt-5 flex h-10 w-full items-center justify-center gap-2 rounded-md bg-ink text-sm font-medium text-white transition active:scale-[0.98] disabled:bg-zinc-300 disabled:active:scale-100"
          >
            <LockKeyhole size={15} />
            {submitting ? '正在登录' : '登录'}
          </button>
        </form>
        </div>
      </div>
    </div>
  )
}

function ForcePasswordChangeView({
  username,
  onChanged,
}: {
  username: string
  onChanged: (user: AuthUser) => void
}): React.ReactElement {
  const [oldPassword, setOldPassword] = React.useState('')
  const [newPassword, setNewPassword] = React.useState('')
  const [confirmPassword, setConfirmPassword] = React.useState('')
  const [submitting, setSubmitting] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!oldPassword || !newPassword) return
    if (newPassword !== confirmPassword) {
      setError('两次输入的新密码不一致')
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      onChanged(await api.changePassword(oldPassword, newPassword))
    } catch (changeError) {
      setError(changeError instanceof Error ? changeError.message : '修改密码失败')
    } finally {
      setSubmitting(false)
    }
  }

  const inputClass = 'h-10 w-full rounded-md border border-line bg-panel px-3 text-sm outline-none transition focus:border-zinc-500 focus:ring-2 focus:ring-zinc-200'

  return (
    <div className="relative min-h-[100dvh] overflow-hidden bg-shell text-ink">
      <LoginBackdrop />
      <div className="relative z-10 min-h-[100dvh] overflow-y-auto px-4 py-8 sm:px-6">
        <div className="mx-auto flex min-h-[calc(100dvh-4rem)] w-full max-w-[400px] flex-col justify-center">
        <div className="mb-5 flex items-center gap-3 px-1">
          <div className="flex h-10 w-10 items-center justify-center rounded-md bg-[#237a57] text-white shadow-sm">
            <img src={CORTEX_MARK_URL} alt="" className="h-8 w-8" />
          </div>
          <div>
            <div className="text-lg font-semibold text-white">Cortex Agent</div>
            <div className="text-xs text-zinc-400">企业智能体工作台</div>
          </div>
        </div>
        <form className="rounded-md border border-white/80 bg-panel/95 p-5 shadow-[0_18px_50px_rgba(0,0,0,0.45)] backdrop-blur-[2px] sm:p-7" onSubmit={submit}>
          <div className="mb-5">
            <div className="text-base font-semibold">设置新密码</div>
            <div className="mt-1 text-sm text-zinc-500">
              账号 {username} 使用的是初始密码，请先设置新密码（至少 8 位）
            </div>
          </div>
          <label className="mb-4 block">
            <span className="mb-1.5 block text-xs font-medium text-zinc-600">当前密码</span>
            <input
              type="password"
              autoComplete="current-password"
              value={oldPassword}
              onChange={(event) => setOldPassword(event.target.value)}
              className={inputClass}
            />
          </label>
          <label className="mb-4 block">
            <span className="mb-1.5 block text-xs font-medium text-zinc-600">新密码</span>
            <input
              type="password"
              autoComplete="new-password"
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              className={inputClass}
            />
          </label>
          <label className="block">
            <span className="mb-1.5 block text-xs font-medium text-zinc-600">确认新密码</span>
            <input
              type="password"
              autoComplete="new-password"
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
              className={inputClass}
            />
          </label>
          {error && <div className="mt-4 bg-red-50 px-3 py-2 text-sm text-danger" role="alert">{error}</div>}
          <button
            type="submit"
            disabled={submitting || !oldPassword || !newPassword || !confirmPassword}
            className="mt-5 flex h-10 w-full items-center justify-center gap-2 rounded-md bg-ink text-sm font-medium text-white transition active:scale-[0.98] disabled:bg-zinc-300 disabled:active:scale-100"
          >
            <LockKeyhole size={15} />
            {submitting ? '正在提交' : '确认修改'}
          </button>
        </form>
        </div>
      </div>
    </div>
  )
}

function WorkspaceApp({ user, onLogout }: { user: AuthUser; onLogout: () => void }): React.ReactElement {
  const [tabs, setTabs] = useAtom(tabsAtom)
  const [activeTabId, setActiveTabId] = useAtom(activeTabIdAtom)
  const [workspaceMode, setWorkspaceMode] = useAtom(workspaceModeAtom)
  const [hydrated, setHydrated] = React.useState(false)
  const [notice, setNotice] = React.useState<string | null>(null)
  const [mobileSidebarOpen, setMobileSidebarOpen] = React.useState(false)
  const restoredRef = React.useRef(false)
  const initialTabSelectedRef = React.useRef(false)
  const requestedWorkspaceModeRef = React.useRef<WorkspaceMode>(workspaceMode)
  const pendingWorkspaceModeRef = React.useRef<WorkspaceMode | null>(null)
  const modeCreationsRef = React.useRef(new Set<WorkspaceMode>())
  const queryClient = useQueryClient()
  const sessionsQuery = useQuery({ queryKey: ['tasks'], queryFn: api.listTasks })
  const conversationsQuery = useQuery({ queryKey: ['conversations'], queryFn: api.listConversations })
  const spacesQuery = useQuery({ queryKey: ['spaces'], queryFn: mockApi.listSpaces })

  // 当前工作模式对应的功能被管理员关闭时，自动切到仍可用的模式
  React.useEffect(() => {
    if (user.features[workspaceMode]) return
    const fallback: WorkspaceMode | null = user.features.agent ? 'agent' : user.features.chat ? 'chat' : null
    if (fallback) setWorkspaceMode(fallback)
  }, [setWorkspaceMode, user.features, workspaceMode])

  React.useEffect(() => {
    if (restoredRef.current) return
    restoredRef.current = true
    setTabs([])
    setActiveTabId(null)
    db.tabs.where('ownerId').equals(user.id).sortBy('order')
      .then((stored) => {
        if (stored.length > 0) {
          const preferred = stored
            .filter((tab) => tab.type === workspaceMode)
            .sort((left, right) => right.updatedAt - left.updatedAt)[0] ?? stored[0]
          setTabs(stored)
          setActiveTabId(preferred?.id ?? null)
          if (preferred?.type === 'agent' || preferred?.type === 'chat') setWorkspaceMode(preferred.type)
        }
      })
      .finally(() => setHydrated(true))
  }, [setActiveTabId, setTabs, setWorkspaceMode, user.id, workspaceMode])

  React.useEffect(() => {
    if (!hydrated || initialTabSelectedRef.current) return
    if (tabs.length > 0) {
      initialTabSelectedRef.current = true
      return
    }
    const sourceQuery = workspaceMode === 'chat' ? conversationsQuery : sessionsQuery
    if (!sourceQuery.isSuccess) return
    initialTabSelectedRef.current = true
    const first = workspaceMode === 'chat'
      ? conversationsQuery.data?.[0]
      : sessionsQuery.data?.[0]
    if (first) {
      const tab = createTab(user.id, workspaceMode, first.id, first.title, 0)
      setTabs([tab])
      setActiveTabId(tab.id)
    }
  }, [
    conversationsQuery.data,
    conversationsQuery.isSuccess,
    hydrated,
    sessionsQuery.data,
    sessionsQuery.isSuccess,
    setActiveTabId,
    setTabs,
    tabs.length,
    user.id,
    workspaceMode,
  ])

  React.useEffect(() => {
    if (!hydrated) return
    const timer = window.setTimeout(() => {
      db.transaction('rw', db.tabs, async () => {
        await db.tabs.where('ownerId').equals(user.id).delete()
        const ownedTabs = tabs.filter((tab) => tab.ownerId === user.id)
        if (ownedTabs.length > 0) await db.tabs.bulkPut(ownedTabs)
      }).catch(console.error)
    }, 250)
    return () => window.clearTimeout(timer)
  }, [hydrated, tabs, user.id])

  React.useEffect(() => {
    if (!hydrated || !sessionsQuery.isSuccess) return
    const taskIds = new Set((sessionsQuery.data ?? []).map((task) => task.id))
    setTabs((current) => {
      const valid = current
        .filter((tab) => tab.type !== 'agent' || taskIds.has(tab.refId))
        .map((tab, order) => ({ ...tab, order }))
      if (valid.length === current.length) return current
      if (activeTabId && !valid.some((tab) => tab.id === activeTabId)) {
        const next = valid[0] ?? null
        setActiveTabId(next?.id ?? null)
        if (next?.type === 'agent' || next?.type === 'chat') setWorkspaceMode(next.type)
      }
      return valid
    })
  }, [activeTabId, hydrated, sessionsQuery.data, sessionsQuery.isSuccess, setActiveTabId, setTabs, setWorkspaceMode])

  React.useEffect(() => {
    if (!sessionsQuery.data) return
    const titles = new Map(sessionsQuery.data.map((task) => [task.id, task.title]))
    setTabs((current) => {
      let changed = false
      const next = current.map((tab) => {
        const title = tab.type === 'agent' ? titles.get(tab.refId) : undefined
        if (!title || title === tab.title) return tab
        changed = true
        return { ...tab, title, updatedAt: Date.now() }
      })
      return changed ? next : current
    })
  }, [sessionsQuery.data, setTabs])

  const openTab = React.useCallback((type: TabType, refId: string, title: string) => {
    const id = tabId(user.id, type, refId)
    if (type === 'agent' || type === 'chat') {
      requestedWorkspaceModeRef.current = type
      setWorkspaceMode(type)
    }
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
      const next = createTab(user.id, type, refId, title, current.length)
      setActiveTabId(next.id)
      return [...current, next]
    })
  }, [setActiveTabId, setTabs, setWorkspaceMode, user.id])

  const closeTab = React.useCallback((tabId: string) => {
    setTabs((current) => {
      const index = current.findIndex((tab) => tab.id === tabId)
      const next = current.filter((tab) => tab.id !== tabId).map((tab, order) => ({ ...tab, order }))
      if (activeTabId === tabId) {
        const nextActive = next[Math.max(0, index - 1)] ?? next[0] ?? null
        setActiveTabId(nextActive?.id ?? null)
        if (nextActive?.type === 'agent' || nextActive?.type === 'chat') {
          requestedWorkspaceModeRef.current = nextActive.type
          setWorkspaceMode(nextActive.type)
        }
      }
      return next
    })
  }, [activeTabId, setActiveTabId, setTabs, setWorkspaceMode])

  const activateTab = React.useCallback((tabId: string) => {
    const tab = tabs.find((item) => item.id === tabId)
    setActiveTabId(tabId)
    if (tab?.type === 'agent' || tab?.type === 'chat') {
      requestedWorkspaceModeRef.current = tab.type
      setWorkspaceMode(tab.type)
    }
  }, [setActiveTabId, setWorkspaceMode, tabs])

  const createConversation = React.useCallback((activate = true) => {
    return api.createConversation()
      .then((conversation) => {
        queryClient.setQueryData<ConversationSummary[]>(['conversations'], (current = []) => [conversation, ...current])
        if (activate) openTab('chat', conversation.id, conversation.title)
        return conversation
      })
      .catch((error) => {
        setNotice(error instanceof Error ? error.message : '新建问答失败')
        return null
      })
  }, [openTab, queryClient])

  const createAgentTask = React.useCallback((title?: string, activate = true) => {
    return api.createTask(title)
      .then((task) => {
        const summary: SessionSummary = {
          id: task.id,
          title: task.title,
          space: '企业工作区',
          status: task.status,
          updatedAt: new Date(task.updatedAt).toLocaleTimeString('zh-CN', {
            hour: '2-digit',
            minute: '2-digit',
          }),
          risk: task.risk,
        }
        queryClient.setQueryData<AgentTaskDetail>(['task', task.id], task)
        queryClient.setQueryData<SessionSummary[]>(['tasks'], (current = []) => [
          summary,
          ...current.filter((item) => item.id !== task.id),
        ])
        if (activate) openTab('agent', task.id, task.title)
        return task
      })
      .catch((error) => {
        setNotice(error instanceof Error ? error.message : '新建任务失败')
        return null
      })
  }, [openTab, queryClient])

  const changeWorkspaceMode = React.useCallback((mode: WorkspaceMode) => {
    requestedWorkspaceModeRef.current = mode
    setWorkspaceMode(mode)
    const existing = tabs
      .filter((tab) => tab.type === mode)
      .sort((left, right) => right.updatedAt - left.updatedAt)[0]
    if (existing) {
      pendingWorkspaceModeRef.current = null
      setActiveTabId(existing.id)
      return
    }

    setActiveTabId(null)
    const sourceReady = mode === 'chat'
      ? conversationsQuery.isSuccess
      : sessionsQuery.isSuccess
    if (!sourceReady) {
      pendingWorkspaceModeRef.current = mode
      return
    }
    pendingWorkspaceModeRef.current = null
    const first = mode === 'chat'
      ? conversationsQuery.data?.[0]
      : sessionsQuery.data?.[0]
    if (first) {
      openTab(mode, first.id, first.title)
      return
    }
    if (modeCreationsRef.current.has(mode)) return

    modeCreationsRef.current.add(mode)
    const creation = mode === 'chat'
      ? createConversation(false)
      : createAgentTask(undefined, false)
    void creation
      .then((created) => {
        if (created && requestedWorkspaceModeRef.current === mode) {
          openTab(mode, created.id, created.title)
        }
      })
      .finally(() => modeCreationsRef.current.delete(mode))
  }, [
    conversationsQuery.data,
    conversationsQuery.isSuccess,
    createAgentTask,
    createConversation,
    openTab,
    sessionsQuery.data,
    sessionsQuery.isSuccess,
    setActiveTabId,
    setWorkspaceMode,
    tabs,
  ])

  React.useEffect(() => {
    const pendingMode = pendingWorkspaceModeRef.current
    if (!pendingMode) return
    const sourceReady = pendingMode === 'chat'
      ? conversationsQuery.isSuccess
      : sessionsQuery.isSuccess
    if (sourceReady) changeWorkspaceMode(pendingMode)
  }, [changeWorkspaceMode, conversationsQuery.isSuccess, sessionsQuery.isSuccess])

  const updateConversationTab = React.useCallback((sessionId: string, title: string) => {
    setTabs((current) => current.map((tab) => (
      tab.type === 'chat' && tab.refId === sessionId ? { ...tab, title, updatedAt: Date.now() } : tab
    )))
  }, [setTabs])

  const activeTab = tabs.find((tab) => tab.id === activeTabId) ?? null

  return (
    <div className="h-[100dvh] overflow-hidden bg-shell text-ink">
      <div className="grid h-full grid-cols-1 lg:grid-cols-[280px_minmax(0,1fr)]">
        <Sidebar
          user={user}
          sessions={sessionsQuery.data ?? []}
          conversations={conversationsQuery.data ?? []}
          mode={workspaceMode}
          activeTab={activeTab}
          onOpenTab={openTab}
          onModeChange={changeWorkspaceMode}
          onNewConversation={createConversation}
          onNewAgentTask={() => createAgentTask()}
          mobileOpen={mobileSidebarOpen}
          onClose={() => setMobileSidebarOpen(false)}
        />
        <div className="flex min-h-0 min-w-0 flex-col overflow-hidden">
          <TopBar
            spaces={spacesQuery.data ?? []}
            user={user}
            onOpenNavigation={() => setMobileSidebarOpen(true)}
            onLogout={() => void api.logout().finally(onLogout)}
          />
          <TabBar tabs={tabs} activeTabId={activeTabId} onActivate={activateTab} onClose={closeTab} />
          <div className="grid min-h-0 flex-1 grid-cols-1 overflow-hidden xl:grid-cols-[minmax(0,1fr)_340px]">
            <main className="relative min-w-0 overflow-hidden border-r border-line bg-panel">
              {activeTab ? (
                <TabContent
                  tab={activeTab}
                  user={user}
                  onOpenTab={openTab}
                  onConversationUpdated={updateConversationTab}
                  onConversationArchived={(sessionId) => closeTab(tabId(user.id, 'chat', sessionId))}
                  onCreateAgentTask={createAgentTask}
                  onTaskDeleted={(taskId) => closeTab(tabId(user.id, 'agent', taskId))}
                />
              ) : (
                <EmptyWorkspace onNewTask={() => {
                  if (workspaceMode === 'chat') void createConversation()
                  else void createAgentTask()
                }} />
              )}
              <PetFloat tab={activeTab} />
            </main>
            <RightPanel activeTab={activeTab} />
          </div>
        </div>
      </div>
      {notice && (
        <div className="fixed bottom-5 left-1/2 z-50 -translate-x-1/2 rounded-md border border-line bg-panel px-4 py-2 text-sm shadow-card">
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
  user,
  sessions,
  conversations,
  mode,
  activeTab,
  onOpenTab,
  onModeChange,
  onNewConversation,
  onNewAgentTask,
  mobileOpen,
  onClose,
}: {
  user: AuthUser
  sessions: SessionSummary[]
  conversations: ConversationSummary[]
  mode: WorkspaceMode
  activeTab: WorkTab | null
  onOpenTab: (type: TabType, refId: string, title: string) => void
  onModeChange: (mode: WorkspaceMode) => void
  onNewConversation: () => void
  onNewAgentTask: () => void
  mobileOpen: boolean
  onClose: () => void
}) {
  const [searchQuery, setSearchQuery] = React.useState('')
  const normalizedSearch = searchQuery.trim().toLocaleLowerCase('zh-CN')
  const visibleSessions = normalizedSearch
    ? sessions.filter((session) => session.title.toLocaleLowerCase('zh-CN').includes(normalizedSearch))
    : sessions
  const visibleConversations = normalizedSearch
    ? conversations.filter((conversation) => conversation.title.toLocaleLowerCase('zh-CN').includes(normalizedSearch))
    : conversations
  const pinnedConversations = visibleConversations.filter((conversation) => conversation.pinned)
  const conversationGroups: Array<{ title: string; items: ConversationSummary[] }> = [
    { title: '今天', items: visibleConversations.filter((conversation) => !conversation.pinned && conversation.period === 'today') },
    { title: '昨天', items: visibleConversations.filter((conversation) => !conversation.pinned && conversation.period === 'yesterday') },
    { title: '更早', items: visibleConversations.filter((conversation) => !conversation.pinned && conversation.period === 'earlier') },
  ]

  const openTab = (type: TabType, refId: string, title: string) => {
    onOpenTab(type, refId, title)
    onClose()
  }

  const changeMode = (nextMode: WorkspaceMode) => {
    onModeChange(nextMode)
    onClose()
  }

  const createConversation = () => {
    onNewConversation()
    onClose()
  }

  const createAgentTask = () => {
    onNewAgentTask()
    onClose()
  }

  return (
    <>
      {mobileOpen && (
        <button
          aria-label="关闭导航"
          className="fixed inset-y-0 left-[280px] right-0 z-30 bg-black/20 lg:hidden"
          onClick={onClose}
        />
      )}
      <aside className={cn(
        'fixed inset-y-0 left-0 z-40 flex w-[280px] min-h-0 flex-col border-r border-line bg-[#fbfbfc] transition-transform duration-200 lg:static lg:z-auto lg:translate-x-0',
        mobileOpen ? 'translate-x-0' : '-translate-x-full',
      )}>
      <div className="border-b border-line px-3 py-3">
        <div className="flex items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-md bg-[#237a57] text-white shadow-sm">
            <img src={CORTEX_MARK_URL} alt="" className="h-7 w-7" />
          </div>
          <div className="min-w-0">
            <div className="truncate text-[15px] font-semibold">Cortex Agent</div>
            <div className="text-xs text-zinc-500">企业智能体工作台</div>
          </div>
          <button
            title="关闭导航"
            className="ml-auto flex h-8 w-8 items-center justify-center rounded-md text-zinc-500 hover:bg-field hover:text-ink lg:hidden"
            onClick={onClose}
          >
            <X size={16} />
          </button>
        </div>

        <div className="mt-3 grid grid-cols-2 gap-1 rounded-md bg-[#edf1ee] p-1" aria-label="工作模式">
          {([
            { value: 'agent' as const, label: 'Agent', icon: AgentMark, title: '任务执行' },
            { value: 'chat' as const, label: 'Chat', icon: MessageSquareText, title: '智能问答' },
          ]).filter((item) => user.features[item.value]).map((item) => {
            const Icon = item.icon
            const active = mode === item.value
            return (
              <button
                key={item.value}
                title={item.title}
                aria-pressed={active}
                className={cn(
                  'flex h-8 items-center justify-center gap-2 rounded text-sm font-medium transition active:scale-[0.98]',
                  active ? 'bg-[#3d735a] text-white shadow-sm' : 'text-zinc-500 hover:bg-white/70 hover:text-ink',
                )}
                onClick={() => changeMode(item.value)}
              >
                <Icon size={15} />
                {item.label}
              </button>
            )
          })}
        </div>

        <button
          className="mt-4 flex h-9 w-full items-center justify-center gap-2 rounded-md bg-ink px-3 text-sm font-medium text-white transition active:scale-[0.98]"
          onClick={() => mode === 'chat'
            ? createConversation()
            : createAgentTask()}
        >
          <Plus size={16} />
          {mode === 'agent' ? '新建任务' : '新建问答'}
        </button>
        <label className="mt-3 flex h-9 items-center gap-2 rounded-md border border-line bg-panel px-3">
          <Search size={15} className="text-zinc-400" />
          <input
            className="min-w-0 flex-1 bg-transparent text-sm outline-none"
            placeholder={mode === 'agent' ? '搜索任务' : '搜索问答'}
            value={searchQuery}
            onChange={(event) => setSearchQuery(event.target.value)}
          />
        </label>
      </div>

      <div className="thin-scrollbar min-h-0 flex-1 overflow-y-auto px-3 py-3">
        {mode === 'agent' ? (
          <NavGroup title="最近任务">
            <div className="space-y-1">
              {visibleSessions.map((session) => (
                <button
                  key={session.id}
                  className={cn(
                    'w-full rounded-md px-2.5 py-2 text-left transition hover:bg-field',
                    activeTab?.type === 'agent' && activeTab.refId === session.id && 'bg-field',
                  )}
                  onClick={() => openTab('agent', session.id, session.title)}
                >
                  <div className="flex items-center gap-2">
                    <span className={cn('h-2 w-2 shrink-0 rounded-full status-dot', session.status === 'running' || session.status === 'planning' ? 'bg-success text-success' : session.status === 'awaiting_approval' ? 'bg-caution text-caution' : 'bg-zinc-400 text-zinc-400')} />
                    <span className="min-w-0 flex-1 truncate text-sm font-medium">{session.title}</span>
                  </div>
                  <div className="mt-1 flex items-center justify-between pl-4 text-[11px] text-zinc-500">
                    <span>{session.space}</span>
                    <span>{statusText(session.status)} · {session.updatedAt}</span>
                  </div>
                </button>
              ))}
              {visibleSessions.length === 0 && (
                <div className="border-y border-line px-2.5 py-3 text-xs text-zinc-500">
                  暂无任务
                </div>
              )}
            </div>
          </NavGroup>
        ) : (
          <>
            {pinnedConversations.length > 0 && (
              <ConversationGroup title="置顶问答" items={pinnedConversations} activeTab={activeTab} onOpenTab={openTab} />
            )}
            {conversationGroups.map((group) => group.items.length > 0 && (
              <ConversationGroup key={group.title} title={group.title} items={group.items} activeTab={activeTab} onOpenTab={openTab} />
            ))}
          </>
        )}

        {(user.features.knowledge || user.features.memory) && (
          <NavGroup title="业务资源">
            {user.features.knowledge && (
              <NavButton active={activeTab?.type === 'knowledgeBase' || activeTab?.type === 'knowledgeBaseDetail' || activeTab?.type === 'document'} icon={Database} label="知识库" onClick={() => openTab('knowledgeBase', 'main', '知识库')} />
            )}
            {user.features.memory && (
              <NavButton active={activeTab?.type === 'memory'} icon={Brain} label="记忆中心" onClick={() => openTab('memory', 'main', '记忆中心')} />
            )}
          </NavGroup>
        )}

        <NavGroup title="系统管理">
          {user.role === 'superadmin' && (
            <NavButton active={activeTab?.type === 'users'} icon={Users} label="用户与权限" onClick={() => openTab('users', 'main', '用户与权限')} />
          )}
          <NavButton active={activeTab?.type === 'security'} icon={ShieldCheck} label="能力与安全" onClick={() => openTab('security', 'main', '能力与安全')} />
          {(user.role === 'admin' || user.role === 'superadmin') && (
            <NavButton active={activeTab?.type === 'audit'} icon={ClipboardList} label="审计中心" onClick={() => openTab('audit', 'main', '审计中心')} />
          )}
        </NavGroup>

      </div>
      </aside>
    </>
  )
}

function ConversationGroup({
  title,
  items,
  activeTab,
  onOpenTab,
}: {
  title: string
  items: ConversationSummary[]
  activeTab: WorkTab | null
  onOpenTab: (type: TabType, refId: string, title: string) => void
}) {
  return (
    <NavGroup title={title}>
      <div className="space-y-1 border-l border-[#dce7df] pl-1.5">
        {items.map((conversation) => {
          const active = activeTab?.type === 'chat' && activeTab.refId === conversation.id
          return (
            <button
              key={conversation.id}
              className={cn(
                'group flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm transition active:scale-[0.98]',
                active ? 'bg-[#e0ece4] font-medium text-[#28513d]' : 'text-zinc-700 hover:bg-field',
              )}
              onClick={() => onOpenTab('chat', conversation.id, conversation.title)}
            >
              <MessageSquareText size={14} className={cn('shrink-0', active ? 'text-[#3d735a]' : 'text-zinc-400')} />
              <span className="min-w-0 flex-1 truncate">{conversation.title}</span>
              <span className="hidden shrink-0 text-[10px] text-zinc-400 group-hover:inline">{conversation.updatedAt}</span>
            </button>
          )
        })}
      </div>
    </NavGroup>
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

function TopBar({
  spaces,
  user,
  onOpenNavigation,
  onLogout,
}: {
  spaces: KnowledgeSpace[]
  user: AuthUser
  onOpenNavigation: () => void
  onLogout: () => void
}) {
  const [selectedSpace, setSelectedSpace] = useAtom(selectedSpaceAtom)
  const [petVisible, setPetVisible] = useAtom(petVisibleAtom)
  const activeSpace = spaces.find((space) => space.id === selectedSpace) ?? spaces[0]

  return (
    <header className="flex h-14 items-center justify-between border-b border-line bg-panel px-3 sm:px-4">
      <div className="flex min-w-0 items-center gap-2 sm:gap-3">
        <button
          title="打开导航"
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-line text-zinc-600 hover:bg-field lg:hidden"
          onClick={onOpenNavigation}
        >
          <Menu size={17} />
        </button>
        <button className="flex h-9 min-w-0 items-center gap-2 rounded-md border border-line bg-panel px-2.5 text-sm hover:bg-field sm:px-3">
          <Archive size={15} />
          <span className="truncate">{activeSpace?.name ?? '业务空间'}</span>
          <ChevronDown size={14} className="text-zinc-400" />
        </button>
        <div className="hidden items-center gap-1.5 text-xs text-zinc-500 lg:flex">
          <span className="h-2 w-2 rounded-full bg-success" />
          <span>Chat 服务已连接</span>
        </div>
      </div>
      <div className="ml-2 flex shrink-0 items-center gap-2">
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
          {user.username}
        </div>
        <button
          title={petVisible ? '隐藏小猫' : '显示小猫'}
          className={cn(
            'flex h-8 w-8 items-center justify-center rounded-md border border-line hover:bg-field',
            petVisible ? 'text-ink' : 'text-zinc-400',
          )}
          onClick={() => setPetVisible(!petVisible)}
        >
          <Cat size={15} />
        </button>
        <button
          title="退出登录"
          className="flex h-8 w-8 items-center justify-center rounded-md border border-line text-zinc-500 hover:bg-field hover:text-ink"
          onClick={onLogout}
        >
          <LogOut size={14} />
        </button>
      </div>
    </header>
  )
}

/** 标签上的迷你宠物：有活跃/结果状态时替换默认类型图标（垫纸色小片） */
function TabPet({ type, refId, fallback }: { type: TabType; refId: string; fallback: React.ReactNode }) {
  if (type === 'chat') return <ChatTabPet sessionId={refId} fallback={fallback} />
  if (type === 'agent') return <AgentTabPet taskId={refId} fallback={fallback} />
  return <>{fallback}</>
}

function TabPetChip({ state, fallback }: { state: PetState; fallback: React.ReactNode }) {
  if (state === 'idle') return <>{fallback}</>
  return (
    <span className="flex shrink-0 items-center rounded-md border border-[#e9e2d0] bg-[#faf7ef] p-px">
      <PetAvatar state={state} size={18} />
    </span>
  )
}

function ChatTabPet({ sessionId, fallback }: { sessionId: string; fallback: React.ReactNode }) {
  const run = useChatRun(sessionId)
  return <TabPetChip state={petStateFromChatRun(run)} fallback={fallback} />
}

function AgentTabPet({ taskId, fallback }: { taskId: string; fallback: React.ReactNode }) {
  const run = useAgentRun(taskId)
  return <TabPetChip state={petStateFromAgentRun(run)} fallback={fallback} />
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
            <TabPet type={tab.type} refId={tab.refId} fallback={<Icon size={14} />} />
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

function TabContent({
  tab,
  user,
  onOpenTab,
  onConversationUpdated,
  onConversationArchived,
  onCreateAgentTask,
  onTaskDeleted,
}: {
  tab: WorkTab
  user: AuthUser
  onOpenTab: (type: TabType, refId: string, title: string) => void
  onConversationUpdated: (sessionId: string, title: string) => void
  onConversationArchived: (sessionId: string) => void
  onCreateAgentTask: (title?: string) => void
  onTaskDeleted: (taskId: string) => void
}) {
  const isAdmin = user.role === 'admin' || user.role === 'superadmin'
  if (tab.type === 'agent') {
    if (!user.features.agent) return <NoAccess feature="Agent 任务" />
    return <AgentView key={tab.refId} taskId={tab.refId} title={tab.title} onDeleted={onTaskDeleted} onOpenTab={onOpenTab} />
  }
  if (tab.type === 'chat') {
    if (!user.features.chat) return <NoAccess feature="Chat 问数" />
    return (
      <ChatView
        key={tab.refId}
        sessionId={tab.refId}
        title={tab.title}
        knowledgeEnabled={user.features.knowledge}
        onOpenTab={onOpenTab}
        onConversationUpdated={onConversationUpdated}
        onConversationArchived={onConversationArchived}
        onPromote={() => onCreateAgentTask(`${tab.title} · 执行`)}
      />
    )
  }
  if (tab.type === 'knowledgeBase') {
    if (!user.features.knowledge) return <NoAccess feature="知识库" />
    return <KnowledgeBaseListView isAdmin={isAdmin} onOpenTab={onOpenTab} />
  }
  if (tab.type === 'knowledgeBaseDetail') {
    if (!user.features.knowledge) return <NoAccess feature="知识库" />
    return <KnowledgeBaseView kbId={tab.refId} isAdmin={isAdmin} onOpenTab={onOpenTab} />
  }
  if (tab.type === 'document') {
    if (!user.features.knowledge) return <NoAccess feature="知识库" />
    return <DocumentDetailView documentId={tab.refId} isAdmin={isAdmin} onOpenTab={onOpenTab} />
  }
  if (tab.type === 'memory') {
    if (!user.features.memory) return <NoAccess feature="记忆中心" />
    return <MemoryView />
  }
  if (tab.type === 'users') {
    if (user.role !== 'superadmin') return <NoAccess feature="用户与权限" />
    return <UserAdminView currentUser={user} />
  }
  if (tab.type === 'security') return <SecurityView />
  if (tab.type === 'audit') {
    if (!isAdmin) return <NoAccess feature="审计中心" />
    return <AuditView />
  }
  return null
}

function NoAccess({ feature }: { feature: string }) {
  return (
    <div className="flex h-full items-center justify-center">
      <div className="text-center">
        <div className="text-base font-medium">无权访问{feature}</div>
        <div className="mt-1 text-sm text-zinc-500">该功能未对你的账号开放，请联系超级管理员。</div>
      </div>
    </div>
  )
}

function EmptyWorkspace({ onNewTask }: { onNewTask: () => void }) {
  return (
    <div className="flex h-full items-center justify-center">
      <div className="text-center">
        <PetAvatar state="idle" size={104} className="mx-auto mb-3" />
        <div className="text-base font-medium">没有打开的标签</div>
        <button className="mt-4 rounded-md bg-ink px-4 py-2 text-sm text-white" onClick={onNewTask}>新建任务</button>
      </div>
    </div>
  )
}

function ChatView({
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
  const [files, setFiles] = useAtom(chatAttachedFilesAtom)
  const [knowledgeQaEnabled] = useAtom(knowledgeQaEnabledAtom)
  const [knowledgeQaKbId] = useAtom(knowledgeQaKbIdAtom)
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
        knowledgeQa: knowledgeQaActive ? { kbId: knowledgeQaKbId } : null,
      })
      setDraft('')
      setActionError(null)
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '无法启动 Chat 服务')
    }
  }, [chatRunManager, draft, isRunning, knowledgeQaActive, knowledgeQaKbId, sessionId])

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

  const addFiles = (selected: FileList | null) => {
    if (!selected?.length) return
    const next = Array.from(selected).map((file) => ({
      id: `chat-file-${Date.now()}-${file.name}`,
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
              <span className="ml-1 hidden truncate text-xs text-zinc-500 sm:inline">{files.length} 个问答文件</span>
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
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && !actionPending) setDeleteConfirmOpen(false)
          }}
        >
          <div
            className="w-full max-w-sm rounded-md border border-line bg-panel p-5 shadow-card"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="delete-conversation-title"
          >
            <div className="flex items-start gap-3">
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-red-50 text-danger">
                <Trash2 size={17} />
              </div>
              <div>
                <div id="delete-conversation-title" className="text-sm font-semibold">永久删除问答</div>
                <p className="mt-1 text-sm leading-6 text-zinc-500">
                  对话消息和模型运行记录将被删除，企业审计记录仍会保留。
                </p>
              </div>
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button
                className="h-9 rounded-md border border-line px-3 text-sm hover:bg-field disabled:text-zinc-400"
                disabled={actionPending}
                onClick={() => setDeleteConfirmOpen(false)}
              >
                取消
              </button>
              <button
                className="flex h-9 items-center gap-2 rounded-md bg-danger px-3 text-sm font-medium text-white disabled:bg-zinc-300"
                disabled={actionPending}
                onClick={() => void deleteConversation()}
              >
                {actionPending ? <LoaderCircle size={15} className="animate-spin" /> : <Trash2 size={15} />}
                删除
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function AgentView({ taskId, title, onDeleted, onOpenTab }: { taskId: string; title: string; onDeleted: (taskId: string) => void; onOpenTab: (type: TabType, refId: string, title: string) => void }) {
  const [files, setFiles] = useAtom(attachedFilesAtom)
  const queryClient = useQueryClient()
  const agentRunManager = useAgentRunManager()
  const run = useAgentRun(taskId)
  const query = useQuery({
    queryKey: ['task', taskId],
    queryFn: () => api.getTask(taskId),
    refetchInterval: (result) => result.state.data?.activeRun ? 1000 : false,
  })
  const [draft, setDraft] = React.useState('')
  const [actionPending, setActionPending] = React.useState(false)
  const [actionError, setActionError] = React.useState<string | null>(null)
  const fileInputRef = React.useRef<HTMLInputElement | null>(null)
  const scrollRef = React.useRef<HTMLDivElement | null>(null)
  const restoredScrollRef = React.useRef(false)

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

  React.useLayoutEffect(() => {
    const element = scrollRef.current
    if (!element || restoredScrollRef.current || !task) return
    restoredScrollRef.current = true
    const saved = agentRunManager.getScrollPosition(taskId)
    element.scrollTop = active ? element.scrollHeight : Math.min(saved ?? element.scrollHeight, element.scrollHeight)
  }, [active, agentRunManager, task, taskId])

  React.useEffect(() => {
    restoredScrollRef.current = false
    return () => {
      const element = scrollRef.current
      if (element) agentRunManager.setScrollPosition(taskId, element.scrollTop)
    }
  }, [agentRunManager, taskId])

  React.useLayoutEffect(() => {
    const element = scrollRef.current
    if (element && active) element.scrollTop = element.scrollHeight
  }, [active, messages.length, run?.assistantMessage.content, toolEvents.length, pendingApprovals.length])

  const sendPlanRequest = React.useCallback(() => {
    const text = draft.trim()
    if (!text || !task || !canPlan) return
    setDraft('')
    setActionError(null)
    try {
      agentRunManager.startPlan(task, text)
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '无法启动规划')
    }
  }, [agentRunManager, canPlan, draft, task])

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
      agentRunManager.startExecute(task)
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '无法启动执行')
    }
  }, [active, agentRunManager, task])

  const stopTask = React.useCallback(() => {
    void agentRunManager.cancel(taskId).catch((error) => {
      setActionError(error instanceof Error ? error.message : '停止任务失败')
    })
  }, [agentRunManager, taskId])

  const deleteTask = React.useCallback(async () => {
    if (active || !window.confirm('确认删除该任务及其对话、计划和工具记录？')) return
    setActionPending(true)
    setActionError(null)
    try {
      await api.deleteTask(taskId)
      onDeleted(taskId)
      void queryClient.invalidateQueries({ queryKey: ['tasks'] })
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '删除任务失败')
    } finally {
      setActionPending(false)
    }
  }, [active, onDeleted, queryClient, taskId])

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
            onClick={() => void deleteTask()}
          >
            <Trash2 size={15} />
          </button>
        </div>
      </div>

      <div
        ref={scrollRef}
        className="thin-scrollbar min-h-0 flex-1 overflow-y-auto px-8 py-6"
        onScroll={(event) => {
          if (!active) agentRunManager.setScrollPosition(taskId, event.currentTarget.scrollTop)
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
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            className="block min-h-[82px] w-full resize-none bg-transparent px-4 py-3 text-sm outline-none"
            placeholder={approvedPlan ? '计划已批准，可在上方调整权限后执行' : '输入任务目标，Agent 将先生成执行计划'}
            disabled={!canPlan}
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
                active || (canPlan && draft.trim()) ? 'bg-ink text-white' : 'bg-zinc-200 text-zinc-400',
              )}
              disabled={!active && (!canPlan || !draft.trim())}
              onClick={active ? stopTask : sendPlanRequest}
            >
              {active ? <CircleStop size={15} /> : <Send size={15} />}
              {active ? '停止' : '生成计划'}
            </button>
          </div>
          </div>
        </div>
      </div>
    </div>
  )
}

const MessageBubble = React.memo(function MessageBubble({
  message,
  onOpenTab,
}: {
  message: ChatMessage
  /** 引用卡片点击跳转文档详情；不传（无 knowledge feature）时卡片降级为纯文本 */
  onOpenTab?: (type: TabType, refId: string, title: string) => void
}) {
  const assistant = message.role === 'assistant'
  const system = message.role === 'system'
  const thinking = assistant && message.status === 'streaming'
  const completedSeconds = message.durationMs === undefined
    ? null
    : Math.max(1, Math.ceil(message.durationMs / 1000))

  if (thinking && !message.content) {
    return <ThinkingBubble message={message} />
  }

  return (
    <div className={cn('flex gap-3', !assistant && !system && 'justify-end')}>
      {(assistant || system) && (
        <div className={cn('flex h-8 w-8 shrink-0 items-center justify-center rounded-md', system ? 'bg-amber-50 text-caution' : 'bg-[#237a57] text-white')}>
          {system ? <ShieldCheck size={16} /> : <img src={CORTEX_MARK_URL} alt="" className="h-7 w-7" />}
        </div>
      )}
      <div className={cn('max-w-[88%] rounded-md border px-3 py-3 text-sm leading-6 sm:max-w-[78%] sm:px-4', assistant || system ? 'border-line bg-panel' : 'border-ink bg-ink text-white')}>
        {assistant || system ? (
          <Markdown content={message.content || '...'} />
        ) : (
          <div className="whitespace-pre-wrap break-words">{message.content || '...'}</div>
        )}
        {assistant && !system && message.citations && message.citations.length > 0 && (
          <CitationCards citations={message.citations} onOpenTab={onOpenTab} />
        )}
        <div className={cn('mt-2 text-[11px]', assistant || system ? 'text-zinc-400' : 'text-white/60')}>
          {thinking && message.thinkingStartedAt ? (
            <ElapsedThinkingTime startedAt={message.thinkingStartedAt} />
          ) : (
            <>
              {assistant && completedSeconds !== null ? `思考了 ${completedSeconds} 秒${message.createdAt ? ' · ' : ''}` : ''}
              {message.createdAt}
            </>
          )}
        </div>
      </div>
    </div>
  )
})

/** 知识库问答的"参考来源"卡片列表：文档名 + 分块标题 + 摘要 + 序号 badge。 */
function CitationCards({
  citations,
  onOpenTab,
}: {
  citations: KnowledgeCitation[]
  onOpenTab?: (type: TabType, refId: string, title: string) => void
}) {
  return (
    <div className="mt-3 border-t border-line pt-2">
      <div className="mb-1.5 text-[11px] font-medium text-zinc-400">参考来源</div>
      <div className="space-y-1.5">
        {citations.map((citation, index) => {
          const clickable = Boolean(onOpenTab && citation.docId)
          const body = (
            <div className="flex items-start gap-2">
              <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-sm bg-[#e4f3ec] text-[10px] font-semibold text-[#237a57]">
                {citation.num ?? index + 1}
              </span>
              <span className="min-w-0">
                <span className="block truncate text-xs font-medium text-ink">
                  {citation.docName || '未命名文档'}
                  {citation.chunkTitle && <span className="font-normal text-zinc-500"> · {citation.chunkTitle}</span>}
                </span>
                {citation.snippet && (
                  <span className="mt-0.5 line-clamp-2 block text-xs leading-5 text-zinc-500">{citation.snippet}</span>
                )}
              </span>
            </div>
          )
          const className = cn(
            'w-full rounded-md border border-line bg-[#fcfcfd] px-2.5 py-2 text-left',
            clickable && 'transition hover:border-[#237a57]/40 hover:bg-[#f2f9f6]',
          )
          return clickable ? (
            <button
              key={citation.chunkId || index}
              type="button"
              className={className}
              title="查看文档详情"
              onClick={() => onOpenTab?.('document', citation.docId, citation.docName || '文档详情')}
            >
              {body}
            </button>
          ) : (
            <div key={citation.chunkId || index} className={className}>{body}</div>
          )
        })}
      </div>
    </div>
  )
}

/** Chat 输入框的知识库问答开关 + 选库下拉（全部知识库 / 指定库）。 */
function KnowledgeQaPicker({ bases }: { bases: KnowledgeBase[] }) {
  const [enabled, setEnabled] = useAtom(knowledgeQaEnabledAtom)
  const [kbId, setKbId] = useAtom(knowledgeQaKbIdAtom)
  const [open, setOpen] = React.useState(false)
  const rootRef = React.useRef<HTMLDivElement | null>(null)

  React.useEffect(() => {
    if (!open) return
    const onPointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    return () => document.removeEventListener('mousedown', onPointerDown)
  }, [open])

  // 库被删除后本地残留的 kbId 自动回退为"全部知识库"
  const selectedBase = kbId ? bases.find((base) => base.id === kbId) : null
  React.useEffect(() => {
    if (kbId && bases.length > 0 && !selectedBase) setKbId(null)
  }, [bases.length, kbId, selectedBase, setKbId])
  const selectedName = selectedBase?.name ?? '全部知识库'

  const itemClass = 'flex h-9 w-full items-center gap-2 rounded px-2.5 text-left text-sm hover:bg-field'
  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        title="知识库问答"
        className={cn(
          'flex h-8 items-center gap-1.5 rounded-md px-2 text-xs transition',
          enabled ? 'bg-[#e4f3ec] font-medium text-[#237a57]' : 'text-zinc-500 hover:bg-field hover:text-ink',
        )}
        onClick={() => setOpen((current) => !current)}
      >
        <Database size={16} />
        {enabled && <span className="max-w-32 truncate">{selectedName}</span>}
        <ChevronDown size={12} className={cn('transition', open && 'rotate-180')} />
      </button>
      {open && (
        <div className="absolute bottom-10 left-0 z-20 w-60 rounded-md border border-line bg-panel p-1 shadow-card">
          <button type="button" className={itemClass} onClick={() => setEnabled((current) => !current)}>
            <span className={cn(
              'flex h-4 w-4 shrink-0 items-center justify-center rounded-sm border',
              enabled ? 'border-[#237a57] bg-[#237a57] text-white' : 'border-line bg-panel',
            )}>
              {enabled && <Check size={12} />}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-sm">知识库问答</span>
              <span className="block text-[11px] text-zinc-400">严格基于知识库回答并标注来源</span>
            </span>
          </button>
          {enabled && (
            <>
              <div className="my-1 border-t border-line" />
              <div className="px-2.5 pb-1 pt-0.5 text-[11px] text-zinc-400">检索范围</div>
              <button
                type="button"
                className={cn(itemClass, kbId === null && 'bg-field font-medium')}
                onClick={() => { setKbId(null); setOpen(false) }}
              >
                <Database size={14} className="shrink-0 text-zinc-400" />
                全部知识库
              </button>
              {bases.map((base) => (
                <button
                  key={base.id}
                  type="button"
                  className={cn(itemClass, kbId === base.id && 'bg-field font-medium')}
                  onClick={() => { setKbId(base.id); setOpen(false) }}
                >
                  <FileText size={14} className="shrink-0 text-zinc-400" />
                  <span className="truncate">{base.name}</span>
                </button>
              ))}
              {bases.length === 0 && (
                <div className="px-2.5 py-1.5 text-xs text-zinc-400">还没有知识库，请先在知识库页面创建</div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}

function ThinkingBubble({ message }: { message: ChatMessage }) {
  const displayTime = message.createdAt || new Date(
    message.thinkingStartedAt ?? Date.now(),
  ).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })

  return (
    <div className="flex items-start gap-3" role="status" aria-live="polite">
      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-[#237a57] text-white shadow-sm">
        <img src={CORTEX_MARK_URL} alt="" className="h-8 w-8" />
      </div>
      <div className="min-w-0 pt-0.5">
        <div className="flex min-h-9 flex-wrap items-center gap-2">
          <span className="text-sm font-medium text-zinc-500">Cortex · {displayTime}</span>
          <span className="inline-flex h-8 items-center gap-2 rounded-full bg-[#e4f3ec] px-3 text-sm font-medium text-[#237a57]">
            <span className="h-2 w-2 animate-pulse rounded-full bg-[#82bda5]" />
            正在生成
          </span>
        </div>
        <div className="mt-2 flex h-5 items-center gap-1.5 text-xs text-zinc-400">
          <span className="h-2 w-2 animate-bounce rounded-full bg-[#b7d8ca]" style={{ animationDelay: '-320ms' }} />
          <span className="h-2 w-2 animate-bounce rounded-full bg-[#5aa083]" style={{ animationDelay: '-160ms' }} />
          <span className="h-2 w-2 animate-bounce rounded-full bg-[#237a57]" />
          {message.thinkingStartedAt && (
            <span className="ml-1"><ElapsedThinkingTime startedAt={message.thinkingStartedAt} /></span>
          )}
        </div>
      </div>
    </div>
  )
}

function ElapsedThinkingTime({ startedAt }: { startedAt: number }) {
  const [now, setNow] = React.useState(Date.now())

  React.useEffect(() => {
    setNow(Date.now())
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [startedAt])

  const elapsed = Math.max(0, Math.floor((now - startedAt) / 1000))
  return <span>已思考 {elapsed} 秒</span>
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

function TaskPlanPanel({
  status,
  plan,
  permissionMode,
  pending,
  onApprove,
  onExecute,
  onModeChange,
}: {
  status: AgentTaskStatus
  plan: TaskPlan | null
  permissionMode: PermissionMode
  pending: boolean
  onApprove: () => void
  onExecute: () => void
  onModeChange: (mode: PermissionMode) => void
}) {
  if (!plan && status === 'draft') return null
  const awaitingApproval = plan?.status === 'pending' && status === 'awaiting_approval'
  const executable = plan?.status === 'approved' && ['ready', 'failed', 'cancelled'].includes(status)
  return (
    <div className="border-y border-line bg-[#fcfcfd] px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <div className={cn(
            'flex h-9 w-9 shrink-0 items-center justify-center rounded-md',
            awaitingApproval ? 'bg-amber-50 text-caution' : 'bg-emerald-50 text-success',
          )}>
            {awaitingApproval ? <AlertTriangle size={18} /> : <FileCheck2 size={18} />}
          </div>
          <div className="min-w-0">
            <div className="text-sm font-semibold">
              {status === 'planning' ? '正在生成执行计划' : awaitingApproval ? `执行计划 v${plan?.version}` : status === 'completed' ? '任务已完成' : '已批准执行计划'}
            </div>
            <div className="text-xs text-zinc-500">
              {awaitingApproval
                ? '审批后才可执行；涉及写入或终端操作时还需切换到完全访问。'
                : permissionMode === 'full'
                  ? '完全访问将在本次执行结束后自动恢复为只读。'
                  : '当前只会启用与权限等级匹配的工具。'}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {awaitingApproval && (
            <button
              className="flex h-8 items-center gap-1.5 rounded-md bg-ink px-3 text-xs font-medium text-white disabled:bg-zinc-300"
              disabled={pending}
              onClick={onApprove}
            >
              {pending ? <LoaderCircle size={14} className="animate-spin" /> : <FileCheck2 size={14} />}
              批准计划
            </button>
          )}
          {executable && permissionMode !== 'full' && (
            <button
              className="flex h-8 items-center gap-1.5 rounded-md border border-line px-3 text-xs hover:bg-field disabled:text-zinc-400"
              disabled={pending}
              onClick={() => onModeChange('full')}
            >
              <KeyRound size={14} />
              完全访问
            </button>
          )}
          {executable && (
            <button
              className="flex h-8 items-center gap-1.5 rounded-md bg-ink px-3 text-xs font-medium text-white disabled:bg-zinc-300"
              disabled={pending}
              onClick={onExecute}
            >
              <Play size={14} />
              {status === 'failed' || status === 'cancelled' ? '重新执行' : '执行'}
            </button>
          )}
        </div>
      </div>
      {plan?.content && (
        <details className="mt-3 border-t border-line pt-3" open={awaitingApproval}>
          <summary className="cursor-pointer text-xs font-medium text-zinc-600">查看计划内容</summary>
          <Markdown content={plan.content} className="mt-3 max-h-64 overflow-y-auto text-zinc-700" />
        </details>
      )}
    </div>
  )
}

function ToolEventTimeline({ events, activeRunId }: { events: ToolEvent[]; activeRunId: string | null }) {
  const auditableEvents = events.filter((event) => !event.toolName?.startsWith('_'))
  const visible = activeRunId
    ? auditableEvents.filter((event) => event.runId === activeRunId)
    : auditableEvents.slice(-6)
  if (visible.length === 0) return null
  return (
    <section className="border-y border-line py-3" aria-live="polite">
      <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-zinc-500">
        <History size={14} />
        工具活动
      </div>
      <div className="space-y-1.5">
        {visible.slice(-8).map((event) => {
          const failed = event.status === 'failed'
          const completed = event.eventType === 'tool.completed'
          return (
            <div key={`${event.runId}-${event.sequence}-${event.eventType}`} className="flex items-center gap-2 text-xs text-zinc-600">
              <span className={cn(
                'h-1.5 w-1.5 rounded-full',
                failed ? 'bg-danger' : completed ? 'bg-success' : 'animate-pulse bg-caution',
              )} />
              <span className="font-medium">{event.toolName ?? 'Agent tool'}</span>
              <span className="text-zinc-400">{failed ? '执行失败' : completed ? '已完成' : '执行中'}</span>
            </div>
          )
        })}
      </div>
    </section>
  )
}

function ToolApprovalPanel({
  approvals,
  onDecide,
}: {
  approvals: ToolApproval[]
  onDecide: (approvalId: string, decision: 'allow' | 'deny' | 'allow_all') => void
}) {
  return (
    <div className="space-y-2">
      {approvals.map((approval) => (
        <div key={approval.id} className="rounded-md border border-amber-300 bg-amber-50 p-3">
          <div className="flex items-center gap-2 text-sm font-medium text-ink">
            <SquareTerminal size={14} className="text-amber-600" />
            <span>等待批准执行命令</span>
            <span className="rounded bg-panel px-1.5 py-0.5 text-xs text-zinc-500">{approval.toolName}</span>
          </div>
          <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-all rounded-md border border-line bg-panel p-2 font-mono text-xs text-ink">
            {approval.commandPreview || '(无命令预览)'}
          </pre>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <button
              className="h-7 rounded-md bg-ink px-3 text-xs text-white hover:opacity-90"
              onClick={() => onDecide(approval.id, 'allow')}
            >
              允许
            </button>
            <button
              className="h-7 rounded-md border border-line px-3 text-xs text-danger hover:bg-red-50"
              onClick={() => onDecide(approval.id, 'deny')}
            >
              拒绝
            </button>
            <button
              className="h-7 rounded-md border border-line px-3 text-xs text-zinc-600 hover:bg-field"
              onClick={() => onDecide(approval.id, 'allow_all')}
            >
              本次运行全部允许
            </button>
          </div>
        </div>
      ))}
    </div>
  )
}

function PermissionSegment({ value, onChange, compact = false, disabled = false }: { value: PermissionMode; onChange: (mode: PermissionMode) => void; compact?: boolean; disabled?: boolean }) {
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
            title={item.label}
            className={cn(
              'flex items-center gap-1.5 rounded text-xs transition',
              compact ? 'h-8 w-8 justify-center px-0 sm:w-auto sm:px-2' : 'h-7 px-2',
              value === item.value ? 'bg-panel text-ink shadow-sm' : 'text-zinc-500 hover:text-ink',
              compact && item.value === 'controlled' && 'hidden xl:flex',
              disabled && 'cursor-not-allowed opacity-50',
            )}
            disabled={disabled}
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

function SecurityView() {
  const query = useQuery({ queryKey: ['features'], queryFn: api.getFeatures })
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
            <PolicyRow icon={FileCheck2} title="受控写入" text="终端命令需逐条经你批准后执行" />
            <PolicyRow icon={KeyRound} title="完全访问" text="共享知识库修改、数据库写入、终端命令" />
          </PlainPanel>
          {features?.dataPermissions.enabled && (
            <PlainPanel title="数据权限">
              <InfoRow
                label="可访问的表"
                value={
                  features.dataPermissions.allowedTables && features.dataPermissions.allowedTables.length > 0
                    ? features.dataPermissions.allowedTables.join('、')
                    : '无（全部禁止）'
                }
              />
              <div className="mt-2 text-xs text-zinc-500">
                当前角色只能查询以上业务表；越权 SQL 会被拦截并记入审计。
              </div>
            </PlainPanel>
          )}
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
  const eventsQuery = useQuery({
    queryKey: ['auditEvents'],
    queryFn: () => api.listAuditEvents(),
    refetchInterval: 15000,
    retry: false,
  })
  const events = eventsQuery.data ?? []
  const forbidden = eventsQuery.error instanceof ApiError && eventsQuery.error.status === 403

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader icon={ClipboardList} title="审计中心" subtitle="会话、工具、权限切换与高风险动作" />
      <section className="thin-scrollbar min-h-0 flex-1 overflow-y-auto p-6">
        {forbidden ? (
          <div className="py-10 text-center text-sm text-zinc-500">审计记录仅管理员可见</div>
        ) : eventsQuery.isError ? (
          <div className="py-10 text-center text-sm text-zinc-500">审计记录加载失败，请稍后重试</div>
        ) : eventsQuery.isLoading ? (
          <div className="py-10 text-center text-sm text-zinc-500">加载中…</div>
        ) : events.length === 0 ? (
          <div className="py-10 text-center text-sm text-zinc-500">暂无审计记录</div>
        ) : (
          <div className="border-y border-line">
            {events.map((event) => (
              <div key={event.id} className="grid grid-cols-[90px_140px_110px_minmax(0,1fr)_110px] gap-4 border-b border-line px-2 py-3 text-sm last:border-0">
                <span className="text-zinc-500">{event.time}</span>
                <span className="truncate font-mono text-xs text-zinc-500">{event.eventType}</span>
                <span className="truncate text-zinc-600">{event.username || '—'}</span>
                <span className="truncate" title={event.subject}>{event.subject}</span>
                <Badge className={
                  event.status === 'completed' ? 'bg-emerald-50 text-success'
                    : event.status === 'blocked' ? 'bg-red-50 text-danger'
                      : event.status === 'failed' ? 'bg-amber-50 text-caution'
                        : 'bg-zinc-100 text-zinc-600'
                }>{event.status}</Badge>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}

function RightPanel({ activeTab }: { activeTab: WorkTab | null }) {
  const [files] = useAtom(attachedFilesAtom)
  const queryClient = useQueryClient()
  const taskId = activeTab?.type === 'agent' ? activeTab.refId : ''
  const run = useAgentRun(taskId)
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

  if (activeTab?.type === 'chat') {
    return <ChatRightPanel referencedDocs={referencedDocs} />
  }

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
          <PermissionSegment value={permissionMode} onChange={setPermissionMode} disabled={!task || active} />
          {permissionMode === 'full' && (
            <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-caution">
              完全访问仅当前任务生效，任务结束后自动降权。
            </div>
          )}
        </PanelSection>

        <PanelSection title="本次任务文件" icon={FileArchive}>
          <div className="divide-y divide-line border-y border-line">
            {files.length === 0 && (
              <div className="py-3 text-xs text-zinc-500">暂无任务文件</div>
            )}
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

function ChatRightPanel({ referencedDocs }: { referencedDocs: KnowledgeDocument[] }) {
  const [files] = useAtom(chatAttachedFilesAtom)
  const [selectedSpace] = useAtom(selectedSpaceAtom)
  const spacesQuery = useQuery({ queryKey: ['spaces'], queryFn: mockApi.listSpaces })
  const activeSpace = (spacesQuery.data ?? []).find((space) => space.id === selectedSpace)

  return (
    <aside className="hidden min-h-0 flex-col bg-[#fbfbfc] xl:flex">
      <div className="flex h-14 items-center justify-between border-b border-line px-4">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <PanelRight size={16} />
          问答上下文
        </div>
        <Badge className="bg-[#e0ece4] text-[#28513d]">Chat</Badge>
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
            <InfoRow label="业务空间" value={activeSpace?.name ?? '轨道公司'} />
            <InfoRow label="回答方式" value="知识库增强" />
          </div>
        </PanelSection>

        <PanelSection title="问答附件" icon={FileArchive}>
          {files.length === 0 ? (
            <div className="border-y border-line py-3 text-xs text-zinc-500">暂无附件</div>
          ) : (
            <div className="divide-y divide-line border-y border-line">
              {files.map((file) => (
                <div key={file.id} className="py-2.5 text-sm">
                  <div className="flex min-w-0 items-center gap-2">
                    <FileText size={15} className="shrink-0 text-zinc-400" />
                    <span className="truncate">{file.name}</span>
                  </div>
                  <div className="mt-1 flex items-center justify-between pl-6 text-xs text-zinc-500">
                    <span>{formatBytes(file.size)}</span>
                    <span>{file.status === 'ready' ? '可检索' : '解析中'}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </PanelSection>

        <PanelSection title="知识来源" icon={Database}>
          <div className="divide-y divide-line border-y border-line">
            {referencedDocs.length === 0 && (
              <div className="py-3 text-xs text-zinc-500">企业知识库暂无可检索文档</div>
            )}
            {referencedDocs.map((doc) => (
              <div key={doc.id} className="py-2.5 text-sm">
                <div className="truncate font-medium">{doc.title}</div>
                <div className="mt-1 flex items-center justify-between text-xs text-zinc-500">
                  <span className="truncate">{doc.fileName}</span>
                  <span className="shrink-0">{doc.chunkCount} 个片段</span>
                </div>
              </div>
            ))}
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

function PlainPanel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-y border-line py-4">
      <div className="mb-3 text-sm font-semibold">{title}</div>
      <div className="space-y-3">{children}</div>
    </section>
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
