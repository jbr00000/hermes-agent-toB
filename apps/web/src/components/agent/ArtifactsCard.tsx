import * as React from 'react'
import { useQuery } from '@tanstack/react-query'
import { Download, FileCheck2, FileText, LoaderCircle } from 'lucide-react'
import { api } from '../../api'
import type { TaskArtifact } from '../../types'
import { formatBytes } from '../ui'

/** 交付文件卡片：列出任务沙箱工作区里的产物，点击经 fetch+blob 下载（JWT 鉴权）。 */
export function ArtifactsCard({ taskId, active }: { taskId: string; active: boolean }) {
  const [error, setError] = React.useState<string | null>(null)
  const [downloading, setDownloading] = React.useState<string | null>(null)
  const query = useQuery({
    queryKey: ['taskArtifacts', taskId],
    queryFn: () => api.listTaskArtifacts(taskId),
    // 运行中轮询，产物边生成边出现；停止后做一次收尾刷新
    refetchInterval: active ? 2000 : false,
  })
  const wasActiveRef = React.useRef(active)
  React.useEffect(() => {
    if (wasActiveRef.current && !active) void query.refetch()
    wasActiveRef.current = active
  }, [active, query])

  const artifacts = query.data ?? []
  if (artifacts.length === 0 && !error) return null

  const download = (artifact: TaskArtifact) => {
    setError(null)
    setDownloading(artifact.path)
    void api.downloadTaskArtifact(taskId, artifact.path, artifact.name)
      .catch((cause) => setError(cause instanceof Error ? cause.message : '下载失败'))
      .finally(() => setDownloading(null))
  }

  return (
    <section className="rounded-md border border-line bg-[#fcfcfd] px-4 py-3">
      <div className="mb-2 flex items-center gap-2 text-sm font-semibold">
        <FileCheck2 size={15} />
        交付文件
      </div>
      <div className="divide-y divide-line border-y border-line">
        {artifacts.map((artifact) => (
          <div key={artifact.path} className="flex min-w-0 items-center gap-2 py-2.5 text-sm">
            <FileText size={15} className="shrink-0 text-zinc-400" />
            <div className="min-w-0 flex-1">
              <div className="truncate">{artifact.name}</div>
              <div className="mt-0.5 text-xs text-zinc-500">
                {artifact.path.includes('/') && <span className="mr-2">{artifact.path}</span>}
                {formatBytes(artifact.sizeBytes)}
              </div>
            </div>
            <button
              type="button"
              title={`下载 ${artifact.name}`}
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-line text-zinc-500 hover:bg-field hover:text-ink disabled:opacity-40"
              disabled={downloading !== null}
              onClick={() => download(artifact)}
            >
              {downloading === artifact.path
                ? <LoaderCircle size={14} className="animate-spin" />
                : <Download size={14} />}
            </button>
          </div>
        ))}
      </div>
      {error && (
        <div className="mt-2 border-l-2 border-danger bg-red-50 px-3 py-2 text-xs text-danger">
          {error}
        </div>
      )}
    </section>
  )
}
