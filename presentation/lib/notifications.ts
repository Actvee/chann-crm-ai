export type Notification = {
  id: string;
  type: string;
  message: string;
  message_en: string | null;
  entity_type: string | null;
  entity_id: string | null;
  read_at: string | null;
  created_at: string;
};

/**
 * Routed through the existing /api/phase2 proxy, which is generic
 * (/api/phase2/X -> /api/v1/X on the Application tier) despite its name.
 * Reused rather than duplicated; the misleading path is a Phase 20 cleanup.
 */
const BASE = "/api/phase2";

function authHeaders(idToken: string, licenseId: string): HeadersInit {
  return {
    "X-Liff-ID-Token": idToken,
    "X-Liff-Audience": "sales",
    "X-License-Id": licenseId,
    "Content-Type": "application/json",
  };
}

export async function fetchUnreadCount(
  idToken: string,
  licenseId: string,
): Promise<number> {
  const res = await fetch(`${BASE}/notifications/unread_count`, {
    headers: authHeaders(idToken, licenseId),
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`unread_count failed: ${res.status}`);
  const body = await res.json();
  return Number(body.unread_count ?? 0);
}

export async function fetchNotifications(
  idToken: string,
  licenseId: string,
): Promise<Notification[]> {
  const res = await fetch(`${BASE}/notifications?limit=50`, {
    headers: authHeaders(idToken, licenseId),
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`notifications failed: ${res.status}`);
  return res.json();
}

export async function markNotificationRead(
  idToken: string,
  licenseId: string,
  id: string,
): Promise<void> {
  const res = await fetch(`${BASE}/notifications/${encodeURIComponent(id)}/read`, {
    method: "POST",
    headers: authHeaders(idToken, licenseId),
  });
  if (!res.ok) throw new Error(`mark read failed: ${res.status}`);
}
