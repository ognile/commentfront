import { Check } from '@phosphor-icons/react'

type AccentColor = 'blue' | 'white' | 'green'

interface ThemeSwitcherProps {
  currentAccent: AccentColor
  onAccentChange: (accent: AccentColor) => void
}

const accents: { id: AccentColor; name: string; color: string; label: string }[] = [
  { id: 'blue', name: 'Electric', color: '#0070f3', label: 'A' },
  { id: 'white', name: 'Mono', color: '#ffffff', label: 'B' },
  { id: 'green', name: 'Mint', color: '#00d68f', label: 'C' },
]

export function ThemeSwitcher({ currentAccent, onAccentChange }: ThemeSwitcherProps) {
  return (
    <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50">
      <div
        className="flex items-center gap-2 p-2"
        style={{
          background: 'var(--glass-bg)',
          backdropFilter: 'blur(16px)',
          WebkitBackdropFilter: 'blur(16px)',
          border: '1px solid var(--border)',
          borderRadius: '4px',
        }}
      >
        <span className="text-xs text-tertiary px-2 font-mono">ACCENT</span>

        {accents.map((accent) => {
          const isActive = currentAccent === accent.id

          return (
            <button
              key={accent.id}
              onClick={() => onAccentChange(accent.id)}
              className="relative flex items-center gap-2 px-3 py-2 transition-all"
              style={{
                background: isActive ? 'var(--accent-soft)' : 'transparent',
                border: isActive ? '1px solid var(--accent)' : '1px solid transparent',
                borderRadius: '4px',
              }}
            >
              {/* Color dot */}
              <div
                className="w-3 h-3 rounded-full"
                style={{
                  background: accent.color,
                  border: accent.id === 'white' ? '1px solid var(--border)' : 'none',
                }}
              />

              {/* Label */}
              <span
                className="text-xs font-mono"
                style={{
                  color: isActive ? 'var(--accent-text)' : 'var(--text-secondary)',
                }}
              >
                {accent.label}
              </span>

              {/* Check mark */}
              {isActive && (
                <Check
                  weight="bold"
                  className="w-3 h-3"
                  style={{ color: 'var(--accent-text)' }}
                />
              )}
            </button>
          )
        })}
      </div>
    </div>
  )
}
