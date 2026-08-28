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
  isInClient(): boolean;
  login(config?: { redirectUri?: string }): void;
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
    // Inside the LINE app the user is always logged in, so reaching here
    // means something else is wrong. Calling login() anyway is what made
    // every dashboard page bounce back to the menu: login() redirects to
    // the LIFF app's configured endpoint URL, which is the index — so a
    // tap on "Deals" loaded the deals page, failed this check, and got
    // sent straight back, looking like the link simply did not work.
    if (liff.isInClient()) {
      throw new Error("เซสชัน LINE หมดอายุ ปิดหน้านี้แล้วเปิดใหม่จากเมนู");
    }
    // In an external browser logging in IS the right move, but send the
    // person back to the page they asked for rather than the endpoint.
    liff.login({ redirectUri: window.location.href });
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
