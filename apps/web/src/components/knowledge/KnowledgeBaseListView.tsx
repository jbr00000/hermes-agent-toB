import * as React from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Database, LoaderCircle, Plus, Trash2 } from 'lucide-react'
import { api, ApiError } from '../../api'
import type { KnowledgeBase, TabType } from '../../types'
import { DataTable, PageHeader, Td, Th } from '../ui'

function formatDateTime(timestampMs: number): string {
  return new Date(timestampMs).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** 步骤①：知识库列表。admin 可新建/删除（级联）；点击进入库详情做上传与解析。 */
export function KnowledgeBaseListView({
  isAdmin,
  onOpenTab,
}: {
  isAdmin: boolean
  onOpenTab: (type: TabType, refId: string, title: string) => void
}) {
  const queryClient = useQueryClient()
  const [actionError, setActionError] = React.useState<string | null>(null)
  const [creating, setCreating] = React.useState(false)
  const query = useQuery({
    queryKey: ['knowledgeBases'],
    queryFn: () => api.listKnowledgeBases(),
    retry: false,
  })
  const disabled = query.error instanceof ApiError && query.error.status === 404
  const bases = query.data ?? []

  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ['knowledgeBases'] })

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader icon={Database} title="知识库" subtitle="按主题分库管理文档：先建库，再上传文档，最后选择文档解析入库" />
      <div className="thin-scrollbar min-h-0 flex-1 overflow-y-auto p-5">
        <div className="mb-4 flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-sm font-semibold">知识库列表</div>
            <div className="text-xs text-zinc-500">点击进入某个知识库，上传并选择文档解析</div>
          </div>
          {isAdmin && !disabled && (
            <button
              onClick={() => setCreating(true)}
              className="flex h-8 shrink-0 items-center gap-2 whitespace-nowrap rounded-md bg-ink px-3 text-sm text-white transition"
            >
              <Plus size={15} />
              新建知识库
            </button>
          )}
        </div>
        {actionError && <div className="mb-3 text-xs text-danger">{actionError}</div>}
        {disabled ? (
          <div className="border-y border-line py-8 text-center text-sm text-zinc-400">
            当前部署未启用知识库（deployment.yaml: knowledge.enabled=false）
          </div>
        ) : bases.length === 0 && !query.isPending ? (
          <div className="border-y border-line py-8 text-center text-sm text-zinc-400">
            {query.isError
              ? '知识库服务暂不可用'
              : isAdmin
                ? '还没有知识库：点击右上角「新建知识库」创建第一个'
                : '还没有知识库，等待管理员创建'}
          </div>
        ) : (
          <DataTable>
            <colgroup>
              <col className="w-[34%]" />
              <col className="w-[12%]" />
              <col className="w-[12%]" />
              <col className="w-[18%]" />
              {isAdmin && <col className="w-[10%]" />}
            </colgroup>
            <thead>
              <tr>
                <Th>知识库</Th>
                <Th>文档</Th>
                <Th>分块</Th>
                <Th>更新</Th>
                {isAdmin && <Th>操作</Th>}
              </tr>
            </thead>
            <tbody>
              {bases.map((base) => (
                <BaseRow
                  key={base.id}
                  base={base}
                  isAdmin={isAdmin}
                  onOpen={() => onOpenTab('knowledgeBaseDetail', base.id, base.name)}
                  onDelete={() => {
                    const hint = base.docCount > 0
                      ? `确定删除「${base.name}」？库内 ${base.docCount} 个文档及其分块、索引、文件将一并清除，不可恢复。`
                      : `确定删除「${base.name}」？`
                    if (window.confirm(hint)) {
                      setActionError(null)
                      void api
                        .deleteKnowledgeBase(base.id)
                        .then(invalidate)
                        .catch((cause: unknown) => {
                          setActionError(cause instanceof ApiError ? cause.message : '删除失败，请重试')
                        })
                    }
                  }}
                />
              ))}
            </tbody>
          </DataTable>
        )}
      </div>
      {creating && (
        <CreateBaseDialog
          onClose={() => setCreating(false)}
          onCreated={(base) => {
            setCreating(false)
            invalidate()
            onOpenTab('knowledgeBaseDetail', base.id, base.name)
          }}
        />
      )}
    </div>
  )
}

function BaseRow({
  base,
  isAdmin,
  onOpen,
  onDelete,
}: {
  base: KnowledgeBase
  isAdmin: boolean
  onOpen: () => void
  onDelete: () => void
}) {
  return (
    <tr className="border-b border-line last:border-0 hover:bg-[#fafafa]">
      <Td className="overflow-hidden">
        <button
          title={base.description ?? base.name}
          className="flex w-full min-w-0 max-w-full items-center gap-2 overflow-hidden text-left font-medium hover:text-info"
          onClick={onOpen}
        >
          <Database size={15} className="shrink-0 text-zinc-400" />
          <span className="min-w-0 flex-1 truncate">{base.name}</span>
        </button>
      </Td>
      <Td>{base.docCount}</Td>
      <Td>{base.chunkCount}</Td>
      <Td>{formatDateTime(base.updatedAt)}</Td>
      {isAdmin && (
        <Td>
          <button
            title="删除知识库（级联删除库内文档）"
            className="flex h-7 w-7 items-center justify-center rounded text-zinc-500 hover:bg-red-50 hover:text-danger"
            onClick={onDelete}
          >
            <Trash2 size={14} />
          </button>
        </Td>
      )}
    </tr>
  )
}

function CreateBaseDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void
  onCreated: (base: KnowledgeBase) => void
}) {
  const [name, setName] = React.useState('')
  const [description, setDescription] = React.useState('')
  const [pending, setPending] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  const submit = () => {
    if (!name.trim() || pending) return
    setPending(true)
    setError(null)
    void api
      .createKnowledgeBase(name.trim(), description.trim() || undefined)
      .then(onCreated)
      .catch((cause: unknown) => {
        setError(cause instanceof ApiError ? cause.message : '创建失败，请重试')
        setPending(false)
      })
  }

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
            <Database size={17} />
          </div>
          <div className="text-sm font-semibold">新建知识库</div>
        </div>
        <label className="mb-3 block">
          <span className="mb-1 block text-xs text-zinc-500">名称</span>
          <input
            autoFocus
            value={name}
            onChange={(event) => setName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') submit()
            }}
            placeholder="例如：运维规范"
            className="h-9 w-full rounded-md border border-line bg-panel px-3 text-sm"
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-xs text-zinc-500">描述（可选）</span>
          <input
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="这个库存放什么文档"
            className="h-9 w-full rounded-md border border-line bg-panel px-3 text-sm"
          />
        </label>
        {error && <div className="mt-3 text-xs text-danger">{error}</div>}
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
            disabled={pending || !name.trim()}
            onClick={submit}
          >
            {pending && <LoaderCircle size={15} className="animate-spin" />}
            创建
          </button>
        </div>
      </div>
    </div>
  )
}
