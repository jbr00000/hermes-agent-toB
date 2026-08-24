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

/** 两次点击间隔小于该值视为双击（md F-03：双击播放更激动的一次性反应） */
const DOUBLE_CLICK_MS = 300

/** 待机自主行为节拍（md F-06：每 5s 掷一次随机行为） */
const BEHAVIOR_TICK_MS = 5000
/** 每个节拍触发走动的概率（md：60% 不动 / ~25% 走动 / 其余自然进入瞌睡） */
const WALK_CHANCE = 0.35
/** 无交互超过该时长进入瞌睡（md 4.2：60s 无操作 → SLEEP，点击唤醒） */
const SLEEP_AFTER_MS = 60_000

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
  // 自主行为（仅待机时）：走动方向（左走时立绘水平翻转）/ 瞌睡
  const [walkDir, setWalkDir] = React.useState<'left' | 'right' | null>(null)
  const [sleeping, setSleeping] = React.useState(false)
  const rootRef = React.useRef<HTMLDivElement | null>(null)
  const dragOffsetRef = React.useRef<{ dx: number; dy: number } | null>(null)
  const downPointRef = React.useRef<PetPos | null>(null)
  const walkRafRef = React.useRef<number | null>(null)
  const sleepingRef = React.useRef(false)
  const interactRef = React.useRef(Date.now())
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

  const persistPos = (next: PetPos): void => {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
    } catch {
      /* 忽略持久化失败 */
    }
  }

  // 点击反应用 WAAPI 直接播：每次点击独立触发（类切换在连点时不会重启动画），
  // 也不受桌宠自身 idle/任务动画的 class 覆盖影响
  const playReaction = (kind: 'single' | 'double'): void => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const el = rootRef.current?.firstElementChild
    if (!el) return
    if (kind === 'single') {
      // 单击：雀跃一下——压扁蓄力 → 跳起歪头 → 落地回弹
      el.animate(
        [
          { transform: 'scale(1, 1) translateY(0) rotate(0deg)' },
          { transform: 'scale(0.9, 1.08) translateY(-9%) rotate(-5deg)', offset: 0.3 },
          { transform: 'scale(1.07, 0.93) translateY(0) rotate(4deg)', offset: 0.55 },
          { transform: 'scale(0.98, 1.02) translateY(-4%) rotate(-2deg)', offset: 0.78 },
          { transform: 'scale(1, 1) translateY(0) rotate(0deg)' },
        ],
        { duration: 620, easing: 'ease-out' },
      )
      return
    }
    // 双击（md F-03 / 4.2：双击 → CLICK，播完自动回 IDLE）：兴奋地连跳两下
    el.animate(
      [
        { transform: 'scale(1, 1) translateY(0) rotate(0deg)' },
        { transform: 'scale(0.92, 1.1) translateY(-14%) rotate(-6deg)', offset: 0.18 },
        { transform: 'scale(1.08, 0.92) translateY(0) rotate(5deg)', offset: 0.36 },
        { transform: 'scale(0.95, 1.06) translateY(-10%) rotate(-4deg)', offset: 0.55 },
        { transform: 'scale(1.05, 0.95) translateY(0) rotate(3deg)', offset: 0.72 },
        { transform: 'scale(1.01, 0.99) translateY(-2%) rotate(-1deg)', offset: 0.88 },
        { transform: 'scale(1, 1) translateY(0) rotate(0deg)' },
      ],
      { duration: 900, easing: 'ease-out' },
    )
  }

  // 单击/双击区分（md F-03）：第一次点击延迟一拍，窗口内再来一次则升级为双击反应
  const onPetClick = (): void => {
    if (clickTimerRef.current !== null) {
      window.clearTimeout(clickTimerRef.current)
      clickTimerRef.current = null
      playReaction('double')
      return
    }
    clickTimerRef.current = window.setTimeout(() => {
      clickTimerRef.current = null
      playReaction('single')
    }, DOUBLE_CLICK_MS)
  }

  // 拖拽放下（真的移动了位置）：松了一口气——pet-relief 类驱动的多层一次性动画
  // （落地一瘫回弹 + 双臂垂落 + 低头叹气再抬头）。类切换可能撞上连续拖拽，
  // 先移除再在下一帧加回，强制重启动画
  const [relief, setRelief] = React.useState(false)
  const reliefTimerRef = React.useRef<number | null>(null)
  const playRelief = (): void => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    if (reliefTimerRef.current !== null) window.clearTimeout(reliefTimerRef.current)
    setRelief(false)
    requestAnimationFrame(() => {
      setRelief(true)
      reliefTimerRef.current = window.setTimeout(() => {
        reliefTimerRef.current = null
        setRelief(false)
      }, 950)
    })
  }

  const stopWalk = React.useCallback((): void => {
    if (walkRafRef.current !== null) {
      cancelAnimationFrame(walkRafRef.current)
      walkRafRef.current = null
    }
    setWalkDir(null)
  }, [])

  const wake = React.useCallback((): void => {
    interactRef.current = Date.now()
    if (sleepingRef.current) {
      sleepingRef.current = false
      setSleeping(false)
    }
  }, [])

  // 待机自主走动（md F-06 _do_walk：随机方向 3–8 步 × 每步 ~10px， clamp 在容器内）。
  // 位置用 rAF 逐帧推进（直接写 pos，天然与拖拽/收敛同一套坐标，不会出现
  // WAAPI fill-forwards 提交时的闪跳）；左走时给立绘加水平翻转 class。
  const startWalk = React.useCallback((): void => {
    const el = rootRef.current
    const parent = el?.parentElement
    if (!el || !parent || walkRafRef.current !== null || dragOffsetRef.current) return
    const prect = parent.getBoundingClientRect()
    const rect = el.getBoundingClientRect()
    const base = { x: rect.left - prect.left, y: rect.top - prect.top }
    const maxX = Math.max(0, prect.width - rect.width)
    let dir = Math.random() < 0.5 ? -1 : 1
    if (dir > 0 && base.x > maxX - 90) dir = -1
    if (dir < 0 && base.x < 90) dir = 1
    const steps = 3 + Math.floor(Math.random() * 6)
    const stepPx = 10 + Math.random() * 8
    const target = Math.min(Math.max(0, base.x + dir * steps * stepPx), maxX)
    const dist = target - base.x
    if (Math.abs(dist) < 24) return
    // pos 可能还是 null（CSS right/bottom 锚定）：先换算成数值坐标，视觉上原地不动
    setPos(base)
    setWalkDir(dist < 0 ? 'left' : 'right')
    const duration = Math.abs(dist) * 30  // 步速匹配 0.5s 迈步节拍，快了会像飘着滑
    const t0 = performance.now()
    const tick = (now: number): void => {
      if (walkRafRef.current === null) return
      if (dragOffsetRef.current) {
        walkRafRef.current = null
        setWalkDir(null)
        return
      }
      const t = Math.min(1, (now - t0) / duration)
      const eased = t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2
      setPos({ x: base.x + dist * eased, y: base.y })
      if (t < 1) {
        walkRafRef.current = requestAnimationFrame(tick)
        return
      }
      walkRafRef.current = null
      setWalkDir(null)
      persistPos({ x: base.x + dist, y: base.y })
    }
    walkRafRef.current = requestAnimationFrame(tick)
  }, [])

  // 自主行为节拍（md F-06 _random_behavior）：仅待机且待机微动效开启时。
  // 60s 无交互进入瞌睡；瞌睡中不再走动；否则按概率触发走动。
  React.useEffect(() => {
    if (effState !== 'idle' || !idleAnim) return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const timer = window.setInterval(() => {
      if (Date.now() - interactRef.current > SLEEP_AFTER_MS) {
        if (!sleepingRef.current) {
          sleepingRef.current = true
          setSleeping(true)
        }
        return
      }
      if (sleepingRef.current) return
      if (Math.random() < WALK_CHANCE) startWalk()
    }, BEHAVIOR_TICK_MS)
    return () => window.clearInterval(timer)
  }, [effState, idleAnim, startWalk])

  // 任务活动视为交互：切回待机后重新计 60s；离开待机/卸载时收掉走动与瞌睡
  React.useEffect(() => {
    interactRef.current = Date.now()
    if (effState !== 'idle') {
      stopWalk()
      sleepingRef.current = false
      setSleeping(false)
    }
  }, [effState, stopWalk])
  React.useEffect(
    () => () => {
      if (walkRafRef.current !== null) cancelAnimationFrame(walkRafRef.current)
      if (clickTimerRef.current !== null) window.clearTimeout(clickTimerRef.current)
      if (reliefTimerRef.current !== null) window.clearTimeout(reliefTimerRef.current)
    },
    [],
  )

  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>): void => {
    event.preventDefault()
    wake()
    stopWalk()
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
    // 位移很小 = 点击：播放点击反应（单/双击由 onPetClick 区分），不移动位置
    const down = downPointRef.current
    downPointRef.current = null
    if (
      down
      && Math.abs(event.clientX - down.x) < CLICK_SLOP_PX
      && Math.abs(event.clientY - down.y) < CLICK_SLOP_PX
    ) {
      onPetClick()
      return
    }
    playRelief()  // 拖拽后放下：松了一口气
    setPos((current) => {
      if (current) persistPos(current)
      return current
    })
  }

  const style: React.CSSProperties = pos
    ? { left: pos.x, top: pos.y }
    : { right: 20, bottom: 120 }

  const behaviorClass = [
    dragging ? 'pet-dragging cursor-grabbing' : 'cursor-grab',
    walkDir ? `pet-walking${walkDir === 'left' ? ' pet-face-left' : ''}` : '',
    sleeping ? 'pet-sleeping' : '',
    relief && !dragging ? 'pet-relief' : '',
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div
      ref={rootRef}
      className={`pet-float absolute z-30 touch-none select-none ${behaviorClass}`}
      style={style}
      title={`${PET_SKIN_LABEL[skin]} · ${PET_STATE_LABEL[effState]}（可拖拽，单击/双击有反应）`}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
    >
      <span className="pet-stage">
        <PetCharacter state={effState} size={size} animated={animated} />
      </span>
      {sleeping && (
        <span className="pet-zzz" aria-hidden>
          <i>z</i>
          <i>z</i>
          <i>z</i>
        </span>
      )}
    </div>
  )
}
