import { useState, useEffect } from 'react'
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Play, Loader2, ExternalLink, RefreshCw, Trash2, Plus, Unlink, Key, CheckCircle, XCircle, Download } from "lucide-react"

// Types
interface Job {
  id: string;
  profileId: string;
  profileName: string;
  comment: string;
  status: 'pending' | 'running' | 'success' | 'failed';
  proxyIP?: string;
  error?: string;
}

interface UnifiedProfile {
  profile_id: string;
  profile_name: string;
  proxy: string;
  linked_uid: string | null;
  account?: {
    uid: string;
    password_masked: string;
    has_secret: boolean;
  } | null;
  source?: 'adspower' | 'geelark';
  status?: string;
}

interface Credential {
  uid: string;
  password: string;
  secret: string;
}

interface LoginCheckResult {
  status: 'unknown' | 'logged_in' | 'logged_out' | 'error';
  detected_uid?: string;
  error?: string;
}

interface Session {
  file: string;
  profile_name: string;
  user_id: string | null;
  extracted_at: string;
  has_valid_cookies: boolean;
}

interface SessionValidation {
  valid: boolean;
  profile_name: string;
  user_id?: string;
  reason: string;
}

// const API_BASE = "http://localhost:8000";
const API_BASE = "https://commentbot-production.up.railway.app";

function App() {
  // Campaign
  const [url, setUrl] = useState('');
  const [rawComments, setRawComments] = useState('');
  const [jobs, setJobs] = useState<Job[]>([]);
  const [isProcessing, setIsProcessing] = useState(false);
  
  // Unified Data
  const [unifiedProfiles, setUnifiedProfiles] = useState<UnifiedProfile[]>([]);
  const [credentials, setCredentials] = useState<Credential[]>([]);
  
  // Status Tracking
  const [loginChecks, setLoginChecks] = useState<Record<string, LoginCheckResult>>({});
  const [checking, setChecking] = useState<string | null>(null);
  const [launching, setLaunching] = useState<string | null>(null);
  const [otpMap, setOtpMap] = useState<Record<string, string>>({});

  // Forms
  const [newUid, setNewUid] = useState('');
  const [newPass, setNewPass] = useState('');
  const [newSecret, setNewSecret] = useState('');

  // Sessions
  const [sessions, setSessions] = useState<Session[]>([]);
  const [sessionValidations, setSessionValidations] = useState<Record<string, SessionValidation>>({});
  const [extracting, setExtracting] = useState<string | null>(null);
  const [validating, setValidating] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);

  // OTP copied feedback
  const [copiedUid, setCopiedUid] = useState<string | null>(null);

  // Loading state for initial data fetch
  const [isLoading, setIsLoading] = useState(true);

  // Fetch
  const fetchData = async () => {
    try {
      const [profilesRes, credentialsRes, sessionsRes] = await Promise.all([
        fetch(`${API_BASE}/unified_profiles`),
        fetch(`${API_BASE}/credentials`),
        fetch(`${API_BASE}/sessions`)
      ]);

      const [profiles, creds, sess] = await Promise.all([
        profilesRes.json(),
        credentialsRes.json(),
        sessionsRes.json()
      ]);

      setUnifiedProfiles(profiles);
      setCredentials(creds);
      setSessions(sess);
    } catch (error) {
      console.error("Failed to fetch data:", error);
    } finally {
      setIsLoading(false);
    }
  };

  const fetchSessions = async () => {
    try {
      const res = await fetch(`${API_BASE}/sessions`);
      const data = await res.json();
      setSessions(data);
    } catch (error) {
      console.error("Failed to fetch sessions:", error);
    }
  };

  // Restore job status on load
  useEffect(() => {
    fetchData();
    // Check if there are running jobs on the backend
    fetch(`${API_BASE}/status`).then(r => r.json()).then(data => {
      if (data && data.length > 0) {
        setJobs(data);
        // If any job is not finished, resume processing state
        const hasActiveJobs = data.some((j: Job) => j.status === 'pending' || j.status === 'running');
        if (hasActiveJobs) {
          setIsProcessing(true);
        }
      }
    });
  }, []);

  // Poll Jobs
  useEffect(() => {
    let interval: ReturnType<typeof setInterval>;
    if (isProcessing) {
      interval = setInterval(() => {
        fetch(`${API_BASE}/status`).then(r => r.json()).then(data => {
            setJobs(data);
            if (data.length > 0 && data.every((j: Job) => j.status === 'success' || j.status === 'failed')) setIsProcessing(false);
        });
        // Force refresh image by updating state or just relying on the img src timestamp update in render?
        // Actually, React won't re-render the img unless state changes.
        // Let's add a ticker.
        setTick(t => t + 1);
      }, 2000);
    }
    return () => clearInterval(interval);
  }, [isProcessing]);

  const [tick, setTick] = useState(0);

  // Actions
  const mapAccount = async (profileId: string, uid: string) => {
    await fetch(`${API_BASE}/map_account`, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ profileId, uid }) });
    fetchData();
  };

  const unmapAccount = async (profileId: string) => {
    await fetch(`${API_BASE}/unmap_account`, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ profileId }) });
    fetchData();
  };

  const checkStatus = async (profileId: string) => {
    setChecking(profileId);
    const res = await fetch(`${API_BASE}/check_status_smart/${profileId}`);
    const data = await res.json();
    setLoginChecks(prev => ({ ...prev, [profileId]: data }));
    setChecking(null);
  };

  const launchProfile = async (profileId: string) => {
    setLaunching(profileId);
    await fetch(`${API_BASE}/launch`, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ profileId }) });
    setLaunching(null);
  };

  const fetchOtp = async (uid: string) => {
    const res = await fetch(`${API_BASE}/otp/${uid}`);
    if (res.ok) {
        const data = await res.json();
        setOtpMap(prev => ({ ...prev, [uid]: data.code }));
        navigator.clipboard.writeText(data.code);
        setCopiedUid(uid);
        setTimeout(() => setCopiedUid(null), 2000);
    } else alert("Error getting OTP");
  };

  const copyOtp = (uid: string) => {
    if (otpMap[uid]) {
      navigator.clipboard.writeText(otpMap[uid]);
      setCopiedUid(uid);
      setTimeout(() => setCopiedUid(null), 2000);
    }
  };

  const addCredential = async () => {
    if (!newUid || !newPass) return;
    await fetch(`${API_BASE}/credentials`, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ uid: newUid, password: newPass, secret: newSecret }) });
    setNewUid(''); setNewPass(''); setNewSecret('');
    fetchData();
  };

  const deleteCredential = async (uid: string) => {
    if(confirm('Delete?')) await fetch(`${API_BASE}/credentials/${uid}`, { method: 'DELETE' });
    fetchData();
  };

  // Session Actions
  const extractSession = async (profileId: string) => {
    setExtracting(profileId);
    try {
      const res = await fetch(`${API_BASE}/sessions/extract/${profileId}`, { method: 'POST' });
      const data = await res.json();
      if (res.ok) {
        alert(`Session extracted! User ID: ${data.user_id}, Cookies: ${data.cookies_count}`);
        fetchSessions();
      } else {
        alert(`Failed: ${data.detail || 'Unknown error'}`);
      }
    } catch (e) {
      alert(`Error: ${e}`);
    }
    setExtracting(null);
  };

  const validateSession = async (profileName: string) => {
    setValidating(profileName);
    try {
      const res = await fetch(`${API_BASE}/sessions/${encodeURIComponent(profileName)}/validate`);
      const data = await res.json();
      setSessionValidations(prev => ({ ...prev, [profileName]: data }));
    } catch (e) {
      setSessionValidations(prev => ({ ...prev, [profileName]: { valid: false, profile_name: profileName, reason: String(e) } }));
    }
    setValidating(null);
  };

  const deleteSession = async (profileName: string) => {
    if (!confirm(`Delete session for ${profileName}?`)) return;
    setDeleting(profileName);
    try {
      await fetch(`${API_BASE}/sessions/${encodeURIComponent(profileName)}`, { method: 'DELETE' });
      fetchSessions();
      setSessionValidations(prev => {
        const updated = { ...prev };
        delete updated[profileName];
        return updated;
      });
    } catch (e) {
      alert(`Error: ${e}`);
    }
    setDeleting(null);
  };

  // Campaign
  const startCampaign = async () => {
    if(jobs.length === 0) return;
    setIsProcessing(true);
    await fetch(`${API_BASE}/start_campaign`, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ url, jobs: jobs.map(j => ({ profileId: j.profileId, profileName: j.profileName, comment: j.comment })) }) });
  };

  const generatePreview = () => {
    // UPDATED LOGIC: Use ALL available profiles (GeeLark devices + AdsPower profiles with sessions)
    // This allows using GeeLark phones even if they don't have extracted cookies
    const availableProfiles = unifiedProfiles.filter(p => {
      // 1. Is it a GeeLark device? (Usually identified by status or source if we added that field)
      // Since UnifiedProfile structure comes from backend, let's assume if it has no session it might be GeeLark
      // Ideally, the backend should flag 'is_geelark' or 'is_online'
      
      // For now, let's just use ALL profiles in the unified list to be safe.
      // The backend JobManager will figure out how to run them (GeeLark vs AdsPower).
      return true; 
    });

    if (availableProfiles.length === 0) {
      return alert("No profiles available.");
    }

    const commentsList = rawComments.split('\n').filter(l => l.trim());
    if (commentsList.length === 0) {
      return alert("Please enter at least one comment.");
    }

    setJobs(commentsList.map((c, i) => {
      const p = availableProfiles[i % availableProfiles.length];
      return { id: `t_${i}`, profileId: p.profile_id, profileName: p.profile_name, comment: c, status: 'pending', proxyIP: p.proxy };
    }));
  };

  return (
    <div className="min-h-screen bg-slate-50 p-8 font-sans">
      <div className="max-w-[1400px] mx-auto space-y-8">
        
        <div className="flex justify-between items-center">
          <div>
            <h1 className="text-3xl font-bold tracking-tight text-slate-900">AdsPower Command Center</h1>
            <p className="text-slate-500 mt-2">Unified Profile Management & Automation</p>
          </div>
          <div className="flex items-center gap-2">
            <div className={`h-3 w-3 rounded-full shadow-[0_0_8px] transition-colors duration-500 ${isLoading ? 'bg-yellow-500 animate-pulse' : isProcessing ? 'bg-blue-500 animate-pulse' : 'bg-green-500'}`}></div>
            <span className="text-sm font-medium text-slate-700">{isLoading ? 'Loading...' : isProcessing ? 'Campaign Running' : 'System Ready'}</span>
          </div>
        </div>

        <Tabs defaultValue="dashboard" className="w-full">
          <TabsList className="grid w-full grid-cols-4 lg:w-[800px]">
            <TabsTrigger value="dashboard">Dashboard</TabsTrigger>
            <TabsTrigger value="campaign">Campaign Runner</TabsTrigger>
            <TabsTrigger value="sessions">Sessions</TabsTrigger>
            <TabsTrigger value="vault">Credential Vault</TabsTrigger>
          </TabsList>

          {/* TAB 1: DASHBOARD */}
          <TabsContent value="dashboard" className="pt-4">
            <Card className="shadow-md border-slate-200">
              <CardHeader className="bg-slate-100/50 border-b border-slate-100 pb-4 flex flex-row justify-between items-center">
                <CardTitle className="text-lg">Profile Overview</CardTitle>
                <Button variant="outline" size="sm" onClick={fetchData}><RefreshCw className="w-4 h-4 mr-2"/> Refresh</Button>
              </CardHeader>
              <CardContent className="p-0">
                <Table>
                  <TableHeader className="bg-slate-50">
                    <TableRow>
                      <TableHead className="w-[200px]">Profile</TableHead>
                      <TableHead className="w-[300px]">Linked Account</TableHead>
                      <TableHead className="w-[200px]">Login Status</TableHead>
                      <TableHead className="text-right">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {unifiedProfiles.map((p) => {
                      const check = loginChecks[p.profile_id];
                      return (
                        <TableRow key={p.profile_id}>
                          <TableCell>
                            <div className="font-medium text-slate-900 flex items-center gap-2">
                              {p.profile_name}
                              {p.source === 'geelark' && <Badge className="bg-blue-600 text-[10px] h-5">GeeLark</Badge>}
                            </div>
                            <div className="text-xs text-slate-500 font-mono">{p.profile_id}</div>
                          </TableCell>
                          
                          <TableCell>
                            {p.linked_uid ? (
                              <div className="flex items-center gap-2">
                                <Badge variant="secondary" className="font-mono">{p.linked_uid}</Badge>
                                <Button size="icon" variant="ghost" className="h-6 w-6 text-slate-400 hover:text-red-500" onClick={() => unmapAccount(p.profile_id)}>
                                  <Unlink className="w-3 h-3"/>
                                </Button>
                                {p.account?.has_secret && (
                                  <div className="flex items-center gap-1 ml-2">
                                    {otpMap[p.linked_uid] ? (
                                      <code className="text-xs bg-slate-900 text-white px-1.5 py-0.5 rounded cursor-pointer" onClick={() => navigator.clipboard.writeText(otpMap[p.linked_uid!])}>
                                        {otpMap[p.linked_uid]}
                                      </code>
                                    ) : (
                                      <Button size="sm" variant="outline" className="h-6 text-[10px] px-2" onClick={() => fetchOtp(p.linked_uid!)}>2FA</Button>
                                    )}
                                  </div>
                                )}
                              </div>
                            ) : (
                              <div className="flex items-center gap-2">
                                <Select onValueChange={(val) => mapAccount(p.profile_id, val)}>
                                  <SelectTrigger className="w-[180px] h-8 text-xs !bg-white !text-slate-900 border-slate-300">
                                    <SelectValue placeholder="Link Account" />
                                  </SelectTrigger>
                                  <SelectContent className="!bg-white border-slate-200">
                                    {credentials.map(c => (
                                      <SelectItem key={c.uid} value={c.uid} className="!text-slate-900 focus:!bg-slate-100 cursor-pointer">
                                        {c.uid}
                                      </SelectItem>
                                    ))}
                                  </SelectContent>
                                </Select>
                                <Badge variant="outline" className="text-yellow-600 bg-yellow-50 border-yellow-200">Unmapped</Badge>
                              </div>
                            )}
                          </TableCell>

                          <TableCell>
                            {!check ? (
                              <span className="text-slate-400 text-sm italic">Unknown</span>
                            ) : check.status === 'logged_in' ? (
                              <div className="flex flex-col">
                                <Badge className="bg-green-600 w-fit">Logged In</Badge>
                                {check.detected_uid && check.detected_uid !== p.linked_uid && (
                                  <span className="text-[10px] text-red-500 font-bold mt-1">
                                    Mismatch: {check.detected_uid}
                                  </span>
                                )}
                              </div>
                            ) : (
                              <Badge variant="destructive">Logged Out</Badge>
                            )}
                          </TableCell>

                          <TableCell className="text-right flex justify-end gap-2">
                            <Button size="sm" variant="secondary" onClick={() => checkStatus(p.profile_id)} disabled={checking === p.profile_id}>
                              {checking === p.profile_id ? <Loader2 className="w-3 h-3 animate-spin"/> : "Check"}
                            </Button>
                            <Button size="sm" onClick={() => launchProfile(p.profile_id)} disabled={launching === p.profile_id}>
                              {launching === p.profile_id ? <Loader2 className="w-3 h-3 animate-spin"/> : <ExternalLink className="w-3 h-3 mr-1"/>} 
                              Launch
                            </Button>
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          </TabsContent>

          {/* TAB 2: CAMPAIGN */}
          <TabsContent value="campaign" className="pt-4">
             <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
              <Card className="lg:col-span-1 shadow-md border-slate-200">
                <CardHeader className="bg-slate-100/50 border-b border-slate-100 pb-4">
                  <CardTitle className="text-lg">Job Configuration</CardTitle>
                </CardHeader>
                <CardContent className="space-y-6 pt-6">
                  <div className="space-y-2"><Label>Target URL</Label><Input value={url} onChange={e => setUrl(e.target.value)} placeholder="https://..." className="bg-white"/></div>
                  <div className="space-y-2"><Label>Comments</Label><Textarea value={rawComments} onChange={e => setRawComments(e.target.value)} className="min-h-[200px] bg-white"/></div>
                  <Button onClick={generatePreview} disabled={isLoading} className="w-full bg-slate-900 hover:bg-slate-800 disabled:opacity-50">
                    {isLoading ? <><Loader2 className="h-4 w-4 animate-spin mr-2" />Loading...</> : 'Generate Preview'}
                  </Button>
                </CardContent>
              </Card>
              <Card className="lg:col-span-2 shadow-md border-slate-200 min-h-[500px]">
                <CardHeader className="flex flex-row items-center justify-between border-b border-slate-100 bg-slate-100/50 pb-4">
                  <CardTitle className="text-lg">Execution Queue</CardTitle>
                  <Button onClick={startCampaign} disabled={jobs.length === 0 || isProcessing} className="bg-green-600 hover:bg-green-700 text-white">
                    {isProcessing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Play className="mr-2 h-4 w-4" />} Start
                  </Button>
                </CardHeader>
                <CardContent className="p-0">
                  <div className="p-4 border-b border-slate-100 bg-slate-50/50">
                    <h3 className="text-sm font-semibold mb-2">Live Cloud View</h3>
                    <div className="relative aspect-video bg-black rounded-lg overflow-hidden border border-slate-300">
                      {isProcessing ? (
                        <img 
                          src={`${API_BASE}/debug_view/latest.png?t=${Date.now() + tick}`} 
                          className="w-full h-full object-contain"
                          onError={(e) => e.currentTarget.style.display = 'none'}
                        />
                      ) : (
                        <div className="flex items-center justify-center h-full text-slate-500 text-xs">
                          Waiting for active job...
                        </div>
                      )}
                      {isProcessing && (
                        <div className="absolute top-2 right-2">
                          <span className="flex h-2 w-2">
                            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75"></span>
                            <span className="relative inline-flex rounded-full h-2 w-2 bg-red-500"></span>
                          </span>
                        </div>
                      )}
                    </div>
                  </div>
                  <Table>
                    <TableHeader className="bg-slate-50"><TableRow><TableHead>Profile</TableHead><TableHead>Comment</TableHead><TableHead>Status</TableHead></TableRow></TableHeader>
                    <TableBody>
                      {jobs.map(j => (
                        <TableRow key={j.id}>
                          <TableCell><div className="font-medium">{j.profileName}</div></TableCell>
                          <TableCell className="truncate max-w-[300px]">{j.comment}</TableCell>
                          <TableCell>
                            {j.status === 'success' && <Badge className="bg-green-600">Done</Badge>}
                            {j.status === 'failed' && <Badge variant="destructive" title={j.error}>Failed</Badge>}
                            {j.status === 'pending' && <Badge variant="outline">Pending</Badge>}
                            {j.status === 'running' && <Badge className="bg-blue-500">Running</Badge>}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            </div>
          </TabsContent>

          {/* TAB 3: SESSIONS */}
          <TabsContent value="sessions" className="pt-4">
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
              {/* Extract Session Card */}
              <Card className="lg:col-span-1 shadow-md border-slate-200 h-fit">
                <CardHeader className="bg-slate-100/50 border-b border-slate-100 pb-4">
                  <CardTitle className="flex items-center gap-2">
                    <Download className="w-5 h-5" />
                    Extract Session
                  </CardTitle>
                </CardHeader>
                <CardContent className="pt-4">
                  <p className="text-sm text-slate-600 mb-4">
                    Extract cookies from a logged-in profile. Sessions enable fast automation without re-login.
                  </p>
                  <div className="space-y-2">
                    {unifiedProfiles.map(p => {
                      const hasSession = sessions.some(s =>
                        s.profile_name.toLowerCase().replace(/ /g, '_') === p.profile_name.toLowerCase().replace(/ /g, '_')
                      );
                      return (
                        <div key={p.profile_id} className="flex items-center justify-between p-2 bg-slate-50 rounded">
                          <div>
                            <div className="font-medium text-sm">{p.profile_name}</div>
                            {hasSession && <Badge variant="outline" className="text-green-600 bg-green-50 text-[10px]">Has Session</Badge>}
                          </div>
                          <Button
                            size="sm"
                            variant={hasSession ? "outline" : "default"}
                            onClick={() => extractSession(p.profile_id)}
                            disabled={extracting === p.profile_id}
                          >
                            {extracting === p.profile_id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Download className="w-3 h-3 mr-1" />}
                            {hasSession ? 'Re-extract' : 'Extract'}
                          </Button>
                        </div>
                      );
                    })}
                  </div>
                </CardContent>
              </Card>

              {/* Sessions List */}
              <Card className="lg:col-span-2 shadow-md border-slate-200">
                <CardHeader className="bg-slate-100/50 border-b border-slate-100 pb-4 flex flex-row justify-between items-center">
                  <CardTitle className="flex items-center gap-2">
                    <Key className="w-5 h-5" />
                    Saved Sessions
                  </CardTitle>
                  <Button variant="outline" size="sm" onClick={fetchSessions}>
                    <RefreshCw className="w-4 h-4 mr-2" /> Refresh
                  </Button>
                </CardHeader>
                <CardContent className="p-0">
                  {sessions.length === 0 ? (
                    <div className="p-8 text-center text-slate-500">
                      <Key className="w-12 h-12 mx-auto mb-4 opacity-20" />
                      <p>No sessions saved yet.</p>
                      <p className="text-sm">Extract a session from a logged-in profile to enable fast automation.</p>
                    </div>
                  ) : (
                    <Table>
                      <TableHeader className="bg-slate-50">
                        <TableRow>
                          <TableHead>Profile</TableHead>
                          <TableHead>FB User ID</TableHead>
                          <TableHead>Extracted</TableHead>
                          <TableHead>Status</TableHead>
                          <TableHead className="text-right">Actions</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {sessions.map(s => {
                          const validation = sessionValidations[s.profile_name];
                          return (
                            <TableRow key={s.file}>
                              <TableCell>
                                <div className="font-medium">{s.profile_name}</div>
                                <div className="text-xs text-slate-500 font-mono">{s.file}</div>
                              </TableCell>
                              <TableCell>
                                <code className="text-sm bg-slate-100 px-2 py-0.5 rounded">{s.user_id || 'N/A'}</code>
                              </TableCell>
                              <TableCell>
                                <div className="text-sm">{new Date(s.extracted_at).toLocaleDateString()}</div>
                                <div className="text-xs text-slate-500">{new Date(s.extracted_at).toLocaleTimeString()}</div>
                              </TableCell>
                              <TableCell>
                                {!validation ? (
                                  <Badge variant="outline" className="text-slate-500">Not Validated</Badge>
                                ) : validation.valid ? (
                                  <div className="flex items-center gap-1">
                                    <CheckCircle className="w-4 h-4 text-green-600" />
                                    <Badge className="bg-green-600">Valid</Badge>
                                  </div>
                                ) : (
                                  <div className="flex items-center gap-1">
                                    <XCircle className="w-4 h-4 text-red-500" />
                                    <Badge variant="destructive">Expired</Badge>
                                  </div>
                                )}
                              </TableCell>
                              <TableCell className="text-right">
                                <div className="flex justify-end gap-2">
                                  <Button
                                    size="sm"
                                    variant="secondary"
                                    onClick={() => validateSession(s.profile_name)}
                                    disabled={validating === s.profile_name}
                                  >
                                    {validating === s.profile_name ? <Loader2 className="w-3 h-3 animate-spin" /> : 'Validate'}
                                  </Button>
                                  <Button
                                    size="icon"
                                    variant="ghost"
                                    className="h-8 w-8 text-red-500"
                                    onClick={() => deleteSession(s.profile_name)}
                                    disabled={deleting === s.profile_name}
                                  >
                                    {deleting === s.profile_name ? <Loader2 className="w-3 h-3 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                                  </Button>
                                </div>
                              </TableCell>
                            </TableRow>
                          );
                        })}
                      </TableBody>
                    </Table>
                  )}
                </CardContent>
              </Card>
            </div>
          </TabsContent>

          {/* TAB 4: VAULT */}
          <TabsContent value="vault" className="pt-4">
             <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
                <Card className="lg:col-span-1 shadow-md border-slate-200 h-fit">
                    <CardHeader className="bg-slate-100/50 border-b border-slate-100 pb-4"><CardTitle>Add Credential</CardTitle></CardHeader>
                    <CardContent className="space-y-4 pt-6">
                        <div className="space-y-2"><Label>UID</Label><Input value={newUid} onChange={e => setNewUid(e.target.value)} className="bg-white"/></div>
                        <div className="space-y-2"><Label>Password</Label><Input value={newPass} onChange={e => setNewPass(e.target.value)} type="password" className="bg-white"/></div>
                        <div className="space-y-2"><Label>Secret</Label><Input value={newSecret} onChange={e => setNewSecret(e.target.value)} className="bg-white font-mono"/></div>
                        <Button onClick={addCredential} className="w-full bg-slate-900" disabled={!newUid || !newPass}><Plus className="w-4 h-4 mr-2"/> Add</Button>
                    </CardContent>
                </Card>
                <Card className="lg:col-span-2 shadow-md border-slate-200">
                    <CardHeader className="bg-slate-100/50 border-b border-slate-100 pb-4"><CardTitle>Stored Accounts</CardTitle></CardHeader>
                    <CardContent className="p-0">
                        <Table>
                            <TableHeader className="bg-slate-50"><TableRow><TableHead>UID</TableHead><TableHead>Password</TableHead><TableHead>2FA Secret</TableHead><TableHead>OTP Code</TableHead><TableHead className="text-right">Action</TableHead></TableRow></TableHeader>
                            <TableBody>
                                {credentials.map(c => (
                                    <TableRow key={c.uid}>
                                        <TableCell className="font-mono text-sm">{c.uid}</TableCell>
                                        <TableCell className="font-mono text-slate-500 text-sm">{c.password}</TableCell>
                                        <TableCell>{c.secret ? <Badge variant="outline" className="bg-green-50 text-green-700 border-green-200">Set</Badge> : <Badge variant="outline">Missing</Badge>}</TableCell>
                                        <TableCell>
                                          {c.secret ? (
                                            <div className="flex items-center gap-2">
                                              {otpMap[c.uid] ? (
                                                <div className="flex items-center gap-2">
                                                  <code
                                                    className="text-lg font-bold bg-slate-900 text-green-400 px-3 py-1.5 rounded cursor-pointer hover:bg-slate-800 transition-colors select-all"
                                                    onClick={() => copyOtp(c.uid)}
                                                    title="Click to copy"
                                                  >
                                                    {otpMap[c.uid]}
                                                  </code>
                                                  {copiedUid === c.uid && (
                                                    <span className="text-green-600 text-xs font-medium animate-pulse">Copied!</span>
                                                  )}
                                                  <Button
                                                    size="sm"
                                                    variant="ghost"
                                                    className="h-6 px-2 text-xs text-slate-500 hover:text-blue-600"
                                                    onClick={() => fetchOtp(c.uid)}
                                                    title="Refresh OTP"
                                                  >
                                                    <RefreshCw className="w-3 h-3" />
                                                  </Button>
                                                </div>
                                              ) : (
                                                <Button
                                                  size="sm"
                                                  variant="default"
                                                  className="bg-blue-600 hover:bg-blue-700"
                                                  onClick={() => fetchOtp(c.uid)}
                                                >
                                                  <Key className="w-3 h-3 mr-1" />
                                                  Get OTP
                                                </Button>
                                              )}
                                            </div>
                                          ) : (
                                            <span className="text-slate-400 text-sm italic">No 2FA</span>
                                          )}
                                        </TableCell>
                                        <TableCell className="text-right"><Button size="icon" variant="ghost" className="h-8 w-8 text-red-500 hover:text-red-700 hover:bg-red-50" onClick={() => deleteCredential(c.uid)}><Trash2 className="w-4 h-4"/></Button></TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
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
