/**
 * Admin Tab Component
 * Main container for the admin portal (admin only)
 */

import { useState } from 'react';
import { CreateUserForm } from './CreateUserForm';
import { UserList } from './UserList';

export function AdminTab() {
  const [refreshTrigger, setRefreshTrigger] = useState(0);

  const handleUserCreated = () => {
    setRefreshTrigger(prev => prev + 1);
  };

  const handleRefresh = () => {
    setRefreshTrigger(prev => prev + 1);
  };

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <CreateUserForm onUserCreated={handleUserCreated} />
        <UserList refreshTrigger={refreshTrigger} onRefresh={handleRefresh} />
      </div>
    </div>
  );
}
