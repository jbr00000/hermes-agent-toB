import * as React from 'react'
import { LoaderCircle, Upload } from 'lucide-react'
import { cn } from '../ui'

export const ACCEPTED_EXTS = ['.pdf', '.doc', '.docx', '.ppt', '.pptx', '.xls', '.xlsx', '.txt', '.md']

/** 上传入口按钮：点击选文件（可多选）。拖拽上传由 KnowledgeBaseView 的整区放置区处理。 */
export function UploadButton({
  uploading,
  onPick,
}: {
  uploading: boolean
  onPick: (files: File[]) => void
}) {
  const inputRef = React.useRef<HTMLInputElement>(null)

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={ACCEPTED_EXTS.join(',')}
        className="hidden"
        onChange={(event) => {
          onPick(Array.from(event.target.files ?? []))
          if (inputRef.current) inputRef.current.value = ''
        }}
      />
      <button
        disabled={uploading}
        onClick={() => inputRef.current?.click()}
        className={cn(
          'flex h-8 shrink-0 items-center gap-2 whitespace-nowrap rounded-md bg-ink px-3 text-sm text-white transition',
          uploading && 'opacity-60',
        )}
        title={`支持 ${ACCEPTED_EXTS.join(' / ')}；也可以直接把文件拖进文档列表区域`}
      >
        {uploading ? <LoaderCircle size={15} className="animate-spin" /> : <Upload size={15} />}
        {uploading ? '上传中…' : '上传文档'}
      </button>
    </>
  )
}
