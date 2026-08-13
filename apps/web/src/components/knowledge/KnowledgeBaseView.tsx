import * as React from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Database, FileText, RotateCcw, Trash2 } from 'lucide-react'
import { api, ApiError } from '../../api'
import type { KnowledgeDocument, TabType } from '../../types'
import { DataTable, formatBytes, PageHeader, Td, Th } from '../ui'
import { StatusBadge } from './StatusBadge'
import { UploadButton } from './UploadButton'

const ACTIVE_STATUSES = new Set(['pending', 'parsing', 'syncing'])

function formatDateTime(timestampMs: number): string {
  return new Date(timestampMs).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** 企业统一知识库：admin 可上传/删除/重试，普通用户只读；构建中每 3s 轮询。 */
export function KnowledgeBaseView({
  isAdmin,
  onOpenTab,
}: {
  isAdmin: boolean
  onOpenTab: (type: TabType, refId: string, title: string) => void
}) {
  const queryClient = useQueryClient()
  const [actionError, setActionError] = React.useState<string | null>(null)
  const query = useQuery({
    queryKey: ['knowledgeDocuments'],
    queryFn: () => api.listKnowledgeDocuments(),
    retry: false,
    refetchInterval: (q) => {
      const docs = q.state.data?.documents ?? []
      return docs.some((doc) => ACTIVE_STATUSES.has(doc.status)) ? 3000 : false
    },
  })
  const disabled = query.error instanceof ApiError && query.error.status === 404
  const documents = query.data?.documents ?? []
  const stats = query.data?.stats ?? { documents: 0, chunks: 0 }

  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ['knowledgeDocuments'] })
  const runAction = (action: () => Promise<unknown>) => {
    setActionError(null)
    void action().then(invalidate).catch((cause: unknown) => {
      setActionError(cause instanceof ApiError ? cause.message : '操作失败，请重试')
    })
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader icon={Database} title="知识库" subtitle="企业统一知识库：文档解析、语义分块与检索入库" />
      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[260px_minmax(0,1fr)]">
        <aside className="border-r border-line bg-[#fbfbfc] p-3">
          <div className="w-full rounded-md bg-field px-3 py-2 text-left text-sm">
            <div className="flex items-center justify-between">
              <span className="font-medium">企业知识库</span>
              <span className="text-[11px] text-zinc-500">全员可读</span>
            </div>
            <div className="mt-1 text-xs text-zinc-500">
              {stats.documents} 个文档 · {stats.chunks} 个分块
            </div>
          </div>
          <p className="mt-3 px-1 text-xs leading-5 text-zinc-500">
            由管理员统一维护；个人知识库将在后续版本开放。
          </p>
        </aside>
        <section className="thin-scrollbar min-w-0 overflow-y-auto p-5">
          <div className="mb-4 flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="text-sm font-semibold">文档列表</div>
              <div className="text-xs text-zinc-500">
                支持 PDF / Word / PPT / Excel / TXT / Markdown，上传后自动解析入库
              </div>
            </div>
            {isAdmin && !disabled && <UploadButton onUploaded={invalidate} />}
          </div>
          {actionError && <div className="mb-3 text-xs text-danger">{actionError}</div>}
          {disabled ? (
            <div className="border-y border-line py-8 text-center text-sm text-zinc-400">
              当前部署未启用知识库（deployment.yaml: knowledge.enabled=false）
            </div>
          ) : documents.length === 0 && !query.isPending ? (
            <div className="border-y border-line py-8 text-center text-sm text-zinc-400">
              {query.isError ? '知识库服务暂不可用' : '还没有文档，等待管理员上传'}
            </div>
          ) : (
            <DataTable>
              <colgroup>
                <col className="w-[36%]" />
                <col className="w-[12%]" />
                <col className="w-[9%]" />
                <col className="w-[10%]" />
                <col className="w-[15%]" />
                {isAdmin && <col className="w-[18%]" />}
              </colgroup>
              <thead>
                <tr>
                  <Th>文档</Th>
                  <Th>状态</Th>
                  <Th>分块</Th>
                  <Th>大小</Th>
                  <Th>更新</Th>
                  {isAdmin && <Th>操作</Th>}
                </tr>
              </thead>
              <tbody>
                {documents.map((doc) => (
                  <DocumentRow
                    key={doc.id}
                    doc={doc}
                    isAdmin={isAdmin}
                    onOpen={() => onOpenTab('document', doc.id, doc.title)}
                    onRetry={() => runAction(() => api.retryKnowledgeDocument(doc.id))}
                    onDelete={() => {
                      if (window.confirm(`确定删除「${doc.title}」？分块与索引将一并清除。`)) {
                        runAction(() => api.deleteKnowledgeDocument(doc.id))
                      }
                    }}
                  />
                ))}
              </tbody>
            </DataTable>
          )}
        </section>
      </div>
    </div>
  )
}

function DocumentRow({
  doc,
  isAdmin,
  onOpen,
  onRetry,
  onDelete,
}: {
  doc: KnowledgeDocument
  isAdmin: boolean
  onOpen: () => void
  onRetry: () => void
  onDelete: () => void
}) {
  return (
    <tr className="border-b border-line last:border-0 hover:bg-[#fafafa]">
      <Td className="overflow-hidden">
        <button
          title={doc.error ? `${doc.fileName}：${doc.error}` : doc.fileName}
          className="flex w-full min-w-0 max-w-full items-center gap-2 overflow-hidden text-left font-medium hover:text-info"
          onClick={onOpen}
        >
          <FileText size={15} className="shrink-0 text-zinc-400" />
          <span className="min-w-0 flex-1 truncate">{doc.title}</span>
        </button>
      </Td>
      <Td><StatusBadge status={doc.status} /></Td>
      <Td>{doc.chunkCount}</Td>
      <Td>{formatBytes(doc.sizeBytes)}</Td>
      <Td>{formatDateTime(doc.updatedAt)}</Td>
      {isAdmin && (
        <Td>
          <div className="flex items-center gap-1">
            {doc.status === 'failed' && (
              <button
                title="重试构建"
                className="flex h-7 w-7 items-center justify-center rounded text-zinc-500 hover:bg-field hover:text-ink"
                onClick={onRetry}
              >
                <RotateCcw size={14} />
              </button>
            )}
            <button
              title="删除文档"
              className="flex h-7 w-7 items-center justify-center rounded text-zinc-500 hover:bg-red-50 hover:text-danger"
              onClick={onDelete}
            >
              <Trash2 size={14} />
            </button>
          </div>
        </Td>
      )}
    </tr>
  )
}
