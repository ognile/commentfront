import { useCallback, useEffect, useRef, useState } from 'react'
import type {
  PointerEvent as ReactPointerEvent,
} from 'react'
import { toast } from 'sonner'

import type { ActionLogEntry, PendingUpload, RemoteSessionTarget } from '@/components/remote/types'
import { REMOTE_VIEWPORT_HEIGHT, REMOTE_VIEWPORT_WIDTH } from '@/components/remote/types'
import { API_BASE, createAuthenticatedWebSocket } from '@/lib/api'
import { getAccessToken } from '@/lib/auth'

const TERMINAL_REMOTE_ERROR_CODES = new Set([
  'profile_busy',
  'remote_capacity_full',
  'remote_session_closed',
  'remote_session_not_found',
  'remote_viewer_disconnected',
])

type RemoteWheelEvent = Pick<WheelEvent, 'clientX' | 'clientY' | 'deltaY' | 'preventDefault'>

function getRemoteWsPath(session: RemoteSessionTarget): string {
  const encodedProfile = encodeURIComponent(session.profileName)
  if (session.platform === 'reddit') {
    return `/ws/reddit/session/${encodedProfile}/control`
  }
  return `/ws/session/${encodedProfile}/control`
}

function getRemoteRestartPath(session: RemoteSessionTarget): string {
  const encodedProfile = encodeURIComponent(session.profileName)
  if (session.platform === 'reddit') {
    return `${API_BASE}/reddit/sessions/${encodedProfile}/remote/restart`
  }
  return `${API_BASE}/sessions/${encodedProfile}/remote/restart`
}

function getRemoteStopPath(session: RemoteSessionTarget): string {
  const encodedProfile = encodeURIComponent(session.profileName)
  if (session.platform === 'reddit') {
    return `${API_BASE}/reddit/sessions/${encodedProfile}/remote/stop`
  }
  return `${API_BASE}/sessions/${encodedProfile}/remote/stop`
}

function getUploadPath(session: RemoteSessionTarget): string {
  return `${API_BASE}/sessions/${encodeURIComponent(session.profileName)}/upload-image`
}

function getPrepareUploadPath(session: RemoteSessionTarget): string {
  return `${API_BASE}/sessions/${encodeURIComponent(session.profileName)}/prepare-file-upload`
}

function normalizeRemoteKey(key: string): string {
  switch (key) {
    case ' ':
    case 'Spacebar':
      return 'Space'
    case 'Esc':
      return 'Escape'
    case 'Del':
      return 'Delete'
    case 'Up':
      return 'ArrowUp'
    case 'Down':
      return 'ArrowDown'
    case 'Left':
      return 'ArrowLeft'
    case 'Right':
      return 'ArrowRight'
    default:
      return key
  }
}

function isEditableElement(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false
  }
  if (target.isContentEditable) {
    return true
  }
  return target.tagName === 'INPUT' || target.tagName === 'TEXTAREA'
}

export function useRemoteControl() {
  const [remoteModalOpen, setRemoteModalOpen] = useState(false)
  const [remoteSession, setRemoteSession] = useState<RemoteSessionTarget | null>(null)
  const [remoteFrame, setRemoteFrame] = useState<string | null>(null)
  const [remoteConnected, setRemoteConnected] = useState(false)
  const [remoteConnecting, setRemoteConnecting] = useState(false)
  const [remoteProgress, setRemoteProgress] = useState<string | null>(null)
  const [remoteUrlInput, setRemoteUrlInput] = useState('')
  const [actionLog, setActionLog] = useState<ActionLogEntry[]>([])
  const [pendingUpload, setPendingUpload] = useState<PendingUpload | null>(null)
  const [uploadReady, setUploadReady] = useState(false)
  const [remoteRole, setRemoteRole] = useState<'controller' | 'observer' | null>(null)
  const [remoteCanControl, setRemoteCanControl] = useState(false)
  const [remoteControllerUser, setRemoteControllerUser] = useState<string | null>(null)
  const [remoteViewerCount, setRemoteViewerCount] = useState(0)
  const [remoteLeaseId, setRemoteLeaseId] = useState<string | null>(null)
  const [keyboardCaptureEnabled, setKeyboardCaptureEnabled] = useState(false)

  const remoteWsRef = useRef<WebSocket | null>(null)
  const screenshotContainerRef = useRef<HTMLDivElement>(null)
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const reconnectAttemptRef = useRef(0)
  const heartbeatIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const remoteModalOpenRef = useRef(remoteModalOpen)
  const remoteSessionRef = useRef<RemoteSessionTarget | null>(remoteSession)
  const reconnectEnabledRef = useRef(false)
  const terminalSocketCloseRef = useRef(false)
  const pointerStartRef = useRef<{ x: number; y: number } | null>(null)
  const activeKeysRef = useRef<Set<string>>(new Set())

  useEffect(() => {
    remoteModalOpenRef.current = remoteModalOpen
  }, [remoteModalOpen])

  useEffect(() => {
    remoteSessionRef.current = remoteSession
  }, [remoteSession])

  const resetRemoteLeaseState = useCallback(() => {
    setRemoteRole(null)
    setRemoteCanControl(false)
    setRemoteControllerUser(null)
    setRemoteViewerCount(0)
    setRemoteLeaseId(null)
    setKeyboardCaptureEnabled(false)
    pointerStartRef.current = null
    activeKeysRef.current.clear()
  }, [])

  const clearRemoteModalState = useCallback(() => {
    remoteModalOpenRef.current = false
    remoteSessionRef.current = null
    setRemoteModalOpen(false)
    setRemoteSession(null)
    setRemoteFrame(null)
    setRemoteProgress(null)
    setRemoteUrlInput('')
    setActionLog([])
    setPendingUpload(null)
    setUploadReady(false)
  }, [])

  const getAuthHeaders = useCallback((): HeadersInit => {
    const token = getAccessToken()
    return token ? { Authorization: `Bearer ${token}` } : {}
  }, [])

  const addActionLogEntry = useCallback((type: ActionLogEntry['type'], details: string, actionId: string) => {
    const entry: ActionLogEntry = {
      id: actionId,
      timestamp: new Date().toISOString(),
      type,
      details,
      status: 'sent',
    }
    setActionLog((prev) => [entry, ...prev].slice(0, 100))
  }, [])

  const updateLeaseState = useCallback((payload: Record<string, unknown>) => {
    setRemoteUrlInput(typeof payload.url === 'string' ? payload.url : '')
    setRemoteRole(payload.role === 'observer' ? 'observer' : payload.role === 'controller' ? 'controller' : null)
    setRemoteCanControl(Boolean(payload.can_control))
    setRemoteControllerUser(typeof payload.controller_user === 'string' ? payload.controller_user : null)
    setRemoteViewerCount(typeof payload.viewer_count === 'number' ? payload.viewer_count : 0)
    setRemoteLeaseId(typeof payload.lease_id === 'string' ? payload.lease_id : null)
  }, [])

  const disconnectRemoteWebSocket = useCallback(() => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current)
      reconnectTimeoutRef.current = null
    }
    if (heartbeatIntervalRef.current) {
      clearInterval(heartbeatIntervalRef.current)
      heartbeatIntervalRef.current = null
    }
    if (remoteWsRef.current) {
      remoteWsRef.current.onclose = null
      remoteWsRef.current.close()
      remoteWsRef.current = null
    }
    setRemoteConnected(false)
    setRemoteConnecting(false)
    resetRemoteLeaseState()
  }, [resetRemoteLeaseState])

  const terminateRemoteModal = useCallback(() => {
    reconnectEnabledRef.current = false
    terminalSocketCloseRef.current = true
    disconnectRemoteWebSocket()
    clearRemoteModalState()
  }, [clearRemoteModalState, disconnectRemoteWebSocket])

  const connectRemoteWebSocket = useCallback((session: RemoteSessionTarget) => {
    if (remoteWsRef.current) {
      remoteWsRef.current.onclose = null
      remoteWsRef.current.close()
      remoteWsRef.current = null
    }

    setRemoteConnecting(true)
    setRemoteProgress('connecting')
    terminalSocketCloseRef.current = false

    try {
      const ws = createAuthenticatedWebSocket(getRemoteWsPath(session))

      ws.onopen = () => {
        setRemoteConnected(true)
        setRemoteConnecting(false)
        reconnectAttemptRef.current = 0
        toast.success('browser connected')

        if (heartbeatIntervalRef.current) {
          clearInterval(heartbeatIntervalRef.current)
        }
        heartbeatIntervalRef.current = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'ping' }))
          }
        }, 30000)
      }

      ws.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data)

          switch (message.type) {
            case 'frame': {
              const format = message.data?.format || 'jpeg'
              const image = message.data?.image || ''
              setRemoteFrame(image ? `data:image/${format};base64,${image}` : null)
              setRemoteProgress(null)
              break
            }
            case 'state':
              updateLeaseState(message.data || {})
              break
            case 'lease_role':
              setRemoteRole(message.data?.role === 'observer' ? 'observer' : message.data?.role === 'controller' ? 'controller' : null)
              setRemoteCanControl(Boolean(message.data?.can_control))
              setRemoteControllerUser(message.data?.controller_user || null)
              break
            case 'browser_ready':
              setRemoteLeaseId(message.data?.lease_id || null)
              setRemoteProgress(null)
              toast.success('browser ready')
              break
            case 'session_idle_timeout_close':
              toast('session closed after idle timeout')
              terminateRemoteModal()
              break
            case 'session_closed':
              toast(message.data?.reason ? `session closed: ${message.data.reason}` : 'session closed')
              terminateRemoteModal()
              break
            case 'action_result':
              setActionLog((prev) =>
                prev.map((entry) =>
                  entry.id === message.data.action_id
                    ? { ...entry, status: message.data.success ? 'success' : 'failed' }
                    : entry,
                ),
              )
              if (!message.data.success && message.data.error) {
                toast.error(message.data.error)
              }
              break
            case 'error':
              if (TERMINAL_REMOTE_ERROR_CODES.has(String(message.data?.code || ''))) {
                toast.error(message.data?.message || 'remote browser error')
                terminateRemoteModal()
                break
              }
              toast.error(message.data?.message || 'remote browser error')
              break
          }
        } catch (error) {
          console.error('failed to parse remote ws message:', error)
        }
      }

      ws.onclose = () => {
        if (remoteWsRef.current === ws) {
          remoteWsRef.current = null
        }
        setRemoteConnected(false)
        setRemoteConnecting(false)
        setKeyboardCaptureEnabled(false)

        if (heartbeatIntervalRef.current) {
          clearInterval(heartbeatIntervalRef.current)
          heartbeatIntervalRef.current = null
        }

        if (
          reconnectEnabledRef.current &&
          remoteModalOpenRef.current &&
          remoteSessionRef.current &&
          !terminalSocketCloseRef.current &&
          reconnectAttemptRef.current < 5 &&
          remoteWsRef.current === null
        ) {
          const delay = Math.min(1000 * Math.pow(2, reconnectAttemptRef.current), 10000)
          toast.loading('reconnecting...', { id: 'remote-reconnect' })
          reconnectTimeoutRef.current = setTimeout(() => {
            reconnectAttemptRef.current += 1
            if (remoteSessionRef.current) {
              connectRemoteWebSocket(remoteSessionRef.current)
            }
          }, delay)
        }
      }

      ws.onerror = (error) => {
        console.error('remote ws error:', error)
      }

      remoteWsRef.current = ws
    } catch (error) {
      console.error('failed to create remote websocket:', error)
      setRemoteConnecting(false)
      setRemoteProgress(null)
    }
  }, [terminateRemoteModal, updateLeaseState])

  const sendRemoteAction = useCallback((action: { type: string; data?: Record<string, unknown> }) => {
    if (remoteWsRef.current?.readyState === WebSocket.OPEN) {
      const actionId = crypto.randomUUID()
      remoteWsRef.current.send(JSON.stringify({ ...action, action_id: actionId }))
      return actionId
    }
    return null
  }, [])

  const resolveViewportPoint = useCallback((clientX: number, clientY: number) => {
    const img = screenshotContainerRef.current?.querySelector('img')
    if (!img) {
      return null
    }

    const rect = img.getBoundingClientRect()
    const relativeX = clientX - rect.left
    const relativeY = clientY - rect.top

    if (relativeX < 0 || relativeX > rect.width || relativeY < 0 || relativeY > rect.height) {
      return null
    }

    const x = Math.round((relativeX / rect.width) * REMOTE_VIEWPORT_WIDTH)
    const y = Math.round((relativeY / rect.height) * REMOTE_VIEWPORT_HEIGHT)
    return { x, y }
  }, [])

  const openRemoteModal = useCallback((session: RemoteSessionTarget) => {
    remoteSessionRef.current = session
    remoteModalOpenRef.current = true
    reconnectEnabledRef.current = true
    setRemoteSession(session)
    setRemoteModalOpen(true)
    setRemoteFrame(null)
    setRemoteProgress('connecting')
    setActionLog([])
    setPendingUpload(null)
    setUploadReady(false)
    resetRemoteLeaseState()
    connectRemoteWebSocket(session)
  }, [connectRemoteWebSocket, resetRemoteLeaseState])

  const closeRemoteModal = useCallback(async () => {
    const session = remoteSessionRef.current
    const shouldStopLease = Boolean(
      session &&
      remoteRole !== 'observer' &&
      remoteViewerCount <= 1,
    )

    reconnectEnabledRef.current = false
    terminalSocketCloseRef.current = true
    disconnectRemoteWebSocket()
    clearRemoteModalState()
    if (!session || !shouldStopLease) {
      return
    }

    try {
      const response = await fetch(getRemoteStopPath(session), {
        method: 'POST',
        headers: getAuthHeaders(),
      })
      if (response.status === 404) {
        return
      }
      const result = await response.json()
      if (!response.ok || result.success === false) {
        throw new Error(result.error || result.detail?.message || result.detail || 'failed to stop remote browser')
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'failed to stop remote browser')
    }
  }, [clearRemoteModalState, disconnectRemoteWebSocket, getAuthHeaders, remoteRole, remoteViewerCount])

  const handleRemotePointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (!remoteConnected || !remoteCanControl) return

    const point = resolveViewportPoint(event.clientX, event.clientY)
    if (!point) return

    event.preventDefault()
    event.currentTarget.focus()
    pointerStartRef.current = point
    setKeyboardCaptureEnabled(true)
  }, [remoteCanControl, remoteConnected, resolveViewportPoint])

  const handleRemotePointerUp = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    const start = pointerStartRef.current
    pointerStartRef.current = null
    if (!remoteConnected || !remoteCanControl || !start) return

    const end = resolveViewportPoint(event.clientX, event.clientY) || start
    const moved = Math.abs(end.x - start.x) > 8 || Math.abs(end.y - start.y) > 8

    if (moved) {
      const actionId = sendRemoteAction({
        type: 'drag',
        data: {
          startX: start.x,
          startY: start.y,
          endX: end.x,
          endY: end.y,
        },
      })
      if (actionId) {
        addActionLogEntry('drag', `drag from (${start.x}, ${start.y}) to (${end.x}, ${end.y})`, actionId)
      }
      return
    }

    const actionId = sendRemoteAction({ type: 'tap', data: start })
    if (actionId) {
      addActionLogEntry('tap', `tap at (${start.x}, ${start.y})`, actionId)
    }
  }, [addActionLogEntry, remoteCanControl, remoteConnected, resolveViewportPoint, sendRemoteAction])

  const handleRemotePointerCancel = useCallback(() => {
    pointerStartRef.current = null
  }, [])

  const handleRemoteScroll = useCallback((event: RemoteWheelEvent) => {
    if (!remoteConnected || !remoteCanControl) return
    event.preventDefault()

    const point = resolveViewportPoint(event.clientX, event.clientY)
    if (!point) return

    const actionId = sendRemoteAction({
      type: 'scroll_gesture',
      data: { x: point.x, y: point.y, deltaY: event.deltaY },
    })
    if (actionId) {
      const direction = event.deltaY > 0 ? 'down' : 'up'
      addActionLogEntry('scroll', `scroll ${direction}`, actionId)
    }
  }, [addActionLogEntry, remoteCanControl, remoteConnected, resolveViewportPoint, sendRemoteAction])

  useEffect(() => {
    if (!remoteModalOpen) return
    const container = screenshotContainerRef.current
    if (!container) return

    const handleWheel = (event: WheelEvent) => {
      handleRemoteScroll(event)
    }

    container.addEventListener('wheel', handleWheel, { passive: false })
    return () => {
      container.removeEventListener('wheel', handleWheel)
    }
  }, [handleRemoteScroll, remoteModalOpen])

  useEffect(() => {
    if (!remoteModalOpen || !remoteSession) return

    const handlePageHide = () => {
      const session = remoteSessionRef.current
      if (!session || remoteRole === 'observer' || remoteViewerCount > 1) {
        return
      }

      reconnectEnabledRef.current = false
      const token = getAccessToken()
      void fetch(getRemoteStopPath(session), {
        method: 'POST',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        keepalive: true,
      }).catch(() => undefined)
    }

    window.addEventListener('pagehide', handlePageHide)
    return () => {
      window.removeEventListener('pagehide', handlePageHide)
    }
  }, [remoteModalOpen, remoteRole, remoteSession, remoteViewerCount])

  useEffect(() => {
    if (!remoteModalOpen || !remoteConnected || !remoteCanControl || !keyboardCaptureEnabled) return
    const activeKeys = activeKeysRef.current

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.isComposing || isEditableElement(event.target)) {
        return
      }

      const key = normalizeRemoteKey(event.key)
      const hasShortcutModifier = event.ctrlKey || event.metaKey || event.altKey

      if (!hasShortcutModifier && key.length === 1 && !event.repeat) {
        event.preventDefault()
        const actionId = sendRemoteAction({ type: 'text_input', data: { text: key } })
        if (actionId) {
          addActionLogEntry('type', `type "${key}"`, actionId)
        }
        return
      }

      if (event.repeat && activeKeys.has(key)) {
        event.preventDefault()
        return
      }

      event.preventDefault()
      activeKeys.add(key)

      const actionId = sendRemoteAction({ type: 'key_down', data: { key } })
      if (actionId) {
        addActionLogEntry('key', `key down: ${key}`, actionId)
      }
    }

    const handleKeyUp = (event: KeyboardEvent) => {
      if (event.isComposing || isEditableElement(event.target)) {
        return
      }

      const key = normalizeRemoteKey(event.key)
      if (!activeKeys.has(key)) {
        return
      }

      event.preventDefault()
      activeKeys.delete(key)
      sendRemoteAction({ type: 'key_up', data: { key } })
    }

    const handlePaste = (event: ClipboardEvent) => {
      if (isEditableElement(event.target)) {
        return
      }

      const text = event.clipboardData?.getData('text')?.replace(/\r\n/g, '\n') || ''
      if (!text) {
        return
      }

      event.preventDefault()
      const actionId = sendRemoteAction({ type: 'paste_text', data: { text } })
      if (actionId) {
        addActionLogEntry('paste', `paste ${text.length} chars`, actionId)
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    window.addEventListener('keyup', handleKeyUp)
    window.addEventListener('paste', handlePaste)

    return () => {
      window.removeEventListener('keydown', handleKeyDown)
      window.removeEventListener('keyup', handleKeyUp)
      window.removeEventListener('paste', handlePaste)
      activeKeys.clear()
    }
  }, [addActionLogEntry, keyboardCaptureEnabled, remoteCanControl, remoteConnected, remoteModalOpen, sendRemoteAction])

  const handleRemoteNavigate = useCallback(() => {
    if (!remoteConnected || !remoteCanControl || !remoteUrlInput.trim()) return

    let url = remoteUrlInput.trim()
    if (!url.startsWith('http://') && !url.startsWith('https://')) {
      url = `https://${url}`
    }

    const actionId = sendRemoteAction({ type: 'navigate', data: { url } })
    if (actionId) {
      addActionLogEntry('navigate', `navigate to ${url}`, actionId)
    }
  }, [addActionLogEntry, remoteCanControl, remoteConnected, remoteUrlInput, sendRemoteAction])

  const handleTakeover = useCallback(() => {
    if (!remoteConnected || remoteRole !== 'observer') return

    const actionId = sendRemoteAction({ type: 'takeover', data: {} })
    if (actionId) {
      addActionLogEntry('takeover', 'request controller takeover', actionId)
    }
  }, [addActionLogEntry, remoteConnected, remoteRole, sendRemoteAction])

  const handleRemoteRestart = useCallback(async () => {
    if (!remoteSession || !remoteCanControl) return
    try {
      setRemoteProgress('restarting')
      setRemoteFrame(null)
      const response = await fetch(getRemoteRestartPath(remoteSession), {
        method: 'POST',
        headers: getAuthHeaders(),
      })
      const data = await response.json()
      if (!response.ok || !data.success) {
        throw new Error(data.error || data.detail?.message || data.detail || 'failed to restart remote browser')
      }
      toast.success('remote browser restart triggered')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'failed to restart remote browser')
      setRemoteProgress(null)
    }
  }, [getAuthHeaders, remoteCanControl, remoteSession])

  const handleImageUpload = useCallback(async (file: File) => {
    if (!remoteSession || remoteSession.platform !== 'facebook' || !remoteCanControl) return

    const allowedTypes = ['image/jpeg', 'image/png', 'image/webp']
    if (!allowedTypes.includes(file.type)) {
      toast.error('please upload a jpg, png, or webp image')
      return
    }

    if (file.size > 10 * 1024 * 1024) {
      toast.error('image must be under 10mb')
      return
    }

    const formData = new FormData()
    formData.append('file', file)

    try {
      const response = await fetch(getUploadPath(remoteSession), {
        method: 'POST',
        headers: getAuthHeaders(),
        body: formData,
      })

      const result = await response.json()
      if (result.success) {
        setPendingUpload({
          filename: result.filename,
          size: result.size,
          imageId: result.image_id,
          expiresAt: result.expires_at,
        })
        setUploadReady(false)
        toast.success(`image uploaded: ${result.filename}`)
      } else {
        toast.error(`upload failed: ${result.error}`)
      }
    } catch (error) {
      toast.error(`upload error: ${error}`)
    }
  }, [getAuthHeaders, remoteCanControl, remoteSession])

  const prepareFileUpload = useCallback(async () => {
    if (!remoteSession || remoteSession.platform !== 'facebook' || !remoteCanControl) return

    try {
      const response = await fetch(getPrepareUploadPath(remoteSession), {
        method: 'POST',
        headers: getAuthHeaders(),
      })
      const result = await response.json()
      if (result.success) {
        setUploadReady(true)
        toast.success('file ready. click the upload control in the browser.')
      } else {
        toast.error(result.error || result.detail?.message || 'failed to prepare upload')
      }
    } catch (error) {
      toast.error(`error: ${error}`)
    }
  }, [getAuthHeaders, remoteCanControl, remoteSession])

  return {
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
    openRemoteModal,
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
  }
}
