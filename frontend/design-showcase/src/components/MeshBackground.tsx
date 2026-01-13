import { useEffect, useRef } from 'react'

type AccentColor = 'blue' | 'white' | 'green'

interface MeshBackgroundProps {
  accent: AccentColor
}

export function MeshBackground({ accent }: MeshBackgroundProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const ctx = canvas.getContext('2d')
    if (!ctx) return

    // Set canvas size
    const resize = () => {
      canvas.width = window.innerWidth
      canvas.height = window.innerHeight
    }
    resize()
    window.addEventListener('resize', resize)

    // Very subtle color based on accent - barely visible (10-15% opacity)
    const getColor = () => {
      switch (accent) {
        case 'blue':
          return { r: 0, g: 70, b: 150 }   // Deep blue
        case 'white':
          return { r: 60, g: 60, b: 70 }   // Neutral gray
        case 'green':
          return { r: 0, g: 80, b: 60 }    // Deep teal
        default:
          return { r: 0, g: 70, b: 150 }
      }
    }

    const color = getColor()
    let animationId: number
    let time = 0

    const draw = () => {
      time += 0.002 // Very slow movement

      // Clear with base color
      ctx.fillStyle = '#0a0a0a'
      ctx.fillRect(0, 0, canvas.width, canvas.height)

      // Single very subtle gradient blob - 10-12% opacity max
      const centerX = canvas.width * 0.5 + Math.sin(time) * 80
      const centerY = canvas.height * 0.4 + Math.cos(time * 0.7) * 40
      const radius = Math.min(canvas.width, canvas.height) * 0.6

      const gradient = ctx.createRadialGradient(
        centerX, centerY, 0,
        centerX, centerY, radius
      )
      gradient.addColorStop(0, `rgba(${color.r}, ${color.g}, ${color.b}, 0.10)`)
      gradient.addColorStop(0.5, `rgba(${color.r}, ${color.g}, ${color.b}, 0.05)`)
      gradient.addColorStop(1, 'rgba(0, 0, 0, 0)')

      ctx.fillStyle = gradient
      ctx.fillRect(0, 0, canvas.width, canvas.height)

      // Very subtle second blob for depth
      const centerX2 = canvas.width * 0.7 + Math.cos(time * 0.8) * 60
      const centerY2 = canvas.height * 0.6 + Math.sin(time * 0.5) * 50

      const gradient2 = ctx.createRadialGradient(
        centerX2, centerY2, 0,
        centerX2, centerY2, radius * 0.6
      )
      gradient2.addColorStop(0, `rgba(${color.r}, ${color.g}, ${color.b}, 0.06)`)
      gradient2.addColorStop(1, 'rgba(0, 0, 0, 0)')

      ctx.fillStyle = gradient2
      ctx.fillRect(0, 0, canvas.width, canvas.height)

      animationId = requestAnimationFrame(draw)
    }

    draw()

    return () => {
      window.removeEventListener('resize', resize)
      cancelAnimationFrame(animationId)
    }
  }, [accent])

  return (
    <canvas
      ref={canvasRef}
      className="fixed inset-0 -z-10"
      style={{ background: '#0a0a0a' }}
    />
  )
}
