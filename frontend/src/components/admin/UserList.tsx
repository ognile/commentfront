/**
 * User List Component
 * Displays all users with management actions (admin only)
 */

import { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Loader2, RefreshCw, Trash2, Key, Users, Shield, User as UserIcon } from 'lucide-react';
import { apiFetch } from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';
import { ChangePasswordDialog } from './ChangePasswordDialog';

interface User {
  username: string;
  role: 'admin' | 'user';
  is_active: boolean;
  created_at: string | null;
  last_login: string | null;
}

interface UserListProps {
  refreshTrigger: number;
  onRefresh: () => void;
}

export function UserList({ refreshTrigger, onRefresh }: UserListProps) {
  const { user: currentUser } = useAuth();
  const [users, setUsers] = useState<User[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [changePasswordTarget, setChangePasswordTarget] = useState<string | null>(null);

  const fetchUsers = async () => {
    setIsLoading(true);
    try {
      const data = await apiFetch<User[]>('/users');
      setUsers(data);
    } catch (error) {
      console.error('Failed to fetch users:', error);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchUsers();
  }, [refreshTrigger]);

  const handleDelete = async () => {
    if (!deleteTarget) return;

    setIsDeleting(true);
    try {
      await apiFetch(`/users/${encodeURIComponent(deleteTarget)}`, {
        method: 'DELETE',
      });
      fetchUsers();
      onRefresh();
    } catch (error) {
      alert(error instanceof Error ? error.message : 'Failed to delete user');
    } finally {
      setIsDeleting(false);
      setDeleteTarget(null);
    }
  };

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return 'Never';
    const date = new Date(dateStr);
    return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  };

  const canDelete = (username: string) => {
    // Can't delete yourself
    if (username === currentUser?.username) return false;
    // Check if this is the last admin
    const adminCount = users.filter(u => u.role === 'admin').length;
    const targetUser = users.find(u => u.username === username);
    if (targetUser?.role === 'admin' && adminCount <= 1) return false;
    return true;
  };

  return (
    <>
      <Card>
        <CardHeader className="border-b border-[rgba(0,0,0,0.1)] pb-4 flex flex-row justify-between items-center">
          <CardTitle className="text-lg flex items-center gap-2">
            <Users className="w-5 h-5" />
            Team Members ({users.length})
          </CardTitle>
          <Button size="sm" variant="outline" onClick={fetchUsers} disabled={isLoading}>
            <RefreshCw className={`w-4 h-4 mr-2 ${isLoading ? 'animate-spin' : ''}`} />
            Refresh
          </Button>
        </CardHeader>
        <CardContent className="p-0">
          {isLoading ? (
            <div className="p-8 text-center text-[#999999]">
              <Loader2 className="w-8 h-8 animate-spin mx-auto mb-4 text-[#666666]" />
              Loading users...
            </div>
          ) : users.length === 0 ? (
            <div className="p-8 text-center text-[#999999]">
              No users found.
            </div>
          ) : (
            <div className="divide-y divide-[rgba(0,0,0,0.1)]">
              {users.map((user) => (
                <div
                  key={user.username}
                  className={`p-4 flex items-center justify-between hover:bg-[rgba(51,51,51,0.04)] ${
                    user.username === currentUser?.username ? 'bg-[rgba(51,51,51,0.06)]' : ''
                  }`}
                >
                  <div className="flex items-center gap-3">
                    <div className={`w-10 h-10 rounded-full flex items-center justify-center ${
                      user.role === 'admin' ? 'bg-[rgba(245,158,11,0.15)]' : 'bg-[rgba(51,51,51,0.08)]'
                    }`}>
                      {user.role === 'admin' ? (
                        <Shield className="w-5 h-5 text-[#f59e0b]" />
                      ) : (
                        <UserIcon className="w-5 h-5 text-[#666666]" />
                      )}
                    </div>
                    <div>
                      <div className="font-medium text-[#111111] flex items-center gap-2">
                        {user.username}
                        {user.username === currentUser?.username && (
                          <Badge variant="outline" className="text-xs">You</Badge>
                        )}
                      </div>
                      <div className="text-xs text-[#999999]">
                        Created: {formatDate(user.created_at)} | Last login: {formatDate(user.last_login)}
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge variant={user.role === 'admin' ? 'default' : 'secondary'} className="capitalize">
                      {user.role}
                    </Badge>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => setChangePasswordTarget(user.username)}
                    >
                      <Key className="w-3 h-3 mr-1" />
                      Password
                    </Button>
                    {canDelete(user.username) && (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => setDeleteTarget(user.username)}
                      >
                        <Trash2 className="w-3 h-3 text-[#ef4444]" />
                      </Button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Delete Confirmation Dialog */}
      <AlertDialog open={!!deleteTarget} onOpenChange={(open: boolean) => !open && setDeleteTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete User</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete user <span className="font-medium">{deleteTarget}</span>?
              This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDelete}
              className="bg-[#ef4444] hover:opacity-85"
              disabled={isDeleting}
            >
              {isDeleting ? (
                <>
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  Deleting...
                </>
              ) : (
                'Delete User'
              )}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Change Password Dialog */}
      <ChangePasswordDialog
        username={changePasswordTarget}
        onClose={() => setChangePasswordTarget(null)}
        onSuccess={() => {
          alert('Password changed successfully!');
        }}
      />
    </>
  );
}
