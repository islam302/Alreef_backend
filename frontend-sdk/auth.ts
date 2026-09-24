/**
 * Auth endpoints — apps/users/urls_auth.py, apps/users/urls.py (addresses).
 */
import { apiDelete, apiGet, apiPatch, apiPost, tokenStore } from "./api-client";
import type {
  Address,
  AddressInput,
  LoginResult,
  Me,
  TokenRefreshResult,
  User,
} from "./types";

export interface RegisterInput {
  email: string;
  password: string;
  first_name?: string;
  last_name?: string;
  phone?: string;
}

/** POST /auth/register/ — creates the account. Does NOT log the user in. */
export async function register(input: RegisterInput): Promise<User> {
  return apiPost<User>("/auth/register/", input);
}

/** POST /auth/login/ — stores access+refresh in the token store and returns the result. */
export async function login(email: string, password: string): Promise<LoginResult> {
  const result = await apiPost<LoginResult>("/auth/login/", { email, password });
  tokenStore.setTokens(result.access, result.refresh);
  return result;
}

/**
 * POST /auth/logout/ — blacklists the refresh token server-side, then clears
 * local token state regardless of whether the call succeeds.
 */
export async function logout(): Promise<void> {
  const refresh = tokenStore.getRefresh();
  try {
    if (refresh) {
      await apiPost<null>("/auth/logout/", { refresh });
    }
  } finally {
    tokenStore.clear();
  }
}

/**
 * POST /auth/token/refresh/ — manual refresh. Note the api-client already
 * does this automatically on a 401; call this directly only if you need to
 * pre-emptively refresh (e.g. on app boot before rendering auth-gated UI).
 */
export async function refreshToken(): Promise<TokenRefreshResult> {
  const refresh = tokenStore.getRefresh();
  if (!refresh) throw new Error("No refresh token available.");
  const result = await apiPost<TokenRefreshResult>("/auth/token/refresh/", { refresh });
  tokenStore.setTokens(result.access, result.refresh);
  return result;
}

/** GET /auth/me/ */
export async function getMe(): Promise<Me> {
  return apiGet<Me>("/auth/me/");
}

export interface UpdateMeInput {
  first_name?: string;
  last_name?: string;
  phone?: string;
  profile?: Partial<{
    date_of_birth: string | null;
    gender: string;
    marketing_consent: boolean;
    language: string;
    currency: string;
  }>;
}

/** PATCH /auth/me/ */
export async function updateMe(input: UpdateMeInput): Promise<Me> {
  return apiPatch<Me>("/auth/me/", input);
}

/** POST /auth/change-password/ */
export async function changePassword(oldPassword: string, newPassword: string): Promise<void> {
  await apiPost<null>("/auth/change-password/", {
    old_password: oldPassword,
    new_password: newPassword,
  });
}

/** POST /auth/password-reset/ — always "succeeds" server-side, never leaks whether the email exists. */
export async function requestPasswordReset(email: string): Promise<void> {
  await apiPost<null>("/auth/password-reset/", { email });
}

/** POST /auth/password-reset-confirm/ */
export async function confirmPasswordReset(token: string, newPassword: string): Promise<void> {
  await apiPost<null>("/auth/password-reset-confirm/", { token, new_password: newPassword });
}

/** POST /auth/verify-email/ — resends to the authenticated user's email. */
export async function requestEmailVerification(): Promise<void> {
  await apiPost<null>("/auth/verify-email/");
}

/** POST /auth/verify-email/confirm/ — token comes from the emailed link's query string. */
export async function confirmEmailVerification(token: string): Promise<void> {
  await apiPost<null>("/auth/verify-email/confirm/", { token });
}

/** POST /auth/verify-phone/ — phone defaults to the user's phone on file if omitted. */
export async function requestPhoneVerification(phone?: string): Promise<void> {
  await apiPost<null>("/auth/verify-phone/", phone ? { phone } : {});
}

/** POST /auth/verify-phone/confirm/ */
export async function confirmPhoneVerification(code: string): Promise<void> {
  await apiPost<null>("/auth/verify-phone/confirm/", { code });
}

// ---------------------------------------------------------------------------
// Addresses (apps/users/urls.py -> /api/v1/addresses/)
// ---------------------------------------------------------------------------

/** GET /addresses/ */
export async function listAddresses(): Promise<Address[]> {
  return apiGet<Address[]>("/addresses/");
}

/** POST /addresses/ */
export async function createAddress(input: AddressInput): Promise<Address> {
  return apiPost<Address>("/addresses/", input);
}

/** GET /addresses/{id}/ */
export async function getAddress(id: number): Promise<Address> {
  return apiGet<Address>(`/addresses/${id}/`);
}

/** PATCH /addresses/{id}/ */
export async function updateAddress(id: number, input: Partial<AddressInput>): Promise<Address> {
  return apiPatch<Address>(`/addresses/${id}/`, input);
}

/** DELETE /addresses/{id}/ */
export async function deleteAddress(id: number): Promise<void> {
  await apiDelete<null>(`/addresses/${id}/`);
}

/** POST /addresses/{id}/set-default/ */
export async function setDefaultAddress(id: number): Promise<Address> {
  return apiPost<Address>(`/addresses/${id}/set-default/`);
}
