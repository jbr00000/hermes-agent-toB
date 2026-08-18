import * as React from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Database, FileWarning, LoaderCircle, Play, RotateCcw, Trash2 } from 'lucide-react'
import { api, ApiError, DuplicateUploadError } from '../../api'
import type { KnowledgeDocument, TabType } from '../../types'
import { ConfirmDialog } from '../ConfirmDialog'
import { DataTable, formatBytes, formatDateTime, PageHeader, Td, Th } from '../ui'
import { FormatIcon } from './FormatIcon'
import { StatusBadge } from './StatusBadge'
import { ACCEPTED_EXTS, UploadButton } from './UploadButton'

const ACTIVE_STATUSES = new Set(['pending', 'parsing', 'syncing'])
// 可被批量解析选中的状态：待解析（uploaded）与失败重试走 retry 接口，这里只放行 uploaded
const PARSEABLE_STATUSES = new Set(['uploaded', 'failed'])

/** 上传查重命中时挂起的文件：弹框确认后 force 重传或跳过，再继续剩余队列 */
interface PendingDuplicate {
  file: File
  matches: { name: KnowledgeDocument | null; content: KnowledgeDocument | null }
  queue: File[]
}

/** 步骤②③：某个知识库的详情。admin 上传文档（只落库不解析），勾选后批量解析；普通用户只读。 */
export function KnowledgeBaseView({
  kbId,
  isAdmin,
  onOpenTab,
}: {
  kbId: string
  isAdmin: boolean
  onOpenTab: (type: TabType, refId: string, title: string) => void
}) {
  const queryClient = useQueryClient()
  const [actionError, setActionError] = React.useState<string | null>(null)
  const [notice, setNotice] = React.useState<string | null>(null)
  const [uploading, setUploading] = React.useState(false)
  const [parsing, setParsing] = React.useState(false)
  const [dragActive, setDragActive] = React.useState(false)
  const [selected, setSelected] = React.useState<Set<string>>(new Set())
  const [deleteTarget, setDeleteTarget] = React.useState<KnowledgeDocument | null>(null)
  const [duplicate, setDuplicate] = React.useState<PendingDuplicate | null>(null)
  // dragenter/dragleave 会在子元素间抖动，用计数器判定"真的离开了放置区"
  const dragDepth = React.useRef(0)

  const baseQuery = useQuery({
    queryKey: ['knowledgeBases'],
    queryFn: () => api.listKnowledgeBases(),
    retry: false,
  })
  const base = baseQuery.data?.find((item) => item.id === kbId)

  const query = useQuery({
    queryKey: ['knowledgeDocuments', kbId],
    queryFn: () => api.listKnowledgeDocuments(undefined, kbId),
    retry: false,
    refetchInterval: (q) => {
      const docs = q.state.data?.documents ?? []
      return docs.some((doc) => ACTIVE_STATUSES.has(doc.status)) ? 3000 : false
    },
  })
  const disabled = query.error instanceof ApiError && query.error.status === 404
  const documents = query.data?.documents ?? []
  const parseableSelected = documents.filter((doc) => selected.has(doc.id) && PARSEABLE_STATUSES.has(doc.status))

  // 文档被删除后把它的选中状态一并清掉，避免残留无效 id
  React.useEffect(() => {
    setSelected((prev) => {
      const alive = new Set(documents.map((doc) => doc.id))
      const next = new Set([...prev].filter((id) => alive.has(id)))
      return next.size === prev.size ? prev : next
    })
  }, [documents])

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['knowledgeDocuments', kbId] })
    void queryClient.invalidateQueries({ queryKey: ['knowledgeBases'] })
  }
  const runAction = (action: () => Promise<unknown>) => {
    setActionError(null)
    setNotice(null)
    void action().then(invalidate).catch((cause: unknown) => {
      setActionError(cause instanceof ApiError ? cause.message : '操作失败，请重试')
    })
  }

  // 点击与拖拽共用的上传入口；多个文件顺序上传（只落库为「待解析」，不入队）。
  // 命中查重（409）时挂起当前文件与剩余队列，弹框确认后 force 重传或跳过
  const uploadFiles = (files: File[]) => {
    if (uploading || files.length === 0) return
    const supported = files.filter((file) =>
      ACCEPTED_EXTS.some((ext) => file.name.toLowerCase().endsWith(ext)),
    )
    const skipped = files.length - supported.length
    setActionError(skipped > 0 ? `${skipped} 个文件格式不支持，已跳过` : null)
    setNotice(null)
    if (supported.length === 0) return
    setUploading(true)
    void uploadSequentially(supported)
  }

  const uploadSequentially = async (files: File[], forceFirst = false) => {
    let uploaded = 0
    try {
      for (let index = 0; index < files.length; index += 1) {
        const file = files[index]
        try {
          await api.uploadKnowledgeDocument(
            kbId, file, undefined, forceFirst && index === 0 ? { force: true } : undefined,
          )
          uploaded += 1
        } catch (cause) {
          if (cause instanceof DuplicateUploadError) {
            // 挂起：弹框决定这个文件怎么办，剩余文件等确认后继续
            setDuplicate({ file, matches: cause.matches, queue: files.slice(index + 1) })
            return
          }
          throw cause
        }
      }
      if (uploaded > 0) setNotice(`已上传 ${uploaded} 个文档，勾选后点击「解析所选」开始构建`)
    } catch (cause) {
      setActionError(cause instanceof ApiError ? cause.message : '上传失败，请重试')
    } finally {
      setUploading(false)
      invalidate()
    }
  }

  // 弹框「仍要上传」：force 重传挂起的文件，然后继续剩余队列
  const resolveDuplicate = (force: boolean) => {
    if (!duplicate) return
    const { file, queue } = duplicate
    setDuplicate(null)
    if (!force) {
      // 跳过该文件：直接续传剩余
      if (queue.length > 0) {
        setUploading(true)
        void uploadSequentially(queue)
      }
      return
    }
    setUploading(true)
    void uploadSequentially([file, ...queue], true)
  }

  const parseSelected = () => {
    if (parsing || parseableSelected.length === 0) return
    setParsing(true)
    setActionError(null)
    setNotice(null)
    void api
      .parseKnowledgeDocuments(parseableSelected.map((doc) => doc.id))
      .then((result) => {
        const skipped = result.skipped.length
        setNotice(
          skipped > 0
            ? `已提交 ${result.queued.length} 个文档解析，${skipped} 个因状态不符合被跳过`
            : `已提交 ${result.queued.length} 个文档解析`,
        )
        setSelected(new Set())
      })
      .catch((cause: unknown) => {
        setActionError(cause instanceof ApiError ? cause.message : '解析提交失败，请重试')
      })
      .finally(() => {
        setParsing(false)
        invalidate()
      })
  }

  const toggleAll = (checked: boolean) => {
    setSelected(checked ? new Set(documents.filter((doc) => PARSEABLE_STATUSES.has(doc.status)).map((doc) => doc.id)) : new Set())
  }
  const allParseableSelected =
    documents.some((doc) => PARSEABLE_STATUSES.has(doc.status)) &&
    documents.filter((doc) => PARSEABLE_STATUSES.has(doc.status)).every((doc) => selected.has(doc.id))

  const canDrop = isAdmin && !disabled && !uploading

  return (
    // 外层拦截 dragover/drop 的默认行为：文件拖到放置区之外松手时，浏览器不会跳走打开文件
    <div
      className="flex h-full min-h-0 flex-col"
      onDragOver={(event) => event.preventDefault()}
      onDrop={(event) => event.preventDefault()}
    >
      <PageHeader
        icon={Database}
        title={base?.name ?? '知识库'}
        subtitle={base?.description ?? '上传文档后勾选解析，构建分块与检索索引'}
      />
      <section
        className="thin-scrollbar relative min-h-0 min-w-0 flex-1 overflow-y-auto p-5"
        onDragEnter={(event) => {
          if (!canDrop || !event.dataTransfer.types.includes('Files')) return
          event.preventDefault()
          dragDepth.current += 1
          setDragActive(true)
        }}
        onDragOver={(event) => {
          if (!dragActive) return
          event.preventDefault()
          event.dataTransfer.dropEffect = 'copy'
        }}
        onDragLeave={() => {
          if (!dragActive) return
          dragDepth.current -= 1
          if (dragDepth.current <= 0) {
            dragDepth.current = 0
            setDragActive(false)
          }
        }}
        onDrop={(event) => {
          event.preventDefault()
          event.stopPropagation()
          dragDepth.current = 0
          setDragActive(false)
          if (canDrop) uploadFiles(Array.from(event.dataTransfer.files))
        }}
      >
        {dragActive && (
          <div className="pointer-events-none absolute inset-0 z-10 m-3 flex items-center justify-center rounded-md border-2 border-dashed border-info bg-white/85">
            <div className="text-sm font-medium text-info">松开鼠标，上传到「{base?.name ?? '知识库'}」</div>
          </div>
        )}
        <div className="mb-4 flex items-start justify-between gap-3">
          <div className="flex min-w-0 items-start gap-2">
            <button
              title="返回知识库列表"
              className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded text-zinc-500 hover:bg-field hover:text-ink"
              onClick={() => onOpenTab('knowledgeBase', 'main', '知识库')}
            >
              <ArrowLeft size={15} />
            </button>
            <div className="min-w-0">
              <div className="text-sm font-semibold">文档列表</div>
              <div className="text-xs text-zinc-500">
                支持 PDF / Word / PPT / Excel / TXT / Markdown；上传后为「待解析」，勾选后批量解析入库
              </div>
            </div>
          </div>
          {isAdmin && !disabled && (
            <div className="flex shrink-0 items-center gap-2">
              <button
                disabled={parsing || parseableSelected.length === 0}
                onClick={parseSelected}
                className="flex h-8 items-center gap-2 whitespace-nowrap rounded-md bg-ink px-3 text-sm text-white transition disabled:bg-zinc-300"
              >
                {parsing ? <LoaderCircle size={15} className="animate-spin" /> : <Play size={14} />}
                解析所选{parseableSelected.length > 0 ? `（${parseableSelected.length}）` : ''}
              </button>
              <UploadButton uploading={uploading} onPick={uploadFiles} />
            </div>
          )}
        </div>
        {actionError && <div className="mb-3 text-xs text-danger">{actionError}</div>}
        {notice && <div className="mb-3 text-xs text-success">{notice}</div>}
        {disabled ? (
          <div className="border-y border-line py-8 text-center text-sm text-zinc-400">
            当前部署未启用知识库（deployment.yaml: knowledge.enabled=false）
          </div>
        ) : documents.length === 0 && !query.isPending ? (
          <div className="border-y border-line py-8 text-center text-sm text-zinc-400">
            {query.isError
              ? '知识库服务暂不可用'
              : isAdmin
                ? '还没有文档：点击右上角「上传文档」，或直接把文件拖进这个区域'
                : '还没有文档，等待管理员上传'}
          </div>
        ) : (
          <DataTable>
            <colgroup>
              {isAdmin && <col className="w-[4%]" />}
              <col className="w-[34%]" />
              <col className="w-[12%]" />
              <col className="w-[9%]" />
              <col className="w-[10%]" />
              <col className="w-[15%]" />
              {isAdmin && <col className="w-[16%]" />}
            </colgroup>
            <thead>
              <tr>
                {isAdmin && (
                  <Th>
                    <input
                      type="checkbox"
                      aria-label="全选可解析文档"
                      checked={allParseableSelected}
                      onChange={(event) => toggleAll(event.target.checked)}
                    />
                  </Th>
                )}
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
                  checked={selected.has(doc.id)}
                  onToggle={(checked) =>
                    setSelected((prev) => {
                      const next = new Set(prev)
                      if (checked) next.add(doc.id)
                      else next.delete(doc.id)
                      return next
                    })
                  }
                  onOpen={() => onOpenTab('document', doc.id, doc.title)}
                  onRetry={() => runAction(() => api.retryKnowledgeDocument(doc.id))}
                  onDelete={() => setDeleteTarget(doc)}
                />
              ))}
            </tbody>
          </DataTable>
        )}
      </section>
      {deleteTarget && (
        <ConfirmDialog
          title={`删除文档「${deleteTarget.title}」`}
          description="该文档的分块与检索索引将一并清除，不可恢复。"
          onConfirm={() => {
            const docId = deleteTarget.id
            setDeleteTarget(null)
            runAction(() => api.deleteKnowledgeDocument(docId))
          }}
          onCancel={() => setDeleteTarget(null)}
        />
      )}
      {duplicate && (
        <DuplicateDialog
          file={duplicate.file}
          matches={duplicate.matches}
          onSkip={() => resolveDuplicate(false)}
          onForce={() => resolveDuplicate(true)}
        />
      )}
    </div>
  )
}

/** 上传查重提示：同名 / 内容相同命中时给出既有文档信息，跳过或仍要上传。 */
function DuplicateDialog({
  file,
  matches,
  onSkip,
  onForce,
}: {
  file: File
  matches: PendingDuplicate['matches']
  onSkip: () => void
  onForce: () => void
}) {
  const reasons: string[] = []
  if (matches.name) {
    reasons.push(`已存在同名文档《${matches.name.title}》（${matches.name.fileName}）`)
  }
  if (matches.content) {
    reasons.push(`内容与已有文档《${matches.content.title}》（${matches.content.fileName}）完全相同`)
  }
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onSkip()
      }}
    >
      <div
        className="w-full max-w-sm rounded-md border border-line bg-panel p-5 shadow-card"
        role="alertdialog"
        aria-modal="true"
        aria-label="检测到重复文档"
      >
        <div className="flex items-start gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-amber-50 text-caution">
            <FileWarning size={17} />
          </div>
          <div>
            <div className="text-sm font-semibold">检测到重复文档</div>
            <p className="mt-1 text-sm leading-6 text-zinc-500">
              「{file.name}」{reasons.join('；')}。仍要上传会生成一篇新文档。
            </p>
          </div>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <button
            className="h-9 rounded-md border border-line px-3 text-sm hover:bg-field"
            onClick={onSkip}
          >
            跳过该文件
          </button>
          <button
            className="flex h-9 items-center gap-2 rounded-md bg-[#237a57] px-3 text-sm font-medium text-white"
            onClick={onForce}
          >
            仍要上传
          </button>
        </div>
      </div>
    </div>
  )
}

function DocumentRow({
  doc,
  isAdmin,
  checked,
  onToggle,
  onOpen,
  onRetry,
  onDelete,
}: {
  doc: KnowledgeDocument
  isAdmin: boolean
  checked: boolean
  onToggle: (checked: boolean) => void
  onOpen: () => void
  onRetry: () => void
  onDelete: () => void
}) {
  const parseable = PARSEABLE_STATUSES.has(doc.status)
  return (
    <tr className="border-b border-line last:border-0 hover:bg-[#fafafa]">
      {isAdmin && (
        <Td>
          <input
            type="checkbox"
            aria-label={`选择 ${doc.title}`}
            disabled={!parseable}
            checked={parseable && checked}
            onChange={(event) => onToggle(event.target.checked)}
          />
        </Td>
      )}
      <Td className="overflow-hidden">
        <button
          title={doc.error ? `${doc.fileName}：${doc.error}` : doc.fileName}
          className="flex w-full min-w-0 max-w-full items-center gap-2 overflow-hidden text-left font-medium hover:text-info"
          onClick={onOpen}
        >
          <FormatIcon ext={doc.fileExt} />
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
