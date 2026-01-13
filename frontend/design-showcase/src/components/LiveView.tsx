import { Eye, DeviceMobile, Cpu, Pulse, Lightning } from '@phosphor-icons/react'
import { GlassPanel } from './GlassPanel'

export function LiveView() {
  return (
    <div className="grid grid-cols-3 gap-4">
      {/* Main Screenshot Area */}
      <div className="col-span-2">
        <GlassPanel className="p-5 h-full">
          <div className="flex items-center justify-between mb-4">
            <h2 className="font-display font-semibold text-base text-primary flex items-center gap-2">
              <Eye weight="bold" className="w-5 h-5" style={{ color: 'var(--accent)' }} />
              Live View
            </h2>

            <div
              className="flex items-center gap-2 px-3 py-1.5"
              style={{
                background: 'var(--success-soft)',
                border: '1px solid var(--border)',
                borderRadius: '9999px',
              }}
            >
              <div className="status-dot" style={{ background: 'var(--success)' }} />
              <span className="text-xs font-medium" style={{ color: 'var(--success)' }}>
                Recording
              </span>
            </div>
          </div>

          {/* Phone Frame - 16px radius */}
          <div className="flex justify-center">
            <div
              className="relative overflow-hidden"
              style={{
                width: '260px',
                height: '520px',
                background: '#f5f5f5',
                borderRadius: '16px',
                border: '1px solid var(--border)',
              }}
            >
              {/* Simulated Content */}
              <div className="pt-8 px-4 space-y-4">
                {/* Post Header */}
                <div className="flex items-center gap-3">
                  <div
                    className="w-10 h-10 rounded-full"
                    style={{ background: '#e0e0e0', border: '1px solid var(--border)' }}
                  />
                  <div className="space-y-1.5">
                    <div
                      className="h-2.5 w-24"
                      style={{ background: '#e0e0e0', borderRadius: '9999px' }}
                    />
                    <div
                      className="h-2 w-16"
                      style={{ background: '#e8e8e8', borderRadius: '9999px' }}
                    />
                  </div>
                </div>

                {/* Post Content */}
                <div className="space-y-1.5">
                  <div className="h-2.5 w-full" style={{ background: '#e0e0e0', borderRadius: '9999px' }} />
                  <div className="h-2.5 w-4/5" style={{ background: '#e0e0e0', borderRadius: '9999px' }} />
                </div>

                {/* Image Placeholder */}
                <div
                  className="h-36"
                  style={{ background: '#e8e8e8', borderRadius: '16px', border: '1px solid var(--border)' }}
                />

                {/* Comment Area - Highlighted */}
                <div
                  className="p-3"
                  style={{
                    background: 'var(--accent-soft)',
                    border: '1px dashed var(--accent)',
                    borderRadius: '16px',
                  }}
                >
                  <div className="flex items-center gap-2">
                    <div
                      className="w-7 h-7 rounded-full"
                      style={{ background: 'var(--accent)' }}
                    />
                    <div
                      className="h-8 flex-1"
                      style={{ background: '#ffffff', borderRadius: '9999px', border: '1px solid var(--border)' }}
                    />
                  </div>
                  <p className="text-[10px] text-center mt-2 font-medium" style={{ color: 'var(--accent)' }}>
                    Typing comment...
                  </p>
                </div>
              </div>

              {/* Status Overlay */}
              <div
                className="absolute bottom-4 left-4 right-4 p-3"
                style={{
                  background: 'rgba(255,255,255,0.95)',
                  border: '1px solid var(--border)',
                  borderRadius: '16px',
                }}
              >
                <p className="text-xs font-medium text-primary mb-0.5">
                  Typing comment...
                </p>
                <p className="text-[10px] text-tertiary">
                  Job 2 of 5 • Profile: john_doe_123
                </p>
              </div>
            </div>
          </div>
        </GlassPanel>
      </div>

      {/* Stats Panel */}
      <div className="space-y-4">
        {/* Device Info */}
        <GlassPanel className="p-4">
          <h3 className="font-display font-medium text-xs text-secondary mb-3 flex items-center gap-2">
            <DeviceMobile weight="bold" className="w-4 h-4" />
            Device
          </h3>

          <div className="space-y-2 text-xs">
            <div className="flex justify-between">
              <span className="text-tertiary">Viewport</span>
              <span className="font-mono text-primary">393 x 873</span>
            </div>
            <div className="flex justify-between">
              <span className="text-tertiary">User Agent</span>
              <span className="font-mono text-primary truncate max-w-[100px]">iPhone 12 Pro</span>
            </div>
          </div>
        </GlassPanel>

        {/* Vision Model */}
        <GlassPanel className="p-4">
          <h3 className="font-display font-medium text-xs text-secondary mb-3 flex items-center gap-2">
            <Cpu weight="bold" className="w-4 h-4" />
            Vision
          </h3>

          <div
            className="flex items-center gap-2 px-3 py-2"
            style={{
              background: 'var(--accent-soft)',
              border: '1px solid var(--border)',
              borderRadius: '9999px',
            }}
          >
            <Lightning weight="fill" className="w-4 h-4" style={{ color: 'var(--accent)' }} />
            <span className="text-xs font-medium" style={{ color: 'var(--accent)' }}>
              Gemini 3 Flash
            </span>
          </div>
        </GlassPanel>

        {/* Activity Log */}
        <GlassPanel className="p-4">
          <h3 className="font-display font-medium text-xs text-secondary mb-3 flex items-center gap-2">
            <Pulse weight="bold" className="w-4 h-4" />
            Activity
          </h3>

          <div className="space-y-1.5 text-[11px] font-mono">
            {[
              { time: '12:34:02', action: 'Clicked comment button', active: false },
              { time: '12:34:05', action: 'Focused input field', active: false },
              { time: '12:34:06', action: 'Typing comment...', active: true },
            ].map((log, i) => (
              <div
                key={i}
                className="flex items-start gap-2 p-2"
                style={{
                  background: log.active ? 'var(--accent-soft)' : 'transparent',
                  border: log.active ? '1px solid var(--border)' : '1px solid transparent',
                  borderRadius: '9999px',
                }}
              >
                <span className="text-tertiary">{log.time}</span>
                <span style={{ color: log.active ? 'var(--accent)' : 'var(--text-secondary)' }}>
                  {log.action}
                </span>
              </div>
            ))}
          </div>
        </GlassPanel>
      </div>
    </div>
  )
}
