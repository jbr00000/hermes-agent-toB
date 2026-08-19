import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight, Download, FileText, Pencil, RefreshCw, X } from 'lucide-react'
import { api, ApiError } from '../../api'
import type { KnowledgeChunk, TabType } from '../../types'
import { Badge, cn, formatBytes, PageHeader, Toggle } from '../ui'
import { Markdown } from '../Markdown'
import { StatusBadge } from './StatusBadge'

/** 文档详情双栏视图：左栏分块卡片（admin 可编辑/启停），右栏原始文件预览。 */
const CHUNK_PAGE_SIZE = 10
/** 解析时经 LibreOffice 转出 PDF 的格式——有预览件时按 PDF 渲染 */
const OFFICE_PREVIEW_EXTS = new Set(['.doc', '.docx', '.ppt', '.pptx', '.xls'])
/** 原文本身可直接在线预览的格式——只有这些才在进入页面时拉取原始文件，其余按需（点下载时）再拉 */
const ORIGINAL_PREVIEW_EXTS = new Set(['.pdf', '.md', '.txt'])
export function DocumentDetailView({
  documentId,
  isAdmin,
  onOpenTab,
}: {
  documentId: string
  isAdmin: boolean
  onOpenTab: (type: TabType, refId: string, title: string) => void
}) {
  const queryClient = useQueryClient()
  const chunksKey = ['knowledgeChunks', documentId] as const

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
    queryKey: chunksKey,
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
  const fileExt = doc?.fileExt ?? ''
  // 上传落盘后任何状态都能预览原文；分块只有 ready 后才可编辑
  const canEdit = Boolean(isAdmin && doc?.status === 'ready')

  // 分块前端分页：每页 10 块；换文档时回到第一页
  const [page, setPage] = useState(1)
  useEffect(() => setPage(1), [documentId])
  const totalPages = Math.max(1, Math.ceil(chunks.length / CHUNK_PAGE_SIZE))
  const currentPage = Math.min(page, totalPages)
  const pagedChunks = chunks.slice((currentPage - 1) * CHUNK_PAGE_SIZE, currentPage * CHUNK_PAGE_SIZE)

  // 编辑态放组件本地 state（不从 query data 派生）：别的卡片 invalidate 不会冲掉草稿
  const [editingId, setEditingId] = useState<string | null>(null)
  const [draftTitle, setDraftTitle] = useState('')
  const [draftContent, setDraftContent] = useState('')
  const [notice, setNotice] = useState<string | null>(null)
  const [syncWarn, setSyncWarn] = useState<string | null>(null)
  // 所有 hook 必须在上方早退（isPending / !doc）之前声明——否则首次打开
  // 文档详情（无缓存 → 先走早退分支）时 hook 数量变化，React 直接抛错白屏
  const [downloading, setDownloading] = useState(false)

  // 编辑期间文档被重试重建 → 状态离开 ready，草稿对应的 chunk 已失效
  useEffect(() => {
    if (editingId && doc && doc.status !== 'ready') {
      setEditingId(null)
      setNotice('文档正在重建，编辑已取消')
    }
  }, [editingId, doc])

  const fileQuery = useQuery({
    queryKey: ['knowledgeDocFile', documentId],
    queryFn: () => api.fetchKnowledgeDocumentFile(documentId),
    // Office/xlsx 的右栏走转换 PDF 或下载降级，原文只在点下载时按需拉取，避免白拉大文件
    enabled: Boolean(doc) && ORIGINAL_PREVIEW_EXTS.has(fileExt),
    staleTime: Infinity,
    retry: false,
  })
  const textQuery = useQuery({
    queryKey: ['knowledgeDocText', documentId],
    queryFn: () => fileQuery.data!.text(),
    enabled: Boolean(fileQuery.data) && (fileExt === '.md' || fileExt === '.txt'),
    staleTime: Infinity,
    retry: false,
  })

  // Blob → objectURL（PDF iframe 用）；卸载/换文档时回收，防内存泄漏
  const blobUrl = useMemo(() => {
    if (!fileQuery.data || fileExt !== '.pdf') return null
    return URL.createObjectURL(fileQuery.data)
  }, [fileQuery.data, fileExt])
  useEffect(() => {
    return () => {
      if (blobUrl) URL.revokeObjectURL(blobUrl)
    }
  }, [blobUrl])

  // Office 格式：取解析时留存的转换 PDF 作预览件，同样走 Blob → objectURL
  const officePreview = OFFICE_PREVIEW_EXTS.has(fileExt)
  const previewQuery = useQuery({
    queryKey: ['knowledgeDocPreview', documentId],
    queryFn: () => api.fetchKnowledgeDocumentFile(documentId, 'preview'),
    enabled: Boolean(doc) && officePreview,
    staleTime: Infinity,
    retry: false,
  })
  const previewUrl = useMemo(() => {
    if (!previewQuery.data) return null
    return URL.createObjectURL(previewQuery.data)
  }, [previewQuery.data])
  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl)
    }
  }, [previewUrl])

  const toggleMutation = useMutation({
    mutationFn: ({ chunk, next }: { chunk: KnowledgeChunk; next: boolean }) =>
      api.updateKnowledgeChunk(documentId, chunk.id, { isUse: next }),
    onMutate: async ({ chunk, next }) => {
      // 乐观更新 + 失败回滚
      await queryClient.cancelQueries({ queryKey: chunksKey })
      const previous = queryClient.getQueryData<KnowledgeChunk[]>(chunksKey)
      queryClient.setQueryData<KnowledgeChunk[]>(chunksKey, (old) =>
        old?.map((item) => (item.id === chunk.id ? { ...item, isUse: next } : item)),
      )
      return { previous }
    },
    onError: (error, _vars, context) => {
      if (context?.previous) queryClient.setQueryData(chunksKey, context.previous)
      setNotice(
        error instanceof ApiError && error.status === 409
          ? '文档正在重建，请稍后重试'
          : `操作失败：${error.message}`,
      )
    },
    onSuccess: (result) => {
      if (!result.synced) setSyncWarn('分块已保存，但检索索引同步失败')
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: chunksKey }),
  })

  const saveMutation = useMutation({
    mutationFn: ({ chunk, title, content }: { chunk: KnowledgeChunk; title: string; content: string }) =>
      api.updateKnowledgeChunk(documentId, chunk.id, {
        content,
        chunkTitle: title.trim() ? title.trim() : null,
      }),
    onSuccess: (result) => {
      queryClient.setQueryData<KnowledgeChunk[]>(chunksKey, (old) =>
        old?.map((item) => (item.id === result.chunk.id ? result.chunk : item)),
      )
      setEditingId(null)
      if (result.synced) {
        setNotice('分块已保存')
      } else {
        setSyncWarn('分块已保存，但检索索引同步失败')
      }
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 404) {
        setEditingId(null)
        setNotice('分块已被重建，请刷新')
      } else if (error instanceof ApiError && error.status === 409) {
        setNotice(error.message)
      } else {
        setNotice(`保存失败：${error.message}`)
      }
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: chunksKey }),
  })

  const resyncMutation = useMutation({
    mutationFn: () => api.resyncKnowledgeDocument(documentId),
    onSuccess: () => {
      setSyncWarn(null)
      setNotice('检索索引已重新同步')
    },
    onError: (error) => setNotice(`重新同步失败：${error.message}`),
  })

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

  const downloadFile = async () => {
    if (downloading) return
    setDownloading(true)
    try {
      // 未为预览拉过原文时（Office/xlsx），点击下载才按需拉取并回填缓存
      const blob = fileQuery.data
        ?? await queryClient.fetchQuery<Blob>({
          queryKey: ['knowledgeDocFile', documentId],
          queryFn: () => api.fetchKnowledgeDocumentFile(documentId),
          staleTime: Infinity,
          retry: false,
        })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = doc.fileName
      anchor.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (error) {
      setNotice(`下载失败：${error instanceof Error ? error.message : '请重试'}`)
    } finally {
      setDownloading(false)
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader icon={FileText} title={doc.title} subtitle={doc.fileName} />

      {/* 面包屑 + 状态摘要（压缩成一行，把纵向空间让给双栏） */}
      <div className="flex items-center justify-between gap-3 border-b border-line px-6 py-2.5">
        <div className="flex min-w-0 items-center gap-1 text-xs text-zinc-500">
          <button className="shrink-0 hover:text-info" onClick={() => onOpenTab('knowledgeBase', 'main', '知识库')}>
            知识库
          </button>
          <ChevronRight size={12} className="shrink-0 text-zinc-300" />
          {base ? (
            <button
              className="shrink-0 hover:text-info"
              onClick={() => onOpenTab('knowledgeBaseDetail', base.id, base.name)}
            >
              {base.name}
            </button>
          ) : (
            <span className="shrink-0">所属知识库</span>
          )}
          <ChevronRight size={12} className="shrink-0 text-zinc-300" />
          <span className="truncate text-zinc-700">{doc.title}</span>
        </div>
        <div className="flex shrink-0 items-center gap-3 text-xs text-zinc-500">
          <StatusBadge status={doc.status} />
          <span>{doc.chunkCount} 分块</span>
          <span>{formatBytes(doc.sizeBytes)}</span>
        </div>
      </div>

      {doc.error && (
        <p className="border-b border-line bg-red-50 px-6 py-2 text-xs leading-5 text-danger">
          {doc.error}
        </p>
      )}
      {syncWarn && (
        <div className="flex items-center justify-between gap-3 border-b border-line bg-amber-50 px-6 py-2 text-xs text-caution">
          <span>{syncWarn}——数据库已更新，但检索索引还是旧的。</span>
          <button
            className="flex h-6 shrink-0 items-center gap-1 rounded border border-caution/40 px-2 hover:bg-amber-100 disabled:opacity-50"
            onClick={() => resyncMutation.mutate()}
            disabled={resyncMutation.isPending}
          >
            <RefreshCw size={11} className={resyncMutation.isPending ? 'animate-spin' : undefined} />
            重新同步
          </button>
        </div>
      )}
      {notice && (
        <div className="flex items-center justify-between gap-3 border-b border-line px-6 py-1.5 text-xs text-zinc-500">
          <span>{notice}</span>
          <button className="shrink-0 text-zinc-400 hover:text-ink" onClick={() => setNotice(null)}>
            <X size={12} />
          </button>
        </div>
      )}

      {/* 双栏：各自独立滚动 */}
      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-2 lg:divide-x lg:divide-line">
        {/* 左：分块结果 */}
        <section className="flex min-h-[360px] flex-col lg:min-h-0">
          <header className="border-b border-line px-4 py-2 text-xs font-semibold text-zinc-500">
            分块结果（{chunks.length}）
          </header>
          <div className="thin-scrollbar min-h-0 flex-1 overflow-y-auto p-4">
            {chunks.length === 0 ? (
              <div className="py-10 text-center text-sm text-zinc-400">
                {doc.status === 'ready' ? '该文档没有可用分块' : '构建完成后这里会显示分块内容'}
              </div>
            ) : (
              <div className="space-y-3">
                {pagedChunks.map((chunk) =>
                  editingId === chunk.id ? (
                    <article key={chunk.id} className="rounded-md border border-ink/30 bg-panel p-3">
                      <div className="mb-2 text-xs font-semibold text-zinc-700">块 {chunk.docPos + 1}（编辑中）</div>
                      <input
                        value={draftTitle}
                        onChange={(event) => setDraftTitle(event.target.value)}
                        placeholder="分块标题（可空）"
                        className="mb-2 h-8 w-full rounded-md border border-line bg-panel px-2 text-sm"
                      />
                      <textarea
                        value={draftContent}
                        onChange={(event) => setDraftContent(event.target.value)}
                        rows={Math.min(20, Math.max(6, draftContent.split('\n').length + 1))}
                        className="w-full rounded-md border border-line bg-panel p-2 text-sm leading-6"
                      />
                      <div className="mt-2 flex items-center justify-between">
                        <span className="text-xs text-zinc-400">{draftContent.length} 字符</span>
                        <div className="flex gap-2">
                          <button
                            className="h-8 rounded-md border border-line px-3 text-sm hover:bg-field"
                            onClick={() => setEditingId(null)}
                            disabled={saveMutation.isPending}
                          >
                            取消
                          </button>
                          <button
                            className="h-8 rounded-md bg-ink px-3 text-sm text-white disabled:bg-zinc-300"
                            disabled={!draftContent.trim() || saveMutation.isPending}
                            onClick={() =>
                              saveMutation.mutate({ chunk, title: draftTitle, content: draftContent })
                            }
                          >
                            {saveMutation.isPending ? '保存中…' : '保存'}
                          </button>
                        </div>
                      </div>
                    </article>
                  ) : (
                    <ChunkCard
                      key={chunk.id}
                      chunk={chunk}
                      canEdit={canEdit}
                      toggling={toggleMutation.isPending}
                      onToggle={(next) => toggleMutation.mutate({ chunk, next })}
                      onStartEdit={() => {
                        setEditingId(chunk.id)
                        setDraftTitle(chunk.chunkTitle ?? '')
                        setDraftContent(chunk.content)
                      }}
                    />
                  ),
                )}
              </div>
            )}
          </div>
          {chunks.length > CHUNK_PAGE_SIZE && (
            <footer className="flex items-center justify-between border-t border-line px-4 py-2">
              <span className="text-xs text-zinc-400">
                第 {currentPage} / {totalPages} 页
              </span>
              <div className="flex items-center gap-1">
                <button
                  className="flex h-7 items-center gap-0.5 rounded border border-line px-2 text-xs text-zinc-600 hover:bg-field disabled:cursor-not-allowed disabled:opacity-40"
                  disabled={currentPage <= 1}
                  onClick={() => setPage(currentPage - 1)}
                >
                  <ChevronLeft size={12} />
                  上一页
                </button>
                <button
                  className="flex h-7 items-center gap-0.5 rounded border border-line px-2 text-xs text-zinc-600 hover:bg-field disabled:cursor-not-allowed disabled:opacity-40"
                  disabled={currentPage >= totalPages}
                  onClick={() => setPage(currentPage + 1)}
                >
                  下一页
                  <ChevronRight size={12} />
                </button>
              </div>
            </footer>
          )}
        </section>

        {/* 右：文档原始内容 */}
        <section className="flex min-h-[360px] flex-col border-t border-line lg:min-h-0 lg:border-t-0">
          <header className="flex items-center justify-between border-b border-line px-4 py-2">
            <span className="text-xs font-semibold text-zinc-500">文档原始内容</span>
            <button
              className="flex h-6 items-center gap-1 rounded border border-line px-2 text-xs text-zinc-500 hover:bg-field hover:text-ink disabled:opacity-50"
              onClick={() => void downloadFile()}
              disabled={downloading}
              title={`下载 ${doc.fileName}`}
            >
              <Download size={11} />
              {downloading ? '下载中…' : '下载'}
            </button>
          </header>
          <div className="min-h-0 flex-1">
            <OriginalContent
              fileExt={fileExt}
              loading={fileQuery.isPending}
              error={fileQuery.error}
              blobUrl={blobUrl}
              text={textQuery.data ?? null}
              textLoading={textQuery.isPending}
              officePreview={officePreview}
              previewUrl={previewUrl}
              previewLoading={previewQuery.isPending}
              onDownload={() => void downloadFile()}
            />
          </div>
        </section>
      </div>
    </div>
  )
}

function ChunkCard({
  chunk,
  canEdit,
  toggling,
  onToggle,
  onStartEdit,
}: {
  chunk: KnowledgeChunk
  canEdit: boolean
  toggling: boolean
  onToggle: (next: boolean) => void
  onStartEdit: () => void
}) {
  return (
    <article className={cn('rounded-md border border-line bg-panel p-3', !chunk.isUse && 'opacity-60')}>
      <header className="mb-2 flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2 text-xs text-zinc-500">
          <span className="shrink-0 font-semibold text-zinc-700">块 {chunk.docPos + 1}</span>
          <Badge className="h-5 bg-field text-zinc-500">{chunk.tokenNum} tokens</Badge>
          {!chunk.isUse && <Badge className="h-5 bg-zinc-100 text-zinc-400">已停用</Badge>}
        </div>
        {canEdit && (
          <div className="flex shrink-0 items-center gap-2">
            <button
              className="flex h-7 w-7 items-center justify-center rounded text-zinc-500 hover:bg-field hover:text-ink"
              onClick={onStartEdit}
              title="编辑分块"
            >
              <Pencil size={13} />
            </button>
            <Toggle
              checked={chunk.isUse}
              disabled={toggling}
              onChange={onToggle}
              label={chunk.isUse ? '停用该分块' : '启用该分块'}
            />
          </div>
        )}
      </header>
      {chunk.chunkTitle && <h4 className="mb-1 text-sm font-medium">{chunk.chunkTitle}</h4>}
      <p className="whitespace-pre-wrap break-all text-sm leading-6 text-zinc-700">{chunk.content}</p>
    </article>
  )
}

/** 右栏原文：按扩展名分发——PDF iframe / md 渲染 / txt 纯文本 / Office 转换 PDF 预览。 */
function OriginalContent({
  fileExt,
  loading,
  error,
  blobUrl,
  text,
  textLoading,
  officePreview,
  previewUrl,
  previewLoading,
  onDownload,
}: {
  fileExt: string
  loading: boolean
  error: unknown
  blobUrl: string | null
  text: string | null
  textLoading: boolean
  officePreview: boolean
  previewUrl: string | null
  previewLoading: boolean
  onDownload: () => void
}) {
  const center = 'flex h-full items-center justify-center p-6 text-center text-sm text-zinc-400'
  // loading/error 只对真正会拉原文的格式（pdf/md/txt）生效：Office 等格式
  // fileQuery 处于 disabled 状态，React Query v5 下 isPending 恒为 true，
  // 不拦截的话永远卡在这里，走不到下方的 officePreview 分支
  const pullsOriginal = ORIGINAL_PREVIEW_EXTS.has(fileExt)
  if (pullsOriginal && loading) return <div className={center}>加载原文中…</div>
  if (pullsOriginal && error) {
    const gone = error instanceof ApiError && error.status === 410
    return <div className={center}>{gone ? '原始文件已丢失，请重新上传' : '原文加载失败'}</div>
  }
  if (fileExt === '.pdf') {
    return blobUrl ? (
      <iframe
        src={`${blobUrl}#toolbar=0&navpanes=0&view=FitH`}
        className="h-full w-full"
        title="文档原始内容"
      />
    ) : (
      <div className={center}>加载原文中…</div>
    )
  }
  if (fileExt === '.md') {
    if (textLoading || text === null) return <div className={center}>加载原文中…</div>
    return (
      <div className="thin-scrollbar h-full overflow-y-auto p-5">
        <Markdown content={text} className="text-sm" />
      </div>
    )
  }
  if (fileExt === '.txt') {
    if (textLoading || text === null) return <div className={center}>加载原文中…</div>
    return (
      <div className="thin-scrollbar h-full overflow-y-auto p-5">
        <pre className="whitespace-pre-wrap break-all text-sm leading-6 text-zinc-700">{text}</pre>
      </div>
    )
  }
  // Office：解析时留存的转换 PDF 按 PDF 渲染；预览件缺失（未解析/老文档）降级为下载
  if (officePreview) {
    if (previewUrl) {
      return (
        <iframe
          src={`${previewUrl}#toolbar=0&navpanes=0&view=FitH`}
          className="h-full w-full"
          title="文档预览（PDF 转换件）"
        />
      )
    }
    if (previewLoading) return <div className={center}>加载预览中…</div>
    return (
      <div className={center}>
        <div>
          <FileText size={28} className="mx-auto mb-2 text-zinc-300" />
          <p className="mb-3">预览件尚未生成，重新解析该文档后自动可用</p>
          <button
            className="h-8 rounded-md border border-line px-3 text-sm text-zinc-600 hover:bg-field"
            onClick={onDownload}
          >
            下载原文件
          </button>
        </div>
      </div>
    )
  }
  // xlsx 等：浏览器无法直接渲染，降级为下载
  return (
    <div className={center}>
      <div>
        <FileText size={28} className="mx-auto mb-2 text-zinc-300" />
        <p className="mb-3">该格式暂不支持在线预览</p>
        <button
          className="h-8 rounded-md border border-line px-3 text-sm text-zinc-600 hover:bg-field"
          onClick={onDownload}
        >
          下载原文件
        </button>
      </div>
    </div>
  )
}
