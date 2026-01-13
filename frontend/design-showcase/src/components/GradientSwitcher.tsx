import { Check } from '@phosphor-icons/react'
import { type PearlVariant, pearlGradients } from './PearlBackground'

interface GradientSwitcherProps {
  currentVariant: PearlVariant
  onVariantChange: (variant: PearlVariant) => void
}

const variants: PearlVariant[] = [
  'warm',
  'cool',
  'rose',
  'mint',
  'lavender',
  'golden',
  'arctic',
  'sunset',
]

export function GradientSwitcher({ currentVariant, onVariantChange }: GradientSwitcherProps) {
  return (
    <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50">
      <div
        className="flex flex-col items-center gap-3 p-4"
        style={{
          background: 'rgba(255, 255, 255, 0.9)',
          backdropFilter: 'blur(16px)',
          WebkitBackdropFilter: 'blur(16px)',
          border: '1px solid var(--border)',
          borderRadius: '16px',
        }}
      >
        {/* Current variant name */}
        <div className="flex items-center gap-2">
          <span className="text-xs text-tertiary font-mono uppercase tracking-wide">Pearl Gradient</span>
          <span className="text-sm font-medium text-primary">
            {pearlGradients[currentVariant].name}
          </span>
        </div>

        {/* Gradient swatches */}
        <div className="flex items-center gap-2">
          {variants.map((variant) => {
            const isActive = currentVariant === variant
            const config = pearlGradients[variant]

            return (
              <button
                key={variant}
                onClick={() => onVariantChange(variant)}
                className="relative flex items-center justify-center transition-all duration-200"
                style={{
                  width: 40,
                  height: 40,
                  borderRadius: '50%',
                  background: config.gradient,
                  border: isActive ? '2px solid var(--accent)' : '1px solid var(--border)',
                  transform: isActive ? 'scale(1.1)' : 'scale(1)',
                }}
                title={config.name}
              >
                {/* Check mark for active */}
                {isActive && (
                  <Check
                    weight="bold"
                    className="w-4 h-4"
                    style={{ color: 'var(--accent)' }}
                  />
                )}
              </button>
            )
          })}
        </div>
      </div>
    </div>
  )
}
