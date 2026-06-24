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
  SignupResult,
  apiClaim,
  apiForgotPassword,
  apiLoginPassword,
  apiLogout,
  apiMe,
  apiRequestCode,
  apiResendVerification,
  apiResetPassword,
  apiSignup,
  apiVerifyCode,
  apiVerifyEmail,
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
  }) => Promise<SignupResult>;
  loginWithPassword: (input: {
    email: string;
    password: string;
  }) => Promise<ClaimResult>;
  loginWithCode: (input: {
    email: string;
    code: string;
  }) => Promise<ClaimResult>;
  requestCode: (email: string) => Promise<void>;
  resendVerification: (email: string) => Promise<void>;
  verifyEmail: (token: string) => Promise<ClaimResult>;
  requestPasswordReset: (email: string) => Promise<void>;
  resetPassword: (token: string, password: string) => Promise<ClaimResult>;
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
      // Does NOT sign in — returns the "verification email sent" state. The
      // session is established later by verifyEmail() when the link is clicked.
      return apiSignup(input);
    },
    [],
  );

  const verifyEmail = useCallback(
    async (token: string) => {
      const res = await apiVerifyEmail(token);
      return finishLogin(res);
    },
    [finishLogin],
  );

  const requestPasswordReset = useCallback(async (email: string) => {
    await apiForgotPassword(email);
  }, []);

  const resetPassword = useCallback(
    async (token: string, password: string) => {
      const res = await apiResetPassword(token, password);
      return finishLogin(res);
    },
    [finishLogin],
  );

  const resendVerification = useCallback(async (email: string) => {
    await apiResendVerification(email);
  }, []);

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
        resendVerification,
        verifyEmail,
        requestPasswordReset,
        resetPassword,
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
