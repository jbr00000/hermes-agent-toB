/** 用户与权限管理：仅 superadmin 可见（入口由 Sidebar/TabContent 守卫，权限由后端强制）。
 *  建号、改角色、重置密码、启停、配置四个功能开关（agent/chat/knowledge/memory）。 */
import * as React from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { KeyRound, LoaderCircle, Pencil, Plus, Power, Trash2, Users, type LucideIcon } from 'lucide-react'
import { api, ApiError } from '../../api'
import type { AdminUserRow, AuthUser, UserFeatures, UserRole } from '../../types'
import { Badge, cn, DataTable, PageHeader, Td, Th } from '../ui'

const FEATURE_LABELS: Array<{ key: keyof UserFeatures; label: string }> = [
  { key: 'agent', label: 'Agent 任务' },
  { key: 'chat', label: 'Chat 问数' },
  { key: 'knowledge', label: '知识库' },
  { key: 'memory', label: '记忆' },
]

const ROLE_LABELS: Record<UserRole, string> = {
  superadmin: '超级管理员',
  admin: '管理员',
  user: '普通用户',
}

const ALL_FEATURES: UserFeatures = { agent: true, chat: true, knowledge: true, memory: true }

function formatDate(timestampSeconds: number): string {
  return new Date(timestampSeconds * 1000).toLocaleDateString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  })
}

function errorMessage(cause: unknown): string {
  return cause instanceof ApiError ? cause.message : '操作失败，请重试'
}

export function UserAdminView({ currentUser }: { currentUser: AuthUser }) {
  const queryClient = useQueryClient()
  const [actionError, setActionError] = React.useState<string | null>(null)
  const [createOpen, setCreateOpen] = React.useState(false)
  const [resetTarget, setResetTarget] = React.useState<AdminUserRow | null>(null)
  const [featuresTarget, setFeaturesTarget] = React.useState<AdminUserRow | null>(null)
  const query = useQuery({ queryKey: ['adminUsers'], queryFn: () => api.listUsers(), retry: false })
  const users = query.data ?? []

  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ['adminUsers'] })
  const runAction = (action: () => Promise<unknown>) => {
    setActionError(null)
    void action().then(invalidate).catch((cause: unknown) => setActionError(errorMessage(cause)))
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader icon={Users} title="用户与权限" subtitle="账号、角色与功能访问控制（仅超级管理员）" />
      <section className="thin-scrollbar min-h-0 flex-1 overflow-y-auto p-6">
        <div className="mb-4 flex items-start justify-between gap-3">
          <div className="text-xs leading-5 text-zinc-500">
            共 {users.length} 个账号。功能开关即时生效，被关闭功能的用户调用对应接口会被拒绝。
          </div>
          <button
            className="flex h-8 shrink-0 items-center gap-2 rounded-md bg-ink px-3 text-sm text-white"
            onClick={() => setCreateOpen(true)}
          >
            <Plus size={15} />
            创建用户
          </button>
        </div>
        {actionError && <div className="mb-3 text-xs text-danger">{actionError}</div>}
        {users.length === 0 && !query.isPending ? (
          <div className="border-y border-line py-8 text-center text-sm text-zinc-400">
            {query.isError ? errorMessage(query.error) : '还没有用户'}
          </div>
        ) : (
          <DataTable>
            <colgroup>
              <col className="w-[20%]" />
              <col className="w-[15%]" />
              <col className="w-[13%]" />
              <col className="w-[26%]" />
              <col className="w-[11%]" />
              <col className="w-[15%]" />
            </colgroup>
            <thead>
              <tr>
                <Th>用户</Th>
                <Th>角色</Th>
                <Th>状态</Th>
                <Th>功能权限</Th>
                <Th>创建时间</Th>
                <Th>操作</Th>
              </tr>
            </thead>
            <tbody>
              {users.map((row) => (
                <UserRow
                  key={row.id}
                  row={row}
                  isSelf={row.id === currentUser.id}
                  onRoleChange={(role) => runAction(() => api.updateUserRole(row.id, role))}
                  onToggleStatus={() =>
                    runAction(() =>
                      api.updateUserStatus(row.id, row.status === 'active' ? 'disabled' : 'active'),
                    )
                  }
                  onEditFeatures={() => setFeaturesTarget(row)}
                  onResetPassword={() => setResetTarget(row)}
                  onDelete={() => {
                    if (window.confirm(`确定删除用户「${row.username}」？该操作不可恢复。`)) {
                      runAction(() => api.deleteUser(row.id))
                    }
                  }}
                />
              ))}
            </tbody>
          </DataTable>
        )}
      </section>
      {createOpen && (
        <CreateUserDialog
          onClose={() => setCreateOpen(false)}
          onCreated={() => {
            setCreateOpen(false)
            invalidate()
          }}
        />
      )}
      {resetTarget && (
        <ResetPasswordDialog
          target={resetTarget}
          onClose={() => setResetTarget(null)}
          onDone={() => setResetTarget(null)}
        />
      )}
      {featuresTarget && (
        <FeaturesDialog
          target={featuresTarget}
          onClose={() => setFeaturesTarget(null)}
          onDone={() => {
            setFeaturesTarget(null)
            invalidate()
          }}
        />
      )}
    </div>
  )
}

function UserRow({
  row,
  isSelf,
  onRoleChange,
  onToggleStatus,
  onEditFeatures,
  onResetPassword,
  onDelete,
}: {
  row: AdminUserRow
  isSelf: boolean
  onRoleChange: (role: UserRole) => void
  onToggleStatus: () => void
  onEditFeatures: () => void
  onResetPassword: () => void
  onDelete: () => void
}) {
  const active = row.status === 'active'
  return (
    <tr className="border-b border-line last:border-0 hover:bg-[#fafafa]">
      <Td>
        <div className="font-medium">
          {row.username}
          {isSelf && <span className="ml-1 text-xs text-zinc-400">（我）</span>}
        </div>
      </Td>
      <Td>
        <select
          className="h-8 rounded-md border border-line bg-panel px-2 text-sm"
          value={row.role}
          onChange={(event) => onRoleChange(event.target.value as UserRole)}
        >
          {(Object.keys(ROLE_LABELS) as UserRole[]).map((role) => (
            <option key={role} value={role}>
              {ROLE_LABELS[role]}
            </option>
          ))}
        </select>
      </Td>
      <Td>
        <div className="flex items-center gap-2">
          <Badge className={active ? 'bg-emerald-50 text-success' : 'bg-zinc-100 text-zinc-500'}>
            {active ? '启用' : '停用'}
          </Badge>
          <button
            title={isSelf ? '不能停用自己的账号' : active ? '停用账号' : '启用账号'}
            className="flex h-7 w-7 items-center justify-center rounded text-zinc-500 hover:bg-field hover:text-ink disabled:cursor-not-allowed disabled:text-zinc-300"
            disabled={isSelf}
            onClick={onToggleStatus}
          >
            <Power size={14} />
          </button>
        </div>
      </Td>
      <Td>
        <div className="flex flex-wrap items-center gap-1">
          {FEATURE_LABELS.map(({ key, label }) => (
            <Badge
              key={key}
              className={row.features[key] ? 'bg-emerald-50 text-success' : 'bg-zinc-100 text-zinc-400'}
            >
              {label}
            </Badge>
          ))}
          <button
            title="编辑功能权限"
            className="flex h-7 w-7 items-center justify-center rounded text-zinc-500 hover:bg-field hover:text-ink"
            onClick={onEditFeatures}
          >
            <Pencil size={13} />
          </button>
        </div>
      </Td>
      <Td>{formatDate(row.createdAt)}</Td>
      <Td>
        <div className="flex items-center gap-1">
          <button
            title="重置密码"
            className="flex h-7 w-7 items-center justify-center rounded text-zinc-500 hover:bg-field hover:text-ink"
            onClick={onResetPassword}
          >
            <KeyRound size={14} />
          </button>
          <button
            title={isSelf ? '不能删除自己的账号' : '删除用户'}
            className="flex h-7 w-7 items-center justify-center rounded text-zinc-500 hover:bg-red-50 hover:text-danger disabled:cursor-not-allowed disabled:text-zinc-300 disabled:hover:bg-transparent"
            disabled={isSelf}
            onClick={onDelete}
          >
            <Trash2 size={14} />
          </button>
        </div>
      </Td>
    </tr>
  )
}

function DialogShell({
  icon: Icon,
  title,
  children,
  pending,
  onClose,
}: {
  icon: LucideIcon
  title: string
  children: React.ReactNode
  pending: boolean
  onClose: () => void
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !pending) onClose()
      }}
    >
      <div
        className="w-full max-w-sm rounded-md border border-line bg-panel p-5 shadow-card"
        role="dialog"
        aria-modal="true"
      >
        <div className="mb-4 flex items-center gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-field text-zinc-700">
            <Icon size={17} />
          </div>
          <div className="text-sm font-semibold">{title}</div>
        </div>
        {children}
      </div>
    </div>
  )
}

function DialogActions({
  pending,
  submitLabel,
  onCancel,
  onSubmit,
}: {
  pending: boolean
  submitLabel: string
  onCancel: () => void
  onSubmit: () => void
}) {
  return (
    <div className="mt-5 flex justify-end gap-2">
      <button
        className="h-9 rounded-md border border-line px-3 text-sm hover:bg-field disabled:text-zinc-400"
        disabled={pending}
        onClick={onCancel}
      >
        取消
      </button>
      <button
        className="flex h-9 items-center gap-2 rounded-md bg-ink px-3 text-sm font-medium text-white disabled:bg-zinc-300"
        disabled={pending}
        onClick={onSubmit}
      >
        {pending && <LoaderCircle size={15} className="animate-spin" />}
        {submitLabel}
      </button>
    </div>
  )
}

const inputClass = 'h-9 w-full rounded-md border border-line bg-panel px-3 text-sm'

function FeatureCheckboxes({
  value,
  onChange,
}: {
  value: UserFeatures
  onChange: (next: UserFeatures) => void
}) {
  return (
    <div className="grid grid-cols-2 gap-2">
      {FEATURE_LABELS.map(({ key, label }) => (
        <label key={key} className="flex items-center gap-2 text-sm text-zinc-700">
          <input
            type="checkbox"
            className="h-4 w-4 accent-[#3d735a]"
            checked={value[key]}
            onChange={(event) => onChange({ ...value, [key]: event.target.checked })}
          />
          {label}
        </label>
      ))}
    </div>
  )
}

function CreateUserDialog({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [username, setUsername] = React.useState('')
  const [password, setPassword] = React.useState('')
  const [role, setRole] = React.useState<UserRole>('user')
  const [features, setFeatures] = React.useState<UserFeatures>(ALL_FEATURES)
  const [pending, setPending] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  const submit = () => {
    if (!username.trim()) {
      setError('请输入用户名')
      return
    }
    if (password.length < 8) {
      setError('密码至少 8 位')
      return
    }
    setPending(true)
    setError(null)
    void api
      .createUser({ username: username.trim(), password, role, features })
      .then(onCreated)
      .catch((cause: unknown) => setError(errorMessage(cause)))
      .finally(() => setPending(false))
  }

  return (
    <DialogShell icon={Plus} title="创建用户" pending={pending} onClose={onClose}>
      <div className="flex flex-col gap-3">
        <input
          className={inputClass}
          placeholder="用户名"
          value={username}
          onChange={(event) => setUsername(event.target.value)}
        />
        <input
          className={inputClass}
          type="password"
          placeholder="初始密码（至少 8 位）"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
        <select
          className={inputClass}
          value={role}
          onChange={(event) => setRole(event.target.value as UserRole)}
        >
          {(Object.keys(ROLE_LABELS) as UserRole[]).map((item) => (
            <option key={item} value={item}>
              {ROLE_LABELS[item]}
            </option>
          ))}
        </select>
        <div>
          <div className="mb-2 text-xs text-zinc-500">功能权限（默认全部开启）</div>
          <FeatureCheckboxes value={features} onChange={setFeatures} />
        </div>
        {error && <div className="text-xs text-danger">{error}</div>}
      </div>
      <DialogActions pending={pending} submitLabel="创建" onCancel={onClose} onSubmit={submit} />
    </DialogShell>
  )
}

function ResetPasswordDialog({
  target,
  onClose,
  onDone,
}: {
  target: AdminUserRow
  onClose: () => void
  onDone: () => void
}) {
  const [password, setPassword] = React.useState('')
  const [confirm, setConfirm] = React.useState('')
  const [pending, setPending] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  const submit = () => {
    if (password.length < 8) {
      setError('密码至少 8 位')
      return
    }
    if (password !== confirm) {
      setError('两次输入的密码不一致')
      return
    }
    setPending(true)
    setError(null)
    void api
      .resetUserPassword(target.id, password)
      .then(onDone)
      .catch((cause: unknown) => setError(errorMessage(cause)))
      .finally(() => setPending(false))
  }

  return (
    <DialogShell icon={KeyRound} title={`重置密码：${target.username}`} pending={pending} onClose={onClose}>
      <div className="flex flex-col gap-3">
        <input
          className={inputClass}
          type="password"
          placeholder="新密码（至少 8 位）"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
        <input
          className={inputClass}
          type="password"
          placeholder="再次输入新密码"
          value={confirm}
          onChange={(event) => setConfirm(event.target.value)}
        />
        <div className="text-xs leading-5 text-zinc-500">重置后该用户的登录状态将全部失效，需要重新登录。</div>
        {error && <div className="text-xs text-danger">{error}</div>}
      </div>
      <DialogActions pending={pending} submitLabel="重置" onCancel={onClose} onSubmit={submit} />
    </DialogShell>
  )
}

function FeaturesDialog({
  target,
  onClose,
  onDone,
}: {
  target: AdminUserRow
  onClose: () => void
  onDone: () => void
}) {
  const [features, setFeatures] = React.useState<UserFeatures>({ ...target.features })
  const [pending, setPending] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  const submit = () => {
    setPending(true)
    setError(null)
    void api
      .updateUserFeatures(target.id, features)
      .then(onDone)
      .catch((cause: unknown) => setError(errorMessage(cause)))
      .finally(() => setPending(false))
  }

  return (
    <DialogShell icon={Pencil} title={`功能权限：${target.username}`} pending={pending} onClose={onClose}>
      <FeatureCheckboxes value={features} onChange={setFeatures} />
      <div className="mt-3 text-xs leading-5 text-zinc-500">
        关闭后该用户立即无法使用对应功能（历史数据保留，重新开启即恢复）。
      </div>
      {error && <div className="mt-2 text-xs text-danger">{error}</div>}
      <DialogActions pending={pending} submitLabel="保存" onCancel={onClose} onSubmit={submit} />
    </DialogShell>
  )
}
