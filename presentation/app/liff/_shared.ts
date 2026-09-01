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
  /** The license_members row id — needed by anything acting AS this
   *  member, such as claiming a ticket. Absent for customer shop
   *  relationships, which have no members row. */
  member_id?: string | null;
};

type LiffApi = {
  init(config: { liffId: string; withLoginOnExternalBrowser?: boolean }): Promise<void>;
  openWindow(config: { url: string; external?: boolean }): void;
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

export type Audience = "sales" | "technician" | "customer";

export const SALES_BASE_PATH = "/liff/sales";

/**
 * Which LIFF app a page belongs to.
 *
 * Threaded through rather than hardcoded because the three OAs are three
 * separate LIFF apps with three separate ID tokens: sending a technician's
 * token to /api/liff/sales/me fails verification, and the failure looks
 * like a broken login rather than a wrong audience.
 */
export function basePathFor(audience: Audience): string {
  return `/liff/${audience}`;
}

// Kept so the diagnostics line can report why init failed. liff.init()
// rejecting is otherwise invisible: the page just never starts.
let lastInitError = "";

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

/**
 * Complete a deep-link arrival, and return where to go next.
 *
 * Order is the whole point. LINE lands on the endpoint URL carrying both
 * the destination (`liff.state`) and an authorisation `code` that
 * liff.init() must exchange for a session. init() runs FIRST so the code is
 * consumed; only then is it safe to leave the page.
 *
 * An earlier version navigated on liff.state immediately, which discarded
 * the code, caused LIFF to reauthorise, and looped — a fresh `code=` in the
 * access log every ~700ms. A later version left the navigation to init()
 * itself on the assumption that it redirects; it does not always, and the
 * page simply sat blank.
 *
 * Returns the in-app path to navigate to, or null when there is nothing to
 * follow.
 */
export async function completeLiffRedirect(liffId: string): Promise<string | null> {
  if (typeof window === "undefined") return null;
  const state = new URLSearchParams(window.location.search).get("liff.state");
  if (!state) return null;

  const liff = getLiff();
  if (liff && liffId) {
    try {
      await withTimeout("liff.init", 15_000, liff.init({ liffId }));
    } catch (error) {
      lastInitError = error instanceof Error ? error.message : String(error);
      // Fall through and navigate anyway: the destination page runs the
      // same init and will report the failure with its own diagnostics,
      // which is far more useful than a blank menu.
    }
  }

  // init() may have already navigated. If it did, the path no longer
  // matches the endpoint and there is nothing left to do.
  if (!window.location.pathname.endsWith(SALES_BASE_PATH)) return null;

  // Only ever within this app. liff.state comes off a URL anyone can craft,
  // so following an absolute target would be an open redirect.
  const target = state.startsWith("/") ? state : `/${state}`;
  if (target.startsWith("//") || target.includes("://")) return null;
  return `${SALES_BASE_PATH}${target}`;
}

export function proxyHeaders(
  token: string, licenseId: string, audience: Audience = "sales",
): HeadersInit {
  return {
    "Content-Type": "application/json",
    "X-Liff-ID-Token": token,
    "X-Liff-Audience": audience,
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
      // idToken is the one this app actually needs, and it is null whenever
      // the LIFF app lacks the openid scope even though login "succeeded" —
      // a distinction worth seeing separately from loggedIn.
      parts.push(`idToken=${liff.getIDToken() ? "yes" : "no"}`);
    } catch {
      parts.push("state=uninitialised");
    }
  }
  if (lastInitError) parts.push(`initError=${lastInitError}`);
  const state = new URLSearchParams(window.location.search).get("liff.state");
  if (state) parts.push(`liff.state=${state}`);
  return parts.join(" · ");
}

/** Returns the ID token and memberships, or throws with a message worth showing. */
export async function initLiffSession(
  liffId: string,
  audience: Audience = "sales",
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
  try {
    await withTimeout("liff.init", 15_000, liff.init({ liffId }));
    // Console, not the page: this is developer output. Rendering it made a
    // normal loading screen look like an error to the person using it.
    // eslint-disable-next-line no-console
    console.info("[liff]", liffDiagnostics());
  } catch (error) {
    lastInitError = error instanceof Error ? error.message : String(error);
    throw error;
  }
  if (!liff.isLoggedIn()) {
    if (liff.isInClient()) {
      // Do NOT try to recover by reopening through the LIFF URL. That was
      // tried and it reload-looped: LINE opens a fresh webview each time,
      // so a sessionStorage guard is gone before it can stop anything.
      //
      // Being in the LINE app with no session after a successful init is
      // not something this code can fix — it points at the LIFF app's own
      // configuration (scope, endpoint URL, or channel state), so it says
      // so and stops.
      throw new Error(
        "No LINE session. The LIFF app is reachable but issued no token — " +
        "check its Scope (openid and profile) and Endpoint URL in the LINE " +
        "Developers console.",
      );
    }
    liff.login({ redirectUri: window.location.href });
    return { token: "", memberships: [] };
  }

  const idToken = liff.getIDToken();
  if (!idToken) throw new Error("LIFF did not return an ID token");

  const response = await withTimeout(
    `/api/liff/${audience}/me`,
    15_000,
    fetch(`/api/liff/${audience}/me`, { headers: { "X-Liff-ID-Token": idToken } }),
  );
  if (!response.ok) throw new Error(`authentication failed (${response.status})`);
  const me = (await response.json()) as { memberships: Membership[] };
  return { token: idToken, memberships: me.memberships };
}

/**
 * What this person may do, so the UI can show a field as editable or not.
 *
 * Fetched rather than inferred from a role name: two tenants can both have
 * a role called "sales" with entirely different permissions, so deciding
 * from the role would offer edit controls that 403 on save — the person
 * fills the form, loses the work, and learns nothing about why.
 */
export async function fetchPermissions(
  token: string,
  licenseId: string,
  audience: Audience = "sales",
): Promise<Set<string>> {
  try {
    const response = await fetch(
      `/api/phase2/licenses/${licenseId}/me/permissions`,
      { headers: proxyHeaders(token, licenseId, audience) },
    );
    if (!response.ok) return new Set();
    const body = (await response.json()) as { permission_keys?: string[] };
    return new Set(body.permission_keys ?? []);
  } catch {
    // An empty set means "show everything read-only", which is the safe
    // direction to fail in: nothing is offered that would then be refused.
    return new Set();
  }
}

/**
 * Open a URL from inside LIFF.
 *
 * liff.openWindow, not an anchor or window.open. LINE's in-app browser
 * refuses blob: URLs outright — "ไม่สามารถเปิดลิงก์ได้" — and a popup
 * opened after an await is not treated as user-initiated, so both of the
 * obvious approaches fail in exactly the place this dashboard runs.
 *
 * `external: true` hands it to the phone's real browser, which is what
 * you want for a PDF: the in-app viewer cannot save or share one.
 */
export function openExternal(url: string): void {
  const liff = getLiff();
  if (liff && typeof liff.openWindow === "function") {
    liff.openWindow({ url, external: true });
    return;
  }
  // Outside LIFF — during local development, or if the SDK failed to
  // load — a plain navigation is correct and available.
  window.open(url, "_blank", "noopener");
}
