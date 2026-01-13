import { WifiHigh, Command } from '@phosphor-icons/react'
import { GlassPanel } from './GlassPanel'

export function Header() {
  return (
    <header className="sticky top-0 z-50 px-6 py-4">
      <GlassPanel className="px-5 py-3" hover={false}>
        <div className="flex items-center justify-between">
          {/* Logo & Brand */}
          <div className="flex items-center gap-3">
            {/* Logo mark - pill */}
            <div
              className="w-9 h-9 flex items-center justify-center"
              style={{
                background: 'var(--accent-soft)',
                border: '1px solid var(--border)',
                borderRadius: '9999px',
              }}
            >
              <Command weight="bold" className="w-5 h-5" style={{ color: 'var(--accent)' }} />
            </div>

            <div>
              <h1 className="font-display text-base font-semibold text-primary tracking-tight">
                CommentBot
              </h1>
              <p className="text-xs text-tertiary">
                Automation platform
              </p>
            </div>
          </div>

          {/* Status & Avatar */}
          <div className="flex items-center gap-3">
            {/* Connection Status - pill shape */}
            <div
              className="flex items-center gap-2 px-3 py-1.5"
              style={{
                background: 'var(--success-soft)',
                border: '1px solid var(--border)',
                borderRadius: '9999px',
              }}
            >
              <div
                className="status-dot"
                style={{ background: 'var(--success)' }}
              />
              <span className="text-xs font-medium" style={{ color: 'var(--success)' }}>
                Connected
              </span>
              <WifiHigh weight="bold" className="w-3.5 h-3.5" style={{ color: 'var(--success)' }} />
            </div>

            {/* User Avatar - circle */}
            <div
              className="w-8 h-8 rounded-full flex items-center justify-center font-medium text-xs"
              style={{
                background: 'var(--accent-soft)',
                border: '1px solid var(--border)',
                color: 'var(--accent)',
              }}
            >
              NK
            </div>
          </div>
        </div>
      </GlassPanel>
    </header>
  )
}
