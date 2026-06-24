"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useAuth } from "@/components/AuthContext";

type State = "working" | "success" | "error";

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full w-full items-center justify-center p-6">
      <div className="bg-surface-container-lowest rounded-xl ambient-shadow w-full max-w-md p-8 flex flex-col items-center gap-4 text-center">
        {children}
      </div>
    </div>
  );
}

function VerifyInner() {
  const router = useRouter();
  const params = useSearchParams();
  const { verifyEmail } = useAuth();
  const [state, setState] = useState<State>("working");
  // Guard against React's double-effect in dev — the token is one-time-use, so
  // a second call would consume it and flip a success into an error.
  const ran = useRef(false);

  useEffect(() => {
    if (ran.current) return;
    ran.current = true;
    const token = params.get("token");
    if (!token) {
      setState("error");
      return;
    }
    verifyEmail(token)
      .then(() => {
        setState("success");
        setTimeout(() => router.push("/"), 1200);
      })
      .catch(() => setState("error"));
  }, [params, router, verifyEmail]);

  if (state === "working") {
    return (
      <Centered>
        <span className="material-symbols-outlined text-3xl text-blue-700 animate-pulse">
          mark_email_read
        </span>
        <h1 className="font-headline text-lg font-semibold text-on-surface">
          Verifying your email…
        </h1>
      </Centered>
    );
  }

  if (state === "success") {
    return (
      <Centered>
        <span className="material-symbols-outlined text-3xl text-green-600">
          check_circle
        </span>
        <h1 className="font-headline text-lg font-semibold text-on-surface">
          Email verified
        </h1>
        <p className="text-sm text-on-surface-variant">
          Your account is active. Taking you to LexisAI…
        </p>
      </Centered>
    );
  }

  return (
    <Centered>
      <span className="material-symbols-outlined text-3xl text-error">
        error
      </span>
      <h1 className="font-headline text-lg font-semibold text-on-surface">
        Verification link invalid
      </h1>
      <p className="text-sm text-on-surface-variant">
        This link is invalid or has expired. Sign in and request a new
        verification email.
      </p>
      <button
        type="button"
        onClick={() => router.push("/")}
        className="gradient-primary text-white text-sm font-medium px-4 py-2.5 rounded-xl hover:opacity-90"
      >
        Go to sign in
      </button>
    </Centered>
  );
}

export default function VerifyPage() {
  return (
    <Suspense fallback={<Centered>Loading…</Centered>}>
      <VerifyInner />
    </Suspense>
  );
}
