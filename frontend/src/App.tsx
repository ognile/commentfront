import { useState, useEffect } from 'react'
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Loader2, Send, CheckCircle, XCircle, RefreshCw } from "lucide-react"

const API_BASE = "https://commentbot-production.up.railway.app";

interface Session {
  file: string;
  profile_name: string;
  user_id: string | null;
  extracted_at: string;
  valid: boolean;
}

interface Job {
  profile_name: string;
  comment: string;
  status: 'pending' | 'success' | 'failed';
}

function App() {
  const [url, setUrl] = useState('');
  const [comments, setComments] = useState('');
  const [sessions, setSessions] = useState<Session[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [isProcessing, setIsProcessing] = useState(false);
  const [loading, setLoading] = useState(true);

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

  useEffect(() => {
    fetchSessions();
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
      
      // Update job statuses
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

  return (
    <div className="min-h-screen bg-slate-50 p-8 font-sans">
      <div className="max-w-[1200px] mx-auto space-y-8">
        
        {/* Header */}
        <div className="flex justify-between items-center">
          <div>
            <h1 className="text-3xl font-bold tracking-tight text-slate-900">CommentBot</h1>
            <p className="text-slate-500 mt-2">Streamlined Facebook Comment Automation</p>
          </div>
          <div className="flex items-center gap-2">
            <div className={`h-3 w-3 rounded-full ${loading ? 'bg-yellow-500 animate-pulse' : isProcessing ? 'bg-blue-500 animate-pulse' : 'bg-green-500'}`} />
            <span className="text-sm font-medium text-slate-700">
              {loading ? 'Loading...' : isProcessing ? 'Processing' : 'Ready'}
            </span>
          </div>
        </div>

        {/* Campaign Form */}
        <Card className="shadow-md border-slate-200">
          <CardHeader className="bg-slate-100/50 border-b border-slate-100 pb-4">
            <CardTitle className="text-lg">Campaign</CardTitle>
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

        {/* Jobs Preview */}
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

        {/* Sessions */}
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

      </div>
    </div>
  )
}

export default App
