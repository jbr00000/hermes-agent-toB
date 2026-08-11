import React from 'react'
import type { ChatRunSnapshot } from '../../chatRunManager'
import type { AgentRunSnapshot } from '../../agentRunManager'
import type { AgentTaskStatus } from '../../types'
import type { PetState } from './PetAvatar'

/**
 * Chat 运行 → 宠物神态。
 * Chat 是纯问答（无工具事件）：生成中 = 思考，完成 = 恍然大悟，失败/取消 = 难过。
 */
export function petStateFromChatRun(run: ChatRunSnapshot | null): PetState {
  if (!run) return 'idle'
  switch (run.status) {
    case 'connecting':
    case 'streaming':
    case 'cancelling':
      return 'thinking'
    case 'completed':
      return 'eureka'
    case 'failed':
    case 'cancelled':
      return 'sad'
  }
}

/**
 * Agent 运行 → 宠物神态。
 * 优先级：失败/取消 > 待审批（疑惑）> 执行中/工具运行（工作）> 规划中（思考）> 完成 > 待机。
 * plan 阶段流结束、计划待审批时不庆祝——那是"疑惑/等你拿主意"的时刻。
 */
export function petStateFromAgentRun(
  run: AgentRunSnapshot | null,
  taskStatus?: AgentTaskStatus,
): PetState {
  if (run) {
    if (run.status === 'failed' || run.status === 'cancelled') return 'sad'
    if (run.status === 'completed') {
      return run.taskStatus === 'awaiting_approval' ? 'confused' : 'eureka'
    }
    // connecting / streaming / cancelling
    if (run.taskStatus === 'awaiting_approval') return 'confused'
    if (run.phase === 'execute') return 'working'
    if (run.toolEvents.some((event) => event.status === 'running')) return 'working'
    return 'thinking'
  }
  // 无实时运行时按持久化任务状态兜底
  switch (taskStatus) {
    case 'awaiting_approval':
      return 'confused'
    case 'queued':
    case 'planning':
      return 'thinking'
    case 'running':
      return 'working'
    case 'failed':
    case 'cancelled':
      return 'sad'
    default:
      return 'idle'
  }
}

/** 恍然大悟展示的时长（之后自动回待机） */
export const EUREKA_HOLD_MS = 2500

/**
 * 宠物神态展示钩子：给 eureka 加 2.5s 停留。
 * 停留期间抑制回落到 idle（完成快照被 reconcile 移除时不会一闪而过），
 * 但任何新的活跃状态（thinking/working/sad…）会立即打断庆祝。
 * 注意：eureka → idle 的回落也必须靠定时器触发——target 从 eureka 变成 idle 时
 * 旧定时器会被 effect cleanup 清掉，所以 idle 抑制分支要重新排一个剩余时长的定时器。
 */
export function usePetState(target: PetState): PetState {
  const [display, setDisplay] = React.useState<PetState>(target)
  const targetRef = React.useRef(target)
  targetRef.current = target
  const holdUntilRef = React.useRef(0)

  React.useEffect(() => {
    if (target === 'eureka') {
      holdUntilRef.current = Date.now() + EUREKA_HOLD_MS
      setDisplay('eureka')
    } else if (target === 'idle' && Date.now() < holdUntilRef.current) {
      // eureka 停留期：抑制回落到 idle，下面统一排到点定时器
    } else {
      holdUntilRef.current = 0
      setDisplay(target)
      return undefined
    }
    const remaining = Math.max(0, holdUntilRef.current - Date.now())
    const timer = window.setTimeout(() => {
      holdUntilRef.current = 0
      const latest = targetRef.current
      setDisplay(latest === 'eureka' ? 'idle' : latest)
    }, remaining)
    return () => window.clearTimeout(timer)
  }, [target])

  return display
}
