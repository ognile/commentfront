import { Plus, Play, Trash, Clock, ChatCircle, ArrowSquareOut, CheckCircle, CircleNotch, Warning } from '@phosphor-icons/react'
import { GlassPanel } from './GlassPanel'

const campaigns = [
  { id: 1, url: 'facebook.com/post/123456', comments: 5, duration: 10, status: 'completed' as const },
  { id: 2, url: 'facebook.com/post/789012', comments: 3, duration: 8, status: 'running' as const },
  { id: 3, url: 'facebook.com/post/345678', comments: 7, duration: 15, status: 'pending' as const },
]

export function CampaignQueue() {
  const getStatusIcon = (status: 'pending' | 'running' | 'completed' | 'failed') => {
    switch (status) {
      case 'pending': return <Clock weight="bold" className="w-3.5 h-3.5" />
      case 'running': return <CircleNotch weight="bold" className="w-3.5 h-3.5 animate-spin" />
      case 'completed': return <CheckCircle weight="fill" className="w-3.5 h-3.5" />
      case 'failed': return <Warning weight="fill" className="w-3.5 h-3.5" />
    }
  }

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'pending': return 'var(--text-tertiary)'
      case 'running': return 'var(--accent)'
      case 'completed': return 'var(--success)'
      case 'failed': return 'var(--error)'
      default: return 'var(--text-secondary)'
    }
  }

  return (
    <div className="space-y-4">
      {/* Add Campaign Form */}
      <GlassPanel className="p-5">
        <h2 className="font-display font-semibold text-base text-primary mb-4">
          Add Campaign
        </h2>

        <div className="space-y-3">
          {/* URL Input - pill */}
          <div>
            <label className="block text-xs font-medium text-secondary mb-1.5">
              Target URL
            </label>
            <div className="flex gap-2">
              <input
                type="text"
                placeholder="https://facebook.com/post/..."
                className="pill-input flex-1 px-4 py-2 text-sm"
              />
              <button className="pill-button-secondary p-2.5">
                <ArrowSquareOut weight="bold" className="w-4 h-4" />
              </button>
            </div>
          </div>

          {/* Comments - rounded textarea */}
          <div>
            <label className="block text-xs font-medium text-secondary mb-1.5">
              Comments (one per line)
            </label>
            <textarea
              rows={3}
              placeholder="Great post!&#10;Love this!&#10;Amazing content!"
              className="rounded-textarea w-full px-4 py-3 text-sm resize-none"
            />
          </div>

          {/* Duration & Submit */}
          <div className="flex items-end gap-3">
            <div className="w-28">
              <label className="block text-xs font-medium text-secondary mb-1.5">
                Duration (min)
              </label>
              <input
                type="number"
                defaultValue={10}
                className="pill-input w-full px-4 py-2 text-sm"
              />
            </div>

            <button className="pill-button flex items-center gap-2 px-5 py-2.5 text-sm">
              <Plus weight="bold" className="w-4 h-4" />
              Add to Queue
            </button>
          </div>
        </div>
      </GlassPanel>

      {/* Campaign Queue */}
      <GlassPanel className="p-5">
        <div className="flex items-center justify-between mb-4">
          <h2 className="font-display font-semibold text-base text-primary">
            Queue
          </h2>

          <button
            className="flex items-center gap-2 px-5 py-2.5 text-sm font-medium"
            style={{
              background: 'var(--success)',
              color: '#fff',
              borderRadius: '9999px',
            }}
          >
            <Play weight="fill" className="w-3.5 h-3.5" />
            Run
          </button>
        </div>

        {/* Queue Items */}
        <div className="space-y-2">
          {campaigns.map((campaign, index) => (
            <div
              key={campaign.id}
              className="flex items-center justify-between p-3 transition-all"
              style={{
                background: campaign.status === 'running' ? 'var(--accent-soft)' : 'var(--card)',
                border: campaign.status === 'running' ? '1px solid var(--accent)' : '1px solid var(--border)',
                borderRadius: '9999px',
              }}
            >
              <div className="flex items-center gap-3">
                {/* Number - pill */}
                <span
                  className="w-7 h-7 flex items-center justify-center text-xs font-mono"
                  style={{
                    background: 'var(--accent-soft)',
                    border: '1px solid var(--border)',
                    borderRadius: '9999px',
                    color: 'var(--text-tertiary)',
                  }}
                >
                  {index + 1}
                </span>

                {/* URL */}
                <span className="font-mono text-xs text-primary truncate max-w-[180px]">
                  {campaign.url}
                </span>

                {/* Meta - pill badges */}
                <div className="flex items-center gap-1.5">
                  <span
                    className="flex items-center gap-1 px-2.5 py-1 text-xs"
                    style={{
                      background: 'var(--accent-soft)',
                      border: '1px solid var(--border)',
                      borderRadius: '9999px',
                      color: 'var(--text-secondary)',
                    }}
                  >
                    <ChatCircle weight="bold" className="w-3 h-3" />
                    {campaign.comments}
                  </span>
                  <span
                    className="flex items-center gap-1 px-2.5 py-1 text-xs"
                    style={{
                      background: 'var(--accent-soft)',
                      border: '1px solid var(--border)',
                      borderRadius: '9999px',
                      color: 'var(--text-secondary)',
                    }}
                  >
                    <Clock weight="bold" className="w-3 h-3" />
                    {campaign.duration}m
                  </span>
                </div>
              </div>

              <div className="flex items-center gap-2">
                {/* Status - pill */}
                <span
                  className="flex items-center gap-1 px-3 py-1.5 text-xs font-medium capitalize"
                  style={{
                    background: `color-mix(in srgb, ${getStatusColor(campaign.status)} 15%, transparent)`,
                    color: getStatusColor(campaign.status),
                    border: `1px solid color-mix(in srgb, ${getStatusColor(campaign.status)} 30%, transparent)`,
                    borderRadius: '9999px',
                  }}
                >
                  {getStatusIcon(campaign.status)}
                  {campaign.status}
                </span>

                {/* Delete */}
                {campaign.status === 'pending' && (
                  <button
                    className="pill-icon-button p-2"
                  >
                    <Trash weight="bold" className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>

        {/* Summary */}
        <div
          className="mt-4 pt-4 flex items-center justify-between text-xs"
          style={{
            borderTop: '1px solid var(--border)',
            color: 'var(--text-tertiary)',
          }}
        >
          <span>{campaigns.length} campaigns</span>
          <span>15 comments</span>
          <span>~33 min</span>
        </div>
      </GlassPanel>
    </div>
  )
}
