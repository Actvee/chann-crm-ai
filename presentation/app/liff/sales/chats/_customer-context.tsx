"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { Sheet } from "../../_sheet";
import { fullDateTime } from "../../_list-controls";
import { Badge } from "../_components";
import { RelatedHeading } from "../_record";
import { useFormatters } from "../_format";
import { dealValue, hasDealValue } from "../_deal-value";
import { proxyHeaders } from "../_lib";

type CustomerRow = {
  id: string;
  customer_id?: string;
  first_name?: string | null;
  last_name?: string | null;
  phone?: string | null;
  email?: string | null;
  stage?: string;
};
type DealRow = {
  id: string;
  deal_id: string;
  stage: string;
  amount?: number | string | null;
  currency?: string | null;
  products?: { qty?: number; quoted_unit_price?: string | number }[];
};
type TicketRow = { id: string; ticket_number: string; status: string; issue?: string | null; product_name?: string | null };
type NoteRow = { id: string; body?: string | null; created_at?: string | null; author_display_name?: string | null };

/**
 * The customer, beside the conversation.
 *
 * Owner, 20 ก.ย. 2569: a person answering a chat needs to know who this
 * is in the shop's terms — which deals are open with them, which jobs,
 * what was noted last time — and to get to those records in one tap.
 * The chat page showed a name and nothing else; the answer to "is this
 * the one whose air-con we fixed last month" was three screens away.
 *
 * One button in the thread head, one sheet: the record's own line with
 * a link to its page, then deals, jobs and notes, each row a link. Every
 * list is gated on the key its route checks and loads on its own, so a
 * failed notes call does not take the deals down with it.
 *
 * A conversation with someone who is NOT in the contact book says so and
 * says where to add them, rather than showing three empty lists.
 */
export function CustomerContext({
  token,
  licenseId,
  permissions,
  name,
  recordId,
}: {
  token: string;
  licenseId: string;
  permissions: Set<string>;
  name: string;
  recordId: string | null;
}) {
  const { t } = useLanguage();
  const copy = t.dashboard.chats;
  const [open, setOpen] = useState(false);

  return (
    <>
      <button type="button" className="btn" data-variant="quiet" onClick={() => setOpen(true)}>
        {copy.customerInfo}
      </button>
      <Sheet open={open} title={name} onClose={() => setOpen(false)}>
        {recordId ? (
          <ContextBody token={token} licenseId={licenseId} permissions={permissions} recordId={recordId} />
        ) : (
          <div className="empty">
            <p>{copy.noRecord}</p>
          </div>
        )}
      </Sheet>
    </>
  );
}

function ContextBody({
  token,
  licenseId,
  permissions,
  recordId,
}: {
  token: string;
  licenseId: string;
  permissions: Set<string>;
  recordId: string;
}) {
  const { t, locale } = useLanguage();
  const { money } = useFormatters();
  const copy = t.dashboard.chats;
  const [customer, setCustomer] = useState<CustomerRow | null>(null);
  const [deals, setDeals] = useState<DealRow[] | null>(null);
  const [tickets, setTickets] = useState<TicketRow[] | null>(null);
  const [notes, setNotes] = useState<NoteRow[] | null>(null);
  const [failed, setFailed] = useState(false);

  const canDeals = permissions.has("deal.read");
  const canTickets = permissions.has("ticket.read");

  useEffect(() => {
    if (!token || !licenseId || !recordId) return;
    const headers = proxyHeaders(token, licenseId);
    let cancelled = false;

    async function fetchRows<T>(url: string, set: (rows: T[]) => void) {
      try {
        const response = await fetch(url, { headers, cache: "no-store" });
        if (cancelled) return;
        set(response.ok ? ((await response.json()) as T[]) : []);
      } catch {
        if (!cancelled) set([]);
      }
    }

    void (async () => {
      try {
        const response = await fetch(`/api/phase2/licenses/${licenseId}/customers/${recordId}`, { headers, cache: "no-store" });
        if (cancelled) return;
        if (!response.ok) throw new Error(String(response.status));
        setCustomer((await response.json()) as CustomerRow);
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    if (canDeals) void fetchRows<DealRow>(`/api/phase2/licenses/${licenseId}/deals?contact_id=${recordId}`, setDeals);
    if (canTickets) void fetchRows<TicketRow>(`/api/phase2/licenses/${licenseId}/tickets?contact_id=${recordId}&limit=20`, setTickets);
    void fetchRows<NoteRow>(`/api/phase2/licenses/${licenseId}/notes?entity_type=customer&entity_id=${recordId}`, setNotes);

    return () => {
      cancelled = true;
    };
  }, [token, licenseId, recordId, canDeals, canTickets]);

  const stageLabel = (stage: string) => (t.deal.stage as Record<string, string>)[stage] ?? stage;
  const ticketStatus = (status: string) =>
    (t.dashboard.tickets.status as Record<string, string>)[status] ?? status;

  if (failed) {
    return (
      <div className="empty">
        <p>{copy.contextFailed}</p>
      </div>
    );
  }

  return (
    <>
      {customer && (
        <div className="context-head">
          <strong>
            {`${customer.first_name ?? ""} ${customer.last_name ?? ""}`.trim() || customer.customer_id}
            {customer.stage && (
              <Badge
                stage={customer.stage}
                label={customer.stage === "contact" ? t.customer.title : t.customer.lead}
              />
            )}
          </strong>
          <span className="card-meta">
            <span className="code">{customer.customer_id}</span>
            {customer.phone ? ` · ${customer.phone}` : ""}
            {customer.email ? ` · ${customer.email}` : ""}
          </span>
          <div className="actions" style={{ marginTop: 6 }}>
            <Link className="btn" href={`/liff/sales/customers/${customer.id}`}>
              {copy.openCustomer}
            </Link>
          </div>
        </div>
      )}

      {canDeals && (
        <>
          <RelatedHeading title={copy.deals} count={deals?.length ?? 0} />
          {deals === null ? null : deals.length === 0 ? (
            <p className="card-meta" style={{ margin: "0 0 14px" }}>{copy.noDeals}</p>
          ) : (
            <ul className="context-list">
              {deals.map((deal) => (
                <li key={deal.id}>
                  <Link className="row-link" href={`/liff/sales/deals/${deal.id}`}>
                    <span className="row-body">
                      <span className="card-title">
                        <span className="code">{deal.deal_id}</span>
                        <Badge stage={deal.stage} label={stageLabel(deal.stage)} />
                      </span>
                      {/* The one rule (final review I4): a deal valued by
                          its lines has a value too. */}
                      {hasDealValue(deal) && (
                        <span className="card-meta">
                          {money(dealValue(deal), 0)} {deal.currency ?? "THB"}
                        </span>
                      )}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      {canTickets && (
        <>
          <RelatedHeading title={copy.tickets} count={tickets?.length ?? 0} />
          {tickets === null ? null : tickets.length === 0 ? (
            <p className="card-meta" style={{ margin: "0 0 14px" }}>{copy.noTickets}</p>
          ) : (
            <ul className="context-list">
              {tickets.map((ticket) => (
                <li key={ticket.id}>
                  <Link
                    className="row-link"
                    href={`/liff/sales/tickets?q=${encodeURIComponent(ticket.ticket_number)}`}
                  >
                    <span className="row-body">
                      <span className="card-title">
                        <span className="code">{ticket.ticket_number}</span>
                        <Badge stage={ticket.status} label={ticketStatus(ticket.status)} />
                      </span>
                      {(ticket.product_name || ticket.issue) && (
                        <span className="card-meta">
                          {[ticket.product_name, ticket.issue].filter(Boolean).join(" · ")}
                        </span>
                      )}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      <RelatedHeading title={t.dashboard.related.notes} count={notes?.length ?? 0} />
      {notes === null ? null : notes.length === 0 ? (
        <p className="card-meta" style={{ margin: "0 0 14px" }}>{t.dashboard.related.noNotes}</p>
      ) : (
        <ul className="context-list">
          {notes.slice(0, 5).map((note) => (
            <li key={note.id} className="context-note">
              <div className="card-meta" style={{ color: "var(--ink)" }}>{note.body ?? ""}</div>
              <div className="card-meta" style={{ fontSize: 12, color: "var(--ink-faint)" }}>
                {fullDateTime(note.created_at, locale)}
                {note.author_display_name ? ` · ${note.author_display_name}` : ""}
              </div>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
