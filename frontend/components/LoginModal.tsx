"use client";

import { useEffect, useRef, useState } from "react";
import { useAuth } from "@/components/AuthContext";

type Props = {
  open: boolean;
  onClose: () => void;
  onLoggedIn?: (claimedDocIds: string[]) => void;
};

type Mode = "signin" | "signup";
type SigninMethod = "password" | "code";
type CodeStep = "request" | "verify";

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const USERNAME_RE = /^[A-Za-z0-9_-]{3,20}$/;
const MIN_PASSWORD = 8;
const SYMBOL_RE = /[!"#$%&'()*+,\-./:;<=>?@[\\\]^_`{|}~]/;
const CODE_LENGTH = 6;

function passwordChecks(pw: string) {
  return {
    length: pw.length >= MIN_PASSWORD,
    upper: /[A-Z]/.test(pw),
    symbol: SYMBOL_RE.test(pw),
  };
}

function allPasswordChecksPass(pw: string): boolean {
  const c = passwordChecks(pw);
  return c.length && c.upper && c.symbol;
}

function mapServerError(detail: string): string {
  // resend_throttle:N comes back as a structured detail; surface the wait.
  if (detail.startsWith("resend_throttle:")) {
    const sec = detail.split(":")[1];
    return `Please wait ${sec}s before requesting another code.`;
  }
  switch (detail) {
    case "email_taken":
      return "An account with this email already exists.";
    case "username_taken":
      return "That username is taken.";
    case "invalid_credentials":
      return "Incorrect email or password.";
    case "invalid_code":
      return "That code is invalid or expired.";
    case "no_account":
      return "No account found for this email.";
    case "password_weak":
      return "Password must be 8+ chars with an uppercase letter and a symbol.";
    default:
      return detail.replace(/_/g, " ");
  }
}

export function LoginModal({ open, onClose, onLoggedIn }: Props) {
  const {
    user,
    signup,
    loginWithPassword,
    loginWithCode,
    requestCode,
    logout,
  } = useAuth();
  const [mode, setMode] = useState<Mode>("signin");
  const [signinMethod, setSigninMethod] = useState<SigninMethod>("password");
  const [codeStep, setCodeStep] = useState<CodeStep>("request");
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showCreateLink, setShowCreateLink] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<{
    email?: string;
    username?: string;
    password?: string;
    code?: string;
  }>({});
  const [resendIn, setResendIn] = useState(0);
  const emailRef = useRef<HTMLInputElement | null>(null);
  const codeRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (open) {
      setMode("signin");
      setSigninMethod("password");
      setCodeStep("request");
      setEmail("");
      setUsername("");
      setPassword("");
      setCode("");
      setError(null);
      setShowCreateLink(false);
      setFieldErrors({});
      setBusy(false);
      setResendIn(0);
      const t = setTimeout(() => emailRef.current?.focus(), 80);
      return () => clearTimeout(t);
    }
  }, [open]);

  // Resend countdown — fires while resendIn > 0.
  useEffect(() => {
    if (resendIn <= 0) return;
    const t = setInterval(() => setResendIn((s) => Math.max(0, s - 1)), 1000);
    return () => clearInterval(t);
  }, [resendIn]);

  // Auto-focus the OTP input when entering verify step.
  useEffect(() => {
    if (signinMethod === "code" && codeStep === "verify") {
      const t = setTimeout(() => codeRef.current?.focus(), 60);
      return () => clearTimeout(t);
    }
  }, [signinMethod, codeStep]);

  function switchMode(next: Mode) {
    setMode(next);
    setPassword("");
    setCode("");
    setError(null);
    setShowCreateLink(false);
    setFieldErrors({});
    setCodeStep("request");
  }

  function switchSigninMethod(next: SigninMethod) {
    setSigninMethod(next);
    setError(null);
    setShowCreateLink(false);
    setFieldErrors({});
    setCodeStep("request");
    setCode("");
  }

  function validate(): boolean {
    const fe: typeof fieldErrors = {};
    const e = email.trim();
    if (!EMAIL_RE.test(e)) fe.email = "Enter a valid email.";

    if (mode === "signup") {
      if (!allPasswordChecksPass(password))
        fe.password =
          "Password must be 8+ chars with an uppercase letter and a symbol.";
      if (!USERNAME_RE.test(username))
        fe.username = "3–20 chars, letters/numbers/_/- only.";
    } else if (signinMethod === "password") {
      if (!password) fe.password = "Enter your password.";
    } else {
      // signinMethod === "code"
      if (codeStep === "verify") {
        if (!/^\d{6}$/.test(code))
          fe.code = `Enter the ${CODE_LENGTH}-digit code from your email.`;
      }
    }
    setFieldErrors(fe);
    return Object.keys(fe).length === 0;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!validate()) return;
    setBusy(true);
    setError(null);
    setShowCreateLink(false);
    try {
      if (mode === "signup") {
        const r = await signup({ email: email.trim(), username, password });
        onLoggedIn?.(r.claimedDocIds);
        onClose();
      } else if (signinMethod === "password") {
        const r = await loginWithPassword({ email: email.trim(), password });
        onLoggedIn?.(r.claimedDocIds);
        onClose();
      } else if (codeStep === "request") {
        await requestCode(email.trim());
        setCodeStep("verify");
        setResendIn(60);
      } else {
        const r = await loginWithCode({ email: email.trim(), code });
        onLoggedIn?.(r.claimedDocIds);
        onClose();
      }
    } catch (err: any) {
      const detail = String(err?.message ?? "request_failed");
      if (mode === "signup" && detail === "email_taken") {
        setFieldErrors((f) => ({ ...f, email: mapServerError(detail) }));
      } else if (mode === "signup" && detail === "username_taken") {
        setFieldErrors((f) => ({ ...f, username: mapServerError(detail) }));
      } else if (mode === "signup" && detail === "password_weak") {
        setFieldErrors((f) => ({ ...f, password: mapServerError(detail) }));
      } else if (signinMethod === "code" && detail === "no_account") {
        setError(mapServerError(detail));
        setShowCreateLink(true);
      } else if (
        signinMethod === "code" &&
        detail.startsWith("resend_throttle:")
      ) {
        const sec = parseInt(detail.split(":")[1] ?? "60", 10);
        setError(mapServerError(detail));
        setResendIn(Number.isFinite(sec) ? sec : 60);
      } else if (signinMethod === "code" && detail === "invalid_code") {
        setFieldErrors((f) => ({ ...f, code: mapServerError(detail) }));
        setCode("");
      } else {
        setError(mapServerError(detail));
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleResend() {
    if (resendIn > 0 || busy) return;
    setBusy(true);
    setError(null);
    setFieldErrors((f) => ({ ...f, code: undefined }));
    try {
      await requestCode(email.trim());
      setResendIn(60);
      setCode("");
    } catch (err: any) {
      const detail = String(err?.message ?? "request_failed");
      if (detail.startsWith("resend_throttle:")) {
        const sec = parseInt(detail.split(":")[1] ?? "60", 10);
        setResendIn(Number.isFinite(sec) ? sec : 60);
      }
      setError(mapServerError(detail));
    } finally {
      setBusy(false);
    }
  }

  const isAlreadyIn = !!user;

  // Submit button label — varies across the four sub-flows.
  const submitLabel = (() => {
    if (busy) {
      if (mode === "signup") return "Creating account…";
      if (signinMethod === "password") return "Signing in…";
      if (codeStep === "request") return "Sending code…";
      return "Verifying…";
    }
    if (mode === "signup") return "Create account";
    if (signinMethod === "password") return "Sign in";
    if (codeStep === "request") return "Send code";
    return "Verify & sign in";
  })();

  return (
    <>
      <div
        className={`fixed inset-0 bg-black/30 z-[60] transition-opacity ${
          open
            ? "opacity-100 pointer-events-auto"
            : "opacity-0 pointer-events-none"
        }`}
        onClick={onClose}
      />
      <div
        className={`fixed inset-0 z-[61] flex items-center justify-center p-4 transition-opacity ${
          open
            ? "opacity-100 pointer-events-auto"
            : "opacity-0 pointer-events-none"
        }`}
      >
        <div
          onClick={(e) => e.stopPropagation()}
          className="bg-surface-container-lowest rounded-xl ambient-shadow w-full max-w-md flex flex-col"
        >
          <div className="flex items-center justify-between px-6 pt-6 pb-2">
            <h2 className="font-headline text-lg font-semibold text-on-surface">
              {isAlreadyIn
                ? "You're signed in"
                : mode === "signup"
                  ? "Create an account"
                  : "Sign in to LexisAI"}
            </h2>
            <button
              type="button"
              onClick={onClose}
              className="p-1 rounded-lg hover:bg-surface-container text-on-surface-variant"
              aria-label="Close"
            >
              <span className="material-symbols-outlined text-sm">close</span>
            </button>
          </div>

          {isAlreadyIn ? (
            <div className="px-6 pb-6 pt-2 flex flex-col gap-3">
              <p className="text-sm text-on-surface-variant">
                Signed in as{" "}
                <span className="font-medium text-on-surface">
                  {user?.username}
                </span>{" "}
                ({user?.email}).
              </p>
              <button
                type="button"
                onClick={async () => {
                  await logout();
                  onClose();
                }}
                className="gradient-primary text-white text-sm font-medium px-4 py-2.5 rounded-xl hover:opacity-90"
              >
                Sign out
              </button>
            </div>
          ) : (
            <form
              onSubmit={handleSubmit}
              className="px-6 pb-6 pt-2 flex flex-col gap-4"
            >
              <ModeToggle mode={mode} onChange={switchMode} disabled={busy} />

              {mode === "signin" && (
                <div className="flex gap-1 border-b border-outline-variant/30 -mx-6 px-6">
                  <SubTabButton
                    active={signinMethod === "password"}
                    onClick={() => switchSigninMethod("password")}
                    label="Password"
                  />
                  <SubTabButton
                    active={signinMethod === "code"}
                    onClick={() => switchSigninMethod("code")}
                    label="Email code"
                  />
                </div>
              )}

              <div className="flex flex-col gap-3">
                {/* Email field — read-only on the verify step so the user can't edit by mistake */}
                {signinMethod === "code" && codeStep === "verify" ? (
                  <div className="flex items-center justify-between">
                    <div>
                      <span className="text-xs font-medium text-on-surface-variant">
                        Sending code to
                      </span>
                      <div className="text-sm text-on-surface">{email}</div>
                    </div>
                    <button
                      type="button"
                      onClick={() => {
                        setCodeStep("request");
                        setCode("");
                        setError(null);
                        setFieldErrors({});
                      }}
                      disabled={busy}
                      className="text-xs text-blue-700 hover:underline"
                    >
                      Change
                    </button>
                  </div>
                ) : (
                  <Field
                    label="Email"
                    error={fieldErrors.email}
                    input={
                      <input
                        ref={emailRef}
                        type="email"
                        autoComplete="email"
                        value={email}
                        onChange={(e) => setEmail(e.target.value)}
                        disabled={busy}
                        placeholder="you@example.com"
                        className="w-full bg-surface-container-low border border-outline-variant/40 rounded-md p-3 text-sm text-on-surface placeholder:text-outline-variant focus:outline-none focus:ring-2 focus:ring-primary/30"
                      />
                    }
                  />
                )}

                {mode === "signup" && (
                  <Field
                    label="Username"
                    helper="3–20 chars, letters/numbers/_/- only."
                    error={fieldErrors.username}
                    input={
                      <input
                        type="text"
                        autoComplete="username"
                        value={username}
                        onChange={(e) => setUsername(e.target.value)}
                        disabled={busy}
                        placeholder="alice42"
                        className="w-full bg-surface-container-low border border-outline-variant/40 rounded-md p-3 text-sm text-on-surface placeholder:text-outline-variant focus:outline-none focus:ring-2 focus:ring-primary/30"
                      />
                    }
                  />
                )}

                {(mode === "signup" ||
                  (mode === "signin" && signinMethod === "password")) && (
                  <div className="flex flex-col">
                    <Field
                      label="Password"
                      error={fieldErrors.password}
                      input={
                        <input
                          type="password"
                          autoComplete={
                            mode === "signup"
                              ? "new-password"
                              : "current-password"
                          }
                          value={password}
                          onChange={(e) => setPassword(e.target.value)}
                          disabled={busy}
                          placeholder="••••••••"
                          className="w-full bg-surface-container-low border border-outline-variant/40 rounded-md p-3 text-sm text-on-surface placeholder:text-outline-variant focus:outline-none focus:ring-2 focus:ring-primary/30"
                        />
                      }
                    />
                    {mode === "signup" && (
                      <PasswordChecklist password={password} />
                    )}
                  </div>
                )}

                {mode === "signin" &&
                  signinMethod === "code" &&
                  codeStep === "request" && (
                    <p className="text-xs text-on-surface-variant -mt-1">
                      We&apos;ll email you a {CODE_LENGTH}-digit code.
                    </p>
                  )}

                {mode === "signin" &&
                  signinMethod === "code" &&
                  codeStep === "verify" && (
                    <Field
                      label={`${CODE_LENGTH}-digit code`}
                      error={fieldErrors.code}
                      input={
                        <input
                          ref={codeRef}
                          type="text"
                          inputMode="numeric"
                          autoComplete="one-time-code"
                          maxLength={CODE_LENGTH}
                          value={code}
                          onChange={(e) =>
                            setCode(
                              e.target.value
                                .replace(/\D/g, "")
                                .slice(0, CODE_LENGTH),
                            )
                          }
                          disabled={busy}
                          placeholder="000000"
                          className="w-full bg-surface-container-low border border-outline-variant/40 rounded-md p-3 text-lg font-mono tracking-[0.5em] text-center text-on-surface placeholder:text-outline-variant focus:outline-none focus:ring-2 focus:ring-primary/30"
                        />
                      }
                    />
                  )}
              </div>

              {error && (
                <div className="text-xs text-error bg-error-container/40 rounded-md px-3 py-2 flex items-center justify-between gap-2">
                  <span>{error}</span>
                  {showCreateLink && (
                    <button
                      type="button"
                      onClick={() => switchMode("signup")}
                      className="text-blue-700 hover:underline whitespace-nowrap"
                    >
                      Create one
                    </button>
                  )}
                </div>
              )}

              <button
                type="submit"
                disabled={
                  busy ||
                  (mode === "signup" && !allPasswordChecksPass(password))
                }
                className="gradient-primary text-white text-sm font-medium px-4 py-2.5 rounded-xl hover:opacity-90 disabled:opacity-50"
              >
                {submitLabel}
              </button>

              {mode === "signin" &&
                signinMethod === "code" &&
                codeStep === "verify" && (
                  <button
                    type="button"
                    onClick={handleResend}
                    disabled={busy || resendIn > 0}
                    className="text-xs text-blue-700 hover:underline self-center disabled:text-on-surface-variant disabled:no-underline"
                  >
                    {resendIn > 0
                      ? `Resend code in ${resendIn}s`
                      : "Resend code"}
                  </button>
                )}

              <div className="flex items-center justify-between text-xs">
                {mode === "signin" ? (
                  <button
                    type="button"
                    onClick={() => switchMode("signup")}
                    disabled={busy}
                    className="text-blue-700 hover:underline"
                  >
                    Don&apos;t have an account? Create one
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={() => switchMode("signin")}
                    disabled={busy}
                    className="text-blue-700 hover:underline"
                  >
                    Already have an account? Sign in
                  </button>
                )}
                <button
                  type="button"
                  onClick={onClose}
                  disabled={busy}
                  className="text-on-surface-variant hover:text-on-surface"
                >
                  Continue as guest
                </button>
              </div>
            </form>
          )}
        </div>
      </div>
    </>
  );
}

function ModeToggle({
  mode,
  onChange,
  disabled,
}: {
  mode: Mode;
  onChange: (m: Mode) => void;
  disabled: boolean;
}) {
  return (
    <div className="flex p-1 bg-surface-container-low rounded-lg gap-1">
      <SegButton
        label="Sign in"
        active={mode === "signin"}
        onClick={() => onChange("signin")}
        disabled={disabled}
      />
      <SegButton
        label="Create account"
        active={mode === "signup"}
        onClick={() => onChange("signup")}
        disabled={disabled}
      />
    </div>
  );
}

function SegButton({
  label,
  active,
  onClick,
  disabled,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
  disabled: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`flex-1 px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
        active
          ? "bg-surface-container-lowest text-blue-700 ambient-shadow"
          : "text-on-surface-variant hover:text-on-surface"
      }`}
    >
      {label}
    </button>
  );
}

function SubTabButton({
  active,
  onClick,
  label,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`px-3 py-1.5 text-sm font-medium rounded-t-md ${
        active
          ? "text-blue-700 border-b-2 border-blue-600 -mb-px"
          : "text-on-surface-variant hover:text-on-surface"
      }`}
    >
      {label}
    </button>
  );
}

function Field({
  label,
  input,
  helper,
  error,
}: {
  label: string;
  input: React.ReactNode;
  helper?: string;
  error?: string;
}) {
  return (
    <label className="flex flex-col">
      <span className="text-xs font-medium text-on-surface-variant mb-1">
        {label}
      </span>
      {input}
      {error ? (
        <span className="text-xs text-error mt-1">{error}</span>
      ) : helper ? (
        <span className="text-xs text-on-surface-variant mt-1">{helper}</span>
      ) : null}
    </label>
  );
}

function PasswordChecklist({ password }: { password: string }) {
  const c = passwordChecks(password);
  const rows: Array<[keyof typeof c, string]> = [
    ["length", `At least ${MIN_PASSWORD} characters`],
    ["upper", "One uppercase letter"],
    ["symbol", "One symbol (e.g. ! @ # $)"],
  ];
  return (
    <ul
      className="mt-1 flex flex-col gap-0.5 text-xs"
      aria-label="Password requirements"
    >
      {rows.map(([key, label]) => {
        const ok = c[key];
        return (
          <li
            key={key}
            className={`flex items-center gap-1.5 ${
              ok ? "text-green-700" : "text-on-surface-variant"
            }`}
            data-rule={key}
            data-ok={ok ? "1" : "0"}
          >
            <span className="material-symbols-outlined text-[14px] leading-none">
              {ok ? "check_circle" : "radio_button_unchecked"}
            </span>
            <span>{label}</span>
          </li>
        );
      })}
    </ul>
  );
}
