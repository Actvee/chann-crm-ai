"use client";

import Link from "next/link";
import { ReactNode } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

/**
 * Ticket presentation, shared by the technician and customer dashboards.
 *
 * One component rather than one per audience, because the two views differ
 * in WHAT they may do with a ticket, not in what a ticket looks like. A
 * technician sees a claim button and a customer does not; both see the
 * same number, status and appointment, and a copy per audience would let
 * those drift into disagreeing about the same row.
 */

export type Ticket = {
  id: string;
  ticket_number: string;
  status: string;
  visibility: string;
  accept_status: string;
  issue_description: string;
  customer_name?: string | null;
  customer_phone?: string | null;
  service_address?: string | null;
  serial_number?: string | null;
  scheduled_date?: string | null;
  scheduled_time?: string | null;
  assigned_target_type?: string | null;
  assigned_to_ref?: string | null;
};

/** Status to the stage rail's vocabulary, so the colour means the same
 *  thing here as on a deal or a quote. */
export function ticketStage(status: string): string {
  return (
    {
      open: "new",
      assigned: "proposed",
      in_progress: "proposed",
      completed: "won",
      cancelled: "lost",
    }[status] ?? ""
  );
}

export function formatWhen(ticket: Ticket): string {
  const parts: string[] = [];
  if (ticket.scheduled_date) parts.push(ticket.scheduled_date);
  if (ticket.scheduled_time) parts.push(String(ticket.scheduled_time).slice(0, 5));
  return parts.join(" ") || "—";
}

export function TicketRow({
  ticket,
  href,
  statusLabel,
  action,
}: {
  ticket: Ticket;
  href?: string;
  statusLabel: string;
  action?: ReactNode;
}) {
  const { t } = useLanguage();
  const body = (
    <span className="row-body">
      <span className="card-title">
        <span className="code">{ticket.ticket_number}</span>
        <span className="badge" data-stage={ticketStage(ticket.status)}>
          {statusLabel}
        </span>
      </span>
      <span className="card-meta">{ticket.issue_description}</span>
      {ticket.scheduled_date && (
        <span className="card-meta">
          {t.dashboard.tickets.scheduled}: {formatWhen(ticket)}
        </span>
      )}
    </span>
  );

  return (
    <li className="card" data-stage={ticketStage(ticket.status)}>
      {href ? (
        <Link className="row-link" href={href}>
          {body}
        </Link>
      ) : (
        body
      )}
      {action && <div className="card-actions">{action}</div>}
    </li>
  );
}
