/**
 * Authentication utilities for token management
 */

const ACCESS_TOKEN_KEY = 'commentbot_access_token';
const REFRESH_TOKEN_KEY = 'commentbot_refresh_token';

/**
 * Get the stored access token
 */
export function getAccessToken(): string | null {
  return localStorage.getItem(ACCESS_TOKEN_KEY);
}

/**
 * Store the access token
 */
export function setAccessToken(token: string): void {
  localStorage.setItem(ACCESS_TOKEN_KEY, token);
}

/**
 * Get the stored refresh token
 */
export function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_TOKEN_KEY);
}

/**
 * Store the refresh token
 */
export function setRefreshToken(token: string): void {
  localStorage.setItem(REFRESH_TOKEN_KEY, token);
}

/**
 * Clear all auth tokens (logout)
 */
export function clearTokens(): void {
  localStorage.removeItem(ACCESS_TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
}

/**
 * Parse a JWT token and return its payload
 */
export function parseJwt(token: string): Record<string, unknown> | null {
  try {
    const base64Url = token.split('.')[1];
    const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
    const jsonPayload = decodeURIComponent(
      atob(base64)
        .split('')
        .map((c) => '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2))
        .join('')
    );
    return JSON.parse(jsonPayload);
  } catch {
    return null;
  }
}

/**
 * Get the expiration time of a token in milliseconds
 */
export function getTokenExpiry(token: string): number | null {
  const payload = parseJwt(token);
  if (payload && typeof payload.exp === 'number') {
    return payload.exp * 1000; // Convert to milliseconds
  }
  return null;
}

/**
 * Check if a token is expired or will expire within the given margin (ms)
 */
export function isTokenExpired(token: string, marginMs: number = 0): boolean {
  const expiry = getTokenExpiry(token);
  if (!expiry) return true;
  return Date.now() + marginMs >= expiry;
}

/**
 * Get username from token
 */
export function getUsernameFromToken(token: string): string | null {
  const payload = parseJwt(token);
  if (payload && typeof payload.sub === 'string') {
    return payload.sub;
  }
  return null;
}
