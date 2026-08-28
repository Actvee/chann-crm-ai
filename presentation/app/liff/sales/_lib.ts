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
  init(config: { liffId: string; withLoginOnExternalBrowser?: boolean }): Promise<void>;
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

export const SALES_BASE_PATH = "/liff/sales";

/**
 * The in-app path a `liff.state` deep link is asking for, or null.
 *
 * Opening https://liff.line.me/{id}/customers does NOT load that path
 * directly: LINE loads the app's endpoint URL with the rest of the path in
 * a `liff.state` query parameter, and expects the page to navigate the
 * remainder itself. The menu never did that, so every deep link from chat
 * landed on the menu and stopped there.
 *
 * Returns a path for the caller to navigate CLIENT-SIDE rather than
 * navigating itself. A full page load would discard the LIFF session that
 * only exists in the document LINE opened — the exact bug this pairing of
 * functions was written to fix.
 */
export function liffStateTarget(basePath: string = SALES_BASE_PATH): string | null {
  if (typeof window === "undefined") return null;
  const state = new URLSearchParams(window.location.search).get("liff.state");
  if (!state) return null;
  // Only ever navigate within this app's own path. liff.state is
  // attacker-supplyable via a crafted link, so following an absolute URL
  // out of it would be an open redirect.
  const target = state.startsWith("/") ? state : `/${state}`;
  if (target.startsWith("//") || target.includes("://")) return null;
  return `${basePath}${target}`;
}

// One-shot marker so a re-entry that still yields no session fails visibly
// instead of looping between LINE and this app forever.
const REENTRY_FLAG = "chann.liff.reentered";

/**
 * Re-open the current page through its LIFF URL so LINE establishes a
 * session, and report whether a navigation was started.
 *
 * Needed because a LIFF session lives only in the document LINE opened via
 * a LIFF URL. Landing on a sub-path any other way — a bookmark, a rich-menu
 * link, a full page load — gives inClient=true with loggedIn=false, which
 * is precisely what was measured on the customers page. Rather than fail,
 * go back out through LIFF once and come back with a session.
 */
export function reenterThroughLiff(liffId: string): boolean {
  if (typeof window === "undefined" || !liffId) return false;
  if (sessionStorage.getItem(REENTRY_FLAG)) return false;
  const subPath = window.location.pathname.startsWith(SALES_BASE_PATH)
    ? window.location.pathname.slice(SALES_BASE_PATH.length).replace(/^\//, "")
    : "";
  sessionStorage.setItem(REENTRY_FLAG, "1");
  window.location.replace(
    subPath ? `https://liff.line.me/${liffId}/${subPath}` : `https://liff.line.me/${liffId}`,
  );
  return true;
}

/** Clears the re-entry marker once a session is in hand. */
export function clearReentryFlag(): void {
  if (typeof window !== "undefined") sessionStorage.removeItem(REENTRY_FLAG);
}

export function proxyHeaders(token: string, licenseId: string): HeadersInit {
  return {
    "Content-Type": "application/json",
    "X-Liff-ID-Token": token,
    "X-Liff-Audience": "sales",
    "X-License-Id": licenseId,
  };
}

/**
 * A one-line description of where LIFF thinks it is.
 *
 * Exists because three separate causes of "the page bounced back to the
 * menu" were diagnosed by guessing, and only the third guess was right.
 * Surfacing the actual state costs one line on screen and turns the next
 * occurrence into a fact instead of another round of inference.
 */
export function liffDiagnostics(): string {
  if (typeof window === "undefined") return "";
  const liff = getLiff();
  const parts = [
    `path=${window.location.pathname}`,
    `sdk=${liff ? "loaded" : "missing"}`,
  ];
  if (liff) {
    try {
      parts.push(`inClient=${liff.isInClient()}`, `loggedIn=${liff.isLoggedIn()}`);
    } catch {
      parts.push("state=uninitialised");
    }
  }
  const state = new URLSearchParams(window.location.search).get("liff.state");
  if (state) parts.push(`liff.state=${state}`);
  return parts.join(" · ");
}

/**
 * Every await in the startup path is wrapped in this.
 *
 * A hang is the worst possible failure here: the page shows a spinner
 * forever, no error is raised, and the diagnostics below never appear — so
 * there is nothing to report except "it did not load". A timeout converts
 * that into a specific, visible failure naming the step that stalled.
 */
async function withTimeout<T>(label: string, ms: number, work: Promise<T>): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      work,
      new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new Error(`${label} timed out after ${ms / 1000}s`)), ms);
      }),
    ]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

/** Returns the ID token and memberships, or throws with a message worth showing. */
export async function initLiffSession(
  liffId: string,
): Promise<{ token: string; memberships: Membership[] }> {
  const liff = getLiff();
  if (!liffId || !liff) {
    throw new Error("NEXT_PUBLIC_LIFF_SALES_ID is REQUIRED_NOT_CONFIGURED");
  }
  // withLoginOnExternalBrowser is deliberately NOT set.
  //
  // With it on, liff.init() performs the login redirect ITSELF whenever it
  // decides the user is not logged in — and that redirect goes to the LIFF
  // app's configured endpoint URL, which is the menu. Every sub-page
  // therefore loaded, redirected, and landed back on the menu, which looked
  // exactly like the links were broken. The menu itself never did this
  // because it does not call liff.init() at all.
  //
  // Login is still supported in an external browser, just triggered
  // explicitly below with a redirectUri pointing back at the page the
  // person actually asked for.
  await withTimeout("liff.init", 15_000, liff.init({ liffId }));
  if (!liff.isLoggedIn()) {
    if (liff.isInClient()) {
      // In the LINE app with no session means this document was not opened
      // through a LIFF URL. Re-enter through one; the flag makes this at
      // most a single attempt.
      if (reenterThroughLiff(liffId)) return { token: "", memberships: [] };
      throw new Error(
        "LINE session unavailable even after reopening through LIFF — " +
        "check the LIFF app's endpoint URL",
      );
    }
    liff.login({ redirectUri: window.location.href });
    return { token: "", memberships: [] };
  }
  clearReentryFlag();
  const idToken = liff.getIDToken();
  if (!idToken) throw new Error("LIFF did not return an ID token");

  const response = await withTimeout(
    "/api/liff/sales/me",
    15_000,
    fetch("/api/liff/sales/me", { headers: { "X-Liff-ID-Token": idToken } }),
  );
  if (!response.ok) throw new Error(`authentication failed (${response.status})`);
  const me = (await response.json()) as { memberships: Membership[] };
  return { token: idToken, memberships: me.memberships };
}
