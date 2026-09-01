const TOKEN_KEY = "keenpay_access_token";
const REFRESH_KEY = "keenpay_refresh_token";

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  role: string;
  merchant_id: string;
  user_id: string;
}

export interface UserProfile {
  user_id: string;
  email: string;
  merchant_id: string;
  role: string;
  display_name?: string;
}

export function getAccessToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function getRefreshToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(REFRESH_KEY);
}

export function storeTokens(tokens: TokenPair): void {
  localStorage.setItem(TOKEN_KEY, tokens.access_token);
  localStorage.setItem(REFRESH_KEY, tokens.refresh_token);
}

export function clearTokens(): void {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(REFRESH_KEY);
}

export async function login(email: string, password: string): Promise<TokenPair> {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  const response = await fetch(`${apiUrl}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, merchant_id: "merchant_keen" }),
  });
  if (!response.ok) {
    const err = await response.json();
    throw new Error(err?.error?.message ?? "Login failed");
  }
  const tokens: TokenPair = await response.json();
  storeTokens(tokens);
  return tokens;
}

export async function refreshAccessToken(): Promise<string | null> {
  const refresh = getRefreshToken();
  if (!refresh) return null;
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  const response = await fetch(`${apiUrl}/api/v1/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refresh }),
  });
  if (!response.ok) {
    clearTokens();
    return null;
  }
  const tokens: TokenPair = await response.json();
  storeTokens(tokens);
  return tokens.access_token;
}

export async function fetchProfile(): Promise<UserProfile> {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  const token = getAccessToken();
  const response = await fetch(`${apiUrl}/api/v1/auth/me`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (response.status === 401) {
    const refreshed = await refreshAccessToken();
    if (!refreshed) throw new Error("Session expired");
    return fetchProfile();
  }
  if (!response.ok) throw new Error("Failed to load profile");
  return response.json();
}

export async function logout(): Promise<void> {
  const refresh = getRefreshToken();
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  if (refresh) {
    await fetch(`${apiUrl}/api/v1/auth/revoke`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refresh }),
    });
  }
  clearTokens();
}
