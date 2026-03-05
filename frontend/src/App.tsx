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
import { ProfileHealthConsole } from '@/components/analytics/ProfileHealthConsole'
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

interface AutoRetryState {
  status: 'scheduled' | 'in_progress' | 'exhausted' | 'completed';
  current_round: number;
  max_rounds: number;
  next_retry_at?: string;
  schedule_seconds: number[];
  failed_jobs: Array<{
    job_index: number;
    comment: string;
    excluded_profiles: string[];
    last_profile: string;
    exhausted: boolean;
  }>;
  completed_at?: string;
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
  auto_retry?: AutoRetryState;
}

interface QueueState {
  processor_running: boolean;
  current_campaign_id: string | null;
  pending_count: number;
  max_pending: number;
  pending: QueuedCampaign[];
  history: QueuedCampaign[];
}

interface QueueWarning {
  code: string;
  message: string;
  errors?: string[];
  duplicate_conflicts?: Array<Record<string, unknown>>;
}

interface CampaignDraft {
  id: string;
  url: string;
  comments: string[];
  jobs?: Array<Record<string, unknown>>;
  ai_metadata?: Record<string, unknown>;
  duration_minutes: number;
  filter_tags?: string[];
  enable_warmup?: boolean;
  created_at: string;
  updated_at: string;
  created_by: string;
  updated_by: string;
}

interface CampaignAIContextSnapshot {
  context_id: string;
  url: string;
  op_post?: {
    id?: string;
    text?: string;
    author_name?: string | null;
    author_id?: string | null;
    permalink_url?: string | null;
    created_time?: string | null;
    page_id?: string | null;
  };
  supporting_comments?: Array<{
    id?: string | null;
    text?: string;
    author_name?: string | null;
    author_id?: string | null;
    permalink_url?: string | null;
    created_time?: string | null;
  }>;
  source_meta?: Record<string, unknown>;
}

interface CampaignAIRulesSummary {
  version?: string;
  negative_patterns_count?: number;
  vocabulary_count?: number;
}

interface CampaignAIProduct {
  id: string;
  name: string;
  prompt: string;
  active?: boolean;
  created_at?: string;
  updated_at?: string;
  created_by?: string;
  updated_by?: string;
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
  display_name?: string;
  status: string;
  is_reserved: boolean;
  last_used_at: string | null;
  usage_count: number;
  restriction_expires_at: string | null;
  restriction_reason: string | null;
  recovery_state?: string;
  recovery_last_event?: string | null;
  recovery_last_event_at?: string | null;
  recovery_history?: Array<{
    timestamp: string;
    event: string;
    state: string;
    details?: Record<string, unknown>;
  }>;
  total_comments: number;
  success_rate: number;
  daily_stats: Record<string, { comments: number; success: number; failed: number }>;
  usage_history: Array<{
    timestamp: string;
    campaign_id: string | null;
    comment: string | null;
    success: boolean;
  }>;
  appeal_status?: string;
  appeal_attempts?: number;
  appeal_last_attempt_at?: string | null;
  appeal_last_result?: string | null;
  appeal_last_error?: string | null;
}

interface AppealStatusEntry {
  profile_name: string;
  status: string;
  appeal_status: string;
  appeal_attempts: number;
  appeal_last_attempt_at: string | null;
  appeal_last_result: string | null;
  appeal_last_error: string | null;
  restriction_reason: string | null;
  restriction_expires_at: string | null;
}

interface AnalyticsSummary {
  today: { comments: number; success: number; success_rate: number };
  week: { comments: number; success: number; success_rate: number };
  profiles: { active: number; restricted: number; total: number };
}

interface AppealSchedulerStatus {
  enabled: boolean;
  interval_hours: number;
  last_run_at: string | null;
  last_completed_at: string | null;
  next_run_at: string | null;
  busy_skipped: number;
  last_results: {
    verify_phase?: { total: number; unblocked: number; in_review: number; still_restricted: number; needs_followup: number; busy_skipped?: number; error?: string };
    appeal_phase?: { total: number; succeeded: number; failed: number; error?: string };
    per_profile?: Array<{ name: string; phase: string; status: string; action: string; error?: string; busy_reason?: string }>;
  } | null;
  run_history: Array<{
    run_at: string;
    completed_at: string;
    verify: Record<string, unknown>;
    appeal: Record<string, unknown>;
    profile_count: number;
    busy_skipped?: number;
  }>;
}

interface PremiumRun {
  id: string;
  status: string;
  run_spec?: {
    profile_name?: string;
  };
  next_execute_at?: string | null;
  error?: string | null;
  pass_matrix?: Record<string, string>;
  updated_at?: string;
  queue_position?: number;
  blocked_by_run_id?: string | null;
  admission_policy?: string;
  safety?: {
    duplicate_precheck?: { all_passed?: boolean | null };
    identity_check?: { all_passed?: boolean | null };
    submit_guard?: { all_passed?: boolean | null };
  };
}

interface PremiumStatusPayload {
  scheduler: {
    enabled?: boolean;
    is_running?: boolean;
    last_tick_at?: string | null;
    last_error?: string | null;
    last_processed_count?: number;
  };
  counts: {
    scheduled: number;
    queued: number;
    in_progress: number;
    completed: number;
    failed: number;
    cancelled: number;
  };
  recent_runs: PremiumRun[];
  rules_snapshot?: {
    version?: string;
    synced_at?: string;
  } | null;
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

function App() {
  // Auth state - must be first hook
  const { user, isAuthenticated, isLoading: authLoading, logout } = useAuth();

  const [url, setUrl] = useState('');
  const [comments, setComments] = useState('');
  const [campaignInputMode, setCampaignInputMode] = useState<'manual' | 'ai'>('manual');
  const [aiCommentCount, setAiCommentCount] = useState(10);
  const [aiContextSnapshot, setAiContextSnapshot] = useState<CampaignAIContextSnapshot | null>(null);
  const [aiContextError, setAiContextError] = useState<string | null>(null);
  const [aiRulesSummary, setAiRulesSummary] = useState<CampaignAIRulesSummary | null>(null);
  const [aiModel, setAiModel] = useState<string>('');
  const [aiComments, setAiComments] = useState<string[]>([]);
  const [aiContextLoading, setAiContextLoading] = useState(false);
  const [aiGenerating, setAiGenerating] = useState(false);
  const [aiRegeneratingAll, setAiRegeneratingAll] = useState(false);
  const [aiRegeneratingIndex, setAiRegeneratingIndex] = useState<number | null>(null);
  const [aiProducts, setAiProducts] = useState<CampaignAIProduct[]>([]);
  const [selectedAiProductId, setSelectedAiProductId] = useState<string>('');
  const [aiProductsLoading, setAiProductsLoading] = useState(false);
  const [productEditorOpen, setProductEditorOpen] = useState(false);
  const [editableProductName, setEditableProductName] = useState('');
  const [editableProductPrompt, setEditableProductPrompt] = useState('');
  const [savingAiProduct, setSavingAiProduct] = useState(false);
  const [creatingAiProduct, setCreatingAiProduct] = useState(false);
  const aiContextDebounceRef = useRef<number | null>(null);
  const aiContextAbortRef = useRef<AbortController | null>(null);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [appealStatuses, setAppealStatuses] = useState<Map<string, AppealStatusEntry>>(new Map());
  const [allTags, setAllTags] = useState<string[]>([]);
  const [sessionFilterTags, setSessionFilterTags] = useState<string[]>([]);
  const [sessionSearchQuery, setSessionSearchQuery] = useState('');
  const [sessionStatusFilters, setSessionStatusFilters] = useState<{
    valid?: boolean;
    hasProxy?: boolean;
    restricted?: boolean;
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
  const [drafts, setDrafts] = useState<CampaignDraft[]>([]);
  const [draftsLoading, setDraftsLoading] = useState(true);
  const [activeDraftId, setActiveDraftId] = useState<string | null>(null);
  const [savingDraft, setSavingDraft] = useState(false);
  const [publishingDraftId, setPublishingDraftId] = useState<string | null>(null);
  const [deletingDraftId, setDeletingDraftId] = useState<string | null>(null);
  const [draftSaveStatus, setDraftSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const draftAutosaveTimerRef = useRef<number | null>(null);
  const loadingDraftIntoFormRef = useRef(false);
  const draftSaveInFlightRef = useRef<Promise<CampaignDraft | null> | null>(null);
  const fetchQueueRef = useRef<(() => Promise<void>) | null>(null);
  const fetchDraftsRef = useRef<(() => Promise<void>) | null>(null);

  // Campaign details modal state
  const [selectedCampaign, setSelectedCampaign] = useState<QueuedCampaign | null>(null);
  const [retryingJobIndex, setRetryingJobIndex] = useState<number | null>(null);
  const [retryProfile, setRetryProfile] = useState<string>('');
  const [isRetrying, setIsRetrying] = useState(false);

  // Bulk retry state (simplified - no strategy selection needed)
  const [isBulkRetrying, setIsBulkRetrying] = useState(false);
  const [isRetryingAll, setIsRetryingAll] = useState(false);
  const [historyDisplayCount, setHistoryDisplayCount] = useState(20);
  const [sessionsPage, setSessionsPage] = useState(0);
  const SESSIONS_PER_PAGE = 50;

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
  const [schedulerStatus, setSchedulerStatus] = useState<AppealSchedulerStatus | null>(null);
  const [schedulerRunning, setSchedulerRunning] = useState(false);
  const [profileActionKey, setProfileActionKey] = useState<string | null>(null);
  const [premiumStatus, setPremiumStatus] = useState<PremiumStatusPayload | null>(null);
  const [premiumLoading, setPremiumLoading] = useState(false);

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
  const [, setRemoteUrl] = useState('');
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

              case 'drafts_state_sync':
                setDrafts(update.data?.drafts || []);
                setDraftsLoading(false);
                break;

              case 'draft_created':
                setDrafts(prev => {
                  const without = prev.filter(d => d.id !== update.data.id);
                  return [update.data, ...without];
                });
                break;

              case 'draft_updated':
                setDrafts(prev => {
                  const existingIndex = prev.findIndex(d => d.id === update.data.id);
                  if (existingIndex === -1) {
                    return [update.data, ...prev];
                  }
                  return prev.map(d => (d.id === update.data.id ? update.data : d));
                });
                break;

              case 'draft_deleted':
                setDrafts(prev => prev.filter(d => d.id !== update.data.draft_id));
                setActiveDraftId(prev => (prev === update.data.draft_id ? null : prev));
                setDraftSaveStatus(prev => (prev === 'saving' ? prev : 'idle'));
                break;

              case 'draft_published':
                setDrafts(prev => prev.filter(d => d.id !== update.data.draft_id));
                setActiveDraftId(prev => (prev === update.data.draft_id ? null : prev));
                setDraftSaveStatus(prev => (prev === 'saving' ? prev : 'idle'));
                break;

              case 'queue_campaign_added':
                // New campaign added by any user
                setQueueState(prev => {
                  const exists = prev.pending.some(c => c.id === update.data.id);
                  const pending = exists
                    ? prev.pending.map(c => (c.id === update.data.id ? { ...c, ...update.data } : c))
                    : [...prev.pending, update.data];
                  return {
                    ...prev,
                    pending_count: pending.length,
                    pending
                  };
                });
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
                // Campaign completed - move to history (includes auto_retry state)
                setQueueState(prev => {
                  const completed = prev.pending.find(c => c.id === update.data.campaign_id);
                  if (!completed) return prev;

                  const updatedCampaign: QueuedCampaign = {
                    ...completed,
                    status: 'completed',
                    success_count: update.data.success,
                    total_count: update.data.total,
                    completed_at: new Date().toISOString(),
                    auto_retry: update.data.auto_retry || undefined
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

              // Auto-retry events
              case 'auto_retry_enabled':
              case 'auto_retry_round_start':
              case 'auto_retry_round_complete':
              case 'auto_retry_complete':
                // Update auto_retry state on the campaign in history
                setQueueState(prev => ({
                  ...prev,
                  history: prev.history.map(c => {
                    if (c.id !== update.data.campaign_id) return c;
                    const ar = c.auto_retry || {} as AutoRetryState;
                    if (update.type === 'auto_retry_enabled') {
                      return { ...c, auto_retry: { ...ar, status: 'scheduled' as const, current_round: 0, max_rounds: 4, schedule_seconds: [300, 1800, 7200, 21600], failed_jobs: [], next_retry_at: update.data.first_retry_at } };
                    }
                    if (update.type === 'auto_retry_round_start') {
                      return { ...c, auto_retry: { ...ar, status: 'in_progress' as const, current_round: update.data.round } };
                    }
                    if (update.type === 'auto_retry_round_complete') {
                      return { ...c, auto_retry: { ...ar, status: 'scheduled' as const, next_retry_at: update.data.next_retry_at } };
                    }
                    if (update.type === 'auto_retry_complete') {
                      return { ...c, auto_retry: { ...ar, status: update.data.final_status as AutoRetryState['status'] } };
                    }
                    return c;
                  })
                }));
                if (update.type === 'auto_retry_enabled') {
                  toast.info(`Auto-retry scheduled for ${update.data.failed_count} failed job(s)`);
                }
                if (update.type === 'auto_retry_complete') {
                  toast.info(`Auto-retry ${update.data.final_status}`);
                }
                break;

              case 'auto_retry_job_result':
                // Add retry result to campaign history
                setQueueState(prev => ({
                  ...prev,
                  history: prev.history.map(c => {
                    if (c.id !== update.data.campaign_id) return c;
                    return { ...c, has_retries: true, last_retry_at: new Date().toISOString() };
                  })
                }));
                break;

              // Appeal scheduler events
              case 'appeal_scheduler_start':
                toast.info('Appeal scheduler started');
                void fetchSchedulerStatus();
                break;
              case 'appeal_scheduler_complete':
                toast.success('Appeal scheduler completed');
                void refreshAnalyticsHealth();
                break;

              // Premium automation events
              case 'premium_run_scheduled':
              case 'premium_run_queued':
              case 'premium_run_dequeued':
              case 'premium_run_start':
              case 'premium_step_result':
              case 'premium_verification_progress':
              case 'premium_precheck_blocked':
              case 'premium_identity_check_result':
              case 'premium_run_complete':
              case 'premium_run_failed':
              case 'premium_run_cancelled':
                if (update.type === 'premium_precheck_blocked') {
                  toast.error(`Premium precheck blocked: ${update.data.error || 'safety gate failed'}`);
                }
                if (update.type === 'premium_run_complete') {
                  toast.success(`Premium run complete: ${update.data.run_id?.slice(0, 8)}`);
                }
                if (update.type === 'premium_run_failed') {
                  toast.error(`Premium run failed: ${update.data.error || 'unknown error'}`);
                }
                fetchPremiumStatus();
                break;

              // Bulk retry-all events (parallel background task)
              case 'bulk_retry_all_start':
                setIsRetryingAll(true);
                toast.info(`Retrying ${update.data.total_campaigns} failed campaigns in parallel...`);
                break;
              case 'bulk_retry_all_campaign_complete':
                toast.success(`Campaign ${update.data.campaign_id?.slice(0, 8)}: ${update.data.jobs_succeeded} recovered, ${update.data.jobs_exhausted} exhausted`);
                fetchQueue();
                break;
              case 'bulk_retry_all_complete':
                setIsRetryingAll(false);
                if (update.data.error) {
                  toast.error(`Retry-all failed: ${update.data.error}`);
                } else {
                  toast.success(`Retry-all complete: ${update.data.campaigns_succeeded}/${update.data.campaigns_retried} campaigns succeeded, ${update.data.total_jobs_succeeded} jobs recovered`);
                  if (update.data.total_jobs_exhausted > 0) {
                    toast.warning(`${update.data.total_jobs_exhausted} jobs ran out of eligible profiles`);
                  }
                }
                fetchQueue();
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

  const normalizeErrorMessage = (value: unknown, fallback: string): string => {
    if (typeof value === 'string' && value.trim()) return value;
    if (Array.isArray(value)) {
      const joined = value.map(v => normalizeErrorMessage(v, '')).filter(Boolean).join('; ');
      return joined || fallback;
    }

    if (value && typeof value === 'object') {
      const asRecord = value as Record<string, unknown>;
      if (typeof asRecord.message === 'string' && asRecord.message.trim()) return asRecord.message;
      if (typeof asRecord.detail === 'string' && asRecord.detail.trim()) return asRecord.detail;
      if (Array.isArray(asRecord.errors) && asRecord.errors.length > 0) {
        const joined = asRecord.errors.map(e => String(e)).join('; ');
        if (joined.trim()) return joined;
      }
      try {
        return JSON.stringify(value);
      } catch {
        return fallback;
      }
    }

    if (value instanceof Error && value.message) return value.message;
    return fallback;
  };

  const parseApiError = async (res: Response, fallback: string): Promise<string> => {
    try {
      const body = await res.json();
      return normalizeErrorMessage(body?.detail ?? body, fallback);
    } catch {
      return fallback;
    }
  };

  const parseCommentsInput = (raw: string): string[] => raw
    .split('\n')
    .map(c => c.trim())
    .filter(Boolean);

  const fetchWithTimeout = async (resource: string, init: RequestInit, timeoutMs: number): Promise<Response> => {
    const controller = new AbortController();
    const externalSignal = init.signal;
    const onAbort = () => controller.abort();
    if (externalSignal) {
      if (externalSignal.aborted) {
        controller.abort();
      } else {
        externalSignal.addEventListener('abort', onAbort, { once: true });
      }
    }
    const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
      return await fetch(resource, { ...init, signal: controller.signal });
    } catch (error) {
      if ((error as { name?: string })?.name === 'AbortError') {
        if (externalSignal?.aborted) {
          throw error;
        }
        throw new Error(`Request timed out after ${Math.ceil(timeoutMs / 1000)}s`);
      }
      throw error;
    } finally {
      if (externalSignal) {
        externalSignal.removeEventListener('abort', onAbort);
      }
      window.clearTimeout(timeoutId);
    }
  };

  const syncAiCommentsToComposer = (nextComments: string[]) => {
    const normalized = nextComments.map(c => c.trim());
    setAiComments(normalized);
    setComments(normalized.join('\n'));
  };

  const upsertDraftLocally = useCallback((draft: CampaignDraft) => {
    setDrafts(prev => {
      const without = prev.filter(existing => existing.id !== draft.id);
      return [draft, ...without];
    });
    setDraftsLoading(false);
  }, []);

  const removeDraftLocally = useCallback((draftId: string) => {
    setDrafts(prev => prev.filter(existing => existing.id !== draftId));
    setDraftsLoading(false);
  }, []);

  const upsertPendingCampaignLocally = useCallback((campaignLike: unknown) => {
    if (!campaignLike || typeof campaignLike !== 'object') return;
    const campaign = campaignLike as QueuedCampaign;
    const campaignId = String(campaign.id || '').trim();
    if (!campaignId) return;

    setQueueState(prev => {
      const exists = prev.pending.some(item => item.id === campaignId);
      const pending = exists
        ? prev.pending.map(item => (item.id === campaignId ? { ...item, ...campaign } : item))
        : [...prev.pending, campaign];

      return {
        ...prev,
        pending,
        pending_count: pending.length
      };
    });
    setQueueLoading(false);
  }, []);

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

  const fetchAppealStatuses = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/appeals/status`, { headers: getAuthHeaders() });
      if (!res.ok) return;
      const data = await res.json();
      const map = new Map<string, AppealStatusEntry>();
      for (const p of data.profiles || []) {
        map.set(p.profile_name, p);
      }
      setAppealStatuses(map);
    } catch {
      // Silently fail - appeal status is supplementary
    }
  }, []);

  const fetchSchedulerStatus = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/appeals/scheduler/status`, { headers: getAuthHeaders() });
      if (!res.ok) return;
      const data = await res.json();
      setSchedulerStatus(data);
    } catch {
      // Silently fail
    }
  }, []);

  const fetchPremiumStatus = async () => {
    try {
      setPremiumLoading(true);
      const res = await fetch(`${API_BASE}/premium/status`, { headers: getAuthHeaders() });
      if (!res.ok) return;
      const data = await res.json();
      setPremiumStatus(data);
    } catch {
      // Silently fail
    } finally {
      setPremiumLoading(false);
    }
  };

  const handleSchedulerRunNow = async () => {
    setSchedulerRunning(true);
    try {
      const res = await fetch(`${API_BASE}/appeals/scheduler/run-now`, {
        method: 'POST',
        headers: getAuthHeaders()
      });
      const data = await res.json();
      if (data.status === 'busy') {
        toast.warning('Appeal batch already running');
      } else {
        toast.success('Appeal scheduler run completed');
        await Promise.all([fetchSchedulerStatus(), fetchProfileAnalytics(), fetchAppealStatuses()]);
      }
    } catch {
      toast.error('Failed to run appeal scheduler');
    } finally {
      setSchedulerRunning(false);
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

  const fetchDrafts = async () => {
    try {
      setDraftsLoading(true);
      const res = await fetch(`${API_BASE}/drafts`, { headers: getAuthHeaders() });
      if (!res.ok) throw new Error(await parseApiError(res, 'Failed to fetch drafts'));
      const data = await res.json();
      setDrafts(data.drafts || []);
    } catch (error) {
      console.error('Failed to fetch drafts:', error);
    } finally {
      setDraftsLoading(false);
    }
  };

  useEffect(() => {
    fetchQueueRef.current = fetchQueue;
    fetchDraftsRef.current = fetchDrafts;
  });

  const fetchGeminiObservations = useCallback(async () => {
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
  }, []);

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
    } catch {
      toast.error('Failed to clear observations');
    }
  };

  const fetchProfileAnalytics = useCallback(async () => {
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
  }, []);

  const refreshAnalyticsHealth = useCallback(async () => {
    await Promise.all([fetchProfileAnalytics(), fetchSchedulerStatus(), fetchAppealStatuses()]);
  }, [fetchAppealStatuses, fetchProfileAnalytics, fetchSchedulerStatus]);

  const isProfileActionRunning = useCallback(
    (profileName: string, action: 'verify' | 'appeal' | 'unblock' | 'restrict') =>
      profileActionKey === `${profileName}:${action}`,
    [profileActionKey],
  );

  const unblockProfile = useCallback(async (profileName: string) => {
    const actionKey = `${profileName}:unblock`;
    setProfileActionKey(actionKey);
    try {
      const res = await fetch(`${API_BASE}/analytics/profiles/${encodeURIComponent(profileName)}/unblock`, {
        method: 'POST',
        headers: getAuthHeaders()
      });
      if (!res.ok) {
        toast.error(await parseApiError(res, 'Failed to unblock profile'));
        return;
      }
      toast.success(`Unblocked ${profileName}`);
      await refreshAnalyticsHealth();
    } catch (error) {
      toast.error(normalizeErrorMessage(error, 'Failed to unblock profile'));
    } finally {
      setProfileActionKey(prev => prev === actionKey ? null : prev);
    }
  }, [parseApiError, refreshAnalyticsHealth]);

  const restrictProfile = useCallback(async (profileName: string, hours: number = 24) => {
    const actionKey = `${profileName}:restrict`;
    setProfileActionKey(actionKey);
    try {
      const res = await fetch(`${API_BASE}/analytics/profiles/${encodeURIComponent(profileName)}/restrict?hours=${hours}&reason=manual`, {
        method: 'POST',
        headers: getAuthHeaders()
      });
      if (!res.ok) {
        toast.error(await parseApiError(res, 'Failed to restrict profile'));
        return;
      }
      toast.success(`Restricted ${profileName} for ${hours}h`);
      await refreshAnalyticsHealth();
    } catch (error) {
      toast.error(normalizeErrorMessage(error, 'Failed to restrict profile'));
    } finally {
      setProfileActionKey(prev => prev === actionKey ? null : prev);
    }
  }, [parseApiError, refreshAnalyticsHealth]);

  const verifyRestrictedProfile = useCallback(async (profileName: string) => {
    const actionKey = `${profileName}:verify`;
    setProfileActionKey(actionKey);
    try {
      const res = await fetch(`${API_BASE}/appeals/verify`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({ profile_name: profileName }),
      });
      if (!res.ok) {
        const message = await parseApiError(res, 'Failed to verify restriction status');
        if (res.status === 409) {
          toast.warning(message);
        } else {
          toast.error(message);
        }
        return;
      }
      const data = await res.json();
      if (data.action_taken === 'auto_unblocked') {
        toast.success(`${profileName} is usable again`);
      } else if (data.action_taken === 'marked_in_review') {
        toast.success(`${profileName} already has an appeal in review`);
      } else if (data.action_taken === 'confirmed_restricted') {
        toast.warning(`${profileName} is still restricted`);
      } else {
        toast.warning(`${profileName} needs follow-up`);
      }
      await refreshAnalyticsHealth();
    } catch (error) {
      toast.error(normalizeErrorMessage(error, 'Failed to verify restriction status'));
    } finally {
      setProfileActionKey(prev => prev === actionKey ? null : prev);
    }
  }, [parseApiError, refreshAnalyticsHealth]);

  const appealRestrictedProfile = useCallback(async (profileName: string) => {
    const actionKey = `${profileName}:appeal`;
    setProfileActionKey(actionKey);
    try {
      const res = await fetch(`${API_BASE}/appeals/single`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({ profile_name: profileName }),
      });
      if (!res.ok) {
        const message = await parseApiError(res, 'Failed to submit appeal');
        if (res.status === 409) {
          toast.warning(message);
        } else {
          toast.error(message);
        }
        return;
      }
      const data = await res.json();
      if (data.success) {
        toast.success(data.final_status === 'auto_unblocked'
          ? `${profileName} is usable again`
          : `Appeal submitted for ${profileName}`);
      } else if (data.scenario === 'checkpoint') {
        toast.warning(`${profileName} needs manual checkpoint resolution`);
      } else {
        toast.warning(data.error || `${profileName} still needs follow-up`);
      }
      await refreshAnalyticsHealth();
    } catch (error) {
      toast.error(normalizeErrorMessage(error, 'Failed to submit appeal'));
    } finally {
      setProfileActionKey(prev => prev === actionKey ? null : prev);
    }
  }, [parseApiError, refreshAnalyticsHealth]);

  // Tier 1: Critical path - load immediately for Campaign tab
  useEffect(() => {
    void fetchSessions();
    void fetchTags();
    void fetchAppealStatuses();
    void fetchQueue();
    void fetchDrafts();
    void fetchAiProducts({ silent: true });
    void fetchPremiumStatus();
  }, [fetchAppealStatuses]);

  // Resilience path: if websocket is down, keep queue/drafts fresh without manual refresh.
  useEffect(() => {
    if (liveStatus.connected) return;
    const interval = window.setInterval(() => {
      void fetchQueueRef.current?.();
      void fetchDraftsRef.current?.();
    }, 6000);
    return () => window.clearInterval(interval);
  }, [liveStatus.connected]);

  useEffect(() => {
    if (campaignInputMode !== 'ai') return;
    if (aiProducts.length > 0) return;
    fetchAiProducts({ silent: true });
  }, [campaignInputMode, aiProducts.length]);

  // Tier 2: Background loading - load after critical data, during idle time
  useEffect(() => {
    if (!sessionsLoading) {
      // Use requestIdleCallback to load during browser idle time
      const scheduleIdle = window.requestIdleCallback || ((cb: IdleRequestCallback) => setTimeout(cb, 100));
      scheduleIdle(() => fetchCredentials());
      scheduleIdle(() => fetchProxies());
    }
  }, [sessionsLoading]);

  useEffect(() => {
    if (!activeDraftId) {
      if (draftAutosaveTimerRef.current !== null) {
        window.clearTimeout(draftAutosaveTimerRef.current);
        draftAutosaveTimerRef.current = null;
      }
      return;
    }
    if (loadingDraftIntoFormRef.current) return;

    if (draftAutosaveTimerRef.current !== null) {
      window.clearTimeout(draftAutosaveTimerRef.current);
    }

    setDraftSaveStatus('saving');
    draftAutosaveTimerRef.current = window.setTimeout(async () => {
      const saved = await saveDraftFromComposer({ silent: true, forceDraftId: activeDraftId });
      if (!saved) {
        setDraftSaveStatus('error');
      }
      draftAutosaveTimerRef.current = null;
    }, 800);

    return () => {
      if (draftAutosaveTimerRef.current !== null) {
        window.clearTimeout(draftAutosaveTimerRef.current);
        draftAutosaveTimerRef.current = null;
      }
    };
  }, [activeDraftId, url, comments, campaignDuration, campaignFilterTags]);

  useEffect(() => {
    return () => {
      if (draftAutosaveTimerRef.current !== null) {
        window.clearTimeout(draftAutosaveTimerRef.current);
      }
      if (aiContextDebounceRef.current !== null) {
        window.clearTimeout(aiContextDebounceRef.current);
      }
      if (aiContextAbortRef.current) {
        aiContextAbortRef.current.abort();
      }
    };
  }, []);

  useEffect(() => {
    const selected = aiProducts.find((item) => item.id === selectedAiProductId);
    if (!selected) return;
    setEditableProductName(selected.name || '');
    setEditableProductPrompt(selected.prompt || '');
  }, [selectedAiProductId, aiProducts]);

  useEffect(() => {
    if (campaignInputMode !== 'ai') return;
    const trimmedUrl = url.trim();
    if (!trimmedUrl) {
      setAiContextSnapshot(null);
      setAiContextError(null);
      return;
    }
    if (aiContextSnapshot?.url === trimmedUrl) {
      return;
    }
    if (aiContextDebounceRef.current !== null) {
      window.clearTimeout(aiContextDebounceRef.current);
    }
    aiContextDebounceRef.current = window.setTimeout(() => {
      void fetchAiCampaignContext({ urlOverride: trimmedUrl, silent: true });
      aiContextDebounceRef.current = null;
    }, 700);

    return () => {
      if (aiContextDebounceRef.current !== null) {
        window.clearTimeout(aiContextDebounceRef.current);
        aiContextDebounceRef.current = null;
      }
    };
  }, [campaignInputMode, url]);

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

    // Filter by restricted status
    if (sessionStatusFilters.restricted) {
      result = result.filter(s => {
        const appeal = appealStatuses.get(s.profile_name);
        return appeal && (appeal.status === 'restricted' || appeal.appeal_status !== 'none');
      });
    }

    // Filter by tags (AND logic)
    if (sessionFilterTags.length > 0) {
      result = result.filter(s =>
        sessionFilterTags.every(tag => (s.tags || []).includes(tag))
      );
    }

    return result;
  }, [sessions, sessionSearchQuery, sessionStatusFilters, sessionFilterTags, appealStatuses]);

  const buildComposerPayload = () => ({
    url: url.trim(),
    comments: parseCommentsInput(comments),
    duration_minutes: campaignInputMode === 'ai' ? 30 : campaignDuration,
    filter_tags: campaignFilterTags.length > 0 ? campaignFilterTags : null,
    enable_warmup: enableWarmup
  });

  const showQueueWarnings = (warnings?: QueueWarning[]) => {
    if (!warnings || warnings.length === 0) return;
    warnings.forEach((warning) => {
      const conflictCount = warning.duplicate_conflicts?.length || 0;
      const baseMessage = warning.message || warning.code || 'Campaign warning';
      toast.warning(conflictCount > 0 ? `${baseMessage} (${conflictCount} conflict${conflictCount === 1 ? '' : 's'})` : baseMessage);
    });
  };

  const fetchAiProducts = async (options: { silent?: boolean; preferProductId?: string } = {}) => {
    const { silent = false, preferProductId } = options;
    setAiProductsLoading(true);
    try {
      const res = await fetch(`${API_BASE}/campaign-ai/products`, {
        headers: getAuthHeaders(),
      });
      if (!res.ok) {
        throw new Error(await parseApiError(res, 'Failed to fetch AI products'));
      }
      const data = await res.json();
      const products = Array.isArray(data.products) ? data.products as CampaignAIProduct[] : [];
      setAiProducts(products);

      const availableIds = new Set(products.map((p) => String(p.id)));
      const requested = String(preferProductId || '').trim();
      const current = String(selectedAiProductId || '').trim();
      const lastUsed = String(data.last_product_id || '').trim();
      const fallback = products[0]?.id || '';
      const nextSelected =
        (requested && availableIds.has(requested) && requested) ||
        (current && availableIds.has(current) && current) ||
        (lastUsed && availableIds.has(lastUsed) && lastUsed) ||
        fallback ||
        '';
      setSelectedAiProductId(nextSelected);
    } catch (error) {
      if (!silent) {
        toast.error(normalizeErrorMessage(error, 'Failed to fetch AI products'));
      }
    } finally {
      setAiProductsLoading(false);
    }
  };

  const saveSelectedAiProduct = async () => {
    const productId = selectedAiProductId.trim();
    if (!productId) {
      toast.error('Select a product first');
      return;
    }
    if (!editableProductName.trim() || !editableProductPrompt.trim()) {
      toast.error('Product name and prompt are required');
      return;
    }
    setSavingAiProduct(true);
    try {
      const res = await fetch(`${API_BASE}/campaign-ai/products/${productId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({
          name: editableProductName.trim(),
          prompt: editableProductPrompt.trim(),
        }),
      });
      if (!res.ok) {
        throw new Error(await parseApiError(res, 'Failed to save product'));
      }
      await fetchAiProducts({ silent: true, preferProductId: productId });
      toast.success('Product saved');
    } catch (error) {
      toast.error(normalizeErrorMessage(error, 'Failed to save product'));
    } finally {
      setSavingAiProduct(false);
    }
  };

  const createAiProduct = async () => {
    if (!editableProductName.trim() || !editableProductPrompt.trim()) {
      toast.error('Product name and prompt are required');
      return;
    }
    setCreatingAiProduct(true);
    try {
      const res = await fetch(`${API_BASE}/campaign-ai/products`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({
          name: editableProductName.trim(),
          prompt: editableProductPrompt.trim(),
        }),
      });
      if (!res.ok) {
        throw new Error(await parseApiError(res, 'Failed to create product'));
      }
      const data = await res.json();
      const createdId = String(data?.product?.id || '').trim();
      await fetchAiProducts({ silent: true, preferProductId: createdId });
      setProductEditorOpen(false);
      toast.success('Product created');
    } catch (error) {
      toast.error(normalizeErrorMessage(error, 'Failed to create product'));
    } finally {
      setCreatingAiProduct(false);
    }
  };

  const fetchAiCampaignContext = async (options: { urlOverride?: string; silent?: boolean } = {}) => {
    const trimmedUrl = (options.urlOverride ?? url).trim();
    const silent = Boolean(options.silent);
    if (!trimmedUrl) {
      if (!silent) toast.error('Please enter a URL first');
      setAiContextSnapshot(null);
      setAiContextError(null);
      return;
    }
    if (!trimmedUrl.startsWith('http://') && !trimmedUrl.startsWith('https://')) {
      return;
    }
    if (aiContextSnapshot?.url === trimmedUrl) {
      return;
    }

    if (aiContextAbortRef.current) {
      aiContextAbortRef.current.abort();
    }
    const controller = new AbortController();
    aiContextAbortRef.current = controller;
    setAiContextLoading(true);
    setAiContextError(null);
    try {
      const res = await fetchWithTimeout(`${API_BASE}/campaign-ai/context`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({ url: trimmedUrl }),
        signal: controller.signal,
      }, 45000);
      if (!res.ok) {
        throw new Error(await parseApiError(res, 'Failed to fetch AI context'));
      }
      const data = await res.json();
      setAiContextSnapshot(data);
      setAiContextError(null);
      if (!silent) {
        toast.success('AI context fetched');
      }
    } catch (error) {
      if ((error as { name?: string })?.name === 'AbortError') {
        return;
      }
      const message = normalizeErrorMessage(error, 'Failed to fetch AI context');
      setAiContextSnapshot(null);
      setAiContextError(message);
      if (!silent) {
        toast.error(message);
      }
    } finally {
      if (aiContextAbortRef.current === controller) {
        aiContextAbortRef.current = null;
      }
      setAiContextLoading(false);
    }
  };

  const generateAiComments = async () => {
    if (!url.trim()) {
      toast.error('Please enter a URL first');
      return;
    }
    if (!aiContextSnapshot) {
      toast.error('Fetch context before generating comments');
      return;
    }
    if (aiContextSnapshot.url && aiContextSnapshot.url !== url.trim()) {
      toast.error('URL changed after context fetch. Fetch context again.');
      return;
    }
    if (!selectedAiProductId.trim()) {
      toast.error('Select a product first');
      return;
    }

    const nextCount = Math.max(10, Math.min(50, Number(aiCommentCount) || 10));
    setAiCommentCount(nextCount);
    setAiGenerating(true);
    const generateToastId = toast.loading('Generating comments...');
    try {
      const res = await fetchWithTimeout(`${API_BASE}/campaign-ai/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({
          url: url.trim(),
          product_id: selectedAiProductId,
          comment_count: nextCount,
          filter_tags: campaignFilterTags.length > 0 ? campaignFilterTags : null,
          enable_warmup: enableWarmup,
          draft_id: activeDraftId || null,
        }),
      }, 120000);
      if (!res.ok) {
        throw new Error(await parseApiError(res, 'Failed to generate comments'));
      }
      const data = await res.json();
      const generated = Array.isArray(data.comments) ? data.comments.map((c: unknown) => String(c)) : [];
      syncAiCommentsToComposer(generated);
      if (data.context_snapshot) setAiContextSnapshot(data.context_snapshot);
      if (data.rules_summary) setAiRulesSummary(data.rules_summary);
      if (typeof data.model === 'string') setAiModel(data.model);
      if (typeof data?.product?.id === 'string' && data.product.id) {
        setSelectedAiProductId(data.product.id);
      }
      if (typeof data.draft_id === 'string' && data.draft_id) {
        setActiveDraftId(data.draft_id);
        setDraftSaveStatus('saved');
      }
      if (data?.draft && typeof data.draft === 'object') {
        upsertDraftLocally(data.draft as CampaignDraft);
      }
      toast.success(`Generated ${generated.length} comments`, { id: generateToastId });
    } catch (error) {
      toast.error(normalizeErrorMessage(error, 'Failed to generate comments'), { id: generateToastId });
    } finally {
      setAiGenerating(false);
    }
  };

  const regenerateSingleAiComment = async (index: number) => {
    if (!activeDraftId) {
      toast.error('Generate comments first to create a draft');
      return;
    }
    setAiRegeneratingIndex(index);
    try {
      const res = await fetch(`${API_BASE}/campaign-ai/drafts/${activeDraftId}/regenerate-one`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({ index }),
      });
      if (!res.ok) {
        throw new Error(await parseApiError(res, 'Failed to regenerate comment'));
      }
      const data = await res.json();
      const regenerated = Array.isArray(data.comments) ? data.comments.map((c: unknown) => String(c)) : [];
      syncAiCommentsToComposer(regenerated);
      if (data.rules_summary) setAiRulesSummary(data.rules_summary);
      if (typeof data.model === 'string') setAiModel(data.model);
      if (typeof data.draft_id === 'string' && data.draft_id) {
        setActiveDraftId(data.draft_id);
      }
      if (data?.draft && typeof data.draft === 'object') {
        upsertDraftLocally(data.draft as CampaignDraft);
      }
      setDraftSaveStatus('saved');
      toast.success('Comment regenerated');
    } catch (error) {
      toast.error(normalizeErrorMessage(error, 'Failed to regenerate comment'));
    } finally {
      setAiRegeneratingIndex(null);
    }
  };

  const regenerateAllAiComments = async () => {
    if (!activeDraftId) {
      toast.error('Generate comments first to create a draft');
      return;
    }
    setAiRegeneratingAll(true);
    try {
      const res = await fetch(`${API_BASE}/campaign-ai/drafts/${activeDraftId}/regenerate-all`, {
        method: 'POST',
        headers: { ...getAuthHeaders() },
      });
      if (!res.ok) {
        throw new Error(await parseApiError(res, 'Failed to regenerate all comments'));
      }
      const data = await res.json();
      const regenerated = Array.isArray(data.comments) ? data.comments.map((c: unknown) => String(c)) : [];
      syncAiCommentsToComposer(regenerated);
      if (data.rules_summary) setAiRulesSummary(data.rules_summary);
      if (typeof data.model === 'string') setAiModel(data.model);
      if (typeof data.draft_id === 'string' && data.draft_id) {
        setActiveDraftId(data.draft_id);
      }
      if (data?.draft && typeof data.draft === 'object') {
        upsertDraftLocally(data.draft as CampaignDraft);
      }
      setDraftSaveStatus('saved');
      toast.success(`Regenerated ${regenerated.length} comments`);
    } catch (error) {
      toast.error(normalizeErrorMessage(error, 'Failed to regenerate all comments'));
    } finally {
      setAiRegeneratingAll(false);
    }
  };

  const updateAiCommentText = (index: number, value: string) => {
    const next = [...aiComments];
    next[index] = value;
    syncAiCommentsToComposer(next);
  };

  const saveDraftFromComposer = async (
    options: { silent?: boolean; forceDraftId?: string | null } = {}
  ): Promise<CampaignDraft | null> => {
    if (draftSaveInFlightRef.current) {
      return draftSaveInFlightRef.current;
    }

    const { silent = false, forceDraftId } = options;
    const payload = buildComposerPayload();
    const targetDraftId = forceDraftId ?? activeDraftId;
    const endpoint = targetDraftId ? `${API_BASE}/drafts/${targetDraftId}` : `${API_BASE}/drafts`;
    const method = targetDraftId ? 'PUT' : 'POST';
    const toastId = silent ? null : toast.loading(targetDraftId ? 'Saving draft...' : 'Creating draft...');

    const requestPromise: Promise<CampaignDraft | null> = (async () => {
      setSavingDraft(true);
      try {
        const res = await fetchWithTimeout(endpoint, {
          method,
          headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
          body: JSON.stringify(payload)
        }, 30000);
        if (!res.ok) {
          throw new Error(await parseApiError(res, 'Failed to save draft'));
        }
        const draft = await res.json();
        setActiveDraftId(draft.id);
        setDraftSaveStatus('saved');
        upsertDraftLocally(draft as CampaignDraft);
        if (toastId) {
          toast.success(targetDraftId ? 'Draft updated' : 'Draft saved', { id: toastId });
        }
        return draft as CampaignDraft;
      } catch (error) {
        setDraftSaveStatus('error');
        if (toastId) {
          toast.error(normalizeErrorMessage(error, 'Failed to save draft'), { id: toastId });
        }
        return null;
      } finally {
        setSavingDraft(false);
      }
    })();

    draftSaveInFlightRef.current = requestPromise;
    try {
      return await requestPromise;
    } finally {
      if (draftSaveInFlightRef.current === requestPromise) {
        draftSaveInFlightRef.current = null;
      }
    }
  };

  const saveDraftNow = async () => {
    const payload = buildComposerPayload();
    if (!payload.url && payload.comments.length === 0) {
      toast.error('Enter a URL or comments before saving draft');
      return;
    }
    await saveDraftFromComposer({ silent: false });
  };

  const publishDraftCampaign = async (draftId: string, clearComposer: boolean = false): Promise<boolean> => {
    if (queueState.pending_count >= queueState.max_pending) {
      toast.error(`Queue is full (${queueState.pending_count}/${queueState.max_pending}). Wait for campaigns to complete.`);
      return false;
    }

    setPublishingDraftId(draftId);
    const publishToastId = toast.loading('Publishing draft...');
    try {
      if (activeDraftId === draftId) {
        if (draftAutosaveTimerRef.current !== null) {
          window.clearTimeout(draftAutosaveTimerRef.current);
          draftAutosaveTimerRef.current = null;
        }
        const synced = await saveDraftFromComposer({ silent: true, forceDraftId: draftId });
        if (!synced) {
          throw new Error('Failed to sync draft before publish');
        }
      }

      const res = await fetchWithTimeout(`${API_BASE}/drafts/${draftId}/publish`, {
        method: 'POST',
        headers: getAuthHeaders()
      }, 45000);
      if (!res.ok) {
        throw new Error(await parseApiError(res, 'Failed to publish draft'));
      }

      const data = await res.json();
      showQueueWarnings(data.warnings);
      if (data?.campaign) {
        upsertPendingCampaignLocally(data.campaign);
      }
      removeDraftLocally(draftId);
      toast.success('Draft published to queue', { id: publishToastId });

      if (clearComposer) {
        setActiveDraftId(null);
        setDraftSaveStatus('idle');
        setUrl('');
        setComments('');
      }

      return true;
    } catch (error) {
      toast.error(normalizeErrorMessage(error, 'Failed to publish draft'), { id: publishToastId });
      return false;
    } finally {
      setPublishingDraftId(null);
    }
  };

  const publishNow = async () => {
    const payload = buildComposerPayload();
    if (!payload.url || payload.comments.length === 0) {
      toast.error('Please enter a URL and comments');
      return;
    }

    if (queueState.pending_count >= queueState.max_pending) {
      toast.error(`Queue is full (${queueState.pending_count}/${queueState.max_pending}). Wait for campaigns to complete.`);
      return;
    }

    setAddingToQueue(true);
    try {
      if (activeDraftId) {
        if (draftAutosaveTimerRef.current !== null) {
          window.clearTimeout(draftAutosaveTimerRef.current);
          draftAutosaveTimerRef.current = null;
        }
        const saved = await saveDraftFromComposer({ silent: true, forceDraftId: activeDraftId });
        if (!saved) {
          throw new Error('Failed to sync draft before publish');
        }
        const published = await publishDraftCampaign(activeDraftId, true);
        if (!published) return;
      } else {
        const publishToastId = toast.loading('Publishing campaign...');
        const res = await fetchWithTimeout(`${API_BASE}/queue`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
          body: JSON.stringify(payload)
        }, 45000);
        try {
          if (!res.ok) {
            throw new Error(await parseApiError(res, 'Failed to add campaign to queue'));
          }

          const data = await res.json();
          showQueueWarnings(data.warnings);
          upsertPendingCampaignLocally(data);
          setUrl('');
          setComments('');
          toast.success(
            `Added campaign with ${payload.comments.length} comments to queue${campaignFilterTags.length > 0 ? ` (filtered by: ${campaignFilterTags.join(', ')})` : ''}`,
            { id: publishToastId }
          );
        } catch (error) {
          toast.error(normalizeErrorMessage(error, 'Failed to publish campaign'), { id: publishToastId });
          throw error;
        }
      }
    } catch (error) {
      if (activeDraftId) {
        toast.error(normalizeErrorMessage(error, 'Failed to publish campaign'));
      }
    } finally {
      setAddingToQueue(false);
    }
  };

  const openDraftInComposer = (draft: CampaignDraft) => {
    loadingDraftIntoFormRef.current = true;
    setActiveDraftId(draft.id);
    setUrl(draft.url || '');

    const lines = draft.comments && draft.comments.length > 0
      ? draft.comments
      : Array.isArray(draft.jobs)
        ? draft.jobs
            .map((job) => String((job as Record<string, unknown>).text || '').trim())
            .filter(Boolean)
        : [];
    setComments(lines.join('\n'));
    setAiComments(lines);
    setCampaignDuration(Math.max(10, Math.min(1440, Number(draft.duration_minutes) || 30)));
    setCampaignFilterTags(Array.isArray(draft.filter_tags) ? draft.filter_tags : []);
    setAiCommentCount(Math.max(10, Math.min(50, lines.length || 10)));

    const aiMeta = draft.ai_metadata && typeof draft.ai_metadata === 'object'
      ? draft.ai_metadata as Record<string, unknown>
      : null;
    if (aiMeta) {
      setCampaignInputMode('ai');
      const productId = String(aiMeta.product_id || '').trim();
      if (productId) {
        setSelectedAiProductId(productId);
        void fetchAiProducts({ silent: true, preferProductId: productId });
      }
      const contextSnapshot = aiMeta.context_snapshot;
      if (contextSnapshot && typeof contextSnapshot === 'object') {
        setAiContextSnapshot(contextSnapshot as CampaignAIContextSnapshot);
      }
      setAiModel(String(aiMeta.model || ''));
      const rulesVersion = String(aiMeta.rules_snapshot_version || '');
      setAiRulesSummary(rulesVersion ? { version: rulesVersion } : null);
    } else {
      setCampaignInputMode('manual');
      setAiContextSnapshot(null);
      setAiContextError(null);
      setAiRulesSummary(null);
      setAiModel('');
    }
    setDraftSaveStatus('saved');

    window.setTimeout(() => {
      loadingDraftIntoFormRef.current = false;
    }, 0);
  };

  const deleteDraftItem = async (draftId: string) => {
    if (!confirm('Delete this shared draft?')) return;

    setDeletingDraftId(draftId);
    try {
      const res = await fetch(`${API_BASE}/drafts/${draftId}`, {
        method: 'DELETE',
        headers: getAuthHeaders()
      });
      if (!res.ok) {
        throw new Error(await parseApiError(res, 'Failed to delete draft'));
      }
      removeDraftLocally(draftId);
      if (activeDraftId === draftId) {
        setActiveDraftId(null);
        setDraftSaveStatus('idle');
      }
      toast.success('Draft deleted');
    } catch (error) {
      toast.error(normalizeErrorMessage(error, 'Failed to delete draft'));
    } finally {
      setDeletingDraftId(null);
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
        throw new Error(await parseApiError(res, 'Failed to remove from queue'));
      }

      toast.success('Campaign removed from queue');
    } catch (error: unknown) {
      toast.error(normalizeErrorMessage(error, 'Failed to remove from queue'));
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
        throw new Error(await parseApiError(res, 'Failed to cancel campaign'));
      }

      toast.success('Campaign cancelled');
    } catch (error: unknown) {
      toast.error(normalizeErrorMessage(error, 'Failed to cancel campaign'));
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
        throw new Error(await parseApiError(res, 'Failed to retry job'));
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
      toast.error(normalizeErrorMessage(error, 'Failed to retry job'));
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
        toast.error(normalizeErrorMessage(data, 'Bulk retry failed'));
      }
    } catch (error) {
      toast.error(normalizeErrorMessage(error, 'Bulk retry failed'));
    } finally {
      setIsBulkRetrying(false);
    }
  };

  // Retry ALL failed campaigns at once
  const handleRetryAllFailed = async () => {
    setIsRetryingAll(true);
    toast.info('Checking proxy health and launching parallel retry...');

    try {
      const res = await fetch(`${API_BASE}/queue/retry-all-failed?hours_back=72`, {
        method: 'POST',
        headers: { ...getAuthHeaders() }
      });

      const data = await res.json();

      if (res.status === 503) {
        toast.error(`Proxy is down: ${data.detail}`);
        setIsRetryingAll(false);
        return;
      }

      if (data.task_started) {
        toast.success(`Launched: retrying ${data.campaigns_found} campaigns (${data.parallel_limit} parallel). Progress via websocket.`);
        // isRetryingAll stays true until bulk_retry_all_complete websocket event
      } else if (data.campaigns_found === 0) {
        toast.info('No failed campaigns found');
        setIsRetryingAll(false);
      } else if (data.progress) {
        // Already running
        toast.info(`Retry-all already running: ${data.progress.campaigns_completed}/${data.progress.campaigns_total} done`);
      } else {
        toast.error(data.detail || data.message || 'Retry all failed');
        setIsRetryingAll(false);
      }
    } catch (error) {
      toast.error(normalizeErrorMessage(error, 'Retry all failed'));
      setIsRetryingAll(false);
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

  const schedulerIsStale = useMemo(() => {
    if (!schedulerStatus?.last_completed_at) return false;
    const lastCompletedAt = new Date(schedulerStatus.last_completed_at).getTime();
    if (Number.isNaN(lastCompletedAt)) return false;
    const staleAfterMs = ((schedulerStatus.interval_hours || 24) * 60 + 10) * 60 * 1000;
    return Date.now() - lastCompletedAt > staleAfterMs;
  }, [schedulerStatus]);

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
      void fetchSessions();
    }
    if (activeTab === 'premium') {
      void fetchPremiumStatus();
    }
    if (activeTab === 'analytics') {
      void fetchGeminiObservations();
      void refreshAnalyticsHealth();
    }
  }, [activeTab, fetchGeminiObservations, refreshAnalyticsHealth]);

  useEffect(() => {
    if (activeTab !== 'analytics') return;
    const interval = window.setInterval(() => {
      void refreshAnalyticsHealth();
    }, 30000);
    return () => window.clearInterval(interval);
  }, [activeTab, refreshAnalyticsHealth]);

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
              setRemoteProgress(null);
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
            case 'session_auto_heal_start':
              setRemoteProgress('auto_heal');
              toast.loading('Recovering browser session...', { id: 'remote-auto-heal' });
              break;
            case 'stream_restarted':
              setRemoteProgress('stream_restarted');
              toast.success('Browser stream restarted');
              break;
            case 'session_auto_heal_done':
              if (message.data?.success) {
                setRemoteProgress(null);
                toast.success('Browser session recovered', { id: 'remote-auto-heal' });
              } else {
                toast.error(message.data?.error || 'Browser recovery failed', { id: 'remote-auto-heal' });
              }
              break;
            case 'session_idle_timeout_close':
              toast('Session closed after 5 minutes of idle time');
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

  const handleRemoteRestart = async () => {
    if (!remoteSession) return;
    try {
      setRemoteProgress('auto_heal');
      setRemoteFrame(null);
      const res = await fetch(
        `${API_BASE}/sessions/${encodeURIComponent(remoteSession.profile_name)}/remote/restart`,
        {
          method: 'POST',
          headers: getAuthHeaders(),
        },
      );
      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.error || data.detail || 'Failed to restart remote browser');
      }
      toast.success('Remote browser restart triggered');
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to restart remote browser');
      setRemoteProgress(null);
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
            <TabsTrigger value="premium" onClick={() => { fetchPremiumStatus(); }}>
              Premium
            </TabsTrigger>
            <TabsTrigger value="analytics" onClick={() => { void fetchGeminiObservations(); void refreshAnalyticsHealth(); }}>
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
                  <Label>Campaign Input Mode</Label>
                  <div className="flex items-center gap-2">
                    <Button
                      type="button"
                      size="sm"
                      variant={campaignInputMode === 'manual' ? 'default' : 'outline'}
                      onClick={() => setCampaignInputMode('manual')}
                    >
                      Manual
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant={campaignInputMode === 'ai' ? 'default' : 'outline'}
                      onClick={() => setCampaignInputMode('ai')}
                    >
                      AI Interview
                    </Button>
                  </div>
                </div>

                {campaignInputMode === 'manual' ? (
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
                ) : (
                  <div className="space-y-4 rounded-lg border border-[rgba(0,0,0,0.1)] p-4 bg-[rgba(51,51,51,0.02)]">
                    <div className="space-y-2">
                      <Label>1. Post Context</Label>
                      <div className="flex items-center gap-2 flex-wrap">
                        <Badge variant={aiContextSnapshot ? 'secondary' : 'outline'}>
                          {aiContextLoading ? 'Fetching context...' : aiContextSnapshot ? 'Context ready' : 'Waiting for URL'}
                        </Badge>
                        {aiContextError && (
                          <span className="text-xs text-red-600">{aiContextError}</span>
                        )}
                      </div>
                    </div>

                    {aiContextSnapshot && (
                      <div className="rounded-md border border-[rgba(0,0,0,0.1)] bg-white p-3 space-y-2">
                        <p className="text-xs font-medium text-[#666666]">Context Snapshot</p>
                        <p className="text-sm text-[#111111] whitespace-pre-wrap">
                          {aiContextSnapshot.op_post?.text || '(no post text found)'}
                        </p>
                        {(aiContextSnapshot.supporting_comments || []).length > 0 && (
                          <div className="space-y-1">
                            {(aiContextSnapshot.supporting_comments || []).slice(0, 2).map((comment, idx) => (
                              <p key={`${comment.id || idx}`} className="text-xs text-[#444444] whitespace-pre-wrap">
                                {idx + 1}. {comment.text || '(empty comment)'}
                              </p>
                            ))}
                          </div>
                        )}
                      </div>
                    )}

                    <div className="space-y-2">
                      <Label>2. Product</Label>
                      <div className="flex items-center gap-2">
                        <Select
                          value={selectedAiProductId}
                          onValueChange={setSelectedAiProductId}
                          disabled={aiProductsLoading || aiProducts.length === 0}
                        >
                          <SelectTrigger className="bg-white">
                            <SelectValue placeholder={aiProductsLoading ? 'Loading products...' : 'Select product'} />
                          </SelectTrigger>
                          <SelectContent>
                            {aiProducts.map((product) => (
                              <SelectItem key={product.id} value={product.id}>
                                {product.name}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        <Button
                          type="button"
                          variant="outline"
                          onClick={() => setProductEditorOpen((prev) => !prev)}
                        >
                          {productEditorOpen ? 'Close Editor' : 'Edit Product'}
                        </Button>
                      </div>
                      {productEditorOpen && (
                        <div className="space-y-2 rounded-md border border-[rgba(0,0,0,0.1)] bg-white p-3">
                          <Input
                            value={editableProductName}
                            onChange={(e) => setEditableProductName(e.target.value)}
                            placeholder="Product name"
                            className="bg-white"
                          />
                          <Textarea
                            value={editableProductPrompt}
                            onChange={(e) => setEditableProductPrompt(e.target.value)}
                            placeholder="Long product methodology prompt..."
                            className="min-h-[140px] bg-white"
                          />
                          <div className="flex items-center gap-2">
                            <Button
                              type="button"
                              variant="outline"
                              onClick={saveSelectedAiProduct}
                              disabled={savingAiProduct || !selectedAiProductId}
                            >
                              {savingAiProduct ? (
                                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                              ) : null}
                              Save Selected
                            </Button>
                            <Button
                              type="button"
                              onClick={createAiProduct}
                              disabled={creatingAiProduct}
                            >
                              {creatingAiProduct ? (
                                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                              ) : (
                                <Plus className="w-4 h-4 mr-2" />
                              )}
                              Create New
                            </Button>
                          </div>
                        </div>
                      )}
                    </div>

                    <div className="space-y-2">
                      <Label>3. Comment Count</Label>
                      <Input
                        type="number"
                        min={10}
                        max={50}
                        value={aiCommentCount}
                        onChange={(e) => {
                          const val = Math.max(10, Math.min(50, Number(e.target.value) || 10));
                          setAiCommentCount(val);
                        }}
                        className="w-24 bg-white"
                      />
                    </div>

                    <div className="flex items-center gap-2 flex-wrap">
                      <Button
                        type="button"
                        onClick={generateAiComments}
                        disabled={aiGenerating || !aiContextSnapshot || !selectedAiProductId}
                      >
                        {aiGenerating ? (
                          <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                        ) : (
                          <Star className="w-4 h-4 mr-2" />
                        )}
                        {aiGenerating ? 'Generating...' : 'Generate Comments'}
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        onClick={regenerateAllAiComments}
                        disabled={aiRegeneratingAll || !activeDraftId || aiComments.length === 0}
                      >
                        {aiRegeneratingAll ? (
                          <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                        ) : (
                          <RefreshCw className="w-4 h-4 mr-2" />
                        )}
                        {aiRegeneratingAll ? 'Regenerating...' : 'Regenerate All'}
                      </Button>
                      {aiModel && (
                        <Badge variant="outline" className="text-xs">
                          {aiModel}
                        </Badge>
                      )}
                      {aiRulesSummary?.version && (
                        <Badge variant="outline" className="text-xs">
                          rules {aiRulesSummary.version}
                        </Badge>
                      )}
                    </div>

                    {aiComments.length > 0 && (
                      <div className="space-y-3">
                        <Label>4. Review and Edit</Label>
                        {aiComments.map((comment, idx) => (
                          <div key={`ai-comment-${idx}`} className="flex gap-2 items-start">
                            <span className="text-xs text-[#999999] pt-2 w-5">{idx + 1}</span>
                            <Textarea
                              value={comment}
                              onChange={(e) => updateAiCommentText(idx, e.target.value)}
                              className="min-h-[72px] bg-white"
                            />
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              onClick={() => regenerateSingleAiComment(idx)}
                              disabled={aiRegeneratingIndex === idx || !activeDraftId}
                            >
                              {aiRegeneratingIndex === idx ? (
                                <Loader2 className="w-3 h-3 animate-spin" />
                              ) : (
                                <RefreshCw className="w-3 h-3" />
                              )}
                            </Button>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}

                {campaignInputMode !== 'ai' && (
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
                )}

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

                <div className="flex flex-wrap items-center gap-3">
                  <Button
                    onClick={publishNow}
                    disabled={!url || parseCommentsInput(comments).length === 0 || addingToQueue || queueState.pending_count >= queueState.max_pending}
                  >
                    {addingToQueue ? (
                      <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                    ) : (
                      <Play className="w-4 h-4 mr-2" />
                    )}
                    {addingToQueue ? 'Publishing...' : 'Publish Now'}
                  </Button>

                  <Button
                    variant="outline"
                    onClick={saveDraftNow}
                    disabled={savingDraft}
                  >
                    {savingDraft ? (
                      <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                    ) : (
                      <Plus className="w-4 h-4 mr-2" />
                    )}
                    {activeDraftId ? 'Save Draft' : 'Save as Draft'}
                  </Button>

                  {activeDraftId && (
                    <Badge variant="secondary" className="text-xs">
                      Editing shared draft
                    </Badge>
                  )}

                  {activeDraftId && (
                    <span className="text-xs text-[#999999]">
                      {draftSaveStatus === 'saving' ? 'saving...' : draftSaveStatus === 'saved' ? 'saved' : draftSaveStatus === 'error' ? 'save failed' : ''}
                    </span>
                  )}
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="bg-[rgba(51,51,51,0.04)] border-b border-[rgba(0,0,0,0.1)] pb-4">
                <CardTitle className="text-lg flex items-center justify-between">
                  <span>Shared Drafts</span>
                  <Badge variant="outline" className="font-normal">
                    {drafts.length}
                  </Badge>
                </CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                {draftsLoading ? (
                  <div className="p-6 text-center text-[#999999]">
                    <Loader2 className="w-6 h-6 mx-auto mb-2 animate-spin opacity-50" />
                    <p>Loading drafts...</p>
                  </div>
                ) : drafts.length === 0 ? (
                  <div className="p-6 text-center text-[#999999]">
                    <p>No shared drafts yet.</p>
                    <p className="text-xs mt-1">Use Save Draft while composing a campaign.</p>
                  </div>
                ) : (
                  <div className="divide-y divide-[rgba(0,0,0,0.1)]">
                    {drafts.map((draft) => (
                      <div key={draft.id} className="p-4 flex items-center justify-between gap-3">
                        <div className="min-w-0 flex-1">
                          <p className="text-sm text-[#111111] truncate">{draft.url || '(no url yet)'}</p>
                          <p className="text-xs text-[#999999]">
                            {draft.comments?.length || 0} comments | updated by {draft.updated_by} {formatRelativeTime(draft.updated_at)}
                          </p>
                        </div>
                        <div className="flex items-center gap-2">
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => openDraftInComposer(draft)}
                          >
                            Open
                          </Button>
                          <Button
                            size="sm"
                            onClick={() => publishDraftCampaign(draft.id, activeDraftId === draft.id)}
                            disabled={publishingDraftId === draft.id || queueState.pending_count >= queueState.max_pending}
                          >
                            {publishingDraftId === draft.id ? (
                              <Loader2 className="w-3 h-3 mr-1 animate-spin" />
                            ) : (
                              <Play className="w-3 h-3 mr-1" />
                            )}
                            Publish
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => deleteDraftItem(draft.id)}
                            disabled={deletingDraftId === draft.id}
                            className="text-red-500 hover:text-red-700"
                          >
                            {deletingDraftId === draft.id ? (
                              <Loader2 className="w-3 h-3 animate-spin" />
                            ) : (
                              <Trash2 className="w-4 h-4" />
                            )}
                          </Button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
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
                  <div className="flex items-center justify-between">
                    <CardTitle className="text-lg">
                      Recent History ({queueState.history.length})
                    </CardTitle>
                    {queueState.history.some((c: QueuedCampaign) => c.success_count !== undefined && c.total_count !== undefined && c.success_count < c.total_count) && (
                      <Button
                        variant="destructive"
                        size="sm"
                        onClick={handleRetryAllFailed}
                        disabled={isRetryingAll}
                      >
                        {isRetryingAll ? (
                          <>
                            <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                            Retrying All...
                          </>
                        ) : (
                          <>
                            <RefreshCw className="h-3 w-3 mr-1" />
                            Retry All Failed
                          </>
                        )}
                      </Button>
                    )}
                  </div>
                </CardHeader>
                <CardContent className="p-0 max-h-64 overflow-y-auto">
                  <div className="divide-y divide-[rgba(0,0,0,0.1)]">
                    {queueState.history.slice(0, historyDisplayCount).map((campaign) => (
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
                              {campaign.auto_retry?.status === 'scheduled' && (
                                <span className="ml-2 text-amber-500">
                                  (retry {(campaign.auto_retry.current_round || 0) + 1}/{campaign.auto_retry.max_rounds}
                                  {campaign.auto_retry.next_retry_at && (() => {
                                    const diff = Math.max(0, Math.round((new Date(campaign.auto_retry!.next_retry_at!).getTime() - Date.now()) / 60000));
                                    return diff > 0 ? ` in ${diff}m` : ' now';
                                  })()})
                                </span>
                              )}
                              {campaign.auto_retry?.status === 'in_progress' && (
                                <span className="ml-2 text-blue-500">(retrying round {(campaign.auto_retry.current_round || 0) + 1}/{campaign.auto_retry.max_rounds})</span>
                              )}
                              {campaign.auto_retry?.status === 'completed' && campaign.has_retries && (
                                <span className="ml-2 text-green-500">(auto-retry done)</span>
                              )}
                              {campaign.auto_retry?.status === 'exhausted' && (
                                <span className="ml-2 text-red-400">(retries exhausted)</span>
                              )}
                              {!campaign.auto_retry && campaign.has_retries && (
                                <span className="ml-2 text-blue-500">(retried)</span>
                              )}
                            </div>
                          </div>
                        </div>
                        <ChevronRight className="w-4 h-4 text-[#999999] shrink-0" />
                      </div>
                    ))}
                    {queueState.history.length > historyDisplayCount && (
                      <button
                        onClick={() => setHistoryDisplayCount(prev => prev + 20)}
                        className="w-full p-2 text-xs text-[#666666] hover:text-[#333333] hover:bg-gray-50 transition-colors"
                      >
                        Show more ({queueState.history.length - historyDisplayCount} remaining)
                      </button>
                    )}
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
                    <button
                      onClick={() => setSessionStatusFilters(prev => ({
                        ...prev,
                        restricted: prev.restricted ? undefined : true
                      }))}
                      className={`h-7 px-2.5 text-xs rounded-full border transition-colors ${
                        (sessionStatusFilters as Record<string, unknown>).restricted
                          ? 'bg-amber-100 border-amber-300 text-amber-700'
                          : 'bg-white border-[rgba(0,0,0,0.1)] text-[#666666] hover:border-[#999999]'
                      }`}
                    >
                      Restricted
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
                    {filteredSessions.slice(sessionsPage * SESSIONS_PER_PAGE, (sessionsPage + 1) * SESSIONS_PER_PAGE).map((session, index) => (
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
                            {/* Appeal Status Badges */}
                            {(() => {
                              const appeal = appealStatuses.get(session.profile_name);
                              if (!appeal) return null;
                              if (appeal.appeal_status === 'in_review')
                                return <Badge variant="outline" className="text-[10px] py-0 h-5 bg-yellow-50 text-yellow-700 border-yellow-300">Appeal In Review</Badge>;
                              if (appeal.appeal_status === 'failed')
                                return <Badge variant="outline" className="text-[10px] py-0 h-5 bg-red-50 text-red-700 border-red-300">Appeal Failed ({appeal.appeal_attempts}/3)</Badge>;
                              if (appeal.appeal_status === 'exhausted')
                                return <Badge variant="outline" className="text-[10px] py-0 h-5 bg-gray-50 text-gray-600 border-gray-300">Appeals Exhausted</Badge>;
                              if (appeal.status === 'restricted' && appeal.appeal_status === 'none')
                                return <Badge variant="outline" className="text-[10px] py-0 h-5 bg-amber-50 text-amber-700 border-amber-300">Restricted</Badge>;
                              return null;
                            })()}
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
                    {filteredSessions.length > SESSIONS_PER_PAGE && (
                      <div className="flex items-center justify-between px-4 py-2 bg-gray-50 text-xs text-[#666666]">
                        <span>
                          Showing {sessionsPage * SESSIONS_PER_PAGE + 1}-{Math.min((sessionsPage + 1) * SESSIONS_PER_PAGE, filteredSessions.length)} of {filteredSessions.length}
                        </span>
                        <div className="flex gap-2">
                          <button
                            onClick={() => setSessionsPage(p => Math.max(0, p - 1))}
                            disabled={sessionsPage === 0}
                            className="px-2 py-1 rounded border border-gray-300 disabled:opacity-30 hover:bg-white transition-colors"
                          >
                            Prev
                          </button>
                          <button
                            onClick={() => setSessionsPage(p => Math.min(Math.ceil(filteredSessions.length / SESSIONS_PER_PAGE) - 1, p + 1))}
                            disabled={(sessionsPage + 1) * SESSIONS_PER_PAGE >= filteredSessions.length}
                            className="px-2 py-1 rounded border border-gray-300 disabled:opacity-30 hover:bg-white transition-colors"
                          >
                            Next
                          </button>
                        </div>
                      </div>
                    )}
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

          <TabsContent value="premium" className="mt-6 space-y-6">
            <Card>
              <CardHeader className="bg-[rgba(51,51,51,0.04)] border-b border-[rgba(0,0,0,0.1)] pb-4">
                <CardTitle className="text-lg flex items-center justify-between">
                  <span>Premium Pipeline Status</span>
                  <Button size="sm" variant="outline" onClick={fetchPremiumStatus} disabled={premiumLoading}>
                    {premiumLoading ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <RefreshCw className="w-4 h-4" />
                    )}
                    Refresh
                  </Button>
                </CardTitle>
              </CardHeader>
              <CardContent className="pt-4">
                {!premiumStatus ? (
                  <div className="text-sm text-[#999999]">No premium status data yet.</div>
                ) : (
                  <div className="space-y-4">
                    <div className="grid grid-cols-2 md:grid-cols-7 gap-3 text-sm">
                      <div>
                        <div className="text-xs text-[#999999]">Scheduler</div>
                        <div className="font-medium">{premiumStatus.scheduler?.is_running ? 'Running' : 'Stopped'}</div>
                      </div>
                      <div>
                        <div className="text-xs text-[#999999]">Last Tick</div>
                        <div className="font-medium">{premiumStatus.scheduler?.last_tick_at ? formatRelativeTime(premiumStatus.scheduler.last_tick_at) : 'Never'}</div>
                      </div>
                      <div>
                        <div className="text-xs text-[#999999]">Scheduled</div>
                        <div className="font-medium">{premiumStatus.counts?.scheduled ?? 0}</div>
                      </div>
                      <div>
                        <div className="text-xs text-[#999999]">Queued</div>
                        <div className="font-medium">{premiumStatus.counts?.queued ?? 0}</div>
                      </div>
                      <div>
                        <div className="text-xs text-[#999999]">In Progress</div>
                        <div className="font-medium">{premiumStatus.counts?.in_progress ?? 0}</div>
                      </div>
                      <div>
                        <div className="text-xs text-[#999999]">Completed</div>
                        <div className="font-medium text-green-600">{premiumStatus.counts?.completed ?? 0}</div>
                      </div>
                      <div>
                        <div className="text-xs text-[#999999]">Failed</div>
                        <div className="font-medium text-red-500">{premiumStatus.counts?.failed ?? 0}</div>
                      </div>
                    </div>

                    {premiumStatus.rules_snapshot && (
                      <div className="text-xs text-[#666666]">
                        Rules snapshot: {premiumStatus.rules_snapshot.version || 'N/A'}
                        {premiumStatus.rules_snapshot.synced_at ? ` · synced ${formatRelativeTime(premiumStatus.rules_snapshot.synced_at)}` : ''}
                      </div>
                    )}

                    {premiumStatus.scheduler?.last_error && (
                      <div className="text-xs text-red-500">Last scheduler error: {premiumStatus.scheduler.last_error}</div>
                    )}
                  </div>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="bg-[rgba(51,51,51,0.04)] border-b border-[rgba(0,0,0,0.1)] pb-4">
                <CardTitle className="text-lg">Recent Premium Runs</CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                {!premiumStatus || (premiumStatus.recent_runs || []).length === 0 ? (
                  <div className="p-6 text-sm text-[#999999]">No premium runs recorded yet.</div>
                ) : (
                  <div className="divide-y divide-[rgba(0,0,0,0.1)]">
                    {(premiumStatus.recent_runs || []).slice(0, 15).map((run) => (
                      <div key={run.id} className="p-4">
                        <div className="flex items-center justify-between gap-3">
                          <div>
                            <div className="text-sm font-medium text-[#111111]">
                              {run.run_spec?.profile_name || 'Unknown profile'}
                            </div>
                            <div className="text-xs text-[#999999]">
                              Run {run.id.slice(0, 8)}
                              {run.next_execute_at ? ` · next ${formatRelativeTime(run.next_execute_at)}` : ''}
                              {run.status === 'queued' && run.queue_position ? ` · queue #${run.queue_position}` : ''}
                            </div>
                          </div>
                          <Badge variant={run.status === 'completed' ? 'default' : run.status === 'failed' ? 'destructive' : 'secondary'}>
                            {run.status}
                          </Badge>
                        </div>
                        {run.error && (
                          <div className="text-xs text-red-500 mt-2">{run.error}</div>
                        )}
                        {run.pass_matrix && (
                          <div className="text-xs text-[#666666] mt-2 flex flex-wrap gap-3">
                            {Object.entries(run.pass_matrix).map(([key, value]) => (
                              <span key={key}>{key}: {value}</span>
                            ))}
                          </div>
                        )}
                        {run.safety && (
                          <div className="text-xs text-[#666666] mt-2 flex flex-wrap gap-3">
                            <span>dup: {run.safety.duplicate_precheck?.all_passed === null ? 'n/a' : run.safety.duplicate_precheck?.all_passed ? 'pass' : 'fail'}</span>
                            <span>identity: {run.safety.identity_check?.all_passed === null ? 'n/a' : run.safety.identity_check?.all_passed ? 'pass' : 'fail'}</span>
                            <span>submit guard: {run.safety.submit_guard?.all_passed === null ? 'n/a' : run.safety.submit_guard?.all_passed ? 'pass' : 'fail'}</span>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
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

            {/* Restriction Recovery Card */}
            <Card>
              <CardHeader className="bg-[rgba(51,51,51,0.04)] border-b border-[rgba(0,0,0,0.1)] pb-4">
                <div className="flex items-center justify-between">
                  <CardTitle className="text-lg flex items-center gap-2">
                    <Shield className="w-5 h-5" />
                    Restriction Recovery
                  </CardTitle>
                  <div className="flex items-center gap-2">
                    {schedulerStatus?.enabled && (
                      <Badge variant="outline" className="bg-green-50 text-green-700 text-xs">Enabled</Badge>
                    )}
                    {schedulerStatus?.busy_skipped ? (
                      <Badge variant="outline" className="bg-[#f4f4f4] text-[#555555] text-xs">
                        {schedulerStatus.busy_skipped} busy skipped
                      </Badge>
                    ) : null}
                    {schedulerIsStale && (
                      <Badge variant="outline" className="bg-amber-50 text-amber-700 text-xs border-amber-200">
                        Stale
                      </Badge>
                    )}
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={handleSchedulerRunNow}
                      disabled={schedulerRunning}
                    >
                      {schedulerRunning ? (
                        <Loader2 className="w-4 h-4 animate-spin mr-1" />
                      ) : (
                        <Play className="w-4 h-4 mr-1" />
                      )}
                      Run Now
                    </Button>
                  </div>
                </div>
                <p className="text-sm text-[#666666] mt-2">
                  Automatically verifies and appeals restricted profiles every {schedulerStatus?.interval_hours || 24}h.
                </p>
              </CardHeader>
              <CardContent className="pt-4">
                {!schedulerStatus ? (
                  <div className="text-center py-4 text-[#999999] text-sm">
                    <p>Loading scheduler status...</p>
                  </div>
                ) : (
                  <div className="space-y-3">
                    <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-sm">
                      <div>
                        <div className="text-xs text-[#999999]">Last Run</div>
                        <div className="font-medium">
                          {schedulerStatus.last_run_at ? formatRelativeTime(schedulerStatus.last_run_at) : 'Never'}
                        </div>
                      </div>
                      <div>
                        <div className="text-xs text-[#999999]">Last Completed</div>
                        <div className="font-medium">
                          {schedulerStatus.last_completed_at ? formatRelativeTime(schedulerStatus.last_completed_at) : 'Never'}
                        </div>
                      </div>
                      <div>
                        <div className="text-xs text-[#999999]">Next Run</div>
                        <div className="font-medium">
                          {schedulerStatus.next_run_at ? formatRelativeTime(schedulerStatus.next_run_at) : 'Pending'}
                        </div>
                      </div>
                      {schedulerStatus.last_results?.verify_phase && !schedulerStatus.last_results.verify_phase.error && (
                        <>
                          <div>
                            <div className="text-xs text-[#999999]">Unblocked</div>
                            <div className="font-medium text-green-600">{schedulerStatus.last_results.verify_phase.unblocked || 0}</div>
                          </div>
                          <div>
                            <div className="text-xs text-[#999999]">Still Restricted</div>
                            <div className="font-medium text-red-500">{schedulerStatus.last_results.verify_phase.still_restricted || 0}</div>
                          </div>
                          <div>
                            <div className="text-xs text-[#999999]">Needs Follow-up</div>
                            <div className="font-medium text-amber-600">{schedulerStatus.last_results.verify_phase.needs_followup || 0}</div>
                          </div>
                        </>
                      )}
                    </div>
                    {schedulerIsStale && schedulerStatus.last_completed_at && (
                      <div className="text-xs text-amber-700">
                        Scheduler state is stale. Last completed {formatRelativeTime(schedulerStatus.last_completed_at)}.
                      </div>
                    )}
                    {schedulerStatus.last_results?.appeal_phase && !schedulerStatus.last_results.appeal_phase.error && (
                      <div className="text-xs text-[#666666]">
                        Appeals: {schedulerStatus.last_results.appeal_phase.succeeded || 0} succeeded, {schedulerStatus.last_results.appeal_phase.failed || 0} failed
                      </div>
                    )}
                    {schedulerStatus.last_results?.per_profile && schedulerStatus.last_results.per_profile.length > 0 && (
                      <details>
                        <summary className="text-xs text-[#666666] cursor-pointer">
                          Per-profile details ({schedulerStatus.last_results.per_profile.length})
                        </summary>
                        <div className="mt-2 space-y-1 max-h-32 overflow-y-auto">
                          {schedulerStatus.last_results.per_profile.map((p, i) => (
                            <div key={i} className="text-xs flex items-center gap-2">
                              <span className={p.action === 'auto_unblocked'
                                ? 'text-green-600'
                                : p.action === 'confirmed_restricted'
                                  ? 'text-red-500'
                                  : p.action === 'needs_followup'
                                    ? 'text-amber-700'
                                    : 'text-[#666666]'}>
                                {p.name}: {p.action}
                              </span>
                              {p.busy_reason && <span className="text-[#999999]">({p.busy_reason})</span>}
                              {p.error && <span className="text-red-400">({p.error})</span>}
                            </div>
                          ))}
                        </div>
                      </details>
                    )}
                  </div>
                )}
              </CardContent>
            </Card>

            <ProfileHealthConsole
              profiles={profileAnalytics}
              loading={loadingAnalytics}
              expandedProfile={expandedProfile}
              onExpandProfile={setExpandedProfile}
              onRefresh={() => { void refreshAnalyticsHealth(); }}
              onVerify={(profileName) => { void verifyRestrictedProfile(profileName); }}
              onAppeal={(profileName) => { void appealRestrictedProfile(profileName); }}
              onUnblock={(profileName) => { void unblockProfile(profileName); }}
              onRestrict={(profileName, hours) => { void restrictProfile(profileName, hours); }}
              isActionRunning={isProfileActionRunning}
            />

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

              {/* Auto-Retry Status */}
              {selectedCampaign.auto_retry && (
                <div className={`p-3 rounded-lg border text-sm ${
                  selectedCampaign.auto_retry.status === 'scheduled' ? 'bg-amber-50 border-amber-200' :
                  selectedCampaign.auto_retry.status === 'in_progress' ? 'bg-blue-50 border-blue-200' :
                  selectedCampaign.auto_retry.status === 'completed' ? 'bg-green-50 border-green-200' :
                  'bg-gray-50 border-gray-200'
                }`}>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <RotateCw className={`w-4 h-4 ${selectedCampaign.auto_retry.status === 'in_progress' ? 'animate-spin' : ''}`} />
                      <span className="font-medium">
                        Auto-Retry: {selectedCampaign.auto_retry.status === 'scheduled' ? `Round ${(selectedCampaign.auto_retry.current_round || 0) + 1}/${selectedCampaign.auto_retry.max_rounds} scheduled` :
                          selectedCampaign.auto_retry.status === 'in_progress' ? `Round ${(selectedCampaign.auto_retry.current_round || 0) + 1}/${selectedCampaign.auto_retry.max_rounds} in progress` :
                          selectedCampaign.auto_retry.status === 'completed' ? 'Complete' :
                          'Exhausted'}
                      </span>
                    </div>
                    {selectedCampaign.auto_retry.status === 'scheduled' && selectedCampaign.auto_retry.next_retry_at && (
                      <span className="text-xs text-[#999999]">
                        Next: {formatRelativeTime(selectedCampaign.auto_retry.next_retry_at)}
                      </span>
                    )}
                  </div>
                  {selectedCampaign.auto_retry.failed_jobs && selectedCampaign.auto_retry.failed_jobs.length > 0 && (
                    <details className="mt-2">
                      <summary className="text-xs text-[#666666] cursor-pointer">
                        {selectedCampaign.auto_retry.failed_jobs.filter(j => !j.exhausted).length} jobs pending, {selectedCampaign.auto_retry.failed_jobs.filter(j => j.exhausted).length} exhausted
                      </summary>
                      <div className="mt-1 space-y-1">
                        {selectedCampaign.auto_retry.failed_jobs.map(job => (
                          <div key={job.job_index} className="text-xs flex items-center gap-2">
                            <span className={job.exhausted ? 'text-red-500' : 'text-[#666666]'}>
                              Job {job.job_index}: {job.comment.slice(0, 40)}{job.comment.length > 40 ? '...' : ''}
                            </span>
                            {job.exhausted && <Badge variant="outline" className="text-[10px] px-1">exhausted</Badge>}
                            {job.excluded_profiles.length > 0 && (
                              <span className="text-[#999999]">({job.excluded_profiles.length} excluded)</span>
                            )}
                          </div>
                        ))}
                      </div>
                    </details>
                  )}
                </div>
              )}

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
              <Button variant="outline" onClick={handleRemoteRestart} disabled={!remoteSession}>
                Restart
              </Button>
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
                          remoteProgress === 'auto_heal' ? 'Recovering browser session...' :
                          remoteProgress === 'stream_restarted' ? 'Restarting stream...' :
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
