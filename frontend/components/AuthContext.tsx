"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import {
  AuthUser,
  LoginResponse,
  apiClaim,
  apiLoginPassword,
  apiLogout,
  apiMe,
  apiRequestCode,
  apiSignup,
  apiVerifyCode,
  clearAnonDocId,
  getAnonDocId,
  setToken,
} from "@/lib/auth";

type ClaimResult = { claimedDocIds: string[] };

type AuthContextValue = {
  user: AuthUser | null;
  ready: boolean;
  signup: (input: {
    email: string;
    username: string;
    password: string;
  }) => Promise<ClaimResult>;
  loginWithPassword: (input: {
    email: string;
    password: string;
  }) => Promise<ClaimResult>;
  loginWithCode: (input: {
    email: string;
    code: string;
  }) => Promise<ClaimResult>;
  requestCode: (email: string) => Promise<void>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

async function claimAnonIfAny(): Promise<string[]> {
  const anonId = getAnonDocId();
  if (!anonId) return [];
  try {
    return await apiClaim([anonId]);
  } catch {
    return [];
  } finally {
    clearAnonDocId();
  }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    apiMe()
      .then((u) => {
        if (!cancelled) setUser(u);
      })
      .finally(() => {
        if (!cancelled) setReady(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const finishLogin = useCallback(
    async (res: LoginResponse): Promise<ClaimResult> => {
      setToken(res.token);
      setUser({
        user_id: res.user_id,
        email: res.email,
        username: res.username,
      });
      const claimedDocIds = await claimAnonIfAny();
      return { claimedDocIds };
    },
    [],
  );

  const signup = useCallback(
    async (input: { email: string; username: string; password: string }) => {
      const res = await apiSignup(input);
      return finishLogin(res);
    },
    [finishLogin],
  );

  const loginWithPassword = useCallback(
    async (input: { email: string; password: string }) => {
      const res = await apiLoginPassword(input);
      return finishLogin(res);
    },
    [finishLogin],
  );

  const loginWithCode = useCallback(
    async (input: { email: string; code: string }) => {
      const res = await apiVerifyCode(input);
      return finishLogin(res);
    },
    [finishLogin],
  );

  const requestCode = useCallback(async (email: string) => {
    await apiRequestCode(email);
  }, []);

  const logout = useCallback(async () => {
    await apiLogout();
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider
      value={{
        user,
        ready,
        signup,
        loginWithPassword,
        loginWithCode,
        requestCode,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
