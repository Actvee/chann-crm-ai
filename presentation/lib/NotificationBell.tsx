"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";
import {
  fetchNotifications,
  fetchUnreadCount,
  markNotificationRead,
  type Notification,
} from "@/lib/notifications";

/**
 * Master Spec 6.8 — the dashboard badge polls the unread count rather than
 * being pushed to. Polling is what the spec specifies and it avoids holding a
 * websocket open through Cloud Run's request model.
 */
const POLL_MS = 30_000;

type Props = {
  idToken: string;
  licenseId: string;
};

export function NotificationBell({ idToken, licenseId }: Props) {
  const { t, locale } = useLanguage();
  const [count, setCount] = useState(0);
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<Notification[]>([]);
  const [error, setError] = useState(false);

  // Kept in a ref so the poll callback does not need `open` as a dependency,
  // which would tear down and restart the interval on every open/close.
  const openRef = useRef(open);
  openRef.current = open;

  const refreshCount = useCallback(async () => {
    if (!idToken || !licenseId) return;
    try {
      setCount(await fetchUnreadCount(idToken, licenseId));
      setError(false);
    } catch {
      // A failed poll is not worth a visible error — the next tick may well
      // succeed. Only the opened list surfaces a failure to the user.
      setError(true);
    }
  }, [idToken, licenseId]);

  useEffect(() => {
    void refreshCount();
    const id = window.setInterval(() => {
      // Skip polling while the list is open: the list already refreshed the
      // count, and a poll landing mid-read makes the badge flicker.
      if (!openRef.current) void refreshCount();
    }, POLL_MS);
    return () => window.clearInterval(id);
  }, [refreshCount]);

  const openList = useCallback(async () => {
    setOpen(true);
    try {
      setItems(await fetchNotifications(idToken, licenseId));
      setError(false);
    } catch {
      setError(true);
    }
  }, [idToken, licenseId]);

  const onRead = useCallback(
    async (id: string) => {
      // Optimistic: the row is already durable server-side, and making the
      // user wait on a round trip to see their own tap acknowledged is worse
      // than briefly showing a state that a failed refresh would correct.
      setItems((prev) =>
        prev.map((n) => (n.id === id ? { ...n, read_at: new Date().toISOString() } : n)),
      );
      setCount((c) => Math.max(0, c - 1));
      try {
        await markNotificationRead(idToken, licenseId, id);
      } finally {
        void refreshCount();
      }
    },
    [idToken, licenseId, refreshCount],
  );

  return (
    <section style={{ marginTop: 16 }}>
      <button type="button" onClick={open ? () => setOpen(false) : openList}>
        {t.notification.title}
        {count > 0 ? ` (${count} ${t.notification.unreadBadge})` : ""}
      </button>

      {open && (
        <div style={{ marginTop: 8 }}>
          {error && <p>{t.notification.loadFailed}</p>}
          {!error && items.length === 0 && <p>{t.notification.empty}</p>}
          <ul style={{ listStyle: "none", padding: 0 }}>
            {items.map((n) => {
              const text = locale === "en" && n.message_en ? n.message_en : n.message;
              return (
                <li
                  key={n.id}
                  style={{
                    padding: "8px 0",
                    borderBottom: "1px solid var(--line)",
                    fontWeight: n.read_at ? "normal" : "bold",
                  }}
                >
                  <div>{text}</div>
                  <small>{new Date(n.created_at).toLocaleString(locale)}</small>
                  {!n.read_at && (
                    <div>
                      <button type="button" onClick={() => void onRead(n.id)}>
                        {t.notification.markRead}
                      </button>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </section>
  );
}
