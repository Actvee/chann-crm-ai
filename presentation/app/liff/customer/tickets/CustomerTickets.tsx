"use client";

import { useCallback, useEffect, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { AppShell } from "../../sales/_components";
import { Ticket, formatWhen, ticketStage } from "../../_tickets";
import { initLiffSession, proxyHeaders } from "../../_shared";

/**
 * What a customer opens to check on a repair they reported.
 *
 * Read-only by design. Reporting a fault happens in chat, where someone
 * can describe a problem in their own words and be asked follow-up
 * questions; a form would demand they classify it first, which is exactly
 * the thing they came to a person for.
 *
 * Shows less than the technician view on purpose. A customer needs to
 * know what was reported, when someone is coming, and whether it is done.
 * Which technician took it, and their workload, is not their business.
 */
export default function CustomerTickets({ liffId }: { liffId: string }) {
  const { t } = useLanguage();
  const statusLabel = (status: string) =>
    (t.dashboard.tickets.status as Record<string, string>)[status] ?? status;

  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);

  const initialize = useCallback(async () => {
    try {
      const session = await initLiffSession(liffId, "customer");
      if (!session.token) return;
      const license = session.memberships[0]?.license_id ?? "";
      if (!license) {
        say(t.liff.noCompany, "error");
        return;
      }
      const response = await fetch(
        `/api/phase2/licenses/${license}/tickets`,
        { headers: proxyHeaders(session.token, license, "customer") },
      );
      if (!response.ok) {
        say(
          response.status === 403
            ? t.dashboard.noPermission
            : `${t.dashboard.loadFailed} (${response.status})`,
          "error",
        );
        return;
      }
      setTickets((await response.json()) as Ticket[]);
      say("");
    } catch (error) {
      say(error instanceof Error ? error.message : t.dashboard.openFailed, "error");
    }
  }, [liffId, say, t]);

  return (
    <AppShell
      title={t.dashboard.tickets.title}
      liffId={liffId}
      onReady={() => void initialize()}
      onSdkError={() => say(t.liff.sdkLoadFailed, "error")}
      status={status}
      statusTone={tone}
    >
      {tickets.length === 0 ? (
        <div className="empty">
          <p>{t.dashboard.tickets.empty}</p>
          <p style={{ fontSize: 13.5, color: "var(--ink-faint)" }}>
            {t.dashboard.tickets.reportHint}
          </p>
        </div>
      ) : (
        <ul className="list">
          {tickets.map((ticket) => (
            <li key={ticket.id} className="card" data-stage={ticketStage(ticket.status)}>
              <div className="card-title">
                <span className="code">{ticket.ticket_number}</span>
                <span className="badge" data-stage={ticketStage(ticket.status)}>
                  {statusLabel(ticket.status)}
                </span>
              </div>
              <div className="card-meta">{ticket.issue_description}</div>
              {ticket.scheduled_date && (
                <div className="card-meta">
                  {t.dashboard.tickets.scheduled}: {formatWhen(ticket)}
                </div>
              )}
              {ticket.serial_number && (
                <div className="card-meta">
                  {t.dashboard.tickets.serial}: {ticket.serial_number}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </AppShell>
  );
}
