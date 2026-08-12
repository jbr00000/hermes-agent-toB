import { atom } from 'jotai'
import { atomWithStorage } from 'jotai/utils'
import type { AttachedFile, WorkspaceMode, WorkTab } from './types'

export const tabsAtom = atom<WorkTab[]>([])
export const activeTabIdAtom = atom<string | null>(null)
export const workspaceModeAtom = atomWithStorage<WorkspaceMode>('hermes-workspace-mode', 'agent', undefined, { getOnInit: true })
/** 浮动小猫显隐开关（顶栏切换，localStorage 持久化） */
export const petVisibleAtom = atomWithStorage<boolean>('hermes-pet-visible', true, undefined, { getOnInit: true })
export const selectedSpaceAtom = atom<string>('rail')
export const attachedFilesAtom = atom<AttachedFile[]>([])
export const chatAttachedFilesAtom = atom<AttachedFile[]>([])

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
