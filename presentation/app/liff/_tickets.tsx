"use client";

import Link from "next/link";
import { ReactNode } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";
import { shortDate } from "./_list-controls";

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
  /** The registered unit the fault is about — composed by the Application
   *  tier from the warranty behind `serial_number` (owner, 10 ก.ย. 2569),
   *  never stored on the ticket row. Absent on every ticket opened before
   *  the link existed, and on any fault filed without a serial. */
  product_name?: string | null;
  warranty_number?: string | null;
  warranty_end?: string | null;
  /** "active" | "expired" | "void", derived by the Data tier from the real
   *  end date on the Bangkok calendar — not recomputed here. */
  warranty_status?: string | null;
  scheduled_date?: string | null;
  scheduled_time?: string | null;
  assigned_target_type?: string | null;
  assigned_to_ref?: string | null;
  /** TicketOut.created_at — the queue merges per-status fetches on it. */
  created_at?: string | null;
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

/** "แอร์ 12000 BTU (S/N SN12345678) · อยู่ในประกันถึง 1 ม.ค. 70", or "" when
 *  the ticket names no machine. Every ticket opened before the link
 *  existed keeps a NULL product, so the caller drops the line entirely
 *  rather than printing a label with nothing after it. */
export function machineLine(
  ticket: Ticket,
  copy: { machine: string; inWarranty: string; outOfWarranty: string },
  locale?: string,
): string {
  const serial = (ticket.serial_number ?? "").trim();
  const name = (ticket.product_name ?? "").trim();
  if (!serial && !name) return "";
  let text = name || serial;
  if (name && serial) text = `${name} (S/N ${serial})`;
  if (ticket.warranty_status === "active" && ticket.warranty_end) {
    text = `${text} · ${copy.inWarranty} ${shortDate(ticket.warranty_end, locale)}`;
  } else if (ticket.warranty_status === "expired" || ticket.warranty_status === "void") {
    text = `${text} · ${copy.outOfWarranty}`;
  }
  return `${copy.machine}: ${text}`;
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
  const { t, locale } = useLanguage();
  // Which machine, and whether it is still covered: a technician looking
  // at their queue should not have to open the job to find out.
  const machine = machineLine(ticket, t.dashboard.tickets, locale);
  const body = (
    <span className="row-body">
      <span className="card-title">
        <span className="code">{ticket.ticket_number}</span>
        <span className="badge" data-stage={ticketStage(ticket.status)}>
          {statusLabel}
        </span>
      </span>
      <span className="card-meta">{ticket.issue_description}</span>
      {machine && <span className="card-meta">{machine}</span>}
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
