"use client";

/**
 * Shared LIFF bootstrap for the Sales dashboard pages.
 *
 * Extracted after the third page repeated the same forty lines: LIFF init,
 * login redirect, ID-token exchange for memberships, and the header set
 * every proxied call needs. Three copies of an auth flow is three places
 * for it to drift.
 */

export type Membership = {
  license_id: string;
  license_code: string;
  company_name: string;
};

type LiffApi = {
  init(config: { liffId: string; withLoginOnExternalBrowser: boolean }): Promise<void>;
  isLoggedIn(): boolean;
  login(): void;
  getIDToken(): string | null;
};

export function getLiff(): LiffApi | undefined {
  return (window as Window & { liff?: LiffApi }).liff;
}

export const LIFF_SDK_SRC =
  "https://static.line-scdn.net/liff/edge/versions/2.29.2/sdk.js";

export function proxyHeaders(token: string, licenseId: string): HeadersInit {
  return {
    "Content-Type": "application/json",
    "X-Liff-ID-Token": token,
    "X-Liff-Audience": "sales",
    "X-License-Id": licenseId,
  };
}

/** Returns the ID token and memberships, or throws with a message worth showing. */
export async function initLiffSession(
  liffId: string,
): Promise<{ token: string; memberships: Membership[] }> {
  const liff = getLiff();
  if (!liffId || !liff) {
    throw new Error("NEXT_PUBLIC_LIFF_SALES_ID is REQUIRED_NOT_CONFIGURED");
  }
  await liff.init({ liffId, withLoginOnExternalBrowser: true });
  if (!liff.isLoggedIn()) {
    liff.login();
    // login() navigates away; nothing after this runs in practice, but the
    // caller still needs a well-typed value rather than undefined.
    return { token: "", memberships: [] };
  }
  const idToken = liff.getIDToken();
  if (!idToken) throw new Error("LIFF did not return an ID token");

  const response = await fetch("/api/liff/sales/me", {
    headers: { "X-Liff-ID-Token": idToken },
  });
  if (!response.ok) throw new Error(`authentication failed (${response.status})`);
  const me = (await response.json()) as { memberships: Membership[] };
  return { token: idToken, memberships: me.memberships };
}
