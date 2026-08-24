import React from 'react'
import { useAtom } from 'jotai'
import { Cat } from 'lucide-react'
import {
  petIdleAnimAtom,
  petSizeAtom,
  petSkinAtom,
  petTaskAnimAtom,
  petVisibleAtom,
} from '../../state'
import type { PetSkin } from '../../types'
import { cn, Toggle } from '../ui'
import { PET_SKIN_LABEL, PET_STATE_LABEL, PetSkinAvatar } from './PetAvatar'
import type { PetState } from './PetAvatar'

const SKINS: PetSkin[] = ['cat', 'niulai']
const STATES: PetState[] = ['idle', 'thinking', 'working', 'confused', 'eureka', 'sad']

/** 各神态的触发时机说明（状态预览区展示） */
const STATE_TRIGGER: Record<PetState, string> = {
  idle: '无任务运行时的默认神态',
  thinking: '对话流式生成中 / Agent 规划中',
  working: 'Agent 正在调用工具执行',
  confused: 'Agent 计划待你审批',
  eureka: '任务完成（停留约 2.5 秒）',
  sad: '任务失败或被取消',
}

const SIZE_MIN = 40
const SIZE_MAX = 96

function Row({ label, hint, control }: { label: string; hint?: string; control: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 py-1.5">
      <div className="min-w-0">
        <div className="text-xs font-medium text-ink">{label}</div>
        {hint && <div className="mt-0.5 text-[11px] leading-4 text-zinc-500">{hint}</div>}
      </div>
      {control}
    </div>
  )
}

/**
 * 顶栏桌宠入口：点击展开管理面板（显示开关 / 形象 / 大小 / 动效开关 / 状态预览）。
 * 全部设置走 atomWithStorage，localStorage 持久化、跨标签页即时生效。
 */
export function PetSettingsButton(): React.ReactElement {
  const [open, setOpen] = React.useState(false)
  const [previewState, setPreviewState] = React.useState<PetState>('idle')
  const [visible, setVisible] = useAtom(petVisibleAtom)
  const [skin, setSkin] = useAtom(petSkinAtom)
  const [size, setSize] = useAtom(petSizeAtom)
  const [idleAnim, setIdleAnim] = useAtom(petIdleAnimAtom)
  const [taskAnim, setTaskAnim] = useAtom(petTaskAnimAtom)
  const rootRef = React.useRef<HTMLDivElement | null>(null)

  // 点击面板外 / Escape 关闭
  React.useEffect(() => {
    if (!open) return
    const onPointerDown = (event: PointerEvent): void => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false)
    }
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  return (
    <div ref={rootRef} className="relative">
      <button
        title="桌宠设置"
        className={cn(
          'flex h-8 w-8 items-center justify-center rounded-md border border-line hover:bg-field',
          visible ? 'text-ink' : 'text-zinc-400',
          open && 'bg-field',
        )}
        onClick={() => setOpen((v) => !v)}
      >
        <Cat size={15} />
      </button>

      {open && (
        <div className="absolute right-0 top-10 z-50 w-72 rounded-lg border border-line bg-panel p-3 shadow-lg">
          <div className="mb-1 text-xs font-semibold text-ink">桌宠</div>

          <Row
            label="显示桌宠"
            control={<Toggle checked={visible} onChange={setVisible} label="显示桌宠" />}
          />

          <div className="py-1.5">
            <div className="mb-1.5 text-xs font-medium text-ink">形象</div>
            <div className="grid grid-cols-2 gap-2">
              {SKINS.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => setSkin(s)}
                  className={cn(
                    'flex flex-col items-center gap-1 rounded-md border p-2 hover:bg-field',
                    skin === s ? 'border-ink bg-field' : 'border-line',
                  )}
                >
                  <span className="flex h-11 items-end justify-center">
                    <PetSkinAvatar skin={s} state="idle" size={40} animated={false} />
                  </span>
                  <span className="text-[11px] text-ink">{PET_SKIN_LABEL[s]}</span>
                </button>
              ))}
            </div>
          </div>

          <Row
            label={`大小 · ${size}px`}
            control={
              <input
                type="range"
                min={SIZE_MIN}
                max={SIZE_MAX}
                step={8}
                value={size}
                onChange={(event) => setSize(Number(event.target.value))}
                className="w-24 accent-zinc-700"
                aria-label="桌宠大小"
              />
            }
          />
          <Row
            label="待机微动效"
            hint="关闭后待机时完全静止（不呼吸、不走动、不打瞌睡）"
            control={<Toggle checked={idleAnim} onChange={setIdleAnim} label="待机微动效" />}
          />
          <Row
            label="任务动画"
            hint="关闭后不随运行状态变换神态"
            control={<Toggle checked={taskAnim} onChange={setTaskAnim} label="任务动画" />}
          />

          <div className="border-t border-line pt-2">
            <div className="mb-1.5 text-xs font-medium text-ink">状态预览</div>
            <div className="mb-2 flex flex-wrap gap-1">
              {STATES.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => setPreviewState(s)}
                  className={cn(
                    'rounded-md border px-1.5 py-0.5 text-[11px] hover:bg-field',
                    previewState === s ? 'border-ink bg-field text-ink' : 'border-line text-zinc-500',
                  )}
                >
                  {PET_STATE_LABEL[s]}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-3 rounded-md bg-field px-2 py-1.5">
              <span className="flex h-14 w-14 shrink-0 items-end justify-center">
                <PetSkinAvatar skin={skin} state={previewState} size={52} />
              </span>
              <span className="text-[11px] leading-4 text-zinc-500">{STATE_TRIGGER[previewState]}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
