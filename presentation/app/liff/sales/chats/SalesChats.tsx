"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { AppShell } from "../_components";
import { fetchPermissions, initLiffSession, proxyHeaders } from "../_lib";

type ChatSession = {
  id: string;
  customer_chann_uid: string;
  customer_name?: string | null;
  status: string;
  assigned_to?: string | null;
  sla_deadline?: string | null;
  escalated_at?: string | null;
  last_message?: string | null;
  last_sender_type?: string | null;
  last_message_at?: string | null;
  unread_from_customer?: number;
  updated_at: string;
};

type ChatMessage = {
  id: string;
  sender_type: string;
  sender_chann_uid?: string | null;
  content: string;
  created_at: string;
};

const POLL_MS = 8000;

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

  const [token, setToken] = useState("");
  const [licenseId, setLicenseId] = useState("");
  const [canReply, setCanReply] = useState(false);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [tab, setTab] = useState<"live" | "all">("live");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();
  const scroller = useRef<HTMLDivElement | null>(null);
  const stickToBottom = useRef(true);
  const lastMessageId = useRef<string>("");

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);

  const selected = sessions.find((s) => s.id === selectedId) ?? null;

  const loadSessions = useCallback(
    async (currentToken = token, license = licenseId, which = tab) => {
      if (!currentToken || !license) return;
      const response = await fetch(
        `/api/phase2/licenses/${license}/chat-sessions?status_filter=${which === "all" ? "all" : "live"}`,
        { headers: proxyHeaders(currentToken, license) },
      );
      if (!response.ok) {
        throw new Error(
          response.status === 403
            ? t.dashboard.noPermission
            : `${t.dashboard.loadFailed} (${response.status})`,
        );
      }
      const rows = (await response.json()) as ChatSession[];
      // Same rows, same order → keep the old array so React skips the work.
      setSessions((prev) =>
        prev.length === rows.length &&
        prev.every((p, i) => p.id === rows[i].id && p.updated_at === rows[i].updated_at &&
          p.unread_from_customer === rows[i].unread_from_customer && p.status === rows[i].status)
          ? prev
          : rows,
      );
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

  const initialize = useCallback(async () => {
    try {
      const session = await initLiffSession(liffId);
      if (!session.token) return;
      const license = session.memberships[0]?.license_id ?? "";
      if (!license) {
        say(t.liff.noCompany, "error");
        return;
      }
      setToken(session.token);
      setLicenseId(license);
      const permissions = await fetchPermissions(session.token, license);
      setCanReply(permissions.has("chat_session.reply"));
      await loadSessions(session.token, license, "live");
      say("");
    } catch (error) {
      say(error instanceof Error ? error.message : t.dashboard.openFailed, "error");
    }
  }, [liffId, loadSessions, say, t]);

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
    void loadSessions().catch(() => undefined);
  }, [tab, loadSessions]);

  // Scroll to the newest line only when the reader was already there.
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    if (stickToBottom.current) el.scrollTop = el.scrollHeight;
  }, [messages]);

  function onScroll() {
    const el = scroller.current;
    if (!el) return;
    stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
  }

  async function open(session: ChatSession) {
    setSelectedId(session.id);
    setMessages([]);
    lastMessageId.current = "";
    stickToBottom.current = true;
    try {
      await loadThread(session.id);
      say("");
    } catch (error) {
      say(error instanceof Error ? error.message : t.dashboard.loadFailed, "error");
    }
  }

  async function reply() {
    if (!selected || !draft.trim()) return;
    setBusy(true);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/chat-sessions/${selected.id}/messages`,
        {
          method: "POST",
          headers: { ...proxyHeaders(token, licenseId), "Content-Type": "application/json" },
          body: JSON.stringify({ content: draft.trim() }),
        },
      );
      if (!response.ok) throw new Error(String(response.status));
      setDraft("");
      stickToBottom.current = true;
      await loadThread(selected.id);
      await loadSessions();
    } catch {
      say(copy.sendFailed, "error");
    } finally {
      setBusy(false);
    }
  }

  async function close() {
    if (!selected) return;
    if (!window.confirm(copy.closeConfirm)) return;
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
      {sessions.length === 0 ? (
        <div className="empty chat-empty">
          <p>{copy.empty}</p>
          <p className="card-meta">{copy.intro}</p>
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
                      {row.last_message || statusLabel(row.status)}
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
    <section className="chat-thread-pane" aria-label={nameOf(selected)}>
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
            {statusLabel(selected.status)}
            {slaChip(selected) ? ` · ${slaChip(selected)?.text}` : ""}
          </span>
        </div>
        {isLive(selected) && canReply && (
          <button type="button" className="btn" data-variant="quiet" disabled={busy} onClick={() => void close()}>
            {copy.close}
          </button>
        )}
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
                  <div className="bubble-body">{m.content}</div>
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
            onSubmit={(e) => {
              e.preventDefault();
              void reply();
            }}
          >
            <label className="sr-only" htmlFor="chat-draft">{copy.replyPlaceholder}</label>
            <textarea
              id="chat-draft"
              rows={1}
              value={draft}
              placeholder={copy.replyPlaceholder}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void reply();
                }
              }}
            />
            <button type="submit" className="btn" data-variant="primary" disabled={busy || !draft.trim()}>
              {busy ? t.dashboard.related.saving : copy.send}
            </button>
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
    <AppShell
      title={copy.title}
      liffId={liffId}
      onReady={initialize}
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
    </AppShell>
  );
}
