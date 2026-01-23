import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Checkbox } from "@/components/ui/checkbox"
import { Loader2, CheckCircle, XCircle, RefreshCw, Key, Copy, Trash2, Wifi, WifiOff, Eye, Upload, Globe, Plus, Play, AlertCircle, X, Mouse, LogOut, Shield, Tag, User, ChevronRight, RotateCw, BarChart3, Star, Check, Search } from "lucide-react"
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Toaster, toast } from 'sonner'
import { useAuth } from '@/contexts/AuthContext'
import { LoginPage } from '@/components/auth/LoginPage'
import { AdminTab } from '@/components/admin/AdminTab'
import { API_BASE, WS_BASE } from '@/lib/api'
import { getAccessToken } from '@/lib/auth'
import { PearlBackground } from '@/components/PearlBackground'
import { TagInput } from '@/components/TagInput'

interface Session {
  file: string;
  profile_name: string;
  display_name?: string;  // Pretty name for UI display (e.g., "Elizabeth Cruz")
  user_id: string | null;
  extracted_at: string;
  valid: boolean;
  proxy?: string;
  proxy_masked?: string;  // Masked proxy URL for display
  proxy_source?: string;  // "session" or "env" to show source
  profile_picture?: string | null;  // Base64 encoded PNG
  tags?: string[];  // Session tags for filtering
}

interface CampaignResult {
  profile_name: string;
  comment: string;
  success: boolean;
  verified: boolean;
  method: string;
  error: string | null;
  job_index: number;
  is_retry?: boolean;
  original_profile?: string;
  retried_at?: string;
  warmup?: {
    success: boolean;
    scrolls?: number;
    likes?: number;
    duration?: number;
    error?: string;
  };
  throttled?: boolean;
  throttle_reason?: string;
}

interface QueuedCampaign {
  id: string;
  url: string;
  comments: string[];
  duration_minutes: number;
  filter_tags?: string[];
  enable_warmup?: boolean;
  status: 'pending' | 'processing' | 'completed' | 'failed' | 'cancelled';
  created_at: string;
  created_by: string;
  started_at?: string;
  completed_at?: string;
  success_count?: number;
  total_count?: number;
  error?: string;
  current_job?: number;
  total_jobs?: number;
  current_profile?: string;
  results?: CampaignResult[];
  has_retries?: boolean;
  last_retry_at?: string;
}

interface QueueState {
  processor_running: boolean;
  current_campaign_id: string | null;
  pending_count: number;
  max_pending: number;
  pending: QueuedCampaign[];
  history: QueuedCampaign[];
}

interface LiveStatus {
  connected: boolean;
  currentStep: string;
  currentJob: number;
  totalJobs: number;
}

interface Credential {
  uid: string;
  profile_name: string | null;
  has_secret: boolean;
  created_at: string;
  session_connected?: boolean;
  session_valid?: boolean | null;
  session_profile_name?: string | null;  // Profile name from the linked session
}

interface OTPData {
  code: string | null;
  remaining_seconds: number;
  valid: boolean;
  error: string | null;
}

interface Proxy {
  id: string;
  name: string;
  url_masked: string;
  host: string | null;
  port: number | null;
  type: string;
  country: string;
  health_status: string;
  last_tested: string | null;
  success_rate: number | null;
  avg_response_ms: number | null;
  test_count: number;
  assigned_sessions: string[];
  created_at: string | null;
  is_system?: boolean;  // True for PROXY_URL system proxy
  is_default?: boolean;  // True if this is the user-set default proxy
}

interface SessionCreateStatus {
  uid: string;
  step: string;
  status: 'pending' | 'in_progress' | 'success' | 'failed' | 'needs_attention';
  error?: string;
}

// Remote control interfaces
interface ActionLogEntry {
  id: string;
  timestamp: string;
  type: 'click' | 'scroll' | 'key' | 'navigate' | 'type';
  details: string;
  status: 'sent' | 'success' | 'failed';
}

interface PendingUpload {
  filename: string;
  size: number;
  imageId: string;
}

interface GeminiObservation {
  timestamp: string;
  screenshot_name: string;
  operation_type: string;
  prompt_type: string;
  full_response: string;
  parsed_result: Record<string, unknown>;
  profile_name: string | null;
  campaign_id: string | null;
}

interface ProfileAnalytics {
  profile_name: string;
  status: string;
  last_used_at: string | null;
  usage_count: number;
  restriction_expires_at: string | null;
  restriction_reason: string | null;
  total_comments: number;
  success_rate: number;
  daily_stats: Record<string, { comments: number; success: number; failed: number }>;
  usage_history: Array<{
    timestamp: string;
    campaign_id: string | null;
    comment: string | null;
    success: boolean;
  }>;
}

interface AnalyticsSummary {
  today: { comments: number; success: number; success_rate: number };
  week: { comments: number; success: number; success_rate: number };
  profiles: { active: number; restricted: number; total: number };
}

// Mobile viewport dimensions
const VIEWPORT_WIDTH = 393;
const VIEWPORT_HEIGHT = 873;

// Format duration in minutes to human-readable string
const formatDuration = (minutes: number): string => {
  if (minutes < 60) {
    return `${minutes} minute${minutes !== 1 ? 's' : ''}`;
  }
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  if (mins === 0) {
    return `${hours} hour${hours !== 1 ? 's' : ''}`;
  }
  return `${hours}h ${mins}m`;
};

// Consolidate campaign results by job_index - latest result wins
// This ensures retries properly update the displayed status
const getConsolidatedResults = (results: CampaignResult[]): CampaignResult[] => {
  const byJobIndex: Record<number, CampaignResult> = {};

  // Process results in order - latest result (retry) overwrites earlier ones
  results.forEach(result => {
    const jobIdx = result.job_index;
    // Always overwrite with the latest result for this job_index
    // Retries come after original attempts in the array
    byJobIndex[jobIdx] = result;
  });

  // Return sorted by job_index for consistent display order
  return Object.values(byJobIndex).sort((a, b) => a.job_index - b.job_index);
};

// Normalize Facebook URL to extract unique post identifier
const normalizeUrl = (url: string): string => {
  try {
    // Try to extract post ID from various FB URL formats
    const patterns = [
      /posts\/(\d+)/,
      /story_fbid=(\d+)/,
      /permalink\/(\d+)/,
      /photos\/[^/]+\/(\d+)/,
      /\/(\d+)\/?$/
    ];
    for (const pattern of patterns) {
      const match = url.match(pattern);
      if (match) return match[1];
    }
    // Fallback: use the full URL lowercased
    return url.toLowerCase().trim();
  } catch {
    return url.toLowerCase().trim();
  }
};

function App() {
  // Auth state - must be first hook
  const { user, isAuthenticated, isLoading: authLoading, logout } = useAuth();

  const [url, setUrl] = useState('');
  const [comments, setComments] = useState('');
  const [sessions, setSessions] = useState<Session[]>([]);
  const [allTags, setAllTags] = useState<string[]>([]);
  const [sessionFilterTags, setSessionFilterTags] = useState<string[]>([]);
  const [sessionSearchQuery, setSessionSearchQuery] = useState('');
  const [sessionStatusFilters, setSessionStatusFilters] = useState<{
    valid?: boolean;
    hasProxy?: boolean;
  }>({});
  const [campaignFilterTags, setCampaignFilterTags] = useState<string[]>([]);
  const [credentials, setCredentials] = useState<Credential[]>([]);
  // isProcessing removed - now using queueState.processor_running instead
  const [sessionsLoading, setSessionsLoading] = useState(true);
  const [credentialsLoading, setCredentialsLoading] = useState(true);
  const [proxiesLoading, setProxiesLoading] = useState(true);
  const [campaignDuration, setCampaignDuration] = useState(30); // Duration in minutes (10-1440)
  const enableWarmup = true; // Warmup always enabled for new campaigns

  // Campaign queue state - synced with backend
  const [queueState, setQueueState] = useState<QueueState>({
    processor_running: false,
    current_campaign_id: null,
    pending_count: 0,
    max_pending: 50,
    pending: [],
    history: []
  });
  const [queueLoading, setQueueLoading] = useState(true);
  const [addingToQueue, setAddingToQueue] = useState(false);

  // Campaign details modal state
  const [selectedCampaign, setSelectedCampaign] = useState<QueuedCampaign | null>(null);
  const [retryingJobIndex, setRetryingJobIndex] = useState<number | null>(null);
  const [retryProfile, setRetryProfile] = useState<string>('');
  const [isRetrying, setIsRetrying] = useState(false);

  // Bulk retry state (simplified - no strategy selection needed)
  const [isBulkRetrying, setIsBulkRetrying] = useState(false);

  const [newUid, setNewUid] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [newSecret, setNewSecret] = useState('');
  const [newProfileName, setNewProfileName] = useState('');
  const [otpData, setOtpData] = useState<Record<string, OTPData>>({});
  const [isImporting, setIsImporting] = useState(false);

  // Proxy state
  const [proxies, setProxies] = useState<Proxy[]>([]);
  const [newProxyName, setNewProxyName] = useState('');
  const [newProxyUrl, setNewProxyUrl] = useState('');
  const [newProxyType, setNewProxyType] = useState('mobile');
  const [newProxyCountry, setNewProxyCountry] = useState('US');
  const [testingProxy, setTestingProxy] = useState<string | null>(null);
  const [settingDefaultProxy, setSettingDefaultProxy] = useState<string | null>(null);

  // Session creation state
  const [creatingSession, setCreatingSession] = useState<string | null>(null);
  const [sessionCreateStatus, setSessionCreateStatus] = useState<Record<string, SessionCreateStatus>>({});

  // Session refresh state
  const [refreshingSession, setRefreshingSession] = useState<string | null>(null);

  // Bulk session selection state
  const [selectedSessions, setSelectedSessions] = useState<Set<string>>(new Set());
  const [lastSelectedIndex, setLastSelectedIndex] = useState<number | null>(null);
  const [keepSelection, setKeepSelection] = useState(false);
  const [bulkRefreshing, setBulkRefreshing] = useState(false);
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [bulkTagModalOpen, setBulkTagModalOpen] = useState(false);

  // Batch session creation state
  const [selectedCredentials, setSelectedCredentials] = useState<Set<string>>(new Set());
  const [batchInProgress, setBatchInProgress] = useState(false);

  // Analytics / Debug state
  const [geminiObservations, setGeminiObservations] = useState<GeminiObservation[]>([]);
  const [loadingObservations, setLoadingObservations] = useState(false);
  const [expandedObservation, setExpandedObservation] = useState<number | null>(null);
  const [profileAnalytics, setProfileAnalytics] = useState<ProfileAnalytics[]>([]);
  const [analyticsSummary, setAnalyticsSummary] = useState<AnalyticsSummary | null>(null);
  const [loadingAnalytics, setLoadingAnalytics] = useState(false);
  const [expandedProfile, setExpandedProfile] = useState<string | null>(null);

  // WebSocket and live status
  const [liveStatus, setLiveStatus] = useState<LiveStatus>({
    connected: false,
    currentStep: 'idle',
    currentJob: 0,
    totalJobs: 0
  });
  const [screenshotKey, setScreenshotKey] = useState(0);
  const [activeTab, setActiveTab] = useState('campaign');
  const wsRef = useRef<WebSocket | null>(null);

  // Remote control state
  const [remoteModalOpen, setRemoteModalOpen] = useState(false);
  const [remoteSession, setRemoteSession] = useState<Session | null>(null);
  const [remoteFrame, setRemoteFrame] = useState<string | null>(null);
  const [remoteConnected, setRemoteConnected] = useState(false);
  const [remoteConnecting, setRemoteConnecting] = useState(false);
  const [remoteProgress, setRemoteProgress] = useState<string | null>(null);
  const [_remoteUrl, setRemoteUrl] = useState('');
  const [remoteUrlInput, setRemoteUrlInput] = useState('');
  const [actionLog, setActionLog] = useState<ActionLogEntry[]>([]);
  const [pendingUpload, setPendingUpload] = useState<PendingUpload | null>(null);
  const [uploadReady, setUploadReady] = useState(false);
  const remoteWsRef = useRef<WebSocket | null>(null);
  const screenshotContainerRef = useRef<HTMLDivElement>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectAttemptRef = useRef(0);
  const heartbeatIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // WebSocket connection
  useEffect(() => {
    const connectWebSocket = () => {
      try {
        const accessToken = getAccessToken();
        if (!accessToken) {
          console.log('No access token, skipping WebSocket connection');
          return;
        }
        const ws = new WebSocket(`${WS_BASE}/ws/live?token=${accessToken}`);

        ws.onopen = () => {
          console.log('WebSocket connected');
          reconnectAttemptRef.current = 0; // Reset reconnect attempts on successful connection
          setLiveStatus(prev => ({ ...prev, connected: true }));
        };

        ws.onmessage = (event) => {
          try {
            const update = JSON.parse(event.data);
            console.log('WS update:', update);

            switch (update.type) {
              case 'campaign_start':
                setLiveStatus(prev => ({
                  ...prev,
                  currentStep: 'Starting campaign',
                  totalJobs: update.data.total_jobs,
                  currentJob: 0
                }));
                break;
              case 'job_start':
                setLiveStatus(prev => ({
                  ...prev,
                  currentStep: `Processing ${update.data.profile_name}`,
                  currentJob: update.data.job_index + 1
                }));
                // Update queue state with real-time progress
                setQueueState(prev => ({
                  ...prev,
                  pending: prev.pending.map(c =>
                    c.id === update.data.campaign_id
                      ? {
                          ...c,
                          current_job: update.data.job_index + 1,
                          total_jobs: update.data.total_jobs,
                          current_profile: update.data.profile_name
                        }
                      : c
                  )
                }));
                setScreenshotKey(k => k + 1);
                break;
              case 'job_complete':
                // Update queue state with job completion
                setQueueState(prev => ({
                  ...prev,
                  pending: prev.pending.map(c =>
                    c.id === update.data.campaign_id
                      ? {
                          ...c,
                          current_job: update.data.job_index + 1,
                          success_count: (c.success_count || 0) + (update.data.success ? 1 : 0)
                        }
                      : c
                  )
                }));
                setScreenshotKey(k => k + 1);
                break;
              case 'profile_throttled':
                // Profile was detected as throttled/restricted
                toast.warning(`Profile "${update.data.profile_name}" restricted: ${update.data.reason}`, {
                  duration: 8000,
                  icon: '🚫'
                });
                break;
              case 'warmup_start':
                // Warm-up phase started for a profile
                setLiveStatus(prev => ({
                  ...prev,
                  currentStep: `Warming up: ${update.data.profile_name}`
                }));
                break;
              case 'campaign_complete':
                setLiveStatus(prev => ({
                  ...prev,
                  currentStep: `Done: ${update.data.success}/${update.data.total} successful`
                }));
                break;

              // =============================================
              // Persistent Queue Events (server-synced)
              // =============================================

              case 'queue_state_sync':
                // Full state sync on connect or reconnect
                setQueueState(update.data);
                setQueueLoading(false);
                break;

              case 'queue_campaign_added':
                // New campaign added by any user
                setQueueState(prev => ({
                  ...prev,
                  pending_count: prev.pending_count + 1,
                  pending: [...prev.pending, update.data]
                }));
                break;

              case 'queue_campaign_removed':
                // Campaign removed
                setQueueState(prev => ({
                  ...prev,
                  pending_count: Math.max(0, prev.pending_count - 1),
                  pending: prev.pending.filter(c => c.id !== update.data.campaign_id)
                }));
                break;

              case 'queue_campaign_start':
                // Campaign started processing
                setQueueState(prev => ({
                  ...prev,
                  processor_running: true,
                  current_campaign_id: update.data.campaign_id,
                  pending: prev.pending.map(c =>
                    c.id === update.data.campaign_id
                      ? { ...c, status: 'processing' as const, started_at: new Date().toISOString() }
                      : c
                  )
                }));
                setLiveStatus(prev => ({
                  ...prev,
                  currentStep: `Processing: ${update.data.url}`,
                  totalJobs: update.data.total_comments || 0,
                  currentJob: 0
                }));
                break;

              case 'queue_campaign_complete':
                // Campaign completed - move to history
                setQueueState(prev => {
                  const completed = prev.pending.find(c => c.id === update.data.campaign_id);
                  if (!completed) return prev;

                  const updatedCampaign: QueuedCampaign = {
                    ...completed,
                    status: 'completed',
                    success_count: update.data.success,
                    total_count: update.data.total,
                    completed_at: new Date().toISOString()
                  };

                  return {
                    ...prev,
                    processor_running: false,
                    current_campaign_id: null,
                    pending_count: Math.max(0, prev.pending_count - 1),
                    pending: prev.pending.filter(c => c.id !== update.data.campaign_id),
                    history: [updatedCampaign, ...prev.history].slice(0, 100)
                  };
                });
                setLiveStatus(prev => ({
                  ...prev,
                  currentStep: `Completed: ${update.data.success}/${update.data.total} successful`
                }));
                break;

              case 'queue_campaign_failed':
                // Campaign failed
                setQueueState(prev => {
                  const failed = prev.pending.find(c => c.id === update.data.campaign_id);
                  if (!failed) return prev;

                  const updatedCampaign: QueuedCampaign = {
                    ...failed,
                    status: 'failed',
                    error: update.data.error,
                    completed_at: new Date().toISOString()
                  };

                  return {
                    ...prev,
                    processor_running: false,
                    current_campaign_id: null,
                    pending_count: Math.max(0, prev.pending_count - 1),
                    pending: prev.pending.filter(c => c.id !== update.data.campaign_id),
                    history: [updatedCampaign, ...prev.history].slice(0, 100)
                  };
                });
                setLiveStatus(prev => ({
                  ...prev,
                  currentStep: `Failed: ${update.data.error}`
                }));
                toast.error(`Campaign failed: ${update.data.error}`);
                break;

              case 'queue_campaign_cancelled':
                // Campaign cancelled
                setQueueState(prev => {
                  const cancelled = prev.pending.find(c => c.id === update.data.campaign_id);
                  if (!cancelled) return prev;

                  const updatedCampaign: QueuedCampaign = {
                    ...cancelled,
                    status: 'cancelled',
                    completed_at: new Date().toISOString()
                  };

                  return {
                    ...prev,
                    processor_running: prev.current_campaign_id === update.data.campaign_id ? false : prev.processor_running,
                    current_campaign_id: prev.current_campaign_id === update.data.campaign_id ? null : prev.current_campaign_id,
                    pending_count: Math.max(0, prev.pending_count - 1),
                    pending: prev.pending.filter(c => c.id !== update.data.campaign_id),
                    history: [updatedCampaign, ...prev.history].slice(0, 100)
                  };
                });
                break;

              case 'queue_campaign_retry_complete':
                // Retry completed - update campaign in history
                setQueueState(prev => {
                  const campaignId = update.data.campaign_id;
                  const updatedHistory = prev.history.map(c => {
                    if (c.id === campaignId) {
                      return {
                        ...c,
                        results: [...(c.results || []), update.data.result],
                        success_count: update.data.new_success_count,
                        total_count: update.data.new_total_count,
                        has_retries: true,
                        last_retry_at: new Date().toISOString()
                      };
                    }
                    return c;
                  });

                  return {
                    ...prev,
                    history: updatedHistory
                  };
                });
                // Also update selected campaign if modal is open
                if (selectedCampaign?.id === update.data.campaign_id) {
                  setSelectedCampaign(prev => prev ? {
                    ...prev,
                    results: [...(prev.results || []), update.data.result],
                    success_count: update.data.new_success_count,
                    total_count: update.data.new_total_count,
                    has_retries: true,
                    last_retry_at: new Date().toISOString()
                  } : null);
                }
                break;

              // Legacy queue events (for backward compatibility during transition)
              case 'queue_start':
              case 'queue_complete':
                // Ignored - handled by new queue_campaign_* events
                break;
              case 'session_create_start':
                setSessionCreateStatus(prev => ({
                  ...prev,
                  [update.data.credential_uid]: {
                    uid: update.data.credential_uid,
                    step: 'Starting login...',
                    status: 'in_progress'
                  }
                }));
                break;
              case 'login_progress':
                setSessionCreateStatus(prev => ({
                  ...prev,
                  [update.data.uid]: {
                    uid: update.data.uid,
                    step: `${update.data.step}: ${update.data.status}`,
                    status: update.data.status === 'needs_attention' ? 'needs_attention' : 'in_progress',
                    error: update.data.details?.error
                  }
                }));
                setScreenshotKey(k => k + 1);
                break;
              case 'session_create_complete':
                setSessionCreateStatus(prev => ({
                  ...prev,
                  [update.data.credential_uid]: {
                    uid: update.data.credential_uid,
                    step: update.data.success ? 'Session created!' : update.data.error || 'Failed',
                    status: update.data.needs_attention ? 'needs_attention' : (update.data.success ? 'success' : 'failed'),
                    error: update.data.error
                  }
                }));
                setCreatingSession(null);
                if (update.data.success) {
                  fetchSessions();
                  fetchCredentials(); // Also refresh credentials to show linked session profile name
                }
                break;
              case 'batch_session_start':
                toast.info(`Starting batch: ${update.data.total} sessions`);
                break;
              case 'batch_session_complete':
                setBatchInProgress(false);
                setSelectedCredentials(new Set());
                toast.success(`Batch complete: ${update.data.success_count}/${update.data.total} sessions created`);
                fetchSessions();
                fetchCredentials();
                break;
            }
          } catch (e) {
            console.error('Error parsing WS message:', e);
          }
        };

        ws.onclose = () => {
          console.log('WebSocket disconnected');
          setLiveStatus(prev => ({ ...prev, connected: false }));
          // Exponential backoff: 1s, 2s, 4s, 8s, 16s, max 30s
          const delay = Math.min(1000 * Math.pow(2, reconnectAttemptRef.current), 30000);
          reconnectAttemptRef.current++;
          console.log(`Reconnecting in ${delay}ms (attempt ${reconnectAttemptRef.current})`);
          setTimeout(connectWebSocket, delay);
        };

        ws.onerror = (error) => {
          console.error('WebSocket error:', error);
        };

        wsRef.current = ws;
      } catch (error) {
        console.error('Failed to connect WebSocket:', error);
        const delay = Math.min(1000 * Math.pow(2, reconnectAttemptRef.current), 30000);
        reconnectAttemptRef.current++;
        setTimeout(connectWebSocket, delay);
      }
    };

    connectWebSocket();

    return () => {
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, []);

  // Helper to get auth headers
  const getAuthHeaders = (): HeadersInit => {
    const token = getAccessToken();
    return token ? { Authorization: `Bearer ${token}` } : {};
  };

  const fetchSessions = async () => {
    try {
      setSessionsLoading(true);
      const res = await fetch(`${API_BASE}/sessions`, { headers: getAuthHeaders() });
      if (!res.ok) throw new Error('Failed to fetch sessions');
      const data = await res.json();
      setSessions(data);
    } catch (error) {
      console.error("Failed to fetch sessions:", error);
      toast.error('Failed to load sessions');
    } finally {
      setSessionsLoading(false);
    }
  };

  const fetchTags = async () => {
    try {
      const res = await fetch(`${API_BASE}/tags`, { headers: getAuthHeaders() });
      if (!res.ok) throw new Error('Failed to fetch tags');
      const data = await res.json();
      setAllTags(data);
    } catch (error) {
      console.error("Failed to fetch tags:", error);
    }
  };

  const updateSessionTags = async (profileName: string, tags: string[]) => {
    try {
      const res = await fetch(`${API_BASE}/sessions/${encodeURIComponent(profileName)}/tags`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({ tags })
      });
      if (!res.ok) throw new Error('Failed to update tags');
      fetchSessions();
      fetchTags();
      toast.success('Tags updated');
    } catch (error) {
      toast.error(`Error: ${error}`);
    }
  };

  const fetchCredentials = async () => {
    try {
      setCredentialsLoading(true);
      const res = await fetch(`${API_BASE}/credentials`, { headers: getAuthHeaders() });
      if (!res.ok) throw new Error('Failed to fetch credentials');
      const data = await res.json();
      setCredentials(data);
    } catch (error) {
      console.error("Failed to fetch credentials:", error);
      toast.error('Failed to load credentials');
    } finally {
      setCredentialsLoading(false);
    }
  };

  const fetchProxies = async () => {
    try {
      setProxiesLoading(true);
      const res = await fetch(`${API_BASE}/proxies`, { headers: getAuthHeaders() });
      if (!res.ok) throw new Error('Failed to fetch proxies');
      const data = await res.json();
      setProxies(data);
    } catch (error) {
      console.error("Failed to fetch proxies:", error);
      toast.error('Failed to load proxies');
    } finally {
      setProxiesLoading(false);
    }
  };

  const fetchQueue = async () => {
    try {
      setQueueLoading(true);
      const res = await fetch(`${API_BASE}/queue`, { headers: getAuthHeaders() });
      if (!res.ok) throw new Error('Failed to fetch queue');
      const data = await res.json();
      setQueueState(data);
    } catch (error) {
      console.error("Failed to fetch queue:", error);
    } finally {
      setQueueLoading(false);
    }
  };

  const fetchGeminiObservations = async () => {
    try {
      setLoadingObservations(true);
      const res = await fetch(`${API_BASE}/debug/gemini-logs?limit=50`, { headers: getAuthHeaders() });
      if (!res.ok) throw new Error('Failed to fetch Gemini logs');
      const data = await res.json();
      setGeminiObservations(data.observations || []);
    } catch (error) {
      console.error("Failed to fetch Gemini observations:", error);
      toast.error('Failed to load Gemini observations');
    } finally {
      setLoadingObservations(false);
    }
  };

  const clearGeminiObservations = async () => {
    try {
      const res = await fetch(`${API_BASE}/debug/gemini-logs/clear`, {
        method: 'POST',
        headers: getAuthHeaders()
      });
      if (!res.ok) throw new Error('Failed to clear Gemini logs');
      const data = await res.json();
      toast.success(`Cleared ${data.cleared} observations`);
      setGeminiObservations([]);
    } catch (error) {
      toast.error('Failed to clear observations');
    }
  };

  const fetchProfileAnalytics = async () => {
    try {
      setLoadingAnalytics(true);
      const [summaryRes, profilesRes] = await Promise.all([
        fetch(`${API_BASE}/analytics/summary`, { headers: getAuthHeaders() }),
        fetch(`${API_BASE}/analytics/profiles`, { headers: getAuthHeaders() })
      ]);

      if (summaryRes.ok) {
        const summaryData = await summaryRes.json();
        setAnalyticsSummary(summaryData);
      }

      if (profilesRes.ok) {
        const profilesData = await profilesRes.json();
        setProfileAnalytics(profilesData.profiles || []);
      }
    } catch (error) {
      console.error("Failed to fetch profile analytics:", error);
    } finally {
      setLoadingAnalytics(false);
    }
  };

  const unblockProfile = async (profileName: string) => {
    try {
      const res = await fetch(`${API_BASE}/analytics/profiles/${encodeURIComponent(profileName)}/unblock`, {
        method: 'POST',
        headers: getAuthHeaders()
      });
      if (!res.ok) throw new Error('Failed to unblock profile');
      toast.success(`Unblocked ${profileName}`);
      fetchProfileAnalytics();
    } catch (error) {
      toast.error('Failed to unblock profile');
    }
  };

  const restrictProfile = async (profileName: string, hours: number = 24) => {
    try {
      const res = await fetch(`${API_BASE}/analytics/profiles/${encodeURIComponent(profileName)}/restrict?hours=${hours}&reason=manual`, {
        method: 'POST',
        headers: getAuthHeaders()
      });
      if (!res.ok) throw new Error('Failed to restrict profile');
      toast.success(`Restricted ${profileName} for ${hours}h`);
      fetchProfileAnalytics();
    } catch (error) {
      toast.error('Failed to restrict profile');
    }
  };

  // Tier 1: Critical path - load immediately for Campaign tab
  useEffect(() => {
    fetchSessions();
    fetchTags();
    fetchQueue();
  }, []);

  // Tier 2: Background loading - load after critical data, during idle time
  useEffect(() => {
    if (!sessionsLoading) {
      // Use requestIdleCallback to load during browser idle time
      const scheduleIdle = window.requestIdleCallback || ((cb: IdleRequestCallback) => setTimeout(cb, 100));
      scheduleIdle(() => fetchCredentials());
      scheduleIdle(() => fetchProxies());
    }
  }, [sessionsLoading]);

  // Filter sessions by search query, status, and selected tags (AND logic)
  const filteredSessions = useMemo(() => {
    let result = sessions;

    // Filter by search query (name, profile_name, or user_id)
    if (sessionSearchQuery.trim()) {
      const query = sessionSearchQuery.toLowerCase().trim();
      result = result.filter(s =>
        (s.display_name || '').toLowerCase().includes(query) ||
        (s.profile_name || '').toLowerCase().includes(query) ||
        (s.user_id || '').toLowerCase().includes(query)
      );
    }

    // Filter by status (valid/invalid)
    if (sessionStatusFilters.valid !== undefined) {
      result = result.filter(s => s.valid === sessionStatusFilters.valid);
    }

    // Filter by proxy status
    if (sessionStatusFilters.hasProxy !== undefined) {
      result = result.filter(s =>
        sessionStatusFilters.hasProxy ? !!s.proxy_masked : !s.proxy_masked
      );
    }

    // Filter by tags (AND logic)
    if (sessionFilterTags.length > 0) {
      result = result.filter(s =>
        sessionFilterTags.every(tag => (s.tags || []).includes(tag))
      );
    }

    return result;
  }, [sessions, sessionSearchQuery, sessionStatusFilters, sessionFilterTags]);

  // Add campaign to queue (API call)
  const addToQueue = async () => {
    if (!url || !comments) {
      toast.error('Please enter a URL and comments');
      return;
    }

    const commentList = comments.split('\n').filter(c => c.trim());
    if (commentList.length === 0) {
      toast.error('Please enter at least one comment');
      return;
    }

    // Check queue limit
    if (queueState.pending_count >= queueState.max_pending) {
      toast.error(`Queue is full (${queueState.pending_count}/${queueState.max_pending}). Wait for campaigns to complete.`);
      return;
    }

    // Calculate available profiles based on tag filter
    const availableProfiles = sessions.filter(s => {
      if (!s.valid) return false;
      if (campaignFilterTags.length === 0) return true;
      return campaignFilterTags.every(tag => (s.tags || []).includes(tag));
    }).length;

    if (availableProfiles === 0) {
      toast.error(campaignFilterTags.length > 0
        ? 'No valid sessions match the selected tags!'
        : 'No valid sessions available!');
      return;
    }

    const normalizedUrl = normalizeUrl(url);

    // Count existing comments for this URL in pending queue
    const existingForUrl = queueState.pending
      .filter(c => normalizeUrl(c.url) === normalizedUrl)
      .reduce((sum, c) => sum + c.comments.length, 0);

    const totalForUrl = existingForUrl + commentList.length;

    // Per-URL validation: total comments for this URL must not exceed available profiles
    if (totalForUrl > availableProfiles) {
      if (existingForUrl > 0) {
        toast.error(`This URL already has ${existingForUrl} comments queued. Adding ${commentList.length} more would total ${totalForUrl}, exceeding ${availableProfiles} available profiles.`);
      } else {
        toast.error(`You have ${commentList.length} comments but only ${availableProfiles} active sessions. Please reduce to ${availableProfiles} or fewer.`);
      }
      return;
    }

    setAddingToQueue(true);

    try {
      const res = await fetch(`${API_BASE}/queue`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({
          url,
          comments: commentList,
          duration_minutes: campaignDuration,
          filter_tags: campaignFilterTags.length > 0 ? campaignFilterTags : null,
          enable_warmup: enableWarmup
        })
      });

      if (!res.ok) {
        const error = await res.json();
        throw new Error(error.detail || 'Failed to add to queue');
      }

      // Clear form - state update comes via WebSocket
      setUrl('');
      setComments('');

      toast.success(`Added campaign with ${commentList.length} comments to queue${campaignFilterTags.length > 0 ? ` (filtered by: ${campaignFilterTags.join(', ')})` : ''}`);
    } catch (error: unknown) {
      toast.error(`Error: ${error instanceof Error ? error.message : 'Unknown error'}`);
    } finally {
      setAddingToQueue(false);
    }
  };

  // Remove campaign from queue (API call)
  const removeFromQueue = async (campaignId: string) => {
    try {
      const res = await fetch(`${API_BASE}/queue/${campaignId}`, {
        method: 'DELETE',
        headers: getAuthHeaders()
      });

      if (!res.ok) {
        const error = await res.json();
        throw new Error(error.detail || 'Failed to remove from queue');
      }

      toast.success('Campaign removed from queue');
    } catch (error: unknown) {
      toast.error(`Error: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  // Cancel campaign (API call)
  const cancelCampaign = async (campaignId: string) => {
    if (!confirm('Cancel this campaign?')) return;

    try {
      const res = await fetch(`${API_BASE}/queue/${campaignId}/cancel`, {
        method: 'POST',
        headers: getAuthHeaders()
      });

      if (!res.ok) {
        const error = await res.json();
        throw new Error(error.detail || 'Failed to cancel campaign');
      }

      toast.success('Campaign cancelled');
    } catch (error: unknown) {
      toast.error(`Error: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  // Retry a failed job in a campaign (API call)
  const retryJob = async (campaignId: string, jobIndex: number, profileName: string, comment: string, originalProfile?: string) => {
    setIsRetrying(true);

    try {
      const res = await fetch(`${API_BASE}/queue/${campaignId}/retry`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({
          job_index: jobIndex,
          profile_name: profileName,
          comment: comment,
          original_profile: originalProfile
        })
      });

      if (!res.ok) {
        const error = await res.json();
        throw new Error(error.detail || 'Failed to retry job');
      }

      const result = await res.json();

      if (result.success) {
        toast.success(`Retry successful! Comment posted by ${profileName}`);
        // Update selected campaign with new data from response
        if (result.campaign) {
          setSelectedCampaign(result.campaign);
        }
      } else {
        toast.error(`Retry failed: ${result.result?.error || 'Unknown error'}`);
      }

      // Reset retry UI state
      setRetryingJobIndex(null);
      setRetryProfile('');

    } catch (error: unknown) {
      toast.error(`Error: ${error instanceof Error ? error.message : 'Unknown error'}`);
    } finally {
      setIsRetrying(false);
    }
  };

  // Bulk retry all failed jobs in a campaign
  // Now simplified - no strategy selection needed, just click and it works
  const handleBulkRetry = async () => {
    if (!selectedCampaign) return;

    setIsBulkRetrying(true);
    toast.info('Starting bulk retry... This may take a while.');

    try {
      const res = await fetch(`${API_BASE}/queue/${selectedCampaign.id}/bulk-retry`, {
        method: 'POST',
        headers: { ...getAuthHeaders() }
        // No body needed - backend handles everything automatically
      });

      const data = await res.json();

      if (data.success) {
        toast.success(`Retry Complete: ${data.jobs_succeeded}/${data.jobs_retried} jobs succeeded`);
        if (data.jobs_exhausted > 0) {
          toast.warning(`${data.jobs_exhausted} jobs ran out of eligible profiles`);
        }
        if (data.campaign) {
          setSelectedCampaign(data.campaign);
        }
      } else {
        toast.error(data.detail || 'Bulk retry failed');
      }
    } catch (error) {
      toast.error(`Error: ${error instanceof Error ? error.message : 'Unknown error'}`);
    } finally {
      setIsBulkRetrying(false);
    }
  };

  // Copy text to clipboard
  const copyToClipboard = (text: string, label: string = 'Text') => {
    navigator.clipboard.writeText(text);
    toast.success(`${label} copied to clipboard`);
  };

  // Format relative time
  const formatRelativeTime = (dateString: string): string => {
    const date = new Date(dateString);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    if (diffMins < 1) return 'just now';
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    if (diffDays < 7) return `${diffDays}d ago`;
    return date.toLocaleDateString();
  };

  const deleteSession = async (profileName: string) => {
    if (!confirm(`Delete session "${profileName}"? This cannot be undone.`)) return;

    try {
      const res = await fetch(`${API_BASE}/sessions/${encodeURIComponent(profileName)}`, {
        method: 'DELETE',
        headers: getAuthHeaders()
      });
      const result = await res.json();
      if (result.success) {
        toast.success(`Session "${profileName}" deleted`);
        fetchSessions();
        fetchCredentials(); // Refresh credentials to update session_connected status
      } else {
        toast.error(`Failed to delete session: ${result.error || 'Unknown error'}`);
      }
    } catch (error) {
      toast.error(`Error: ${error}`);
    }
  };

  // Bulk session operations with shift-click support
  const toggleSessionSelection = (profileName: string, index: number, shiftKey: boolean = false) => {
    setSelectedSessions(prev => {
      const newSet = new Set(prev);

      // Shift-click: select range from last selected to current
      if (shiftKey && lastSelectedIndex !== null) {
        const start = Math.min(lastSelectedIndex, index);
        const end = Math.max(lastSelectedIndex, index);
        for (let i = start; i <= end; i++) {
          newSet.add(filteredSessions[i].profile_name);
        }
      } else {
        // Normal click: toggle single item
        if (newSet.has(profileName)) {
          newSet.delete(profileName);
        } else {
          newSet.add(profileName);
        }
      }

      return newSet;
    });
    setLastSelectedIndex(index);
  };

  const toggleAllSessions = () => {
    if (selectedSessions.size === filteredSessions.length) {
      setSelectedSessions(new Set());
    } else {
      setSelectedSessions(new Set(filteredSessions.map(s => s.profile_name)));
    }
  };

  const bulkDeleteSessions = async () => {
    if (selectedSessions.size === 0) return;
    if (!confirm(`Delete ${selectedSessions.size} sessions? This cannot be undone.`)) return;

    setBulkDeleting(true);
    let successCount = 0;
    let failCount = 0;

    for (const profileName of selectedSessions) {
      try {
        const res = await fetch(`${API_BASE}/sessions/${encodeURIComponent(profileName)}`, {
          method: 'DELETE',
          headers: getAuthHeaders()
        });
        const result = await res.json();
        if (result.success) {
          successCount++;
        } else {
          failCount++;
        }
      } catch {
        failCount++;
      }
    }

    setBulkDeleting(false);
    // Always clear selection for delete since items no longer exist
    setSelectedSessions(new Set());
    fetchSessions();
    fetchCredentials();

    if (failCount === 0) {
      toast.success(`Deleted ${successCount} sessions`);
    } else {
      toast.warning(`Deleted ${successCount} sessions, ${failCount} failed`);
    }
  };

  const bulkRefreshNames = async () => {
    if (selectedSessions.size === 0) return;

    setBulkRefreshing(true);
    let successCount = 0;
    let failCount = 0;

    for (const profileName of selectedSessions) {
      try {
        const res = await fetch(`${API_BASE}/sessions/${encodeURIComponent(profileName)}/refresh-name`, {
          method: 'POST',
          headers: getAuthHeaders()
        });
        const result = await res.json();
        if (result.success) {
          successCount++;
        } else {
          failCount++;
        }
      } catch {
        failCount++;
      }
    }

    setBulkRefreshing(false);
    if (!keepSelection) setSelectedSessions(new Set());
    fetchSessions();

    if (failCount === 0) {
      toast.success(`Refreshed ${successCount} profile names`);
    } else {
      toast.warning(`Refreshed ${successCount} profile names, ${failCount} failed`);
    }
  };

  const bulkAddTag = async (tag: string) => {
    if (selectedSessions.size === 0) return;

    let successCount = 0;
    let failCount = 0;

    for (const profileName of selectedSessions) {
      const session = sessions.find(s => s.profile_name === profileName);
      if (!session) continue;

      const currentTags = session.tags || [];
      if (currentTags.includes(tag)) {
        successCount++; // Already has tag
        continue;
      }

      try {
        const res = await fetch(`${API_BASE}/sessions/${encodeURIComponent(profileName)}/tags`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
          body: JSON.stringify({ tags: [...currentTags, tag] })
        });
        const result = await res.json();
        if (result.success) {
          successCount++;
        } else {
          failCount++;
        }
      } catch {
        failCount++;
      }
    }

    fetchSessions();
    fetchTags();
    setBulkTagModalOpen(false);
    if (!keepSelection) setSelectedSessions(new Set());

    if (failCount === 0) {
      toast.success(`Added tag "${tag}" to ${successCount} sessions`);
    } else {
      toast.warning(`Added tag to ${successCount} sessions, ${failCount} failed`);
    }
  };

  const addCredential = async () => {
    if (!newUid || !newPassword) {
      alert("UID and Password are required!");
      return;
    }

    try {
      await fetch(`${API_BASE}/credentials`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({
          uid: newUid,
          password: newPassword,
          secret: newSecret || undefined,
          profile_name: newProfileName || undefined
        })
      });
      
      setNewUid('');
      setNewPassword('');
      setNewSecret('');
      setNewProfileName('');
      fetchCredentials();
      alert("Credential added!");
    } catch (error) {
      alert(`Error: ${error}`);
    }
  };

  const deleteCredential = async (uid: string) => {
    if (!confirm(`Delete credential for ${uid}?`)) return;

    try {
      await fetch(`${API_BASE}/credentials/${encodeURIComponent(uid)}`, {
        method: 'DELETE',
        headers: getAuthHeaders()
      });
      fetchCredentials();
    } catch (error) {
      alert(`Error: ${error}`);
    }
  };

  const bulkImportCredentials = async (file: File) => {
    setIsImporting(true);
    try {
      const formData = new FormData();
      formData.append('file', file);

      const res = await fetch(`${API_BASE}/credentials/bulk-import`, {
        method: 'POST',
        headers: getAuthHeaders(),
        body: formData
      });

      const result = await res.json();

      if (result.errors && result.errors.length > 0) {
        alert(`Imported ${result.imported} credentials.\n\nErrors:\n${result.errors.join('\n')}`);
      } else {
        alert(`Successfully imported ${result.imported} credentials!`);
      }

      fetchCredentials();
    } catch (error) {
      alert(`Import failed: ${error}`);
    } finally {
      setIsImporting(false);
    }
  };

  const getOTP = useCallback(async (uid: string) => {
    try {
      const res = await fetch(`${API_BASE}/otp/${encodeURIComponent(uid)}`, {
        headers: getAuthHeaders()
      });
      const data = await res.json();
      setOtpData(prev => ({ ...prev, [uid]: data }));
    } catch (error) {
      console.error("Failed to get OTP:", error);
    }
  }, []);

  const copyOTP = (code: string | null) => {
    if (code) {
      navigator.clipboard.writeText(code);
    }
  };

  // Proxy management functions
  const addProxy = async () => {
    if (!newProxyName || !newProxyUrl) {
      alert("Name and URL are required!");
      return;
    }

    try {
      await fetch(`${API_BASE}/proxies`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({
          name: newProxyName,
          url: newProxyUrl,
          proxy_type: newProxyType,
          country: newProxyCountry
        })
      });

      setNewProxyName('');
      setNewProxyUrl('');
      setNewProxyType('mobile');
      setNewProxyCountry('US');
      fetchProxies();
      alert("Proxy added!");
    } catch (error) {
      alert(`Error: ${error}`);
    }
  };

  const deleteProxy = async (proxyId: string) => {
    if (!confirm("Delete this proxy?")) return;

    try {
      await fetch(`${API_BASE}/proxies/${encodeURIComponent(proxyId)}`, {
        method: 'DELETE',
        headers: getAuthHeaders()
      });
      fetchProxies();
    } catch (error) {
      alert(`Error: ${error}`);
    }
  };

  const testProxy = async (proxyId: string) => {
    setTestingProxy(proxyId);
    try {
      const res = await fetch(`${API_BASE}/proxies/${encodeURIComponent(proxyId)}/test`, {
        method: 'POST',
        headers: getAuthHeaders()
      });
      const result = await res.json();

      if (result.success) {
        alert(`Proxy working! IP: ${result.ip}, Response time: ${result.response_time_ms}ms`);
      } else {
        alert(`Proxy failed: ${result.error}`);
      }
      fetchProxies();
    } catch (error) {
      alert(`Error: ${error}`);
    } finally {
      setTestingProxy(null);
    }
  };

  const setProxyAsDefault = async (proxyId: string) => {
    setSettingDefaultProxy(proxyId);
    try {
      const res = await fetch(`${API_BASE}/proxies/${encodeURIComponent(proxyId)}/set-default`, {
        method: 'POST',
        headers: getAuthHeaders()
      });
      const result = await res.json();

      if (result.success) {
        toast.success(result.message || 'Proxy is now the default for all operations');
      } else {
        toast.error(result.detail || 'Failed to set default proxy');
      }
      fetchProxies();
    } catch (error) {
      toast.error(`Failed to set default proxy: ${error}`);
    } finally {
      setSettingDefaultProxy(null);
    }
  };

  // Session creation function
  const createSession = async (uid: string, proxyId?: string) => {
    setCreatingSession(uid);
    setSessionCreateStatus(prev => ({
      ...prev,
      [uid]: { uid, step: 'Starting...', status: 'pending' }
    }));

    try {
      const res = await fetch(`${API_BASE}/sessions/create`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({
          credential_uid: uid,
          proxy_id: proxyId
        })
      });

      const result = await res.json();

      if (result.success) {
        alert(`Session created for ${result.profile_name}!`);
        fetchSessions();
        fetchCredentials();
      } else if (result.needs_attention) {
        alert(`Login requires attention: ${result.error}`);
      } else {
        alert(`Failed: ${result.error}`);
      }
    } catch (error) {
      alert(`Error: ${error}`);
      setSessionCreateStatus(prev => ({
        ...prev,
        [uid]: { uid, step: 'Error', status: 'failed', error: String(error) }
      }));
    } finally {
      setCreatingSession(null);
    }
  };

  // Batch session creation functions
  const toggleCredentialSelection = (uid: string) => {
    setSelectedCredentials(prev => {
      const next = new Set(prev);
      if (next.has(uid)) {
        next.delete(uid);
      } else {
        next.add(uid);
      }
      return next;
    });
  };

  // Get credentials eligible for batch session creation (have 2FA, no session)
  const eligibleCredentials = credentials.filter(c => c.has_secret && !c.session_connected);

  const toggleSelectAll = () => {
    if (selectedCredentials.size === eligibleCredentials.length && eligibleCredentials.length > 0) {
      // Deselect all
      setSelectedCredentials(new Set());
    } else {
      // Select all eligible
      setSelectedCredentials(new Set(eligibleCredentials.map(c => c.uid)));
    }
  };

  const allSelected = eligibleCredentials.length > 0 && selectedCredentials.size === eligibleCredentials.length;

  const createBatchSessions = async () => {
    if (selectedCredentials.size === 0) return;

    setBatchInProgress(true);

    // Initialize status for all selected credentials
    const initialStatus: Record<string, SessionCreateStatus> = {};
    selectedCredentials.forEach(uid => {
      initialStatus[uid] = { uid, step: 'Queued...', status: 'pending' };
    });
    setSessionCreateStatus(prev => ({ ...prev, ...initialStatus }));

    try {
      const res = await fetch(`${API_BASE}/sessions/create-batch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({
          credential_uids: Array.from(selectedCredentials)
        })
      });

      const result = await res.json();

      if (!res.ok) {
        toast.error(`Batch error: ${result.detail || 'Unknown error'}`);
      }
      // Success/complete handling happens via WebSocket
    } catch (error) {
      toast.error(`Batch error: ${error}`);
      setBatchInProgress(false);
    }
  };

  // Session profile name refresh functions (non-blocking with toast notifications)
  const refreshSessionName = (profileName: string) => {
    // Show loading toast immediately
    const toastId = toast.loading(`Refreshing ${profileName}...`);
    setRefreshingSession(profileName);

    // Fire and forget - don't await, let it run in background
    fetch(`${API_BASE}/sessions/${encodeURIComponent(profileName)}/refresh-name`, {
      method: 'POST',
      headers: getAuthHeaders()
    })
      .then(res => res.json())
      .then(result => {
        if (result.success) {
          if (result.new_profile_name !== result.old_profile_name) {
            toast.success(`Profile updated: ${result.old_profile_name} → ${result.new_profile_name}`, { id: toastId });
          } else {
            toast.success(`Profile confirmed: ${result.new_profile_name}`, { id: toastId });
          }
          fetchSessions();
          fetchCredentials();
        } else {
          toast.error(`Failed to refresh ${profileName}: ${result.error}`, { id: toastId });
        }
      })
      .catch(error => {
        toast.error(`Error refreshing ${profileName}: ${error}`, { id: toastId });
      })
      .finally(() => {
        setRefreshingSession(null);
      });
  };

  useEffect(() => {
    const interval = setInterval(() => {
      credentials.forEach(cred => {
        if (otpData[cred.uid]?.valid && otpData[cred.uid].remaining_seconds <= 5) {
          getOTP(cred.uid);
        }
      });
    }, 1000);
    return () => clearInterval(interval);
  }, [credentials, otpData, getOTP]);

  // Live View polling - independent interval that doesn't depend on image onLoad
  useEffect(() => {
    if (activeTab !== 'live') return;

    const interval = setInterval(() => {
      setScreenshotKey(k => k + 1);
    }, 500);

    return () => clearInterval(interval);
  }, [activeTab]);

  // Sessions tab auto-refresh when switching to it
  useEffect(() => {
    if (activeTab === 'sessions') {
      fetchSessions();
    }
  }, [activeTab]);

  // ============================================================================
  // Remote Control Functions
  // ============================================================================

  const connectRemoteWebSocket = useCallback((sessionId: string) => {
    if (remoteWsRef.current) {
      remoteWsRef.current.close();
    }

    setRemoteConnecting(true);
    setRemoteProgress(null);

    try {
      const accessToken = getAccessToken();
      if (!accessToken) {
        toast.error('Not authenticated');
        setRemoteConnecting(false);
        return;
      }
      const ws = new WebSocket(`${WS_BASE}/ws/session/${encodeURIComponent(sessionId)}/control?token=${accessToken}`);

      ws.onopen = () => {
        console.log('Remote WS connected');
        setRemoteConnected(true);
        setRemoteConnecting(false);
        reconnectAttemptRef.current = 0;
        toast.success('Browser connected');

        // Start heartbeat to detect dead connections
        if (heartbeatIntervalRef.current) {
          clearInterval(heartbeatIntervalRef.current);
        }
        heartbeatIntervalRef.current = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'ping' }));
          }
        }, 30000); // Ping every 30 seconds
      };

      ws.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);

          switch (message.type) {
            case 'frame':
              setRemoteFrame(message.data.image);
              break;
            case 'state':
              setRemoteUrl(message.data.url || '');
              setRemoteUrlInput(message.data.url || '');
              break;
            case 'progress':
              setRemoteProgress(message.data.stage);
              break;
            case 'browser_ready':
              setRemoteProgress(null);
              toast.success('Browser ready');
              break;
            case 'action_result':
              setActionLog(prev => prev.map(entry =>
                entry.id === message.data.action_id
                  ? { ...entry, status: message.data.success ? 'success' : 'failed' }
                  : entry
              ));
              break;
            case 'error':
              toast.error(message.data.message);
              break;
          }
        } catch (e) {
          console.error('Failed to parse remote WS message:', e);
        }
      };

      ws.onclose = () => {
        setRemoteConnected(false);
        setRemoteConnecting(false);

        // Clear heartbeat interval
        if (heartbeatIntervalRef.current) {
          clearInterval(heartbeatIntervalRef.current);
          heartbeatIntervalRef.current = null;
        }

        // Auto-reconnect with exponential backoff
        if (remoteModalOpen && reconnectAttemptRef.current < 5) {
          const delay = Math.min(1000 * Math.pow(2, reconnectAttemptRef.current), 10000);
          toast.loading('Reconnecting...', { id: 'reconnect' });
          reconnectTimeoutRef.current = setTimeout(() => {
            reconnectAttemptRef.current++;
            if (remoteSession) {
              connectRemoteWebSocket(remoteSession.profile_name);
            }
          }, delay);
        }
      };

      ws.onerror = (error) => {
        console.error('Remote WS error:', error);
      };

      remoteWsRef.current = ws;
    } catch (error) {
      console.error('Failed to create remote WebSocket:', error);
      setRemoteConnecting(false);
    }
  }, [remoteModalOpen, remoteSession]);

  const disconnectRemoteWebSocket = useCallback(() => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
    }
    if (heartbeatIntervalRef.current) {
      clearInterval(heartbeatIntervalRef.current);
      heartbeatIntervalRef.current = null;
    }
    if (remoteWsRef.current) {
      remoteWsRef.current.close();
      remoteWsRef.current = null;
    }
    setRemoteConnected(false);
    setRemoteConnecting(false);
  }, []);

  const sendRemoteAction = useCallback((action: { type: string; data: Record<string, unknown> }) => {
    if (remoteWsRef.current?.readyState === WebSocket.OPEN) {
      const actionId = crypto.randomUUID();
      remoteWsRef.current.send(JSON.stringify({ ...action, action_id: actionId }));
      return actionId;
    }
    return null;
  }, []);

  const addActionLogEntry = useCallback((type: ActionLogEntry['type'], details: string, actionId: string) => {
    const entry: ActionLogEntry = {
      id: actionId,
      timestamp: new Date().toISOString(),
      type,
      details,
      status: 'sent'
    };
    setActionLog(prev => [entry, ...prev].slice(0, 100));
  }, []);

  const openRemoteModal = (session: Session) => {
    setRemoteSession(session);
    setRemoteModalOpen(true);
    setRemoteFrame(null);
    setActionLog([]);
    setPendingUpload(null);
    setUploadReady(false);
    connectRemoteWebSocket(session.profile_name);
  };

  const closeRemoteModal = () => {
    disconnectRemoteWebSocket();
    setRemoteModalOpen(false);
    setRemoteSession(null);
    setRemoteFrame(null);
    setActionLog([]);
    setPendingUpload(null);
    setUploadReady(false);
  };

  // Handle click on screenshot
  const handleRemoteClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!remoteConnected || !screenshotContainerRef.current) return;

    const img = screenshotContainerRef.current.querySelector('img');
    if (!img) return;

    const imgRect = img.getBoundingClientRect();

    // Calculate scale
    const scale = imgRect.width / VIEWPORT_WIDTH;

    // Get click position relative to image
    const relativeX = e.clientX - imgRect.left;
    const relativeY = e.clientY - imgRect.top;

    // Check bounds
    if (relativeX < 0 || relativeX > imgRect.width || relativeY < 0 || relativeY > imgRect.height) {
      return;
    }

    // Translate to viewport coordinates
    const x = Math.round(relativeX / scale);
    const y = Math.round(relativeY / scale);

    const actionId = sendRemoteAction({ type: 'click', data: { x, y } });
    if (actionId) {
      addActionLogEntry('click', `Click at (${x}, ${y})`, actionId);
    }
  };

  // Handle scroll on screenshot
  const handleRemoteScroll = (e: React.WheelEvent<HTMLDivElement>) => {
    if (!remoteConnected) return;
    e.preventDefault();

    const actionId = sendRemoteAction({
      type: 'scroll',
      data: { x: VIEWPORT_WIDTH / 2, y: VIEWPORT_HEIGHT / 2, deltaY: e.deltaY }
    });
    if (actionId) {
      const direction = e.deltaY > 0 ? 'down' : 'up';
      addActionLogEntry('scroll', `Scroll ${direction}`, actionId);
    }
  };

  // Handle keyboard input
  useEffect(() => {
    if (!remoteModalOpen || !remoteConnected) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      // Only capture if the modal is focused (not typing in URL bar)
      const activeElement = document.activeElement;
      if (activeElement?.tagName === 'INPUT' || activeElement?.tagName === 'TEXTAREA') {
        return;
      }

      e.preventDefault();

      const modifiers: string[] = [];
      if (e.ctrlKey) modifiers.push('Control');
      if (e.altKey) modifiers.push('Alt');
      if (e.shiftKey) modifiers.push('Shift');
      if (e.metaKey) modifiers.push('Meta');

      const actionId = sendRemoteAction({
        type: 'key',
        data: { key: e.key, modifiers }
      });

      if (actionId) {
        const keyDisplay = modifiers.length > 0 ? `${modifiers.join('+')}+${e.key}` : e.key;
        addActionLogEntry('key', `Key: ${keyDisplay}`, actionId);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [remoteModalOpen, remoteConnected, sendRemoteAction, addActionLogEntry]);

  // Handle URL navigation
  const handleRemoteNavigate = () => {
    if (!remoteConnected || !remoteUrlInput.trim()) return;

    let url = remoteUrlInput.trim();
    if (!url.startsWith('http://') && !url.startsWith('https://')) {
      url = 'https://' + url;
    }

    const actionId = sendRemoteAction({ type: 'navigate', data: { url } });
    if (actionId) {
      addActionLogEntry('navigate', `Navigate to ${url}`, actionId);
    }
  };

  // Handle image upload for profile picture
  const handleImageUpload = async (file: File) => {
    if (!remoteSession) return;

    const allowedTypes = ['image/jpeg', 'image/png', 'image/webp'];
    if (!allowedTypes.includes(file.type)) {
      toast.error('Please upload a JPG, PNG, or WebP image');
      return;
    }

    if (file.size > 10 * 1024 * 1024) {
      toast.error('Image must be under 10MB');
      return;
    }

    const formData = new FormData();
    formData.append('file', file);

    try {
      const res = await fetch(`${API_BASE}/sessions/${encodeURIComponent(remoteSession.profile_name)}/upload-image`, {
        method: 'POST',
        headers: getAuthHeaders(),
        body: formData
      });

      const result = await res.json();
      if (result.success) {
        setPendingUpload({
          filename: result.filename,
          size: result.size,
          imageId: result.image_id
        });
        setUploadReady(false);
        toast.success(`Image uploaded: ${result.filename}`);
      } else {
        toast.error(`Upload failed: ${result.error}`);
      }
    } catch (error) {
      toast.error(`Upload error: ${error}`);
    }
  };

  const prepareFileUpload = async () => {
    if (!remoteSession) return;

    try {
      const res = await fetch(`${API_BASE}/sessions/${encodeURIComponent(remoteSession.profile_name)}/prepare-file-upload`, {
        method: 'POST',
        headers: getAuthHeaders()
      });
      const result = await res.json();
      if (result.success) {
        setUploadReady(true);
        toast.success('File ready! Click the upload button on Facebook.');
      } else {
        toast.error(result.error || 'Failed to prepare upload');
      }
    } catch (error) {
      toast.error(`Error: ${error}`);
    }
  };

  // Auth loading state
  if (authLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-white">
        <Loader2 className="w-8 h-8 animate-spin text-blue-600" />
      </div>
    );
  }

  // Not authenticated - show login
  if (!isAuthenticated) {
    return <LoginPage />;
  }

  return (
    <div className="min-h-screen relative font-sans">
      {/* Pearl gradient background */}
      <PearlBackground />

      {/* Content layer */}
      <div className="relative z-10 p-6 lg:p-8">
        <div className="max-w-[1200px] mx-auto space-y-6">

          {/* Header Card */}
          <Card className="p-5">
            <div className="flex justify-between items-center">
              <div className="flex items-center gap-3">
                {/* Logo mark */}
                <div className="w-10 h-10 rounded-full bg-[rgba(51,51,51,0.08)] border border-[rgba(0,0,0,0.1)] flex items-center justify-center">
                  <Play className="w-5 h-5 text-[#333333]" />
                </div>
                <div>
                  <h1 className="text-lg font-semibold tracking-tight text-[#111111]">CommentBot</h1>
                  <p className="text-xs text-[#999999]">Automation platform</p>
                </div>
              </div>
              <div className="flex items-center gap-4">
                {/* Status indicator - derive from actual queue state, not just processor_running flag */}
                {(() => {
                  const isProcessing = queueState.pending.some(c => c.status === 'processing');
                  return (
                    <div className="flex items-center gap-2 px-3 py-1.5 rounded-full border border-[rgba(0,0,0,0.1)]" style={{ background: sessionsLoading ? 'rgba(245,158,11,0.1)' : isProcessing ? 'rgba(59,130,246,0.1)' : 'rgba(34,197,94,0.1)' }}>
                      <div className={`status-dot`} style={{ background: sessionsLoading ? '#f59e0b' : isProcessing ? '#3b82f6' : '#22c55e' }} />
                      <span className="text-xs font-medium" style={{ color: sessionsLoading ? '#f59e0b' : isProcessing ? '#3b82f6' : '#22c55e' }}>
                        {sessionsLoading ? 'Loading...' : isProcessing ? 'Processing' : 'Ready'}
                      </span>
                    </div>
                  );
                })()}
                {/* User info */}
                <div className="flex items-center gap-3">
                  <div className="text-right">
                    <p className="text-sm font-medium text-[#111111]">{user?.username}</p>
                    <p className="text-xs text-[#999999] capitalize">{user?.role}</p>
                  </div>
                  <Button variant="outline" size="sm" onClick={logout}>
                    <LogOut className="w-4 h-4 mr-1" />
                    Logout
                  </Button>
                </div>
              </div>
            </div>
          </Card>

          <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
            <TabsList>
            <TabsTrigger value="campaign">Campaign</TabsTrigger>
            <TabsTrigger value="live">Live View</TabsTrigger>
            <TabsTrigger value="sessions">Sessions</TabsTrigger>
            <TabsTrigger value="credentials">Credentials</TabsTrigger>
            <TabsTrigger value="proxies">Proxies</TabsTrigger>
            <TabsTrigger value="analytics" onClick={() => { fetchGeminiObservations(); fetchProfileAnalytics(); }}>
              <BarChart3 className="w-4 h-4 mr-1" />
              Analytics
            </TabsTrigger>
            {user?.role === 'admin' && (
              <TabsTrigger value="admin">
                <Shield className="w-4 h-4 mr-1" />
                Admin
              </TabsTrigger>
            )}
          </TabsList>

          <TabsContent value="campaign" className="space-y-6 mt-6">
            {/* Add Campaign Form */}
            <Card className="">
              <CardHeader className="bg-[rgba(51,51,51,0.04)] border-b border-[rgba(0,0,0,0.1)] pb-4">
                <CardTitle className="text-lg">Add Campaign to Queue</CardTitle>
              </CardHeader>
              <CardContent className="space-y-6 pt-6">
                <div className="space-y-2">
                  <Label>Target URL</Label>
                  <Input
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    placeholder="https://www.facebook.com/..."
                    className="bg-white"
                  />
                </div>

                <div className="space-y-2">
                  <Label>Comments (one per line)</Label>
                  <Textarea
                    value={comments}
                    onChange={(e) => setComments(e.target.value)}
                    placeholder="Comment 1&#10;Comment 2&#10;Comment 3"
                    className="min-h-[150px] bg-white"
                  />
                  <p className="text-xs text-[#999999]">
                    {sessions.filter(s => s.valid).length} profiles available. Same profile can comment on different posts.
                  </p>
                </div>

                <div className="space-y-2">
                  <Label>Campaign Duration</Label>
                  <div className="flex items-center gap-4">
                    <Input
                      type="number"
                      min={10}
                      max={1440}
                      value={campaignDuration}
                      onChange={(e) => {
                        const val = Math.max(10, Math.min(1440, Number(e.target.value) || 10));
                        setCampaignDuration(val);
                      }}
                      className="w-24 bg-white"
                    />
                    <span className="text-sm text-[#666666]">
                      minutes ({formatDuration(campaignDuration)})
                    </span>
                  </div>
                  <p className="text-xs text-[#999999]">
                    Comments will be spread across this time (10 min - 24 hours)
                  </p>
                </div>

                {/* Tag Filter for Campaign */}
                <div className="space-y-2">
                  <Label>Filter Sessions by Tags (optional)</Label>
                  <div className="flex items-center gap-2 flex-wrap min-h-[32px]">
                    <TagInput
                      allTags={allTags}
                      selectedTags={campaignFilterTags}
                      onTagAdd={(tag) => setCampaignFilterTags(prev => [...prev, tag])}
                      onTagRemove={(tag) => setCampaignFilterTags(prev => prev.filter(t => t !== tag))}
                      placeholder="Search tags..."
                      showSelectedAsBadges={true}
                      allowCreate={false}
                    />
                    {campaignFilterTags.length > 0 && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setCampaignFilterTags([])}
                      >
                        Clear
                      </Button>
                    )}
                  </div>
                  <p className="text-xs text-[#999999]">
                    {campaignFilterTags.length > 0
                      ? `Only sessions with ALL selected tags (${sessions.filter(s => s.valid && campaignFilterTags.every(tag => (s.tags || []).includes(tag))).length} matching)`
                      : 'Leave empty to use all valid sessions'}
                  </p>
                </div>

                <Button
                  onClick={addToQueue}
                  disabled={!url || !comments || addingToQueue || queueState.pending_count >= queueState.max_pending}
                >
                  {addingToQueue ? (
                    <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  ) : (
                    <Plus className="w-4 h-4 mr-2" />
                  )}
                  {addingToQueue ? 'Adding...' : 'Add to Queue'}
                </Button>
              </CardContent>
            </Card>

            {/* Campaign Queue - Server Synced */}
            <Card className="">
              <CardHeader className="bg-[rgba(51,51,51,0.04)] border-b border-[rgba(0,0,0,0.1)] pb-4">
                <CardTitle className="text-lg flex items-center justify-between">
                  <span className="flex items-center gap-2">
                    Campaign Queue
                    <Badge variant="outline" className="ml-2 font-normal">
                      {queueState.pending_count}/{queueState.max_pending}
                    </Badge>
                  </span>
                  {queueState.pending.some(c => c.status === 'processing') && (
                    <Badge className="bg-blue-500 animate-pulse">
                      <Loader2 className="w-3 h-3 mr-1 animate-spin" />
                      Processing
                    </Badge>
                  )}
                </CardTitle>
                {queueState.pending.length > 0 && (
                  <p className="text-sm text-[#999999]">
                    {queueState.pending.reduce((sum, c) => sum + c.comments.length, 0)} comments queued |
                    Est. {formatDuration(queueState.pending.reduce((sum, c) => sum + c.duration_minutes, 0))}
                  </p>
                )}
              </CardHeader>
              <CardContent className="p-0">
                {queueLoading ? (
                  <div className="p-8 text-center text-[#999999]">
                    <Loader2 className="w-8 h-8 mx-auto mb-2 animate-spin opacity-50" />
                    <p>Loading queue...</p>
                  </div>
                ) : queueState.pending.length === 0 ? (
                  <div className="p-8 text-center text-[#999999]">
                    <AlertCircle className="w-8 h-8 mx-auto mb-2 opacity-50" />
                    <p>No campaigns in queue. Add a campaign above to get started.</p>
                    <p className="text-xs mt-2">Campaigns run automatically in the background</p>
                  </div>
                ) : (
                  <div className="divide-y divide-[rgba(0,0,0,0.1)]">
                    {queueState.pending.map((campaign, i) => (
                      <div
                        key={campaign.id}
                        className={`p-4 flex items-center justify-between transition-all duration-200 hover:bg-white ${
                          campaign.status === 'processing' ? 'bg-blue-50 border-l-4 border-blue-500' : ''
                        }`}
                      >
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="font-medium text-[#333333] shrink-0">#{i + 1}</span>
                            <div className="relative overflow-hidden flex-1 min-w-0">
                              <span className="text-sm text-[#111111] whitespace-nowrap block">{campaign.url}</span>
                              <div className="absolute inset-y-0 right-0 w-16 bg-gradient-to-l from-white to-transparent pointer-events-none" />
                            </div>
                          </div>
                          <div className="text-sm text-[#999999]">
                            {campaign.comments.length} comments | {formatDuration(campaign.duration_minutes)}
                            {campaign.filter_tags && campaign.filter_tags.length > 0 && (
                              <span className="ml-2">| Tags: {campaign.filter_tags.join(', ')}</span>
                            )}
                          </div>
                          {campaign.status === 'processing' && campaign.current_job !== undefined && campaign.total_jobs !== undefined && (
                            <div className="mt-2">
                              <div className="flex items-center gap-2 text-xs text-blue-600">
                                <span>Job {campaign.current_job}/{campaign.total_jobs}</span>
                                {campaign.current_profile && <span>({campaign.current_profile})</span>}
                              </div>
                              <div className="w-full h-1.5 bg-gray-200 rounded-full mt-1 overflow-hidden">
                                <div
                                  className="h-full bg-blue-500 rounded-full transition-all duration-300"
                                  style={{ width: `${(campaign.current_job / campaign.total_jobs) * 100}%` }}
                                />
                              </div>
                            </div>
                          )}
                          <div className="text-xs text-[#bbbbbb] mt-1">
                            Added by {campaign.created_by}
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          <Badge
                            variant={
                              campaign.status === 'processing' ? 'default' :
                              'secondary'
                            }
                            className={campaign.status === 'processing' ? 'bg-blue-500' : ''}
                          >
                            {campaign.status === 'processing' ? (
                              <Loader2 className="w-3 h-3 mr-1 animate-spin" />
                            ) : null}
                            {campaign.status}
                          </Badge>
                          {campaign.status === 'pending' && (
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => removeFromQueue(campaign.id)}
                              title="Remove from queue"
                            >
                              <X className="w-4 h-4" />
                            </Button>
                          )}
                          {campaign.status === 'processing' && (
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => cancelCampaign(campaign.id)}
                              title="Cancel campaign"
                              className="text-red-500 hover:text-red-700"
                            >
                              <X className="w-4 h-4" />
                            </Button>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Campaign History */}
            {queueState.history.length > 0 && (
              <Card className="">
                <CardHeader className="bg-[rgba(51,51,51,0.04)] border-b border-[rgba(0,0,0,0.1)] pb-4">
                  <CardTitle className="text-lg">
                    Recent History ({queueState.history.length})
                  </CardTitle>
                </CardHeader>
                <CardContent className="p-0 max-h-64 overflow-y-auto">
                  <div className="divide-y divide-[rgba(0,0,0,0.1)]">
                    {queueState.history.slice(0, 10).map((campaign) => (
                      <div
                        key={campaign.id}
                        className="p-3 flex items-center justify-between hover:bg-white text-sm cursor-pointer transition-colors"
                        onClick={() => setSelectedCampaign(campaign)}
                      >
                        <div className="flex-1 min-w-0 flex items-center gap-3">
                          {campaign.status === 'completed' ? (
                            <CheckCircle className="w-4 h-4 text-green-500 shrink-0" />
                          ) : campaign.status === 'failed' ? (
                            <XCircle className="w-4 h-4 text-red-500 shrink-0" />
                          ) : (
                            <AlertCircle className="w-4 h-4 text-yellow-500 shrink-0" />
                          )}
                          <div className="flex-1 min-w-0">
                            <div className="truncate text-[#333333]">{campaign.url}</div>
                            <div className="text-xs text-[#999999]">
                              {campaign.success_count !== undefined && campaign.total_count !== undefined
                                ? `${campaign.success_count}/${campaign.total_count} successful`
                                : campaign.error || campaign.status}
                              {campaign.completed_at && (
                                <span className="ml-2">
                                  {formatRelativeTime(campaign.completed_at)}
                                </span>
                              )}
                              {campaign.has_retries && (
                                <span className="ml-2 text-blue-500">(retried)</span>
                              )}
                            </div>
                          </div>
                        </div>
                        <ChevronRight className="w-4 h-4 text-[#999999] shrink-0" />
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            )}
          </TabsContent>

          <TabsContent value="live" className="mt-6">
            <Card className="">
              <CardHeader className="bg-[rgba(51,51,51,0.04)] border-b border-[rgba(0,0,0,0.1)] pb-4">
                <CardTitle className="text-lg flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="relative flex h-3 w-3">
                      <span className={`animate-ping absolute inline-flex h-full w-full rounded-full ${liveStatus.connected ? 'bg-green-400' : 'bg-red-400'} opacity-75`}></span>
                      <span className={`relative inline-flex rounded-full h-3 w-3 ${liveStatus.connected ? 'bg-green-500' : 'bg-red-500'}`}></span>
                    </span>
                    Live Automation View
                  </div>
                  <div className="flex items-center gap-2 text-sm font-normal">
                    {liveStatus.connected ? (
                      <Badge variant="default" className="bg-green-500">
                        <Wifi className="w-3 h-3 mr-1" />
                        Connected
                      </Badge>
                    ) : (
                      <Badge variant="destructive">
                        <WifiOff className="w-3 h-3 mr-1" />
                        Disconnected
                      </Badge>
                    )}
                  </div>
                </CardTitle>
              </CardHeader>
              <CardContent className="p-0 bg-black">
                <div className="relative aspect-video flex items-center justify-center overflow-hidden">
                  <img
                    src={`${API_BASE}/screenshots/latest.png?t=${screenshotKey}`}
                    alt="Live Bot View"
                    className="max-h-full max-w-full object-contain"
                    onError={(e) => {
                      (e.target as HTMLImageElement).style.display = 'none';
                    }}
                    onLoad={(e) => {
                      (e.target as HTMLImageElement).style.display = 'block';
                    }}
                  />
                  {/* Status overlay */}
                  <div className="absolute top-4 left-4 bg-black/70 text-white px-3 py-2 rounded-lg text-sm font-mono backdrop-blur-sm">
                    <div className="flex items-center gap-2 mb-1">
                      <Eye className="w-4 h-4" />
                      <span className="font-semibold">{liveStatus.currentStep}</span>
                    </div>
                    {liveStatus.totalJobs > 0 && (
                      <div className="text-xs text-[#999999]">
                        Job {liveStatus.currentJob} of {liveStatus.totalJobs}
                      </div>
                    )}
                  </div>
                  <div className="absolute bottom-4 left-4 bg-black/50 text-white px-2 py-1 rounded text-xs font-mono backdrop-blur-sm">
                    Viewport: iPhone 12 Pro (393x873) | Vision: Gemini 3 Flash
                  </div>
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="sessions" className="mt-6">
            <Card className="">
              <CardHeader className="bg-[rgba(51,51,51,0.04)] border-b border-[rgba(0,0,0,0.1)] pb-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <Checkbox
                      checked={filteredSessions.length > 0 && selectedSessions.size === filteredSessions.length}
                      onCheckedChange={toggleAllSessions}
                      className="data-[state=checked]:bg-[#333333]"
                    />
                    <CardTitle className="text-lg">Sessions ({filteredSessions.length})</CardTitle>
                  </div>
                  <div className="flex items-center gap-2">
                    <Button size="sm" variant="outline" onClick={fetchSessions}>
                      <RefreshCw className="w-4 h-4 mr-2" />
                      Reload
                    </Button>
                  </div>
                </div>
              </CardHeader>

              {/* Bulk Actions Toolbar */}
              {selectedSessions.size > 0 && (
                <div className="px-4 py-3 bg-blue-50 border-b border-blue-200 flex items-center gap-3 flex-wrap">
                  <span className="text-sm font-medium text-blue-700">
                    {selectedSessions.size} selected
                  </span>
                  <div className="flex items-center gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => setBulkTagModalOpen(true)}
                      className="h-7 text-xs"
                    >
                      <Tag className="w-3 h-3 mr-1" />
                      Add Tag
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={bulkRefreshNames}
                      disabled={bulkRefreshing}
                      className="h-7 text-xs"
                    >
                      {bulkRefreshing ? (
                        <Loader2 className="w-3 h-3 mr-1 animate-spin" />
                      ) : (
                        <RefreshCw className="w-3 h-3 mr-1" />
                      )}
                      Refresh Names
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={bulkDeleteSessions}
                      disabled={bulkDeleting}
                      className="h-7 text-xs text-red-600 hover:text-red-700 hover:bg-red-50"
                    >
                      {bulkDeleting ? (
                        <Loader2 className="w-3 h-3 mr-1 animate-spin" />
                      ) : (
                        <Trash2 className="w-3 h-3 mr-1" />
                      )}
                      Delete
                    </Button>
                  </div>
                  <div className="flex items-center gap-2 ml-auto">
                    <label className="flex items-center gap-1.5 text-xs text-[#666666] cursor-pointer">
                      <Checkbox
                        checked={keepSelection}
                        onCheckedChange={(checked) => setKeepSelection(checked === true)}
                        className="h-3.5 w-3.5 data-[state=checked]:bg-[#333333]"
                      />
                      Keep selection
                    </label>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setSelectedSessions(new Set())}
                      className="h-7 text-xs"
                    >
                      Clear
                    </Button>
                  </div>
                </div>
              )}

              {/* Bulk Tag Modal */}
              {bulkTagModalOpen && (
                <div className="px-4 py-3 bg-[rgba(51,51,51,0.02)] border-b border-[rgba(0,0,0,0.1)] flex items-center gap-3">
                  <span className="text-sm text-[#666666]">Add tag to {selectedSessions.size} sessions:</span>
                  <TagInput
                    allTags={allTags}
                    selectedTags={[]}
                    onTagAdd={bulkAddTag}
                    placeholder="Search or create tag..."
                    allowCreate={true}
                    showSelectedAsBadges={false}
                  />
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setBulkTagModalOpen(false)}
                  >
                    Cancel
                  </Button>
                </div>
              )}

              {/* Filter Bar - Search, Tags, Status */}
              {!bulkTagModalOpen && (
                <div className="px-4 py-3 bg-[rgba(51,51,51,0.02)] border-b border-[rgba(0,0,0,0.1)] flex items-center gap-3 flex-wrap">
                  {/* Search Input */}
                  <div className="relative">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#999999]" />
                    <input
                      type="text"
                      value={sessionSearchQuery}
                      onChange={(e) => setSessionSearchQuery(e.target.value)}
                      placeholder="Search names..."
                      className="h-9 pl-9 pr-3 w-48 rounded-full border border-[rgba(0,0,0,0.1)] bg-white text-sm text-[#111111] placeholder:text-[#999999] focus:outline-none focus:border-[#333333] transition-colors"
                    />
                  </div>

                  <div className="w-px h-6 bg-[rgba(0,0,0,0.1)]" />

                  {/* Tag Filter */}
                  <div className="flex items-center gap-2">
                    <Tag className="w-4 h-4 text-[#666666]" />
                    <TagInput
                      allTags={allTags}
                      selectedTags={sessionFilterTags}
                      onTagAdd={(tag) => setSessionFilterTags(prev => [...prev, tag])}
                      onTagRemove={(tag) => setSessionFilterTags(prev => prev.filter(t => t !== tag))}
                      placeholder="Filter tags..."
                      showSelectedAsBadges={true}
                      allowCreate={false}
                    />
                  </div>

                  <div className="w-px h-6 bg-[rgba(0,0,0,0.1)]" />

                  {/* Status Quick Filters */}
                  <div className="flex items-center gap-1.5">
                    <button
                      onClick={() => setSessionStatusFilters(prev => ({
                        ...prev,
                        valid: prev.valid === true ? undefined : true
                      }))}
                      className={`h-7 px-2.5 text-xs rounded-full border transition-colors ${
                        sessionStatusFilters.valid === true
                          ? 'bg-green-100 border-green-300 text-green-700'
                          : 'bg-white border-[rgba(0,0,0,0.1)] text-[#666666] hover:border-[#999999]'
                      }`}
                    >
                      Valid
                    </button>
                    <button
                      onClick={() => setSessionStatusFilters(prev => ({
                        ...prev,
                        valid: prev.valid === false ? undefined : false
                      }))}
                      className={`h-7 px-2.5 text-xs rounded-full border transition-colors ${
                        sessionStatusFilters.valid === false
                          ? 'bg-red-100 border-red-300 text-red-700'
                          : 'bg-white border-[rgba(0,0,0,0.1)] text-[#666666] hover:border-[#999999]'
                      }`}
                    >
                      Invalid
                    </button>
                    <button
                      onClick={() => setSessionStatusFilters(prev => ({
                        ...prev,
                        hasProxy: prev.hasProxy === true ? undefined : true
                      }))}
                      className={`h-7 px-2.5 text-xs rounded-full border transition-colors ${
                        sessionStatusFilters.hasProxy === true
                          ? 'bg-blue-100 border-blue-300 text-blue-700'
                          : 'bg-white border-[rgba(0,0,0,0.1)] text-[#666666] hover:border-[#999999]'
                      }`}
                    >
                      Proxy
                    </button>
                    <button
                      onClick={() => setSessionStatusFilters(prev => ({
                        ...prev,
                        hasProxy: prev.hasProxy === false ? undefined : false
                      }))}
                      className={`h-7 px-2.5 text-xs rounded-full border transition-colors ${
                        sessionStatusFilters.hasProxy === false
                          ? 'bg-orange-100 border-orange-300 text-orange-700'
                          : 'bg-white border-[rgba(0,0,0,0.1)] text-[#666666] hover:border-[#999999]'
                      }`}
                    >
                      No Proxy
                    </button>
                  </div>

                  {/* Clear All Filters */}
                  {(sessionSearchQuery || sessionFilterTags.length > 0 || Object.keys(sessionStatusFilters).some(k => sessionStatusFilters[k as keyof typeof sessionStatusFilters] !== undefined)) && (
                    <>
                      <div className="w-px h-6 bg-[rgba(0,0,0,0.1)]" />
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          setSessionSearchQuery('');
                          setSessionFilterTags([]);
                          setSessionStatusFilters({});
                        }}
                        className="h-7 text-xs"
                      >
                        Clear All
                      </Button>
                    </>
                  )}
                </div>
              )}

              <CardContent className="p-0">
                {sessionsLoading ? (
                  <div className="p-8 text-center text-[#999999]">
                    <Loader2 className="w-8 h-8 animate-spin mx-auto mb-4" />
                    Loading sessions...
                  </div>
                ) : sessions.length === 0 ? (
                  <div className="p-8 text-center text-[#999999]">
                    No sessions found. Extract sessions from AdsPower first.
                  </div>
                ) : filteredSessions.length === 0 ? (
                  <div className="p-8 text-center text-[#999999]">
                    No sessions match the current filters.
                  </div>
                ) : (
                  <div className="divide-y divide-[rgba(0,0,0,0.1)]">
                    {filteredSessions.map((session, index) => (
                      <div
                        key={session.file}
                        className={`px-4 py-3 flex items-center gap-4 hover:bg-white transition-colors ${
                          selectedSessions.has(session.profile_name) ? 'bg-blue-50' : ''
                        }`}
                      >
                        {/* Checkbox with shift-click support */}
                        <Checkbox
                          checked={selectedSessions.has(session.profile_name)}
                          onClick={(e) => {
                            e.preventDefault();
                            toggleSessionSelection(session.profile_name, index, e.shiftKey);
                          }}
                          className="data-[state=checked]:bg-[#333333] flex-shrink-0"
                        />

                        {/* Avatar - smaller */}
                        <div className="w-10 h-10 rounded-full overflow-hidden bg-[rgba(0,0,0,0.1)] flex-shrink-0">
                          {session.profile_picture ? (
                            <img
                              src={`data:image/png;base64,${session.profile_picture}`}
                              alt={session.profile_name}
                              className="w-full h-full object-cover"
                            />
                          ) : (
                            <div className="w-full h-full flex items-center justify-center text-[#999999] text-sm font-medium">
                              {(session.display_name || session.profile_name)?.[0]?.toUpperCase() || '?'}
                            </div>
                          )}
                        </div>

                        {/* Name & Info - horizontal */}
                        <div className="flex-1 min-w-0 flex items-center gap-4">
                          <div className="min-w-[140px]">
                            <div className="font-medium text-[#111111] text-sm truncate">{session.display_name || session.profile_name}</div>
                            <div className="text-xs text-[#999999] flex items-center gap-2">
                              <span className="truncate">{session.user_id?.slice(0, 10) || 'Unknown'}</span>
                              <span>•</span>
                              <span>{session.extracted_at.split('T')[0]}</span>
                              <span>•</span>
                              <span className={`flex items-center gap-1 ${session.proxy_masked ? 'text-[#999999]' : 'text-red-400'}`}>
                                <span className={`w-1.5 h-1.5 rounded-full ${session.proxy_masked ? 'bg-green-500' : 'bg-red-500'}`}></span>
                                {session.proxy_masked ? 'Proxy' : 'No Proxy'}
                              </span>
                            </div>
                          </div>

                          {/* Tags - inline (sorted alphabetically) */}
                          <div className="flex items-center gap-1 flex-wrap flex-1">
                            {[...(session.tags || [])].sort().map(tag => (
                              <Badge
                                key={tag}
                                variant="secondary"
                                className="text-[10px] py-0 h-5 cursor-pointer hover:bg-red-100 group"
                                onClick={() => {
                                  const newTags = (session.tags || []).filter(t => t !== tag);
                                  updateSessionTags(session.profile_name, newTags);
                                }}
                              >
                                {tag}
                                <X className="w-2 h-2 ml-1 opacity-0 group-hover:opacity-100" />
                              </Badge>
                            ))}
                            <TagInput
                              allTags={allTags}
                              selectedTags={session.tags || []}
                              onTagAdd={(tag) => updateSessionTags(session.profile_name, [...(session.tags || []), tag])}
                              placeholder=""
                              size="sm"
                              allowCreate={true}
                              showSelectedAsBadges={false}
                            />
                          </div>
                        </div>

                        {/* Actions - compact */}
                        <div className="flex items-center gap-1.5 flex-shrink-0">
                          <Badge variant={session.valid ? 'default' : 'destructive'} className="text-xs">
                            {session.valid ? 'Valid' : 'Invalid'}
                          </Badge>
                          <Button
                            size="sm"
                            variant="default"
                            onClick={() => openRemoteModal(session)}
                            disabled={!session.valid}
                            className="h-7 text-xs px-2"
                          >
                            <Mouse className="w-3 h-3" />
                          </Button>
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => refreshSessionName(session.profile_name)}
                            disabled={refreshingSession === session.profile_name}
                            className="h-7 w-7 p-0"
                          >
                            {refreshingSession === session.profile_name ? (
                              <Loader2 className="w-3 h-3 animate-spin" />
                            ) : (
                              <RefreshCw className="w-3 h-3" />
                            )}
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => deleteSession(session.profile_name)}
                            className="h-7 w-7 p-0"
                          >
                            <Trash2 className="w-3 h-3 text-red-500" />
                          </Button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="credentials" className="mt-6">
            {/* Bulk Import Section */}
            <Card className=" mb-6">
              <CardHeader className="bg-[rgba(51,51,51,0.04)] border-b border-[rgba(0,0,0,0.1)] pb-4">
                <CardTitle className="text-lg flex items-center gap-2">
                  <Upload className="w-4 h-4" />
                  Bulk Import
                </CardTitle>
              </CardHeader>
              <CardContent className="pt-6">
                <div className="flex items-center gap-4">
                  <Input
                    type="file"
                    accept=".txt"
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) bulkImportCredentials(file);
                      e.target.value = '';
                    }}
                    className="bg-white"
                    disabled={isImporting}
                  />
                  {isImporting && <Loader2 className="w-5 h-5 animate-spin" />}
                </div>
                <p className="text-xs text-[#999999] mt-2">
                  Format: uid:password:2fa_secret (one per line)
                </p>
              </CardContent>
            </Card>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <Card className="">
                <CardHeader className="bg-[rgba(51,51,51,0.04)] border-b border-[rgba(0,0,0,0.1)] pb-4">
                  <CardTitle className="text-lg flex items-center gap-2">
                    <Key className="w-4 h-4" />
                    Add Credential
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4 pt-6">
                  <div className="space-y-2">
                    <Label>UID (Email or Phone)</Label>
                    <Input 
                      value={newUid}
                      onChange={(e) => setNewUid(e.target.value)}
                      placeholder="user@email.com or 123456789"
                      className="bg-white"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label>Password</Label>
                    <Input 
                      type="password"
                      value={newPassword}
                      onChange={(e) => setNewPassword(e.target.value)}
                      placeholder="********"
                      className="bg-white"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label>2FA Secret (optional)</Label>
                    <Input 
                      value={newSecret}
                      onChange={(e) => setNewSecret(e.target.value)}
                      placeholder="JBSWY3DPEHPK3PXP"
                      className="bg-white"
                    />
                    <p className="text-xs text-[#999999]">Base32 secret from Google Authenticator</p>
                  </div>
                  <div className="space-y-2">
                    <Label>Profile Name (optional)</Label>
                    <Input 
                      value={newProfileName}
                      onChange={(e) => setNewProfileName(e.target.value)}
                      placeholder="My Profile"
                      className="bg-white"
                    />
                  </div>
                  <Button onClick={addCredential} className="w-full">
                    <Key className="w-4 h-4 mr-2" />
                    Add Credential
                  </Button>
                </CardContent>
              </Card>

              <Card className="">
                <CardHeader className="bg-[rgba(51,51,51,0.04)] border-b border-[rgba(0,0,0,0.1)] pb-4">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      {eligibleCredentials.length > 0 && (
                        <Checkbox
                          checked={allSelected}
                          onCheckedChange={toggleSelectAll}
                          disabled={batchInProgress || creatingSession !== null}
                          aria-label="Select all eligible credentials"
                        />
                      )}
                      <CardTitle className="text-lg">Saved Credentials ({credentials.length})</CardTitle>
                    </div>
                    <div className="flex items-center gap-2">
                      {selectedCredentials.size > 0 && (
                        <Button
                          size="sm"
                          onClick={createBatchSessions}
                          disabled={batchInProgress || creatingSession !== null}
                        >
                          {batchInProgress ? (
                            <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                          ) : (
                            <Play className="w-4 h-4 mr-2" />
                          )}
                          Create {selectedCredentials.size} Session{selectedCredentials.size > 1 ? 's' : ''}
                        </Button>
                      )}
                      <Button size="sm" variant="outline" onClick={fetchCredentials}>
                        <RefreshCw className="w-4 h-4 mr-2" />
                        Refresh
                      </Button>
                    </div>
                  </div>
                </CardHeader>
                <CardContent className="p-0">
                  {credentialsLoading ? (
                    <div className="p-8 text-center text-[#999999]">
                      <Loader2 className="w-8 h-8 animate-spin mx-auto mb-4" />
                      Loading credentials...
                    </div>
                  ) : credentials.length === 0 ? (
                    <div className="p-8 text-center text-[#999999]">
                      No credentials saved yet.
                    </div>
                  ) : (
                    <div className="divide-y divide-[rgba(0,0,0,0.1)] max-h-[500px] overflow-y-auto">
                      {credentials.map((cred) => {
                        const isEligible = cred.has_secret && !cred.session_connected;
                        const isSelected = selectedCredentials.has(cred.uid);

                        return (
                          <div key={cred.uid} className="p-4 hover:bg-white">
                            <div className="flex items-start gap-3">
                              {/* Checkbox - only show for eligible credentials */}
                              {isEligible && (
                                <Checkbox
                                  checked={isSelected}
                                  onCheckedChange={() => toggleCredentialSelection(cred.uid)}
                                  disabled={batchInProgress || creatingSession !== null}
                                  className="mt-1"
                                />
                              )}

                              {/* Credential content */}
                              <div className="flex-1">
                                <div className="flex items-center justify-between mb-2">
                                  <div className="font-medium text-[#111111]">{cred.uid}</div>
                                  <div className="flex items-center gap-2">
                                    <Badge variant={cred.has_secret ? 'default' : 'secondary'}>
                                      {cred.has_secret ? '2FA' : 'No 2FA'}
                                    </Badge>
                                    <Badge variant={cred.session_connected ? (cred.session_valid ? 'default' : 'destructive') : 'secondary'}>
                                      {cred.session_connected ? (cred.session_valid ? 'Session Linked' : 'Session Invalid') : 'No Session'}
                                    </Badge>
                                    <Button size="sm" variant="ghost" onClick={() => deleteCredential(cred.uid)}>
                                      <Trash2 className="w-3 h-3 text-red-500" />
                                    </Button>
                                  </div>
                                </div>
                                {/* Show session's profile name if linked, otherwise show credential's profile name */}
                                {(cred.session_connected && cred.session_profile_name) ? (
                                  <div className="text-xs text-green-600 mb-2 flex items-center gap-1">
                                    <CheckCircle className="w-3 h-3" />
                                    Session: {cred.session_profile_name}
                                  </div>
                                ) : cred.profile_name ? (
                                  <div className="text-xs text-[#999999] mb-2">Profile: {cred.profile_name}</div>
                                ) : null}
                                {/* Session creation status or button */}
                                <div className="mb-2">
                                  {sessionCreateStatus[cred.uid] ? (
                                    <div className={`text-xs p-2 rounded flex items-center gap-2 ${
                                      sessionCreateStatus[cred.uid].status === 'success' ? 'bg-green-100 text-green-700' :
                                      sessionCreateStatus[cred.uid].status === 'failed' ? 'bg-red-100 text-red-700' :
                                      sessionCreateStatus[cred.uid].status === 'needs_attention' ? 'bg-orange-100 text-orange-700' :
                                      'bg-blue-100 text-blue-700'
                                    }`}>
                                      {sessionCreateStatus[cred.uid].status === 'in_progress' && (
                                        <Loader2 className="w-3 h-3 animate-spin" />
                                      )}
                                      {sessionCreateStatus[cred.uid].status === 'needs_attention' && (
                                        <AlertCircle className="w-3 h-3" />
                                      )}
                                      {sessionCreateStatus[cred.uid].step}
                                    </div>
                                  ) : !cred.session_connected ? (
                                    <Button
                                      size="sm"
                                      variant="outline"
                                      onClick={() => createSession(cred.uid)}
                                      disabled={creatingSession !== null || batchInProgress || !cred.has_secret}
                                    >
                                      {creatingSession === cred.uid ? (
                                        <Loader2 className="w-3 h-3 mr-1 animate-spin" />
                                      ) : (
                                        <Play className="w-3 h-3 mr-1" />
                                      )}
                                      Create Session
                                    </Button>
                                  ) : null}
                                </div>
                                {cred.has_secret && (
                                  <div className="flex items-center gap-2">
                                    {otpData[cred.uid]?.valid ? (
                                      <>
                                        <div className="bg-[#333333] text-white px-3 py-1 rounded font-mono text-lg">
                                          {otpData[cred.uid].code}
                                        </div>
                                        <Button size="sm" variant="outline" onClick={() => copyOTP(otpData[cred.uid].code)}>
                                          <Copy className="w-3 h-3" />
                                        </Button>
                                        <span className="text-xs text-[#999999]">
                                          {otpData[cred.uid].remaining_seconds}s
                                        </span>
                                      </>
                                    ) : (
                                      <Button size="sm" variant="secondary" onClick={() => getOTP(cred.uid)}>
                                        Get OTP
                                      </Button>
                                    )}
                                  </div>
                                )}
                              </div>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </CardContent>
              </Card>
            </div>
          </TabsContent>

          <TabsContent value="proxies" className="mt-6">
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <Card className="">
                <CardHeader className="bg-[rgba(51,51,51,0.04)] border-b border-[rgba(0,0,0,0.1)] pb-4">
                  <CardTitle className="text-lg flex items-center gap-2">
                    <Globe className="w-4 h-4" />
                    Add Proxy
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4 pt-6">
                  <div className="space-y-2">
                    <Label>Name</Label>
                    <Input
                      value={newProxyName}
                      onChange={(e) => setNewProxyName(e.target.value)}
                      placeholder="US Mobile 1"
                      className="bg-white"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label>URL</Label>
                    <Input
                      value={newProxyUrl}
                      onChange={(e) => setNewProxyUrl(e.target.value)}
                      placeholder="http://user:pass@host:port"
                      className="bg-white"
                    />
                    <p className="text-xs text-[#999999]">Format: http://username:password@host:port</p>
                  </div>
                  <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-2">
                      <Label>Type</Label>
                      <select
                        value={newProxyType}
                        onChange={(e) => setNewProxyType(e.target.value)}
                        className="w-full h-10 px-3 border border-[rgba(0,0,0,0.1)] rounded-md bg-white text-sm"
                      >
                        <option value="mobile">Mobile</option>
                        <option value="residential">Residential</option>
                        <option value="datacenter">Datacenter</option>
                      </select>
                    </div>
                    <div className="space-y-2">
                      <Label>Country</Label>
                      <Input
                        value={newProxyCountry}
                        onChange={(e) => setNewProxyCountry(e.target.value)}
                        placeholder="US"
                        className="bg-white"
                      />
                    </div>
                  </div>
                  <Button onClick={addProxy} className="w-full">
                    <Plus className="w-4 h-4 mr-2" />
                    Add Proxy
                  </Button>
                </CardContent>
              </Card>

              <Card className="">
                <CardHeader className="bg-[rgba(51,51,51,0.04)] border-b border-[rgba(0,0,0,0.1)] pb-4 flex flex-row justify-between items-center">
                  <CardTitle className="text-lg">Saved Proxies ({proxies.length})</CardTitle>
                  <Button size="sm" variant="outline" onClick={fetchProxies}>
                    <RefreshCw className="w-4 h-4 mr-2" />
                    Refresh
                  </Button>
                </CardHeader>
                <CardContent className="p-0">
                  {proxiesLoading ? (
                    <div className="p-8 text-center text-[#999999]">
                      <Loader2 className="w-8 h-8 animate-spin mx-auto mb-4" />
                      Loading proxies...
                    </div>
                  ) : proxies.length === 0 ? (
                    <div className="p-8 text-center text-[#999999]">
                      No proxies configured yet.
                    </div>
                  ) : (
                    <div className="divide-y divide-[rgba(0,0,0,0.1)] max-h-[500px] overflow-y-auto">
                      {proxies.map((proxy) => (
                        <div key={proxy.id} className={`p-4 hover:bg-white ${proxy.is_system ? 'bg-green-50/50' : ''} ${proxy.is_default ? 'bg-blue-50/50 border-l-4 border-blue-500' : ''}`}>
                          <div className="flex items-center justify-between mb-2">
                            <div className="flex items-center gap-2">
                              <span className="font-medium text-[#111111]">{proxy.name}</span>
                              {proxy.is_system && (
                                <Badge variant="secondary" className="text-[10px]">System</Badge>
                              )}
                              {proxy.is_default && (
                                <Badge variant="default" className="text-[10px] bg-blue-500">Default</Badge>
                              )}
                            </div>
                            <div className="flex items-center gap-2">
                              <Badge variant={
                                proxy.health_status === 'healthy' ? 'default' :
                                proxy.health_status === 'active' ? 'default' :
                                proxy.health_status === 'degraded' ? 'secondary' :
                                proxy.health_status === 'unhealthy' ? 'destructive' :
                                'outline'
                              }>
                                {proxy.health_status || 'untested'}
                              </Badge>
                              {!proxy.is_system && (
                                <>
                                  <Button
                                    size="sm"
                                    variant={proxy.is_default ? "default" : "outline"}
                                    onClick={() => setProxyAsDefault(proxy.id)}
                                    disabled={proxy.is_default || settingDefaultProxy === proxy.id}
                                    className={proxy.is_default ? "bg-blue-500 hover:bg-blue-600" : ""}
                                  >
                                    {settingDefaultProxy === proxy.id ? (
                                      <Loader2 className="w-3 h-3 animate-spin" />
                                    ) : proxy.is_default ? (
                                      <Check className="w-3 h-3" />
                                    ) : (
                                      <Star className="w-3 h-3" />
                                    )}
                                  </Button>
                                  <Button
                                    size="sm"
                                    variant="outline"
                                    onClick={() => testProxy(proxy.id)}
                                    disabled={testingProxy === proxy.id}
                                  >
                                    {testingProxy === proxy.id ? (
                                      <Loader2 className="w-3 h-3 animate-spin" />
                                    ) : (
                                      <Play className="w-3 h-3" />
                                    )}
                                  </Button>
                                  <Button size="sm" variant="ghost" onClick={() => deleteProxy(proxy.id)}>
                                    <Trash2 className="w-3 h-3 text-red-500" />
                                  </Button>
                                </>
                              )}
                            </div>
                          </div>
                          <div className="text-xs text-[#999999] space-y-1">
                            <div>URL: {proxy.url_masked}</div>
                            <div className="flex items-center gap-4">
                              <span>Type: {proxy.type}</span>
                              <span>Country: {proxy.country}</span>
                            </div>
                            {proxy.success_rate !== null && (
                              <div className="flex items-center gap-4">
                                <span>Success: {(proxy.success_rate * 100).toFixed(0)}%</span>
                                {proxy.avg_response_ms && <span>Avg: {proxy.avg_response_ms}ms</span>}
                                <span>Tests: {proxy.test_count}</span>
                              </div>
                            )}
                            {proxy.assigned_sessions.length > 0 && (
                              <div className="text-green-600 font-medium">
                                <span className="w-2 h-2 rounded-full bg-green-500 inline-block mr-1"></span>
                                {proxy.assigned_sessions.length} sessions connected
                              </div>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>
            </div>
          </TabsContent>

          {/* Analytics Tab - Gemini Observations */}
          <TabsContent value="analytics" className="mt-6 space-y-6">
            {/* Summary Stats */}
            {analyticsSummary && (
              <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                <Card className="p-4">
                  <div className="text-2xl font-bold">{analyticsSummary.today.comments}</div>
                  <div className="text-sm text-[#666666]">Today's Comments</div>
                  <div className="text-xs text-green-600 mt-1">
                    {analyticsSummary.today.success_rate.toFixed(0)}% success
                  </div>
                </Card>
                <Card className="p-4">
                  <div className="text-2xl font-bold">{analyticsSummary.week.comments}</div>
                  <div className="text-sm text-[#666666]">This Week</div>
                  <div className="text-xs text-green-600 mt-1">
                    {analyticsSummary.week.success_rate.toFixed(0)}% success
                  </div>
                </Card>
                <Card className="p-4">
                  <div className="text-2xl font-bold text-green-600">{analyticsSummary.profiles.active}</div>
                  <div className="text-sm text-[#666666]">Active Profiles</div>
                </Card>
                <Card className="p-4">
                  <div className="text-2xl font-bold text-red-500">{analyticsSummary.profiles.restricted}</div>
                  <div className="text-sm text-[#666666]">Restricted</div>
                </Card>
              </div>
            )}

            {/* Profile Usage Table */}
            <Card>
              <CardHeader className="bg-[rgba(51,51,51,0.04)] border-b border-[rgba(0,0,0,0.1)] pb-4">
                <div className="flex items-center justify-between">
                  <CardTitle className="text-lg flex items-center gap-2">
                    <User className="w-5 h-5" />
                    Profile Usage Tracker
                  </CardTitle>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => fetchProfileAnalytics()}
                    disabled={loadingAnalytics}
                  >
                    {loadingAnalytics ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <RefreshCw className="w-4 h-4" />
                    )}
                    Refresh
                  </Button>
                </div>
                <p className="text-sm text-[#666666] mt-2">
                  Profiles are rotated using LRU (least recently used first) to prevent overuse.
                </p>
              </CardHeader>
              <CardContent className="pt-4">
                {loadingAnalytics ? (
                  <div className="flex items-center justify-center py-8">
                    <Loader2 className="w-6 h-6 animate-spin text-[#999999]" />
                    <span className="ml-2 text-[#666666]">Loading profile stats...</span>
                  </div>
                ) : profileAnalytics.length === 0 ? (
                  <div className="text-center py-8 text-[#999999]">
                    <User className="w-12 h-12 mx-auto mb-2 opacity-50" />
                    <p>No profile data yet.</p>
                    <p className="text-sm">Run a campaign to see usage stats.</p>
                  </div>
                ) : (
                  <div className="space-y-2 max-h-[400px] overflow-y-auto">
                    {profileAnalytics.map((profile) => {
                      const isExpanded = expandedProfile === profile.profile_name;
                      const isRestricted = profile.status === 'restricted';

                      return (
                        <div
                          key={profile.profile_name}
                          className={`border rounded-lg p-3 cursor-pointer transition-colors ${
                            isExpanded ? 'bg-[rgba(51,51,51,0.04)]' : 'hover:bg-[rgba(51,51,51,0.02)]'
                          }`}
                          onClick={() => setExpandedProfile(isExpanded ? null : profile.profile_name)}
                        >
                          {/* Profile Row */}
                          <div className="flex items-center justify-between">
                            <div className="flex items-center gap-3">
                              {isRestricted ? (
                                <div className="w-2 h-2 rounded-full bg-red-500" />
                              ) : (
                                <div className="w-2 h-2 rounded-full bg-green-500" />
                              )}
                              <span className="font-medium">{profile.profile_name}</span>
                              {isRestricted && (
                                <Badge variant="destructive" className="text-xs">
                                  Restricted
                                </Badge>
                              )}
                            </div>
                            <div className="flex items-center gap-4 text-sm text-[#666666]">
                              <span>{profile.usage_count} uses</span>
                              <span>{profile.success_rate.toFixed(0)}% success</span>
                              <span>
                                {profile.last_used_at
                                  ? new Date(profile.last_used_at).toLocaleDateString()
                                  : 'Never used'}
                              </span>
                              <ChevronRight
                                className={`w-4 h-4 transition-transform ${isExpanded ? 'rotate-90' : ''}`}
                              />
                            </div>
                          </div>

                          {/* Expanded Content */}
                          {isExpanded && (
                            <div className="mt-3 pt-3 border-t space-y-3">
                              {/* Actions */}
                              <div className="flex gap-2">
                                {isRestricted ? (
                                  <Button
                                    size="sm"
                                    variant="outline"
                                    onClick={(e) => { e.stopPropagation(); unblockProfile(profile.profile_name); }}
                                  >
                                    <CheckCircle className="w-4 h-4 mr-1" />
                                    Unblock
                                  </Button>
                                ) : (
                                  <Button
                                    size="sm"
                                    variant="outline"
                                    onClick={(e) => { e.stopPropagation(); restrictProfile(profile.profile_name, 24); }}
                                  >
                                    <XCircle className="w-4 h-4 mr-1" />
                                    Restrict 24h
                                  </Button>
                                )}
                              </div>

                              {/* Restriction Info */}
                              {isRestricted && profile.restriction_expires_at && (
                                <div className="text-sm text-red-600">
                                  Expires: {new Date(profile.restriction_expires_at).toLocaleString()}
                                  {profile.restriction_reason && ` (${profile.restriction_reason})`}
                                </div>
                              )}

                              {/* Recent History */}
                              {profile.usage_history && profile.usage_history.length > 0 && (
                                <div>
                                  <p className="text-xs font-medium text-[#666666] mb-2">Recent Activity:</p>
                                  <div className="space-y-1">
                                    {profile.usage_history.slice(0, 5).map((entry, idx) => (
                                      <div key={idx} className="flex items-center gap-2 text-xs">
                                        {entry.success ? (
                                          <CheckCircle className="w-3 h-3 text-green-500" />
                                        ) : (
                                          <XCircle className="w-3 h-3 text-red-500" />
                                        )}
                                        <span className="text-[#999999]">
                                          {new Date(entry.timestamp).toLocaleString()}
                                        </span>
                                        {entry.comment && (
                                          <span className="truncate max-w-[200px]">
                                            "{entry.comment}"
                                          </span>
                                        )}
                                      </div>
                                    ))}
                                  </div>
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Gemini Observations Card */}
            <Card>
              <CardHeader className="bg-[rgba(51,51,51,0.04)] border-b border-[rgba(0,0,0,0.1)] pb-4">
                <div className="flex items-center justify-between">
                  <CardTitle className="text-lg flex items-center gap-2">
                    <BarChart3 className="w-5 h-5" />
                    Gemini AI Observations
                  </CardTitle>
                  <div className="flex gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => fetchGeminiObservations()}
                      disabled={loadingObservations}
                    >
                      {loadingObservations ? (
                        <Loader2 className="w-4 h-4 animate-spin" />
                      ) : (
                        <RefreshCw className="w-4 h-4" />
                      )}
                      Refresh
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => clearGeminiObservations()}
                      disabled={geminiObservations.length === 0}
                    >
                      <Trash2 className="w-4 h-4" />
                      Clear
                    </Button>
                  </div>
                </div>
                <p className="text-sm text-[#666666] mt-2">
                  View full AI responses to understand what Gemini sees on each screenshot.
                  This helps debug false negatives and understand why actions fail.
                </p>
              </CardHeader>
              <CardContent className="pt-4">
                {loadingObservations ? (
                  <div className="flex items-center justify-center py-8">
                    <Loader2 className="w-6 h-6 animate-spin text-[#999999]" />
                    <span className="ml-2 text-[#666666]">Loading observations...</span>
                  </div>
                ) : geminiObservations.length === 0 ? (
                  <div className="text-center py-8 text-[#999999]">
                    <Eye className="w-12 h-12 mx-auto mb-2 opacity-50" />
                    <p>No Gemini observations yet.</p>
                    <p className="text-sm">Run a campaign to see AI vision responses.</p>
                  </div>
                ) : (
                  <div className="space-y-3 max-h-[600px] overflow-y-auto">
                    {geminiObservations.map((obs, index) => {
                      const isExpanded = expandedObservation === index;
                      const result = obs.parsed_result;
                      const isSuccess = result.success === true || result.found === true || result.status === 'verified';

                      return (
                        <div
                          key={`${obs.timestamp}-${index}`}
                          className={`border rounded-lg p-3 cursor-pointer transition-colors ${
                            isExpanded ? 'bg-[rgba(51,51,51,0.04)]' : 'hover:bg-[rgba(51,51,51,0.02)]'
                          }`}
                          onClick={() => setExpandedObservation(isExpanded ? null : index)}
                        >
                          {/* Header Row */}
                          <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2">
                              {isSuccess ? (
                                <CheckCircle className="w-4 h-4 text-green-500" />
                              ) : (
                                <XCircle className="w-4 h-4 text-red-500" />
                              )}
                              <span className="font-medium text-sm">
                                {obs.operation_type}/{obs.prompt_type}
                              </span>
                              <Badge variant="outline" className="text-xs">
                                {obs.screenshot_name}
                              </Badge>
                            </div>
                            <div className="flex items-center gap-2 text-xs text-[#999999]">
                              {obs.profile_name && (
                                <Badge variant="secondary" className="text-xs">
                                  {obs.profile_name}
                                </Badge>
                              )}
                              <span>
                                {new Date(obs.timestamp).toLocaleTimeString()}
                              </span>
                              <ChevronRight
                                className={`w-4 h-4 transition-transform ${isExpanded ? 'rotate-90' : ''}`}
                              />
                            </div>
                          </div>

                          {/* Collapsed Preview */}
                          {!isExpanded && (
                            <p className="text-sm text-[#666666] mt-1 truncate">
                              {obs.full_response.slice(0, 100)}...
                            </p>
                          )}

                          {/* Expanded Content */}
                          {isExpanded && (
                            <div className="mt-3 space-y-3">
                              {/* Parsed Result */}
                              <div className="bg-white rounded border p-3">
                                <p className="text-xs font-medium text-[#666666] mb-1">Parsed Result:</p>
                                <pre className="text-xs font-mono bg-[rgba(0,0,0,0.03)] p-2 rounded overflow-x-auto">
                                  {JSON.stringify(result, null, 2)}
                                </pre>
                              </div>

                              {/* Full Response */}
                              <div className="bg-white rounded border p-3">
                                <p className="text-xs font-medium text-[#666666] mb-1">Full AI Response:</p>
                                <p className="text-sm whitespace-pre-wrap bg-[rgba(0,0,0,0.03)] p-2 rounded">
                                  {obs.full_response}
                                </p>
                              </div>

                              {/* Metadata */}
                              <div className="flex gap-4 text-xs text-[#999999]">
                                <span>Campaign: {obs.campaign_id || 'N/A'}</span>
                                <span>Time: {new Date(obs.timestamp).toLocaleString()}</span>
                              </div>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </CardContent>
            </Card>
          </TabsContent>

          {user?.role === 'admin' && (
            <TabsContent value="admin" className="mt-6">
              <AdminTab />
            </TabsContent>
          )}
        </Tabs>

        </div>
      </div>

      {/* Campaign Details Modal */}
      <Dialog open={!!selectedCampaign} onOpenChange={(open) => !open && setSelectedCampaign(null)}>
        <DialogContent className="max-w-3xl max-h-[85vh] overflow-hidden flex flex-col">
          <DialogHeader className="shrink-0">
            <DialogTitle className="flex items-center gap-2">
              {selectedCampaign?.status === 'completed' ? (
                <CheckCircle className="w-5 h-5 text-green-500" />
              ) : selectedCampaign?.status === 'failed' ? (
                <XCircle className="w-5 h-5 text-red-500" />
              ) : (
                <AlertCircle className="w-5 h-5 text-yellow-500" />
              )}
              Campaign Details
            </DialogTitle>
            <DialogDescription>
              View campaign results and retry failed jobs
            </DialogDescription>
          </DialogHeader>

          {selectedCampaign && (
            <div className="flex-1 overflow-y-auto space-y-4">
              {/* Campaign Info */}
              <div className="bg-[rgba(51,51,51,0.04)] rounded-lg p-4 space-y-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex-1 min-w-0">
                    <div className="text-xs text-[#999999] mb-1">Target URL</div>
                    <div className="flex items-center gap-2">
                      <a
                        href={selectedCampaign.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-sm text-blue-600 hover:underline truncate flex-1"
                      >
                        {selectedCampaign.url}
                      </a>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="shrink-0 h-7 w-7 p-0"
                        onClick={() => copyToClipboard(selectedCampaign.url, 'URL')}
                      >
                        <Copy className="w-3.5 h-3.5" />
                      </Button>
                    </div>
                  </div>
                </div>

                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                  <div>
                    <div className="text-xs text-[#999999]">Status</div>
                    {(() => {
                      // Use consolidated results for accurate count display
                      const consolidated = selectedCampaign.results ? getConsolidatedResults(selectedCampaign.results) : [];
                      const successCount = consolidated.filter(r => r.success).length;
                      const totalCount = consolidated.length;
                      const isFullySuccessful = totalCount > 0 && successCount === totalCount;

                      return (
                        <Badge
                          variant={
                            isFullySuccessful || selectedCampaign.status === 'completed' ? 'default' :
                            selectedCampaign.status === 'failed' ? 'destructive' : 'secondary'
                          }
                          className={isFullySuccessful || selectedCampaign.status === 'completed' ? 'bg-green-500' : ''}
                        >
                          {isFullySuccessful ? 'completed' : selectedCampaign.status}
                          {totalCount > 0 && (
                            <span className="ml-1">({successCount}/{totalCount})</span>
                          )}
                        </Badge>
                      );
                    })()}
                  </div>
                  <div>
                    <div className="text-xs text-[#999999]">Duration</div>
                    <div className="font-medium">{formatDuration(selectedCampaign.duration_minutes)}</div>
                  </div>
                  <div>
                    <div className="text-xs text-[#999999]">Created</div>
                    <div className="font-medium">
                      {selectedCampaign.created_at && formatRelativeTime(selectedCampaign.created_at)}
                    </div>
                  </div>
                  <div>
                    <div className="text-xs text-[#999999]">By</div>
                    <div className="font-medium">{selectedCampaign.created_by || 'Unknown'}</div>
                  </div>
                </div>

                {selectedCampaign.filter_tags && selectedCampaign.filter_tags.length > 0 && (
                  <div>
                    <div className="text-xs text-[#999999] mb-1">Filter Tags</div>
                    <div className="flex flex-wrap gap-1">
                      {selectedCampaign.filter_tags.map(tag => (
                        <Badge key={tag} variant="outline" className="text-xs">
                          <Tag className="w-3 h-3 mr-1" />
                          {tag}
                        </Badge>
                      ))}
                    </div>
                  </div>
                )}

                {selectedCampaign.error && (
                  <div className="bg-red-50 text-red-700 rounded p-2 text-sm">
                    <strong>Error:</strong> {selectedCampaign.error}
                  </div>
                )}
              </div>

              {/* Retry All Failed Banner */}
              {selectedCampaign.results && selectedCampaign.results.length > 0 && (() => {
                const consolidated = getConsolidatedResults(selectedCampaign.results);
                const failedCount = consolidated.filter(r => !r.success).length;
                if (failedCount === 0) return null;
                return (
                  <div className="flex items-center justify-between gap-2 p-3 bg-red-50 border border-red-200 rounded-lg">
                    <div className="flex items-center gap-2">
                      <AlertCircle className="h-4 w-4 text-red-500" />
                      <span className="text-sm text-red-700 font-medium">
                        {failedCount} failed job{failedCount > 1 ? 's' : ''}
                      </span>
                    </div>
                    <Button
                      variant="destructive"
                      size="sm"
                      onClick={handleBulkRetry}
                      disabled={isBulkRetrying}
                    >
                      {isBulkRetrying ? (
                        <>
                          <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                          Retrying...
                        </>
                      ) : (
                        <>
                          <RefreshCw className="h-3 w-3 mr-1" />
                          Retry All Failed
                        </>
                      )}
                    </Button>
                  </div>
                );
              })()}

              {/* Results by Profile - Consolidated (latest result per job_index) */}
              {selectedCampaign.results && selectedCampaign.results.length > 0 && (
                <div>
                  {(() => {
                    const consolidatedResults = getConsolidatedResults(selectedCampaign.results);
                    return (
                      <>
                        <h3 className="text-sm font-semibold mb-2 flex items-center gap-2">
                          <User className="w-4 h-4" />
                          Results by Profile ({consolidatedResults.length})
                        </h3>
                        <div className="space-y-2 max-h-64 overflow-y-auto">
                          {consolidatedResults.map((result) => (
                            <div
                              key={result.job_index}
                              className={`rounded-lg border p-3 ${
                                result.success ? 'bg-green-50 border-green-200' : 'bg-red-50 border-red-200'
                              }`}
                            >
                              <div className="flex items-start justify-between gap-2">
                                <div className="flex items-center gap-2 min-w-0">
                                  {result.success ? (
                                    <CheckCircle className="w-4 h-4 text-green-600 shrink-0" />
                                  ) : (
                                    <XCircle className="w-4 h-4 text-red-600 shrink-0" />
                                  )}
                                  <span className="font-medium text-sm truncate">
                                    {result.profile_name}
                                    {result.is_retry && (
                                      <Badge variant="secondary" className="ml-2 text-xs">
                                        Retry
                                      </Badge>
                                    )}
                                  </span>
                                </div>
                                {!result.success && (
                                  <div className="shrink-0">
                                    {retryingJobIndex === result.job_index ? (
                                      <div className="flex items-center gap-2">
                                        <Select
                                          value={retryProfile}
                                          onValueChange={setRetryProfile}
                                        >
                                          <SelectTrigger className="w-40 h-8 text-xs">
                                            <SelectValue placeholder="Select profile" />
                                          </SelectTrigger>
                                          <SelectContent>
                                            {sessions.filter(s => s.valid).map(session => (
                                              <SelectItem key={session.profile_name} value={session.profile_name}>
                                                {session.profile_name}
                                              </SelectItem>
                                            ))}
                                          </SelectContent>
                                        </Select>
                                        <Button
                                          size="sm"
                                          className="h-8 text-xs"
                                          disabled={!retryProfile || isRetrying}
                                          onClick={() => {
                                            retryJob(
                                              selectedCampaign.id,
                                              result.job_index,
                                              retryProfile,
                                              result.comment,
                                              result.profile_name
                                            );
                                          }}
                                        >
                                          {isRetrying ? (
                                            <Loader2 className="w-3 h-3 animate-spin" />
                                          ) : (
                                            'Retry'
                                          )}
                                        </Button>
                                        <Button
                                          size="sm"
                                          variant="ghost"
                                          className="h-8 w-8 p-0"
                                          onClick={() => {
                                            setRetryingJobIndex(null);
                                            setRetryProfile('');
                                          }}
                                        >
                                          <X className="w-3 h-3" />
                                        </Button>
                                      </div>
                                    ) : (
                                      <Button
                                        size="sm"
                                        variant="outline"
                                        className="h-7 text-xs"
                                        onClick={() => {
                                          setRetryingJobIndex(result.job_index);
                                          setRetryProfile(result.profile_name);
                                        }}
                                      >
                                        <RotateCw className="w-3 h-3 mr-1" />
                                        Retry
                                      </Button>
                                    )}
                                  </div>
                                )}
                              </div>
                              <div className="mt-2 text-sm text-[#666666] italic">
                                "{result.comment}"
                              </div>
                              <div className="mt-1 flex items-center gap-2 text-xs text-[#999999]">
                                {result.success && result.verified && (
                                  <span className="text-green-600">
                                    Verified via {result.method || 'vision'}
                                  </span>
                                )}
                                {!result.success && result.error && (
                                  <span className="text-red-600">{result.error}</span>
                                )}
                                {result.is_retry && result.original_profile && (
                                  <span>(retried from {result.original_profile})</span>
                                )}
                              </div>
                            </div>
                          ))}
                        </div>
                      </>
                    );
                  })()}
                </div>
              )}

              {/* All Comments Reference */}
              <div>
                <h3 className="text-sm font-semibold mb-2 flex items-center gap-2">
                  Comments ({selectedCampaign.comments.length})
                </h3>
                <div className="bg-[rgba(51,51,51,0.04)] rounded-lg p-3 max-h-40 overflow-y-auto">
                  {(() => {
                    // Use consolidated results for accurate status display
                    const consolidated = selectedCampaign.results ? getConsolidatedResults(selectedCampaign.results) : [];
                    return (
                      <ol className="space-y-1 text-sm list-decimal list-inside">
                        {selectedCampaign.comments.map((comment, idx) => {
                          const result = consolidated.find(r => r.job_index === idx);
                          return (
                            <li key={idx} className={`${result?.success ? 'text-green-700' : result ? 'text-red-700' : 'text-[#666666]'}`}>
                              <span className="ml-1">{comment}</span>
                              {result && (
                                <span className="ml-2">
                                  {result.success ? '✓' : '✗'}
                                </span>
                              )}
                            </li>
                          );
                        })}
                      </ol>
                    );
                  })()}
                </div>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* Bulk Retry Dialog - REMOVED: Now just click "Retry All Failed" and it works */}

      {/* Remote Control Modal */}
      {remoteModalOpen && remoteSession && (
        <div className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-4">
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-5xl max-h-[85vh] flex flex-col overflow-hidden">
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-2 border-b bg-white shrink-0">
              <div className="flex items-center gap-4">
                <div className="flex items-center gap-2">
                  <div className={`h-3 w-3 rounded-full ${
                    remoteConnected ? 'bg-green-500' :
                    remoteConnecting ? 'bg-yellow-500 animate-pulse' :
                    'bg-red-500'
                  }`} />
                  <span className="text-sm font-medium">
                    {remoteConnected ? 'Connected' : remoteConnecting ? 'Connecting...' : 'Disconnected'}
                  </span>
                </div>
                <div className="text-sm text-[#999999]">
                  Session: <span className="font-medium text-[#111111]">{remoteSession.profile_name}</span>
                </div>
              </div>
              <Button variant="ghost" size="sm" onClick={closeRemoteModal}>
                <X className="w-5 h-5" />
              </Button>
            </div>

            {/* URL Bar */}
            <div className="flex items-center gap-2 px-4 py-2 border-b bg-white shrink-0">
              <Globe className="w-4 h-4 text-[#999999]" />
              <Input
                value={remoteUrlInput}
                onChange={(e) => setRemoteUrlInput(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleRemoteNavigate()}
                placeholder="Enter URL..."
                className="flex-1 bg-white"
              />
              <Button onClick={handleRemoteNavigate} disabled={!remoteConnected}>
                Go
              </Button>
            </div>

            {/* Main Content */}
            <div className="flex-1 flex overflow-hidden min-h-0">
              {/* Screenshot Area */}
              <div className="flex-1 p-2 flex items-center justify-center bg-[#333333] min-h-0">
                <div
                  ref={screenshotContainerRef}
                  className="relative cursor-crosshair outline-none h-full flex items-center justify-center"
                  onClick={handleRemoteClick}
                  onWheel={handleRemoteScroll}
                  tabIndex={0}
                >
                  {remoteFrame ? (
                    <img
                      src={`data:image/jpeg;base64,${remoteFrame}`}
                      alt="Browser View"
                      className="rounded-lg shadow-lg object-contain"
                      style={{
                        maxHeight: '100%',
                        maxWidth: '100%',
                        aspectRatio: `${VIEWPORT_WIDTH}/${VIEWPORT_HEIGHT}`,
                      }}
                      draggable={false}
                    />
                  ) : (
                    <div className="flex items-center justify-center text-[#999999]" style={{ width: 250, height: 500 }}>
                      <div className="text-center">
                        <Loader2 className="w-8 h-8 animate-spin mx-auto mb-2" />
                        <p>{
                          remoteProgress === 'launching_browser' ? 'Launching browser...' :
                          remoteProgress === 'applying_stealth' ? 'Applying security...' :
                          remoteProgress === 'navigating' ? 'Loading Facebook...' :
                          remoteProgress === 'retrying' ? 'Retrying connection...' :
                          'Waiting for browser...'
                        }</p>
                      </div>
                    </div>
                  )}

                  {!remoteConnected && remoteFrame && (
                    <div className="absolute inset-0 bg-black/70 flex items-center justify-center rounded-lg">
                      <div className="text-white text-center">
                        <WifiOff className="w-12 h-12 mx-auto mb-2" />
                        <p>Disconnected</p>
                      </div>
                    </div>
                  )}
                </div>
              </div>

              {/* Right Sidebar */}
              <div className="w-64 border-l bg-white flex flex-col shrink-0">
                {/* Image Upload Section */}
                <div className="p-3 border-b">
                  <div className="text-xs font-medium mb-2">Profile Picture Upload</div>
                  <Input
                    type="file"
                    accept=".jpg,.jpeg,.png,.webp"
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) handleImageUpload(file);
                      e.target.value = '';
                    }}
                    className="text-xs"
                  />
                  {pendingUpload && (
                    <div className="mt-2 p-2 bg-blue-50 rounded text-xs">
                      <p className="text-blue-700">
                        Ready: {pendingUpload.filename} ({Math.round(pendingUpload.size / 1024)}KB)
                      </p>
                      {!uploadReady ? (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={prepareFileUpload}
                          className="mt-2 w-full text-xs"
                        >
                          Prepare for Upload
                        </Button>
                      ) : (
                        <p className="mt-2 text-green-700 font-medium">
                          Click the upload button on Facebook!
                        </p>
                      )}
                    </div>
                  )}
                </div>

                {/* Action Log */}
                <div className="flex-1 flex flex-col overflow-hidden">
                  <div className="px-4 py-3 border-b font-medium text-sm flex items-center gap-2">
                    <Mouse className="w-4 h-4" />
                    Action Log
                  </div>
                  <div className="flex-1 overflow-y-auto p-2 space-y-1">
                    {actionLog.map(entry => (
                      <div
                        key={entry.id}
                        className={`text-xs p-2 rounded ${
                          entry.status === 'success' ? 'bg-green-50 text-green-700' :
                          entry.status === 'failed' ? 'bg-red-50 text-red-700' :
                          'bg-[rgba(51,51,51,0.08)] text-[#666666]'
                        }`}
                      >
                        <div className="flex items-center justify-between">
                          <span className="font-mono">
                            {new Date(entry.timestamp).toLocaleTimeString()}
                          </span>
                          <Badge variant="outline" className="text-xs">
                            {entry.type}
                          </Badge>
                        </div>
                        <div className="mt-1 truncate">{entry.details}</div>
                      </div>
                    ))}
                    {actionLog.length === 0 && (
                      <div className="text-center text-[#999999] py-8 text-sm">
                        No actions yet. Click on the browser to interact.
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </div>

            {/* Footer */}
            <div className="flex items-center justify-between px-4 py-1 border-t bg-white text-xs text-[#999999] shrink-0">
              <div className="flex items-center gap-4">
                <span>Viewport: 393x873 (iPhone 12 Pro)</span>
                <span>|</span>
                <span className={remoteConnected ? 'text-green-600' : 'text-[#999999]'}>
                  {remoteConnected ? 'Keyboard capture: ON (click browser area first)' : 'Keyboard capture: OFF'}
                </span>
              </div>
              <div>
                Actions: {actionLog.length}
              </div>
            </div>
          </div>
        </div>
      )}

      <Toaster position="bottom-right" richColors />
    </div>
  )
}

export default App
