/**
 * Core HTTP client for the Alreef API (/api/v1/).
 *
 * - Wraps axios with a base URL, `Authorization: Bearer` attachment, and a
 *   response interceptor that transparently refreshes an expired access
 *   token once and retries the original request (see README.md ->
 *   "Automatic 401 handling").
 * - Unwraps the `{ success, message, data, errors }` envelope
 *   (apps/core/response.py) — every SDK function gets back `data` directly,
 *   or an `ApiError` is thrown.
 * - `withCredentials: true` is required even for logged-out users because
 *   the guest cart is tied to the Django session cookie
 *   (apps/carts/services.py).
 *
 * Dependency: axios (peer dependency — install it in the consuming app).
 */
import axios, {
  AxiosError,
  AxiosInstance,
  AxiosRequestConfig,
  InternalAxiosRequestConfig,
} from "axios";
import type { ApiErrorBody, ApiResponse, ErrorCode } from "./types";

// ---------------------------------------------------------------------------
// Token store
// ---------------------------------------------------------------------------

/**
 * Minimal pluggable token store. The default implementation keeps the access
 * token in memory only and persists the refresh token to localStorage (see
 * docs/frontend/README.md -> "Token storage strategy" for the tradeoffs).
 * Swap `tokenStore` for an httpOnly-cookie-backed implementation later
 * without touching the rest of this file.
 */
export interface TokenStore {
  getAccess(): string | null;
  getRefresh(): string | null;
  setTokens(access: string | null, refresh: string | null): void;
  clear(): void;
}

function createDefaultTokenStore(): TokenStore {
  let access: string | null = null;
  const REFRESH_KEY = "alreef_refresh_token";

  const readRefresh = (): string | null =>
    typeof window === "undefined" ? null : window.localStorage.getItem(REFRESH_KEY);

  return {
    getAccess: () => access,
    getRefresh: () => readRefresh(),
    setTokens(newAccess, newRefresh) {
      access = newAccess;
      if (typeof window !== "undefined") {
        if (newRefresh) window.localStorage.setItem(REFRESH_KEY, newRefresh);
        else window.localStorage.removeItem(REFRESH_KEY);
      }
    },
    clear() {
      access = null;
      if (typeof window !== "undefined") window.localStorage.removeItem(REFRESH_KEY);
    },
  };
}

export const tokenStore: TokenStore = createDefaultTokenStore();

// ---------------------------------------------------------------------------
// ApiError
// ---------------------------------------------------------------------------

export class ApiError extends Error {
  /** Machine-readable code from apps/core/error_codes.py, when available. */
  code: ErrorCode | "UNKNOWN_ERROR";
  /** DRF-style field error map, e.g. { email: ["This field is required."] }. */
  fields: Record<string, string[] | string>;
  status: number | null;

  constructor(message: string, code: ErrorCode | "UNKNOWN_ERROR", fields: Record<string, string[] | string>, status: number | null) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.fields = fields;
    this.status = status;
  }
}

function toApiError(error: unknown): ApiError {
  if (axios.isAxiosError(error)) {
    const err = error as AxiosError<ApiResponse<unknown>>;
    const body = err.response?.data;
    const errors: ApiErrorBody | null = body?.errors ?? null;
    return new ApiError(
      body?.message ?? err.message ?? "Request failed.",
      errors?.code ?? "UNKNOWN_ERROR",
      errors?.fields ?? {},
      err.response?.status ?? null
    );
  }
  return new ApiError(error instanceof Error ? error.message : "Unknown error.", "UNKNOWN_ERROR", {}, null);
}

// ---------------------------------------------------------------------------
// axios instance
// ---------------------------------------------------------------------------

// Read via globalThis (not a bare `process` reference) so this file compiles
// standalone without requiring @types/node — Next.js/Node consumers still
// get the real process.env at runtime.
const nodeProcess: { env?: Record<string, string | undefined> } | undefined = (
  globalThis as { process?: { env?: Record<string, string | undefined> } }
).process;

const DEFAULT_BASE_URL =
  nodeProcess?.env?.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000/api/v1";

export const http: AxiosInstance = axios.create({
  baseURL: DEFAULT_BASE_URL,
  withCredentials: true,
});

/** Override the base URL at runtime, e.g. for different environments/tests. */
export function configureApiClient(options: { baseURL?: string }) {
  if (options.baseURL) http.defaults.baseURL = options.baseURL;
}

http.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const access = tokenStore.getAccess();
  if (access) {
    config.headers = config.headers ?? {};
    config.headers.Authorization = `Bearer ${access}`;
  }
  return config;
});

let refreshInFlight: Promise<string | null> | null = null;

async function refreshAccessToken(): Promise<string | null> {
  const refresh = tokenStore.getRefresh();
  if (!refresh) return null;

  if (!refreshInFlight) {
    refreshInFlight = axios
      .post<ApiResponse<{ access: string; refresh: string }>>(
        `${http.defaults.baseURL}/auth/token/refresh/`,
        { refresh }
      )
      .then((res) => {
        const data = res.data.data;
        if (!data) return null;
        // ROTATE_REFRESH_TOKENS=True — the old refresh token is now blacklisted,
        // always persist the new one.
        tokenStore.setTokens(data.access, data.refresh);
        return data.access;
      })
      .catch(() => {
        tokenStore.clear();
        return null;
      })
      .finally(() => {
        refreshInFlight = null;
      });
  }
  return refreshInFlight;
}

http.interceptors.response.use(
  (response) => response,
  async (error: AxiosError<ApiResponse<unknown>>) => {
    const original = error.config as (AxiosRequestConfig & { _retried?: boolean }) | undefined;
    const code = error.response?.data?.errors?.code;
    const isAuthExpiry =
      error.response?.status === 401 && (code === "AUTH_TOKEN_EXPIRED" || code === "AUTH_TOKEN_INVALID");

    if (isAuthExpiry && original && !original._retried) {
      original._retried = true;
      const newAccess = await refreshAccessToken();
      if (newAccess) {
        original.headers = { ...(original.headers ?? {}), Authorization: `Bearer ${newAccess}` };
        return http(original);
      }
    }
    return Promise.reject(error);
  }
);

// ---------------------------------------------------------------------------
// Envelope-unwrapping request helpers
// ---------------------------------------------------------------------------

/** GET request that unwraps `data`. Throws ApiError on failure. */
export async function apiGet<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
  try {
    const res = await http.get<ApiResponse<T>>(url, config);
    return res.data.data as T;
  } catch (error) {
    throw toApiError(error);
  }
}

export async function apiPost<T>(url: string, body?: unknown, config?: AxiosRequestConfig): Promise<T> {
  try {
    const res = await http.post<ApiResponse<T>>(url, body, config);
    return res.data.data as T;
  } catch (error) {
    throw toApiError(error);
  }
}

export async function apiPatch<T>(url: string, body?: unknown, config?: AxiosRequestConfig): Promise<T> {
  try {
    const res = await http.patch<ApiResponse<T>>(url, body, config);
    return res.data.data as T;
  } catch (error) {
    throw toApiError(error);
  }
}

export async function apiPut<T>(url: string, body?: unknown, config?: AxiosRequestConfig): Promise<T> {
  try {
    const res = await http.put<ApiResponse<T>>(url, body, config);
    return res.data.data as T;
  } catch (error) {
    throw toApiError(error);
  }
}

export async function apiDelete<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
  try {
    const res = await http.delete<ApiResponse<T>>(url, config);
    return res.data.data as T;
  } catch (error) {
    throw toApiError(error);
  }
}

/** Generates a fresh Idempotency-Key. Used for POST /checkout/ and admin refunds. */
export function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  // Fallback for environments without crypto.randomUUID (older Node/browsers).
  return `${Date.now()}-${Math.random().toString(16).slice(2)}-${Math.random().toString(16).slice(2)}`;
}
