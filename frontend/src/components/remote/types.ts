export type RemotePlatform = 'facebook' | 'reddit'

export interface RemoteSessionTarget {
  platform: RemotePlatform
  profileName: string
  displayName?: string | null
  valid?: boolean | null
}

export interface ActionLogEntry {
  id: string
  timestamp: string
  type: 'click' | 'scroll' | 'key' | 'navigate' | 'type'
  details: string
  status: 'sent' | 'success' | 'failed'
}

export interface PendingUpload {
  filename: string
  size: number
  imageId: string
}

export const REMOTE_VIEWPORT_WIDTH = 393
export const REMOTE_VIEWPORT_HEIGHT = 873
