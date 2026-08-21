import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api'
import type { UploadedFile, UploadOwnerType } from '../../types'

/** 与 server/uploads.py 对齐：每 owner 累计最多 5 个、单文件 ≤20MB */
export const UPLOAD_MAX_FILES = 5
export const UPLOAD_MAX_FILE_BYTES = 20 * 1024 * 1024

/** 附件列表 + 上传/删除 mutation。parsing 中的文件存在时每 1.5s 轮询一次。 */
export function useAttachments(ownerType: UploadOwnerType, ownerId: string) {
  const queryClient = useQueryClient()
  const queryKey = ['uploads', ownerType, ownerId] as const
  const query = useQuery({
    queryKey,
    queryFn: () => api.listUploads(ownerType, ownerId),
    enabled: Boolean(ownerId),
    refetchInterval: (result) =>
      result.state.data?.files.some((file) => file.parseStatus === 'parsing') ? 1500 : false,
  })
  const invalidate = () => queryClient.invalidateQueries({ queryKey })

  const upload = useMutation({
    mutationFn: (files: File[]) => api.uploadFiles(ownerType, ownerId, files),
    onSuccess: invalidate,
  })
  const remove = useMutation({
    mutationFn: (fileId: string) => api.deleteUpload(fileId),
    onSuccess: invalidate,
  })

  return {
    files: query.data?.files ?? [],
    budget: query.data?.budget ?? null,
    loading: query.isLoading,
    upload,
    remove,
    /** 选文件入口：前端预校验（≤5 个、≤20MB）后立即上传；错误经 onError 冒泡给视图 */
    addFiles: (selected: FileList | null, onError: (message: string) => void) => {
      if (!selected?.length) return
      const picked = Array.from(selected)
      const validationError = validateSelection(query.data?.files.length ?? 0, picked)
      if (validationError) {
        onError(validationError)
        return
      }
      upload.mutate(picked, {
        onError: (error) => onError(error instanceof Error ? error.message : '附件上传失败'),
      })
    },
  }
}

/** 选文件后的前端预校验（后端仍兜底）；返回错误文案或 null。 */
export function validateSelection(currentCount: number, selected: File[]): string | null {
  if (currentCount + selected.length > UPLOAD_MAX_FILES) {
    return `最多上传 ${UPLOAD_MAX_FILES} 个附件（已有 ${currentCount} 个）`
  }
  const oversized = selected.find((file) => file.size > UPLOAD_MAX_FILE_BYTES)
  if (oversized) {
    return `文件超过大小限制（${UPLOAD_MAX_FILE_BYTES / 1024 / 1024}MB）：${oversized.name}`
  }
  return null
}

/** token 数的人话格式：12,400 → "1.2 万"；980 → "980" */
export function formatTokens(count: number): string {
  return count >= 10_000 ? `${(count / 10_000).toFixed(1)} 万` : String(count)
}

/** 附件注入状态的中文短标签（chip 与消息气泡共用） */
export function attachmentStatusLabel(file: UploadedFile): string {
  if (file.parseStatus === 'parsing') return '解析中，本轮不包含'
  if (file.parseStatus === 'failed') return '解析失败'
  return `${formatTokens(file.tokenCount)} tokens`
}
