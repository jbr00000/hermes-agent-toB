import React from 'react'
import { useQuery } from '@tanstack/react-query'
import { useAtomValue } from 'jotai'
import { useAgentRun } from '../../agentRunManager'
import { api } from '../../api'
import { useChatRun } from '../../chatRunManager'
import { petIdleAnimAtom, petSizeAtom, petSkinAtom, petTaskAnimAtom, petVisibleAtom } from '../../state'
import type { TabType } from '../../types'
import { PetCharacter } from './PetAvatar'
import type { PetState } from './PetAvatar'
import { PET_SKIN_LABEL, PET_STATE_LABEL } from './PetAvatar'
import { petStateFromAgentRun, petStateFromChatRun, usePetState } from './petState'

const STORAGE_KEY = 'cortex-pet-pos'

/** 指针位移小于该阈值视为点击（播放点击反应），否则是拖拽 */
const CLICK_SLOP_PX = 6

interface PetPos {
  x: number
  y: number
}

function loadPos(): PetPos | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed: unknown = JSON.parse(raw)
    if (
      typeof parsed === 'object' && parsed !== null
      && typeof (parsed as PetPos).x === 'number'
      && typeof (parsed as PetPos).y === 'number'
    ) {
      return parsed as PetPos
    }
  } catch {
    /* localStorage 不可用时退回默认位置 */
  }
  return null
}

/**
 * 主区域右下角的浮动桌宠：可被用户拖拽移动，拖拽位置写入 localStorage 跨会话保留。
 * 所有标签页都显示：chat/agent 页跟随运行神态（可被「任务动画」开关关掉），
 * 其余功能页保持待机神态；形象 / 尺寸 / 动效开关都在顶栏桌宠设置面板里调。
 * 可由顶栏开关（petVisibleAtom）整体隐藏。无标签页时不显示（EmptyWorkspace 中央已有大桌宠）。
 * 注意：需挂在 position:relative 的容器内（absolute 定位基于父容器）。
 */
export function PetFloat({ tab }: { tab: { type: TabType; refId: string } | null }): React.ReactElement | null {
  const visible = useAtomValue(petVisibleAtom)
  if (!visible || !tab) return null
  if (tab.type === 'chat') return <ChatPetFloat sessionId={tab.refId} />
  if (tab.type === 'agent') return <AgentPetFloat taskId={tab.refId} />
  // 无运行状态的功能页（知识库/记忆/用户/审计等）：恒定待机，无需 usePetState 的 eureka 停留
  return <PetFloatShell state="idle" />
}

function ChatPetFloat({ sessionId }: { sessionId: string }): React.ReactElement {
  const run = useChatRun(sessionId)
  return <PetFloatShell state={usePetState(petStateFromChatRun(run))} />
}

function AgentPetFloat({ taskId }: { taskId: string }): React.ReactElement {
  const run = useAgentRun(taskId)
  // 与 AgentView 共用 ['task', id] 缓存：实时运行快照消失后，
  // 用持久化的任务状态兜底（待审批→疑惑、失败→难过）
  const taskQuery = useQuery({ queryKey: ['task', taskId], queryFn: () => api.getTask(taskId) })
  return <PetFloatShell state={usePetState(petStateFromAgentRun(run, taskQuery.data?.status))} />
}

function PetFloatShell({ state }: { state: PetState }): React.ReactElement {
  const size = useAtomValue(petSizeAtom)
  const skin = useAtomValue(petSkinAtom)
  const taskAnim = useAtomValue(petTaskAnimAtom)
  const idleAnim = useAtomValue(petIdleAnimAtom)
  // 任务动画关闭：恒定待机；待机微动效只决定 idle 时动不动（任务神态保持有动画）
  const effState = taskAnim ? state : 'idle'
  const animated = effState === 'idle' ? idleAnim : true

  const [pos, setPos] = React.useState<PetPos | null>(loadPos)
  const [dragging, setDragging] = React.useState(false)
  const [clicked, setClicked] = React.useState(false)
  const rootRef = React.useRef<HTMLDivElement | null>(null)
  const dragOffsetRef = React.useRef<{ dx: number; dy: number } | null>(null)
  const downPointRef = React.useRef<PetPos | null>(null)
  const clickTimerRef = React.useRef<number | null>(null)

  const clamp = React.useCallback((x: number, y: number): PetPos => {
    const parent = rootRef.current?.parentElement
    const maxX = Math.max(0, (parent?.clientWidth ?? window.innerWidth) - size)
    const maxY = Math.max(0, (parent?.clientHeight ?? window.innerHeight) - size)
    return { x: Math.min(Math.max(0, x), maxX), y: Math.min(Math.max(0, y), maxY) }
  }, [size])

  // localStorage 恢复的位置可能来自更宽/更高的旧布局；容器变小后 main 的
  // overflow-hidden 会把小猫整个裁掉且无法拖回。挂载后与窗口 resize 时
  // 都把位置收敛回当前容器内。
  React.useEffect(() => {
    const reclamp = (): void => {
      setPos((current) => {
        if (!current) return current
        const next = clamp(current.x, current.y)
        return next.x === current.x && next.y === current.y ? current : next
      })
    }
    reclamp()
    window.addEventListener('resize', reclamp)
    return () => window.removeEventListener('resize', reclamp)
  }, [clamp])

  const toParentCoords = (clientX: number, clientY: number): PetPos => {
    const parent = rootRef.current?.parentElement
    if (!parent) return { x: clientX, y: clientY }
    const rect = parent.getBoundingClientRect()
    return { x: clientX - rect.left, y: clientY - rect.top }
  }

  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>): void => {
    event.preventDefault()
    const rect = event.currentTarget.getBoundingClientRect()
    dragOffsetRef.current = { dx: event.clientX - rect.left, dy: event.clientY - rect.top }
    downPointRef.current = { x: event.clientX, y: event.clientY }
    event.currentTarget.setPointerCapture(event.pointerId)
    setDragging(true)
  }

  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>): void => {
    const offset = dragOffsetRef.current
    if (!offset) return
    const point = toParentCoords(event.clientX - offset.dx, event.clientY - offset.dy)
    setPos(clamp(point.x, point.y))
  }

  const endDrag = (event: React.PointerEvent<HTMLDivElement>): void => {
    if (!dragOffsetRef.current) return
    dragOffsetRef.current = null
    setDragging(false)
    // 位移很小 = 点击：播放一次点击反应（挤压弹跳），不移动位置
    const down = downPointRef.current
    downPointRef.current = null
    if (
      down
      && Math.abs(event.clientX - down.x) < CLICK_SLOP_PX
      && Math.abs(event.clientY - down.y) < CLICK_SLOP_PX
    ) {
      if (clickTimerRef.current !== null) window.clearTimeout(clickTimerRef.current)
      setClicked(true)
      clickTimerRef.current = window.setTimeout(() => setClicked(false), 480)
      return
    }
    setPos((current) => {
      if (current) {
        try {
          window.localStorage.setItem(STORAGE_KEY, JSON.stringify(current))
        } catch {
          /* 忽略持久化失败 */
        }
      }
      return current
    })
  }

  const style: React.CSSProperties = pos
    ? { left: pos.x, top: pos.y }
    : { right: 20, bottom: 120 }

  return (
    <div
      ref={rootRef}
      className={`absolute z-30 touch-none select-none ${dragging ? 'cursor-grabbing' : 'cursor-grab'}${clicked ? ' pet-click' : ''}`}
      style={style}
      title={`${PET_SKIN_LABEL[skin]} · ${PET_STATE_LABEL[effState]}（可拖拽，点击有反应）`}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
    >
      <PetCharacter state={effState} size={size} animated={animated} />
    </div>
  )
}
