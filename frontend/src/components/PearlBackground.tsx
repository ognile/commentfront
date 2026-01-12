/**
 * PearlBackground - Subtle pearl gradient background
 * A fixed, full-viewport gradient with warm cream, pink, and blue tints
 */
export function PearlBackground() {
  return (
    <div
      className="fixed inset-0 -z-10"
      style={{
        background: `linear-gradient(
          135deg,
          #fdfcfb 0%,
          #f8f7f6 25%,
          #faf8f9 50%,
          #f7f9fa 75%,
          #fdfcfb 100%
        )`,
      }}
    />
  )
}
