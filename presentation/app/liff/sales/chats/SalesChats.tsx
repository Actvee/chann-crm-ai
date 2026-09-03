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

function when(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString("th-TH", { hour: "2-digit", minute: "2-digit" });
}

/**
 * Phase 15 — the shop's side of live chat (Master Spec 15.4).
 *
 * Sales/CS answer HERE, never in LINE directly: an answer typed into the
 * Sales OA reaches nobody, because the customer is on the Customer OA.
 * Whoever answers first owns the conversation; the SLA clock shows only
 * while the customer is the one waiting.
 *
 * Polling, not sockets: a LIFF page lives in a phone's browser for a
 * couple of minutes at a time, and the Application tier is stateless
 * Cloud Run. Eight seconds is fast enough for a person, cheap enough for
 * a shop with three people watching.
 */
export default function SalesChats({ liffId }: { liffId: string }) {
  const { t } = useLanguage();
  const copy = t.dashboard.chats;

  const [token, setToken] = useState("");
  const [licenseId, setLicenseId] = useState("");
  const [canReply, setCanReply] = useState(false);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [showClosed, setShowClosed] = useState(false);
  const [selected, setSelected] = useState<ChatSession | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();
  const threadEnd = useRef<HTMLLIElement | null>(null);

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);

  const loadSessions = useCallback(
    async (currentToken = token, license = licenseId, closed = showClosed) => {
      if (!currentToken || !license) return;
      const headers = proxyHeaders(currentToken, license);
      const filter = closed ? "all" : "live";
      const response = await fetch(
        `/api/phase2/licenses/${license}/chat-sessions?status_filter=${filter}`,
        { headers },
      );
      if (!response.ok) {
        throw new Error(
          response.status === 403
            ? t.dashboard.noPermission
            : `${t.dashboard.loadFailed} (${response.status})`,
        );
      }
      const rows = (await response.json()) as ChatSession[];
      setSessions(rows);
      setSelected((current) => (current ? rows.find((r) => r.id === current.id) ?? current : current));
    },
    [token, licenseId, showClosed, t],
  );

  const loadThread = useCallback(
    async (session: ChatSession, currentToken = token, license = licenseId) => {
      if (!currentToken || !license) return;
      const response = await fetch(
        `/api/phase2/licenses/${license}/chat-sessions/${session.id}/messages`,
        { headers: proxyHeaders(currentToken, license) },
      );
      if (!response.ok) throw new Error(`${t.dashboard.loadFailed} (${response.status})`);
      const body = (await response.json()) as { session: ChatSession; messages: ChatMessage[] };
      setMessages(body.messages);
      setSelected(body.session);
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
      await loadSessions(session.token, license, false);
      say("");
    } catch (error) {
      say(error instanceof Error ? error.message : t.dashboard.openFailed, "error");
    }
  }, [liffId, loadSessions, say, t]);

  // The clock: the list every few seconds, the open thread with it.
  useEffect(() => {
    if (!token || !licenseId) return;
    const timer = setInterval(() => {
      void loadSessions().catch(() => undefined);
      if (selected && (selected.status === "open" || selected.status === "assigned")) {
        void loadThread(selected).catch(() => undefined);
      }
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [token, licenseId, selected, loadSessions, loadThread]);

  useEffect(() => {
    threadEnd.current?.scrollIntoView({ block: "end" });
  }, [messages.length]);

  async function open(session: ChatSession) {
    setSelected(session);
    setMessages([]);
    try {
      await loadThread(session);
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
      if (response.status === 409) {
        say(copy.closedAlready, "error");
        return;
      }
      if (!response.ok) throw new Error(String(response.status));
      setDraft("");
      await loadThread(selected);
      await loadSessions();
      say(copy.sent, "ok");
    } catch {
      say(copy.sendFailed, "error");
    } finally {
      setBusy(false);
    }
  }

  async function close() {
    if (!selected) return;
    setBusy(true);
    try {
      const response = await fetch(
        `/api/phase2/licenses/${licenseId}/chat-sessions/${selected.id}/close`,
        { method: "POST", headers: proxyHeaders(token, licenseId) },
      );
      if (!response.ok) throw new Error(String(response.status));
      await loadThread(selected);
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
  const overdue = (row: ChatSession) =>
    !!row.sla_deadline && new Date(row.sla_deadline).getTime() < Date.now();
  const live = selected ? selected.status === "open" || selected.status === "assigned" : false;

  return (
    <AppShell
      title={copy.title}
      liffId={liffId}
      onReady={initialize}
      onSdkError={() => say(t.dashboard.openFailed, "error")}
      status={status}
      statusTone={tone}
    >
      <section className="section">
        <div className="section-head">
          <h2>
            {copy.title} ({sessions.length})
          </h2>
          <label className="card-meta">
            <input
              type="checkbox"
              checked={showClosed}
              onChange={(e) => {
                setShowClosed(e.target.checked);
                void loadSessions(token, licenseId, e.target.checked).catch(() => undefined);
              }}
            />{" "}
            {copy.showClosed}
          </label>
        </div>
        <p className="card-meta">{copy.intro}</p>
        {sessions.length === 0 ? (
          <div className="empty">
            <p>{copy.empty}</p>
          </div>
        ) : (
          <ul className="list">
            {sessions.map((row) => (
              <li
                key={row.id}
                className="card"
                data-selected={selected?.id === row.id ? "true" : undefined}
              >
                <button
                  type="button"
                  className="card-button"
                  onClick={() => void open(row)}
                  aria-current={selected?.id === row.id ? "true" : undefined}
                >
                  <div className="card-title">
                    {row.customer_name || row.customer_chann_uid}
                    {row.unread_from_customer ? (
                      <span className="badge" data-tone="warn">
                        {" "}
                        {row.unread_from_customer}
                      </span>
                    ) : null}
                  </div>
                  <div className="card-meta">
                    {statusLabel(row.status)}
                    {row.sla_deadline && (row.status === "open" || row.status === "assigned")
                      ? ` · ${overdue(row) ? copy.overdue : copy.waiting}`
                      : ""}
                    {row.last_message ? ` · ${row.last_message.slice(0, 60)}` : ""}
                    {row.last_message_at ? ` · ${when(row.last_message_at)}` : ""}
                  </div>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {selected && (
        <section className="section">
          <div className="section-head">
            <h2>{selected.customer_name || selected.customer_chann_uid}</h2>
            <span className="card-meta">{statusLabel(selected.status)}</span>
          </div>
          {messages.length === 0 ? (
            <div className="empty">
              <p>{copy.noMessages}</p>
            </div>
          ) : (
            <ul className="list chat-thread">
              {messages.map((m) => (
                <li key={m.id} className="card chat-line" data-sender={m.sender_type}>
                  <div className="card-meta">
                    {m.sender_type === "customer" ? copy.customer : copy.shop} · {when(m.created_at)}
                  </div>
                  <div>{m.content}</div>
                </li>
              ))}
              <li ref={threadEnd} aria-hidden="true" />
            </ul>
          )}
          {live ? (
            canReply ? (
              <div className="fields">
                <textarea
                  rows={3}
                  value={draft}
                  placeholder={copy.replyPlaceholder}
                  onChange={(e) => setDraft(e.target.value)}
                />
                <div className="actions">
                  <button
                    type="button"
                    className="btn"
                    data-variant="primary"
                    disabled={busy || !draft.trim()}
                    onClick={() => void reply()}
                  >
                    {copy.send}
                  </button>
                  <button type="button" className="btn" disabled={busy} onClick={() => void close()}>
                    {copy.close}
                  </button>
                </div>
              </div>
            ) : (
              <p className="card-meta">{copy.readOnly}</p>
            )
          ) : (
            <p className="card-meta">{copy.closedAlready}</p>
          )}
        </section>
      )}
    </AppShell>
  );
}
