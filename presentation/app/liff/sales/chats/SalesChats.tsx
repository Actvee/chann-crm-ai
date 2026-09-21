"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { proxyHeaders } from "../_lib";
import { useSalesSession } from "../_session";
import { SalesShell } from "../_shell";
import { ConfirmDialog, useConfirm } from "../../_confirm";
import { CustomerContext } from "./_customer-context";

type ChatSession = {
  id: string;
  customer_chann_uid: string;
  customer_name?: string | null;
  /** The shop's record of this person, when they are in the contact book. */
  customer_record_id?: string | null;
  customer_code?: string | null;
  status: string;
  assigned_to?: string | null;
  sla_deadline?: string | null;
  escalated_at?: string | null;
  last_message?: string | null;
  last_sender_type?: string | null;
  last_message_at?: string | null;
  /** The newest line is a picture (its caption, if any, is last_message). */
  last_message_image?: boolean;
  unread_from_customer?: number;
  updated_at: string;
};

type ChatMessage = {
  id: string;
  sender_type: string;
  sender_chann_uid?: string | null;
  content: string;
  /** A picture on the thread (round 20T): a link good for a year. */
  image_url?: string | null;
  created_at: string;
};

const POLL_MS = 8000;
const PHONE = "(max-width: 759px)";
const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
const IMAGE_EDGE = 1600;

/**
 * A picture picked on the phone, shrunk in the browser before it travels:
 * a 4000-pixel camera JPEG is 3–6 MB, and the server would shrink it to
 * 1600 pixels anyway (chat_images.py) — doing it here first saves the
 * upload on a mobile connection and shows the preview instantly. Anything
 * that cannot be drawn (a HEIC the browser will not decode) goes up as it
 * is, and the server has the last word on what is and is not an image.
 */
async function shrinkImage(file: File): Promise<string> {
  const asDataUrl = () =>
    new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(file);
    });
  try {
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, IMAGE_EDGE / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const context = canvas.getContext("2d");
    if (!context) throw new Error("no canvas");
    context.fillStyle = "#fff";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close();
    return canvas.toDataURL("image/jpeg", 0.85);
  } catch {
    return asDataUrl();
  }
}

function clock(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString("th-TH", { hour: "2-digit", minute: "2-digit" });
}

function dayKey(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toDateString();
}

function dayLabel(iso: string, locale: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString(locale === "en" ? "en-GB" : "th-TH", {
    weekday: "short",
    day: "numeric",
    month: "short",
  });
}

function initials(name: string): string {
  const trimmed = name.trim();
  if (!trimmed) return "?";
  const parts = trimmed.split(/\s+/);
  return (parts[0].slice(0, 1) + (parts[1]?.slice(0, 1) ?? "")).toUpperCase();
}

function minutesSince(iso: string): number {
  return Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 60000));
}

/**
 * Phase 15 — the shop's inbox, laid out the way a live-chat console is
 * (Zoho SalesIQ as the reference): a conversation list on the left, the
 * open thread on the right, the composer pinned to the bottom of the
 * thread. On a phone the two panes become two screens.
 *
 * Sales/CS answer HERE, never in LINE directly. The first person to
 * answer owns the conversation; the SLA chip shows only while the
 * customer is the one waiting.
 *
 * Steadiness: the list and the thread poll every 8 s but only re-render
 * rows that changed, and the thread scrolls to the newest line only
 * when the reader was already at the bottom — text never moves under
 * a thumb that is reading or typing.
 */
export default function SalesChats({ liffId }: { liffId: string }) {
  const { t, locale } = useLanguage();
  const copy = t.dashboard.chats;

  const { request: confirming, ask, close: closeConfirm } = useConfirm();
  const [token, setToken] = useState("");
  const [licenseId, setLicenseId] = useState("");
  const [canReply, setCanReply] = useState(false);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [tab, setTab] = useState<"live" | "all">("live");
  // Loading and empty are two different things and looked identical: a tab
  // switch kept showing the previous tab's "ยังไม่มีแชท" until the new rows
  // landed, so a shop with no live conversations pressed "ทั้งหมด" and read
  // it as their chats having vanished (owner, 18 ก.ย. 2569). The eight-second
  // poll must NOT raise this — a list that blinks into skeletons every eight
  // seconds is worse than one that never says anything.
  const [loadingList, setLoadingList] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  // The picture waiting in the composer, as the data: URL that will be sent.
  const [attachment, setAttachment] = useState<{ dataUrl: string; name: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();
  const scroller = useRef<HTMLDivElement | null>(null);
  const pane = useRef<HTMLElement | null>(null);
  const fileInput = useRef<HTMLInputElement | null>(null);
  const stickToBottom = useRef(true);
  const lastMessageId = useRef<string>("");
  //: Bumped by every list request; a reply whose number is stale is ignored.
  const requestSeq = useRef(0);

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);

  const selected = sessions.find((s) => s.id === selectedId) ?? null;

  const loadSessions = useCallback(
    async (currentToken = token, license = licenseId, which = tab, announce = false) => {
      if (!currentToken || !license) return;
      // Whoever asked LAST owns the answer.
      //
      // Round 20i gave this a loading state and the symptom came back on
      // rapid tab switching (owner, 18 ก.ย. 2569), because two requests can
      // be in flight at once — a switch, and the 8-second poll that started
      // before it. Two things then went wrong: the slower reply overwrote
      // the faster one whatever tab it was for, and the FIRST `finally` to
      // run turned the skeleton off while the other was still coming, so an
      // empty older answer was shown as "ยังไม่มีการสนทนา".
      //
      // A sequence number fixes both: a reply that is not the newest
      // request is dropped on the floor, rows and spinner alike.
      const seq = ++requestSeq.current;
      if (announce) setLoadingList(true);
      try {
        const response = await fetch(
          `/api/phase2/licenses/${license}/chat-sessions?status_filter=${which === "all" ? "all" : "live"}`,
          { headers: proxyHeaders(currentToken, license) },
        );
        if (seq !== requestSeq.current) return;
        if (!response.ok) {
          throw new Error(
            response.status === 403
              ? t.dashboard.noPermission
              : `${t.dashboard.loadFailed} (${response.status})`,
          );
        }
        const rows = (await response.json()) as ChatSession[];
        if (seq !== requestSeq.current) return;
        // Same rows, same order → keep the old array so React skips the work.
        setSessions((prev) =>
          prev.length === rows.length &&
          prev.every((p, i) => p.id === rows[i].id && p.updated_at === rows[i].updated_at &&
            p.unread_from_customer === rows[i].unread_from_customer && p.status === rows[i].status)
            ? prev
            : rows,
        );
      } finally {
        // Only the newest request may put the skeleton away.
        if (announce && seq === requestSeq.current) setLoadingList(false);
      }
    },
    [token, licenseId, tab, t],
  );

  const loadThread = useCallback(
    async (sessionId: string, currentToken = token, license = licenseId) => {
      if (!currentToken || !license) return;
      const response = await fetch(
        `/api/phase2/licenses/${license}/chat-sessions/${sessionId}/messages`,
        { headers: proxyHeaders(currentToken, license) },
      );
      if (!response.ok) throw new Error(`${t.dashboard.loadFailed} (${response.status})`);
      const body = (await response.json()) as { session: ChatSession; messages: ChatMessage[] };
      const newest = body.messages[body.messages.length - 1]?.id ?? "";
      if (newest !== lastMessageId.current) {
        lastMessageId.current = newest;
        setMessages(body.messages);
      }
      setSessions((prev) => {
        const i = prev.findIndex((p) => p.id === body.session.id);
        if (i < 0) return prev;
        const same = prev[i].status === body.session.status && prev[i].updated_at === body.session.updated_at;
        if (same) return prev;
        const next = prev.slice();
        next[i] = { ...prev[i], ...body.session };
        return next;
      });
    },
    [token, licenseId, t],
  );

  // The shared session (review C4/C5): the shop, its permissions and the
  // suspended notice come from one place, and a switch starts over.
  const session = useSalesSession(liffId, say);
  useEffect(() => {
    if (!session.ready) return;
    setToken(session.token);
    setLicenseId(session.licenseId);
    setCanReply(!session.suspended && session.permissions.has("chat_session.reply"));
    loadSessions(session.token, session.licenseId, "live", true)
      .then(() => say(""))
      .catch((error: unknown) =>
        say(error instanceof Error ? error.message : t.dashboard.openFailed, "error"),
      );
  }, [session.ready, session.token, session.licenseId, session.permissions, session.suspended, loadSessions, say, t]);

  // Round 19g: opened from a job or a customer — that conversation is
  // selected on arrival (?session=<id>).
  useEffect(() => {
    if (!token || !licenseId) return;
    try {
      const wanted = new URLSearchParams(window.location.search).get("session");
      if (wanted && wanted !== selectedId) {
        setSelectedId(wanted);
        void loadThread(wanted).catch(() => undefined);
      }
    } catch {
      /* no query string to read */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, licenseId]);

  // The clock.
  useEffect(() => {
    if (!token || !licenseId) return;
    const timer = setInterval(() => {
      void loadSessions().catch(() => undefined);
      if (selectedId) void loadThread(selectedId).catch(() => undefined);
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [token, licenseId, selectedId, loadSessions, loadThread]);

  useEffect(() => {
    // A tab switch is a new question, so it gets a visible answer; the poll
    // above refreshes the same question quietly.
    void loadSessions(undefined, undefined, undefined, true).catch(() => {
      setLoadingList(false);
    });
  }, [tab, loadSessions]);

  // Scroll to the newest line only when the reader was already there.
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    if (stickToBottom.current) el.scrollTop = el.scrollHeight;
  }, [messages]);

  // On a phone the open conversation IS the screen (owner, 21 ก.ย. 2569:
  // "มีการสไลด์หน้าแชทกับตัวหน้า liff แยกกัน … ใช้งานยาก"). Before this the
  // thread was a box with its own scrollbar inside a page that also
  // scrolled, so a thumb reading the thread kept dragging the whole page,
  // and the composer sat wherever the page had scrolled to. Now the pane
  // is fixed over the page with ONE scroll region — the messages — the
  // page underneath is locked, and the pane is sized to the visual
  // viewport so the composer rides up with the keyboard instead of
  // disappearing behind it (iOS does not shrink 100dvh for the keyboard).
  useEffect(() => {
    if (!selectedId) return;
    const phone = window.matchMedia(PHONE);
    const viewport = window.visualViewport;
    const root = document.documentElement;
    const fit = () => {
      const el = pane.current;
      if (!el) return;
      if (!phone.matches) {
        root.removeAttribute("data-chat-open");
        el.style.height = "";
        el.style.top = "";
        return;
      }
      root.setAttribute("data-chat-open", "true");
      if (viewport) {
        el.style.height = `${Math.round(viewport.height)}px`;
        el.style.top = `${Math.round(viewport.offsetTop)}px`;
      }
      if (stickToBottom.current && scroller.current) {
        scroller.current.scrollTop = scroller.current.scrollHeight;
      }
    };
    fit();
    viewport?.addEventListener("resize", fit);
    viewport?.addEventListener("scroll", fit);
    phone.addEventListener("change", fit);
    return () => {
      viewport?.removeEventListener("resize", fit);
      viewport?.removeEventListener("scroll", fit);
      phone.removeEventListener("change", fit);
      root.removeAttribute("data-chat-open");
      const el = pane.current;
      if (el) {
        el.style.height = "";
        el.style.top = "";
      }
    };
  }, [selectedId]);

  function onScroll() {
    const el = scroller.current;
    if (!el) return;
    stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
  }

  async function open(session: ChatSession) {
    setSelectedId(session.id);
    setMessages([]);
    setAttachment(null);
    lastMessageId.current = "";
    stickToBottom.current = true;
    try {
      await loadThread(session.id);
      say("");
    } catch (error) {
      say(error instanceof Error ? error.message : t.dashboard.loadFailed, "error");
    }
  }

  async function pickImage(file: File | null) {
    if (fileInput.current) fileInput.current.value = "";
    if (!file) return;
    // The two refusals the server makes, said before the upload and in
    // the person's language (as the job photos do, review D12).
    if (file.size > MAX_IMAGE_BYTES) {
      say(copy.imageTooLarge, "error");
      return;
    }
    if (file.type && !file.type.startsWith("image/")) {
      say(copy.imageNotImage, "error");
      return;
    }
    try {
      setAttachment({ dataUrl: await shrinkImage(file), name: file.name });
      say(copy.imageReady, "ok");
    } catch {
      say(copy.imageNotImage, "error");
    }
  }

  async function reply() {
    if (!selected) return;
    const words = draft.trim();
    if (!words && !attachment) return;
    setBusy(true);
    try {
      // A picture goes with its caption in ONE line; words alone go the
      // usual way. Same conversation, same rules, one more shape.
      const response = attachment
        ? await fetch(`/api/phase2/licenses/${licenseId}/chat-sessions/${selected.id}/images`, {
            method: "POST",
            headers: { ...proxyHeaders(token, licenseId), "Content-Type": "application/json" },
            body: JSON.stringify({ image: attachment.dataUrl, caption: words || null }),
          })
        : await fetch(`/api/phase2/licenses/${licenseId}/chat-sessions/${selected.id}/messages`, {
            method: "POST",
            headers: { ...proxyHeaders(token, licenseId), "Content-Type": "application/json" },
            body: JSON.stringify({ content: words }),
          });
      if (!response.ok) {
        const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
        const detail = typeof body?.detail === "string" ? body.detail : "";
        if (detail.includes("10 MB")) throw new Error(copy.imageTooLarge);
        if (detail.includes("not an image")) throw new Error(copy.imageNotImage);
        throw new Error(copy.sendFailed);
      }
      setDraft("");
      setAttachment(null);
      stickToBottom.current = true;
      say("");
      await loadThread(selected.id);
      await loadSessions();
    } catch (error) {
      say(error instanceof Error && error.message ? error.message : copy.sendFailed, "error");
    } finally {
      setBusy(false);
    }
  }

  async function close() {
    if (!selected) return;
    const ok = await ask({
      action: copy.closeAction,
      target: selected.customer_name || copy.chatWord,
      affects: [copy.closeAffects],
      reversible: copy.closeKeeps,
      confirmLabel: copy.closeAction,
    });
    if (!ok) return;
    setBusy(true);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/chat-sessions/${selected.id}/close`,
        { method: "POST", headers: proxyHeaders(token, licenseId) },
      );
      if (!response.ok) throw new Error(String(response.status));
      await loadThread(selected.id);
      await loadSessions();
      say(copy.closedNow, "ok");
    } catch {
      say(copy.sendFailed, "error");
    } finally {
      setBusy(false);
    }
  }

  const statusLabel = (value: string) =>
    (copy.status as Record<string, string>)[value] ?? value;
  const isLive = (row: ChatSession) => row.status === "open" || row.status === "assigned";
  const slaChip = (row: ChatSession): { text: string; tone: "wait" | "late" } | null => {
    if (row.status === "unanswered") return { text: copy.status.unanswered, tone: "late" };
    if (!row.sla_deadline || !isLive(row)) return null;
    const deadline = new Date(row.sla_deadline).getTime();
    if (deadline < Date.now()) {
      return { text: copy.overdueBy.replace("{min}", String(minutesSince(row.sla_deadline))), tone: "late" };
    }
    return { text: copy.waiting, tone: "wait" };
  };
  const nameOf = (row: ChatSession) => row.customer_name || row.customer_chann_uid;

  const list = (
    <aside className="chat-list" data-hidden-on-phone={selectedId ? "true" : undefined}>
      <div className="chat-tabs" role="tablist" aria-label={copy.title}>
        {(["live", "all"] as const).map((key) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            data-active={tab === key ? "true" : undefined}
            onClick={() => setTab(key)}
          >
            {key === "live" ? copy.tabLive : copy.tabAll}
          </button>
        ))}
      </div>
      {loadingList ? (
        // Rows, not a spinner: the list keeps its shape, so nothing jumps
        // when the real rows arrive and nobody reads the pause as "gone".
        <ul className="chat-rows" aria-busy="true" aria-label={copy.loading}>
          {[0, 1, 2, 3].map((n) => (
            <li key={n} aria-hidden="true">
              <div className="chat-row chat-row-skeleton">
                <span className="avatar skeleton-block" />
                <span className="chat-row-main">
                  <span className="skeleton-line" style={{ width: "42%" }} />
                  <span className="skeleton-line" style={{ width: "78%" }} />
                </span>
              </div>
            </li>
          ))}
          <li className="chat-loading-note">{copy.loading}</li>
        </ul>
      ) : sessions.length === 0 ? (
        <div className="empty chat-empty">
          <p>{tab === "live" ? copy.emptyLive : copy.emptyAll}</p>
          <p className="card-meta">{tab === "live" ? copy.emptyLiveHint : copy.intro}</p>
        </div>
      ) : (
        <ul className="chat-rows">
          {sessions.map((row) => {
            const chip = slaChip(row);
            const unread = row.unread_from_customer ?? 0;
            return (
              <li key={row.id}>
                <button
                  type="button"
                  className="chat-row"
                  aria-current={selectedId === row.id ? "true" : undefined}
                  onClick={() => void open(row)}
                >
                  <span className="avatar" aria-hidden="true" data-live={isLive(row) ? "true" : undefined}>
                    {initials(nameOf(row))}
                  </span>
                  <span className="chat-row-main">
                    <span className="chat-row-top">
                      <span className="chat-row-name">{nameOf(row)}</span>
                      <span className="chat-row-time">{clock(row.last_message_at ?? row.updated_at)}</span>
                    </span>
                    <span className="chat-row-preview">
                      {row.last_sender_type === "agent" ? `${copy.shop}: ` : ""}
                      {row.last_message_image ? "📷 " : ""}
                      {row.last_message || (row.last_message_image ? copy.imageWord : statusLabel(row.status))}
                    </span>
                    <span className="chat-row-chips">
                      <span className="chip" data-tone={isLive(row) ? "live" : "muted"}>{statusLabel(row.status)}</span>
                      {chip && <span className="chip" data-tone={chip.tone}>{chip.text}</span>}
                      {unread > 0 && (
                        <span className="chip" data-tone="unread" role="status" aria-atomic="true">
                          {copy.unread.replace("{n}", String(unread))}
                        </span>
                      )}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </aside>
  );

  const groups: { key: string; label: string; items: ChatMessage[] }[] = [];
  for (const m of messages) {
    const key = dayKey(m.created_at);
    const last = groups[groups.length - 1];
    if (last && last.key === key) last.items.push(m);
    else groups.push({ key, label: dayLabel(m.created_at, locale), items: [m] });
  }

  const thread = selected ? (
    <section className="chat-thread-pane" aria-label={nameOf(selected)} ref={pane}>
      <header className="chat-thread-head">
        <button
          type="button"
          className="btn"
          data-variant="quiet"
          data-phone-only="true"
          onClick={() => setSelectedId(null)}
          aria-label={copy.backToList}
        >
          ←
        </button>
        <span className="avatar" aria-hidden="true" data-live={isLive(selected) ? "true" : undefined}>
          {initials(nameOf(selected))}
        </span>
        <div className="chat-thread-title">
          <strong>{nameOf(selected)}</strong>
          <span className="card-meta">
            {selected.customer_code ? `${selected.customer_code} · ` : ""}
            {statusLabel(selected.status)}
            {slaChip(selected) ? ` · ${slaChip(selected)?.text}` : ""}
          </span>
        </div>
        {/* Who this is, in the shop's own terms: their deals, jobs and
            notes, each a link to the record (owner, 20 ก.ย. 2569). */}
        <div className="chat-thread-tools">
          <CustomerContext
            token={session.token}
            licenseId={session.licenseId}
            permissions={session.permissions}
            name={nameOf(selected)}
            recordId={selected.customer_record_id ?? null}
          />
          {isLive(selected) && canReply && (
            <button type="button" className="btn" data-variant="quiet" disabled={busy} onClick={() => void close()}>
              {copy.close}
            </button>
          )}
        </div>
      </header>
      <div className="chat-scroll" ref={scroller} onScroll={onScroll}>
        {messages.length === 0 ? (
          <p className="chat-day">{copy.noMessages}</p>
        ) : (
          groups.map((g) => (
            <div key={g.key}>
              <p className="chat-day">{g.label}</p>
              {g.items.map((m) => (
                <div
                  key={m.id}
                  className="bubble"
                  data-side={m.sender_type === "customer" ? "them" : m.sender_type === "agent" ? "us" : "system"}
                >
                  {m.image_url && (
                    // The picture opens full size in a new tab; the bubble
                    // shows it at bubble width, never wider than the thread.
                    <a className="bubble-image" href={m.image_url} target="_blank" rel="noreferrer">
                      <img src={m.image_url} alt={copy.imageAlt} loading="lazy" />
                    </a>
                  )}
                  {m.content && <div className="bubble-body">{m.content}</div>}
                  <div className="bubble-meta">
                    {m.sender_type === "customer" ? copy.customer : m.sender_type === "agent" ? copy.shop : statusLabel(m.sender_type)}
                    {" · "}
                    {clock(m.created_at)}
                  </div>
                </div>
              ))}
            </div>
          ))
        )}
      </div>
      {!isLive(selected) && canReply && (
        <p className="chat-note" data-tone="info">{copy.parkedNote}</p>
      )}
      {canReply ? (
        (
          <form
            className="chat-composer"
            data-attached={attachment ? "true" : undefined}
            onSubmit={(e) => {
              e.preventDefault();
              void reply();
            }}
          >
            {attachment && (
              /* The picture waits here until it is sent, with its name and
                 a way to take it back; the words typed below become its
                 caption. */
              <div className="chat-attachment" role="group" aria-label={copy.attachImage}>
                <img src={attachment.dataUrl} alt="" />
                <span className="chat-attachment-name">{attachment.name}</span>
                <button
                  type="button"
                  className="btn"
                  data-variant="quiet"
                  onClick={() => setAttachment(null)}
                  disabled={busy}
                  aria-label={copy.removeImage}
                >
                  ✕
                </button>
              </div>
            )}
            <div className="chat-composer-row">
              <input
                ref={fileInput}
                type="file"
                accept="image/*"
                className="sr-only"
                id="chat-image"
                onChange={(e) => void pickImage(e.target.files?.[0] ?? null)}
                disabled={busy}
              />
              {/* A real button for the file picker, not a styled label:
                  the keyboard reaches it and the 44px hit area is its own
                  (ui-ux-pro-max, Touch & Interaction). */}
              <button
                type="button"
                className="btn chat-attach"
                data-variant="quiet"
                aria-label={copy.attachImage}
                title={copy.attachImage}
                disabled={busy}
                onClick={() => fileInput.current?.click()}
              >
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <rect x="3" y="5" width="18" height="14" rx="2" />
                  <circle cx="8.5" cy="10" r="1.5" />
                  <path d="M21 15l-5-5-8 8" />
                </svg>
              </button>
              <label className="sr-only" htmlFor="chat-draft">{copy.replyPlaceholder}</label>
              <textarea
                id="chat-draft"
                rows={1}
                value={draft}
                placeholder={attachment ? copy.captionPlaceholder : copy.replyPlaceholder}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void reply();
                  }
                }}
              />
              <button
                type="submit"
                className="btn"
                data-variant="primary"
                disabled={busy || (!draft.trim() && !attachment)}
              >
                {busy ? t.dashboard.related.saving : attachment ? copy.sendImage : copy.send}
              </button>
            </div>
          </form>
        )
      ) : (
        <p className="chat-note">{copy.readOnly}</p>
      )}
    </section>
  ) : (
    <section className="chat-thread-pane chat-thread-idle" aria-label={copy.title}>
      <p className="card-meta">{copy.pickOne}</p>
    </section>
  );

  return (
    <SalesShell
      session={session}
      title={copy.title}
      liffId={liffId}
      onSdkError={() => say(t.dashboard.openFailed, "error")}
      status={status}
      statusTone={tone}
      guideHref="/liff/sales/guide"
      wide
    >
      <div className="chat-layout" data-thread-open={selectedId ? "true" : undefined}>
        {list}
        {thread}
      </div>
      <ConfirmDialog request={confirming} onClose={closeConfirm} busy={Boolean(busy)} />
    </SalesShell>
  );
}
