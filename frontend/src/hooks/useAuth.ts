"use client";

import { useCallback, useEffect, useState } from "react";
import {
  UserProfile,
  clearTokens,
  fetchProfile,
  getAccessToken,
  login as authLogin,
  logout as authLogout,
} from "@/lib/auth";

export function useAuth() {
  const [user, setUser] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    if (!getAccessToken()) {
      setUser(null);
      setLoading(false);
      return;
    }
    try {
      setUser(await fetchProfile());
    } catch {
      clearTokens();
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const login = async (email: string, password: string) => {
    await authLogin(email, password);
    await load();
  };

  const logout = async () => {
    await authLogout();
    setUser(null);
  };

  return { user, loading, login, logout, isAuthenticated: !!user };
}
