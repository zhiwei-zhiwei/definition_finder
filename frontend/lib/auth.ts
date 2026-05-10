import { API_BASE } from "./api";

const TOKEN_KEY = "lexisai_token";
const ANON_DOC_KEY = "lexisai_anon_doc";

export type AuthUser = {
  user_id: string;
  email: string;
  username: string;
};
export type LoginResponse = AuthUser & { token: string };

const isBrowser = () => typeof window !== "undefined";

export function getToken(): string | null {
  return isBrowser() ? window.localStorage.getItem(TOKEN_KEY) : null;
}

export function setToken(t: string): void {
  if (isBrowser()) window.localStorage.setItem(TOKEN_KEY, t);
}

export function clearToken(): void {
  if (isBrowser()) window.localStorage.removeItem(TOKEN_KEY);
}

export function getAnonDocId(): string | null {
  return isBrowser() ? window.sessionStorage.getItem(ANON_DOC_KEY) : null;
}

export function setAnonDocId(id: string): void {
  if (isBrowser()) window.sessionStorage.setItem(ANON_DOC_KEY, id);
}

export function clearAnonDocId(): void {
  if (isBrowser()) window.sessionStorage.removeItem(ANON_DOC_KEY);
}

export function authHeaders(): Record<string, string> {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

/**
 * Wraps fetch error responses into Error objects whose `.message` is the
 * server's `detail` string (e.g. "email_taken"). Lets the modal switch
 * branches on a stable error code instead of parsing HTTP status.
 */
async function postJson(path: string, body: unknown): Promise<Response> {
  return fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body),
  });
}

async function readError(r: Response, fallback: string): Promise<string> {
  try {
    const data = await r.json();
    if (typeof data?.detail === "string") return data.detail;
    // Pydantic ValidationError shape: detail is a list of {msg, ...}. We use
    // the *raw* validator message (e.g. "password_weak") as our error code,
    // so strip Pydantic's "Value error, " prefix when present.
    if (Array.isArray(data?.detail) && data.detail.length > 0) {
      const msg = String(data.detail[0]?.msg ?? "");
      const stripped = msg.replace(/^Value error,\s*/, "");
      if (stripped) return stripped;
    }
  } catch {
    /* not json */
  }
  return fallback;
}

export async function apiSignup(input: {
  email: string;
  username: string;
  password: string;
}): Promise<LoginResponse> {
  const r = await postJson("/auth/signup", input);
  if (!r.ok) throw new Error(await readError(r, `signup_failed_${r.status}`));
  return r.json();
}

export async function apiLoginPassword(input: {
  email: string;
  password: string;
}): Promise<LoginResponse> {
  const r = await postJson("/auth/login", input);
  if (!r.ok) throw new Error(await readError(r, `login_failed_${r.status}`));
  return r.json();
}

export async function apiRequestCode(email: string): Promise<void> {
  const r = await postJson("/auth/email-code/request", { email });
  if (!r.ok) throw new Error(await readError(r, `code_request_${r.status}`));
}

export async function apiVerifyCode(input: {
  email: string;
  code: string;
}): Promise<LoginResponse> {
  const r = await postJson("/auth/email-code/verify", input);
  if (!r.ok) throw new Error(await readError(r, `code_verify_${r.status}`));
  return r.json();
}

export async function apiMe(): Promise<AuthUser | null> {
  const t = getToken();
  if (!t) return null;
  const r = await fetch(`${API_BASE}/auth/me`, { headers: authHeaders() });
  if (!r.ok) return null;
  const data = await r.json();
  return data ?? null;
}

export async function apiLogout(): Promise<void> {
  try {
    await fetch(`${API_BASE}/auth/logout`, {
      method: "POST",
      headers: authHeaders(),
    });
  } catch {
    /* best-effort */
  }
  clearToken();
}

export async function apiClaim(docIds: string[]): Promise<string[]> {
  if (docIds.length === 0) return [];
  const r = await postJson("/auth/claim", { doc_ids: docIds });
  if (!r.ok) throw new Error(`claim failed: ${r.status}`);
  const data = await r.json();
  return data.claimed ?? [];
}
