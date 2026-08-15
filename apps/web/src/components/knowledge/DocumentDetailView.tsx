import { useQuery } from '@tanstack/react-query'
import { ChevronRight, FileText } from 'lucide-react'
import { api, ApiError } from '../../api'
import type { TabType } from '../../types'
import { formatBytes, InfoRow, PageHeader } from '../ui'
import { StatusBadge } from './StatusBadge'

/** 文档详情：元信息 + 分块预览（#/标题/token 数/内容截断）。 */
export function DocumentDetailView({
  documentId,
  onOpenTab,
}: {
  documentId: string
  onOpenTab: (type: TabType, refId: string, title: string) => void
}) {
  const docQuery = useQuery({
    queryKey: ['knowledgeDocument', documentId],
    queryFn: () => api.getKnowledgeDocument(documentId),
    retry: false,
    refetchInterval: (q) => {
      const status = q.state.data?.status
      return status === 'pending' || status === 'parsing' || status === 'syncing' ? 3000 : false
    },
  })
  const chunksQuery = useQuery({
    queryKey: ['knowledgeChunks', documentId],
    queryFn: () => api.listKnowledgeChunks(documentId),
    retry: false,
  })
  const basesQuery = useQuery({
    queryKey: ['knowledgeBases'],
    queryFn: () => api.listKnowledgeBases(),
    retry: false,
  })
  const doc = docQuery.data
  const chunks = chunksQuery.data ?? []
  const base = doc ? basesQuery.data?.find((item) => item.id === doc.kbId) : undefined

  if (docQuery.isPending) {
    return <div className="p-8 text-sm text-zinc-400">加载中…</div>
  }
  if (!doc) {
    const notFound = docQuery.error instanceof ApiError && docQuery.error.status === 404
    return (
      <div className="p-8 text-sm text-zinc-400">
        {notFound ? '文档不存在或已被删除' : '加载失败，请稍后重试'}
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader icon={FileText} title={doc.title} subtitle={doc.fileName} />
      <div className="thin-scrollbar min-w-0 flex-1 overflow-y-auto p-6">
        <div className="mb-4 flex items-center gap-1 text-xs text-zinc-500">
          <button className="hover:text-info" onClick={() => onOpenTab('knowledgeBase', 'main', '知识库')}>
            知识库
          </button>
          <ChevronRight size={12} className="text-zinc-300" />
          {base ? (
            <button
              className="hover:text-info"
              onClick={() => onOpenTab('knowledgeBaseDetail', base.id, base.name)}
            >
              {base.name}
            </button>
          ) : (
            <span>所属知识库</span>
          )}
          <ChevronRight size={12} className="text-zinc-300" />
          <span className="truncate text-zinc-700">{doc.title}</span>
        </div>
        <div className="border-y border-line py-4">
          <div className="mb-3 flex items-center justify-between">
            <div className="text-sm font-semibold">构建状态</div>
            <StatusBadge status={doc.status} />
          </div>
          <div className="grid max-w-3xl grid-cols-1 gap-y-3 sm:grid-cols-2 sm:gap-x-8">
            <InfoRow label="分块数" value={doc.chunkCount} />
            <InfoRow label="文件大小" value={formatBytes(doc.sizeBytes)} />
            <InfoRow
              label="解析器"
              value={doc.parser === 'mineru' ? 'MinerU（版面解析）' : doc.parser === 'local' ? '本地直读' : '—'}
            />
            <InfoRow label="重试次数" value={doc.retryCount} />
          </div>
          {doc.error && (
            <p className="mt-3 rounded-md bg-red-50 px-3 py-2 text-xs leading-5 text-danger">
              {doc.error}
            </p>
          )}
        </div>

        <div className="mt-6">
          <div className="mb-3 text-sm font-semibold">分块预览（{chunks.length}）</div>
          {chunks.length === 0 ? (
            <div className="border-y border-line py-6 text-center text-sm text-zinc-400">
              {doc.status === 'ready' ? '该文档没有可用分块' : '构建完成后这里会显示分块内容'}
            </div>
          ) : (
            <div className="divide-y divide-line border-y border-line">
              {chunks.map((chunk) => (
                <div key={chunk.id} className="py-3 text-sm">
                  <div className="mb-1 flex items-center justify-between gap-3 text-xs text-zinc-400">
                    <span className="min-w-0 truncate">
                      Chunk {chunk.docPos + 1}
                      {chunk.chunkTitle && ` · ${chunk.chunkTitle}`}
                    </span>
                    <span className="shrink-0">{chunk.tokenNum} tokens</span>
                  </div>
                  <p className="line-clamp-3 whitespace-pre-wrap break-all leading-6 text-zinc-700">
                    {chunk.content}
                  </p>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
