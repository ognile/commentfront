/**
 * Authentication Context for CommentBot
 * Provides auth state and methods throughout the app
 */

import { createContext, useContext, useState, useEffect, useCallback, type ReactNode } from 'react';
import {
  getAccessToken,
  getRefreshToken,
  clearTokens,
  isTokenExpired,
  setAccessToken,
  setRefreshToken,
} from '@/lib/auth';
import { API_BASE, setLogoutCallback } from '@/lib/api';

export interface User {
  username: string;
  role: 'admin' | 'user';
  is_active: boolean;
  created_at: string | null;
  last_login: string | null;
}

interface AuthState {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  error: string | null;
}

interface AuthContextType extends AuthState {
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  refreshAuth: () => Promise<boolean>;
}

const AuthContext = createContext<AuthContextType | null>(null);

export function useAuth(): AuthContextType {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}

interface AuthProviderProps {
  children: ReactNode;
}

export function AuthProvider({ children }: AuthProviderProps) {
  const [state, setState] = useState<AuthState>({
    user: null,
    isAuthenticated: false,
    isLoading: true,
    error: null,
  });

  // Fetch current user info
  const fetchUser = useCallback(async (accessToken: string): Promise<User | null> => {
    try {
      const response = await fetch(`${API_BASE}/auth/me`, {
        headers: {
          Authorization: `Bearer ${accessToken}`,
        },
      });

      if (response.ok) {
        return await response.json();
      }
    } catch (error) {
      console.error('Failed to fetch user:', error);
    }
    return null;
  }, []);

  // Refresh access token
  const refreshAuth = useCallback(async (): Promise<boolean> => {
    const refreshToken = getRefreshToken();
    if (!refreshToken) {
      return false;
    }

    try {
      const response = await fetch(`${API_BASE}/auth/refresh`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });

      if (response.ok) {
        const data = await response.json();
        setAccessToken(data.access_token);
        setRefreshToken(data.refresh_token);

        const user = await fetchUser(data.access_token);
        if (user) {
          setState({
            user,
            isAuthenticated: true,
            isLoading: false,
            error: null,
          });
          return true;
        }
      }
    } catch (error) {
      console.error('Token refresh failed:', error);
    }

    return false;
  }, [fetchUser]);

  // Logout
  const logout = useCallback(() => {
    clearTokens();
    setState({
      user: null,
      isAuthenticated: false,
      isLoading: false,
      error: null,
    });
  }, []);

  // Login
  const login = useCallback(async (username: string, password: string): Promise<void> => {
    setState((prev) => ({ ...prev, isLoading: true, error: null }));

    try {
      const response = await fetch(`${API_BASE}/auth/login`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ username, password }),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || 'Login failed');
      }

      const data = await response.json();
      setAccessToken(data.access_token);
      setRefreshToken(data.refresh_token);

      const user = await fetchUser(data.access_token);
      if (user) {
        setState({
          user,
          isAuthenticated: true,
          isLoading: false,
          error: null,
        });
      } else {
        throw new Error('Failed to fetch user info');
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Login failed';
      setState((prev) => ({
        ...prev,
        isLoading: false,
        error: message,
      }));
      throw error;
    }
  }, [fetchUser]);

  // Initialize auth state on mount
  useEffect(() => {
    const initAuth = async () => {
      const accessToken = getAccessToken();
      const refreshToken = getRefreshToken();

      if (!accessToken && !refreshToken) {
        setState((prev) => ({ ...prev, isLoading: false }));
        return;
      }

      // Try to use existing access token
      if (accessToken && !isTokenExpired(accessToken)) {
        const user = await fetchUser(accessToken);
        if (user) {
          setState({
            user,
            isAuthenticated: true,
            isLoading: false,
            error: null,
          });
          return;
        }
      }

      // Try to refresh
      if (refreshToken) {
        const success = await refreshAuth();
        if (success) return;
      }

      // All failed, clear tokens
      clearTokens();
      setState((prev) => ({ ...prev, isLoading: false }));
    };

    initAuth();
  }, [fetchUser, refreshAuth]);

  // Set logout callback for API module
  useEffect(() => {
    setLogoutCallback(logout);
  }, [logout]);

  // Proactive token refresh interval
  useEffect(() => {
    if (!state.isAuthenticated) return;

    const interval = setInterval(async () => {
      const accessToken = getAccessToken();
      // Refresh if token expires in less than 5 minutes
      if (accessToken && isTokenExpired(accessToken, 5 * 60 * 1000)) {
        await refreshAuth();
      }
    }, 60 * 1000); // Check every minute

    return () => clearInterval(interval);
  }, [state.isAuthenticated, refreshAuth]);

  const value: AuthContextType = {
    ...state,
    login,
    logout,
    refreshAuth,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
