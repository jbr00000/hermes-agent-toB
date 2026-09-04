import interactionAtlasUrl from './assets/cat-interactions.webp'
import standardAtlasUrl from './assets/cat-spritesheet.webp'
import { SpritePetAvatar } from './SpritePetAvatar'
import type { SpriteSpec } from './SpritePetAvatar'
import { PET_STATE_LABEL, PET_WALK_CYCLE_MS } from './pet-model'
import type { PetActivity, PetState } from './pet-model'

const STANDARD_ROWS = 11
const INTERACTION_ROWS = 3

const STATE_SPRITES: Record<PetState, SpriteSpec> = {
  idle: { atlas: standardAtlasUrl, rows: STANDARD_ROWS, row: 0, frames: 6, frameMs: 420 },
  thinking: { atlas: standardAtlasUrl, rows: STANDARD_ROWS, row: 8, frames: 6, frameMs: 360, posterFrame: 3 },
  working: { atlas: standardAtlasUrl, rows: STANDARD_ROWS, row: 7, frames: 6, frameMs: 180, posterFrame: 2 },
  confused: { atlas: standardAtlasUrl, rows: STANDARD_ROWS, row: 6, frames: 6, frameMs: 300, posterFrame: 3 },
  eureka: { atlas: standardAtlasUrl, rows: STANDARD_ROWS, row: 4, frames: 5, frameMs: 150, posterFrame: 2 },
  sad: { atlas: standardAtlasUrl, rows: STANDARD_ROWS, row: 5, frames: 8, frameMs: 300, loop: false, posterFrame: 6 },
}

const ACTIVITY_SPRITES: Record<PetActivity, SpriteSpec> = {
  'walk-right': { atlas: standardAtlasUrl, rows: STANDARD_ROWS, row: 1, frames: 8, frameMs: PET_WALK_CYCLE_MS / 8 },
  'walk-left': { atlas: standardAtlasUrl, rows: STANDARD_ROWS, row: 2, frames: 8, frameMs: PET_WALK_CYCLE_MS / 8 },
  waving: { atlas: standardAtlasUrl, rows: STANDARD_ROWS, row: 3, frames: 4, frameMs: 170, loop: false },
  jumping: { atlas: standardAtlasUrl, rows: STANDARD_ROWS, row: 4, frames: 5, frameMs: 150, loop: false },
  dragging: { atlas: interactionAtlasUrl, rows: INTERACTION_ROWS, row: 0, frames: 6, frameMs: 120 },
  relief: { atlas: interactionAtlasUrl, rows: INTERACTION_ROWS, row: 1, frames: 6, frameMs: 145, loop: false },
  sleeping: { atlas: interactionAtlasUrl, rows: INTERACTION_ROWS, row: 2, frames: 6, frameMs: 460 },
}

export interface CatAvatarProps {
  state: PetState
  size?: number
  animated?: boolean
  className?: string
  activity?: PetActivity
}

/** Pencil cat rendered from complete whole-body frames. */
export function CatAvatar({
  state,
  size = 64,
  animated = true,
  className,
  activity,
}: CatAvatarProps): React.ReactElement {
  const spec = activity ? ACTIVITY_SPRITES[activity] : STATE_SPRITES[state]
  const poseClassName = activity ? `c-${activity}` : `c-${state}`

  return (
    <SpritePetAvatar
      spec={spec}
      size={size}
      animated={animated}
      spriteClassName="pet-sprite pencil-cat"
      animatedClassName="pencil-cat-anim"
      poseClassName={poseClassName}
      className={className}
      ariaLabel={`铅笔小猫桌宠：${PET_STATE_LABEL[state]}`}
    />
  )
}
