import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useRemoteControl } from '@/hooks/useRemoteControl'

type MockSocket = {
  readyState: number
  sent: string[]
  onopen: (() => void) | null
  onmessage: ((event: MessageEvent) => void) | null
  onclose: (() => void) | null
  onerror: ((event: Event) => void) | null
  send: (message: string) => void
  close: () => void
  emitOpen: () => void
  emitMessage: (payload: unknown) => void
}

type RemotePointerDownEvent = Parameters<ReturnType<typeof useRemoteControl>['handleRemotePointerDown']>[0]

const { mockedToast, socketRegistry, fetchMock } = vi.hoisted(() => ({
  mockedToast: Object.assign(vi.fn(), {
    success: vi.fn(),
    error: vi.fn(),
    loading: vi.fn(),
  }),
  socketRegistry: {
    instances: [] as MockSocket[],
  },
  fetchMock: vi.fn(async () => ({
    ok: true,
    status: 200,
    json: async () => ({ success: true }),
  })),
}))

vi.mock('sonner', () => ({ toast: mockedToast }))
vi.mock('@/lib/auth', () => ({ getAccessToken: vi.fn(() => 'token') }))
vi.mock('@/lib/api', () => ({
  API_BASE: 'http://localhost:8000',
  createAuthenticatedWebSocket: vi.fn(() => {
    const socket: MockSocket = {
      readyState: 0,
      sent: [],
      onopen: null,
      onmessage: null,
      onclose: null,
      onerror: null,
      send(message: string) {
        this.sent.push(message)
      },
      close() {
        this.readyState = 3
        this.onclose?.()
      },
      emitOpen() {
        this.readyState = 1
        this.onopen?.()
      },
      emitMessage(payload: unknown) {
        this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent)
      },
    }
    socketRegistry.instances.push(socket)
    return socket as unknown as WebSocket
  }),
}))

describe('useRemoteControl', () => {
  let actionCounter = 0

  beforeEach(() => {
    socketRegistry.instances = []
    actionCounter = 0
    mockedToast.mockReset()
    mockedToast.success.mockReset()
    mockedToast.error.mockReset()
    mockedToast.loading.mockReset()
    fetchMock.mockClear()
    vi.stubGlobal('crypto', {
      randomUUID: vi.fn(() => `action-${++actionCounter}`),
    })
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('tracks observer state and sends takeover requests', () => {
    const { result } = renderHook(() => useRemoteControl())

    act(() => {
      result.current.openRemoteModal({
        platform: 'facebook',
        profileName: 'alpha',
        displayName: 'Alpha',
        valid: true,
      })
    })

    const socket = socketRegistry.instances[0]
    act(() => {
      socket.emitOpen()
      socket.emitMessage({
        type: 'state',
        data: {
          url: 'https://example.com',
          role: 'observer',
          can_control: false,
          controller_user: 'alice',
          viewer_count: 2,
          lease_id: 'lease-alpha',
        },
      })
    })

    expect(result.current.remoteRole).toBe('observer')
    expect(result.current.remoteCanControl).toBe(false)
    expect(result.current.remoteControllerUser).toBe('alice')
    expect(result.current.remoteViewerCount).toBe(2)

    act(() => {
      result.current.handleTakeover()
    })

    expect(JSON.parse(socket.sent.at(-1) || '{}')).toMatchObject({
      type: 'takeover',
      data: {},
      action_id: 'action-1',
    })
  })

  it('sends paste_text when clipboard text is pasted into an armed controller session', () => {
    const { result } = renderHook(() => useRemoteControl())

    const container = document.createElement('div')
    const image = document.createElement('img')
    image.getBoundingClientRect = () =>
      ({
        left: 0,
        top: 0,
        width: 393,
        height: 873,
        right: 393,
        bottom: 873,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      }) as DOMRect
    container.appendChild(image)

    act(() => {
      result.current.openRemoteModal({
        platform: 'facebook',
        profileName: 'alpha',
        displayName: 'Alpha',
        valid: true,
      })
      Object.assign(result.current.screenshotContainerRef, { current: container })
    })

    const socket = socketRegistry.instances[0]
    act(() => {
      socket.emitOpen()
      socket.emitMessage({
        type: 'state',
        data: {
          url: 'https://example.com',
          role: 'controller',
          can_control: true,
          controller_user: 'tester',
          viewer_count: 1,
          lease_id: 'lease-alpha',
        },
      })
    })

    act(() => {
      result.current.handleRemotePointerDown({
        clientX: 20,
        clientY: 30,
        preventDefault: vi.fn(),
        currentTarget: { focus: vi.fn() },
      } as unknown as RemotePointerDownEvent)
    })

    const pasteEvent = new Event('paste', { bubbles: true, cancelable: true })
    Object.defineProperty(pasteEvent, 'clipboardData', {
      value: { getData: () => 'hello world' },
    })

    act(() => {
      window.dispatchEvent(pasteEvent)
    })

    expect(JSON.parse(socket.sent.at(-1) || '{}')).toMatchObject({
      type: 'paste_text',
      data: { text: 'hello world' },
      action_id: 'action-1',
    })
  })

  it('stops a sole-controller lease on modal close without reconnecting', async () => {
    const { result } = renderHook(() => useRemoteControl())

    act(() => {
      result.current.openRemoteModal({
        platform: 'facebook',
        profileName: 'alpha',
        displayName: 'Alpha',
        valid: true,
      })
    })

    const socket = socketRegistry.instances[0]
    act(() => {
      socket.emitOpen()
      socket.emitMessage({
        type: 'state',
        data: {
          url: 'https://m.facebook.com/',
          role: 'controller',
          can_control: true,
          controller_user: 'tester',
          viewer_count: 1,
          lease_id: 'lease-alpha',
        },
      })
    })

    await act(async () => {
      await result.current.closeRemoteModal()
    })

    expect(fetchMock).toHaveBeenCalledWith('http://localhost:8000/sessions/alpha/remote/stop', {
      method: 'POST',
      headers: { Authorization: 'Bearer token' },
    })
    expect(socketRegistry.instances).toHaveLength(1)
    expect(result.current.remoteModalOpen).toBe(false)
  })

  it('does not stop the lease when an observer closes the modal', async () => {
    const { result } = renderHook(() => useRemoteControl())

    act(() => {
      result.current.openRemoteModal({
        platform: 'facebook',
        profileName: 'alpha',
        displayName: 'Alpha',
        valid: true,
      })
    })

    const socket = socketRegistry.instances[0]
    act(() => {
      socket.emitOpen()
      socket.emitMessage({
        type: 'state',
        data: {
          url: 'https://m.facebook.com/',
          role: 'observer',
          can_control: false,
          controller_user: 'alice',
          viewer_count: 2,
          lease_id: 'lease-alpha',
        },
      })
    })

    await act(async () => {
      await result.current.closeRemoteModal()
    })

    expect(fetchMock).not.toHaveBeenCalled()
    expect(socketRegistry.instances).toHaveLength(1)
    expect(result.current.remoteModalOpen).toBe(false)
  })

  it('closes the modal without reconnecting when the server closes the session', () => {
    const { result } = renderHook(() => useRemoteControl())

    act(() => {
      result.current.openRemoteModal({
        platform: 'facebook',
        profileName: 'alpha',
        displayName: 'Alpha',
        valid: true,
      })
    })

    const socket = socketRegistry.instances[0]
    act(() => {
      socket.emitOpen()
      socket.emitMessage({
        type: 'session_closed',
        data: { reason: 'manual_stop' },
      })
    })

    expect(result.current.remoteModalOpen).toBe(false)
    expect(socketRegistry.instances).toHaveLength(1)
  })

  it('closes the modal without reconnecting on terminal remote errors', () => {
    const { result } = renderHook(() => useRemoteControl())

    act(() => {
      result.current.openRemoteModal({
        platform: 'facebook',
        profileName: 'alpha',
        displayName: 'Alpha',
        valid: true,
      })
    })

    const socket = socketRegistry.instances[0]
    act(() => {
      socket.emitOpen()
      socket.emitMessage({
        type: 'error',
        data: {
          message: 'remote capacity full: 2/2 active leases',
          code: 'remote_capacity_full',
        },
      })
    })

    expect(mockedToast.error).toHaveBeenCalledWith('remote capacity full: 2/2 active leases')
    expect(result.current.remoteModalOpen).toBe(false)
    expect(socketRegistry.instances).toHaveLength(1)
  })

  it('stops a sole-controller lease on pagehide with keepalive', () => {
    const { result } = renderHook(() => useRemoteControl())

    act(() => {
      result.current.openRemoteModal({
        platform: 'facebook',
        profileName: 'alpha',
        displayName: 'Alpha',
        valid: true,
      })
    })

    const socket = socketRegistry.instances[0]
    act(() => {
      socket.emitOpen()
      socket.emitMessage({
        type: 'state',
        data: {
          url: 'https://m.facebook.com/',
          role: 'controller',
          can_control: true,
          controller_user: 'tester',
          viewer_count: 1,
          lease_id: 'lease-alpha',
        },
      })
    })

    act(() => {
      window.dispatchEvent(new Event('pagehide'))
    })

    expect(fetchMock).toHaveBeenCalledWith('http://localhost:8000/sessions/alpha/remote/stop', {
      method: 'POST',
      headers: { Authorization: 'Bearer token' },
      keepalive: true,
    })
  })
})
