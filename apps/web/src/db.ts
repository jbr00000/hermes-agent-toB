import Dexie, { type Table } from 'dexie'
import type { WorkTab } from './types'

export interface PersistedTabUiState {
  tabId: string
  rightPanelOpen?: boolean
  rightPanelTab?: 'files' | 'knowledge' | 'plan' | 'permissions'
  selectedSpaceId?: string
  selectedKnowledgeBaseIds?: string[]
  attachedFileRefs?: string[]
  draft?: string
  updatedAt: number
}

class HermesWebDb extends Dexie {
  tabs!: Table<WorkTab, string>
  tabUiState!: Table<PersistedTabUiState, string>

  constructor() {
    super('hermes-tob-web')
    this.version(1).stores({
      tabs: 'id, type, refId, order, updatedAt',
      tabUiState: 'tabId, updatedAt',
    })
  }
}

export const db = new HermesWebDb()
