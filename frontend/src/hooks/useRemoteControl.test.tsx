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

const { mockedToast, socketRegistry, fetchMock, clipboardReadTextMock, clipboardWriteTextMock } = vi.hoisted(() => ({
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
  clipboardReadTextMock: vi.fn(async () => 'clipboard text'),
  clipboardWriteTextMock: vi.fn(async () => undefined),
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
    Object.defineProperty(window.navigator, 'clipboard', {
      configurable: true,
      value: {
        readText: clipboardReadTextMock,
        writeText: clipboardWriteTextMock,
      },
    })
    clipboardReadTextMock.mockReset()
    clipboardWriteTextMock.mockReset()
    clipboardReadTextMock.mockResolvedValue('clipboard text')
    clipboardWriteTextMock.mockResolvedValue(undefined)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function openArmedController(result: { current: ReturnType<typeof useRemoteControl> }) {
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

    return socket
  }

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
    const socket = openArmedController(result)

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

  it('uses meta+v to read the local clipboard and send paste_text', async () => {
    const { result } = renderHook(() => useRemoteControl())
    const socket = openArmedController(result)

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'v', metaKey: true, bubbles: true, cancelable: true }))
      await Promise.resolve()
    })

    expect(clipboardReadTextMock).toHaveBeenCalledTimes(1)
    const message = JSON.parse(socket.sent.at(-1) || '{}')
    expect(message).toMatchObject({
      type: 'paste_text',
      data: { text: 'clipboard text' },
      action_id: 'action-1',
    })

    act(() => {
      socket.emitMessage({
        type: 'action_result',
        data: {
          action_id: 'action-1',
          success: true,
          action: 'paste_text',
          focus_snapshot: { tag_name: 'div', is_content_editable: true },
          selection_kind: 'contenteditable',
          selection_length: 0,
        },
      })
    })

    expect(result.current.actionLog[0]?.status).toBe('success')
  })

  it('surfaces clipboard read failures on meta+v without sending a remote action', async () => {
    clipboardReadTextMock.mockRejectedValueOnce(new Error('clipboard read denied'))

    const { result } = renderHook(() => useRemoteControl())
    const socket = openArmedController(result)

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'v', metaKey: true, bubbles: true, cancelable: true }))
      await Promise.resolve()
    })

    expect(socket.sent).toHaveLength(0)
    expect(mockedToast.error).toHaveBeenCalledWith('clipboard read denied')
  })

  it('uses meta+a to send select_all', async () => {
    const { result } = renderHook(() => useRemoteControl())
    const socket = openArmedController(result)

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'a', metaKey: true, bubbles: true, cancelable: true }))
      await Promise.resolve()
    })

    expect(JSON.parse(socket.sent.at(-1) || '{}')).toMatchObject({
      type: 'select_all',
      data: {},
      action_id: 'action-1',
    })

    act(() => {
      socket.emitMessage({
        type: 'action_result',
        data: {
          action_id: 'action-1',
          success: true,
          action: 'select_all',
          selection_kind: 'contenteditable',
          selection_length: 12,
        },
      })
    })

    expect(result.current.actionLog[0]?.status).toBe('success')
  })

  it('uses meta+c to copy the remote selection into the local clipboard', async () => {
    const { result } = renderHook(() => useRemoteControl())
    const socket = openArmedController(result)

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'c', metaKey: true, bubbles: true, cancelable: true }))
      await Promise.resolve()
    })

    expect(JSON.parse(socket.sent.at(-1) || '{}')).toMatchObject({
      type: 'copy_selection',
      data: {},
      action_id: 'action-1',
    })

    await act(async () => {
      socket.emitMessage({
        type: 'action_result',
        data: {
          action_id: 'action-1',
          success: true,
          action: 'copy_selection',
          clipboard_text: 'remote copied text',
          selection_kind: 'contenteditable',
          selection_length: 18,
          can_delete: true,
          focus_snapshot: { tag_name: 'div', is_content_editable: true },
        },
      })
      await Promise.resolve()
    })

    expect(clipboardWriteTextMock).toHaveBeenCalledWith('remote copied text')
  })

  it('uses meta+x to copy first and only then delete the remote selection', async () => {
    const { result } = renderHook(() => useRemoteControl())
    const socket = openArmedController(result)

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'x', metaKey: true, bubbles: true, cancelable: true }))
      await Promise.resolve()
    })

    expect(JSON.parse(socket.sent.at(-1) || '{}')).toMatchObject({
      type: 'copy_selection',
      data: {},
      action_id: 'action-1',
    })

    await act(async () => {
      socket.emitMessage({
        type: 'action_result',
        data: {
          action_id: 'action-1',
          success: true,
          action: 'copy_selection',
          clipboard_text: 'remote cut text',
          selection_kind: 'contenteditable',
          selection_length: 15,
          can_delete: true,
          focus_snapshot: { tag_name: 'div', is_content_editable: true },
        },
      })
      await Promise.resolve()
    })

    expect(clipboardWriteTextMock).toHaveBeenCalledWith('remote cut text')
    expect(JSON.parse(socket.sent.at(-1) || '{}')).toMatchObject({
      type: 'delete_selection',
      data: {},
      action_id: 'action-2',
    })

    act(() => {
      socket.emitMessage({
        type: 'action_result',
        data: {
          action_id: 'action-2',
          success: true,
          action: 'delete_selection',
          selection_kind: 'contenteditable',
          selection_length: 15,
          can_delete: true,
        },
      })
    })

    expect(result.current.actionLog.some((entry) => entry.type === 'cut')).toBe(true)
  })

  it('does not delete on meta+x when clipboard write fails', async () => {
    clipboardWriteTextMock.mockRejectedValueOnce(new Error('clipboard denied'))

    const { result } = renderHook(() => useRemoteControl())
    const socket = openArmedController(result)

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'x', metaKey: true, bubbles: true, cancelable: true }))
      await Promise.resolve()
    })

    await act(async () => {
      socket.emitMessage({
        type: 'action_result',
        data: {
          action_id: 'action-1',
          success: true,
          action: 'copy_selection',
          clipboard_text: 'remote cut text',
          selection_kind: 'contenteditable',
          selection_length: 15,
          can_delete: true,
          focus_snapshot: { tag_name: 'div', is_content_editable: true },
        },
      })
      await Promise.resolve()
    })

    expect(socket.sent).toHaveLength(1)
    expect(mockedToast.error).toHaveBeenCalledWith('clipboard denied')
  })

  it('keeps plain typing on the text_input action path', () => {
    const { result } = renderHook(() => useRemoteControl())
    const socket = openArmedController(result)

    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'a', bubbles: true, cancelable: true }))
    })

    expect(JSON.parse(socket.sent.at(-1) || '{}')).toMatchObject({
      type: 'text_input',
      data: { text: 'a' },
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
