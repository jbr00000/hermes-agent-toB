import React from 'react'
import { useQuery } from '@tanstack/react-query'
import { useAtomValue } from 'jotai'
import { useAgentRun } from '../../agentRunManager'
import { api } from '../../api'
import { useChatRun } from '../../chatRunManager'
import { petVisibleAtom } from '../../state'
import type { TabType } from '../../types'
import { PetAvatar } from './PetAvatar'
import type { PetState } from './PetAvatar'
import { PET_STATE_LABEL } from './PetAvatar'
import { petStateFromAgentRun, petStateFromChatRun, usePetState } from './petState'

/** 浮动小猫尺寸（px） */
const SIZE = 64
const STORAGE_KEY = 'cortex-pet-pos'

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
 * 主区域右下角的浮动小猫：可被用户拖拽移动，拖拽位置写入 localStorage 跨会话保留。
 * 所有标签页都显示：chat/agent 页跟随运行神态，其余功能页保持待机神态；
 * 可由顶栏开关（petVisibleAtom）整体隐藏。无标签页时不显示（EmptyWorkspace 中央已有大猫）。
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
  const [pos, setPos] = React.useState<PetPos | null>(loadPos)
  const [dragging, setDragging] = React.useState(false)
  const rootRef = React.useRef<HTMLDivElement | null>(null)
  const dragOffsetRef = React.useRef<{ dx: number; dy: number } | null>(null)

  const clamp = React.useCallback((x: number, y: number): PetPos => {
    const parent = rootRef.current?.parentElement
    const maxX = Math.max(0, (parent?.clientWidth ?? window.innerWidth) - SIZE)
    const maxY = Math.max(0, (parent?.clientHeight ?? window.innerHeight) - SIZE)
    return { x: Math.min(Math.max(0, x), maxX), y: Math.min(Math.max(0, y), maxY) }
  }, [])

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
    event.currentTarget.setPointerCapture(event.pointerId)
    setDragging(true)
  }

  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>): void => {
    const offset = dragOffsetRef.current
    if (!offset) return
    const point = toParentCoords(event.clientX - offset.dx, event.clientY - offset.dy)
    setPos(clamp(point.x, point.y))
  }

  const endDrag = (): void => {
    if (!dragOffsetRef.current) return
    dragOffsetRef.current = null
    setDragging(false)
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
      className={`absolute z-30 touch-none select-none ${dragging ? 'cursor-grabbing' : 'cursor-grab'}`}
      style={style}
      title={`小猫 · ${PET_STATE_LABEL[state]}（可拖拽）`}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
    >
      <PetAvatar state={state} size={SIZE} />
    </div>
  )
}
