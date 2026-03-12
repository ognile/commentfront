import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Loader2, Globe, Mouse, WifiOff, X } from 'lucide-react'

import { REMOTE_VIEWPORT_HEIGHT, REMOTE_VIEWPORT_WIDTH } from '@/components/remote/types'
import { useRemoteControl } from '@/hooks/useRemoteControl'

type RemoteControlState = ReturnType<typeof useRemoteControl>

function progressLabel(progress: string | null, platform: 'facebook' | 'reddit' | undefined): string {
  if (progress === 'launching_browser') return 'launching browser...'
  if (progress === 'applying_stealth') return 'applying security...'
  if (progress === 'navigating') return platform === 'reddit' ? 'loading reddit...' : 'loading facebook...'
  if (progress === 'retrying') return 'retrying connection...'
  if (progress === 'auto_heal') return 'recovering browser session...'
  if (progress === 'stream_restarted') return 'restarting stream...'
  return 'waiting for browser...'
}

function platformLabel(platform: 'facebook' | 'reddit') {
  return platform === 'reddit' ? 'reddit' : 'facebook'
}

interface RemoteControlModalProps {
  remote: RemoteControlState
}

export function RemoteControlModal({ remote }: RemoteControlModalProps) {
  const {
    remoteModalOpen,
    remoteSession,
    remoteFrame,
    remoteConnected,
    remoteConnecting,
    remoteProgress,
    remoteUrlInput,
    setRemoteUrlInput,
    actionLog,
    pendingUpload,
    uploadReady,
    remoteRole,
    remoteCanControl,
    remoteControllerUser,
    remoteViewerCount,
    remoteLeaseId,
    keyboardCaptureEnabled,
    screenshotContainerRef,
    closeRemoteModal,
    handleRemotePointerDown,
    handleRemotePointerUp,
    handleRemotePointerCancel,
    handleRemoteScroll,
    handleRemoteNavigate,
    handleRemoteRestart,
    handleTakeover,
    handleImageUpload,
    prepareFileUpload,
  } = remote

  if (!remoteModalOpen || !remoteSession) return null

  const supportsUpload = remoteSession.platform === 'facebook'

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4">
      <div className="flex max-h-[85vh] w-full max-w-5xl flex-col overflow-hidden rounded-xl bg-white shadow-2xl">
        <div className="shrink-0 border-b bg-white px-4 py-2">
          <div className="flex items-center justify-between gap-4">
              <div className="flex items-center gap-4">
                <div className="flex items-center gap-2">
                <div
                  className={`h-3 w-3 rounded-full ${
                    remoteConnected
                      ? 'bg-green-500'
                      : remoteConnecting
                        ? 'animate-pulse bg-yellow-500'
                        : 'bg-red-500'
                  }`}
                />
                <span className="text-sm font-medium">
                  {remoteConnected ? 'connected' : remoteConnecting ? 'connecting...' : 'disconnected'}
                </span>
                </div>
                <Badge variant="outline">{platformLabel(remoteSession.platform)}</Badge>
                <Badge variant={remoteRole === 'controller' ? 'default' : 'secondary'}>
                  {remoteRole || 'connecting'}
                </Badge>
                <div className="text-sm text-[#999999]">
                  session: <span className="font-medium text-[#111111]">{remoteSession.profileName}</span>
                </div>
                <div className="text-sm text-[#999999]">
                  viewers: <span className="font-medium text-[#111111]">{remoteViewerCount}</span>
                </div>
              </div>
            <div className="flex items-center gap-2">
              {remoteRole === 'observer' ? (
                <Button variant="outline" size="sm" onClick={handleTakeover} disabled={!remoteConnected}>
                  take over
                </Button>
              ) : null}
              <Button variant="ghost" size="sm" onClick={closeRemoteModal}>
                <X className="h-5 w-5" />
              </Button>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2 border-b bg-white px-4 py-2">
          <Globe className="h-4 w-4 text-[#999999]" />
          <Input
            value={remoteUrlInput}
            onChange={(event) => setRemoteUrlInput(event.target.value)}
            onKeyDown={(event) => event.key === 'Enter' && handleRemoteNavigate()}
            placeholder="enter url..."
            className="flex-1 bg-white"
          />
          <Button variant="outline" onClick={() => void handleRemoteRestart()} disabled={!remoteSession || !remoteCanControl}>
            restart
          </Button>
          <Button onClick={handleRemoteNavigate} disabled={!remoteConnected || !remoteCanControl}>
            go
          </Button>
        </div>

        <div className="flex min-h-0 flex-1 overflow-hidden">
          <div className="flex min-h-0 flex-1 items-center justify-center bg-[#333333] p-2">
            <div
              ref={screenshotContainerRef}
              className="relative flex h-full cursor-crosshair items-center justify-center outline-none"
              onPointerDown={handleRemotePointerDown}
              onPointerUp={handleRemotePointerUp}
              onPointerCancel={handleRemotePointerCancel}
              onWheel={handleRemoteScroll}
              onContextMenu={(event) => event.preventDefault()}
              tabIndex={0}
            >
              {remoteFrame ? (
                <img
                  src={remoteFrame}
                  alt="browser view"
                  className="rounded-lg object-contain shadow-lg"
                  style={{
                    maxHeight: '100%',
                    maxWidth: '100%',
                    aspectRatio: `${REMOTE_VIEWPORT_WIDTH}/${REMOTE_VIEWPORT_HEIGHT}`,
                  }}
                  draggable={false}
                />
              ) : (
                <div className="flex items-center justify-center text-[#999999]" style={{ width: 250, height: 500 }}>
                  <div className="text-center">
                    <Loader2 className="mx-auto mb-2 h-8 w-8 animate-spin" />
                    <p>{progressLabel(remoteProgress, remoteSession.platform)}</p>
                  </div>
                </div>
              )}

              {!remoteConnected && remoteFrame && (
                <div className="absolute inset-0 flex items-center justify-center rounded-lg bg-black/70">
                  <div className="text-center text-white">
                    <WifiOff className="mx-auto mb-2 h-12 w-12" />
                    <p>disconnected</p>
                  </div>
                </div>
              )}
            </div>
          </div>

          <div className="flex w-64 shrink-0 flex-col border-l bg-white">
            {supportsUpload ? (
              <div className="border-b p-3">
                <div className="mb-2 text-xs font-medium">profile picture upload</div>
                <Input
                  type="file"
                  accept=".jpg,.jpeg,.png,.webp"
                  disabled={!remoteCanControl}
                  onChange={(event) => {
                    const file = event.target.files?.[0]
                    if (file) {
                      void handleImageUpload(file)
                    }
                    event.target.value = ''
                  }}
                  className="text-xs"
                />
                {pendingUpload ? (
                  <div className="mt-2 rounded bg-blue-50 p-2 text-xs">
                    <p className="text-blue-700">
                      ready: {pendingUpload.filename} ({Math.round(pendingUpload.size / 1024)}kb)
                    </p>
                    {!uploadReady ? (
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => void prepareFileUpload()}
                        disabled={!remoteCanControl}
                        className="mt-2 w-full text-xs"
                      >
                        prepare for upload
                      </Button>
                    ) : (
                      <p className="mt-2 font-medium text-green-700">click the upload button on facebook!</p>
                    )}
                  </div>
                ) : null}
              </div>
            ) : null}

            <div className="flex flex-1 flex-col overflow-hidden">
              <div className="flex items-center gap-2 border-b px-4 py-3 text-sm font-medium">
                <Mouse className="h-4 w-4" />
                action log
              </div>
              <div className="flex-1 space-y-1 overflow-y-auto p-2">
                {actionLog.map((entry) => (
                  <div
                    key={entry.id}
                    className={`rounded p-2 text-xs ${
                      entry.status === 'success'
                        ? 'bg-green-50 text-green-700'
                        : entry.status === 'failed'
                          ? 'bg-red-50 text-red-700'
                          : 'bg-[rgba(51,51,51,0.08)] text-[#666666]'
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-mono">{new Date(entry.timestamp).toLocaleTimeString()}</span>
                      <Badge variant="outline" className="text-xs">
                        {entry.type}
                      </Badge>
                    </div>
                    <div className="mt-1 truncate">{entry.details}</div>
                  </div>
                ))}
                {actionLog.length === 0 ? (
                  <div className="py-8 text-center text-sm text-[#999999]">
                    {remoteRole === 'observer'
                      ? 'observer mode. take over to interact.'
                      : 'no actions yet. click the browser to arm keyboard capture.'}
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        </div>

        <div className="flex shrink-0 items-center justify-between border-t bg-white px-4 py-1 text-xs text-[#999999]">
          <div className="flex items-center gap-4">
            <span>viewport: 393x873 mobile</span>
            <span>|</span>
            <span className={remoteCanControl && keyboardCaptureEnabled ? 'text-green-600' : 'text-[#999999]'}>
              {remoteCanControl && keyboardCaptureEnabled ? 'keyboard capture: armed' : 'keyboard capture: idle'}
            </span>
            {remoteControllerUser ? (
              <>
                <span>|</span>
                <span>controller: {remoteControllerUser}</span>
              </>
            ) : null}
          </div>
          <div>{remoteLeaseId ? `lease ${remoteLeaseId}` : `actions: ${actionLog.length}`}</div>
        </div>
      </div>
    </div>
  )
}
