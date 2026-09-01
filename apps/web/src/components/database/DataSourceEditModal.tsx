import * as React from 'react'
import { LoaderCircle, Pencil, Plus, type LucideIcon } from 'lucide-react'
import type { DataSource, DataSourceInput, DbType } from '../../types'

const inputClass = 'h-9 w-full rounded-md border border-line bg-panel px-3 text-sm'

const DEFAULT_PORTS: Record<DbType, number> = {
  mysql: 3306,
  postgresql: 5432,
  sqlite: 0,
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

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs text-zinc-500">{label}</span>
      {children}
    </label>
  )
}

/** 新建/编辑数据库连接弹窗（图2）。密码 write-only：编辑时留空 = 不修改。 */
export function DataSourceEditModal({
  target,
  pending,
  error,
  onSubmit,
  onClose,
}: {
  /** null = 新建；否则编辑该连接 */
  target: DataSource | null
  pending: boolean
  error: string | null
  onSubmit: (input: DataSourceInput) => void
  onClose: () => void
}) {
  const editing = target !== null
  const [name, setName] = React.useState(target?.name ?? '')
  const [dbType, setDbType] = React.useState<DbType>(target?.dbType ?? 'mysql')
  const [host, setHost] = React.useState(target?.host ?? '')
  const [port, setPort] = React.useState(target ? String(target.port) : String(DEFAULT_PORTS.mysql))
  const [database, setDatabase] = React.useState(target?.database ?? '')
  const [username, setUsername] = React.useState(target?.username ?? '')
  const [password, setPassword] = React.useState('')

  const portNum = Number(port)
  const valid =
    name.trim() !== '' &&
    host.trim() !== '' &&
    Number.isInteger(portNum) && portNum > 0 && portNum <= 65535 &&
    database.trim() !== '' &&
    username.trim() !== '' &&
    (editing || password !== '')

  const submit = () => {
    if (!valid || pending) return
    onSubmit({
      name: name.trim(),
      dbType,
      host: host.trim(),
      port: portNum,
      database: database.trim(),
      username: username.trim(),
      ...(password !== '' ? { password } : {}),
    })
  }

  return (
    <DialogShell icon={editing ? Pencil : Plus} title={editing ? '编辑数据库连接' : '新增数据库连接'} pending={pending} onClose={onClose}>
      <div className="space-y-3">
        <Field label="数据库中文名">
          <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)} placeholder="如：工作督办" />
        </Field>
        <Field label="数据库类型">
          <select
            className={inputClass}
            value={dbType}
            onChange={(e) => {
              const next = e.target.value as DbType
              setDbType(next)
              if (!editing) setPort(String(DEFAULT_PORTS[next]))
            }}
          >
            <option value="mysql">MySQL</option>
            <option value="postgresql">PostgreSQL</option>
          </select>
        </Field>
        <div className="grid grid-cols-[1fr_120px] gap-3">
          <Field label="主机地址">
            <input className={inputClass} value={host} onChange={(e) => setHost(e.target.value)} placeholder="如：192.168.1.86" />
          </Field>
          <Field label="端口">
            <input className={inputClass} value={port} onChange={(e) => setPort(e.target.value)} inputMode="numeric" />
          </Field>
        </div>
        <Field label="用户名">
          <input className={inputClass} value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="off" />
        </Field>
        <Field label={editing ? '密码（留空表示不修改）' : '密码'}>
          <input
            className={inputClass}
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={editing ? '留空表示不修改' : ''}
            autoComplete="new-password"
          />
        </Field>
        <Field label="数据库名称">
          <input className={inputClass} value={database} onChange={(e) => setDatabase(e.target.value)} placeholder="如：business_data" />
        </Field>
        {error && <div className="text-xs text-danger">{error}</div>}
      </div>

      <div className="mt-5 flex justify-end gap-2">
        <button
          className="h-9 rounded-md border border-line px-3 text-sm hover:bg-field disabled:text-zinc-400"
          disabled={pending}
          onClick={onClose}
        >
          取消
        </button>
        <button
          className="flex h-9 items-center gap-2 rounded-md bg-ink px-3 text-sm font-medium text-white disabled:bg-zinc-300"
          disabled={pending || !valid}
          onClick={submit}
        >
          {pending && <LoaderCircle size={15} className="animate-spin" />}
          确定
        </button>
      </div>
    </DialogShell>
  )
}
