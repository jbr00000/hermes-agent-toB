import * as React from 'react'
import { LoaderCircle, Pencil, Plus, type LucideIcon } from 'lucide-react'
import type { Dataset, DatasetInput, DataSource } from '../../types'
import { Toggle } from '../ui'

const inputClass = 'h-9 w-full rounded-md border border-line bg-panel px-3 text-sm'
const textareaClass = 'w-full rounded-md border border-line bg-panel px-3 py-2 text-sm'

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
        className="w-full max-w-md rounded-md border border-line bg-panel p-5 shadow-card"
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

/** 新增/编辑数据集弹窗（图3「新增数据集」入口）。 */
export function DatasetEditModal({
  target,
  dataSources,
  pending,
  error,
  onSubmit,
  onClose,
}: {
  /** null = 新建；否则编辑该数据集 */
  target: Dataset | null
  dataSources: DataSource[]
  pending: boolean
  error: string | null
  onSubmit: (input: DatasetInput) => void
  onClose: () => void
}) {
  const editing = target !== null
  const [name, setName] = React.useState(target?.name ?? '')
  const [dataSourceId, setDataSourceId] = React.useState(target?.dataSourceId ?? dataSources[0]?.id ?? '')
  const [description, setDescription] = React.useState(target?.description ?? '')
  const [prompt, setPrompt] = React.useState(target?.prompt ?? '')
  const [enabled, setEnabled] = React.useState(target?.enabled ?? true)

  const valid = name.trim() !== '' && dataSourceId !== ''

  const submit = () => {
    if (!valid || pending) return
    onSubmit({
      name: name.trim(),
      description: description.trim(),
      dataSourceId,
      enabled,
      prompt: prompt.trim(),
    })
  }

  return (
    <DialogShell icon={editing ? Pencil : Plus} title={editing ? '编辑数据集' : '新增数据集'} pending={pending} onClose={onClose}>
      <div className="space-y-3">
        <Field label="数据集名称">
          <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)} placeholder="如：基金问数数据集" />
        </Field>
        <Field label="业务数据源">
          <select className={inputClass} value={dataSourceId} onChange={(e) => setDataSourceId(e.target.value)}>
            {dataSources.map((ds) => (
              <option key={ds.id} value={ds.id}>
                {ds.name}（{ds.database}）
              </option>
            ))}
          </select>
        </Field>
        <Field label="业务说明（为空时治理状态显示「缺业务说明」）">
          <textarea className={textareaClass} rows={3} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="数据集覆盖的业务范围、统计口径等" />
        </Field>
        <Field label="问数提示词（为空时治理状态显示「缺提示词」）">
          <textarea className={textareaClass} rows={3} value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="问数时注入的领域约定：字段口径、单位、时间格式等" />
        </Field>
        <div className="flex items-center justify-between pt-1">
          <span className="text-xs text-zinc-500">启用该数据集（停用后问数页不可选）</span>
          <Toggle checked={enabled} onChange={setEnabled} label={enabled ? '停用' : '启用'} />
        </div>
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
