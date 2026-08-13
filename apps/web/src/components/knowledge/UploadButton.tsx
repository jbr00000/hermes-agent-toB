import * as React from 'react'
import { LoaderCircle, Upload } from 'lucide-react'
import { api, ApiError } from '../../api'
import type { KnowledgeDocument } from '../../types'
import { cn } from '../ui'

const ACCEPTED_EXTS = ['.pdf', '.doc', '.docx', '.ppt', '.pptx', '.xls', '.xlsx', '.txt', '.md']

/** 上传入口：点击选文件或拖拽文件到虚线区；本地维护 上传中/失败 状态。 */
export function UploadButton({
  onUploaded,
}: {
  onUploaded: (document: KnowledgeDocument) => void
}) {
  const inputRef = React.useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = React.useState(false)
  const [dragOver, setDragOver] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  const upload = (file: File | undefined) => {
    if (!file || uploading) return
    setUploading(true)
    setError(null)
    void api
      .uploadKnowledgeDocument(file)
      .then(onUploaded)
      .catch((cause: unknown) => {
        setError(cause instanceof ApiError ? cause.message : '上传失败，请重试')
      })
      .finally(() => {
        setUploading(false)
        if (inputRef.current) inputRef.current.value = ''
      })
  }

  return (
    <div className="min-w-0">
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPTED_EXTS.join(',')}
        className="hidden"
        onChange={(event) => upload(event.target.files?.[0])}
      />
      <button
        disabled={uploading}
        onClick={() => inputRef.current?.click()}
        onDragOver={(event) => {
          event.preventDefault()
          setDragOver(true)
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(event) => {
          event.preventDefault()
          setDragOver(false)
          upload(event.dataTransfer.files?.[0])
        }}
        className={cn(
          'flex h-8 shrink-0 items-center gap-2 whitespace-nowrap rounded-md bg-ink px-3 text-sm text-white transition',
          uploading && 'opacity-60',
          dragOver && 'outline-dashed outline-2 outline-offset-2 outline-info',
        )}
        title={`支持 ${ACCEPTED_EXTS.join(' / ')}，可拖拽文件到此`}
      >
        {uploading ? <LoaderCircle size={15} className="animate-spin" /> : <Upload size={15} />}
        {uploading ? '上传中…' : '上传文档'}
      </button>
      {error && <div className="mt-1 text-xs text-danger">{error}</div>}
    </div>
  )
}
