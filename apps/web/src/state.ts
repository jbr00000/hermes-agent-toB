import { atom } from 'jotai'
import type { PermissionMode, WorkTab } from './types'

export const tabsAtom = atom<WorkTab[]>([])
export const activeTabIdAtom = atom<string | null>(null)
export const permissionModeAtom = atom<PermissionMode>('read')
export const selectedSpaceAtom = atom<string>('rail')
export const attachedFilesAtom = atom<Array<{ id: string; name: string; size: number; status: 'ready' | 'parsing' }>>([
  { id: 'f-demo-1', name: '智库平台-软件开发费用测算V0.2.xlsx', size: 2_480_000, status: 'ready' },
])

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
