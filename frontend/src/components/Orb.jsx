import { useEffect, useRef } from 'react'

/**
 * The centrepiece (product.md §4). The orb IS the status display — there is no status text.
 *
 *   idle       slow breathing, dim              — tap to start a call
 *   listening  ripples driven by the real mic level
 *   thinking   tight double pulse               — between final transcript and first audio
 *   speaking   concentric waveform rings
 *
 * One canvas, one requestAnimationFrame loop, no dependencies. Everything is drawn from a
 * single `phase` counter and the live level, so states cross-fade instead of snapping:
 * `energy` chases its target every frame rather than jumping to it.
 *
 * Timeboxed on purpose (roadmap: UI polish is not the deep dive). It has to look
 * intentional, not win awards.
 */

const ACCENT = [240, 180, 41] // --color-amber

const rgba = (alpha, [r, g, b] = ACCENT) => `rgba(${r}, ${g}, ${b}, ${alpha})`

export default function Orb({ state = 'idle', getLevel, onTap, disabled = false }) {
  const canvasRef = useRef(null)
  // The loop reads these through a ref so it survives re-renders, and it pulls the mic
  // level itself rather than taking it as a prop — otherwise the whole app would have to
  // re-render sixty times a second just to animate one circle.
  const live = useRef({ state, getLevel })
  live.current.state = state
  live.current.getLevel = getLevel

  useEffect(() => {
    const canvas = canvasRef.current
    const context = canvas.getContext('2d')
    let frame = 0
    let phase = 0
    let energy = 0

    const resize = () => {
      const ratio = window.devicePixelRatio || 1
      const size = canvas.clientWidth
      canvas.width = size * ratio
      canvas.height = size * ratio
      context.setTransform(ratio, 0, 0, ratio, 0, 0)
    }
    resize()
    window.addEventListener('resize', resize)

    const draw = () => {
      const size = canvas.clientWidth
      const cx = size / 2
      const cy = size / 2
      const unit = size / 2
      const current = live.current.state
      const micLevel = current === 'listening' ? (live.current.getLevel?.() ?? 0) : 0

      phase += 0.016

      // Where this state wants the orb's energy to sit, 0..1.
      const target =
        current === 'listening' ? 0.25 + micLevel * 0.75
        : current === 'speaking' ? 0.55 + Math.abs(Math.sin(phase * 4)) * 0.45
        : current === 'thinking' ? 0.45
        : 0.12
      energy += (target - energy) * 0.12

      context.clearRect(0, 0, size, size)

      // Breathing is always present; thinking beats twice as fast and twice as deep.
      const beat = current === 'thinking' ? Math.sin(phase * 6) : Math.sin(phase * 1.4)
      const core = unit * (0.3 + energy * 0.1 + beat * (current === 'thinking' ? 0.035 : 0.02))

      // --- outer ripples: listening reacts to the room, speaking to its own rhythm ---
      if (current === 'listening' || current === 'speaking') {
        const rings = current === 'speaking' ? 4 : 3
        for (let i = 0; i < rings; i += 1) {
          // Each ring is offset in the cycle, so they chase each other outward.
          const cycle = (phase * (current === 'speaking' ? 0.9 : 0.55) + i / rings) % 1
          const radius = core + cycle * unit * (0.45 + energy * 0.35)
          const fade = (1 - cycle) * energy * 0.55
          if (fade <= 0.01) continue
          context.beginPath()
          context.arc(cx, cy, radius, 0, Math.PI * 2)
          context.strokeStyle = rgba(fade)
          context.lineWidth = 1 + energy * 1.5
          context.stroke()
        }
      }

      // --- the glow ---
      const glow = context.createRadialGradient(cx, cy, core * 0.2, cx, cy, core * 2.6)
      glow.addColorStop(0, rgba(0.32 + energy * 0.38))
      glow.addColorStop(0.5, rgba(0.1 + energy * 0.14))
      glow.addColorStop(1, rgba(0))
      context.fillStyle = glow
      context.beginPath()
      context.arc(cx, cy, core * 2.6, 0, Math.PI * 2)
      context.fill()

      // --- the core ---
      const body = context.createRadialGradient(
        cx - core * 0.3, cy - core * 0.35, core * 0.1, cx, cy, core,
      )
      body.addColorStop(0, rgba(0.95 + energy * 0.05, [255, 232, 170]))
      body.addColorStop(0.55, rgba(0.6 + energy * 0.3))
      body.addColorStop(1, rgba(0.25 + energy * 0.25, [180, 120, 20]))
      context.fillStyle = body
      context.beginPath()
      context.arc(cx, cy, core, 0, Math.PI * 2)
      context.fill()

      // --- speaking: a waveform band across the core, the classic "voice" read ---
      if (current === 'speaking') {
        context.save()
        context.beginPath()
        context.arc(cx, cy, core * 0.94, 0, Math.PI * 2)
        context.clip()
        context.beginPath()
        for (let x = -core; x <= core; x += 2) {
          const t = x / core
          // Two detuned sines: regular enough to read as a waveform, irregular enough not
          // to look like a test pattern.
          const wave =
            Math.sin(t * 7 + phase * 9) * 0.5 + Math.sin(t * 13 - phase * 6) * 0.28
          const envelope = Math.cos((t * Math.PI) / 2) // taper at the clipped edges
          const y = wave * envelope * core * 0.42 * energy
          if (x === -core) context.moveTo(cx + x, cy + y)
          else context.lineTo(cx + x, cy + y)
        }
        context.strokeStyle = rgba(0.85, [255, 240, 200])
        context.lineWidth = 2
        context.stroke()
        context.restore()
      }

      // --- idle: a thin ring, so the orb reads as a button before anything happens ---
      if (current === 'idle') {
        context.beginPath()
        context.arc(cx, cy, core * 1.5, 0, Math.PI * 2)
        context.strokeStyle = rgba(0.16)
        context.lineWidth = 1
        context.stroke()
      }

      frame = requestAnimationFrame(draw)
    }

    frame = requestAnimationFrame(draw)
    return () => {
      cancelAnimationFrame(frame)
      window.removeEventListener('resize', resize)
    }
  }, [])

  const label =
    state === 'idle' ? 'اضغط للكلام · Tap to talk' : 'إنهاء المكالمة · Tap to hang up'

  return (
    <button
      type="button"
      onClick={onTap}
      disabled={disabled}
      aria-label={label}
      title={label}
      className="group relative aspect-square w-56 rounded-full transition-transform duration-200 hover:scale-[1.03] focus-visible:ring-2 focus-visible:ring-amber/60 focus-visible:outline-none active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50 sm:w-64"
    >
      <canvas ref={canvasRef} className="h-full w-full" />
    </button>
  )
}
