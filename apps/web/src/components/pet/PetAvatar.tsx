import React from 'react'
import './pet.css'

/** 宠物神态：待机 / 思考 / 工作 / 疑惑 / 恍然大悟 / 难过 */
export type PetState = 'idle' | 'thinking' | 'working' | 'confused' | 'eureka' | 'sad'

export const PET_STATE_LABEL: Record<PetState, string> = {
  idle: '待机',
  thinking: '思考',
  working: '工作',
  confused: '疑惑',
  eureka: '恍然大悟',
  sad: '难过',
}

/* ================= 铅笔稿调色板 ================= */
const C = {
  line: '#46433e', // 石墨主线
  soft: '#8a857c', // 浅稿线（胡须 / 速度线）
  paper: '#FEFDF9', // 纸白填充
  shade: '#e9e4d8', // 铅笔淡影（内耳等）
  eye: '#33302c',
  yarn: '#2C8A63', // 毛线球：唯一彩色
  yarnDark: '#1B5F44',
  yarnSoft: '#EDF5F0',
} as const

function ellipsePath(cx: number, cy: number, rx: number, ry: number): string {
  return `M${cx - rx} ${cy} a${rx} ${ry} 0 1 0 ${2 * rx} 0 a${rx} ${ry} 0 1 0 ${-2 * rx} 0`
}

/** 铅笔轮廓：主线 + 一条轻微偏移的起草复线 */
function rough(
  d: string,
  { fill = C.paper, stroke = C.line, w = 1.8, dbl = true }: {
    fill?: string
    stroke?: string
    w?: number
    dbl?: boolean
  } = {},
): React.ReactElement {
  return (
    <>
      <path
        d={d}
        fill={fill}
        stroke={stroke}
        strokeWidth={w}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {dbl && (
        <path
          d={d}
          fill="none"
          stroke={stroke}
          strokeWidth={w * 0.75}
          opacity="0.22"
          transform="translate(0.9 0.7)"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      )}
    </>
  )
}

/* ================= 部件 ================= */
function Tail(): React.ReactElement {
  return (
    <g className="tail">
      {rough('M86 102 C 102 99, 110 87, 104 74 C 102 70, 97 71, 97 75 C 101 86, 96 94, 84 96 Z')}
    </g>
  )
}

function Body({ pose, hatchId }: { pose: PetState; hatchId: string }): React.ReactElement {
  return (
    <g className={`breathe ${pose === 'sad' ? 'sigh' : ''}`}>
      {rough('M32 112 C 30 86, 40 70, 60 70 C 80 70, 90 86, 88 112 Z')}
      <path
        d={ellipsePath(60, 97, 15, 13.5)}
        fill={`url(#${hatchId})`}
        stroke={C.soft}
        strokeWidth="1"
      />
      <path
        d="M36 84 q 5 3 1 8 M84 84 q -5 3 -1 8"
        stroke={C.soft}
        strokeWidth="2"
        strokeLinecap="round"
        fill="none"
        opacity="0.8"
      />
    </g>
  )
}

function Ears({ pose }: { pose: PetState }): React.ReactElement {
  const left = (transform?: string) => (
    <g transform={transform}>
      {rough('M40 30 L 35 8 L 58 19 Z', { dbl: false })}
      <path d="M42.5 26.5 L 39.5 13 L 53.5 20.5 Z" fill={C.shade} stroke={C.soft} strokeWidth="1" />
    </g>
  )
  const right = (transform?: string) => (
    <g transform={transform}>
      {rough('M80 30 L 85 8 L 62 19 Z', { dbl: false })}
      <path d="M77.5 26.5 L 80.5 13 L 66.5 20.5 Z" fill={C.shade} stroke={C.soft} strokeWidth="1" />
    </g>
  )
  if (pose === 'confused') {
    // 右耳半耷
    return <>{left()}{right('rotate(48 76 22)')}</>
  }
  if (pose === 'sad') {
    // 专用下垂耳：沿头侧挂下来
    return (
      <>
        {rough('M46 24 C 33 27, 26 38, 30 48 C 38 46, 45 37, 49 28 Z', { dbl: false })}
        {rough('M74 24 C 87 27, 94 38, 90 48 C 82 46, 75 37, 71 28 Z', { dbl: false })}
        <path
          d="M43 30 C 37 33, 33 39, 34.5 44 M77 30 C 83 33, 87 39, 85.5 44"
          stroke={C.soft}
          strokeWidth="1.2"
          fill="none"
        />
      </>
    )
  }
  return <>{left()}{right()}</>
}

function Eyes({ pose }: { pose: PetState }): React.ReactElement {
  const open = (dx = 0, dy = 0) => (
    <g className="eyes-open">
      <circle cx={49 + dx} cy={42 + dy} r="4.2" fill={C.eye} />
      <circle cx={71 + dx} cy={42 + dy} r="4.2" fill={C.eye} />
      <circle cx={50.4 + dx} cy={40.6 + dy} r="1.3" fill="#fff" />
      <circle cx={72.4 + dx} cy={40.6 + dy} r="1.3" fill="#fff" />
    </g>
  )
  switch (pose) {
    case 'idle': // 满足地闭眼（⌒⌒）
      return (
        <path
          d="M44 43 Q 49.5 37.5 55 43 M66 43 Q 71.5 37.5 77 43"
          stroke={C.eye}
          strokeWidth="2.6"
          strokeLinecap="round"
          fill="none"
        />
      )
    case 'thinking': // 眼珠看左上
      return open(-2.2, -2.2)
    case 'working': // 盯着球看
      return open(0, 1.8)
    case 'confused': // 左眼正常，右眼眯起
      return (
        <g className="eyes-open">
          <circle cx="49" cy="42" r="4.2" fill={C.eye} />
          <circle cx="50.4" cy="40.6" r="1.3" fill="#fff" />
          <ellipse cx="71" cy="43" rx="4.2" ry="2.1" fill={C.eye} />
        </g>
      )
    case 'eureka': {
      // 星星眼（线稿小星）
      const star = (cx: number, cy: number) => (
        <path
          key={`${cx}-${cy}`}
          d={`M${cx} ${cy - 6.5} L ${cx + 1.9} ${cy - 1.9} L ${cx + 6.5} ${cy} L ${cx + 1.9} ${cy + 1.9} L ${cx} ${cy + 6.5} L ${cx - 1.9} ${cy + 1.9} L ${cx - 6.5} ${cy} L ${cx - 1.9} ${cy - 1.9} Z`}
          fill={C.paper}
          stroke={C.eye}
          strokeWidth="1.6"
          strokeLinejoin="round"
        />
      )
      return <>{star(49, 42)}{star(71, 42)}</>
    }
    case 'sad': // 下垂眼 + 愁眉 + 泪滴
      return (
        <>
          <circle cx="49" cy="44" r="3.4" fill={C.eye} />
          <circle cx="71" cy="44" r="3.4" fill={C.eye} />
          <path
            d="M43.5 36.5 Q 49 33.5 54 36 M66 36 Q 71 33.5 76.5 36.5"
            stroke={C.eye}
            strokeWidth="2"
            strokeLinecap="round"
            fill="none"
          />
          <path
            className="tear"
            d="M75.5 49 q 2.6 3.6 0 5.6 q -2.6 -2 0 -5.6"
            fill={C.paper}
            stroke={C.line}
            strokeWidth="1.4"
          />
        </>
      )
    default:
      return open()
  }
}

function Mouth({ pose }: { pose: PetState }): React.ReactElement {
  const nose = <path d="M57 51 h6 l-3 4.5 z" fill={C.eye} />
  const mStroke = {
    stroke: C.eye,
    strokeWidth: 1.8,
    strokeLinecap: 'round' as const,
    fill: 'none',
  }
  switch (pose) {
    case 'eureka': // 张嘴笑
      return (
        <>
          {nose}
          <path
            d="M53.5 57 Q 60 66 66.5 57 Q 60 60 53.5 57 Z"
            fill={C.paper}
            stroke={C.eye}
            strokeWidth="1.6"
          />
        </>
      )
    case 'sad': // 撇嘴
      return <>{nose}<path d="M55 60.5 Q 60 57 65 60.5" {...mStroke} /></>
    case 'confused': // 波浪嘴
      return <>{nose}<path d="M53.5 58 q 3.2 -2.4 6.5 0 q 3.2 2.4 6.5 0" {...mStroke} /></>
    default: // 标准 ω 嘴
      return <>{nose}<path d="M60 55.5 Q 60 59.5 55.5 59.5 M60 55.5 Q 60 59.5 64.5 59.5" {...mStroke} /></>
  }
}

function Whiskers(): React.ReactElement {
  return (
    <path
      d="M31 47 L 15 43 M31 52 L 14 52 M89 47 L 105 43 M89 52 L 106 52"
      stroke={C.soft}
      strokeWidth="1.3"
      strokeLinecap="round"
      fill="none"
    />
  )
}

function Head({ pose }: { pose: PetState }): React.ReactElement {
  return (
    <g className="head">
      <Ears pose={pose} />
      {rough(ellipsePath(60, 42, 27, 23))}
      <path
        d="M51 22.5 v7 M60 20.5 v8 M69 22.5 v7"
        stroke={C.line}
        strokeWidth="2"
        strokeLinecap="round"
        opacity="0.55"
      />
      <Eyes pose={pose} />
      <Mouth pose={pose} />
      <Whiskers />
    </g>
  )
}

/** 毛线球：浅绿纸面 + 绿色缠绕线 + 一根溜出来的线头 */
function Ball({
  cx,
  cy,
  r,
  spin = false,
  stringTo = null,
}: {
  cx: number
  cy: number
  r: number
  spin?: boolean
  stringTo?: [number, number] | null
}): React.ReactElement {
  return (
    <>
      {stringTo && (
        <path
          d={`M${stringTo[0]} ${stringTo[1]} q -7 5 -14 1 q -6 -3.4 -12 0.6`}
          stroke={C.yarnDark}
          strokeWidth="2"
          strokeLinecap="round"
          fill="none"
        />
      )}
      <g className={spin ? 'ball-spin' : ''}>
        {rough(ellipsePath(cx, cy, r, r), { fill: C.yarnSoft, stroke: C.yarnDark, w: 2 })}
        <path
          d={`M${cx - r * 0.88} ${cy - r * 0.18} Q ${cx} ${cy - r * 0.62} ${cx + r * 0.88} ${cy - r * 0.18}
              M${cx - r * 0.88} ${cy + r * 0.3} Q ${cx} ${cy + r * 0.75} ${cx + r * 0.88} ${cy + r * 0.3}
              M${cx - r * 0.15} ${cy - r * 0.88} Q ${cx + r * 0.36} ${cy} ${cx - r * 0.15} ${cy + r * 0.88}`}
          stroke={C.yarn}
          strokeWidth="2"
          fill="none"
          strokeLinecap="round"
        />
        <path
          d={`M${cx - r * 0.5} ${cy + r * 0.9} Q ${cx + r * 0.4} ${cy + r * 0.55} ${cx + r * 1.1} ${cy + r * 0.6}`}
          stroke={C.yarn}
          strokeWidth="1.6"
          fill="none"
          strokeLinecap="round"
          opacity="0.7"
        />
      </g>
    </>
  )
}

/** 毛线球层（画在身体之后、头之前） */
function BallLayer({ pose }: { pose: PetState }): React.ReactElement | null {
  switch (pose) {
    case 'idle':
      return <Ball cx={60} cy={99} r={11} />
    case 'thinking':
      return <Ball cx={60} cy={101} r={10} />
    case 'working':
      return <Ball cx={60} cy={99} r={12} spin />
    case 'confused':
      return <Ball cx={94} cy={98} r={9.5} stringTo={[85, 101]} />
    case 'eureka':
      // 球垂直抛过头顶 + 两侧速度线
      return (
        <>
          <path
            d="M48 14 l -5 -3 M72 14 l 5 -3"
            stroke={C.soft}
            strokeWidth="2"
            strokeLinecap="round"
            fill="none"
          />
          <g className="ball-bounce"><Ball cx={60} cy={9} r={8.5} /></g>
        </>
      )
    case 'sad':
      // 散线球瘪在一边，线拖了一地
      return (
        <>
          <path
            d="M86 105 q -8 5 -16 1.5 q -7 -3.5 -14 0.5"
            stroke={C.yarnDark}
            strokeWidth="2.5"
            strokeLinecap="round"
            fill="none"
          />
          <g transform="scale(1 0.88) translate(0 13)"><Ball cx={95} cy={100} r={8.5} /></g>
        </>
      )
    default:
      return null
  }
}

/** 前爪层（画在头之后） */
function FrontPaws({ pose }: { pose: PetState }): React.ReactElement | null {
  const paw = (cx: number, cy: number, cls = '', rot = 0, rx = 6.5, ry = 4.2, key?: string) => (
    <g key={key ?? `${cx}-${cy}`} transform={rot ? `rotate(${rot} ${cx} ${cy})` : undefined} className={cls}>
      {rough(ellipsePath(cx, cy, rx, ry), { dbl: false })}
    </g>
  )
  switch (pose) {
    case 'idle': // 搭在球上
      return <>{paw(51, 95.5)}{paw(69, 95.5)}</>
    case 'thinking': // 一手托腮 + 思绪泡泡
      return (
        <>
          {paw(51, 106)}
          {paw(73.5, 63.5, 'paw-tap', -20, 5.8, 5, 'chin')}
          <g className="dots" fill={C.soft}>
            <circle cx="26" cy="20" r="2.2" />
            <circle cx="32" cy="13" r="2.7" />
            <circle cx="39" cy="7" r="3.2" />
          </g>
        </>
      )
    case 'working': // 双爪滚球
      return <>{paw(52, 91, 'paw-left')}{paw(68, 91, 'paw-right')}</>
    case 'confused':
      return <>{paw(51, 106)}{paw(69, 106)}</>
    case 'eureka': // 双爪举起 + 线稿闪光
      return (
        <>
          {paw(35, 59, '', -25)}
          {paw(85, 59, '', 25)}
          <path className="sparkle" d="M28 32 l1.8 4.2 4.2 1.8 -4.2 1.8 -1.8 4.2 -1.8 -4.2 -4.2 -1.8 4.2 -1.8 z" fill={C.paper} stroke={C.line} strokeWidth="1.4" />
          <path className="sparkle s2" d="M93 28 l1.5 3.5 3.5 1.5 -3.5 1.5 -1.5 3.5 -1.5 -3.5 -3.5 -1.5 3.5 -1.5 z" fill={C.paper} stroke={C.line} strokeWidth="1.3" />
          <path className="sparkle s3" d="M88 50 l1.2 2.8 2.8 1.2 -2.8 1.2 -1.2 2.8 -1.2 -2.8 -2.8 -1.2 2.8 -1.2 z" fill={C.paper} stroke={C.line} strokeWidth="1.2" />
        </>
      )
    case 'sad': // 趴平
      return <>{paw(49, 107, '', 0, 6.5, 3.8)}{paw(71, 107, '', 0, 6.5, 3.8)}</>
    default:
      return null
  }
}

function Extras({ pose }: { pose: PetState }): React.ReactElement | null {
  if (pose !== 'confused') return null
  return (
    <text
      className="q-mark"
      x="95"
      y="18"
      fontSize="20"
      fontWeight="700"
      fill={C.line}
      fontFamily="'Comic Sans MS', 'Segoe Print', cursive"
    >
      ?
    </text>
  )
}

/**
 * 铅笔草图风小猫。纯展示组件：给定神态和尺寸，渲染对应姿态 + CSS 微动效。
 * - size < 40 时自动关闭抖边滤镜（小尺寸会糊边）
 * - 每个实例的滤镜/排线 defs 用 useId 隔离，可同页多实例
 */
export function PetAvatar({
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
  const uid = React.useId().replace(/[^a-zA-Z0-9]/g, '')
  const filterId = `petRough${uid}`
  const hatchId = `petHatch${uid}`
  const useFilter = size >= 40
  return (
    <svg
      className={`pet p-${state}${animated ? ' pet-anim' : ''}${className ? ` ${className}` : ''}`}
      viewBox="0 0 120 120"
      width={size}
      height={size}
      role="img"
      aria-label={`Cortex 宠物：${PET_STATE_LABEL[state]}`}
    >
      <defs>
        {useFilter && (
          <filter id={filterId} x="-8%" y="-8%" width="116%" height="116%">
            <feTurbulence type="fractalNoise" baseFrequency="0.04" numOctaves="2" seed="11" result="n" />
            <feDisplacementMap in="SourceGraphic" in2="n" scale="2.4" />
          </filter>
        )}
        <pattern id={hatchId} width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2="5" stroke={C.line} strokeWidth="0.8" opacity="0.28" />
        </pattern>
      </defs>
      <g filter={useFilter ? `url(#${filterId})` : undefined}>
        {/* key=state：换神态时重挂子树，触发 pet-pose-in 淡入过渡 */}
        <g key={state} className="pet-pose">
          <Tail />
          <Body pose={state} hatchId={hatchId} />
          <BallLayer pose={state} />
          <Head pose={state} />
          <FrontPaws pose={state} />
          <Extras pose={state} />
        </g>
      </g>
    </svg>
  )
}
