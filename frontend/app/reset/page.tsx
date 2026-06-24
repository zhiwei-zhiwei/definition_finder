"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useAuth } from "@/components/AuthContext";
import { PasswordChecklist } from "@/components/LoginModal";

const SYMBOL_RE = /[!"#$%&'()*+,\-./:;<=>?@[\\\]^_`{|}~]/;
function passwordOk(pw: string): boolean {
  return pw.length >= 8 && /[A-Z]/.test(pw) && SYMBOL_RE.test(pw);
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full w-full items-center justify-center p-6">
      <div className="bg-surface-container-lowest rounded-xl ambient-shadow w-full max-w-md p-8 flex flex-col gap-4">
        {children}
      </div>
    </div>
  );
}

function ResetInner() {
  const router = useRouter();
  const params = useSearchParams();
  const { resetPassword } = useAuth();
  const token = params.get("token");

  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  if (!token) {
    return (
      <Shell>
        <h1 className="font-headline text-lg font-semibold text-on-surface">
          Invalid reset link
        </h1>
        <p className="text-sm text-on-surface-variant">
          This link is missing its token. Request a new password reset from the
          sign-in screen.
        </p>
        <button
          type="button"
          onClick={() => router.push("/")}
          className="gradient-primary text-white text-sm font-medium px-4 py-2.5 rounded-xl hover:opacity-90"
        >
          Go to sign in
        </button>
      </Shell>
    );
  }

  if (done) {
    return (
      <Shell>
        <div className="flex items-center gap-2">
          <span className="material-symbols-outlined text-green-600">
            check_circle
          </span>
          <h1 className="font-headline text-lg font-semibold text-on-surface">
            Password updated
          </h1>
        </div>
        <p className="text-sm text-on-surface-variant">
          You&apos;re signed in. Taking you to LexisAI…
        </p>
      </Shell>
    );
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!passwordOk(password)) {
      setError(
        "Password must be 8+ chars with an uppercase letter and a symbol.",
      );
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await resetPassword(token!, password);
      setDone(true);
      setTimeout(() => router.push("/"), 1000);
    } catch (err: any) {
      const d = String(err?.message ?? "");
      setError(
        d === "invalid_token"
          ? "This reset link is invalid or has expired. Request a new one."
          : d === "password_weak"
            ? "Password must be 8+ chars with an uppercase letter and a symbol."
            : "Could not reset your password. Try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell>
      <h1 className="font-headline text-lg font-semibold text-on-surface">
        Set a new password
      </h1>
      <form onSubmit={submit} className="flex flex-col gap-3">
        <label className="flex flex-col">
          <span className="text-xs font-medium text-on-surface-variant mb-1">
            New password
          </span>
          <input
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={busy}
            placeholder="••••••••"
            className="w-full bg-surface-container-low border border-outline-variant/40 rounded-md p-3 text-sm text-on-surface placeholder:text-outline-variant focus:outline-none focus:ring-2 focus:ring-primary/30"
          />
        </label>
        <PasswordChecklist password={password} />

        {error && (
          <div className="text-xs text-error bg-error-container/40 rounded-md px-3 py-2">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={busy || !passwordOk(password)}
          className="gradient-primary text-white text-sm font-medium px-4 py-2.5 rounded-xl hover:opacity-90 disabled:opacity-50"
        >
          {busy ? "Updating…" : "Update password"}
        </button>
      </form>
    </Shell>
  );
}

export default function ResetPage() {
  return (
    <Suspense fallback={<Shell>Loading…</Shell>}>
      <ResetInner />
    </Suspense>
  );
}
