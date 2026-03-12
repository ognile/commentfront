import { createRef } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { RemoteControlModal } from '@/components/remote/RemoteControlModal'

describe('RemoteControlModal', () => {
  it('shows observer state and exposes takeover control', () => {
    const handleTakeover = vi.fn()

    render(
      <RemoteControlModal
        remote={{
          remoteModalOpen: true,
          remoteSession: {
            platform: 'facebook',
            profileName: 'alpha',
            displayName: 'Alpha',
            valid: true,
          },
          remoteFrame: null,
          remoteConnected: true,
          remoteConnecting: false,
          remoteProgress: null,
          remoteUrlInput: 'https://example.com',
          setRemoteUrlInput: vi.fn(),
          actionLog: [],
          pendingUpload: null,
          uploadReady: false,
          remoteRole: 'observer',
          remoteCanControl: false,
          remoteControllerUser: 'alice',
          remoteViewerCount: 2,
          remoteLeaseId: 'lease-alpha',
          keyboardCaptureEnabled: false,
          screenshotContainerRef: createRef<HTMLDivElement>(),
          openRemoteModal: vi.fn(),
          closeRemoteModal: vi.fn(),
          handleRemotePointerDown: vi.fn(),
          handleRemotePointerUp: vi.fn(),
          handleRemotePointerCancel: vi.fn(),
          handleRemoteScroll: vi.fn(),
          handleRemoteNavigate: vi.fn(),
          handleRemoteRestart: vi.fn(),
          handleTakeover,
          handleImageUpload: vi.fn(),
          prepareFileUpload: vi.fn(),
        }}
      />,
    )

    expect(screen.getByText('observer')).toBeInTheDocument()
    expect(screen.getByText('controller: alice')).toBeInTheDocument()
    expect(screen.getByText('viewers:')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /take over/i }))
    expect(handleTakeover).toHaveBeenCalledTimes(1)
  })
})
