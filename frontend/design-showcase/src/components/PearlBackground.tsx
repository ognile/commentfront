export type PearlVariant =
  | 'warm'
  | 'cool'
  | 'rose'
  | 'mint'
  | 'lavender'
  | 'golden'
  | 'arctic'
  | 'sunset'

export const pearlGradients: Record<PearlVariant, { name: string; gradient: string; swatch: string }> = {
  warm: {
    name: 'Warm Pearl',
    gradient: `linear-gradient(
      135deg,
      #fdfcfb 0%,
      #f8f7f6 25%,
      #faf8f9 50%,
      #f7f9fa 75%,
      #fdfcfb 100%
    )`,
    swatch: '#f8f7f6',
  },
  cool: {
    name: 'Cool Pearl',
    gradient: `linear-gradient(
      135deg,
      #f8f9fb 0%,
      #f0f4f8 25%,
      #e8f0f5 50%,
      #f0f4f8 75%,
      #f8f9fb 100%
    )`,
    swatch: '#e8f0f5',
  },
  rose: {
    name: 'Rose Pearl',
    gradient: `linear-gradient(
      135deg,
      #fdfbfb 0%,
      #faf5f6 25%,
      #fbf3f5 50%,
      #f8eff2 75%,
      #fdfbfb 100%
    )`,
    swatch: '#f8eff2',
  },
  mint: {
    name: 'Mint Pearl',
    gradient: `linear-gradient(
      135deg,
      #f8fcfb 0%,
      #f0f8f5 25%,
      #e8f5f2 50%,
      #f0f8f5 75%,
      #f8fcfb 100%
    )`,
    swatch: '#e8f5f2',
  },
  lavender: {
    name: 'Lavender Pearl',
    gradient: `linear-gradient(
      135deg,
      #fbf8fd 0%,
      #f5f0fa 25%,
      #f2ebf8 50%,
      #eee6f5 75%,
      #fbf8fd 100%
    )`,
    swatch: '#eee6f5',
  },
  golden: {
    name: 'Golden Pearl',
    gradient: `linear-gradient(
      135deg,
      #fdfcf8 0%,
      #faf7f0 25%,
      #f8f4e8 50%,
      #f5f0e0 75%,
      #fdfcf8 100%
    )`,
    swatch: '#f5f0e0',
  },
  arctic: {
    name: 'Arctic Pearl',
    gradient: `linear-gradient(
      135deg,
      #f8fafd 0%,
      #f0f5fb 25%,
      #e8f2fa 50%,
      #e0edf8 75%,
      #f8fafd 100%
    )`,
    swatch: '#e0edf8',
  },
  sunset: {
    name: 'Sunset Pearl',
    gradient: `linear-gradient(
      135deg,
      #fdfaf8 0%,
      #faf5f0 25%,
      #f8f0eb 50%,
      #f5ebe5 75%,
      #fdfaf8 100%
    )`,
    swatch: '#f5ebe5',
  },
}

interface PearlBackgroundProps {
  variant?: PearlVariant
}

export function PearlBackground({ variant = 'warm' }: PearlBackgroundProps) {
  return (
    <div
      className="fixed inset-0 -z-10 transition-all duration-500"
      style={{
        background: pearlGradients[variant].gradient,
      }}
    />
  )
}
