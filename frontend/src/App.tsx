import { useState, useEffect, useCallback, useRef } from 'react'
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Checkbox } from "@/components/ui/checkbox"
import { Loader2, Send, CheckCircle, XCircle, RefreshCw, Key, Copy, Trash2, Wifi, WifiOff, Eye, Upload, Globe, Plus, Play, AlertCircle, X, Mouse } from "lucide-react"
import { Toaster, toast } from 'sonner'

const API_BASE = import.meta.env.VITE_API_BASE || "https://commentbot-production.up.railway.app";
const WS_BASE = API_BASE.replace('https://', 'wss://').replace('http://', 'ws://');

interface Session {
  file: string;
  profile_name: string;
  user_id: string | null;
  extracted_at: string;
  valid: boolean;
  proxy?: string;
  proxy_masked?: string;  // Masked proxy URL for display
  proxy_source?: string;  // "session" or "env" to show source
  profile_picture?: string | null;  // Base64 encoded PNG
}

interface Job {
  profile_name: string;
  comment: string;
  status: 'pending' | 'success' | 'failed';
  verified?: boolean;
  method?: string;
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

function App() {
  const [url, setUrl] = useState('');
  const [comments, setComments] = useState('');
  const [sessions, setSessions] = useState<Session[]>([]);
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [isProcessing, setIsProcessing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [campaignDuration, setCampaignDuration] = useState(30); // Duration in minutes (10-1440)

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

  // Session creation state
  const [creatingSession, setCreatingSession] = useState<string | null>(null);
  const [sessionCreateStatus, setSessionCreateStatus] = useState<Record<string, SessionCreateStatus>>({});

  // Session refresh state
  const [refreshingSession, setRefreshingSession] = useState<string | null>(null);
  const [refreshingAll, setRefreshingAll] = useState(false);

  // Batch session creation state
  const [selectedCredentials, setSelectedCredentials] = useState<Set<string>>(new Set());
  const [batchInProgress, setBatchInProgress] = useState(false);

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
  const [_remoteUrl, setRemoteUrl] = useState('');
  const [remoteUrlInput, setRemoteUrlInput] = useState('');
  const [actionLog, setActionLog] = useState<ActionLogEntry[]>([]);
  const [pendingUpload, setPendingUpload] = useState<PendingUpload | null>(null);
  const [uploadReady, setUploadReady] = useState(false);
  const remoteWsRef = useRef<WebSocket | null>(null);
  const screenshotContainerRef = useRef<HTMLDivElement>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectAttemptRef = useRef(0);

  // WebSocket connection
  useEffect(() => {
    const connectWebSocket = () => {
      try {
        const ws = new WebSocket(`${WS_BASE}/ws/live`);

        ws.onopen = () => {
          console.log('WebSocket connected');
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
                setScreenshotKey(k => k + 1);
                break;
              case 'job_complete':
                setJobs(prev => prev.map((job, i) =>
                  i === update.data.job_index
                    ? {
                        ...job,
                        status: update.data.success ? 'success' : 'failed',
                        verified: update.data.verified,
                        method: update.data.method
                      }
                    : job
                ));
                setScreenshotKey(k => k + 1);
                break;
              case 'campaign_complete':
                setLiveStatus(prev => ({
                  ...prev,
                  currentStep: `Done: ${update.data.success}/${update.data.total} successful`
                }));
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
          // Reconnect after 3 seconds
          setTimeout(connectWebSocket, 3000);
        };

        ws.onerror = (error) => {
          console.error('WebSocket error:', error);
        };

        wsRef.current = ws;
      } catch (error) {
        console.error('Failed to connect WebSocket:', error);
        setTimeout(connectWebSocket, 3000);
      }
    };

    connectWebSocket();

    return () => {
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, []);

  const fetchSessions = async () => {
    try {
      const res = await fetch(`${API_BASE}/sessions`);
      const data = await res.json();
      setSessions(data);
    } catch (error) {
      console.error("Failed to fetch sessions:", error);
    } finally {
      setLoading(false);
    }
  };

  const fetchCredentials = async () => {
    try {
      const res = await fetch(`${API_BASE}/credentials`);
      const data = await res.json();
      setCredentials(data);
    } catch (error) {
      console.error("Failed to fetch credentials:", error);
    }
  };

  const fetchProxies = async () => {
    try {
      const res = await fetch(`${API_BASE}/proxies`);
      const data = await res.json();
      setProxies(data);
    } catch (error) {
      console.error("Failed to fetch proxies:", error);
    }
  };

  useEffect(() => {
    fetchSessions();
    fetchCredentials();
    fetchProxies();
  }, []);

  const generateJobs = () => {
    if (!url || !comments) return;

    const commentList = comments.split('\n').filter(c => c.trim());
    const availableSessions = sessions.filter(s => s.valid);

    if (availableSessions.length === 0) {
      alert("No valid sessions available!");
      return;
    }

    // Block if more comments than available sessions
    if (commentList.length > availableSessions.length) {
      alert(`You have ${commentList.length} comments but only ${availableSessions.length} active sessions. Please reduce comments to ${availableSessions.length} or fewer.`);
      return;
    }

    const newJobs: Job[] = [];
    availableSessions.forEach((session, i) => {
      if (i < commentList.length) {
        newJobs.push({
          profile_name: session.profile_name,
          comment: commentList[i].trim(),
          status: 'pending'
        });
      }
    });

    setJobs(newJobs);
  };

  const runCampaign = async () => {
    if (jobs.length === 0) return;

    setIsProcessing(true);
    try {
      const res = await fetch(`${API_BASE}/campaign`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          url,
          comments: jobs.map(j => j.comment),
          profile_names: jobs.map(j => j.profile_name),
          duration_minutes: campaignDuration
        })
      });

      const result = await res.json();

      setJobs(jobs.map((job, i) => ({
        ...job,
        status: result.results?.[i]?.success ? 'success' : 'failed'
      })));

      alert(`Campaign complete: ${result.success}/${result.total} successful`);
    } catch (error) {
      alert(`Error: ${error}`);
    } finally {
      setIsProcessing(false);
    }
  };

  const testSession = async (profileName: string) => {
    try {
      const res = await fetch(`${API_BASE}/sessions/${encodeURIComponent(profileName)}/test`, {
        method: 'POST'
      });
      const result = await res.json();
      alert(result.valid ? `Session valid for user ${result.user_id}` : `Session invalid: ${result.error}`);
      fetchSessions();
    } catch (error) {
      alert(`Error: ${error}`);
    }
  };

  const deleteSession = async (profileName: string) => {
    if (!confirm(`Delete session "${profileName}"? This cannot be undone.`)) return;

    try {
      const res = await fetch(`${API_BASE}/sessions/${encodeURIComponent(profileName)}`, {
        method: 'DELETE'
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

  const addCredential = async () => {
    if (!newUid || !newPassword) {
      alert("UID and Password are required!");
      return;
    }
    
    try {
      await fetch(`${API_BASE}/credentials`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
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
        method: 'DELETE'
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
      const res = await fetch(`${API_BASE}/otp/${encodeURIComponent(uid)}`);
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
        headers: { 'Content-Type': 'application/json' },
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
        method: 'DELETE'
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
        method: 'POST'
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
        headers: { 'Content-Type': 'application/json' },
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
        headers: { 'Content-Type': 'application/json' },
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

  // Session profile name refresh functions
  const refreshSessionName = async (profileName: string) => {
    setRefreshingSession(profileName);
    try {
      const res = await fetch(`${API_BASE}/sessions/${encodeURIComponent(profileName)}/refresh-name`, {
        method: 'POST'
      });
      const result = await res.json();

      if (result.success) {
        if (result.new_profile_name !== result.old_profile_name) {
          alert(`Profile name updated: ${result.old_profile_name} → ${result.new_profile_name}`);
        } else {
          alert(`Profile name confirmed: ${result.new_profile_name}`);
        }
        fetchSessions();
        fetchCredentials();
      } else {
        alert(`Failed to refresh: ${result.error}`);
      }
    } catch (error) {
      alert(`Error: ${error}`);
    } finally {
      setRefreshingSession(null);
    }
  };

  const refreshAllSessionNames = async () => {
    setRefreshingAll(true);
    try {
      const res = await fetch(`${API_BASE}/sessions/refresh-all-names`, {
        method: 'POST'
      });
      const result = await res.json();

      let message = `Refreshed ${result.success}/${result.total} sessions.\n\n`;
      if (result.updates && result.updates.length > 0) {
        const changes = result.updates.filter((u: { old_name: string; new_name: string }) => u.old_name !== u.new_name && u.new_name);
        if (changes.length > 0) {
          message += "Name changes:\n";
          changes.forEach((u: { old_name: string; new_name: string }) => {
            message += `• ${u.old_name} → ${u.new_name}\n`;
          });
        }
      }
      alert(message);
      fetchSessions();
      fetchCredentials();
    } catch (error) {
      alert(`Error: ${error}`);
    } finally {
      setRefreshingAll(false);
    }
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

  // ============================================================================
  // Remote Control Functions
  // ============================================================================

  const connectRemoteWebSocket = useCallback((sessionId: string) => {
    if (remoteWsRef.current) {
      remoteWsRef.current.close();
    }

    setRemoteConnecting(true);

    try {
      const ws = new WebSocket(`${WS_BASE}/ws/session/${encodeURIComponent(sessionId)}/control`);

      ws.onopen = () => {
        console.log('Remote WS connected');
        setRemoteConnected(true);
        setRemoteConnecting(false);
        reconnectAttemptRef.current = 0;
        toast.success('Browser connected');
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
            case 'browser_ready':
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
        method: 'POST'
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

  return (
    <div className="min-h-screen bg-slate-50 p-8 font-sans">
      <div className="max-w-[1200px] mx-auto space-y-8">
        
        <div className="flex justify-between items-center">
          <div>
            <h1 className="text-3xl font-bold tracking-tight text-slate-900">CommentBot</h1>
            <p className="text-slate-500 mt-2">Facebook Comment Automation</p>
          </div>
          <div className="flex items-center gap-2">
            <div className={`h-3 w-3 rounded-full ${loading ? 'bg-yellow-500 animate-pulse' : isProcessing ? 'bg-blue-500 animate-pulse' : 'bg-green-500'}`} />
            <span className="text-sm font-medium text-slate-700">
              {loading ? 'Loading...' : isProcessing ? 'Processing' : 'Ready'}
            </span>
          </div>
        </div>

        <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
          <TabsList className="grid w-full grid-cols-5 lg:w-[750px]">
            <TabsTrigger value="campaign">Campaign</TabsTrigger>
            <TabsTrigger value="live">Live View</TabsTrigger>
            <TabsTrigger value="sessions">Sessions</TabsTrigger>
            <TabsTrigger value="credentials">Credentials</TabsTrigger>
            <TabsTrigger value="proxies">Proxies</TabsTrigger>
          </TabsList>

          <TabsContent value="campaign" className="space-y-6 mt-6">
            <Card className="shadow-md border-slate-200">
              <CardHeader className="bg-slate-100/50 border-b border-slate-100 pb-4">
                <CardTitle className="text-lg">New Campaign</CardTitle>
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
                    <span className="text-sm text-slate-600">
                      minutes ({formatDuration(campaignDuration)})
                    </span>
                  </div>
                  <p className="text-xs text-slate-400">
                    Comments will be spread across this time (10 min - 24 hours)
                  </p>
                </div>

                <div className="flex gap-4">
                  <Button onClick={generateJobs} variant="outline">
                    <RefreshCw className="w-4 h-4 mr-2" />
                    Preview Jobs
                  </Button>
                  <Button onClick={runCampaign} disabled={jobs.length === 0 || isProcessing}>
                    {isProcessing ? (
                      <>
                        <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                        Processing...
                      </>
                    ) : (
                      <>
                        <Send className="w-4 h-4 mr-2" />
                        Run Campaign ({jobs.length} jobs)
                      </>
                    )}
                  </Button>
                </div>
              </CardContent>
            </Card>

            {jobs.length > 0 && (
              <Card className="shadow-md border-slate-200">
                <CardHeader className="bg-slate-100/50 border-b border-slate-100 pb-4">
                  <CardTitle className="text-lg">Jobs ({jobs.length})</CardTitle>
                </CardHeader>
                <CardContent className="p-0">
                  <div className="divide-y divide-slate-100">
                    {jobs.map((job, i) => (
                      <div key={i} className="p-4 flex items-center justify-between hover:bg-slate-50">
                        <div className="flex-1">
                          <div className="font-medium text-slate-900">{job.profile_name}</div>
                          <div className="text-sm text-slate-500 truncate">{job.comment}</div>
                        </div>
                        <Badge variant={job.status === 'success' ? 'default' : job.status === 'failed' ? 'destructive' : 'secondary'}>
                          {job.status === 'success' ? <CheckCircle className="w-3 h-3 mr-1" /> : 
                           job.status === 'failed' ? <XCircle className="w-3 h-3 mr-1" /> : 
                           <Loader2 className="w-3 h-3 mr-1 animate-spin" />}
                          {job.status}
                        </Badge>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            )}
          </TabsContent>

          <TabsContent value="live" className="mt-6">
            <Card className="shadow-md border-slate-200">
              <CardHeader className="bg-slate-100/50 border-b border-slate-100 pb-4">
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
                    key={screenshotKey}
                    src={`${API_BASE}/debug/latest.png?t=${screenshotKey}`}
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
                      <div className="text-xs text-slate-300">
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
            <Card className="shadow-md border-slate-200">
              <CardHeader className="bg-slate-100/50 border-b border-slate-100 pb-4 flex flex-row justify-between items-center">
                <CardTitle className="text-lg">Sessions ({sessions.length})</CardTitle>
                <div className="flex items-center gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={refreshAllSessionNames}
                    disabled={refreshingAll || sessions.length === 0}
                  >
                    {refreshingAll ? (
                      <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                    ) : (
                      <RefreshCw className="w-4 h-4 mr-2" />
                    )}
                    Refresh All Names
                  </Button>
                  <Button size="sm" variant="outline" onClick={fetchSessions}>
                    <RefreshCw className="w-4 h-4 mr-2" />
                    Reload
                  </Button>
                </div>
              </CardHeader>
              <CardContent className="p-0">
                {loading ? (
                  <div className="p-8 text-center text-slate-500">
                    <Loader2 className="w-8 h-8 animate-spin mx-auto mb-4" />
                    Loading sessions...
                  </div>
                ) : sessions.length === 0 ? (
                  <div className="p-8 text-center text-slate-500">
                    No sessions found. Extract sessions from AdsPower first.
                  </div>
                ) : (
                  <div className="divide-y divide-slate-100">
                    {sessions.map((session) => (
                      <div key={session.file} className="p-4 flex items-center justify-between hover:bg-slate-50">
                        <div className="flex items-center gap-3">
                          {/* Profile Picture */}
                          <div className="w-12 h-12 rounded-full overflow-hidden bg-slate-200 flex-shrink-0">
                            {session.profile_picture ? (
                              <img
                                src={`data:image/png;base64,${session.profile_picture}`}
                                alt={session.profile_name}
                                className="w-full h-full object-cover"
                              />
                            ) : (
                              <div className="w-full h-full flex items-center justify-center text-slate-400 text-lg font-medium">
                                {session.profile_name?.[0]?.toUpperCase() || '?'}
                              </div>
                            )}
                          </div>
                          {/* Profile Info */}
                          <div>
                            <div className="font-medium text-slate-900">{session.profile_name}</div>
                            <div className="text-sm text-slate-500">
                              User: {session.user_id || 'Unknown'} • {session.extracted_at.split('T')[0]}
                            </div>
                            {session.proxy_masked ? (
                               <div className="text-xs text-slate-400 mt-1 flex items-center gap-1">
                                 <span className="w-2 h-2 rounded-full bg-green-500"></span>
                                 <span>Proxy: {session.proxy_masked}</span>
                                 {session.proxy_source === "env" && (
                                   <Badge variant="outline" className="ml-1 text-[10px] py-0 h-4">system</Badge>
                                 )}
                               </div>
                            ) : (
                               <div className="text-xs text-red-400 mt-1 flex items-center gap-1">
                                 <span className="w-2 h-2 rounded-full bg-red-500"></span>
                                 No Proxy
                               </div>
                            )}
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          <Badge variant={session.valid ? 'default' : 'destructive'}>
                            {session.valid ? 'Valid' : 'Invalid'}
                          </Badge>
                          <Button
                            size="sm"
                            variant="default"
                            onClick={() => openRemoteModal(session)}
                            disabled={!session.valid}
                          >
                            <Mouse className="w-3 h-3 mr-1" />
                            Control
                          </Button>
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => refreshSessionName(session.profile_name)}
                            disabled={refreshingSession === session.profile_name || refreshingAll}
                          >
                            {refreshingSession === session.profile_name ? (
                              <Loader2 className="w-3 h-3 animate-spin" />
                            ) : (
                              <RefreshCw className="w-3 h-3" />
                            )}
                          </Button>
                          <Button size="sm" variant="outline" onClick={() => testSession(session.profile_name)}>
                            Test
                          </Button>
                          <Button size="sm" variant="ghost" onClick={() => deleteSession(session.profile_name)}>
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
            <Card className="shadow-md border-slate-200 mb-6">
              <CardHeader className="bg-slate-100/50 border-b border-slate-100 pb-4">
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
                <p className="text-xs text-slate-500 mt-2">
                  Format: uid:password:2fa_secret (one per line)
                </p>
              </CardContent>
            </Card>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <Card className="shadow-md border-slate-200">
                <CardHeader className="bg-slate-100/50 border-b border-slate-100 pb-4">
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
                    <p className="text-xs text-slate-500">Base32 secret from Google Authenticator</p>
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

              <Card className="shadow-md border-slate-200">
                <CardHeader className="bg-slate-100/50 border-b border-slate-100 pb-4">
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
                  {credentials.length === 0 ? (
                    <div className="p-8 text-center text-slate-500">
                      No credentials saved yet.
                    </div>
                  ) : (
                    <div className="divide-y divide-slate-100 max-h-[500px] overflow-y-auto">
                      {credentials.map((cred) => {
                        const isEligible = cred.has_secret && !cred.session_connected;
                        const isSelected = selectedCredentials.has(cred.uid);

                        return (
                          <div key={cred.uid} className="p-4 hover:bg-slate-50">
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
                                  <div className="font-medium text-slate-900">{cred.uid}</div>
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
                                  <div className="text-xs text-slate-500 mb-2">Profile: {cred.profile_name}</div>
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
                                        <div className="bg-slate-900 text-white px-3 py-1 rounded font-mono text-lg">
                                          {otpData[cred.uid].code}
                                        </div>
                                        <Button size="sm" variant="outline" onClick={() => copyOTP(otpData[cred.uid].code)}>
                                          <Copy className="w-3 h-3" />
                                        </Button>
                                        <span className="text-xs text-slate-500">
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
              <Card className="shadow-md border-slate-200">
                <CardHeader className="bg-slate-100/50 border-b border-slate-100 pb-4">
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
                    <p className="text-xs text-slate-500">Format: http://username:password@host:port</p>
                  </div>
                  <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-2">
                      <Label>Type</Label>
                      <select
                        value={newProxyType}
                        onChange={(e) => setNewProxyType(e.target.value)}
                        className="w-full h-10 px-3 border border-slate-200 rounded-md bg-white text-sm"
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

              <Card className="shadow-md border-slate-200">
                <CardHeader className="bg-slate-100/50 border-b border-slate-100 pb-4 flex flex-row justify-between items-center">
                  <CardTitle className="text-lg">Saved Proxies ({proxies.length})</CardTitle>
                  <Button size="sm" variant="outline" onClick={fetchProxies}>
                    <RefreshCw className="w-4 h-4 mr-2" />
                    Refresh
                  </Button>
                </CardHeader>
                <CardContent className="p-0">
                  {proxies.length === 0 ? (
                    <div className="p-8 text-center text-slate-500">
                      No proxies configured yet.
                    </div>
                  ) : (
                    <div className="divide-y divide-slate-100 max-h-[500px] overflow-y-auto">
                      {proxies.map((proxy) => (
                        <div key={proxy.id} className={`p-4 hover:bg-slate-50 ${proxy.is_system ? 'bg-green-50/50' : ''}`}>
                          <div className="flex items-center justify-between mb-2">
                            <div className="flex items-center gap-2">
                              <span className="font-medium text-slate-900">{proxy.name}</span>
                              {proxy.is_system && (
                                <Badge variant="secondary" className="text-[10px]">System</Badge>
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
                          <div className="text-xs text-slate-500 space-y-1">
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
        </Tabs>

      </div>

      {/* Remote Control Modal */}
      {remoteModalOpen && remoteSession && (
        <div className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-4">
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-5xl max-h-[85vh] flex flex-col overflow-hidden">
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-2 border-b bg-slate-50 shrink-0">
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
                <div className="text-sm text-slate-500">
                  Session: <span className="font-medium text-slate-900">{remoteSession.profile_name}</span>
                </div>
              </div>
              <Button variant="ghost" size="sm" onClick={closeRemoteModal}>
                <X className="w-5 h-5" />
              </Button>
            </div>

            {/* URL Bar */}
            <div className="flex items-center gap-2 px-4 py-2 border-b bg-white shrink-0">
              <Globe className="w-4 h-4 text-slate-400" />
              <Input
                value={remoteUrlInput}
                onChange={(e) => setRemoteUrlInput(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleRemoteNavigate()}
                placeholder="Enter URL..."
                className="flex-1 bg-slate-50"
              />
              <Button onClick={handleRemoteNavigate} disabled={!remoteConnected}>
                Go
              </Button>
            </div>

            {/* Main Content */}
            <div className="flex-1 flex overflow-hidden min-h-0">
              {/* Screenshot Area */}
              <div className="flex-1 p-2 flex items-center justify-center bg-slate-900 min-h-0">
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
                    <div className="flex items-center justify-center text-slate-400" style={{ width: 250, height: 500 }}>
                      <div className="text-center">
                        <Loader2 className="w-8 h-8 animate-spin mx-auto mb-2" />
                        <p>Waiting for browser...</p>
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
              <div className="w-64 border-l bg-slate-50 flex flex-col shrink-0">
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
                          'bg-slate-100 text-slate-600'
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
                      <div className="text-center text-slate-400 py-8 text-sm">
                        No actions yet. Click on the browser to interact.
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </div>

            {/* Footer */}
            <div className="flex items-center justify-between px-4 py-1 border-t bg-slate-50 text-xs text-slate-500 shrink-0">
              <div className="flex items-center gap-4">
                <span>Viewport: 393x873 (iPhone 12 Pro)</span>
                <span>|</span>
                <span className={remoteConnected ? 'text-green-600' : 'text-slate-400'}>
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
