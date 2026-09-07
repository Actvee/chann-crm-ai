"use client";

/**
 * A browser-side call from the admin console to its own /api/admin/*
 * routes, with the two things every button used to get wrong (review
 * D11/D13, 6 Sep 2026): a 401 means the operator's session ended, so the
 * page goes back to login instead of saying "failed"; and any other
 * refusal carries the Application tier's reason, which the page shows.
 */
export type AdminCall =
  | { ok: true; body: unknown }
  | { ok: false; status: number; reason: string };

/** The human-readable part of a `detail`, whatever shape it came in. */
export function reasonOf(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const row = detail as Record<string, unknown>;
    if (typeof row.reason === "string") return row.reason;
    if (row.reason && typeof row.reason === "object") return reasonOf(row.reason);
    if (typeof row.message === "string") return row.message;
    if (typeof row.error === "string") return row.error;
    try {
      return JSON.stringify(detail);
    } catch {
      return "";
    }
  }
  return "";
}

export async function adminCall(path: string, body: unknown): Promise<AdminCall> {
  let res: Response;
  try {
    res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    return { ok: false, status: 0, reason: "" };
  }
  const parsed = (await res.json().catch(() => null)) as { detail?: unknown } | null;
  if (res.status === 401) {
    // Session gone (expired, revoked, cookie cleared): nothing on this
    // page can succeed, so go where it can be fixed.
    window.location.assign("/admin/login?expired=1");
    return { ok: false, status: 401, reason: "" };
  }
  if (!res.ok) return { ok: false, status: res.status, reason: reasonOf(parsed?.detail) };
  return { ok: true, body: parsed };
}
