"use client";

import { useCallback, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { AppShell } from "../_components";
import { Ticket, formatWhen, ticketStage } from "../../_tickets";
import { fetchPermissions, initLiffSession, proxyHeaders } from "../_lib";

/**
 * The dispatcher's view: which tickets are waiting, and what is stopping
 * each one from being sent.
 *
 * The blockers are shown on the row rather than only when someone presses
 * assign. A CS person scanning the queue can then fix the gaps in one
 * pass, instead of discovering them one refusal at a time.
 */
export default function SalesTickets({ liffId }: { liffId: string }) {
  const { t } = useLanguage();
  const statusLabel = (status: string) =>
    (t.dashboard.tickets.status as Record<string, string>)[status] ?? status;

  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [blockers, setBlockers] = useState<Record<string, string[]>>({});
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);

  const initialize = useCallback(async () => {
    try {
      const session = await initLiffSession(liffId);
      if (!session.token) return;
      const license = session.memberships[0]?.license_id ?? "";
      if (!license) {
        say(t.liff.noCompany, "error");
        return;
      }
      const headers = proxyHeaders(session.token, license);
      const response = await fetch(
        `/api/phase2/licenses/${license}/tickets`, { headers },
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
      const rows = (await response.json()) as Ticket[];
      setTickets(rows);
      say("");

      // Only for the ones still waiting to go out; a dispatched ticket's
      // gate result is history and not worth a request each.
      const waiting = rows.filter((row) => row.status === "open");
      const found: Record<string, string[]> = {};
      await Promise.all(
        waiting.map(async (row) => {
          const check = await fetch(
            `/api/phase2/licenses/${license}/tickets/${row.id}/dispatch-check`,
            { headers },
          );
          if (check.ok) {
            const body = (await check.json()) as { missing?: string[] };
            if (body.missing?.length) found[row.id] = body.missing;
          }
        }),
      );
      setBlockers(found);
    } catch (error) {
      say(error instanceof Error ? error.message : t.dashboard.openFailed, "error");
    }
  }, [liffId, say, t]);

  return (
    <AppShell
      title={t.dashboard.tickets.title}
      back="/liff/sales"
      liffId={liffId}
      onReady={() => void initialize()}
      onSdkError={() => say(t.liff.sdkLoadFailed, "error")}
      status={status}
      statusTone={tone}
    >
      {tickets.length === 0 ? (
        <div className="empty">
          <p>{t.dashboard.tickets.empty}</p>
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
              {ticket.customer_name && (
                <div className="card-meta">
                  {ticket.customer_name}
                  {ticket.customer_phone ? ` · ${ticket.customer_phone}` : ""}
                </div>
              )}
              {ticket.scheduled_date && (
                <div className="card-meta">
                  {t.dashboard.tickets.scheduled}: {formatWhen(ticket)}
                </div>
              )}
              {blockers[ticket.id] && (
                <div className="card-meta" data-tone="error">
                  {t.dashboard.tickets.blocked}: {blockers[ticket.id].join(", ")}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </AppShell>
  );
}
