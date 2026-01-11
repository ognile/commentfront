import { useState, useEffect, useCallback, useRef } from 'react'
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Loader2, Send, CheckCircle, XCircle, RefreshCw, Key, Copy, Trash2, Wifi, WifiOff, Eye } from "lucide-react"

const API_BASE = import.meta.env.VITE_API_BASE || "https://commentbot-production.up.railway.app";
const WS_BASE = API_BASE.replace('https://', 'wss://').replace('http://', 'ws://');

interface Session {
  file: string;
  profile_name: string;
  user_id: string | null;
  extracted_at: string;
  valid: boolean;
  proxy?: string;
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
}

interface OTPData {
  code: string | null;
  remaining_seconds: number;
  valid: boolean;
  error: string | null;
}

function App() {
  const [url, setUrl] = useState('');
  const [comments, setComments] = useState('');
  const [sessions, setSessions] = useState<Session[]>([]);
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [isProcessing, setIsProcessing] = useState(false);
  const [loading, setLoading] = useState(true);

  const [newUid, setNewUid] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [newSecret, setNewSecret] = useState('');
  const [newProfileName, setNewProfileName] = useState('');
  const [otpData, setOtpData] = useState<Record<string, OTPData>>({});

  // WebSocket and live status
  const [liveStatus, setLiveStatus] = useState<LiveStatus>({
    connected: false,
    currentStep: 'idle',
    currentJob: 0,
    totalJobs: 0
  });
  const [screenshotKey, setScreenshotKey] = useState(0);
  const wsRef = useRef<WebSocket | null>(null);

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

  useEffect(() => {
    fetchSessions();
    fetchCredentials();
  }, []);

  const generateJobs = () => {
    if (!url || !comments) return;
    
    const commentList = comments.split('\n').filter(c => c.trim());
    const availableSessions = sessions.filter(s => s.valid);
    
    if (availableSessions.length === 0) {
      alert("No valid sessions available!");
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
          profile_names: jobs.map(j => j.profile_name)
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

        <Tabs defaultValue="campaign" className="w-full">
          <TabsList className="grid w-full grid-cols-4 lg:w-[600px]">
            <TabsTrigger value="campaign">Campaign</TabsTrigger>
            <TabsTrigger value="live">Live View</TabsTrigger>
            <TabsTrigger value="sessions">Sessions</TabsTrigger>
            <TabsTrigger value="credentials">Credentials</TabsTrigger>
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
                    src={`${API_BASE}/debug/latest.png?t=${Date.now()}`}
                    alt="Live Bot View"
                    className="max-h-full max-w-full object-contain"
                    onError={(e) => {
                      (e.target as HTMLImageElement).style.display = 'none';
                    }}
                    onLoad={(e) => {
                      setTimeout(() => {
                        const img = e.target as HTMLImageElement;
                        if (img) img.src = `${API_BASE}/debug/latest.png?t=${Date.now()}`;
                      }, 1000);
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
                <Button size="sm" variant="outline" onClick={fetchSessions}>
                  <RefreshCw className="w-4 h-4 mr-2" />
                  Refresh
                </Button>
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
                        <div>
                          <div className="font-medium text-slate-900">{session.profile_name}</div>
                          <div className="text-sm text-slate-500">
                            User: {session.user_id || 'Unknown'} • {session.extracted_at.split('T')[0]}
                          </div>
                          {session.proxy ? (
                             <div className="text-xs text-slate-400 mt-1 flex items-center gap-1">
                               <span className="w-2 h-2 rounded-full bg-green-500"></span>
                               Proxy: {session.proxy}
                             </div>
                          ) : (
                             <div className="text-xs text-red-400 mt-1 flex items-center gap-1">
                               <span className="w-2 h-2 rounded-full bg-red-500"></span>
                               No Proxy
                             </div>
                          )}
                        </div>
                        <div className="flex items-center gap-2">
                          <Badge variant={session.valid ? 'default' : 'destructive'}>
                            {session.valid ? 'Valid' : 'Invalid'}
                          </Badge>
                          <Button size="sm" variant="outline" onClick={() => testSession(session.profile_name)}>
                            Test
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
                <CardHeader className="bg-slate-100/50 border-b border-slate-100 pb-4 flex flex-row justify-between items-center">
                  <CardTitle className="text-lg">Saved Credentials ({credentials.length})</CardTitle>
                  <Button size="sm" variant="outline" onClick={fetchCredentials}>
                    <RefreshCw className="w-4 h-4 mr-2" />
                    Refresh
                  </Button>
                </CardHeader>
                <CardContent className="p-0">
                  {credentials.length === 0 ? (
                    <div className="p-8 text-center text-slate-500">
                      No credentials saved yet.
                    </div>
                  ) : (
                    <div className="divide-y divide-slate-100 max-h-[500px] overflow-y-auto">
                      {credentials.map((cred) => (
                        <div key={cred.uid} className="p-4 hover:bg-slate-50">
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
                          {cred.profile_name && (
                            <div className="text-xs text-slate-500 mb-2">Profile: {cred.profile_name}</div>
                          )}
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
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>
            </div>
          </TabsContent>
        </Tabs>

      </div>
    </div>
  )
}

export default App
