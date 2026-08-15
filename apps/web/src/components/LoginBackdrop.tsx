import React from 'react'

/**
 * 登录页背景：深色渐变底 + 程序化"智能体网络"粒子动画。
 * 节点 = 工具/数据源，沿连线流动的光点 = Agent 的推理与调度路径。
 * 中心区域自动压低亮度/密度，避开居中的登录卡片。
 */

type Particle = { x: number; y: number; vx: number; vy: number; r: number }
type Pulse = { a: number; b: number; t: number; speed: number }

const LINK_DIST = 150
const EMERALD = '52, 211, 153' // emerald-400，暗底上比品牌绿 #237a57 更亮
const EMERALD_LIGHT = '167, 243, 208' // emerald-200，光点核心

export default function LoginBackdrop(): React.ReactElement {
  const canvasRef = React.useRef<HTMLCanvasElement | null>(null)

  React.useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    let raf = 0
    let width = 0
    let height = 0
    let particles: Particle[] = []
    const pulses: Pulse[] = []
    let lastPulseAt = -1000

    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      width = canvas.clientWidth
      height = canvas.clientHeight
      canvas.width = Math.max(1, Math.round(width * dpr))
      canvas.height = Math.max(1, Math.round(height * dpr))
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      const target = Math.round((width * height) / 13000)
      particles = Array.from({ length: target }, () => ({
        x: Math.random() * width,
        y: Math.random() * height,
        vx: (Math.random() - 0.5) * 0.25,
        vy: (Math.random() - 0.5) * 0.25,
        r: 1 + Math.random() * 1.6,
      }))
      pulses.length = 0
    }

    // 中心（登录卡片背后）淡出：0.12 = 几乎隐去，边缘 = 1
    const centerFade = (x: number, y: number): number => {
      const dx = (x - width / 2) / (width * 0.5)
      const dy = (y - height / 2) / (height * 0.5)
      const d = Math.sqrt(dx * dx + dy * dy)
      return Math.min(1, Math.max(0.12, (d - 0.34) / (0.75 - 0.34)))
    }

    const step = (now: number) => {
      ctx.clearRect(0, 0, width, height)

      for (const p of particles) {
        p.x += p.vx
        p.y += p.vy
        if (p.x < -20) p.x = width + 20
        else if (p.x > width + 20) p.x = -20
        if (p.y < -20) p.y = height + 20
        else if (p.y > height + 20) p.y = -20
      }

      // 节点连线
      for (let i = 0; i < particles.length; i++) {
        const a = particles[i]
        for (let j = i + 1; j < particles.length; j++) {
          const b = particles[j]
          const dist = Math.hypot(a.x - b.x, a.y - b.y)
          if (dist > LINK_DIST) continue
          const fade = Math.min(centerFade(a.x, a.y), centerFade(b.x, b.y))
          if (fade <= 0.13) continue
          const alpha = (1 - dist / LINK_DIST) * 0.28 * fade
          ctx.strokeStyle = `rgba(${EMERALD}, ${alpha})`
          ctx.lineWidth = 1
          ctx.beginPath()
          ctx.moveTo(a.x, a.y)
          ctx.lineTo(b.x, b.y)
          ctx.stroke()
        }
      }

      // 节点
      for (const p of particles) {
        const fade = centerFade(p.x, p.y)
        if (fade <= 0.13) continue
        ctx.fillStyle = `rgba(${EMERALD}, ${0.5 * fade})`
        ctx.beginPath()
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2)
        ctx.fill()
      }

      // 周期性派生一个沿连线流动的光点
      if (now - lastPulseAt > 500 && pulses.length < 6 && particles.length > 1) {
        for (let attempt = 0; attempt < 8; attempt++) {
          const i = Math.floor(Math.random() * particles.length)
          const a = particles[i]
          if (centerFade(a.x, a.y) < 0.5) continue
          let best = -1
          let bestDist = LINK_DIST
          for (let j = 0; j < particles.length; j++) {
            if (j === i) continue
            const d = Math.hypot(a.x - particles[j].x, a.y - particles[j].y)
            if (d < bestDist) {
              best = j
              bestDist = d
            }
          }
          if (best >= 0) {
            pulses.push({ a: i, b: best, t: 0, speed: 0.008 + Math.random() * 0.008 })
            lastPulseAt = now
            break
          }
        }
      }

      // 绘制流动光点（带辉光，路径两端淡入淡出）
      for (let i = pulses.length - 1; i >= 0; i--) {
        const pulse = pulses[i]
        pulse.t += pulse.speed
        const a = particles[pulse.a]
        const b = particles[pulse.b]
        if (pulse.t >= 1 || !a || !b) {
          pulses.splice(i, 1)
          continue
        }
        const x = a.x + (b.x - a.x) * pulse.t
        const y = a.y + (b.y - a.y) * pulse.t
        const alpha = Math.sin(pulse.t * Math.PI) * centerFade(x, y) * 0.9
        ctx.save()
        ctx.shadowColor = `rgba(${EMERALD}, 0.9)`
        ctx.shadowBlur = 12
        ctx.fillStyle = `rgba(${EMERALD_LIGHT}, ${alpha})`
        ctx.beginPath()
        ctx.arc(x, y, 2, 0, Math.PI * 2)
        ctx.fill()
        ctx.restore()
      }
    }

    const loop = (now: number) => {
      step(now)
      raf = requestAnimationFrame(loop)
    }

    resize()
    window.addEventListener('resize', resize)
    if (reduced) {
      step(0) //  prefers-reduced-motion：只画一帧静态网络
    } else {
      raf = requestAnimationFrame(loop)
    }
    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener('resize', resize)
    }
  }, [])

  return (
    <div aria-hidden="true" className="pointer-events-none absolute inset-0">
      {/* 深色渐变底：偏墨绿的黑，呼应品牌绿 */}
      <div className="absolute inset-0 bg-[radial-gradient(1200px_800px_at_70%_20%,#12251e_0%,#0b1210_45%,#070a09_100%)]" />
      <canvas ref={canvasRef} className="absolute inset-0 h-full w-full" />
      {/* 卡片背后再压一层，保证表单可读性 */}
      <div className="absolute inset-0 bg-[radial-gradient(closest-side_at_50%_50%,rgba(7,10,9,0.55),transparent)]" />
    </div>
  )
}
