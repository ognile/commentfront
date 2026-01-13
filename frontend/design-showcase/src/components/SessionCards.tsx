import { User, ShieldCheck, ShieldWarning, GlobeHemisphereWest, ArrowsClockwise, Play, Trash, CheckCircle, XCircle } from '@phosphor-icons/react'
import { GlassPanel } from './GlassPanel'

const sessions = [
  {
    id: 1,
    profile_name: 'John Smith',
    user_id: '100089234567890',
    valid: true,
    proxy: 'us-west-2.proxy.io',
    proxy_type: 'residential',
  },
  {
    id: 2,
    profile_name: 'Sarah Johnson',
    user_id: '100067890123456',
    valid: true,
    proxy: 'eu-central.proxy.io',
    proxy_type: 'mobile',
  },
  {
    id: 3,
    profile_name: 'Michael Brown',
    user_id: '100045678901234',
    valid: false,
    proxy: null,
    proxy_type: null,
  },
]

export function SessionCards() {
  return (
    <GlassPanel className="p-5">
      <div className="flex items-center justify-between mb-4">
        <h2 className="font-display font-semibold text-base text-primary flex items-center gap-2">
          <User weight="bold" className="w-5 h-5" style={{ color: 'var(--accent)' }} />
          Sessions
        </h2>

        <div className="flex items-center gap-3">
          <span className="text-xs text-tertiary">
            {sessions.filter(s => s.valid).length} of {sessions.length} valid
          </span>
          <button className="pill-button-secondary flex items-center gap-2 px-3 py-1.5 text-xs">
            <ArrowsClockwise weight="bold" className="w-3.5 h-3.5" />
            Refresh
          </button>
        </div>
      </div>

      <div className="space-y-2">
        {sessions.map((session) => {
          const initial = session.profile_name.charAt(0).toUpperCase()

          return (
            <div
              key={session.id}
              className={`flex items-center justify-between p-3 transition-all ${!session.valid ? 'opacity-50' : ''}`}
              style={{
                background: session.valid ? 'var(--accent-soft)' : 'var(--card)',
                border: '1px solid var(--border)',
                borderRadius: '9999px',
              }}
            >
              <div className="flex items-center gap-3">
                {/* Avatar - circle */}
                <div
                  className="w-9 h-9 rounded-full flex items-center justify-center font-medium text-sm"
                  style={{
                    background: session.valid ? 'var(--accent)' : 'var(--accent-soft)',
                    color: session.valid ? '#fff' : 'var(--text-tertiary)',
                    border: '1px solid var(--border)',
                  }}
                >
                  {initial}
                </div>

                {/* Info */}
                <div>
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-sm text-primary">
                      {session.profile_name}
                    </span>

                    {/* Valid badge - pill */}
                    {session.valid ? (
                      <span
                        className="flex items-center gap-1 px-2 py-0.5 text-[10px] font-medium"
                        style={{
                          background: 'var(--success-soft)',
                          color: 'var(--success)',
                          border: '1px solid color-mix(in srgb, var(--success) 30%, transparent)',
                          borderRadius: '9999px',
                        }}
                      >
                        <CheckCircle weight="fill" className="w-3 h-3" />
                        Valid
                      </span>
                    ) : (
                      <span
                        className="flex items-center gap-1 px-2 py-0.5 text-[10px] font-medium"
                        style={{
                          background: 'var(--error-soft)',
                          color: 'var(--error)',
                          border: '1px solid color-mix(in srgb, var(--error) 30%, transparent)',
                          borderRadius: '9999px',
                        }}
                      >
                        <XCircle weight="fill" className="w-3 h-3" />
                        Invalid
                      </span>
                    )}
                  </div>

                  <div className="flex items-center gap-2 mt-1 text-[11px] text-tertiary">
                    <span className="font-mono">{session.user_id}</span>
                    {session.proxy && (
                      <>
                        <span className="opacity-30">•</span>
                        <span className="flex items-center gap-1">
                          <GlobeHemisphereWest weight="bold" className="w-3 h-3" />
                          {session.proxy}
                        </span>
                        <span
                          className="px-1.5 py-0.5 text-[9px] uppercase font-semibold"
                          style={{
                            background: 'var(--accent-soft)',
                            border: '1px solid var(--border)',
                            borderRadius: '9999px',
                            color: 'var(--text-secondary)',
                          }}
                        >
                          {session.proxy_type}
                        </span>
                      </>
                    )}
                  </div>
                </div>
              </div>

              {/* Actions */}
              <div className="flex items-center gap-2">
                {session.valid && (
                  <button className="pill-button flex items-center gap-1.5 px-3 py-1.5 text-xs">
                    <Play weight="fill" className="w-3 h-3" />
                    Control
                  </button>
                )}

                <button className="pill-icon-button p-1.5">
                  <ArrowsClockwise weight="bold" className="w-3.5 h-3.5" />
                </button>

                <button className="pill-icon-button p-1.5">
                  <Trash weight="bold" className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          )
        })}
      </div>

      {/* Status indicators */}
      <div
        className="mt-4 pt-4 flex items-center justify-between text-xs"
        style={{
          borderTop: '1px solid var(--border)',
          color: 'var(--text-tertiary)',
        }}
      >
        <div className="flex items-center gap-1.5">
          <ShieldCheck weight="bold" className="w-3.5 h-3.5" style={{ color: 'var(--success)' }} />
          <span>{sessions.filter(s => s.valid).length} active</span>
        </div>
        <div className="flex items-center gap-1.5">
          <ShieldWarning weight="bold" className="w-3.5 h-3.5" style={{ color: 'var(--error)' }} />
          <span>{sessions.filter(s => !s.valid).length} need attention</span>
        </div>
      </div>
    </GlassPanel>
  )
}
