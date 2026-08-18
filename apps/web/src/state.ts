import { atom } from 'jotai'
import { atomWithStorage } from 'jotai/utils'
import type { AttachedFile, KnowledgeSearchMode, WorkspaceMode, WorkTab } from './types'

export const tabsAtom = atom<WorkTab[]>([])
export const activeTabIdAtom = atom<string | null>(null)
export const workspaceModeAtom = atomWithStorage<WorkspaceMode>('hermes-workspace-mode', 'agent', undefined, { getOnInit: true })
/** 浮动小猫显隐开关（顶栏切换，localStorage 持久化） */
export const petVisibleAtom = atomWithStorage<boolean>('hermes-pet-visible', true, undefined, { getOnInit: true })
/** 知识库问答模式开关（chat 输入框；localStorage 持久化） */
export const knowledgeQaEnabledAtom = atomWithStorage<boolean>('hermes-knowledge-qa-enabled', false, undefined, { getOnInit: true })
/** 知识库问答的选库限定：null = 全部知识库 */
export const knowledgeQaKbIdAtom = atomWithStorage<string | null>('hermes-knowledge-qa-kb', null, undefined, { getOnInit: true })
/** 知识库问答的检索模式：fast 快速（默认）/ precise 精准（轻量模型改写，更慢更准） */
export const knowledgeQaSearchModeAtom = atomWithStorage<KnowledgeSearchMode>('hermes-knowledge-qa-search-mode', 'fast', undefined, { getOnInit: true })
export const selectedSpaceAtom = atom<string>('rail')
/** 附件按会话/任务隔离（key = refId）：全局共享会在标签间串扰。
 *  注意：附件当前仅本地暂存，不参与问答/执行（上传通道未接入）。 */
export const attachedFilesAtom = atom<Record<string, AttachedFile[]>>({})
export const chatAttachedFilesAtom = atom<Record<string, AttachedFile[]>>({})

export function tabId(ownerId: string, type: WorkTab['type'], refId: string): string {
  return `${ownerId}:${type}:${refId}`
}

export function createTab(
  ownerId: string,
  type: WorkTab['type'],
  refId: string,
  title: string,
  order: number,
): WorkTab {
  return {
    id: tabId(ownerId, type, refId),
    ownerId,
    type,
    title,
    refId,
    order,
    updatedAt: Date.now(),
  }
}
