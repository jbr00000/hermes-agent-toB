import React from 'react'
import niulaiUrl from './assets/niulai.png'
import type { PetState } from './PetAvatar'
import { PET_STATE_LABEL } from './PetAvatar'

/** 立绘原始比例（362×512 抠图），高瘦 */
const ASPECT = 362 / 512

/**
 * 牛来桌宠：抠图立绘 + CSS 程序化动画。神态语义与铅笔小猫一致（PetState 不变），
 * 动画全部是 transform/filter 微动效，配合少量字符符号（? / ✦ / 泪滴）做情绪点缀。
 * - idle      呼吸浮动
 * - thinking  歪头 + 思绪气泡
 * - working   左右摇摆
 * - confused  快速抖动 + ?
 * - eureka    弹跳 + 闪光
 * - sad       低垂褪色 + 泪滴
 */
export function NiulaiAvatar({
  state,
  size = 64,
  animated = true,
  className,
}: {
  state: PetState
  size?: number
  animated?: boolean
  className?: string
}): React.ReactElement {
  const height = size
  const width = Math.round(size * ASPECT)
  return (
    <span
      className={`niulai n-${state}${animated ? ' niulai-anim' : ''}${className ? ` ${className}` : ''}`}
      style={{ width, height }}
      role="img"
      aria-label={`牛来桌宠：${PET_STATE_LABEL[state]}`}
    >
      {/* key=state：换神态重挂，动画从头播放 */}
      <img key={state} src={niulaiUrl} width={width} height={height} draggable={false} alt="" />
      {state === 'thinking' && (
        <span className="n-dots" aria-hidden>
          <i />
          <i />
          <i />
        </span>
      )}
      {state === 'confused' && (
        <span className="n-q" aria-hidden>
          ?
        </span>
      )}
      {state === 'eureka' && (
        <>
          <span className="n-sparkle s1" aria-hidden>✦</span>
          <span className="n-sparkle s2" aria-hidden>✦</span>
          <span className="n-sparkle s3" aria-hidden>✦</span>
        </>
      )}
      {state === 'sad' && <span className="n-tear" aria-hidden />}
    </span>
  )
}
