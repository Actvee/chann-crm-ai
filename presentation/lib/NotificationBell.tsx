"use client";

import Link from "next/link";
import { useCallback, useEffect, useId, useRef, useState } from "react";

import { ICONS } from "@/app/liff/_nav-model";
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
/** Past this the exact number stops being information. */
const COUNT_CAP = 99;

/**
 * Where a notification leads. The rows carry `entity_type`/`entity_id`
 * already and nothing had ever used them, so reading "งานใหม่ T-2026-0184"
 * left the person to go and find it themselves.
 *
 * Only the three record types with a detail page get a record link; the
 * rest open the list they live in, which is still somewhere rather than
 * nowhere. A type that is not here gets no link at all rather than a guess
 * that 404s — a door that opens onto nothing is worse than a wall.
 */
const RECORD_PAGES: Record<string, string> = {
  customer: "/liff/sales/customers",
  deal: "/liff/sales/deals",
  quote: "/liff/sales/quotes",
};
const LIST_PAGES: Record<string, string> = {
  service_ticket: "/liff/sales/tickets",
  ticket: "/liff/sales/tickets",
  service_report: "/liff/sales/approvals",
  warranty: "/liff/sales/warranties",
  product: "/liff/sales/products",
  chat_session: "/liff/sales/chats",
  follow_up: "/liff/sales/appointments",
};

function hrefFor(item: Notification): string | null {
  const type = String(item.entity_type || "");
  const id = String(item.entity_id || "");
  const record = RECORD_PAGES[type];
  if (record && id) return `${record}/${id}`;
  return LIST_PAGES[type] ?? null;
}

/** วันนี้ / เมื่อวาน / ก่อนหน้า — the reading a person actually does. */
function dayKeyOf(iso: string, now: Date): "today" | "yesterday" | "earlier" {
  const d = new Date(iso);
  const same = (a: Date, b: Date) =>
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate();
  if (same(d, now)) return "today";
  const y = new Date(now);
  y.setDate(y.getDate() - 1);
  return same(d, y) ? "yesterday" : "earlier";
}

type Props = {
  idToken: string;
  licenseId: string;
};

/**
 * The bell in the top bar, and the list it opens.
 *
 * Owner, 18 ก.ย. 2569: "ตำแหน่งปุ่มและหน้าตายังดูไม่สวย" — and it was the
 * one component in the dashboard written entirely in inline styles, with a
 * plain text button and a list that **expanded in the page flow**, pushing
 * the page's own content down. It was also placed by hand on four pages
 * (appointments, history, roles, members) and missing from every other one,
 * so whether you could see your notifications depended on which screen you
 * happened to be on.
 *
 * Now: one icon button in `SalesShell`'s top bar — so every Sales page has
 * it, in the same place — opening a popover anchored to itself. The page
 * underneath never moves.
 *
 * Accessibility, from the checks that apply to this shape:
 *  - the button's accessible name is a SENTENCE ("การแจ้งเตือน · ยังไม่อ่าน
 *    3 รายการ"), and the live region announces that sentence atomically —
 *    a bare number read aloud tells a person nothing;
 *  - Escape, a click outside, and a real close button all dismiss it;
 *  - focus moves into the panel on open and returns to the bell on close,
 *    so a keyboard never lands behind the popover;
 *  - every control is at least 44px with 8px between neighbours.
 */
export function NotificationBell({ idToken, licenseId }: Props) {
  const { t, locale } = useLanguage();
  const copy = t.notification;
  const [count, setCount] = useState(0);
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<Notification[]>([]);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(false);
  const panelId = useId();

  const wrapRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);

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

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await fetchNotifications(idToken, licenseId));
      setError(false);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, [idToken, licenseId]);

  const close = useCallback(() => {
    setOpen(false);
    // Back to the control that opened it, never to the top of the document.
    triggerRef.current?.focus();
  }, []);

  const openList = useCallback(() => {
    setOpen(true);
    void load();
  }, [load]);

  // Escape and a click outside both dismiss. Bound only while open, so the
  // closed bell costs the page nothing.
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        close();
      }
    };
    const onPointer = (event: PointerEvent) => {
      if (!wrapRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointer);
    };
  }, [open, close]);

  // Focus lands in the panel, not on whatever happened to be next in the
  // document — otherwise tabbing walks the page behind the popover.
  useEffect(() => {
    if (open) panelRef.current?.focus();
  }, [open]);

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

  const readAll = useCallback(async () => {
    const ids = items.filter((n) => !n.read_at).map((n) => n.id);
    if (ids.length === 0) return;
    const now = new Date().toISOString();
    setItems((prev) => prev.map((n) => (n.read_at ? n : { ...n, read_at: now })));
    setCount(0);
    try {
      // The list is capped at 50, so this is a handful of calls rather than
      // a sweep. There is no bulk route yet; adding one for this is a Data
      // tier change this round does not need.
      await Promise.all(ids.map((id) => markNotificationRead(idToken, licenseId, id)));
    } finally {
      void refreshCount();
    }
  }, [items, idToken, licenseId, refreshCount]);

  const unread = items.filter((n) => !n.read_at);
  const shown = count > COUNT_CAP ? `${COUNT_CAP}+` : String(count);
  // One sentence, for the button's name AND for the live region. A live
  // region carrying "3" announces "three" and means nothing.
  const label = count > 0 ? `${copy.title} · ${copy.unreadCount.replace("{n}", shown)}` : copy.title;

  // Grouped by the day a person would name, newest first. The list arrives
  // ordered by the Data tier, so each bucket keeps that order.
  const now = new Date();
  const days: { key: string; title: string; rows: Notification[] }[] = (
    [
      ["today", copy.dayToday],
      ["yesterday", copy.dayYesterday],
      ["earlier", copy.dayEarlier],
    ] as const
  )
    .map(([key, title]) => ({
      key,
      title,
      rows: items.filter((n) => dayKeyOf(n.created_at, now) === key),
    }))
    .filter((group) => group.rows.length > 0);

  function row(item: Notification) {
    const text = locale === "en" && item.message_en ? item.message_en : item.message;
    const when = new Date(item.created_at).toLocaleString(locale, {
      dateStyle: "medium",
      timeStyle: "short",
    });
    const href = hrefFor(item);
    const body = (
      <span className="row-body">
        <span className="notif-text">{text}</span>
        <span className="notif-when">{when}</span>
      </span>
    );
    // Tapping the row IS reading it and going there — the same gesture the
    // rest of the dashboard uses. It used to take two: a button labelled
    // "ทำเครื่องหมายว่าอ่านแล้ว", and then finding the record yourself
    // (owner, 18 ก.ย. 2569: "ให้กดเข้าไปดูผ่านตัว record เลย ไม่ต้องใช้ปุ่ม").
    const open = () => {
      if (!item.read_at) void onRead(item.id);
    };
    return (
      <li key={item.id} className="notif-item" data-unread={item.read_at ? undefined : "true"}>
        {href ? (
          <Link
            className="row-link notif-open"
            href={href}
            onClick={() => {
              open();
              setOpen(false);
            }}
          >
            {body}
          </Link>
        ) : item.read_at ? (
          <span className="notif-open">{body}</span>
        ) : (
          /* Nowhere to go, but still something to dismiss. */
          <button type="button" className="notif-open" onClick={open}>
            {body}
          </button>
        )}
      </li>
    );
  }

  return (
    <div className="notif" ref={wrapRef}>
      <button
        type="button"
        ref={triggerRef}
        className="notif-trigger"
        aria-label={label}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => (open ? close() : openList())}
      >
        {ICONS.bell}
        {count > 0 && (
          <span className="notif-badge" aria-hidden="true">
            {shown}
          </span>
        )}
      </button>
      {/* Atomic, and outside the button, so a count that changes while the
          person is elsewhere on the page is announced once, as a sentence. */}
      <span role="status" aria-atomic="true" className="sr-only">
        {count > 0 ? label : ""}
      </span>

      {open && (
        <div
          className="notif-panel"
          id={panelId}
          ref={panelRef}
          role="dialog"
          aria-label={copy.title}
          tabIndex={-1}
        >
          <div className="notif-head">
            <h2>{copy.title}</h2>
            {unread.length > 0 && (
              <button type="button" className="notif-readall" onClick={() => void readAll()}>
                {copy.markAllRead}
              </button>
            )}
            <button
              type="button"
              className="notif-close"
              onClick={close}
              aria-label={copy.close}
            >
              <span aria-hidden="true">✕</span>
            </button>
          </div>

          <div className="notif-body">
            {loading && <p className="notif-note">{copy.loading}</p>}
            {!loading && error && (
              <p className="notif-note">
                {copy.loadFailed}{" "}
                <button type="button" className="notif-retry" onClick={() => void load()}>
                  {copy.retry}
                </button>
              </p>
            )}
            {!loading && !error && items.length === 0 && (
              <div className="notif-empty">
                <p className="notif-empty-title">{copy.empty}</p>
                <p className="notif-note">{copy.emptyHint}</p>
              </div>
            )}
            {!loading &&
              !error &&
              days.map((group) => (
                <div key={group.key}>
                  <h3 className="notif-section">{group.title}</h3>
                  <ul className="notif-list">{group.rows.map(row)}</ul>
                </div>
              ))}
          </div>
        </div>
      )}
    </div>
  );
}
