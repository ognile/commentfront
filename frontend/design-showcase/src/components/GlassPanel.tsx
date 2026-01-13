import { ReactNode } from 'react'

interface GlassPanelProps {
  children: ReactNode
  className?: string
  hover?: boolean
}

// Renamed conceptually to "Card" but keeping filename for compatibility
export function GlassPanel({ children, className = '', hover = true }: GlassPanelProps) {
  return (
    <div
      className={`${className}`}
      style={{
        background: 'var(--card)',
        border: '1px solid var(--border)',
        borderRadius: '16px',
        transition: hover ? 'border-color 0.15s ease' : undefined,
        // NO SHADOWS - NO BLUR
      }}
      onMouseEnter={(e) => {
        if (hover) {
          e.currentTarget.style.borderColor = 'var(--border-strong)'
        }
      }}
      onMouseLeave={(e) => {
        if (hover) {
          e.currentTarget.style.borderColor = 'var(--border)'
        }
      }}
    >
      {children}
    </div>
  )
}
