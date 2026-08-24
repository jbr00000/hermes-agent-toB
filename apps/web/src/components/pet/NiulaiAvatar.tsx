import React from 'react'
import armLUrl from './assets/niulai-armL.png'
import armRUrl from './assets/niulai-armR.png'
import bodyUrl from './assets/niulai-body.png'
import headUrl from './assets/niulai-head.png'
import { NIULAI_PUPPET } from './assets/niulai_puppet_meta'
import type { PetState } from './PetAvatar'
import { PET_STATE_LABEL } from './PetAvatar'

interface Pivot {
  originX: number
  originY: number
}

function pivotStyle(pivot: Pivot | null): React.CSSProperties {
  // 图层均为全画布尺寸，枢轴是画布百分比（scripts/build_pet_puppet.py 生成）
  return pivot ? { transformOrigin: `${pivot.originX}% ${pivot.originY}%` } : {}
}

/**
 * 牛来桌宠：分层木偶（身体/双臂/头四个透明图层叠加）+ CSS 程序化动画。
 * 图层与枢轴由 scripts/build_pet_puppet.py 从整图立绘自动拆出；
 * 每层独立 transform（歪头、摆臂、垂臂），比整图晃动精细。神态语义与铅笔小猫一致：
 * - idle      呼吸 + 头轻摆 + 双臂微晃
 * - thinking  歪头 + 思绪气泡
 * - working   身体摇摆 + 双臂交替摆动 + 头点动
 * - confused  头部快速摇摆 + ?
 * - eureka    整体弹跳 + 头后仰 + 双臂举起 + 闪光
 * - sad       垂头 + 双臂下垂 + 褪色 + 泪滴
 * 走动时（外壳 .pet-walking）双臂反相摆动、头部颠簸；瞌睡时（.pet-sleeping）头低垂。
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
  const width = Math.round(size * NIULAI_PUPPET.aspect)
  const layerProps = { width, height: size, draggable: false, alt: '' } as const
  return (
    <span
      className={`niulai n-${state}${animated ? ' niulai-anim' : ''}${className ? ` ${className}` : ''}`}
      style={{ width, height }}
      role="img"
      aria-label={`牛来桌宠：${PET_STATE_LABEL[state]}`}
    >
      {/* key=state：换神态重挂，动画从头播放。叠层顺序：身体 → 双臂 → 头 */}
      <span key={state} className="n-rig">
        <img className="n-layer n-body" src={bodyUrl} {...layerProps} />
        {NIULAI_PUPPET.armL && (
          <img className="n-layer n-arm-l" src={armLUrl} style={pivotStyle(NIULAI_PUPPET.armL)} {...layerProps} />
        )}
        {NIULAI_PUPPET.armR && (
          <img className="n-layer n-arm-r" src={armRUrl} style={pivotStyle(NIULAI_PUPPET.armR)} {...layerProps} />
        )}
        <img className="n-layer n-head" src={headUrl} style={pivotStyle(NIULAI_PUPPET.head)} {...layerProps} />
      </span>
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
