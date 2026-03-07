import { useCallback, useEffect, useRef, useState } from 'react'
import type { MouseEvent as ReactMouseEvent, WheelEvent as ReactWheelEvent } from 'react'
import { toast } from 'sonner'

import { API_BASE, createAuthenticatedWebSocket } from '@/lib/api'
import { getAccessToken } from '@/lib/auth'
import type { ActionLogEntry, PendingUpload, RemoteSessionTarget } from '@/components/remote/types'
import { REMOTE_VIEWPORT_HEIGHT, REMOTE_VIEWPORT_WIDTH } from '@/components/remote/types'

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

function getUploadPath(session: RemoteSessionTarget): string {
  return `${API_BASE}/sessions/${encodeURIComponent(session.profileName)}/upload-image`
}

function getPrepareUploadPath(session: RemoteSessionTarget): string {
  return `${API_BASE}/sessions/${encodeURIComponent(session.profileName)}/prepare-file-upload`
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

  const remoteWsRef = useRef<WebSocket | null>(null)
  const screenshotContainerRef = useRef<HTMLDivElement>(null)
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const reconnectAttemptRef = useRef(0)
  const heartbeatIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const remoteModalOpenRef = useRef(remoteModalOpen)
  const remoteSessionRef = useRef<RemoteSessionTarget | null>(remoteSession)

  useEffect(() => {
    remoteModalOpenRef.current = remoteModalOpen
  }, [remoteModalOpen])

  useEffect(() => {
    remoteSessionRef.current = remoteSession
  }, [remoteSession])

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
      remoteWsRef.current.close()
      remoteWsRef.current = null
    }
    setRemoteConnected(false)
    setRemoteConnecting(false)
  }, [])

  const connectRemoteWebSocket = useCallback((session: RemoteSessionTarget) => {
    if (remoteWsRef.current) {
      remoteWsRef.current.close()
    }

    setRemoteConnecting(true)
    setRemoteProgress(null)

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
            case 'frame':
              setRemoteFrame(message.data.image)
              setRemoteProgress(null)
              break
            case 'state':
              setRemoteUrlInput(message.data.url || '')
              break
            case 'progress':
              setRemoteProgress(message.data.stage)
              break
            case 'browser_ready':
              setRemoteProgress(null)
              toast.success('browser ready')
              break
            case 'session_auto_heal_start':
              setRemoteProgress('auto_heal')
              toast.loading('recovering browser session...', { id: 'remote-auto-heal' })
              break
            case 'stream_restarted':
              setRemoteProgress('stream_restarted')
              toast.success('browser stream restarted')
              break
            case 'session_auto_heal_done':
              if (message.data?.success) {
                setRemoteProgress(null)
                toast.success('browser session recovered', { id: 'remote-auto-heal' })
              } else {
                toast.error(message.data?.error || 'browser recovery failed', { id: 'remote-auto-heal' })
              }
              break
            case 'session_idle_timeout_close':
              toast('session closed after 5 minutes of idle time')
              break
            case 'action_result':
              setActionLog((prev) =>
                prev.map((entry) =>
                  entry.id === message.data.action_id
                    ? { ...entry, status: message.data.success ? 'success' : 'failed' }
                    : entry,
                ),
              )
              break
            case 'error':
              toast.error(message.data.message)
              break
          }
        } catch (error) {
          console.error('failed to parse remote ws message:', error)
        }
      }

      ws.onclose = () => {
        setRemoteConnected(false)
        setRemoteConnecting(false)

        if (heartbeatIntervalRef.current) {
          clearInterval(heartbeatIntervalRef.current)
          heartbeatIntervalRef.current = null
        }

        if (remoteModalOpenRef.current && reconnectAttemptRef.current < 5) {
          const delay = Math.min(1000 * Math.pow(2, reconnectAttemptRef.current), 10000)
          toast.loading('reconnecting...', { id: 'reconnect' })
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
    }
  }, [])

  const sendRemoteAction = useCallback((action: { type: string; data: Record<string, unknown> }) => {
    if (remoteWsRef.current?.readyState === WebSocket.OPEN) {
      const actionId = crypto.randomUUID()
      remoteWsRef.current.send(JSON.stringify({ ...action, action_id: actionId }))
      return actionId
    }
    return null
  }, [])

  const openRemoteModal = useCallback((session: RemoteSessionTarget) => {
    setRemoteSession(session)
    setRemoteModalOpen(true)
    setRemoteFrame(null)
    setActionLog([])
    setPendingUpload(null)
    setUploadReady(false)
    connectRemoteWebSocket(session)
  }, [connectRemoteWebSocket])

  const closeRemoteModal = useCallback(() => {
    disconnectRemoteWebSocket()
    setRemoteModalOpen(false)
    setRemoteSession(null)
    setRemoteFrame(null)
    setRemoteProgress(null)
    setRemoteUrlInput('')
    setActionLog([])
    setPendingUpload(null)
    setUploadReady(false)
  }, [disconnectRemoteWebSocket])

  const handleRemoteClick = useCallback((e: ReactMouseEvent<HTMLDivElement>) => {
    if (!remoteConnected || !screenshotContainerRef.current) return

    const img = screenshotContainerRef.current.querySelector('img')
    if (!img) return

    const imgRect = img.getBoundingClientRect()
    const scale = imgRect.width / REMOTE_VIEWPORT_WIDTH
    const relativeX = e.clientX - imgRect.left
    const relativeY = e.clientY - imgRect.top

    if (relativeX < 0 || relativeX > imgRect.width || relativeY < 0 || relativeY > imgRect.height) {
      return
    }

    const x = Math.round(relativeX / scale)
    const y = Math.round(relativeY / scale)

    const actionId = sendRemoteAction({ type: 'click', data: { x, y } })
    if (actionId) {
      addActionLogEntry('click', `click at (${x}, ${y})`, actionId)
    }
  }, [addActionLogEntry, remoteConnected, sendRemoteAction])

  const handleRemoteScroll = useCallback((e: ReactWheelEvent<HTMLDivElement>) => {
    if (!remoteConnected) return
    e.preventDefault()

    const actionId = sendRemoteAction({
      type: 'scroll',
      data: { x: REMOTE_VIEWPORT_WIDTH / 2, y: REMOTE_VIEWPORT_HEIGHT / 2, deltaY: e.deltaY },
    })
    if (actionId) {
      const direction = e.deltaY > 0 ? 'down' : 'up'
      addActionLogEntry('scroll', `scroll ${direction}`, actionId)
    }
  }, [addActionLogEntry, remoteConnected, sendRemoteAction])

  useEffect(() => {
    if (!remoteModalOpen || !remoteConnected) return

    const handleKeyDown = (event: KeyboardEvent) => {
      const activeElement = document.activeElement
      if (activeElement?.tagName === 'INPUT' || activeElement?.tagName === 'TEXTAREA') {
        return
      }

      event.preventDefault()

      const modifiers: string[] = []
      if (event.ctrlKey) modifiers.push('Control')
      if (event.altKey) modifiers.push('Alt')
      if (event.shiftKey) modifiers.push('Shift')
      if (event.metaKey) modifiers.push('Meta')

      const actionId = sendRemoteAction({
        type: 'key',
        data: { key: event.key, modifiers },
      })

      if (actionId) {
        const keyDisplay = modifiers.length > 0 ? `${modifiers.join('+')}+${event.key}` : event.key
        addActionLogEntry('key', `key: ${keyDisplay}`, actionId)
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [addActionLogEntry, remoteConnected, remoteModalOpen, sendRemoteAction])

  const handleRemoteNavigate = useCallback(() => {
    if (!remoteConnected || !remoteUrlInput.trim()) return

    let url = remoteUrlInput.trim()
    if (!url.startsWith('http://') && !url.startsWith('https://')) {
      url = `https://${url}`
    }

    const actionId = sendRemoteAction({ type: 'navigate', data: { url } })
    if (actionId) {
      addActionLogEntry('navigate', `navigate to ${url}`, actionId)
    }
  }, [addActionLogEntry, remoteConnected, remoteUrlInput, sendRemoteAction])

  const handleRemoteRestart = useCallback(async () => {
    if (!remoteSession) return
    try {
      setRemoteProgress('auto_heal')
      setRemoteFrame(null)
      const response = await fetch(getRemoteRestartPath(remoteSession), {
        method: 'POST',
        headers: getAuthHeaders(),
      })
      const data = await response.json()
      if (!response.ok || !data.success) {
        throw new Error(data.error || data.detail || 'failed to restart remote browser')
      }
      toast.success('remote browser restart triggered')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'failed to restart remote browser')
      setRemoteProgress(null)
    }
  }, [getAuthHeaders, remoteSession])

  const handleImageUpload = useCallback(async (file: File) => {
    if (!remoteSession || remoteSession.platform !== 'facebook') return

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
        })
        setUploadReady(false)
        toast.success(`image uploaded: ${result.filename}`)
      } else {
        toast.error(`upload failed: ${result.error}`)
      }
    } catch (error) {
      toast.error(`upload error: ${error}`)
    }
  }, [getAuthHeaders, remoteSession])

  const prepareFileUpload = useCallback(async () => {
    if (!remoteSession || remoteSession.platform !== 'facebook') return

    try {
      const response = await fetch(getPrepareUploadPath(remoteSession), {
        method: 'POST',
        headers: getAuthHeaders(),
      })
      const result = await response.json()
      if (result.success) {
        setUploadReady(true)
        toast.success('file ready! click the upload button on facebook.')
      } else {
        toast.error(result.error || 'failed to prepare upload')
      }
    } catch (error) {
      toast.error(`error: ${error}`)
    }
  }, [getAuthHeaders, remoteSession])

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
    screenshotContainerRef,
    openRemoteModal,
    closeRemoteModal,
    handleRemoteClick,
    handleRemoteScroll,
    handleRemoteNavigate,
    handleRemoteRestart,
    handleImageUpload,
    prepareFileUpload,
  }
}
