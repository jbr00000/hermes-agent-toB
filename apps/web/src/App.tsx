import * as React from 'react'
import { useAtom } from 'jotai'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Archive,
  Brain,
  Cat,
  ChevronDown,
  ClipboardList,
  Database,
  FileText,
  Layers3,
  LockKeyhole,
  LogOut,
  Menu,
  MessageSquareText,
  Plus,
  Search,
  ShieldCheck,
  UserCog,
  Users,
  X,
  type LucideIcon,
} from 'lucide-react'
import { db } from './db'
import { api, setCurrentUserUpdater } from './api'
import {
  useAgentRun,
  useAgentRunManager,
} from './agentRunManager'
import {
  useChatRun,
  useChatRunManager,
} from './chatRunManager'
import { mockApi } from './mockApi'
import { PetAvatar } from './components/pet/PetAvatar'
import type { PetState } from './components/pet/PetAvatar'
import { petStateFromAgentRun, petStateFromChatRun } from './components/pet/petState'
import { PetFloat } from './components/pet/PetFloat'
import { cn, CORTEX_MARK_URL } from './components/ui'
import { ChatView } from './components/chat/ChatView'
import { AgentView } from './components/agent/AgentView'
import { RightPanel } from './components/RightPanel'
import { KnowledgeBaseView } from './components/knowledge/KnowledgeBaseView'
import { KnowledgeBaseListView } from './components/knowledge/KnowledgeBaseListView'
import { DocumentDetailView } from './components/knowledge/DocumentDetailView'
import { UserAdminView } from './components/users/UserAdminView'
import { MemoryView } from './views/MemoryView'
import { SecurityView } from './views/SecurityView'
import { AuditView } from './views/AuditView'
import LoginBackdrop from './components/LoginBackdrop'
import {
  activeTabIdAtom,
  attachedFilesAtom,
  chatAttachedFilesAtom,
  createTab,
  petVisibleAtom,
  selectedSpaceAtom,
  tabId,
  tabsAtom,
  workspaceModeAtom,
} from './state'
import type {
  AgentTaskDetail,
  ConversationSummary,
  AuthUser,
  KnowledgeSpace,
  SessionSummary,
  TabType,
  WorkspaceMode,
  WorkTab,
} from './types'

const MAX_TABS = 12

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
        setAttachedFiles({})
        setChatAttachedFiles({})
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

  const closeTab = React.useCallback((closedTabId: string) => {
    setTabs((current) => {
      const index = current.findIndex((tab) => tab.id === closedTabId)
      const next = current.filter((tab) => tab.id !== closedTabId).map((tab, order) => ({ ...tab, order }))
      if (activeTabId === closedTabId) {
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

  const activateTab = React.useCallback((nextTabId: string) => {
    const tab = tabs.find((item) => item.id === nextTabId)
    setActiveTabId(nextTabId)
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
          <div
            key={tab.id}
            className={cn(
              'group flex h-9 max-w-[230px] items-center gap-2 rounded-t-md border border-b-0 pr-1.5 text-sm transition',
              active ? 'border-line bg-panel text-ink' : 'border-transparent text-zinc-500 hover:bg-field hover:text-ink',
            )}
          >
            <button
              className="flex h-full min-w-0 items-center gap-2 pl-3"
              onClick={() => onActivate(tab.id)}
            >
              <TabPet type={tab.type} refId={tab.refId} fallback={<Icon size={14} />} />
              <span className="truncate">{tab.title}</span>
            </button>
            <button
              aria-label={`关闭 ${tab.title}`}
              className="shrink-0 rounded p-0.5 text-zinc-400 hover:bg-zinc-200 hover:text-ink"
              onClick={() => onClose(tab.id)}
            >
              <X size={13} />
            </button>
          </div>
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
