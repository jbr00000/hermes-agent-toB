import React from 'react'

export interface SpriteSpec {
  atlas: string
  rows: number
  row: number
  frames: number
  frameMs: number
  loop?: boolean
  posterFrame?: number
}

interface SpritePetAvatarProps {
  spec: SpriteSpec
  size: number
  animated: boolean
  spriteClassName: string
  animatedClassName: string
  poseClassName: string
  ariaLabel: string
  className?: string
  cellWidth?: number
  cellHeight?: number
  columns?: number
  children?: React.ReactNode
}

function useReducedMotion(): boolean {
  const [reduced, setReduced] = React.useState(false)

  React.useEffect(() => {
    const query = window.matchMedia('(prefers-reduced-motion: reduce)')
    const update = (): void => setReduced(query.matches)
    update()
    query.addEventListener('change', update)
    return () => query.removeEventListener('change', update)
  }, [])

  return reduced
}

/** Shared whole-body sprite renderer. Pet modules only own state-to-row configuration. */
export function SpritePetAvatar({
  spec,
  size,
  animated,
  spriteClassName,
  animatedClassName,
  poseClassName,
  ariaLabel,
  className,
  cellWidth = 192,
  cellHeight = 208,
  columns = 8,
  children,
}: SpritePetAvatarProps): React.ReactElement {
  const reducedMotion = useReducedMotion()
  const shouldAnimate = animated && !reducedMotion && size >= 24
  const [frame, setFrame] = React.useState(shouldAnimate ? 0 : (spec.posterFrame ?? 0))

  React.useEffect(() => {
    setFrame(shouldAnimate ? 0 : (spec.posterFrame ?? 0))
    if (!shouldAnimate || spec.frames <= 1) return

    const timer = window.setInterval(() => {
      setFrame((current) => {
        if (current < spec.frames - 1) return current + 1
        return spec.loop === false ? current : 0
      })
    }, spec.frameMs)

    return () => window.clearInterval(timer)
  }, [shouldAnimate, spec])

  const width = Math.round(size * cellWidth / cellHeight)
  const visibleFrame = Math.min(frame, spec.frames - 1)
  const x = (visibleFrame / (columns - 1)) * 100
  const y = spec.rows === 1 ? 0 : (spec.row / (spec.rows - 1)) * 100

  return (
    <span
      className={`${spriteClassName} ${poseClassName}${shouldAnimate ? ` ${animatedClassName}` : ''}${className ? ` ${className}` : ''}`}
      style={{
        width,
        height: size,
        backgroundImage: `url(${spec.atlas})`,
        backgroundPosition: `${x}% ${y}%`,
        backgroundSize: `${columns * 100}% ${spec.rows * 100}%`,
      }}
      role="img"
      aria-label={ariaLabel}
    >
      {children}
    </span>
  )
}
