import { atom } from 'jotai'
import { atomWithStorage } from 'jotai/utils'
import type { AttachedFile, PermissionMode, WorkspaceMode, WorkTab } from './types'

export const tabsAtom = atom<WorkTab[]>([])
export const activeTabIdAtom = atom<string | null>(null)
export const workspaceModeAtom = atomWithStorage<WorkspaceMode>('hermes-workspace-mode', 'agent', undefined, { getOnInit: true })
export const permissionModeAtom = atom<PermissionMode>('read')
export const selectedSpaceAtom = atom<string>('rail')
export const attachedFilesAtom = atom<AttachedFile[]>([
  { id: 'f-demo-1', name: '智库平台-软件开发费用测算V0.2.xlsx', size: 2_480_000, status: 'ready' },
])
export const chatAttachedFilesAtom = atom<AttachedFile[]>([])

export function createTab(type: WorkTab['type'], refId: string, title: string, order: number): WorkTab {
  return {
    id: `${type}:${refId}`,
    type,
    title,
    refId,
    order,
    updatedAt: Date.now(),
  }
}
