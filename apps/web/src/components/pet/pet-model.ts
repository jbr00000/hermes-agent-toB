export type PetState = 'idle' | 'thinking' | 'working' | 'confused' | 'eureka' | 'sad'

export type PetActivity =
  | 'walk-right'
  | 'walk-left'
  | 'waving'
  | 'jumping'
  | 'dragging'
  | 'relief'
  | 'sleeping'

export const PET_WALK_CYCLE_MS = 720

export const PET_STATE_LABEL: Record<PetState, string> = {
  idle: '待机',
  thinking: '思考',
  working: '工作',
  confused: '疑惑',
  eureka: '恍然大悟',
  sad: '难过',
}
