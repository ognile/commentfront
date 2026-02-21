/**
 * API client with authentication handling
 */

import { getAccessToken, getRefreshToken, setAccessToken, setRefreshToken, clearTokens, isTokenExpired } from './auth';

export const API_BASE = import.meta.env.VITE_API_BASE || "https://commentbot-production.up.railway.app";
export const WS_BASE = API_BASE.replace('https://', 'wss://').replace('http://', 'ws://');

interface ApiOptions extends RequestInit {
  skipAuth?: boolean;
  _isRetry?: boolean;
}

// Callback to handle logout when tokens are invalid
let logoutCallback: (() => void) | null = null;

export function setLogoutCallback(callback: () => void) {
  logoutCallback = callback;
}

function normalizeApiError(value: unknown, fallback: string): string {
  if (typeof value === 'string' && value.trim()) return value;
  if (Array.isArray(value)) {
    const joined = value.map((v) => normalizeApiError(v, '')).filter(Boolean).join('; ');
    return joined || fallback;
  }
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>;
    if (typeof record.message === 'string' && record.message.trim()) return record.message;
    if (typeof record.detail === 'string' && record.detail.trim()) return record.detail;
    if (Array.isArray(record.errors) && record.errors.length > 0) {
      return normalizeApiError(record.errors, fallback);
    }
    try {
      return JSON.stringify(value);
    } catch {
      return fallback;
    }
  }
  return fallback;
}

/**
 * Refresh access token using refresh token
 */
async function refreshAccessToken(): Promise<boolean> {
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
      return true;
    }
  } catch (error) {
    console.error('Token refresh failed:', error);
  }

  return false;
}

/**
 * Make an authenticated API request
 * Automatically adds Authorization header and handles token refresh
 */
export async function apiFetch<T>(
  endpoint: string,
  options: ApiOptions = {}
): Promise<T> {
  const { skipAuth, _isRetry, ...fetchOptions } = options;

  // Build headers
  const headers: Record<string, string> = {
    ...((fetchOptions.headers as Record<string, string>) || {}),
  };

  // Add Content-Type for JSON bodies
  if (fetchOptions.body && typeof fetchOptions.body === 'string') {
    headers['Content-Type'] = headers['Content-Type'] || 'application/json';
  }

  // Add auth header if not skipped
  if (!skipAuth) {
    let accessToken = getAccessToken();

    // Check if token needs refresh (5 minute margin)
    if (accessToken && isTokenExpired(accessToken, 5 * 60 * 1000)) {
      const refreshed = await refreshAccessToken();
      if (refreshed) {
        accessToken = getAccessToken();
      }
    }

    if (accessToken) {
      headers['Authorization'] = `Bearer ${accessToken}`;
    }
  }

  const response = await fetch(`${API_BASE}${endpoint}`, {
    ...fetchOptions,
    headers,
  });

  // Handle 401 - try to refresh token and retry once
  if (response.status === 401 && !skipAuth && !_isRetry) {
    const refreshed = await refreshAccessToken();
    if (refreshed) {
      return apiFetch<T>(endpoint, { ...options, _isRetry: true });
    } else {
      // Refresh failed, logout
      clearTokens();
      if (logoutCallback) {
        logoutCallback();
      }
      throw new Error('Session expired. Please login again.');
    }
  }

  // Handle other errors
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(normalizeApiError(errorData?.detail ?? errorData, `Request failed: ${response.status}`));
  }

  // Return empty object for 204 No Content
  if (response.status === 204) {
    return {} as T;
  }

  return response.json();
}

/**
 * Login and store tokens
 */
export async function login(username: string, password: string): Promise<{ access_token: string; refresh_token: string }> {
  const response = await fetch(`${API_BASE}/auth/login`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ username, password }),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(normalizeApiError(errorData?.detail ?? errorData, 'Login failed'));
  }

  const data = await response.json();
  setAccessToken(data.access_token);
  setRefreshToken(data.refresh_token);
  return data;
}

/**
 * Get WebSocket URL with authentication token
 */
export function getAuthenticatedWsUrl(path: string): string {
  const accessToken = getAccessToken();
  if (!accessToken) {
    throw new Error('No access token available');
  }
  const separator = path.includes('?') ? '&' : '?';
  return `${WS_BASE}${path}${separator}token=${accessToken}`;
}

/**
 * Create an authenticated WebSocket connection
 */
export function createAuthenticatedWebSocket(path: string): WebSocket {
  const url = getAuthenticatedWsUrl(path);
  return new WebSocket(url);
}
